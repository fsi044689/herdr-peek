import asyncio
import threading
import unittest
from unittest.mock import patch

from prompt_toolkit.utils import get_cwidth

from herdr_quickview.core import QuickViewError, read_file
from herdr_quickview.i18n import get_language, set_language
from herdr_quickview.links import URLReference
import test_quickview as quickview_tests


class SidebarUITests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch
    click = quickview_tests.QuickViewUITests.click

    async def asyncSetUp(self):
        await quickview_tests.QuickViewUITests.asyncSetUp(self)
        previous = get_language()
        self.addCleanup(set_language, previous)
        language = patch('herdr_quickview.quickview.load_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.other = self.root/'second.py'
        self.other.write_text('second preview\n')

    async def key(self, sequence):
        self.pipe.send_text(sequence)
        await asyncio.sleep(.2)

    async def launch_pair(self, **kw):
        await self.launch(text=f'`{self.file}`\n`{self.other}`', **kw)
        self.ui.app.ttimeoutlen = .01
        await self.key('1')

    async def test_list_reuses_visible_targets_without_preloading_files_or_urls(self):
        text = f'`{self.file}`\n`{self.file}`\n`{self.other}`\nhttps://example.com/quickview'
        with patch('herdr_quickview.quickview.read_file', wraps=read_file) as file_read, \
                patch('herdr_quickview.quickview.read_url', return_value=('web preview', False)) as web_read:
            await self.launch(text=text)
            file_read.assert_not_called()
            web_read.assert_not_called()
            self.assertEqual(len(self.ui.targets), 3)
            await self.key('1')
            file_read.assert_called_once_with(self.file)
            web_read.assert_not_called()
            await self.key('\x0b')
            web_read.assert_called_once_with('https://example.com/quickview')
            self.assertEqual(self.ui.current, URLReference('https://example.com/quickview'))
            self.assertEqual(self.ui.body.text, 'web preview')

    async def test_url_shows_site_and_resource_without_repeating_the_address(self):
        url = 'https://herdr.dev/plugins/'
        await self.launch(text=url, columns=120)
        with patch('herdr_quickview.quickview.read_url', return_value=('web preview', False)):
            await self.key('1')
        lines = [fragment[1].strip() for fragment in self.ui.sidebar_fragments() if fragment[1] != '\n']
        self.assertEqual(lines, ['▸ https://herdr.dev', '/plugins/'])

    async def test_url_resource_keeps_double_slashes_and_query_fragment_verbatim(self):
        url = 'https://example.com//double/slash?q=%2F#part'
        await self.launch(text=url, columns=180)
        with patch('herdr_quickview.quickview.read_url', return_value=('web preview', False)):
            await self.key('1')
        lines = [fragment[1].strip() for fragment in self.ui.sidebar_fragments() if fragment[1] != '\n']
        self.assertEqual(lines, ['▸ https://example.com', '//double/slash?q=%2F#part'])
        self.assertEqual(self.ui.path.text, url)

    async def test_shortcuts_switch_preview_with_list_visible_or_collapsed(self):
        await self.launch_pair()
        self.assertTrue(self.ui.sidebar_visible)
        full_width = self.ui.body.window.render_info.window_width
        await self.key('\x0a')
        self.assertEqual(self.ui.current.path, self.other)
        await self.key('\x0b')
        self.assertEqual(self.ui.current.path, self.file)
        await self.key('\x14')
        self.assertFalse(self.ui.sidebar_visible)
        self.assertGreater(self.ui.body.window.render_info.window_width, full_width)
        await self.key('\x0a')
        self.assertEqual(self.ui.body.text, self.other.read_text())
        await self.key('\t')
        self.assertIs(self.ui.app.layout.current_control, self.ui.body.control)
        await self.key('\x14')
        self.assertTrue(self.ui.sidebar_visible)

    async def test_list_arrows_only_select_then_enter_previews_without_moving_focus(self):
        await self.launch_pair()
        await self.key('\t\x1b[B')
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
        self.assertEqual(self.ui.sidebar_index, 1)
        self.assertEqual(self.ui.current.path, self.file)
        await self.key('\r')
        self.assertEqual(self.ui.current.path, self.other)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
        await self.key('\x0a')
        self.assertEqual(self.ui.current.path, self.file)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
        await self.key('\x0b')
        self.assertEqual(self.ui.current.path, self.other)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
        await self.key('\x14')
        self.assertIs(self.ui.app.layout.current_control, self.ui.body.control)
        await self.key('\x1b')
        self.assertIsNone(self.ui.current)
        self.assertFalse(self.task.done())

    async def test_mouse_selects_another_file_and_toggles_the_list(self):
        await self.launch_pair(columns=100)
        screen = self.ui.app.renderer._last_screen
        row = next(y for y in range(24) if 'second.py' in ''.join(screen.data_buffer[y][x].char for x in range(33)))
        await self.click(7, row)
        self.assertEqual(self.ui.current.path, self.other)
        self.assertEqual(self.ui.body.text, self.other.read_text())
        await self.click(4, 1)
        self.assertFalse(self.ui.sidebar_visible)
        await self.click(4, 1)
        self.assertTrue(self.ui.sidebar_visible)

    async def test_switch_away_from_slow_url_does_not_replace_the_next_file(self):
        release = threading.Event()
        await self.launch(text=f'`{self.file}`\nhttps://example.com/slow\n`{self.other}`', columns=120)
        try:
            with patch('herdr_quickview.quickview.read_url', side_effect=lambda _: (release.wait(3) and 'old web body', False)):
                await self.key('1')
                await self.key('\x0a')
                self.assertTrue(self.ui.busy)
                await self.key('\x0a')
                self.assertEqual(self.ui.current.path, self.other)
                self.assertEqual(self.ui.body.text, self.other.read_text())
                release.set()
                await asyncio.sleep(.15)
                self.assertEqual(self.ui.body.text, self.other.read_text())
                self.assertFalse(self.ui.busy)
        finally:
            release.set()

    async def test_list_scrolls_to_keyboard_selection_and_search_preserves_file(self):
        files = []
        for n in range(14):
            file = self.root/f'file-{n:02}.py'
            file.write_text(f'preview {n}')
            files.append(file)
        await self.launch(text='\n'.join(map(str, files)), rows=18)
        await self.key('1')
        await self.key('\t'+'\x1b[B'*13)
        self.assertEqual(self.ui.sidebar_index, 13)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
        screen = self.ui.app.renderer._last_screen
        visible = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(26)) for y in range(18))
        self.assertIn('file-13.py', visible)
        await self.key('\r')
        await self.key('\x06preview')
        self.assertEqual(self.ui.current.path, files[-1])
        self.assertIs(self.ui.app.layout.current_control, self.ui.search.control)
        await self.key('\x1b')
        self.assertEqual(self.ui.current.path, files[-1])

    async def test_long_unicode_names_fit_the_sidebar_at_narrow_width(self):
        file = self.root/('中文🚀'*12+'.md')
        file.write_text('wide name preview')
        await self.launch(text=str(file), columns=60)
        await self.key('1')
        self.assertFalse(self.ui.sidebar_visible)
        await self.key('\x14')
        width = max(16, min(34, 60 // 3)) - 1
        for _, line, *handler in self.ui.sidebar_fragments():
            if line != '\n':
                self.assertEqual(get_cwidth(line), width)
        self.assertIn('…', ''.join(fragment[1] for fragment in self.ui.sidebar_fragments()))
        self.assertEqual(self.ui.current.path, file)
        await self.key('\x14')
        self.assertEqual(self.ui.body.text, 'wide name preview')

    async def test_same_names_use_directory_context_and_keep_extensions_and_line_numbers(self):
        name = 'shared-prefix-' * 4 + '中文版本.md'
        first = self.root/'packages'/'service-alpha'/'src'/name
        second = self.root/'packages'/'service-beta'/'src'/name
        for path in (first, second):
            path.parent.mkdir(parents=True)
            path.write_text('same name preview')
        await self.launch(text=f'"{first}:123"\n"{second}:456"', columns=90)
        await self.key('1')
        lines = [fragment[1].strip() for fragment in self.ui.sidebar_fragments() if fragment[1] != '\n']
        self.assertTrue(lines[0].endswith('.md:123'), lines)
        self.assertTrue(lines[2].endswith('.md:456'), lines)
        self.assertNotEqual(lines[1], lines[3])
        self.assertIn('…', lines[0])
        self.assertEqual(self.ui.current.path, first)
        self.assertEqual(self.ui.current.line, 123)

    async def test_cwd_file_uses_directory_instead_of_repeating_its_name(self):
        await self.launch(text="`"+self.file.name+"`")
        await self.key('1')
        lines = [fragment[1].strip() for fragment in self.ui.sidebar_fragments() if fragment[1] != '\n']
        self.assertEqual(lines, ['▸ '+self.file.name, './'])

    async def test_long_complete_address_wraps_is_navigable_and_copies_without_truncation(self):
        file = self.root
        for n in range(7):
            file = file/('中文 directory with spaces '+str(n))
        file.mkdir(parents=True)
        file = file/'最后一个文件-final-version.py'
        file.write_text('full address preview')
        await self.launch(text='"'+str(file)+'"', columns=80,
                          logical='"'+str(file)+'"')
        await self.key('1')
        self.assertEqual(self.ui.path.text, str(file))
        self.assertEqual(self.ui.path.window.render_info.window_height, 3)
        self.assertGreater(self.ui.body.window.render_info.window_height, 8)
        self.ui.app.layout.focus(self.ui.path)
        await self.key('\x05')
        self.assertEqual(self.ui.path.buffer.cursor_position, len(str(file)))
        screen = self.ui.app.renderer._last_screen
        visible = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(80)) for y in range(24))
        self.assertIn('final-version.py', visible)
        self.assertIn('Home/End', visible)
        await self.key('\x1b[H')
        self.assertEqual(self.ui.path.buffer.cursor_position, 0)
        await self.key('\x1b[F')
        self.assertEqual(self.ui.path.buffer.cursor_position, len(str(file)))
        screen = self.ui.app.renderer._last_screen
        visible = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(80)) for y in range(24))
        self.assertIn('final-version.py', visible)
        await self.key('\x01')
        self.assertEqual(self.ui.path.buffer.cursor_position, 0)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(str(file))
        self.assertEqual(self.ui.body.text, 'full address preview')

    async def test_long_url_keeps_host_resource_tail_and_full_copy_value(self):
        url = 'https://example.com:8443/' + 'shared-segment/' * 25 + 'report.json?mode=full#L42'
        await self.launch(text=url, columns=100, logical=url)
        with patch('herdr_quickview.quickview.read_url', return_value=('web preview', False)) as read:
            await self.key('1')
            read.assert_called_once_with(url)
        lines = [fragment[1].strip() for fragment in self.ui.sidebar_fragments() if fragment[1] != '\n']
        self.assertEqual(lines[0], '▸ https://example.com:8443')
        self.assertTrue(lines[1].endswith('full#L42'), lines)
        self.assertIn('…', lines[1])
        self.assertEqual(self.ui.path.text, url)
        self.assertLessEqual(self.ui.path.window.render_info.window_height, 3)
        with patch('herdr_quickview.quickview.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.1)
            copy.assert_called_once_with(url)

    async def test_mixed_list_scroll_and_click_resource_row_open_correct_target(self):
        urls = ['https://example.com/resource-'+str(n) for n in range(12)]
        await self.launch(text='\n'.join([str(self.file), *urls, str(self.other)]), rows=18)
        with patch('herdr_quickview.quickview.read_url', return_value=('selected web preview', False)) as read:
            await self.key('1')
            await self.key('\t'+'\x1b[B'*12)
            read.assert_not_called()
            self.assertEqual(self.ui.current.path, self.file)
            self.assertEqual(self.ui.sidebar_index, 12)
            self.assertGreater(self.ui.sidebar.children[1].vertical_scroll, 0)
            self.assertIn('选中：', ''.join(f[1] for f in self.ui.preview_navigation()))
            self.assertTrue(''.join(f[1] for f in self.ui.preview_navigation()).endswith('ource-11'))
            await self.key('\x1b[A')
            screen = self.ui.app.renderer._last_screen
            row = next(y for y in range(18) if '/resource-10' in ''.join(screen.data_buffer[y][x].char for x in range(25)))
            await self.click(7, row)
            await asyncio.sleep(.15)
            self.assertEqual(self.ui.current, URLReference(urls[10]))
            read.assert_called_once_with(urls[10])

    async def test_native_failure_then_sidebar_switch_stays_in_herdr_preview(self):
        await self.launch(native=True, text=f'`{self.file}`\n`{self.other}`')
        with patch.object(self.ui.native, 'open', side_effect=QuickViewError('test native failure')) as native:
            await self.key('1')
            self.assertEqual(self.ui.current.path, self.file)
            self.assertTrue(self.ui.native_mode)
            await self.key('\x0a')
            self.assertEqual(self.ui.current.path, self.other)
            self.assertEqual(self.ui.body.text, self.other.read_text())
            native.assert_called_once_with(self.file)
            self.assertFalse(self.ui.busy)

    async def test_wheel_reaches_lower_targets_without_opening_them(self):
        files = []
        for n in range(20):
            file = self.root/f'wheel-{n:02}.py'
            file.write_text(str(n))
            files.append(file)
        await self.launch(text='\n'.join(map(str, files)))
        with patch('herdr_quickview.quickview.read_file', wraps=read_file) as file_read:
            await self.key('1')
            await self.key('\x1b[<65;5;7M'*30)
            self.assertEqual(self.ui.sidebar_index, 19)
            self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
            self.assertGreater(self.ui.sidebar.children[1].vertical_scroll, 0)
            self.assertEqual(self.ui.current.path, files[0])
            file_read.assert_called_once_with(files[0])
            screen = self.ui.app.renderer._last_screen
            row = next(y for y in range(24) if 'wheel-19.py' in ''.join(screen.data_buffer[y][x].char for x in range(26)))
            await self.click(7, row)
            await asyncio.sleep(.15)
            self.assertEqual(self.ui.current.path, files[-1])
            await self.key('\x1b[<64;5;7M'*30)
            self.assertEqual(self.ui.sidebar_index, 0)
            self.assertEqual(self.ui.sidebar.children[1].vertical_scroll, 0)

    async def test_rapid_switches_do_not_start_requests_for_intermediate_urls(self):
        text = f'`{self.file}`\nhttps://example.com/one\nhttps://example.com/two\n`{self.other}`'
        await self.launch(text=text, columns=120)
        with patch('herdr_quickview.quickview.read_file', wraps=read_file) as file_read, \
                patch('herdr_quickview.quickview.read_url') as web_read:
            await self.key('1')
            for _ in range(3):
                self.pipe.send_text('\x0a')
                await asyncio.sleep(.025)
            await asyncio.sleep(.2)
            self.assertEqual(self.ui.current.path, self.other)
            self.assertEqual(self.ui.body.text, self.other.read_text())
            web_read.assert_not_called()
            self.assertEqual([call.args[0] for call in file_read.call_args_list], [self.file, self.other])
            self.assertFalse(self.ui.busy)

    async def test_copy_and_open_during_debounce_do_not_use_previous_file(self):
        await self.launch_pair()
        with patch('herdr_quickview.quickview.clipboard_write') as copy, patch('herdr_quickview.quickview.open_default') as opener:
            self.pipe.send_text('\x0a')
            await asyncio.sleep(.035)
            self.assertTrue(self.ui.busy)
            self.ui.copy_path()
            self.pipe.send_text('\x0f')
            await asyncio.sleep(.04)
            copy.assert_called_once_with(str(self.other))
            opener.assert_not_called()
            await asyncio.sleep(.2)
            await self.key('\x0f')
            opener.assert_called_once_with(self.other)
            self.assertFalse(self.ui.busy)

    async def test_small_window_folds_multiple_targets_until_explicitly_expanded(self):
        await self.launch_pair(columns=60)
        self.assertFalse(self.ui.sidebar_visible)
        await self.key('\x14\t')
        self.assertTrue(self.ui.sidebar_visible)
        self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
