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
from urllib.parse import urlsplit

from prompt_toolkit import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, is_searching
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.application import get_app
from prompt_toolkit.layout import ConditionalContainer, DynamicContainer, Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from prompt_toolkit.lexers import DynamicLexer, PygmentsLexer
from prompt_toolkit.search import start_search, stop_search
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Button, Label, SearchToolbar, TextArea
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

from .i18n import get_language, set_language, tr

from .core import QuickViewError, Context, cli, clipboard_write, open_default, read_file, rpc
from .screen import HINT_KEYS, Screen, capture_screen, hint_grid, hint_labels
from .links import URLReference, WEB_TIMEOUT, read_url
from .settings import load_language, load_mode, local_macos, save_language, save_mode
from .images import ImageCanvas, ImageError, is_image, load_image


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
    return root / ".native/Herdr QuickView.app/Contents/MacOS/herdr-quickview-preview"


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
            raise QuickViewError(tr("系统速览不可用"))
        if self.cancelled.is_set():
            raise QuickViewError(tr("已取消速览"))
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
                        raise QuickViewError(tr("已取消速览"))
                    if not selector.select(.05):
                        continue
                    line = self.process.stdout.readline(4096)
                    if not line:
                        raise QuickViewError(tr("系统速览已提前退出"))
                    status = json.loads(line)
                    if status.get("error"):
                        error = status["error"]
                        if error in ('系统预览未获得焦点', '无效的 HTTP(S) 链接'):
                            error = tr(error)
                        raise QuickViewError(error)
                    if (status.get("ready") and status.get("system_panel")
                            and status.get("key_window") and status.get("active")
                            and status.get("pid") == self.process.pid):
                        return
                    raise QuickViewError(tr("系统速览未确认窗口焦点"))
                raise QuickViewError(tr("系统速览启动超时"))
        except (OSError, ValueError, QuickViewError) as exc:
            self.cancel()
            raise QuickViewError(tr("系统速览打开失败：{error}", error=exc)) from exc
        finally:
            if self.process is not None and self.process.stdout is not None:
                self.process.stdout.close()


def middle_ellipsis(text, width, *, tail_width=0):
    if get_cwidth(text) <= width:
        return text
    available = max(0, width - 1)
    right = min(available, max(available // 2, tail_width))
    left = available - right
    head, tail = [], []
    occupied = 0
    for char in text:
        size = get_cwidth(char)
        if occupied + size > left:
            break
        head.append(char)
        occupied += size
    occupied = 0
    for char in reversed(text):
        size = get_cwidth(char)
        if occupied + size > right:
            break
        tail.append(char)
        occupied += size
    return ''.join(head) + '…' + ''.join(reversed(tail))


class SidebarControl(FormattedTextControl):
    def __init__(self, ui):
        self.ui = ui
        super().__init__(ui.sidebar_fragments, focusable=True, show_cursor=False,
                         get_cursor_position=lambda: Point(0, ui.sidebar_index * 2))

    def mouse_handler(self, event):
        if event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.ui.app.layout.focus(self)
            self.ui.move_sidebar(1 if event.event_type == MouseEventType.SCROLL_DOWN else -1)
            self.ui.app.invalidate()
            return None
        return super().mouse_handler(event)


class TargetLineProcessor(Processor):
    def __init__(self, get_row):
        self.get_row = get_row

    def apply_transformation(self, ti):
        fragments = ti.fragments
        if ti.lineno == self.get_row():
            if not any(fragment[1] for fragment in fragments):
                fragments = [('', ' ')]
            fragments = [('class:quickview.target ' + style, text, *rest)
                         for style, text, *rest in fragments]
        return Transformation(fragments)


class ImageControl(UIControl):
    def is_focusable(self):
        return True

    def create_content(self, width, height):
        return UIContent(get_line=lambda i: [('', ' ' * width)],
                         line_count=max(1, height), show_cursor=False)

    def mouse_handler(self, event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            get_app().layout.focus(self)
            return None
        return NotImplemented


class ImageWindow(Window):
    def __init__(self):
        self.position = None
        super().__init__(ImageControl(), wrap_lines=False, always_hide_cursor=True)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        self.position = write_position
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)


def launch_quickview():
    if os.environ.get("HERDR_ENV") != "1":
        raise QuickViewError(tr("请从 Herdr 打开 Herdr QuickView"))
    context = Context.load()
    screen = capture_screen(context)
    payload = {"context": {**asdict(context), "cwd": str(context.cwd)}, "screen": screen}
    fd, name = tempfile.mkstemp(prefix="herdr-quickview-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        snapshot = cli("api", "snapshot")["snapshot"]
        if snapshot["focused_pane_id"] != context.pane_id:
            raise QuickViewError(tr("活动窗格已变化，请从目标 CLI 重新打开速览"))
        rpc("plugin.pane.open", {
            "plugin_id": "sylar.herdr-quickview", "entrypoint": "quickview",
            "placement": "popup", "focus": True, "width": "100%", "height": "100%",
            "env": {"QUICKVIEW_SOURCE_FILE": name},
        })
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


class QuickViewUI:
    def __init__(self, context, snapshot):
        self.context = context
        self.screen = Screen(snapshot, context.cwd)
        self.current, self.lexer = None, None
        self.target_row = self.pending_row = None
        self.status = snapshot["notice"]
        self.index, self.busy, self.native_opened = -1, False, False
        self.typed = ""
        self.native = NativePreview()
        self.preview_tasks = set()
        self.image = None
        try:
            set_language(load_language())
            self.mode = load_mode()
        except QuickViewError as exc:
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
                             lexer=DynamicLexer(lambda: self.lexer),
                             input_processors=[TargetLineProcessor(lambda: self.target_row)])
        write_body = self.body.window.write_to_screen

        def write_preview(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
            if self.pending_row is not None and write_position.width > 0 and write_position.height > 0:
                window = self.body.window
                content = self.body.control.create_content(write_position.width, write_position.height)
                width = write_position.width - sum(margin.get_width(lambda: content)
                                                   for margin in (*window.left_margins, *window.right_margins))
                row = self.pending_row
                target_height = content.get_height_for_line(row, max(1, width), window.get_line_prefix)
                remaining = max(0, (write_position.height - target_height) // 2)
                while row > 0:
                    height = content.get_height_for_line(row - 1, max(1, width), window.get_line_prefix)
                    if height > remaining:
                        break
                    remaining -= height
                    row -= 1
                window.vertical_scroll, window.vertical_scroll_2 = row, 0
                self.pending_row = None
            write_body(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)

        self.body.window.write_to_screen = write_preview
        self.path = TextArea(read_only=True, height=Dimension(min=1, max=3), wrap_lines=True,
                             dont_extend_height=True, focus_on_click=True)
        self.image_window = ImageWindow()
        self.control = FormattedTextControl(self.fragments, focusable=True, show_cursor=False)
        self.canvas = Window(self.control, wrap_lines=False, always_hide_cursor=True)
        self.grid = self.screen.cells(*snapshot["size"])
        visible_owners = {cell[2] for row in self.grid for cell in row if cell and cell[2] is not None}
        self.selectable = sorted(visible_owners)
        self.labels = hint_labels(self.selectable)
        self.grid = hint_grid(self.grid, self.labels)
        self.target_owners = {}
        for index in self.selectable:
            self.target_owners.setdefault(self.screen.hits[index], index)
        self.targets = list(self.target_owners)
        self.sidebar_visible = len(self.targets) > 1 and snapshot["size"][0] >= 72
        self.sidebar_index = 0
        self.sidebar_control = SidebarControl(self)
        self.sidebar = HSplit([
            Window(FormattedTextControl(lambda: tr(" 文件 / 链接 · {count}", count=len(self.targets))), height=1,
                   style="class:sidebar-heading"),
            Window(self.sidebar_control, wrap_lines=False, always_hide_cursor=True,
                   right_margins=[ScrollbarMargin(display_arrows=True)]),
        ], width=lambda: max(16, min(34, get_app().output.get_size().columns // 3)))
        self.open_button = Button(tr("默认应用打开") + " Ctrl+O", handler=self.open_in_default, width=22)
        self.mode_button = Button(tr("切换预览"), handler=self.toggle_mode, width=14)
        self.copy_button = Button(tr("复制地址"), handler=self.copy_path, width=14)
        self.back_button = Button(tr("返回"), handler=self.back, width=9)
        self.language_button = Button(self.language_label(), handler=self.toggle_language, width=9)
        self.preview = HSplit([
            VSplit([Label(" Herdr QuickView", style="class:title"),
                    self.open_button, self.mode_button, self.copy_button,
                    self.language_button, self.back_button], height=1),
            Window(FormattedTextControl(self.preview_navigation), height=1, style="class:status"),
            VSplit([
                ConditionalContainer(self.sidebar, filter=Condition(lambda: self.sidebar_visible)),
                ConditionalContainer(Window(width=1, char="│", style="class:sidebar-heading"),
                                     filter=Condition(lambda: self.sidebar_visible)),
                HSplit([self.path, DynamicContainer(lambda: self.image_window if self.image else self.body),
                        ConditionalContainer(self.search, filter=Condition(lambda: self.image is None))]),
            ]),
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

        @keys.add("c-f", filter=Condition(lambda: self.current is not None and self.image is None))
        def find(event):
            start_search(self.body.control)

        preview_active = Condition(lambda: self.current is not None) & ~is_searching
        sidebar_focused = Condition(lambda: self.app.layout.current_control == self.sidebar_control)

        @keys.add("c-t", filter=preview_active)
        def toggle_sidebar(event):
            self.toggle_sidebar()

        @keys.add("c-j", filter=preview_active)
        @keys.add("c-k", filter=preview_active)
        def next_preview(event):
            step = 1 if event.key_sequence[0].key == "c-j" else -1
            position = self.targets.index(self.current)
            self.open_sidebar((position + step) % len(self.targets), focus_list=sidebar_focused())

        @keys.add("tab", filter=preview_active)
        @keys.add("s-tab", filter=preview_active)
        def focus_sidebar(event):
            if self.sidebar_visible and not sidebar_focused():
                self.sidebar_index = self.targets.index(self.current)
                self.app.layout.focus(self.sidebar_control)
            else:
                self.app.layout.focus(self.preview_control())

        @keys.add("up", filter=preview_active & sidebar_focused)
        @keys.add("down", filter=preview_active & sidebar_focused)
        def move_sidebar(event):
            step = 1 if event.key_sequence[0].key == "down" else -1
            self.move_sidebar(step)

        @keys.add("enter", filter=preview_active & sidebar_focused)
        def select_sidebar(event):
            self.open_sidebar(self.sidebar_index, focus_list=True)

        self.app = Application(layout=Layout(root, focused_element=self.control), key_bindings=keys,
                               full_screen=True, mouse_support=True, include_default_pygments_style=False,
                               style=Style.from_dict({"": "bg:default fg:default",
                                   "pygments.comment": "italic fg:ansibrightblack",
                                   "pygments.keyword": "bold fg:ansimagenta",
                                   "pygments.keyword.type": "fg:ansicyan",
                                   "pygments.name.builtin": "fg:ansicyan",
                                   "pygments.name.function": "fg:ansiblue",
                                   "pygments.name.class": "bold fg:ansiyellow",
                                   "pygments.name.namespace": "fg:ansiblue",
                                   "pygments.name.decorator": "fg:ansimagenta",
                                   "pygments.name.tag": "fg:ansiblue",
                                   "pygments.name.attribute": "fg:ansicyan",
                                   "pygments.literal.string": "fg:ansigreen",
                                   "pygments.literal.number": "fg:ansiyellow",
                                   "pygments.generic.strong": "bold",
                                   "pygments.generic.emph": "italic",
                                   "pygments.generic.heading": "bold",
                                   "pygments.generic.subheading": "bold",
                                   "pygments.generic.inserted": "fg:ansigreen",
                                   "pygments.generic.deleted": "fg:ansired",
                                   "pygments.error": "underline fg:ansired",
                                   "quickview.target": "bold underline",
                                   "title": "bold fg:ansibrightcyan", "status": "fg:ansibrightcyan",
                                   "hint-path": "bold bg:#164c60 fg:#effcff",
                                   "hint-current": "bold bg:#c9f3ef fg:#102f36",
                                   "hint-muted": "bg:#233a42 fg:#899fa6",
                                   "hint-badge": "bold bg:#ffd166 fg:#182027",
                                   "hint-border": "fg:#75d9eb",
                                   "sidebar-heading": "bold fg:ansibrightcyan",
                                   "sidebar-current": "bold bg:#164c60 fg:#effcff",
                                   "sidebar-selected": "reverse bold",
                                   "button.focused": "reverse bold"}), min_redraw_interval=.03)
        self.image_canvas = ImageCanvas(self.app.output)
        self.app.before_render += self.before_render
        self.app.after_render += self.after_render

    def preview_control(self):
        return self.image_window.content if self.image else self.body.control

    def before_render(self, app):
        self.image_window.position = None

    def after_render(self, app):
        position = self.image_window.position
        if app.is_done:
            self.image_canvas.close()
        elif self.image and position:
            self.image_canvas.show(self.image, position.xpos, position.ypos,
                                   position.width, position.height)
        else:
            self.image_canvas.clear()

    def preview_navigation(self):
        def toggle(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.toggle_sidebar()
            else:
                return NotImplemented
        label = tr(" [文件列表 · Ctrl+T] ") if self.sidebar_visible else tr(" [展开列表 · Ctrl+T] ")
        control = self.app.layout.current_control
        if control == self.sidebar_control:
            help_text = tr(" ↑/↓ 选择 · Enter 打开 · Tab 正文")
            reference = self.targets[self.sidebar_index]
            address = reference.url if isinstance(reference, URLReference) else str(reference.path) + (f":{reference.line}" if reference.line else "")
            prefix = help_text + tr(" · 选中：")
            remaining = get_app().output.get_size().columns - get_cwidth(label + prefix)
            if remaining >= 6:
                help_text = prefix + middle_ellipsis(address, remaining)
        elif control == self.path.control:
            help_text = tr(" 地址 · Home/End 查看首尾 · Tab 列表/正文")
        else:
            help_text = tr(" Ctrl+J/K 切换 · Tab 列表/正文 · 点击地址查看全文")
        return [("reverse", label, toggle), ("", help_text)]

    def toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        if not self.sidebar_visible and self.app.layout.current_control == self.sidebar_control:
            self.app.layout.focus(self.preview_control())
        self.app.invalidate()

    def sidebar_fragments(self):
        result = []
        width = max(16, min(34, get_app().output.get_size().columns // 3)) - 1
        for position, reference in enumerate(self.targets):
            suffix = ""
            if isinstance(reference, URLReference):
                parts = urlsplit(reference.url)
                name = f"{parts.scheme}://{parts.netloc}"
                address = reference.url[len(name):]
                if not address.startswith('/'):
                    address = '/' + address
            else:
                line_suffix = f":{reference.line}" if reference.line else ""
                name = reference.path.name + line_suffix
                suffix = reference.path.suffix + line_suffix
                parent = reference.path.parent
                address = str(parent.relative_to(self.context.cwd)) if parent.is_relative_to(self.context.cwd) else str(parent)
                if address == '.':
                    address = './'
            active = reference == self.current
            focused = self.app.layout.current_control == self.sidebar_control and position == self.sidebar_index
            style = "class:sidebar-selected" if focused else "class:sidebar-current" if active else ""
            def click(event, position=position):
                if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                    self.open_sidebar(position, focus_list=True)
                else:
                    return NotImplemented
            for prefix, text, tail_width in ((f"{'▸' if active else ' '} ", name, get_cwidth(suffix)),
                                             ("  ", address, 0)):
                line = prefix + middle_ellipsis(text, width - get_cwidth(prefix), tail_width=tail_width)
                result.append((style, line + " " * (width - get_cwidth(line)), click))
                result.append(("", "\n"))
        return result[:-1]

    def move_sidebar(self, step):
        self.sidebar_index = max(0, min(len(self.targets) - 1, self.sidebar_index + step))

    def open_sidebar(self, position, *, focus_list=False):
        self.sidebar_index = position
        reference = self.targets[position]
        if reference != self.current:
            self.choose(reference, focus_list=focus_list)
        elif focus_list:
            self.app.layout.focus(self.sidebar_control)

    def size_matches(self):
        size = get_app().output.get_size()
        return [size.columns, size.rows] == self.screen.snapshot["size"]

    def footer_row(self):
        return next((y for y in range(len(self.grid) - 1, -1, -1)
                     if not any(cell and cell[2] is not None for cell in self.grid[y])), None)

    def footer(self):
        same_size = self.size_matches()
        if not same_size:
            message = tr("窗口大小已变化，请 Esc 退出后重新打开 Herdr QuickView。")
        elif self.busy:
            message = tr("正在打开预览… Esc 可取消")
        elif not self.selectable:
            message = tr("当前画面未找到文件或 HTTP(S) 链接。Esc 返回，滚到可见区域后重试。")
        else:
            message = tr("Herdr QuickView · 点击 / 输入编号直达 · Tab / Enter · Esc 取消 · {count} 处", count=len(self.selectable))
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
        except QuickViewError as exc:
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
                self.status = tr("已保存：") + (tr("macOS 系统速览") if self.native_mode else tr("Herdr 浮窗")) + tr("；返回原文后，下次选择文件或链接时生效。")
            except QuickViewError as exc:
                self.status = str(exc)
        self.app.invalidate()

    def create_preview_task(self, coroutine):
        task = self.app.create_background_task(coroutine)
        self.preview_tasks.add(task)
        task.add_done_callback(self.preview_tasks.discard)

    def cancel_preview_tasks(self):
        for task in tuple(self.preview_tasks):
            task.cancel()
        self.preview_tasks.clear()

    def show_selection(self):
        self.target_row = self.pending_row = None
        self.image_canvas.clear()
        self.image = None
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
        self.cancel_preview_tasks()
        self.native.cancel()
        self.show_selection()

    def choose(self, reference, *, focus_list=False):
        if self.current is None and (self.busy or not self.size_matches()):
            return
        from_preview = self.current is not None
        self.target_row = self.pending_row = None
        self.image_canvas.clear()
        self.image = None
        if from_preview:
            self.cancel_preview_tasks()
            self.native.cancel()
            self.native = NativePreview()
            self.current = reference
        self.index = self.target_owners[reference]
        self.sidebar_index = self.targets.index(reference)
        self.busy = True
        self.create_preview_task(self.open(reference, focus_list=focus_list, from_preview=from_preview))

    async def open(self, reference, *, focus_list=False, from_preview=False):
        self.target_row = self.pending_row = None
        self.image_canvas.clear()
        self.image = None
        notice = ""
        if self.native_mode and not from_preview:
            native = self.native
            try:
                if isinstance(reference, URLReference):
                    await off_thread(lambda: native.open(reference.url, url=True))
                else:
                    await off_thread(lambda: native.open(reference.path))
                self.native_opened = True
                await off_thread(native.wait_closed)
                self.show_selection()
                return
            except QuickViewError as exc:
                notice = tr("{error}，已改用 Herdr 预览。 ", error=exc)
        self.current = reference
        is_url = isinstance(reference, URLReference)
        self.path.text = reference.url if is_url else str(reference.path) + (f":{reference.line}" if reference.line else "")
        self.app.layout.focus(self.sidebar_control if focus_list and self.sidebar_visible else self.body)
        self.lexer = None
        self.body.text = tr("正在读取网页… Esc 可取消") if is_url else tr("正在读取文件… Esc 可取消")
        try:
            if from_preview:
                await asyncio.sleep(.12)
            if is_url:
                text, truncated = await asyncio.wait_for(off_thread(lambda: read_url(reference.url)), WEB_TIMEOUT)
            elif await off_thread(lambda: is_image(reference.path)):
                self.image = await load_image(reference.path)
                self.body.text = ''
                self.status = tr("{kind} · {width} × {height} · 需要兼容图像的终端；空白时 Ctrl+O 默认应用 · Esc 返回",
                                 kind=self.image.format, width=self.image.width, height=self.image.height)
                self.app.layout.focus(self.sidebar_control if focus_list and self.sidebar_visible else self.preview_control())
                self.busy = False
                self.app.invalidate()
                return
            else:
                text, truncated = await off_thread(lambda: read_file(reference.path))
                try:
                    self.lexer = PygmentsLexer(type(get_lexer_for_filename(str(reference.path))))
                except ClassNotFound:
                    self.lexer = None
            self.body.text = text
            row = max(0, min(((None if is_url else reference.line) or 1) - 1, self.body.document.line_count - 1))
            self.target_row = row if (not is_url and reference.line is not None
                                      and 1 <= reference.line <= self.body.document.line_count) else None
            self.pending_row = row
            self.body.buffer.cursor_position = self.body.document.translate_row_col_to_index(row, 0)
            self.status = notice + (tr("仅显示前 2 MiB。 ") if truncated else "") + (
                tr("网页正文预览；需 JavaScript 或登录的页面可能不完整。Ctrl+F 搜索 · Esc 返回")
                if is_url else tr("Ctrl+F 搜索 · Esc / q 返回 · Ctrl+Q 退出"))
        except ImageError as exc:
            reasons = {
                'file_limit': tr("图片文件超过 32 MiB，未解码。"),
                'pixel_limit': tr("图片尺寸超过安全预览上限，未解码。"),
                'animated': tr("当前仅支持静态 PNG、JPEG 和 WebP 图片。"),
                'timeout': tr("图片读取超时，已停止解码。"),
            }
            self.body.text = reasons.get(str(exc), tr("图片解码失败或格式不受支持。"))
            self.status = tr("可用默认应用打开（Ctrl+O） · 复制地址 · Esc 返回")
        except (QuickViewError, OSError, asyncio.TimeoutError) as exc:
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
            except QuickViewError as exc:
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
            except QuickViewError as exc:
                self.status = str(exc)
            self.app.invalidate()
        self.create_preview_task(copy())


def main():
    path = Path(os.environ["QUICKVIEW_SOURCE_FILE"])
    try:
        data = json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)
    context = Context(**{**data["context"], "cwd": Path(data["context"]["cwd"])})
    ui = QuickViewUI(context, data["screen"])
    try:
        ui.app.run()
    finally:
        ui.native.cancel()
        ui.image_canvas.close()
