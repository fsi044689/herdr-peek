from __future__ import annotations
import codecs
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


from .i18n import tr

MAX_TEXT = 2 * 1024 * 1024


class PeekError(Exception):
    pass


def execute(args, *, cwd=None, input=None, timeout=12, binary=False, detached=False):
    try:
        if detached:
            with tempfile.TemporaryFile() as errors:
                p = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=errors,
                                   timeout=timeout, check=False, start_new_session=True)
                errors.seek(0)
                error = errors.read(8192).decode(errors='replace')
                output = b'' if binary else ''
        else:
            p = subprocess.run(args, cwd=cwd, input=input, capture_output=True,
                               text=not binary, timeout=timeout, check=False)
            error = p.stderr if not binary else p.stderr.decode(errors='replace')
            output = p.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PeekError(str(exc)) from exc
    if p.returncode:
        raise PeekError(error.strip() or tr("命令退出：{code}", code=p.returncode))
    return output


def cli(*args):
    raw = execute([os.environ.get("HERDR_BIN_PATH", "herdr"), *args])
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise PeekError(tr("Herdr 返回了无法解析的内容")) from exc
    if "error" in value:
        raise PeekError(value["error"].get("message", str(value["error"])))
    return value.get("result", value)


def rpc(method, params, *, timeout=8):
    address = os.environ.get("HERDR_SOCKET_PATH")
    if not address:
        raise PeekError(tr("缺少 Herdr socket；请从 Herdr 插件入口打开"))
    request = {"id": str(uuid.uuid4()), "method": method, "params": params}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(address)
            conn.sendall((json.dumps(request) + "\n").encode())
            with conn.makefile("rb") as stream:
                raw = stream.readline(8 * 1024 * 1024)
        value = json.loads(raw)
    except TimeoutError as exc:
        raise PeekError(tr("Herdr 响应超时，结果未确认；请检查后再操作。")) from exc
    except (OSError, ValueError) as exc:
        raise PeekError(tr("Herdr 接口失败：{error}", error=exc)) from exc
    if "error" in value:
        raise PeekError(value["error"].get("message", str(value["error"])))
    return value["result"]


@dataclass(frozen=True)
class Context:
    pane_id: str
    tab_id: str
    workspace_id: str
    cwd: Path
    selected_text: str = ""

    @classmethod
    def load(cls):
        raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PeekError(tr("插件上下文无效，请关闭后重新打开")) from exc
        pane_id = data.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID")
        if not pane_id:
            raise PeekError(tr("缺少来源窗格，无法确定项目。请从目标窗格打开文件速览"))
        # Resolve the explicit originating pane, never the subsequently focused pane.
        pane = cli("pane", "get", pane_id)["pane"]
        cwd = pane.get("foreground_cwd") or pane.get("cwd")
        if not cwd or not Path(cwd).is_dir():
            raise PeekError(tr("来源窗格的工作目录不可访问"))
        return cls(pane["pane_id"], pane["tab_id"], pane["workspace_id"],
                   Path(cwd).resolve(), data.get("selected_text") or "")


def clean_text(text):
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)


def clipboard_write(text):
    if sys.platform == "darwin":
        execute(["pbcopy"], input=text)
        return
    for args in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(args[0]):
            execute(args, input=text)
            return
    raise PeekError(tr("找不到剪贴板工具，需要 wl-copy 或 xclip"))


def open_default(target, *, url=False):
    if os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'):
        raise PeekError(tr("SSH 环境无法唤起你本机的默认应用，请复制地址后在本机打开"))
    if url:
        from .links import valid_url
        if not valid_url(str(target)):
            raise PeekError(tr("不是有效的 HTTP(S) 链接"))
        address = str(target)
    else:
        path = Path(target).resolve()
        if not path.is_file():
            raise PeekError(tr("文件已不存在，无法用默认应用打开"))
        address = str(path)
    if sys.platform == 'darwin':
        execute(['open', *(['-u'] if url else []), address], timeout=8, detached=True)
    else:
        opener = shutil.which('xdg-open')
        if not opener:
            raise PeekError(tr("此环境找不到默认应用打开工具 xdg-open"))
        execute([opener, address], timeout=8, detached=True)


def resolve_path(text, cwd):
    text = text.strip()
    markdown = re.fullmatch(r"\[[^\]]*\]\((.+)\)", text)
    if markdown:
        text = markdown[1]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '`\'"':
        text = text[1:-1]
    uri_line = None
    if text.startswith("file://"):
        url = urlparse(text)
        if url.netloc not in ("", "localhost"):
            raise PeekError(tr("只支持本机文件路径"))
        text = unquote(url.path)
        anchor = re.fullmatch(r"L(\d+)(?:-L?\d+)?", url.fragment)
        if anchor:
            uri_line = int(anchor[1])
    elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", text):
        raise PeekError(tr("只支持本机文件路径"))
    if "\n" in text or not text or "\x00" in text:
        raise PeekError(tr("请选择单个文件或目录路径"))
    try:
        path = Path(text).expanduser()
    except RuntimeError as exc:
        raise PeekError(tr("无法解析用户目录")) from exc
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path = path.resolve()
    except RuntimeError as exc:
        raise PeekError(tr('无法解析文件路径：{error}', error=exc)) from exc
    # 冒号或 #L 也可以是文件名的一部分，先尊重实际存在的完整路径。
    if path.exists():
        return path, uri_line
    line = uri_line
    match = re.search(r"(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$", text)
    if match:
        line = int(match[1] or match[2])
        text = text[:match.start()]
    try:
        path = Path(text).expanduser()
    except RuntimeError as exc:
        raise PeekError(tr("无法解析用户目录")) from exc
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path = path.resolve()
    except RuntimeError as exc:
        raise PeekError(tr('无法解析文件路径：{error}', error=exc)) from exc
    if not path.exists():
        raise PeekError(tr("路径不存在：{path}", path=path))
    return path, line


def read_file(path):
    if not path.is_file():
        raise PeekError(tr("请选择文件"))
    with path.open("rb") as f:
        data = f.read(MAX_TEXT + 1)
    truncated = len(data) > MAX_TEXT
    data = data[:MAX_TEXT]
    if data.startswith((b"%PDF-", b"\x89PNG\r\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"PK\x03\x04")) or b"\x00" in data:
        raise PeekError(tr("这是二进制文件；当前 Herdr 窗内预览支持 UTF-8 文本"))
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=not truncated)
    except UnicodeDecodeError as exc:
        raise PeekError(tr("文件不是有效的 UTF-8 文本")) from exc
    return clean_text(text), truncated
