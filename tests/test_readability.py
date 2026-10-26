import asyncio
import unittest
from unittest.mock import patch

import test_quickview as quickview_tests


class ReadabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = quickview_tests.QuickViewUITests.asyncSetUp
    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch

    async def open_first(self, text, **size):
        await self.launch(text=text, **size)
        self.pipe.send_text('1')
        await asyncio.sleep(.2)
        self.assertFalse(self.ui.busy)

    def long_file(self, suffix=''):
        self.file.write_text('\n'.join(f'value_{n} = {n}{suffix}' for n in range(1, 301)))

    async def test_code_colors_follow_terminal_palette_without_filling_background(self):
        self.file.write_text('def hello(name):\n    return "ready"\n')
        await self.open_first(str(self.file), columns=100)
        styles = self.ui.app.renderer._attrs_for_style
        screen = self.ui.app.renderer._last_screen
        function_colors, string_colors = set(), set()
        for row in screen.data_buffer.values():
            for cell in row.values():
                if 'class:pygments.name.function' in cell.style:
                    function_colors.add(styles[cell.style].color)
                    self.assertIn(styles[cell.style].bgcolor, ('', 'default'))
                if 'class:pygments.literal.string' in cell.style:
                    string_colors.add(styles[cell.style].color)
        self.assertEqual(function_colors, {'ansiblue'})
        self.assertEqual(string_colors, {'ansigreen'})

    async def test_markdown_headings_keep_emphasis(self):
        self.file = self.root/'notes.md'
        self.file.write_text('# Heading\n\n## Subheading\n\n**bold** and *italic*\n')
        await self.open_first(str(self.file), columns=100)
        attrs = self.ui.app.renderer._attrs_for_style
        cells = [cell for row in self.ui.app.renderer._last_screen.data_buffer.values()
                 for cell in row.values()]
        for token, attribute in (('heading', 'bold'), ('subheading', 'bold'),
                                 ('strong', 'bold'), ('emph', 'italic')):
            matches = [cell for cell in cells if f'class:pygments.generic.{token}' in cell.style]
            self.assertTrue(matches, token)
            self.assertTrue(all(getattr(attrs[cell.style], attribute) for cell in matches), token)

    async def test_referenced_line_is_centered_and_highlight_stays_when_cursor_moves(self):
        self.long_file()
        await self.open_first(f'{self.file}:150', columns=100)
        info = self.ui.body.window.render_info
        self.assertLessEqual(abs(info.cursor_position.y - info.window_height // 2), 1)
        self.assertLess(info.displayed_lines[0], 149)
        self.assertGreater(info.displayed_lines[-1], 149)
        self.assertIsNone(self.ui.pending_row)
        self.pipe.send_text('\x1b[B')
        await asyncio.sleep(.1)
        self.assertEqual(self.ui.body.document.cursor_position_row, 150)
        content = self.ui.body.window.render_info.ui_content
        self.assertTrue(all('class:quickview.target' in fragment[0] for fragment in content.get_line(149)[:-1]))
        self.assertFalse(any('class:quickview.target' in fragment[0] for fragment in content.get_line(150)))
        self.pipe.send_text('\x1b[6~')
        await asyncio.sleep(.1)
        self.assertGreater(self.ui.body.window.render_info.displayed_lines[0], 149)

    async def test_wrapped_lines_use_display_height_for_centering(self):
        self.long_file(' # ' + 'x' * 100)
        await self.open_first(f'{self.file}:150', columns=80)
        info = self.ui.body.window.render_info
        self.assertLessEqual(abs(info.cursor_position.y - info.window_height // 2), 2)
        self.assertIn(149, info.displayed_lines)
        self.assertTrue(any('class:quickview.target' in f[0] for f in info.ui_content.get_line(149)))

    async def test_file_edges_clamped_line_and_switch_without_line(self):
        self.long_file()
        other = self.root/'other.py'
        other.write_text('plain = 1\n' * 100)
        await self.open_first(f'{self.file}:1\n{self.file}:300\n{self.file}:999\n{other}', columns=100)
        self.assertEqual(self.ui.body.window.vertical_scroll, 0)
        self.assertEqual(self.ui.target_row, 0)
        for expected_row, visible_row in ((299, 299), (None, 299), (None, 0)):
            self.pipe.send_text('\x0a')
            await asyncio.sleep(.25)
            self.assertEqual(self.ui.target_row, expected_row)
            info = self.ui.body.window.render_info
            self.assertIn(visible_row, info.displayed_lines)
            if expected_row is None:
                self.assertFalse(any('class:quickview.target' in f[0] for f in info.ui_content.get_line(visible_row)))
        self.assertEqual(self.ui.body.window.vertical_scroll, 0)
        content = self.ui.body.window.render_info.ui_content
        self.assertFalse(any('class:quickview.target' in f[0] for f in content.get_line(0)))

    async def test_search_highlight_survives_on_fixed_target_line(self):
        self.long_file()
        await self.open_first(f'{self.file}:150', columns=100)
        self.pipe.send_text('\x06value_150\r')
        await asyncio.sleep(.2)
        content = self.ui.body.window.render_info.ui_content
        hits = [f for f in content.get_line(149) if 'class:search' in f[0]]
        self.assertTrue(hits)
        self.assertTrue(all('class:quickview.target' in f[0] for f in hits))
        attrs = self.ui.app.renderer._attrs_for_style
        self.assertTrue(all(attrs[f[0]].bgcolor == 'ansibrightyellow' for f in hits))
        self.assertEqual(self.ui.target_row, 149)

    async def test_blank_target_line_has_marker_and_document_stays_unchanged(self):
        text = 'before\n\nafter\n'
        self.file.write_text(text)
        await self.open_first(f'{self.file}:2', columns=100)
        self.assertEqual(self.ui.body.text, text)
        fragments = self.ui.body.window.render_info.ui_content.get_line(1)
        self.assertTrue(any('class:quickview.target' in f[0] and f[1] == ' ' for f in fragments))

    async def test_url_and_back_clear_target_highlight(self):
        self.long_file()
        await self.open_first(f'{self.file}:150\nhttps://example.com/page', columns=100)
        with patch('herdr_quickview.quickview.read_url', return_value=('web text', False)):
            self.pipe.send_text('\x0a')
            await asyncio.sleep(.25)
        self.assertIsNone(self.ui.target_row)
        self.assertEqual(self.ui.body.window.vertical_scroll, 0)
        self.ui.back()
        self.assertIsNone(self.ui.pending_row)
        self.assertIsNone(self.ui.target_row)
