import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_quickview.core import QuickViewError
from herdr_quickview.settings import config_path, load_mode, save_mode


class ModeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'HERDR_PLUGIN_CONFIG_DIR': self.temp.name}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_platform_defaults_and_saved_modes(self):
        for platform in ('darwin', 'linux', 'win32'):
            with patch('herdr_quickview.settings.sys.platform', platform):
                self.assertEqual(load_mode(), 'macos' if platform == 'darwin' else 'herdr')
        with patch('herdr_quickview.settings.sys.platform', 'darwin'):
            for mode in ('herdr', 'macos'):
                save_mode(mode)
                self.assertEqual(load_mode(), mode)
                self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)

    def test_preserves_other_values_tables_and_comments(self):
        with patch('herdr_quickview.settings.sys.platform', 'darwin'):
            for text in ('# comment\nother = 1\n[table]\npreview_mode = "local"\n',
                         '[table]\npreview_mode = "local"\n',
                         'preview_mode = "macos"\n# comment\n[table]\nother = 1\n'):
                config_path().write_text(text)
                save_mode('herdr')
                self.assertEqual(load_mode(), 'herdr')
                self.assertIn(text[text.index('[table]'):], config_path().read_text())

    def test_symlink_and_inline_comment_are_preserved(self):
        target = Path(self.temp.name)/'dotfiles.toml'
        target.write_text('preview_mode = "macos" # keep this comment\n')
        config_path().symlink_to(target)
        save_mode('herdr')
        self.assertTrue(config_path().is_symlink())
        self.assertEqual(target.read_text(), 'preview_mode = "herdr" # keep this comment\n')

    def test_unusual_valid_toml_never_reports_a_save_that_did_not_happen(self):
        for quote in ('"'*3, "'"*3):
            text = 'preview_mode = '+quote+'herdr'+quote+'\n'
            config_path().write_text(text)
            with patch('herdr_quickview.settings.sys.platform', 'darwin'):
                try:
                    save_mode('macos')
                except QuickViewError:
                    self.assertEqual(config_path().read_text(), text)
                else:
                    self.assertEqual(load_mode(), 'macos')

    def test_bad_config_is_reported_and_not_overwritten(self):
        for text in ('preview_mode = "invalid"', 'invalid ['):
            config_path().write_text(text)
            with self.assertRaises(QuickViewError):
                load_mode()
            with self.assertRaises(QuickViewError):
                save_mode('herdr')
            self.assertEqual(config_path().read_text(), text)

    def test_non_mac_and_ssh_cannot_enable_system_mode(self):
        for platform, ssh in (('linux', ''), ('win32', ''), ('darwin', 'remote')):
            with patch('herdr_quickview.settings.sys.platform', platform), patch.dict(os.environ, {'SSH_CONNECTION': ssh}):
                with self.assertRaises(QuickViewError):
                    save_mode('macos')
                self.assertFalse(config_path().exists())
