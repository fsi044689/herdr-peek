import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from herdr_quickview.core import Context, QuickViewError
from herdr_quickview.quickview import NativePreview, QuickViewUI, native_quicklook_available
from herdr_quickview.screen import (HINT_KEYS, Reference, Screen, align_text, capture_screen, content_origin, hint_grid, hint_labels,
                               reference_spans, screen_text, unwrap_visible)


def snapshot(ansi, logical=None, columns=80, rows=24, x=0, y=0):
    return {'frames': [{'x': x, 'y': y, 'ansi': ansi, 'source': True}],
            'text': screen_text(ansi) if logical is None else logical,
            'size': [columns, rows], 'notice': ''}


def setUpModule():
    language = patch("herdr_quickview.i18n._language", "zh")
    language.start()
    unittest.addModuleCleanup(language.stop)
    preference = patch("herdr_quickview.quickview.load_language", return_value="zh")
    preference.start()
    unittest.addModuleCleanup(preference.stop)


class ScreenTests(unittest.TestCase):
    def test_unknown_home_tokens_do_not_crash_or_hide_following_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root/'real.md'
            file.write_text('preview')
            for prefix in ('~5min', '~quickview_user_does_not_exist_501/x'):
                self.assertEqual(Screen(snapshot(f'{prefix} then {file}', columns=180), root).hits,
                                 [Reference(file)])

    def test_apostrophes_and_invalid_quotes_do_not_swallow_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root/'real.md'
            file.write_text('preview')
            for text in (f"I don't think {file} is wrong, it's fine",
                         f'"missing {file} text"', f'`missing {file} text`'):
                self.assertEqual(Screen(snapshot(text, columns=180), root).hits, [Reference(file)])

    def test_cli_indented_wrap_in_parenthesized_versioned_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root/'SAMPLE-v8.12.10 App前端对接.md'
            file.write_text('preview')
            for left, right in [('(', ')'), ('（', '）'), ('`', '`'), ('"', '"')]:
                with self.subTest(left=left):
                    text = f'• 对得上。当前 App 对接文档 {left}{file}{right}，与验收一致。'
                    wrapped = text.replace('App前端', 'App前\n  端')
                    screen = Screen(snapshot(wrapped, columns=160), root)
                    self.assertEqual(screen.hits, [Reference(file)])
                    cells = screen.cells(160, 24)
                    self.assertEqual(cells[1][2][1:], ('端', 0))
                    start = get_cwidth(text[:text.index(str(file))])
                    self.assertEqual(cells[0][start][2], 0)

    def test_versioned_bare_path_with_spaces_does_not_consume_next_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            first = root/'v8.12.10 App前端对接.md'
            second = root/'other.txt'
            for file in (first, second):
                file.write_text('preview')
            text = f'文档 {first} 和 {second} 已完成。'
            found = list(reference_spans(text, root))
            self.assertEqual([r.path for r, _, _ in found], [first, second])
            self.assertEqual([text[a:b] for _, a, b in found], [str(first), str(second)])
            for text in (f'/missing/file.py {second.name}', f'(参考 {second})'):
                self.assertEqual([r.path for r, _, _ in reference_spans(text, root)], [second])

    def test_cli_wrap_does_not_join_unindented_or_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root/'version App前端.md'
            file.write_text('preview')
            for separator in ('\n', '\n\n  ', '\n  \n  '):
                text = f'({str(file).replace("App前端", "App前"+separator+"端")})'
                self.assertEqual(list(reference_spans(text, root)), [])

    def test_real_files_spaces_unicode_suffix_and_duplicate_occurrences(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root / '中文 空格.py'
            file.write_text('hello\n')
            text = f'修改了 "{file}:3:8" 和 [文件](中文 空格.py#L2) `{file}` /missing/file.py'
            found = list(reference_spans(text, root))
            self.assertEqual([r for r, _, _ in found], [Reference(file, 3), Reference(file, 2), Reference(file)])
            self.assertEqual([text[a:b] for _, a, b in found], [f'{file}:3:8', '中文 空格.py#L2', str(file)])

    def test_symlink_keeps_original_click_position_and_ansi_color(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root / 'real.txt'
            file.write_text('hello')
            (root/'link.txt').symlink_to(file)
            screen = Screen(snapshot('\x1b[31m中文 link.txt\x1b[0m', x=3, y=2), root)
            cells = screen.cells(80, 24)
            self.assertEqual(screen.hits, [Reference(file)])
            start = 3 + get_cwidth('中文 ')
            self.assertEqual(cells[2][start][1:], ('l', 0))
            self.assertIn('ansired', cells[2][3][0])
            self.assertIsNone(cells[2][start-1][2])

    def test_wrapped_path_maps_both_rows_without_joining_hard_newlines(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            path = 'folder with spaces/中文文件.md'
            file = root/path
            file.parent.mkdir()
            file.write_text('preview')
            logical = f'修改 `{path}:3`\n下一条日志'
            cut = logical.index('中文')
            rendered = logical[:cut]+'\n'+logical[cut:]
            screen = Screen(snapshot(rendered, logical, x=4, y=3), root)
            self.assertEqual(screen.hits, [Reference(file, 3)])
            cells = screen.cells(80, 24)
            self.assertEqual(cells[4][4][1:], ('中', 0))
            self.assertEqual(cells[3][4+get_cwidth('修改 `')][2], 0)
            self.assertIsNone(cells[5][4][2])
            self.assertIsNone(align_text('/a b.txt', '/ab.txt'))
            self.assertIsNone(align_text('changed.txt', 'other.txt'))

    def test_borders_and_outer_edge_coordinates(self):
        area = {'x': 0, 'y': 0, 'width': 100, 'height': 30}
        right = {'x': 50, 'y': 0, 'width': 50, 'height': 30}
        self.assertEqual(content_origin(right, area, {}, 2), (51, 1))
        self.assertEqual(content_origin(right, area, {'pane_outer_borders': False}, 2), (51, 0))
        self.assertEqual(content_origin(right, area, {'pane_borders': 'off'}, 2), (50, 0))
        self.assertEqual(content_origin(area, area, {}, 1), (0, 0))

    def test_unwrap_is_read_only_and_keeps_raw_text_on_change(self):
        context = Context('origin', 'tab', 'ws', Path('/tmp'))
        pane = {'scroll': {'max_offset_from_bottom': 8, 'offset_from_bottom': 3, 'viewport_rows': 3}}
        raw = '/tmp/a-very-long\n-name.py\nhard newline'
        logical = '/tmp/a-very-long-name.py\nhard newline'
        with patch('herdr_quickview.screen.rpc', return_value={'text': logical}) as rpc:
            self.assertEqual(unwrap_visible(context, pane, raw), (logical, ''))
            rpc.assert_called_once()
            self.assertEqual(rpc.call_args.args[0], 'pane.selection.read')
            self.assertEqual(rpc.call_args.args[1]['anchor'], {'row': 5, 'col': 0})
        with patch('herdr_quickview.screen.rpc', return_value={'text': 'changed'}):
            text, notice = unwrap_visible(context, pane, raw)
            self.assertEqual(text, raw)
            self.assertIn('未能', notice)

    def test_capture_keeps_sibling_positions_and_reads_only_visible(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d)/'config.toml'
            config.write_text('[ui]\npane_scrollbars = false\n')
            context = Context('one', 'tab', 'ws', Path(d))
            area = {'x': 0, 'y': 0, 'width': 83, 'height': 26}
            layout = {'focused_pane_id': 'one', 'area': area, 'zoomed': False, 'panes': [
                {'pane_id': 'one', 'rect': {**area, 'width': 42}},
                {'pane_id': 'two', 'rect': {**area, 'x': 42, 'width': 41}}]}
            responses = [{'layout': layout}, {'read': {'text': 'first'}}, {'pane': {}}, {'read': {'text': 'sibling'}}]
            with patch.dict(os.environ, {'HERDR_CONFIG_PATH': str(config)}), \
                    patch('herdr_quickview.screen.rpc', side_effect=responses) as rpc:
                data = capture_screen(context)
            self.assertEqual(data['size'], [80, 24])
            self.assertEqual([(f['x'], f['y']) for f in data['frames']], [(0, 0), (42, 0)])
            for call in rpc.call_args_list:
                if call.args[0] == 'pane.read':
                    self.assertEqual(call.args[1]['source'], 'visible')
                    self.assertEqual(call.args[1]['format'], 'ansi')

    def test_platform_requires_local_mac_and_native_tools(self):
        with patch.dict(os.environ, {}, clear=True), patch('herdr_quickview.quickview.os.access', return_value=True):
            for platform, native in [('darwin', True), ('linux', False), ('win32', False)]:
                with patch('herdr_quickview.quickview.sys.platform', platform):
                    self.assertEqual(native_quicklook_available(), native)
            with patch('herdr_quickview.quickview.sys.platform', 'darwin'):
                with patch.dict(os.environ, {'SSH_CONNECTION': 'remote'}):
                    self.assertFalse(native_quicklook_available())
                with patch('herdr_quickview.quickview.os.access', return_value=False):
                    self.assertFalse(native_quicklook_available())

    def test_labels_are_unique_and_never_prefix_each_other(self):
        for count in (0, 1, 11, len(HINT_KEYS), len(HINT_KEYS)+1, 1200):
            labels = list(hint_labels(range(count)).values())
            self.assertEqual(len(set(labels)), count)
            self.assertLessEqual(len(set(map(len, labels))), 1)
            self.assertTrue(all('q' not in label for label in labels))
        self.assertEqual(hint_labels(range(11))[10], 'a')

    def test_frame_uses_only_free_space_and_badges_keep_wide_cells_valid(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'文件.py').write_text('preview')
            screen = Screen(snapshot('\n   文件.py   \n\n'), root)
            grid = screen.cells(30, 6)
            decorated = hint_grid(grid, {0: '1'})
            self.assertIn('╭', ''.join(c[1] for c in decorated[0] if c))
            self.assertIn('[1]', ''.join(c[1] for c in decorated[1] if c))
            tight = Screen(snapshot('文字`文件.py`后面'), root).cells(30, 6)
            decorated = hint_grid(tight, {0: '1'})
            for old, new in zip(tight[0], decorated[0]):
                if old and old[2] is None:
                    self.assertEqual(old, new)
            self.assertEqual(sum(get_cwidth(c[1]) for c in decorated[0] if c), 30)


class NativePreviewTests(unittest.TestCase):
    def test_requires_standard_panel_focus_acknowledgement(self):
        for key_window in (True, False):
            with self.subTest(key_window=key_window), tempfile.TemporaryDirectory() as d:
                helper = Path(d)/'preview'
                helper.write_text(f'#!{sys.executable}\nimport json,os,time\n'
                                  f'print(json.dumps(dict(ready=True,system_panel=True,key_window={key_window},active=True,pid=os.getpid())),flush=True)\n'
                                  'time.sleep(10)\n')
                helper.chmod(0o700)
                preview = NativePreview()
                try:
                    with patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), \
                            patch('herdr_quickview.quickview.native_preview_executable', return_value=helper):
                        if key_window:
                            preview.open(Path(d)/'file')
                            self.assertIsNone(preview.process.poll())
                        else:
                            with self.assertRaisesRegex(QuickViewError, '未确认窗口焦点'):
                                preview.open(Path(d)/'file')
                finally:
                    preview.cancel()
                    preview.process.wait(timeout=2)


class ScreenOutput(DummyOutput):
    def __init__(self, columns, rows):
        super().__init__()
        self.size = Size(rows=rows, columns=columns)

    def get_size(self):
        return self.size


class QuickViewUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config_env = patch.dict(os.environ, {'HERDR_PLUGIN_CONFIG_DIR': str(self.root/'config')})
        self.config_env.start()
        self.addCleanup(self.config_env.stop)
        self.file = self.root/'中文 note.py'
        self.file.write_text('first\nsecond\nneedle\nlast\n')
        self.task = self.ui = None

    async def asyncTearDown(self):
        if self.task:
            if not self.task.done():
                self.ui.app.exit()
            await asyncio.wait_for(self.task, 2)
            self.pipe_context.__exit__(None, None, None)
        self.temp.cleanup()

    async def launch(self, native=False, text=None, logical=None, columns=80, rows=24, source_y=0):
        self.pipe_context = create_pipe_input()
        self.pipe = self.pipe_context.__enter__()
        self.output = ScreenOutput(columns, rows)
        text = text or '原 CLI 输出\n修改了 `中文 note.py:3`\n其他日志仍在原来的位置'
        with patch('herdr_quickview.quickview.Application', side_effect=lambda **kw: Application(input=self.pipe, output=self.output, **kw)), \
                patch('herdr_quickview.quickview.native_quicklook_available', return_value=native):
            self.ui = QuickViewUI(Context('origin', 'tab', 'ws', self.root),
                             snapshot(text, logical, columns, rows, y=source_y))
        self.task = asyncio.create_task(self.ui.app.run_async())
        await asyncio.sleep(.1)

    async def click(self, x, y):
        self.pipe.send_text(f'\x1b[<0;{x+1};{y+1}M\x1b[<0;{x+1};{y+1}m')
        await asyncio.sleep(.15)

    async def test_cli_wrapped_encoded_file_uri_click_opens_exact_native_path(self):
        uri = self.file.as_uri()
        cut = uri.index('%')+4
        await self.launch(native=True, text=uri[:cut]+'\n  '+uri[cut:], columns=180)
        self.assertEqual(self.ui.screen.hits, [Reference(self.file)])
        with patch.object(self.ui.native, 'wait_closed'), patch.object(self.ui.native, 'open') as native:
            await self.click(3, 1)
            await asyncio.sleep(.15)
            self.assertFalse(self.task.done())
            native.assert_called_once_with(self.file)

    async def test_cli_wrapped_encoded_file_uri_digit_opens_herdr_body(self):
        uri = self.file.as_uri()+'#L3'
        cut = uri.index('%')+4
        await self.launch(text=uri[:cut]+'\n  '+uri[cut:], columns=180)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current, Reference(self.file, 3))
        self.assertIn('needle', self.ui.body.text)
        self.assertEqual(self.ui.body.document.cursor_position_row, 2)

    async def test_ctrl_o_opens_default_app_from_unsupported_preview_and_keeps_page(self):
        file = self.root/'sample.pdf'
        file.write_bytes(b'%PDF-1.4\nfixture')
        await self.launch(text=str(file), columns=100)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertIn('无法在 Herdr 内预览', self.ui.body.text)
        before = self.ui.body.text
        with patch('herdr_quickview.quickview.open_default') as opener:
            self.pipe.send_text('\x0f')
            await asyncio.sleep(.15)
            opener.assert_called_once_with(file)
            self.assertIn('已提交', self.ui.status)
            self.assertEqual(self.ui.body.text, before)
            self.assertFalse(self.task.done())

    async def test_default_app_button_click_opens_real_file_without_line_number(self):
        await self.launch(columns=100)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        rendered = self.ui.app.renderer._last_screen
        x = next(x for x in range(100) if rendered.data_buffer[0][x].char == '应')
        with patch('herdr_quickview.quickview.open_default') as opener:
            await self.click(x, 0)
            opener.assert_called_once_with(self.file)

    async def test_ctrl_o_url_uses_original_url_not_file_or_line(self):
        url = 'https://www.python.org/?q=%2F#top'
        await self.launch(text=url)
        with patch('herdr_quickview.quickview.read_url', return_value=('body', False)):
            self.pipe.send_text('1')
            await asyncio.sleep(.15)
        with patch('herdr_quickview.quickview.open_default') as opener:
            self.pipe.send_text('\x0f')
            await asyncio.sleep(.15)
            opener.assert_called_once_with(url, url=True)

    async def test_default_opener_failure_keeps_content_and_reports_unconfirmed(self):
        await self.launch()
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        before = self.ui.body.text
        with patch('herdr_quickview.quickview.open_default', side_effect=QuickViewError('no association')):
            self.ui.open_in_default()
            await asyncio.sleep(.1)
        self.assertIn('未确认', self.ui.status)
        self.assertIn('no association', self.ui.status)
        self.assertEqual(self.ui.body.text, before)
        self.assertFalse(self.ui.busy)

    async def test_default_opener_does_not_block_cancel_or_repeat_launch(self):
        await self.launch()
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.open_default', side_effect=lambda _: release.wait(3)) as opener:
                self.ui.open_in_default()
                await asyncio.sleep(.1)
                self.ui.open_in_default()
                opener.assert_called_once_with(self.file)
                self.pipe.send_text('\x1b')
                await asyncio.sleep(.6)
                self.assertFalse(self.task.done())
                self.assertIsNone(self.ui.current)
                self.assertFalse(self.ui.busy)
        finally:
            release.set()

    async def test_escape_returns_to_hints_and_opens_second_file_without_restart(self):
        other = self.root/'second.py'
        other.write_text('second preview')
        await self.launch(text=f'`{self.file}`\n`{other}`', columns=140)
        self.ui.app.ttimeoutlen = .01
        original_grid = self.ui.grid
        self.pipe.send_text('1')
        await asyncio.sleep(.1)
        self.assertEqual(self.ui.current.path, self.file)
        self.pipe.send_text('\x1b')
        await asyncio.sleep(.1)
        self.assertFalse(self.task.done(), 'Esc from preview must keep QuickView running')
        self.assertIsNone(self.ui.current)
        self.assertIs(self.ui.grid, original_grid)
        self.pipe.send_text('2')
        await asyncio.sleep(.1)
        self.assertEqual(self.ui.current.path, other)
        self.assertEqual(self.ui.body.text, 'second preview')
        self.pipe.send_text('q')
        await asyncio.sleep(.1)
        self.assertFalse(self.task.done())
        self.assertIsNone(self.ui.current)
        self.pipe.send_text('\x1b')
        await asyncio.wait_for(asyncio.shield(self.task), 1)

    async def test_preview_button_returns_to_selection(self):
        await self.launch()
        self.ui.app.ttimeoutlen = .01
        self.pipe.send_text('1')
        await asyncio.sleep(.1)
        screen = self.ui.app.renderer._last_screen
        header = ''.join(screen.data_buffer[0][x].char for x in range(80))
        self.assertIn('返回', header)
        x = next(x for x in range(80) if screen.data_buffer[0][x].char == '返')
        await self.click(x, 0)
        self.assertFalse(self.task.done())
        self.assertIsNone(self.ui.current)
        self.pipe.send_text('1')
        await asyncio.sleep(.1)
        self.assertEqual(self.ui.current.path, self.file)
        self.pipe.send_text('\x11')
        await asyncio.wait_for(asyncio.shield(self.task), 1)

    async def test_native_close_returns_to_selection_for_another_file(self):
        other = self.root/'second.txt'
        other.write_text('second preview')
        await self.launch(native=True, text=f'`{self.file}`\n`{other}`', columns=140)
        self.ui.app.ttimeoutlen = .01
        closed = threading.Event()
        class PreviewProcess:
            def wait(self):
                closed.wait(3)
                return 0
            def poll(self):
                return 0 if closed.is_set() else None
            def terminate(self):
                closed.set()
        def open_preview(native, path):
            native.process = PreviewProcess()
        try:
            with patch.object(NativePreview, 'open', autospec=True, side_effect=open_preview) as native:
                self.pipe.send_text('1')
                await asyncio.sleep(.1)
                self.assertFalse(self.task.done(), 'Native preview must keep QuickView alive behind the panel')
                self.assertTrue(self.ui.busy)
                closed.set()
                await asyncio.sleep(.15)
                self.assertFalse(self.task.done())
                self.assertFalse(self.ui.busy)
                self.assertFalse(self.ui.native_opened)
                self.assertIsNone(self.ui.current)
                closed.clear()
                self.pipe.send_text('2')
                await asyncio.sleep(.1)
                self.assertEqual(native.call_count, 2)
                self.assertEqual(native.call_args.args[1], other)
                closed.set()
                await asyncio.sleep(.15)
                self.pipe.send_text('\x1b')
                await asyncio.wait_for(asyncio.shield(self.task), 1)
        finally:
            closed.set()

    async def test_return_during_web_load_cannot_overwrite_next_file(self):
        await self.launch(text=f'https://example.com/slow\n`{self.file}`', columns=140)
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=lambda _: (release.wait(3) and 'old web body', False)):
                self.pipe.send_text('1')
                await asyncio.sleep(.1)
                self.assertTrue(self.ui.busy)
                self.pipe.send_text('q2')
                await asyncio.sleep(.15)
                self.assertEqual(self.ui.current.path, self.file)
                self.assertEqual(self.ui.body.text, self.file.read_text())
                release.set()
                await asyncio.sleep(.15)
                self.assertEqual(self.ui.body.text, self.file.read_text())
                self.assertFalse(self.ui.busy)
        finally:
            release.set()

    async def test_return_cancels_old_default_app_feedback_before_next_web_load(self):
        await self.launch(text=f'`{self.file}`\nhttps://example.com/slow', columns=140)
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.open_default', side_effect=lambda _: release.wait(3)), \
                    patch('herdr_quickview.quickview.read_url', side_effect=lambda _: (release.wait(3) and 'new web body', False)):
                self.pipe.send_text('1')
                await asyncio.sleep(.1)
                self.pipe.send_text('\x0f')
                await asyncio.sleep(.1)
                self.assertTrue(self.ui.busy)
                self.pipe.send_text('q2')
                await asyncio.sleep(.1)
                self.assertTrue(self.ui.busy, 'Cancelled opener must not clear the next preview loading state')
                self.assertEqual(self.ui.current.url, 'https://example.com/slow')
                release.set()
                await asyncio.sleep(.15)
                self.assertEqual(self.ui.body.text, 'new web body')
                self.assertNotIn('已提交', self.ui.status)
        finally:
            release.set()

    async def test_return_cancels_copy_feedback_for_previous_preview(self):
        other = self.root/'second.py'
        other.write_text('second preview')
        await self.launch(text=f'`{self.file}`\n`{other}`', columns=140)
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.clipboard_write', side_effect=lambda _: release.wait(3)) as copy:
                self.pipe.send_text('1')
                await asyncio.sleep(.1)
                self.ui.copy_path()
                await asyncio.sleep(.1)
                self.pipe.send_text('q2')
                await asyncio.sleep(.1)
                release.set()
                await asyncio.sleep(.15)
                copy.assert_called_once_with(str(self.file))
                self.assertEqual(self.ui.current.path, other)
                self.assertNotIn('已复制', self.ui.status)
        finally:
            release.set()

    async def test_escape_exits_search_before_returning_to_hints(self):
        await self.launch()
        self.ui.app.ttimeoutlen = .01
        self.pipe.send_text('1')
        await asyncio.sleep(.1)
        self.pipe.send_text('\x06needle')
        await asyncio.sleep(.1)
        self.assertIs(self.ui.app.layout.current_control, self.ui.search.control)
        self.pipe.send_text('\x1b')
        await asyncio.sleep(.1)
        self.assertIsNotNone(self.ui.current)
        self.assertIs(self.ui.app.layout.current_control, self.ui.body.control)
        self.pipe.send_text('\x1b')
        await asyncio.sleep(.1)
        self.assertIsNone(self.ui.current)
        self.assertFalse(self.task.done())

    async def test_ctrl_q_terminates_native_panel_while_waiting_for_close(self):
        await self.launch(native=True)
        closed = threading.Event()
        class PreviewProcess:
            def wait(self):
                closed.wait(3)
                return 0
            def poll(self):
                return 0 if closed.is_set() else None
            def terminate(self):
                closed.set()
        self.ui.native.process = PreviewProcess()
        with patch.object(self.ui.native, 'open'):
            self.pipe.send_text('1')
            await asyncio.sleep(.1)
            self.assertTrue(self.ui.native_opened)
            self.pipe.send_text('\x11')
            await asyncio.wait_for(asyncio.shield(self.task), 1)
            self.assertTrue(closed.is_set())

    async def test_file_type_matrix_herdr_content_or_explicit_unsupported(self):
        from preview_fixtures import make_files
        files = make_files(self.root/'formats')
        await self.launch(text='\n'.join(str(file) for file in files), columns=180)
        self.assertEqual([r.path for r in self.ui.screen.hits], files)
        for file in files:
            with self.subTest(file=file.name):
                await self.ui.open(Reference(file))
                if file.suffix == '.png':
                    self.assertIsNotNone(self.ui.image)
                    self.assertEqual(self.ui.image.format, 'PNG')
                    self.assertEqual(self.ui.body.text, '')
                elif file.suffix in ('.pdf', '.zip', '.bin'):
                    self.assertIn('无法在 Herdr 内预览', self.ui.body.text)
                    self.assertNotIn('%PDF', self.ui.body.text)
                else:
                    self.assertEqual(self.ui.body.text, file.read_text())

    async def test_ctrl_p_saves_mode_and_forces_herdr_for_file(self):
        from herdr_quickview.settings import load_mode
        await self.launch(native=True)
        with patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), patch('herdr_quickview.quickview.local_macos', return_value=True):
            self.pipe.send_text('\x10')
            await asyncio.sleep(.1)
        self.assertFalse(self.ui.native_mode)
        self.assertEqual(load_mode(), 'herdr')
        with patch.object(self.ui.native, 'open') as native:
            self.pipe.send_text('1')
            await asyncio.sleep(.15)
            native.assert_not_called()
            self.assertIn('needle', self.ui.body.text)

    async def test_clickable_footer_switches_and_saves_mode(self):
        from herdr_quickview.settings import load_mode
        await self.launch(native=True)
        with patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), patch('herdr_quickview.quickview.local_macos', return_value=True):
            await self.click(2, self.ui.footer_row())
        self.assertFalse(self.ui.native_mode)
        self.assertEqual(load_mode(), 'herdr')

    async def test_saved_herdr_mode_and_non_mac_override(self):
        from herdr_quickview.settings import config_path
        config_path().parent.mkdir()
        config_path().write_text('preview_mode = "herdr"\n')
        await self.launch(native=True)
        self.assertFalse(self.ui.native_mode)
        with patch('herdr_quickview.quickview.local_macos', return_value=False), patch('herdr_quickview.quickview.save_mode') as save:
            self.ui.toggle_mode()
            save.assert_not_called()
            self.assertIn('仅支持本机 macOS', self.ui.status)

    async def test_url_digit_uses_native_explicit_argument_without_file_reads(self):
        await self.launch(native=True, text='打开 https://example.com/page?q=x#part')
        with patch.object(self.ui.native, 'wait_closed'), patch.object(self.ui.native, 'open') as native, patch('herdr_quickview.quickview.read_file') as file_read, patch('herdr_quickview.quickview.read_url') as web_read:
            self.pipe.send_text('1')
            await asyncio.sleep(.15)
            self.assertFalse(self.task.done())
            native.assert_called_once_with('https://example.com/page?q=x#part', url=True)
            file_read.assert_not_called()
            web_read.assert_not_called()

    async def test_url_mouse_uses_herdr_body_and_copies_original_link(self):
        url = 'https://example.com/page?q=%2F#part'
        await self.launch(text='打开 '+url)
        with patch('herdr_quickview.quickview.read_url', return_value=('网页标题\n正文\n链接', False)) as read, patch('herdr_quickview.quickview.read_file') as file_read, patch('herdr_quickview.quickview.get_lexer_for_filename') as lexer:
            await self.click(get_cwidth('打开 ')+2, 0)
            read.assert_called_once_with(url)
            file_read.assert_not_called()
            lexer.assert_not_called()
            self.assertIn('网页标题', self.ui.body.text)
            self.assertIn('JavaScript', self.ui.status)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(url)

    async def test_url_native_failure_falls_back_without_changing_saved_mode(self):
        await self.launch(native=True, text='https://example.com/')
        with patch.object(self.ui.native, 'open', side_effect=QuickViewError('焦点失败')), patch('herdr_quickview.quickview.read_url', return_value=('网页正文', False)), patch('herdr_quickview.quickview.save_mode') as save:
            self.pipe.send_text('1')
            await asyncio.sleep(.15)
            self.assertIn('网页正文', self.ui.body.text)
            self.assertIn('已改用 Herdr', self.ui.status)
            save.assert_not_called()

    async def test_url_herdr_failure_remains_in_popup_without_app_launch(self):
        await self.launch(text='https://example.com/')
        with patch('herdr_quickview.quickview.read_url', side_effect=QuickViewError('HTTP 404')), patch.object(self.ui.native, 'open') as native, patch('herdr_quickview.quickview.cli') as cli:
            self.pipe.send_text('1')
            await asyncio.sleep(.15)
            self.assertIn('HTTP 404', self.ui.body.text)
            native.assert_not_called()
            cli.assert_not_called()

    async def test_esc_during_url_load_is_responsive(self):
        await self.launch(text='https://example.com/')
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=lambda _: (release.wait(3) and 'body', False)):
                self.pipe.send_text('1')
                await asyncio.sleep(.1)
                self.assertTrue(self.ui.busy)
                with patch('herdr_quickview.quickview.save_mode') as save:
                    self.ui.toggle_mode()
                    save.assert_not_called()
                self.pipe.send_text('\x1b')
                await asyncio.sleep(.6)
                self.assertFalse(self.task.done())
                self.assertIsNone(self.ui.current)
                self.assertFalse(self.ui.busy)
        finally:
            release.set()

    async def test_url_has_total_ui_deadline(self):
        await self.launch(text='https://example.com/')
        release = threading.Event()
        try:
            with patch('herdr_quickview.quickview.WEB_TIMEOUT', .15), patch('herdr_quickview.quickview.read_url', side_effect=lambda _: (release.wait(3) and 'body', False)):
                self.pipe.send_text('1')
                await asyncio.sleep(.3)
                self.assertFalse(self.ui.busy)
                self.assertIn('网页读取超时', self.ui.body.text)
        finally:
            release.set()

    async def test_mode_save_error_does_not_change_ui_mode(self):
        await self.launch(native=True)
        with patch('herdr_quickview.quickview.local_macos', return_value=True), patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), patch('herdr_quickview.quickview.save_mode', side_effect=QuickViewError('只读目录')):
            self.ui.toggle_mode()
            self.assertTrue(self.ui.native_mode)
            self.assertIn('只读目录', self.ui.status)

    async def test_original_text_stays_in_place_and_mouse_opens_preview(self):
        await self.launch()
        screen = self.ui.app.renderer._last_screen
        self.assertEqual(''.join(screen.data_buffer[0][x].char for x in range(4)), '原 C')
        self.assertIsNone(self.ui.current)
        await self.click(1, 2)
        self.assertIsNone(self.ui.current, 'Ordinary text must not open a file')
        with patch('herdr_quickview.quickview.rpc') as rpc, patch('herdr_quickview.quickview.cli') as cli:
            await self.click(get_cwidth('修改了 `')+1, 1)
            self.assertEqual(self.ui.current, Reference(self.file, 3))
            self.assertIn('needle', self.ui.body.text)
            self.assertEqual(self.ui.body.document.cursor_position_row, 2)
            rpc.assert_not_called()
            cli.assert_not_called()
        self.pipe.send_text('\x1b')
        await asyncio.sleep(.6)
        self.assertFalse(self.task.done())
        self.assertIsNone(self.ui.current)
        self.assertFalse(self.ui.busy)

    async def test_click_on_wrapped_continuation_uses_complete_path(self):
        logical = '修改了 `中文 note.py:3`\n日志'
        rendered = logical.replace('note.py', 'no\nte.py')
        await self.launch(text=rendered, logical=logical)
        await self.click(1, 1)
        self.assertEqual(self.ui.current.path, self.file)
        self.assertIn('needle', self.ui.body.text)

    async def test_click_cli_indented_continuation_copies_complete_versioned_path(self):
        file = self.root/'SAMPLE-v8.12.10 App前端对接.md'
        file.write_text('preview')
        text = f'对接文档 ({file})，与验收一致。'.replace('App前端', 'App前\n  端')
        await self.launch(text=text, columns=160)
        await self.click(2, 1)
        self.assertEqual(self.ui.current.path, file)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(str(file))

    async def test_click_nested_markdown_path_opens_exact_native_file(self):
        file = self.root/'folder(1)'/'file[copy].py'
        file.parent.mkdir()
        file.write_text('first\nsecond\nthird\n')
        text = f'修改 [文件]({file}:3:8) 已完成'
        await self.launch(native=True, text=text, columns=180)
        with patch.object(self.ui.native, 'wait_closed'), patch.object(self.ui.native, 'open') as native:
            await self.click(get_cwidth(text[:text.index(str(file))])+4, 0)
            await asyncio.sleep(.15)
            self.assertFalse(self.task.done())
            native.assert_called_once_with(file)

    async def test_number_selects_second_uri_target_and_keeps_line_anchor(self):
        other = self.root/'other.py'
        other.write_text('other')
        await self.launch(text=f'无效 /missing/file.py\n修改 `{other}`\n位置 {self.file.as_uri()}#L3', columns=180)
        self.pipe.send_text('2')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current, Reference(self.file, 3))
        self.assertEqual(self.ui.body.document.cursor_position_row, 2)

    async def test_shell_escaped_source_opens_and_copies_decoded_path(self):
        source = './'+self.file.name.replace(' ', '\\ ')
        await self.launch(text='修改 '+source+' 已完成')
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current.path, self.file)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(str(self.file))

    async def test_native_close_keeps_selection_layer_running(self):
        await self.launch(native=True)
        with patch.object(self.ui.native, 'wait_closed'), patch.object(self.ui.native, 'open') as native:
            self.pipe.send_text('\t\r')
            await asyncio.sleep(.15)
            self.assertFalse(self.task.done())
            native.assert_called_once_with(self.file)
            self.assertFalse(self.ui.native_opened)
            self.assertFalse(self.ui.busy)
            self.assertIsNone(self.ui.current)

    async def test_digit_opens_file_without_enter(self):
        await self.launch()
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current, Reference(self.file, 3))

    async def test_letter_opens_matching_file_without_enter(self):
        files = [self.root/f'file{i}.py' for i in range(11)]
        for file in files:
            file.write_text(file.name)
        await self.launch(text='\n'.join(file.name for file in files))
        self.pipe.send_text('a')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current.path, files[10])

    async def test_multiple_character_hint_waits_for_complete_code(self):
        files = [self.root/f'file{i}.py' for i in range(len(HINT_KEYS)+1)]
        for file in files:
            file.write_text(file.name)
        await self.launch(text='\n'.join(file.name for file in files), rows=50)
        self.pipe.send_text('1')
        await asyncio.sleep(.1)
        self.assertIsNone(self.ui.current)
        self.assertEqual(self.ui.typed, '1')
        self.pipe.send_text('2')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current.path, files[1])

    async def test_badge_is_clickable_and_footer_does_not_cover_it(self):
        await self.launch()
        x, y = next((x, y) for y, row in enumerate(self.ui.grid) for x, cell in enumerate(row)
                    if cell and cell[0] == 'class:hint-badge')
        self.assertNotEqual(self.ui.footer_row(), y)
        await self.click(x, y)
        self.assertEqual(self.ui.current.path, self.file)

    async def test_native_focus_failure_falls_back_to_herdr_body(self):
        await self.launch(native=True)
        with patch.object(self.ui.native, 'open', side_effect=QuickViewError('未获得焦点')):
            await self.click(get_cwidth('修改了 `')+1, 1)
            self.assertIn('needle', self.ui.body.text)
            self.assertIn('已改用 Herdr', self.ui.status)

    async def test_resize_disables_old_hitboxes(self):
        await self.launch()
        self.output.size = Size(rows=24, columns=79)
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        await self.click(get_cwidth('修改了 `')+1, 1)
        self.assertIsNone(self.ui.current)
        self.assertIn('大小已变化', ''.join(item[1] for item in self.ui.footer()))

    async def test_cancel_does_not_open_any_preview(self):
        await self.launch(native=True)
        with patch.object(self.ui.native, 'open') as native:
            self.pipe.send_text('\x1b')
            await asyncio.wait_for(asyncio.shield(self.task), 1)
            native.assert_not_called()

    async def test_clipped_path_is_not_a_keyboard_target(self):
        await self.launch(text='`中文 note.py`', source_y=-1)
        self.assertEqual(self.ui.selectable, [])
        self.pipe.send_text('\r')
        await asyncio.sleep(.1)
        self.assertIsNone(self.ui.current)

    async def test_footer_never_covers_a_clickable_path(self):
        await self.launch(text='\n'.join(['`中文 note.py`']*24))
        self.assertIsNone(self.ui.footer_row())
        self.assertEqual(self.ui.selection.floats, [])

    async def test_esc_remains_responsive_during_native_launch(self):
        await self.launch(native=True)
        release = threading.Event()
        try:
            with patch.object(self.ui.native, 'open', side_effect=lambda _: release.wait(3)):
                await self.click(get_cwidth('修改了 `')+1, 1)
                self.pipe.send_text('\x1b')
                await asyncio.wait_for(asyncio.shield(self.task), 1)
        finally:
            release.set()

    async def test_copy_uses_real_path_without_terminal_wraps(self):
        await self.launch(columns=60, rows=15)
        await self.click(get_cwidth('修改了 `')+1, 1)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(str(self.file))
        self.assertGreaterEqual(self.ui.body.window.render_info.window_height, 8)


if __name__ == '__main__':
    unittest.main()
