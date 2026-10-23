from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.utils import get_cwidth

from .i18n import tr

from .core import PeekError, resolve_path, rpc
from .links import url_candidates, url_spans


@dataclass(frozen=True)
class Reference:
    path: Path
    line: int | None = None


HINT_KEYS = "1234567890abcdefghijklmnoprstuvwxyz"


def hint_labels(indices):
    width = 1
    while len(indices) > len(HINT_KEYS) ** width:
        width += 1
    result = {}
    for number, index in enumerate(indices):
        label = ""
        for _ in range(width):
            label = HINT_KEYS[number % len(HINT_KEYS)] + label
            number //= len(HINT_KEYS)
        result[index] = label
    return result


def hint_grid(grid, labels):
    """标记不移动原文；空白足够时画边框，否则用整段底色和路径内编号。"""
    result = [row.copy() for row in grid]
    rows, cols = len(grid), len(grid[0]) if grid else 0

    def blank(x, y, width=1):
        return (0 <= y < rows and 0 <= x and x + width <= cols and
                all(cell and cell[1] == " " and cell[2] is None for cell in result[y][x:x+width]))

    def put(x, y, text, owner, style):
        end = x + len(text)
        for col in range(x, end):
            cell = result[y][col]
            if cell and get_cwidth(cell[1]) > 1 and col + get_cwidth(cell[1]) > end:
                for tail in range(end, min(cols, col + get_cwidth(cell[1]))):
                    result[y][tail] = ("", " ", owner)
        for col, char in enumerate(text, x):
            result[y][col] = (style, char, owner)

    for owner, label in labels.items():
        segments = []
        for y, row in enumerate(grid):
            positions = [x for x, cell in enumerate(row) if cell and cell[2] == owner]
            if positions:
                start, last = positions[0], positions[-1]
                end = last + get_cwidth(row[last][1])
                segments.append((y, start, end))
                if blank(start-1, y) and blank(end, y):
                    if blank(start-1, y-1, end-start+2) and blank(start-1, y+1, end-start+2):
                        put(start-1, y-1, "╭" + "─"*(end-start) + "╮", owner, "class:hint-border")
                        put(start-1, y+1, "╰" + "─"*(end-start) + "╯", owner, "class:hint-border")
                    put(start-1, y, "│", owner, "class:hint-border")
                    put(end, y, "│", owner, "class:hint-border")
        if not segments:
            continue
        first_y, first_x, _ = segments[0]
        last_y, _, last_end = segments[-1]
        badge = f"[{label}]"
        candidates = [(last_end+1, last_y), (last_end, last_y),
                      (first_x-len(badge)-1, first_y)]
        position = next(((x, y) for x, y in candidates if blank(x, y, len(badge))), None)
        if position is None:
            y, start, end = segments[0]
            badge = badge if end-start >= len(badge) else label
            if end-start < len(badge):
                continue
            position = start, y
        put(*position, badge, owner, "class:hint-badge")
    return result


PATH_PATTERN = re.compile(
    r'\[[^\]\n]*\]\((?P<markdown>[^)]+)\)|`(?P<backtick>[^`]+)`|"(?P<double>[^\"]+)"|'
    r"'(?P<single>[^']+)'|\((?P<paren>[^)]+)\)|（(?P<wideparen>[^）]+)）|"
    r'(?P<absolute>file://(?:localhost)?/[^\n]*|(?:~/|/|\.\.?/)[^\n]+)|'
    r"(?P<token>[^\s`\"'<>()[\]{}|,;，。；：（）]+)"
)


def balanced_end(text, opening, left, right):
    depth = 1
    for index in range(opening + 1, len(text)):
        if text[index] == left:
            depth += 1
        elif text[index] == right:
            depth -= 1
            if depth == 0:
                return index
    return None


def continued_file_uri(text, start, end, cwd):
    """只接受还原后真实存在的文件；原样单行文件优先。"""
    raw = text[start:end].rstrip()
    if any(char.isspace() for char in raw):
        return text[start:end], end
    candidate = raw
    while True:
        for value in dict.fromkeys((candidate, candidate.rstrip('：:。.,，;；)）]>"\'`'))):
            try:
                path, _ = resolve_path(value, cwd)
                if path.is_file():
                    return text[start:end], end
            except (PeekError, OSError, ValueError):
                pass
        continuation = re.match(r'\n[ \t]+(?P<part>\S+)', text[end:])
        if continuation is None:
            return raw, start + len(raw)
        part = continuation['part']
        if re.match(r'[A-Za-z][A-Za-z0-9+.-]*://|~/|\.\.?/', part):
            return raw, start + len(raw)
        try:
            path, _ = resolve_path(part, cwd)
            if part.startswith('/') and path.exists():
                return raw, start + len(raw)
        except (PeekError, OSError, ValueError):
            pass
        candidate += part
        end += continuation.end()


def reference_spans(text, cwd):
    """保留原文位置；解析后的真实路径不能用于反查相对路径或符号链接的位置。"""
    resolved = {}
    position = 0
    while match := PATH_PATTERN.search(text, position):
        position = match.end()
        group = match.lastgroup
        raw = match[group]
        start = match.start(group)
        if group == 'absolute' and raw.startswith(('file:///', 'file://localhost/')):
            raw, position = continued_file_uri(text, start, position, cwd)
        if group in ("markdown", "paren", "wideparen"):
            left, right = ("（", "）") if group == "wideparen" else ("(", ")")
            closing = balanced_end(text, start - 1, left, right)
            if closing is None:
                position = start
                continue
            raw, position = text[start:closing], closing + 1
            if group == "markdown" and raw.startswith("<") and raw.endswith(">"):
                raw, start = raw[1:-1], start + 1
        # 无引号的路径先验证完整片段，再逐个回退到词边界，不能在版本号处直接截断。
        ends = [len(raw)]
        if group == "absolute":
            ends += sorted({m.start() for m in re.finditer(r'''[\s`"'<>()[\]{}|,;，。；：]''', raw)}, reverse=True)
        for end in ends:
            candidate = raw[:end].rstrip(" \t")
            end = len(candidate)
            if "\n" in candidate:
                # CLI 常主动插入带缩进的折行；仅处理括号/引号内的续行，不跨段落。
                if re.search(r"\n(?![ \t]+\S)", candidate):
                    continue
                candidate = re.sub(r"\n[ \t]+", "", candidate)
            values = [(value, end - len(candidate) + len(value))
                      for value in dict.fromkeys((candidate, candidate.rstrip("：:。.,，;；")))]
            if group == "absolute":
                for value, source_end in values.copy():
                    if "\\" in value:
                        try:
                            words = shlex.split(value)
                        except ValueError:
                            continue
                        if len(words) == 1 and words[0] != value:
                            values.append((words[0], source_end))
            matched = False
            for value, source_end in values:
                if not value:
                    continue
                if value not in resolved:
                    try:
                        path, line = resolve_path(value, cwd)
                        resolved[value] = Reference(path, line) if path.is_file() else None
                    except (PeekError, OSError, ValueError):
                        resolved[value] = None
                if resolved[value] is not None:
                    yield resolved[value], start, start + source_end
                    if group == "absolute":
                        position = start + source_end
                    matched = True
                    break
            if matched:
                break
        else:
            if group in ("paren", "wideparen", "backtick", "double", "single"):
                position = start
            elif group == "absolute" and len(ends) > 1:
                position = start + min(ends[1:]) + 1


def target_spans(text, cwd):
    links = list(url_spans(text))
    masked = list(text)
    for _, start, end in url_candidates(text):
        masked[start:end] = ['\n' if char == '\n' else ' ' for char in text[start:end]]
    files = list(reference_spans(''.join(masked), cwd))
    yield from sorted(links + files, key=lambda item: item[1])


def styled_chars(ansi):
    # 只保留可见文字、换行和 SGR，画面里的其他终端控制序列不参与重放。
    ansi = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", ansi)
    ansi = "".join(re.findall(r"\x1b\[[0-9;:]*m|[^\x00-\x09\x0b-\x1f\x7f]", ansi))
    return [(style, c) for style, text, *_ in ANSI(ansi).__pt_formatted_text__() for c in text]


def screen_text(ansi):
    return "".join(c for _, c in styled_chars(ansi))


def align_text(logical, rendered):
    """仅允许视口额外的软折行和行尾填充；不忽略路径中间的空格差异。"""
    mapping, i, j = {}, 0, 0
    while i < len(logical) and j < len(rendered):
        if logical[i] == rendered[j]:
            mapping[i] = j
            i, j = i + 1, j + 1
        elif rendered[j] == "\n":
            j += 1
        elif rendered[j] == " " and not rendered[j:rendered.find("\n", j) if "\n" in rendered[j:] else len(rendered)].strip(" "):
            j += 1
        else:
            return None
    if logical[i:].strip() or rendered[j:].strip():
        return None
    return mapping


def unwrap_visible(context, pane, rendered):
    scroll = pane.get("scroll", {})
    rows = scroll.get("viewport_rows", 0)
    if not rows or not rendered.strip():
        return rendered, ""
    start = max(0, scroll["max_offset_from_bottom"] - scroll["offset_from_bottom"])
    last_col = max(0, max(map(get_cwidth, rendered.splitlines()), default=1) - 1)
    try:
        logical = rpc("pane.selection.read", {
            "pane_id": context.pane_id,
            "anchor": {"row": start, "col": 0},
            "cursor": {"row": start + rows - 1, "col": last_col},
        }, timeout=2)["text"]
        if align_text(logical, rendered) is not None:
            return logical, ""
    except PeekError:
        pass
    return rendered, tr("折行未能可靠还原；仅标记当前能确认的路径。")


def content_origin(rect, area, ui, count):
    # Herdr 0.9.5 的 pane chrome 规则；layout.rect 已包含 pane gap 的处理。
    mode = ui.get("pane_borders", "auto")
    bordered = mode not in (False, "off") and (mode == "always" or count > 1)
    outer = ui.get("pane_outer_borders", True)
    left = bordered and (outer or rect["x"] != area["x"])
    top = bordered and (outer or rect["y"] != area["y"])
    return rect["x"] + int(left), rect["y"] + int(top)


def capture_screen(context):
    layout = rpc("pane.layout", {"pane_id": context.pane_id}, timeout=2)["layout"]
    if layout["focused_pane_id"] != context.pane_id:
        raise PeekError(tr("活动窗格已变化，请从目标 CLI 重新打开"))
    area = layout["area"]
    config_base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_path = Path(os.environ.get("HERDR_CONFIG_PATH", config_base / "herdr/config.toml"))
    try:
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    except (OSError, ValueError) as exc:
        raise PeekError(tr("无法确认原画面的布局：{error}", error=exc)) from exc
    panes = layout["panes"]
    visible = ([{"pane_id": context.pane_id, "rect": area}] if layout["zoomed"] else panes)
    frames, logical, notice = [], "", ""
    for item in visible:
        ansi = rpc("pane.read", {
            "pane_id": item["pane_id"], "source": "visible", "format": "ansi",
        }, timeout=2)["read"]["text"]
        x, y = content_origin(item["rect"], area, config.get("ui", {}), len(panes))
        source = item["pane_id"] == context.pane_id
        frames.append({"x": x - area["x"] - 1, "y": y - area["y"] - 1,
                       "ansi": ansi, "source": source})
        if source:
            pane = rpc("pane.get", {"pane_id": context.pane_id}, timeout=2)["pane"]
            logical, notice = unwrap_visible(context, pane, screen_text(ansi))
    if not any(f["source"] for f in frames):
        raise PeekError(tr("来源窗格已不在当前画面"))
    # 100% popup 的边框占两列，Herdr 还固定保留一列 scrollbar gutter。
    return {"frames": frames, "text": logical, "notice": notice,
            "size": [max(0, area["width"] - 3), max(0, area["height"] - 2)]}


class Screen:
    def __init__(self, snapshot, cwd):
        self.snapshot = snapshot
        self.hits = []
        self.owners = {}
        self.source = next(f for f in snapshot["frames"] if f["source"])
        rendered = screen_text(self.source["ansi"])
        mapping = align_text(snapshot["text"], rendered)
        if mapping is None:
            raise PeekError(tr("画面与路径位置不一致，请重新打开 Herdr Peek"))
        for reference, start, end in target_spans(snapshot["text"], cwd):
            positions = [mapping[i] for i in range(start, end) if i in mapping]
            if not positions:
                continue
            index = len(self.hits)
            self.hits.append(reference)
            for pos in range(min(positions), max(positions) + 1):
                self.owners[pos] = index

    def cells(self, columns, rows):
        cells = [[("", " ", None) for _ in range(columns)] for _ in range(rows)]
        for frame in self.snapshot["frames"]:
            x, y = frame["x"], frame["y"]
            for pos, (style, c) in enumerate(styled_chars(frame["ansi"])):
                if c == "\n":
                    x, y = frame["x"], y + 1
                    continue
                width = get_cwidth(c)
                owner = self.owners.get(pos) if frame["source"] else None
                if width == 0:
                    if 0 <= y < rows and 0 < x <= columns:
                        col = x - 1
                        while col > 0 and cells[y][col] is None:
                            col -= 1
                        prior = cells[y][col]
                        if prior:
                            cells[y][col] = (prior[0], prior[1] + c, prior[2])
                    continue
                if 0 <= y < rows and 0 <= x and x + width <= columns:
                    cells[y][x] = (style, c, owner)
                    for col in range(x + 1, x + width):
                        cells[y][col] = None
                x += width
        return cells
