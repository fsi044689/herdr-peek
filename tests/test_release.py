import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.data_structures import Size

from herdr_peek.i18n import set_language
from herdr_peek.settings import config_path
import test_i18n as language_tests


ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_old_python_is_rejected_before_creating_virtualenv(self):
        python = self.root/'old python'
        python.write_text('#!/bin/sh\nexit 1\n')
        python.chmod(0o755)
        shutil.copy2(python, self.root/'python3')
        shutil.copy2(ROOT/'bootstrap.sh', self.root/'bootstrap.sh')
        result = subprocess.run(['sh', str(self.root/'bootstrap.sh')], cwd=self.root,
                                env={**os.environ, 'PYTHON': str(python),
                                     'PATH': str(self.root)+os.pathsep+os.environ['PATH']},
                                capture_output=True, text=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.11', result.stderr)
        self.assertFalse((self.root/'.venv').exists())

    def test_installer_finds_versioned_python_when_python3_is_old(self):
        python = self.root/'python3'
        python.write_text('#!/bin/sh\nexit 1\n')
        python.chmod(0o755)
        candidate = self.root/'python3.11'
        candidate.write_text('#!/bin/sh\n[ "$1" = -c ] && exit 0\necho selected-python-3.11 >&2\nexit 23\n')
        candidate.chmod(0o755)
        shutil.copy2(ROOT/'bootstrap.sh', self.root/'bootstrap.sh')
        env = {**os.environ, 'PATH': str(self.root)+':/usr/bin:/bin'}
        env.pop('PYTHON', None)
        result = subprocess.run(['sh', str(self.root/'bootstrap.sh')], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 23)
        self.assertIn('selected-python-3.11', result.stderr)

    def test_launch_without_virtualenv_explains_how_to_install(self):
        result = subprocess.run(['sh', str(ROOT/'launch.sh'), 'peek'], cwd=self.root,
                                env={**os.environ, 'HERDR_PLUGIN_ROOT': str(self.root)},
                                capture_output=True, text=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('bootstrap.sh', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_bad_language_or_config_does_not_abort_before_popup_can_show_error(self):
        spec = importlib.util.spec_from_file_location('peek_entry', ROOT/'run.py')
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        for content in ('language = "fr"\n', '[broken'):
            (self.root/'config.toml').write_text(content)
            with patch.dict(os.environ, {'HERDR_PLUGIN_CONFIG_DIR': str(self.root)}), \
                    patch.object(sys, 'argv', ['run.py', 'peek']), \
                    patch('herdr_peek.peek.launch_peek') as launch:
                entry.main()
                launch.assert_called_once_with()
            self.assertEqual((self.root/'config.toml').read_text(), content)


class ReleaseUITests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = language_tests.LanguageUITests.asyncTearDown
    launch = language_tests.LanguageUITests.launch

    async def asyncSetUp(self):
        await language_tests.LanguageUITests.asyncSetUp(self)
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text('preview_mode = "herdr"\n')

    def rendered_footer(self):
        screen = self.ui.app.renderer._last_screen
        return ''.join(screen.data_buffer[self.ui.footer_row()][x].char for x in range(80))

    async def test_save_error_is_visible_at_eighty_columns(self):
        await self.launch(columns=80)
        self.ui.status = 'Permission denied: read-only'
        self.ui.app.invalidate()
        await asyncio.sleep(.15)
        self.assertIn('Permission denied', self.rendered_footer())

    async def test_selected_url_is_visible_before_help_at_eighty_columns(self):
        url = 'https://example.com/a#part'
        await self.launch(text=url, columns=80)
        self.pipe.send_text('\t')
        await asyncio.sleep(.15)
        self.assertIn(url, self.rendered_footer())

    async def test_bad_config_error_is_visible_and_file_is_preserved(self):
        config_path().write_text('[broken')
        set_language('en')
        await self.launch(columns=80)
        self.assertIn('Cannot read Peek settings', self.rendered_footer())
        self.assertEqual(config_path().read_text(), '[broken')

    async def test_resize_warning_takes_priority_over_selected_address(self):
        await self.launch(text='https://example.com/a/long/selected/path#part', columns=80)
        self.pipe.send_text('\t')
        await asyncio.sleep(.1)
        self.ui.status = 'Saved: previous preview mode'
        self.output.size = Size(rows=24, columns=79)
        self.ui.app.invalidate()
        await asyncio.sleep(.15)
        self.assertIn('Window size changed', self.rendered_footer())
