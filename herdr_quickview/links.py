from __future__ import annotations

import codecs
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .i18n import tr

from .core import MAX_TEXT, QuickViewError, clean_text

WEB_TIMEOUT = 8
URL_START = re.compile(r'(?<![A-Za-z0-9_])https?://', re.I)


@dataclass(frozen=True)
class URLReference:
    url: str


def valid_url(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme.lower() in ('http', 'https') and bool(parts.hostname)
                and parts.port != 0 and parts.username is None and parts.password is None
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
                and '\\' not in url)
    except ValueError:
        return False


def url_candidates(text):
    position = 0
    while match := URL_START.search(text, position):
        start, index = match.start(), match.end()
        wrapper = text[start-1] if start else ''
        explicit = wrapper in ('<', '`', '"', "'", '(')
        closing = {'<': '>', '`': '`', '"': '"', "'": "'", '(': ')'}.get(wrapper)
        depth = {'(': 0, '[': 0}
        while index < len(text):
            char = text[index]
            if char == '\n' and explicit:
                continuation = re.match(r'\n[ \t]+(?=\S)', text[index:])
                if continuation:
                    index += continuation.end()
                    continue
            if char.isspace() or char in '<>"`，。；：！？、」』】》（）':
                break
            if char == closing and not (char == ')' and depth['(']):
                break
            if char in depth:
                depth[char] += 1
            elif char in (')', ']'):
                left = '(' if char == ')' else '['
                if not depth[left]:
                    break
                depth[left] -= 1
            index += 1
        if '\n' in text[start:index] and (index >= len(text) or text[index] != closing):
            index = text.index('\n', start, index)
        end = index
        if not explicit or index >= len(text) or text[index] != closing:
            while end > start and text[end-1] in '.,:;!?\'':
                end -= 1
        candidate = re.sub(r'\n[ \t]+', '', text[start:end])
        yield candidate, start, end
        position = max(index, match.end())


def url_spans(text):
    for url, start, end in url_candidates(text):
        if valid_url(url):
            yield URLReference(url), start, end


def request_url(url):
    if not valid_url(url):
        raise QuickViewError(tr('仅支持不含账号密码的 HTTP(S) 链接'))
    parts = urlsplit(url)
    host = parts.hostname.encode('idna').decode('ascii')
    if ':' in host:
        host = '[' + host + ']'
    if parts.port is not None:
        host += ':' + str(parts.port)
    return urlunsplit((parts.scheme, host, quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
                       quote(parts.query, safe="%/?@!$&'()*+,;=:-._~"), ''))


class WebRedirect(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        target = headers.get('location') or headers.get('uri')
        if target and urlsplit(urljoin(req.full_url, target)).scheme.lower() not in ('http', 'https'):
            fp.close()
            raise QuickViewError(tr('重定向目标不是 HTTP(S) 地址'))
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme.lower() not in ('http', 'https'):
            raise QuickViewError(tr('重定向目标不是 HTTP(S) 地址'))
        return super().redirect_request(req, fp, code, msg, headers, request_url(newurl))


class ReadableHTML(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.title = base, [], [], []
        self.skipped, self.in_title = [], False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'template'):
            self.skipped.append(tag)
        if self.skipped:
            return
        if tag == 'title':
            self.in_title = True
        if tag in ('p', 'div', 'section', 'article', 'header', 'footer', 'br', 'li', 'h1', 'h2', 'h3', 'pre', 'tr'):
            self.parts.append('\n')
        if tag == 'li':
            self.parts.append('• ')
        if tag == 'a' and attrs.get('href'):
            target = urljoin(self.base, attrs['href'])
            if valid_url(target):
                if target not in self.links:
                    self.links.append(target)
                self.anchor = self.links.index(target) + 1

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag == 'title':
            self.in_title = False
        if tag == 'a' and self.anchor:
            self.parts.append(f' [{self.anchor}]')
            self.anchor = None
        if tag in ('p', 'div', 'section', 'article', 'li', 'h1', 'h2', 'h3', 'pre', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.skipped:
            return
        if self.in_title:
            self.title.append(data)
        else:
            self.parts.append(re.sub(r'\s+', ' ', data))

    def render(self):
        title = ' '.join(''.join(self.title).split())
        body = '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())
        if not body:
            body = tr('未提取到可读正文；页面可能需要 JavaScript 或登录。')
        links = '\n'.join(f'[{i}] {url}' for i, url in enumerate(self.links, 1))
        return clean_text((title + '\n\n' if title else '') + body + (tr('\n\n链接\n') + links if links else ''))


def read_url(url):
    started = time.monotonic()
    try:
        request = Request(request_url(url), headers={'User-Agent': 'Herdr-QuickView/0.1.1', 'Accept': 'text/html, text/plain, application/json'})
        with build_opener(WebRedirect()).open(request, timeout=WEB_TIMEOUT) as response:
            kind = response.headers.get_content_type()
            if kind not in ('text/html', 'application/xhtml+xml', 'text/plain', 'application/json'):
                raise QuickViewError(tr("Herdr 网页预览暂不支持 {kind}（图片、PDF 等请在 macOS 使用系统速览）", kind=kind))
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise QuickViewError(tr('服务端返回压缩内容，Herdr 当前无法读取此网页'))
            data = bytearray()
            while len(data) <= MAX_TEXT:
                if time.monotonic() - started >= WEB_TIMEOUT:
                    raise QuickViewError(tr('网页读取超时'))
                chunk = response.read1(min(65536, MAX_TEXT + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            truncated = len(data) > MAX_TEXT
            data = bytes(data[:MAX_TEXT])
            charset = response.headers.get_content_charset()
            if not charset and kind in ('text/html', 'application/xhtml+xml'):
                meta = re.search(br'<meta\b[^>]*charset\s*=\s*[\'\"]?\s*([\w-]+)', data[:8192], re.I)
                charset = meta[1].decode('ascii') if meta else None
            try:
                decoder = codecs.getincrementaldecoder(charset or 'utf-8')(errors='replace')
            except LookupError:
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            text = decoder.decode(data, final=not truncated)
            final_url = response.geturl()
            if kind in ('text/html', 'application/xhtml+xml'):
                parser = ReadableHTML(final_url)
                parser.feed(text)
                text = parser.render()
            else:
                if '\x00' in text:
                    raise QuickViewError(tr('服务端返回了二进制内容，无法显示正文'))
                text = clean_text(text)
            if request_url(url) != final_url:
                text = tr("最终地址：{url}\n\n", url=final_url) + text
            return text, truncated
    except HTTPError as exc:
        exc.close()
        raise QuickViewError(tr("网页请求失败：HTTP {code}", code=exc.code)) from exc
    except (URLError, OSError, ValueError) as exc:
        raise QuickViewError(tr("网页读取失败：{error}", error=exc)) from exc
