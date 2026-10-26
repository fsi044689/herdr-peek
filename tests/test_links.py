import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from herdr_quickview.screen import Screen
from test_quickview import snapshot


def setUpModule():
    language = patch("herdr_quickview.i18n._language", "zh")
    language.start()
    unittest.addModuleCleanup(language.stop)


class LinkRecognitionTests(unittest.TestCase):
    def test_screen_recognizes_link_without_file_existence_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            screen = Screen(snapshot('请预览 https://example.com/page?q=one#part'), Path(d))
            self.assertEqual(len(screen.hits), 1)
            self.assertEqual(screen.hits[0].url, 'https://example.com/page?q=one#part')

    def test_url_matrix_preserves_query_encoding_unicode_and_punctuation(self):
        from herdr_quickview.links import url_spans
        urls = ['https://example.com/a?q=x&v=%E4%B8%AD#L2', 'HTTPS://example.com/A',
                'http://localhost:3000/a', 'http://[::1]:8080/a',
                'https://example.com/中文?q=你好', 'https://en.wikipedia.org/wiki/Foo_(bar)',
                'https://example.com/a,b;c?x=/some/path', 'https://example.com/a%20b']
        for url in urls:
            for left, right in (('', ''), ('<', '>'), ('`', '`'), ('"', '"'), ("'", "'"), ('[网页](', ')'), ('[网页](<', '> "Title")')):
                with self.subTest(url=url, wrapper=left):
                    text = f'见 {left}{url}{right}，结束'
                    found = list(url_spans(text))
                    self.assertEqual([r.url for r, _, _ in found], [url])
                    self.assertEqual([text[a:b] for _, a, b in found], [url])
        for url in ('https://example.com/?q=x,', 'https://example.com/a.'):
            self.assertEqual(next(url_spans(f'<{url}>'))[0].url, url)
        self.assertEqual(next(url_spans('https://example.com/a).'))[0].url, 'https://example.com/a')

    def test_invalid_schemes_credentials_and_hosts_are_not_targets(self):
        from herdr_quickview.links import url_spans
        for value in ('ftp://example.com/a', 'javascript:alert(1)', 'www.example.com', 'xhttp://example.com',
                      'https:///nohost', 'http://[invalid]', 'http://host:bad', 'http://host:99999',
                      'https://user:pass@example.com/path'):
            self.assertEqual(list(url_spans(value)), [], value)

    def test_unclosed_wrapper_never_joins_following_prose(self):
        from herdr_quickview.links import url_spans
        for left in ('(', '<', '`', '"', "'"):
            text = f'see {left}https://example.com/docs\n  next line here'
            self.assertEqual(next(url_spans(text))[0].url, 'https://example.com/docs')

    def test_links_mask_embedded_real_files_and_keep_screen_order(self):
        from herdr_quickview.links import URLReference
        from herdr_quickview.screen import Reference
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            file = root/'bar'
            file.write_text('preview')
            urls = [f'https://example.com/?f={file}', f'https://example.com/a,{file}',
                    'https://en.wikipedia.org/wiki/Foo_(bar)']
            text = f'{file} ' + ' '.join(urls) + f' {file} {urls[0]}'
            screen = Screen(snapshot(text, columns=1000), root)
            self.assertEqual(screen.hits, [Reference(file), *[URLReference(u) for u in urls], Reference(file), URLReference(urls[0])])

    def test_every_soft_wrap_position_keeps_full_url_and_hit_coordinates(self):
        from herdr_quickview.links import URLReference
        url = 'https://example.com/中文?q=%E4%B8%AD&name=a#section'
        logical = '打开 <'+url+'> 结束'
        for cut in range(1, len(logical)):
            rendered = logical[:cut]+'\n'+logical[cut:]
            screen = Screen(snapshot(rendered, logical, columns=200), Path('/tmp'))
            self.assertEqual(screen.hits, [URLReference(url)], cut)
            self.assertTrue(any(c and c[2] == 0 for row in screen.cells(200, 24) for c in row))

    def test_explicit_indented_hard_wrap_and_bare_real_newline(self):
        from herdr_quickview.links import url_spans
        for left, right in (('<', '>'), ('[link](', ')'), ('`', '`')):
            text = left+'https://example.com/very\n  long?q=test'+right
            found = list(url_spans(text))
            self.assertEqual(found[0][0].url, 'https://example.com/verylong?q=test')
            self.assertIn('\n  ', text[found[0][1]:found[0][2]])
        self.assertEqual(next(url_spans('https://example.com/very\n  long'))[0].url, 'https://example.com/very')


import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from herdr_quickview.core import MAX_TEXT, QuickViewError
from herdr_quickview.links import read_url, request_url, WebRedirect
from urllib.request import Request


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/html')
            self.end_headers()
            return
        if self.path == '/bad-redirect':
            self.send_response(302)
            self.send_header('Location', 'file:///etc/hosts')
            self.end_headers()
            return
        if self.path == '/404':
            self.send_error(404)
            return
        kind = 'text/html; charset=utf-8'
        content = '<html><head><title>测试网页</title><style>hidden css</style></head><body><h1>正文标题</h1><p>你好 &amp; world</p><script>secret script</script><a href="/next">Next</a><p>\x1b[31mANSI\x1b[0m\x00</p></body></html>'.encode()
        if self.path == '/text':
            kind, content = 'text/plain; charset=utf-8', '中文文本\nsecond'.encode()
        elif self.path == '/json':
            kind, content = 'application/json', b'{"ok": true}'
        elif self.path == '/meta':
            kind, content = 'text/html', '<meta charset="gb18030"><p>中文编码</p>'.encode('gb18030')
        elif self.path == '/large':
            kind, content = 'text/plain', b'a'*(MAX_TEXT-1)+'中文'.encode()
        elif self.path in ('/image', '/pdf', '/zip'):
            kind, content = {'/image':'image/png', '/pdf':'application/pdf', '/zip':'application/zip'}[self.path], b'\x00binary'
        elif self.path == '/empty':
            content = b'<html><script>renderApp()</script></html>'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass


class WebPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), WebHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_html_title_body_links_without_raw_tags_scripts_or_controls(self):
        text, truncated = read_url(self.base+'/html')
        for expected in ('测试网页', '正文标题', '你好 & world', '[1]', self.base+'/next', 'ANSI'):
            self.assertIn(expected, text)
        for excluded in ('<html>', 'hidden css', 'secret script', '\x1b', '\x00'):
            self.assertNotIn(excluded, text)
        self.assertFalse(truncated)

    def test_text_json_meta_charset_and_redirect(self):
        for path, expected in (('/text', '中文文本'), ('/json', '"ok": true'), ('/meta', '中文编码'), ('/redirect', '最终地址：'+self.base+'/html')):
            self.assertIn(expected, read_url(self.base+path)[0])

    def test_binary_http_errors_and_unsafe_redirects_are_explicit(self):
        for path, message in (('/image', 'image/png'), ('/pdf', 'application/pdf'), ('/zip', 'application/zip'), ('/404', 'HTTP 404'), ('/bad-redirect', '重定向目标不是 HTTP')):
            with self.subTest(path=path), self.assertRaisesRegex(QuickViewError, message):
                read_url(self.base+path)
        handler = WebRedirect()
        for url in ('ftp://example.com/x', 'file:///etc/hosts', 'data:text/plain,hello'):
            with self.assertRaises(QuickViewError):
                handler.redirect_request(Request(self.base), None, 302, '', {}, url)

    def test_multibyte_limit_does_not_emit_broken_character(self):
        text, truncated = read_url(self.base+'/large')
        self.assertTrue(truncated)
        self.assertEqual(text, 'a'*(MAX_TEXT-1))

    def test_empty_script_page_explains_limit(self):
        self.assertIn('JavaScript 或登录', read_url(self.base+'/empty')[0])

    def test_request_unicode_ipv6_and_escaped_query(self):
        self.assertEqual(request_url('https://例子.测试/中文?q=%2F你好#part'), 'https://xn--fsqu00a.xn--0zwm56d/%E4%B8%AD%E6%96%87?q=%2F%E4%BD%A0%E5%A5%BD')
        self.assertEqual(request_url('http://[::1]:8080/a'), 'http://[::1]:8080/a')

    def test_scanning_does_not_request_network(self):
        with patch('herdr_quickview.links.build_opener') as opener:
            self.assertEqual(len(Screen(snapshot(self.base+'/html'), Path('/tmp')).hits), 1)
            opener.assert_not_called()
