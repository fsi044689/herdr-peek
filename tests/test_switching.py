import asyncio
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from prompt_toolkit.application import set_app

from herdr_quickview.core import read_file
from herdr_quickview.i18n import tr
import test_quickview as quickview_tests


class SwitchingUITests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch

    async def asyncSetUp(self):
        await quickview_tests.QuickViewUITests.asyncSetUp(self)
        for name, value in [('load_mode', 'herdr'), ('load_language', 'en')]:
            setting = patch('herdr_quickview.quickview.' + name, return_value=value)
            setting.start()
            self.addCleanup(setting.stop)
        self.other = self.root / 'second.py'
        self.other.write_text('second first\nsecond target\nsecond last\n')

    async def wait_for(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.sleep(.04)

    async def open_first(self, text=None):
        await self.launch(text=text or f'"{self.file}:3"\n"{self.other}:2"', columns=100)
        self.ui.app.ttimeoutlen = .01
        self.pipe.send_text('1')
        await self.wait_for(lambda: self.ui.displayed is not None and not self.ui.busy)

    def choose(self, position):
        with set_app(self.ui.app):
            self.ui.choose(self.ui.targets[position])

    async def test_fast_switch_commits_title_address_body_and_line_together_without_loading_frame(self):
        await self.open_first()
        frames = []
        self.ui.app.after_render += lambda app: frames.append((self.ui.path.text, self.ui.body.text, self.ui.loading))
        old_lexer = self.ui.lexer
        self.choose(1)
        self.assertEqual(self.ui.displayed.path, self.file)
        self.assertEqual(self.ui.path.text, str(self.file) + ':3')
        self.assertEqual(self.ui.body.text, self.file.read_text())
        self.assertIs(self.ui.lexer, old_lexer)
        self.assertEqual(self.ui.target_row, 2)
        await self.wait_for(lambda: not self.ui.busy)
        self.assertEqual(self.ui.displayed.path, self.other)
        self.assertEqual(self.ui.target_row, 1)
        allowed = {(str(self.file) + ':3', self.file.read_text(), False),
                   (str(self.other) + ':2', self.other.read_text(), False)}
        self.assertTrue(frames)
        self.assertTrue(all(frame in allowed for frame in frames), frames)
        with set_app(self.ui.app):
            self.assertIn(self.other.name + ':2', self.ui.preview_title())
        await asyncio.sleep(.2)
        self.assertFalse(self.ui.loading)
        self.assertEqual(self.ui.body.text, self.other.read_text())

    async def test_slow_web_load_changes_all_fields_to_loading_then_content_and_copy_tracks_display(self):
        started, release = threading.Event(), threading.Event()
        url = 'https://example.com/slow'
        await self.open_first(text=f'"{self.file}:3"\n{url}')
        def read(_):
            started.set()
            release.wait(5)
            return 'web result', False
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=read), \
                    patch('herdr_quickview.quickview.clipboard_write') as copy, \
                    patch('herdr_quickview.quickview.open_default') as opener:
                self.choose(1)
                self.assertEqual(self.ui.path.text, str(self.file) + ':3')
                self.ui.copy_path()
                await self.wait_for(lambda: copy.called)
                copy.assert_called_once_with(str(self.file))
                await self.wait_for(lambda: started.is_set() and self.ui.loading)
                self.assertTrue(self.ui.busy)
                self.assertEqual(self.ui.path.text, url)
                self.assertEqual(self.ui.displayed.url, url)
                self.assertEqual(self.ui.body.text, tr('正在读取网页… Esc 可取消'))
                self.assertIsNone(self.ui.lexer)
                self.assertIsNone(self.ui.target_row)
                self.ui.copy_path()
                self.ui.open_in_default()
                await self.wait_for(lambda: copy.call_count == 2)
                self.assertEqual(copy.call_args.args, (url,))
                opener.assert_not_called()
                release.set()
                await self.wait_for(lambda: not self.ui.busy)
                self.assertEqual(self.ui.body.text, 'web result')
                self.assertFalse(self.ui.loading)
        finally:
            release.set()

    async def test_return_to_displayed_file_during_debounce_preserves_cursor_scroll_and_skips_read(self):
        self.file.write_text('\n'.join(f'line {i}' for i in range(100)))
        await self.open_first()
        self.ui.body.buffer.cursor_position = self.ui.body.document.translate_row_col_to_index(45, 0)
        self.ui.app.invalidate()
        await asyncio.sleep(.06)
        cursor = self.ui.body.buffer.cursor_position
        scroll = self.ui.body.window.vertical_scroll
        with patch('herdr_quickview.quickview.read_file', wraps=read_file) as read:
            self.pipe.send_text('\x0a\x0b')
            await self.wait_for(lambda: not self.ui.busy and self.ui.current.path == self.file)
            await asyncio.sleep(.3)
            read.assert_not_called()
        self.assertEqual(self.ui.body.buffer.cursor_position, cursor)
        self.assertEqual(self.ui.body.window.vertical_scroll, scroll)
        self.assertEqual(self.ui.target_row, 2)

    async def test_cancelled_slow_target_cannot_show_loading_or_overwrite_old_file(self):
        started, release = threading.Event(), threading.Event()
        url = 'https://example.com/cancelled'
        await self.open_first(text=f'"{self.file}:3"\n{url}')
        def read(_):
            started.set()
            release.wait(5)
            return 'stale result', False
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=read):
                self.pipe.send_text('\x0a')
                await self.wait_for(started.is_set)
                self.pipe.send_text('\x0b')
                await self.wait_for(lambda: not self.ui.busy)
                release.set()
                await asyncio.sleep(.35)
                self.assertEqual(self.ui.displayed.path, self.file)
                self.assertEqual(self.ui.path.text, str(self.file) + ':3')
                self.assertEqual(self.ui.body.text, self.file.read_text())
                self.assertFalse(self.ui.loading)
        finally:
            release.set()

    async def test_dequeued_loading_callback_cannot_commit_after_cancel_and_same_target_reselection(self):
        started, release = threading.Event(), threading.Event()
        await self.open_first(text=f'"{self.file}:3"\nhttps://example.com/reselect')
        callbacks = []
        loop = asyncio.get_running_loop()
        original = loop.call_later
        def schedule(delay, callback, *args, **kwargs):
            if getattr(callback, '__name__', '') == 'show_loading':
                callbacks.append(callback)
            return original(delay, callback, *args, **kwargs)
        def read(_):
            started.set()
            release.wait(5)
            return 'new web result', False
        try:
            with patch.object(loop, 'call_later', side_effect=schedule), \
                    patch('herdr_quickview.quickview.read_url', side_effect=read):
                self.choose(1)
                await self.wait_for(started.is_set)
                self.assertEqual(len(callbacks), 1)
                loop.call_soon(callbacks[0])
                self.choose(0)
                self.choose(1)
                await asyncio.sleep(0)
                self.assertEqual(self.ui.displayed.path, self.file)
                self.assertEqual(self.ui.body.text, self.file.read_text())
                self.assertFalse(self.ui.loading)
                release.set()
                await self.wait_for(lambda: not self.ui.busy)
                self.assertEqual(self.ui.body.text, 'new web result')
        finally:
            release.set()

    async def test_return_to_a_loading_display_restarts_read_instead_of_leaving_placeholder(self):
        started, release = threading.Event(), threading.Event()
        calls = 0
        def read(path):
            nonlocal calls
            if path == self.file:
                calls += 1
                if calls == 1:
                    started.set()
                    release.wait(5)
            return read_file(path)
        try:
            with patch('herdr_quickview.quickview.read_file', side_effect=read):
                await self.launch(text=f'"{self.file}"\n"{self.other}"', columns=100)
                self.pipe.send_text('1')
                await self.wait_for(started.is_set)
                self.assertTrue(self.ui.loading)
                self.pipe.send_text('\x0a\x0b')
                await self.wait_for(lambda: not self.ui.busy)
                self.assertEqual(calls, 2)
                self.assertEqual(self.ui.body.text, self.file.read_text())
                self.assertFalse(self.ui.loading)
                release.set()
                await asyncio.sleep(.2)
                self.assertEqual(self.ui.body.text, self.file.read_text())
        finally:
            release.set()

    async def test_copy_finishing_after_switch_does_not_replace_new_file_status(self):
        started, release = threading.Event(), threading.Event()
        await self.open_first()
        def copy(_):
            started.set()
            release.wait(5)
        try:
            with patch('herdr_quickview.quickview.clipboard_write', side_effect=copy):
                self.choose(1)
                self.ui.copy_path()
                await self.wait_for(started.is_set)
                await self.wait_for(lambda: not self.ui.busy)
                expected = self.ui.status
                release.set()
                await asyncio.sleep(.15)
                self.assertEqual(self.ui.status, expected)
                self.assertEqual(self.ui.body.text, self.other.read_text())
        finally:
            release.set()

    async def test_external_app_wait_does_not_disable_copy_or_search(self):
        started, release = threading.Event(), threading.Event()
        await self.open_first()
        def open_app(_):
            started.set()
            release.wait(5)
        try:
            with patch('herdr_quickview.quickview.open_default', side_effect=open_app), \
                    patch('herdr_quickview.quickview.clipboard_write') as copy:
                self.pipe.send_text('\x0f')
                await self.wait_for(started.is_set)
                self.assertTrue(self.ui.busy)
                self.ui.copy_path()
                self.pipe.send_text('\x06no_match')
                await self.wait_for(lambda: self.ui.app.layout.is_searching and copy.called)
                copy.assert_called_once_with(str(self.file))
                with set_app(self.ui.app):
                    self.assertIn(tr('未找到'), ''.join(f[1] for f in self.ui.preview_status()))
                release.set()
                await self.wait_for(lambda: not self.ui.busy)
        finally:
            release.set()

    async def test_arrows_preview_clamp_at_ends_and_do_not_request_intermediate_urls(self):
        url = 'https://example.com/skip'
        await self.open_first(text=f'"{self.file}"\n{url}\n"{self.other}"')
        with patch('herdr_quickview.quickview.read_url') as web:
            self.pipe.send_text('\t\x1b[B\x1b[B')
            await self.wait_for(lambda: not self.ui.busy and self.ui.current.path == self.other)
            web.assert_not_called()
            self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
            self.pipe.send_text('\x1b[B\r')
            await asyncio.sleep(.1)
            self.assertEqual(self.ui.sidebar_index, 2)
            self.pipe.send_text('\x1b[A\x1b[A\x1b[A')
            await self.wait_for(lambda: not self.ui.busy and self.ui.current.path == self.file)
            self.assertEqual(self.ui.sidebar_index, 0)
            web.assert_not_called()

    async def test_image_stays_visible_during_debounce_and_focus_migrates_between_content_controls(self):
        picture = self.root / 'image.png'
        Image.new('RGB', (30, 20), 'red').save(picture)
        await self.open_first(text=f'"{self.file}"\n"{picture}"')
        self.pipe.send_text('\x0a')
        await self.wait_for(lambda: not self.ui.busy and self.ui.displayed.path == picture)
        self.assertIs(self.ui.app.layout.current_control, self.ui.image_window.content)
        image = self.ui.image
        self.choose(0)
        self.assertIs(self.ui.image, image)
        self.assertEqual(self.ui.displayed.path, picture)
        self.assertIs(self.ui.app.layout.current_control, self.ui.image_window.content)
        await self.wait_for(lambda: not self.ui.busy)
        self.assertIsNone(self.ui.image)
        self.assertIs(self.ui.app.layout.current_control, self.ui.body.control)
        self.pipe.send_text('\t\x1b[B')
        await self.wait_for(lambda: not self.ui.busy and self.ui.displayed.path == picture)
        self.assertIsNotNone(self.ui.image)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)

    async def test_back_cancels_loading_timer_and_clears_both_targets(self):
        started, release = threading.Event(), threading.Event()
        await self.open_first(text=f'"{self.file}"\nhttps://example.com/slow')
        def read(_):
            started.set()
            release.wait(5)
            return 'stale', False
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=read):
                self.pipe.send_text('\x0a')
                await self.wait_for(started.is_set)
                self.pipe.send_text('\x1b')
                await self.wait_for(lambda: self.ui.current is None)
                release.set()
                await asyncio.sleep(.3)
                self.assertIsNone(self.ui.displayed)
                self.assertFalse(self.ui.loading)
                self.assertFalse(self.ui.busy)
                self.assertFalse(self.task.done())
        finally:
            release.set()
