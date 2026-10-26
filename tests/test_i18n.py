import asyncio
import ast
import os
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_quickview.core import QuickViewError, read_file
from herdr_quickview.i18n import ENGLISH, get_language, set_language, tr
from herdr_quickview.settings import config_path, load_language, load_mode, save_language
import test_quickview as quickview_tests
import test_settings as settings_tests


class LanguageSettingsTests(unittest.TestCase):
    setUp = settings_tests.ModeSettingsTests.setUp

    def test_default_is_english_on_all_platforms_without_language_detection(self):
        for platform in ('darwin', 'linux', 'win32'):
            for locale in ('zh_CN.UTF-8', 'en_US.UTF-8', 'de_DE.UTF-8', ''):
                with patch('sys.platform', platform), patch.dict(os.environ, {'LANG': locale}), \
                        patch('subprocess.run') as run:
                    self.assertEqual(load_language(), 'en')
                    run.assert_not_called()

    def test_saved_language_keeps_mode_comments_tables_and_symlink(self):
        target = Path(self.temp.name)/'dotfiles.toml'
        target.write_text('language = "en" # keep language comment\npreview_mode = "herdr" # keep mode\n[other]\nlanguage = "untouched"\n')
        config_path().symlink_to(target)
        save_language('zh')
        self.assertEqual(load_language(), 'zh')
        self.assertEqual(load_mode(), 'herdr')
        self.assertEqual(target.read_text(), 'language = "zh" # keep language comment\npreview_mode = "herdr" # keep mode\n[other]\nlanguage = "untouched"\n')
        self.assertTrue(config_path().is_symlink())
        save_language('en')
        self.assertEqual(load_language(), 'en')

    def test_invalid_value_or_broken_config_does_not_overwrite_settings(self):
        for original in ('language = "fr"\n', 'bad [', 'preview_mode = "invalid"\n'):
            config_path().write_text(original)
            with self.assertRaises(QuickViewError):
                save_language('zh')
            self.assertEqual(config_path().read_text(), original)
        config_path().write_text('preview_mode = "herdr"\n')
        with self.assertRaises(QuickViewError):
            save_language('ZH')
        self.assertEqual(config_path().read_text(), 'preview_mode = "herdr"\n')

    def test_write_failure_keeps_original_file_and_cleans_temporary_file(self):
        original = 'preview_mode = "herdr"\n'
        config_path().write_text(original)
        with patch('herdr_quickview.settings.os.replace', side_effect=OSError('read-only')), self.assertRaises(QuickViewError):
            save_language('zh')
        self.assertEqual(config_path().read_text(), original)
        self.assertEqual(list(Path(self.temp.name).glob('.quickview-*')), [])

    def test_cli_loads_preference_before_showing_usage_errors(self):
        root = Path(__file__).resolve().parents[1]
        for language, expected in (('en', 'Usage:'), ('zh', '用法：')):
            save_language(language)
            result = subprocess.run([sys.executable, str(root/'run.py'), 'invalid'],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 1)
            self.assertIn(expected, result.stderr)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        previous = get_language()
        self.addCleanup(set_language, previous)

    def test_both_catalogues_have_matching_format_fields(self):
        formatter = string.Formatter()
        for chinese, english in ENGLISH.items():
            fields = {name for _, name, _, _ in formatter.parse(chinese) if name is not None}
            self.assertEqual(fields, {name for _, name, _, _ in formatter.parse(english) if name is not None})
            values = {name: 'sample' for name in fields}
            for language, template in (('en', english), ('zh', chinese)):
                set_language(language)
                self.assertEqual(tr(chinese, **values), template.format(**values))

    def test_code_message_templates_all_exist_in_catalogue(self):
        root = Path(__file__).resolve().parents[1]
        for path in [*root.glob('herdr_quickview/*.py'), root/'run.py']:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
                    argument = node.args[0]
                    candidates = (argument.body, argument.orelse) if isinstance(argument, ast.IfExp) else (argument,)
                    for candidate in candidates:
                        if isinstance(candidate, ast.Constant):
                            self.assertIn(candidate.value, ENGLISH, (path.name, node.lineno))

    def test_localization_does_not_translate_file_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'中文 文件.txt'
            path.write_text('关闭\nClose\n用户自己的内容')
            for language in ('en', 'zh'):
                set_language(language)
                self.assertEqual(read_file(path), ('关闭\nClose\n用户自己的内容', False))


class LanguageUITests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = quickview_tests.QuickViewUITests.asyncTearDown
    launch = quickview_tests.QuickViewUITests.launch
    click = quickview_tests.QuickViewUITests.click

    async def asyncSetUp(self):
        await quickview_tests.QuickViewUITests.asyncSetUp(self)
        previous = get_language()
        set_language('en')
        self.addCleanup(set_language, previous)

    async def test_keyboard_switch_is_immediate_persistent_and_keeps_hint_selection(self):
        await self.launch(columns=100)
        self.assertEqual(self.ui.open_button.text, 'Open in app Ctrl+O')
        self.assertEqual(self.ui.language_button.text, '中文')
        self.pipe.send_text('\x0c')
        await asyncio.sleep(.15)
        self.assertEqual(load_language(), 'zh')
        self.assertEqual(self.ui.open_button.text, '默认应用打开 Ctrl+O')
        self.assertEqual(self.ui.language_button.text, 'English')
        self.assertIn('已保存', self.ui.status)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertEqual(self.ui.current.path, self.file)
        self.assertIn('搜索', self.ui.status)
        self.assertIn('needle', self.ui.body.text)
        self.pipe.send_text('\x0c')
        await asyncio.sleep(.15)
        self.assertEqual(load_language(), 'en')
        self.assertEqual(self.ui.copy_button.text, 'Copy address')
        self.assertEqual(self.ui.body.text, self.file.read_text())

    async def test_saved_preference_is_loaded_when_reopened(self):
        await self.launch()
        self.pipe.send_text('\x0c')
        await asyncio.sleep(.15)
        self.ui.app.exit()
        await self.task
        self.pipe_context.__exit__(None, None, None)
        set_language('en')
        await self.launch()
        self.assertEqual(get_language(), 'zh')
        self.assertEqual(self.ui.back_button.text, '返回')

    async def test_footer_mouse_switches_language(self):
        await self.launch(columns=120)
        row = self.ui.footer_row()
        rendered = self.ui.app.renderer._last_screen
        x = next(x for x in range(120) if rendered.data_buffer[row][x].char == '中')
        await self.click(x, row)
        self.assertEqual(load_language(), 'zh')
        self.assertEqual(self.ui.back_button.text, '返回')

    async def test_preview_button_and_search_prompt_switch_language(self):
        await self.launch(columns=100)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        rendered = self.ui.app.renderer._last_screen
        x = next(x for x in range(100) if rendered.data_buffer[0][x].char == '中')
        await self.click(x, 0)
        self.assertEqual(load_language(), 'zh')
        self.pipe.send_text('\x06')
        await asyncio.sleep(.15)
        screen = self.ui.app.renderer._last_screen
        text = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(100)) for y in range(24))
        self.assertIn('搜索：', text)

    async def test_failed_save_or_busy_does_not_change_language(self):
        await self.launch()
        with patch('herdr_quickview.quickview.save_language', side_effect=QuickViewError('read-only')):
            self.pipe.send_text('\x0c')
            await asyncio.sleep(.15)
        self.assertEqual(get_language(), 'en')
        self.assertEqual(self.ui.back_button.text, 'Back')
        self.assertIn('read-only', self.ui.status)
        with patch('herdr_quickview.quickview.save_language') as save:
            self.ui.busy = True
            self.pipe.send_text('\x0c')
            await asyncio.sleep(.15)
            save.assert_not_called()
        self.assertEqual(get_language(), 'en')

    async def test_default_english_binary_error_and_default_app_hint(self):
        file = self.root/'sample.pdf'
        file.write_bytes(b'%PDF-1.4\nfixture')
        await self.launch(text=str(file), columns=100)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        self.assertIn('This file cannot currently be previewed', self.ui.body.text)
        self.assertIn('binary file', self.ui.body.text)
        self.assertIn('Ctrl+O', self.ui.status)
        self.assertNotIn('文件', self.ui.body.text)

    async def test_header_labels_fit_in_eighty_columns_in_both_languages(self):
        await self.launch(columns=80)
        self.pipe.send_text('1')
        await asyncio.sleep(.15)
        for labels in (('Open in app Ctrl+O', 'Preview mode', 'Copy address', '中文', 'Back'),
                       ('默认应用打开 Ctrl+O', '切换预览', '复制地址', 'English', '返回')):
            screen = self.ui.app.renderer._last_screen
            header = ''.join(screen.data_buffer[0][x].char for x in range(80))
            for label in labels:
                self.assertIn(label, header)
            self.pipe.send_text('\x0c')
            await asyncio.sleep(.15)
