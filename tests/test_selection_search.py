import asyncio
import unittest
from unittest.mock import patch

from prompt_toolkit.application import set_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.utils import get_cwidth

from herdr_quickview.i18n import get_language, tr
import test_quickview as quickview_tests


class SelectionSearchUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await quickview_tests.QuickViewUITests.asyncSetUp(self)
        mode = patch('herdr_quickview.quickview.load_mode', return_value='herdr')
        mode.start()
        self.addCleanup(mode.stop)

    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch
    click = quickview_tests.QuickViewUITests.click

    async def key(self, text):
        self.pipe.send_text(text)
        await asyncio.sleep(.15)

    def row(self, y):
        screen = self.ui.app.renderer._last_screen
        return ''.join(screen.data_buffer[y][x].char for x in range(self.output.size.columns))

    def footer(self):
        return self.row(self.ui.footer_row()).rstrip()

    def status(self):
        return self.row(self.output.size.rows - 1).rstrip()

    async def check_footer(self, columns):
        self.root = self.root / ('nested-folder-' * 8)
        self.root.mkdir()
        self.file = self.root / '中文 note.py'
        self.file.write_text('first\nsecond\nneedle\nlast\n')
        with patch('herdr_quickview.quickview.load_language', return_value='en'):
            await self.launch(columns=columns)
        for _ in range(2):
            self.assertIn(tr('点击 / 输入编号'), self.footer())
            self.assertIn('Esc', self.footer())
            await self.key('\t')
            text = self.footer()
            self.assertIn(self.file.name + ':3', text)
            self.assertIn('…', text)
            with set_app(self.ui.app):
                self.assertLessEqual(get_cwidth(''.join(f[1] for f in self.ui.footer())), columns)
            self.ui.index = -1
            await self.key('\x0c')
            await self.key('\t')
            self.ui.index = -1
            self.ui.app.invalidate()
            await asyncio.sleep(.1)

    async def test_footer_sixty_columns_both_languages(self):
        await self.check_footer(60)

    async def test_footer_eighty_columns_both_languages(self):
        await self.check_footer(80)

    async def test_footer_hundred_columns_both_languages(self):
        await self.check_footer(100)

    async def test_very_long_filename_keeps_extension_and_line(self):
        file = self.root / ('long-name-' * 12 + '中文.py')
        file.write_text('first\nsecond\n')
        await self.launch(text=f'"{file.name}:2"', columns=60)
        await self.key('\t')
        self.assertTrue(self.footer().endswith('中文.py:2'), self.footer())

    async def test_mode_feedback_retires_when_selecting_next_target(self):
        with patch('herdr_quickview.quickview.load_language', return_value='en'):
            await self.launch(columns=80)
        with patch('herdr_quickview.quickview.save_mode'), \
                patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), \
                patch('herdr_quickview.quickview.local_macos', return_value=True):
            await self.key('\x10')
        self.assertIn('Saved:', self.footer())
        await self.key('\t')
        self.assertIn(self.file.name + ':3', self.footer())
        self.assertNotIn('Saved:', self.footer())
        self.assertEqual(self.ui.status, '')

    async def test_typed_prefix_precedes_feedback_and_snapshot_notice_survives_tab(self):
        text = '\n'.join(f'https://example.com/{i}' for i in range(40))
        await self.launch(text=text, columns=80, rows=45)
        self.assertTrue(all(len(label) == 2 for label in self.ui.labels.values()))
        self.ui.status = 'Permission denied: read-only'
        prefix = next(iter(self.ui.labels.values()))[0]
        await self.key(prefix)
        self.assertIsNone(self.ui.current)
        self.assertIn(tr('已输入 {keys}', keys=prefix), self.footer())
        self.assertIn('Permission denied', self.footer())
        self.ui.screen.snapshot['notice'] = 'snapshot warning'
        await self.key('\t')
        self.assertEqual(self.ui.status, 'snapshot warning')
        self.assertIn('snapshot warning', self.footer())
        self.assertEqual(self.ui.typed, '')

    async def test_compact_footer_mode_and_language_mouse_targets(self):
        await self.launch(columns=80)
        with patch('herdr_quickview.quickview.save_mode') as save, \
                patch('herdr_quickview.quickview.native_quicklook_available', return_value=True), \
                patch('herdr_quickview.quickview.local_macos', return_value=True):
            await self.click(2, self.ui.footer_row())
            save.assert_called_once_with('macos')
        previous = get_language()
        with set_app(self.ui.app):
            language_x = get_cwidth(self.ui.footer()[0][1]) + 3
        await self.click(language_x, self.ui.footer_row())
        self.assertNotEqual(get_language(), previous)

    async def test_missing_search_is_visible_red_bold_and_clears_on_escape(self):
        with patch('herdr_quickview.quickview.load_language', return_value='zh'):
            await self.launch(columns=60)
        self.ui.app.ttimeoutlen = .01
        await self.key('1')
        await self.key('\x06no_such_result')
        self.assertTrue(self.ui.app.layout.is_searching)
        self.assertTrue(self.status().startswith(' ' + tr('未找到') + ' · '), self.status())
        attrs = self.ui.app.renderer._attrs_for_style['class:status class:search-missing']
        self.assertTrue(attrs.bold)
        self.assertEqual(attrs.color, 'ansired')
        await self.key('\x1b')
        self.assertNotIn(tr('未找到'), self.status())
        self.assertIsNotNone(self.ui.current)
        await self.key('\x1b')
        self.assertIsNone(self.ui.current)
        self.assertFalse(self.task.done())

    async def test_empty_literal_and_case_sensitive_queries_follow_search_rules(self):
        await self.launch()
        self.ui.app.ttimeoutlen = .01
        await self.key('1')
        await self.key('\x06')
        self.assertNotIn(tr('未找到'), self.status())
        await self.key('needle')
        self.assertNotIn(tr('未找到'), self.status())
        self.ui.search.search_buffer.text = 'NEEDLE'
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        self.assertIn(tr('未找到'), self.status())
        self.ui.body.control.search_state.ignore_case = Condition(lambda: True)
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        self.assertNotIn(tr('未找到'), self.status())
        self.ui.search.search_buffer.text = '.'
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        self.assertIn(tr('未找到'), self.status())
        await self.key('\r')
        self.assertFalse(self.ui.app.layout.is_searching)
        self.assertNotIn(tr('未找到'), self.status())

    async def test_web_search_and_loading_state(self):
        with patch('herdr_quickview.quickview.load_language', return_value='en'):
            await self.launch(text='https://example.com/', columns=60)
        with patch('herdr_quickview.quickview.read_url', return_value=('Web readable content', False)):
            await self.key('1')
            await self.key('\x06absent')
        self.assertIn('Not found', self.status())
        self.ui.busy = True
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        self.assertNotIn('Not found', self.status())
        self.ui.busy = False
        self.ui.search.search_buffer.text = 'readable'
        self.ui.app.invalidate()
        await asyncio.sleep(.1)
        self.assertNotIn('Not found', self.status())
