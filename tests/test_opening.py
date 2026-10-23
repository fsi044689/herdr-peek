import os
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_peek.core import PeekError, execute, open_default


def setUpModule():
    language = patch("herdr_peek.i18n._language", "zh")
    language.start()
    unittest.addModuleCleanup(language.stop)


class DefaultApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name).resolve()/'中文 空格 ; $name.txt'
        self.file.write_text('preview')
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_detached_waits_for_opener_not_its_child_output_pipes(self):
        script = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time;time.sleep(.8)"])'
        started = time.monotonic()
        self.assertEqual(execute([sys.executable, '-c', script], timeout=.25, detached=True), '')
        self.assertLess(time.monotonic()-started, .5)

    def test_mac_file_uses_absolute_argv_with_isolated_stdin(self):
        with patch('herdr_peek.core.sys.platform', 'darwin'), patch('herdr_peek.core.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            open_default(self.file)
        self.assertEqual(run.call_args.args[0], ['open', str(self.file)])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertTrue(run.call_args.kwargs['start_new_session'])
        self.assertEqual(run.call_args.kwargs['timeout'], 8)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_mac_url_explicit_url_flag_preserves_original_fragment_and_encoding(self):
        url = 'https://docs.python.org/3/?q=%E4%B8%AD&name=中文#section'
        with patch('herdr_peek.core.sys.platform', 'darwin'), patch('herdr_peek.core.execute') as execute:
            open_default(url, url=True)
            execute.assert_called_once_with(['open', '-u', url], timeout=8, detached=True)

    def test_linux_uses_xdg_open_for_file_and_link(self):
        with patch('herdr_peek.core.sys.platform', 'linux'), patch('herdr_peek.core.shutil.which', return_value='/usr/bin/xdg-open'), patch('herdr_peek.core.execute') as execute:
            open_default(self.file)
            execute.assert_called_with(['/usr/bin/xdg-open', str(self.file)], timeout=8, detached=True)
            open_default('https://www.python.org/', url=True)
            execute.assert_called_with(['/usr/bin/xdg-open', 'https://www.python.org/'], timeout=8, detached=True)

    def test_missing_file_invalid_url_and_remote_environment_do_not_launch(self):
        with patch('herdr_peek.core.execute') as execute:
            with self.assertRaisesRegex(PeekError, '不存在'):
                open_default(self.file.parent/'missing.pdf')
            for url in ('file:///etc/hosts', 'javascript:alert(1)', 'https:///missing'):
                with self.assertRaises(PeekError):
                    open_default(url, url=True)
            with patch.dict(os.environ, {'SSH_TTY': '/dev/pts/1'}), self.assertRaisesRegex(PeekError, 'SSH'):
                open_default(self.file)
            execute.assert_not_called()

    def test_linux_missing_opener_reports_reason(self):
        with patch('herdr_peek.core.sys.platform', 'linux'), patch('herdr_peek.core.shutil.which', return_value=None), self.assertRaisesRegex(PeekError, 'xdg-open'):
            open_default(self.file)

    def test_opener_error_and_timeout_are_not_reported_as_success(self):
        with patch('herdr_peek.core.sys.platform', 'darwin'):
            def fail(args, **kwargs):
                kwargs['stderr'].write(b'no association')
                return subprocess.CompletedProcess(args, 1)
            with patch('herdr_peek.core.subprocess.run', side_effect=fail), self.assertRaisesRegex(PeekError, 'no association'):
                open_default(self.file)
            with patch('herdr_peek.core.subprocess.run', side_effect=subprocess.TimeoutExpired(['open'], 8)), self.assertRaises(PeekError):
                open_default(self.file)
