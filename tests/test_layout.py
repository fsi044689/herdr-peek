import asyncio
import unittest
from unittest.mock import patch

from prompt_toolkit.application import set_app
from prompt_toolkit.utils import get_cwidth

import test_quickview as quickview_tests


class LayoutUITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = quickview_tests.QuickViewUITests.asyncSetUp
    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch
    click = quickview_tests.QuickViewUITests.click

    async def key(self, text):
        self.pipe.send_text(text)
        await asyncio.sleep(.2)

    def screen_row(self, row):
        screen = self.ui.app.renderer._last_screen
        return ''.join(screen.data_buffer[row][x].char for x in range(self.output.size.columns))

    def title(self):
        with set_app(self.ui.app):
            return self.ui.preview_title().strip()

    async def check_toolbar(self, columns):
        await self.launch(text=f'"{self.file}:3"', columns=columns)
        await self.key('1')
        for _ in range(2):
            header = self.screen_row(0)
            self.assertNotIn('Window too small', header)
            self.assertNotIn('Herdr QuickView', header)
            self.assertIn(self.file.name + ':3', header)
            for button in self.ui.preview_buttons:
                self.assertIn(button.text, header)
            self.assertIn('needle', self.ui.body.text)
            await self.key('\x0c')

    async def test_toolbar_fits_sixty_columns_in_both_languages(self):
        await self.check_toolbar(60)

    async def test_toolbar_fits_eighty_columns_in_both_languages(self):
        await self.check_toolbar(80)

    async def test_navigation_hints_fit_all_focus_states_in_both_languages(self):
        await self.launch(columns=60)
        await self.key('1')
        self.ui.toggle_sidebar()
        for _ in range(2):
            for control in (self.ui.body.control, self.ui.sidebar_control, self.ui.path.control):
                self.ui.app.layout.focus(control)
                self.ui.app.invalidate()
                await asyncio.sleep(.1)
                with set_app(self.ui.app):
                    text = ''.join(f[1] for f in self.ui.preview_navigation())
                self.assertLessEqual(get_cwidth(text), 60, text)
                self.assertEqual(self.screen_row(1).rstrip(), text.rstrip())
                if control == self.ui.path.control:
                    self.assertIn('Home/End', text)
                    await self.key('\t')
                    self.assertIs(self.ui.app.layout.current_control, self.ui.sidebar_control)
                if control == self.ui.sidebar_control:
                    self.assertIn('Enter', text)
            self.ui.toggle_sidebar()
            self.ui.app.layout.focus(self.ui.path.control)
            self.ui.app.invalidate()
            await asyncio.sleep(.1)
            self.assertIn('Tab', self.screen_row(1))
            await self.key('\t')
            self.assertIs(self.ui.app.layout.current_control, self.ui.body.control)
            self.ui.toggle_sidebar()
            await self.key('\x0c')

    async def test_title_updates_and_long_name_keeps_extension_and_line(self):
        other = self.root/('shared-prefix-' * 6 + '中文.md')
        other.write_text('first\nsecond\n')
        await self.launch(text=f'"{self.file}:3"\n"{other}:2"', columns=60)
        await self.key('1')
        self.assertIn(self.file.name + ':3', self.screen_row(0))
        await self.key('\x0a')
        title = self.title()
        self.assertIn('…', title)
        self.assertTrue(title.endswith('.md:2'), title)
        self.assertIn(title.strip(), self.screen_row(0))
        self.assertEqual(self.ui.path.text, str(other) + ':2')

    async def test_url_title_keeps_host_and_resource_without_changing_address(self):
        url = 'https://example.com/' + 'segment/' * 12 + 'report.json?token=private#section'
        await self.launch(text=url, logical=url, columns=80)
        with patch('herdr_quickview.quickview.read_url', return_value=('web preview', False)):
            await self.key('1')
        title = self.title()
        self.assertIn('example.com', title)
        self.assertTrue(title.endswith('report.json'), title)
        self.assertNotIn('token=', title)
        self.assertEqual(self.ui.path.text, url)
        self.assertIn(title.strip(), self.screen_row(0))

    async def test_mode_shortcut_only_changes_preference_from_selection(self):
        await self.launch(columns=80)
        await self.key('1')
        status, mode = self.ui.status, self.ui.mode
        with patch('herdr_quickview.quickview.save_mode') as save, \
                patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), \
                patch('herdr_quickview.quickview.local_macos', return_value=True):
            await self.key('\x10')
            self.ui.toggle_mode()
            save.assert_not_called()
            self.assertEqual((self.ui.status, self.ui.mode), (status, mode))
            self.ui.back()
            await self.key('\x10')
            save.assert_called_once_with('macos')

    async def test_sidebar_hierarchy_preserves_current_and_selected_styles(self):
        other = self.root/'other.py'
        other.write_text('other = 1\n')
        await self.launch(text=f'"{self.file}"\n"{other}"', columns=80)
        await self.key('1')
        def rows():
            return [f for f in self.ui.sidebar_fragments() if f[1] != '\n']
        fragments = rows()
        self.assertTrue(all(f[0] == 'class:sidebar-current' for f in fragments[:2]))
        attrs = self.ui.app.renderer._attrs_for_style
        self.assertTrue(attrs[fragments[2][0]].bold)
        self.assertEqual(attrs[fragments[3][0]].color, 'ansibrightblack')
        await self.key('\t\x1b[B')
        fragments = rows()
        self.assertTrue(all(f[0] == 'class:sidebar-current' for f in fragments[:2]))
        self.assertTrue(all(f[0] == 'class:sidebar-selected' for f in fragments[2:]))
        self.assertTrue(all(attrs[f[0]].reverse for f in fragments[2:]))

    async def test_compact_copy_and_back_buttons_work_with_mouse(self):
        await self.launch(columns=60)
        await self.key('1')
        for _ in range(2):
            start = 60 - sum(button.width for button in self.ui.preview_buttons)
            copy_x = start + self.ui.open_button.width + self.ui.copy_button.width // 2
            with patch('herdr_quickview.quickview.clipboard_write') as copy:
                await self.click(copy_x, 0)
                copy.assert_called_once_with(str(self.file))
            await self.key('\x0c')
        await self.click(60 - self.ui.back_button.width // 2, 0)
        self.assertIsNone(self.ui.current)
        self.assertFalse(self.task.done())
