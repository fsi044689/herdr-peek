from __future__ import annotations

import asyncio
import json
import os
import selectors
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition, is_searching
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.application import get_app
from prompt_toolkit.layout import DynamicContainer, Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from prompt_toolkit.lexers import DynamicLexer, PygmentsLexer
from prompt_toolkit.search import start_search, stop_search
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Label, SearchToolbar, TextArea
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

from .i18n import get_language, set_language, tr

from .core import PeekError, Context, cli, clipboard_write, open_default, read_file, rpc
from .screen import HINT_KEYS, Screen, capture_screen, hint_grid, hint_labels
from .links import URLReference, WEB_TIMEOUT, read_url
from .settings import load_language, load_mode, local_macos, save_language, save_mode


def off_thread(fn):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def finish(result, error):
        if future.done():
            return
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)

    def target():
        try:
            result, error = fn(), None
        except Exception as exc:
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(finish, result, error)
        except RuntimeError:
            pass
    threading.Thread(target=target, daemon=True).start()
    return future


def native_preview_executable():
    root = Path(os.environ.get("HERDR_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))
    return root / ".native/Herdr Peek.app/Contents/MacOS/herdr-peek-preview"


def native_quicklook_available():
    return (sys.platform == "darwin" and not os.environ.get("SSH_CONNECTION")
            and not os.environ.get("SSH_TTY")
            and os.access(native_preview_executable(), os.X_OK))


class NativePreview:
    def __init__(self):
        self.process = None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass

    def wait_closed(self):
        self.process.wait()

    def open(self, path, *, url=False):
        if not native_quicklook_available():
            raise PeekError(tr("系统速览不可用"))
        if self.cancelled.is_set():
            raise PeekError(tr("已取消速览"))
        try:
            self.process = subprocess.Popen(
                [str(native_preview_executable()), *(["--url"] if url else []), str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if self.cancelled.is_set():
                        raise PeekError(tr("已取消速览"))
                    if not selector.select(.05):
                        continue
                    line = self.process.stdout.readline(4096)
                    if not line:
                        raise PeekError(tr("系统速览已提前退出"))
                    status = json.loads(line)
                    if status.get("error"):
                        error = status["error"]
                        if error in ('系统预览未获得焦点', '无效的 HTTP(S) 链接'):
                            error = tr(error)
                        raise PeekError(error)
                    if (status.get("ready") and status.get("system_panel")
                            and status.get("key_window") and status.get("active")
                            and status.get("pid") == self.process.pid):
                        return
                    raise PeekError(tr("系统速览未确认窗口焦点"))
                raise PeekError(tr("系统速览启动超时"))
        except (OSError, ValueError, PeekError) as exc:
            self.cancel()
            raise PeekError(tr("系统速览打开失败：{error}", error=exc)) from exc
        finally:
            if self.process is not None and self.process.stdout is not None:
                self.process.stdout.close()


def launch_peek():
    if os.environ.get("HERDR_ENV") != "1":
        raise PeekError(tr("请从 Herdr 打开 Herdr Peek"))
    context = Context.load()
    screen = capture_screen(context)
    payload = {"context": {**asdict(context), "cwd": str(context.cwd)}, "screen": screen}
    fd, name = tempfile.mkstemp(prefix="herdr-peek-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        snapshot = cli("api", "snapshot")["snapshot"]
        if snapshot["focused_pane_id"] != context.pane_id:
            raise PeekError(tr("活动窗格已变化，请从目标 CLI 重新打开速览"))
        rpc("plugin.pane.open", {
            "plugin_id": "sylar.herdr-peek", "entrypoint": "peek",
            "placement": "popup", "focus": True, "width": "100%", "height": "100%",
            "env": {"PEEK_SOURCE_FILE": name},
        })
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


class PeekUI:
    def __init__(self, context, snapshot):
        self.context = context
        self.screen = Screen(snapshot, context.cwd)
        self.current, self.lexer = None, None
        self.status = snapshot["notice"]
        self.index, self.busy, self.native_opened = -1, False, False
        self.typed = ""
        self.native = NativePreview()
        self.preview_tasks = set()
        try:
            set_language(load_language())
            self.mode = load_mode()
        except PeekError as exc:
            self.mode = 'herdr'
            self.status = str(exc)
        self.native_mode = self.mode == 'macos' and native_quicklook_available()
        if self.mode == 'macos' and not self.native_mode:
            reason = tr('当前不是本机 macOS 环境') if not local_macos() else tr('未找到系统速览组件')
            self.status = (self.status + ' · ' if self.status else '') + reason + tr('，本次使用 Herdr 浮窗；已保存偏好不变。')
        self.search = SearchToolbar(forward_search_prompt=lambda: tr('搜索：'),
                                    backward_search_prompt=lambda: tr('反向搜索：'))
        self.body = TextArea(read_only=True, scrollbar=True, line_numbers=True, wrap_lines=True,
                             search_field=self.search, focus_on_click=True,
                             lexer=DynamicLexer(lambda: self.lexer))
        self.path = TextArea(read_only=True, height=1, wrap_lines=False, focus_on_click=True)
        self.control = FormattedTextControl(self.fragments, focusable=True, show_cursor=False)
        self.canvas = Window(self.control, wrap_lines=False, always_hide_cursor=True)
        self.grid = self.screen.cells(*snapshot["size"])
        visible_owners = {cell[2] for row in self.grid for cell in row if cell and cell[2] is not None}
        self.selectable = sorted(visible_owners)
        self.labels = hint_labels(self.selectable)
        self.grid = hint_grid(self.grid, self.labels)
        self.open_button = Button(tr("默认应用打开") + " Ctrl+O", handler=self.open_in_default, width=22)
        self.mode_button = Button(tr("切换预览"), handler=self.toggle_mode, width=14)
        self.copy_button = Button(tr("复制地址"), handler=self.copy_path, width=14)
        self.back_button = Button(tr("返回"), handler=self.back, width=9)
        self.language_button = Button(self.language_label(), handler=self.toggle_language, width=9)
        self.preview = HSplit([
            VSplit([Label(" Herdr Peek", style="class:title"),
                    self.open_button, self.mode_button, self.copy_button,
                    self.language_button, self.back_button], height=1),
            self.path, self.body, self.search,
            Window(FormattedTextControl(lambda: " " + self.status), height=1, style="class:status"),
        ])
        footer_row = self.footer_row()
        self.selection = FloatContainer(self.canvas, floats=[Float(
            content=Window(FormattedTextControl(self.footer), height=1),
            left=0, right=0, top=footer_row, height=1,
        )] if footer_row is not None else [])
        root = DynamicContainer(lambda: self.preview if self.current else self.selection)
        keys = KeyBindings()

        @keys.add("escape", filter=is_searching, eager=True)
        def end_search(event):
            stop_search()

        @keys.add("escape", filter=~is_searching, eager=True)
        @keys.add("q", filter=~is_searching)
        def back(event):
            self.back()

        @keys.add("c-q")
        def close(event):
            self.native.cancel()
            event.app.exit()

        @keys.add("c-p", filter=~is_searching)
        def switch_mode(event):
            self.toggle_mode()

        @keys.add("c-l", filter=~is_searching)
        def switch_language(event):
            self.toggle_language()

        @keys.add("c-o", filter=Condition(lambda: self.current is not None) & ~is_searching)
        def open_external(event):
            self.open_in_default()

        @keys.add("tab", filter=Condition(lambda: self.current is None))
        @keys.add("s-tab", filter=Condition(lambda: self.current is None))
        def cycle(event):
            if self.selectable and self.size_matches():
                self.typed = ""
                step = -1 if event.key_sequence[0].key == "s-tab" else 1
                position = self.selectable.index(self.index) if self.index in self.selectable else (-1 if step > 0 else 0)
                self.index = self.selectable[(position + step) % len(self.selectable)]

        @keys.add("enter", filter=Condition(lambda: self.current is None))
        def select(event):
            if self.selectable:
                index = self.index if self.index in self.selectable else self.selectable[0]
                self.choose(self.screen.hits[index])

        @keys.add("<any>", filter=Condition(lambda: self.current is None and not self.busy))
        def hint_key(event):
            key = event.data.lower()
            if key not in HINT_KEYS or len(key) != 1 or not self.size_matches():
                return
            prefix = self.typed + key
            matches = [(index, label) for index, label in self.labels.items() if label.startswith(prefix)]
            self.typed = prefix if matches else ""
            if len(matches) == 1 and matches[0][1] == prefix:
                self.choose(self.screen.hits[matches[0][0]])

        @keys.add("backspace", filter=Condition(lambda: self.current is None))
        def erase_hint(event):
            self.typed = self.typed[:-1]

        @keys.add("c-f", filter=Condition(lambda: self.current is not None))
        def find(event):
            start_search(self.body.control)

        self.app = Application(layout=Layout(root, focused_element=self.control), key_bindings=keys,
                               full_screen=True, mouse_support=True,
                               style=Style.from_dict({"": "bg:default fg:default",
                                   "title": "bold fg:ansibrightcyan", "status": "fg:ansibrightcyan",
                                   "hint-path": "bold bg:#164c60 fg:#effcff",
                                   "hint-current": "bold bg:#c9f3ef fg:#102f36",
                                   "hint-muted": "bg:#233a42 fg:#899fa6",
                                   "hint-badge": "bold bg:#ffd166 fg:#182027",
                                   "hint-border": "fg:#75d9eb",
                                   "button.focused": "reverse bold"}), min_redraw_interval=.03)

    def size_matches(self):
        size = get_app().output.get_size()
        return [size.columns, size.rows] == self.screen.snapshot["size"]

    def footer_row(self):
        return next((y for y in range(len(self.grid) - 1, -1, -1)
                     if not any(cell and cell[2] is not None for cell in self.grid[y])), None)

    def footer(self):
        same_size = self.size_matches()
        if not same_size:
            message = tr("窗口大小已变化，请 Esc 退出后重新打开 Herdr Peek。")
        elif self.busy:
            message = tr("正在打开预览… Esc 可取消")
        elif not self.selectable:
            message = tr("当前画面未找到文件或 HTTP(S) 链接。Esc 返回，滚到可见区域后重试。")
        else:
            message = tr("Herdr Peek · 点击 / 输入编号直达 · Tab / Enter · Esc 取消 · {count} 处", count=len(self.selectable))
            if self.typed:
                message += tr(" · 已输入 {keys}", keys=self.typed)
        if self.status and not self.busy and same_size:
            message = self.status + " · " + message
        mode = tr("macOS 系统") if self.native_mode else tr("Herdr 浮窗")
        def switch(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.toggle_mode()
        def switch_language(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.toggle_language()
        fragments = [("reverse", tr(" [{mode} · Ctrl+P] ", mode=mode), switch),
                     ("reverse", f" [{self.language_label()} · Ctrl+L] ", switch_language)]
        if self.index in self.selectable and not self.busy and same_size:
            reference = self.screen.hits[self.index]
            address = reference.url if isinstance(reference, URLReference) else str(reference.path)
            if not self.status:
                message = address + " · " + message
            else:
                message = self.status + " · " + address
        fragments.append(("reverse", message))
        return fragments

    def fragments(self):
        active = self.size_matches() and not self.busy
        result = []
        for row in self.grid:
            for cell in row:
                if cell is None:
                    continue
                style, char, owner = cell
                if owner is None or not active:
                    result.append((style, char))
                else:
                    if "class:hint-badge" not in style and "class:hint-border" not in style:
                        style = ("class:hint-current" if owner == self.index else
                                 "class:hint-muted" if self.typed and not self.labels[owner].startswith(self.typed) else
                                 "class:hint-path")
                    def click(event, reference=self.screen.hits[owner]):
                        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                            self.choose(reference)
                    result.append((style, char, click))
            result.append(("", "\n"))
        return result[:-1]

    @staticmethod
    def language_label():
        return '中文' if get_language() == 'en' else 'English'

    def toggle_language(self):
        if self.busy:
            return
        language = 'zh' if get_language() == 'en' else 'en'
        try:
            save_language(language)
        except PeekError as exc:
            self.status = str(exc)
        else:
            set_language(language)
            self.open_button.text = tr('默认应用打开') + ' Ctrl+O'
            self.mode_button.text = tr('切换预览')
            self.copy_button.text = tr('复制地址')
            self.back_button.text = tr('返回')
            self.language_button.text = self.language_label()
            self.status = tr('界面语言已保存。')
        self.app.invalidate()

    def toggle_mode(self):
        if self.busy:
            return
        if not local_macos():
            self.status = tr("系统 Quick Look 仅支持本机 macOS；当前使用 Herdr 浮窗。")
        elif not native_quicklook_available():
            self.status = tr("未找到 macOS 速览组件；请重新运行 bootstrap.sh，当前使用 Herdr 浮窗。")
        else:
            mode = 'herdr' if self.native_mode else 'macos'
            try:
                save_mode(mode)
                self.mode, self.native_mode = mode, mode == 'macos'
                self.status = tr("已保存：") + (tr("macOS 系统速览") if self.native_mode else tr("Herdr 浮窗")) + tr("；下次选择文件或链接时生效。")
            except PeekError as exc:
                self.status = str(exc)
        self.app.invalidate()

    def create_preview_task(self, coroutine):
        task = self.app.create_background_task(coroutine)
        self.preview_tasks.add(task)
        task.add_done_callback(self.preview_tasks.discard)

    def show_selection(self):
        self.current, self.lexer = None, None
        self.busy, self.native_opened = False, False
        self.typed = ""
        self.native = NativePreview()
        self.status = self.screen.snapshot["notice"]
        self.app.layout.focus(self.control)
        self.app.invalidate()

    def back(self):
        if self.current is None:
            self.native.cancel()
            self.app.exit()
            return
        for task in tuple(self.preview_tasks):
            task.cancel()
        self.preview_tasks.clear()
        self.native.cancel()
        self.show_selection()

    def choose(self, reference):
        if self.busy or not self.size_matches():
            return
        self.busy = True
        self.create_preview_task(self.open(reference))

    async def open(self, reference):
        notice = ""
        if self.native_mode:
            try:
                if isinstance(reference, URLReference):
                    await off_thread(lambda: self.native.open(reference.url, url=True))
                else:
                    await off_thread(lambda: self.native.open(reference.path))
                self.native_opened = True
                await off_thread(self.native.wait_closed)
                self.show_selection()
                return
            except PeekError as exc:
                notice = tr("{error}，已改用 Herdr 预览。 ", error=exc)
        self.current = reference
        is_url = isinstance(reference, URLReference)
        self.path.text = reference.url if is_url else str(reference.path) + (f":{reference.line}" if reference.line else "")
        self.app.layout.focus(self.body)
        try:
            if is_url:
                self.body.text = tr("正在读取网页… Esc 可取消")
                self.lexer = None
                text, truncated = await asyncio.wait_for(off_thread(lambda: read_url(reference.url)), WEB_TIMEOUT)
            else:
                text, truncated = await off_thread(lambda: read_file(reference.path))
                try:
                    self.lexer = PygmentsLexer(type(get_lexer_for_filename(str(reference.path))))
                except ClassNotFound:
                    self.lexer = None
            self.body.text = text
            row = max(0, min(((None if is_url else reference.line) or 1) - 1, self.body.document.line_count - 1))
            self.body.buffer.cursor_position = self.body.document.translate_row_col_to_index(row, 0)
            self.status = notice + (tr("仅显示前 2 MiB。 ") if truncated else "") + (
                tr("网页正文预览；需 JavaScript 或登录的页面可能不完整。Ctrl+F 搜索 · Esc 返回")
                if is_url else tr("Ctrl+F 搜索 · Esc / q 返回 · Ctrl+Q 退出"))
        except (PeekError, OSError, asyncio.TimeoutError) as exc:
            reason = str(exc) or tr("网页读取超时")
            self.body.text = tr("此网页暂时无法在 Herdr 内预览。\n\n{reason}" if is_url else "此文件暂时无法在 Herdr 内预览。\n\n{reason}", reason=reason)
            self.status = notice + tr("可用默认应用打开（Ctrl+O） · 复制地址 · Esc 返回")
        self.busy = False
        self.app.invalidate()

    def open_in_default(self):
        if self.current is None or self.busy:
            return
        reference = self.current
        self.busy = True
        async def launch():
            try:
                if isinstance(reference, URLReference):
                    await off_thread(lambda: open_default(reference.url, url=True))
                else:
                    await off_thread(lambda: open_default(reference.path))
                self.status = tr("已提交系统默认应用打开请求。Esc 可返回选择。")
            except PeekError as exc:
                self.status = tr("默认应用打开请求未确认：{error}", error=exc)
            finally:
                if asyncio.current_task() in self.preview_tasks:
                    self.busy = False
                    self.app.invalidate()
        self.create_preview_task(launch())

    def copy_path(self):
        if self.current is None:
            return
        reference = self.current
        async def copy():
            try:
                await off_thread(lambda: clipboard_write(reference.url if isinstance(reference, URLReference) else str(reference.path)))
                self.status = tr("已复制完整链接。") if isinstance(reference, URLReference) else tr("已复制完整路径（不含终端显示折行）。")
            except PeekError as exc:
                self.status = str(exc)
            self.app.invalidate()
        self.create_preview_task(copy())


def main():
    path = Path(os.environ["PEEK_SOURCE_FILE"])
    try:
        data = json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)
    context = Context(**{**data["context"], "cwd": Path(data["context"]["cwd"])})
    ui = PeekUI(context, data["screen"])
    try:
        ui.app.run()
    finally:
        ui.native.cancel()
