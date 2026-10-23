from __future__ import annotations


_language = 'en'

ENGLISH = {
    '命令退出：{code}': 'Command exited: {code}',
    'Herdr 返回了无法解析的内容': 'Herdr returned an invalid response',
    '缺少 Herdr socket；请从 Herdr 插件入口打开': 'Herdr socket is missing; open Peek from the Herdr plugin menu',
    'Herdr 响应超时，结果未确认；请检查后再操作。': 'Herdr timed out; the result is unconfirmed. Check before trying again.',
    'Herdr 接口失败：{error}': 'Herdr API failed: {error}',
    '插件上下文无效，请关闭后重新打开': 'Invalid plugin context; close Peek and open it again',
    '缺少来源窗格，无法确定项目。请从目标窗格打开文件速览': 'Source pane is missing; open Peek from the target terminal pane',
    '来源窗格的工作目录不可访问': 'The source pane working directory is not accessible',
    '找不到剪贴板工具，需要 wl-copy 或 xclip': 'No clipboard tool found; install wl-copy or xclip',
    'SSH 环境无法唤起你本机的默认应用，请复制地址后在本机打开': 'Cannot open your local default app over SSH; copy the address and open it locally',
    '不是有效的 HTTP(S) 链接': 'Not a valid HTTP(S) URL',
    '文件已不存在，无法用默认应用打开': 'The file no longer exists; cannot open it in the default app',
    '此环境找不到默认应用打开工具 xdg-open': 'Default app launcher xdg-open was not found',
    '只支持本机文件路径': 'Only local file paths are supported',
    '请选择单个文件或目录路径': 'Select a single file or directory path',
    '无法解析用户目录': 'Cannot resolve the home directory',
    '无法解析文件路径：{error}': 'Cannot resolve file path: {error}',
    '路径不存在：{path}': 'Path does not exist: {path}',
    '请选择文件': 'Select a file',
    '这是二进制文件；当前 Herdr 窗内预览支持 UTF-8 文本': 'This is a binary file; Herdr preview currently supports UTF-8 text',
    '文件不是有效的 UTF-8 文本': 'The file is not valid UTF-8 text',
    '仅支持不含账号密码的 HTTP(S) 链接': 'Only HTTP(S) URLs without embedded credentials are supported',
    '重定向目标不是 HTTP(S) 地址': 'Redirect target is not an HTTP(S) URL',
    '未提取到可读正文；页面可能需要 JavaScript 或登录。': 'No readable content found; this page may require JavaScript or a login.',
    '\n\n链接\n': '\n\nLinks\n',
    'Herdr 网页预览暂不支持 {kind}（图片、PDF 等请在 macOS 使用系统速览）': 'Herdr web preview does not support {kind} (use system Quick Look on macOS for images, PDFs, etc.)',
    '服务端返回压缩内容，Herdr 当前无法读取此网页': 'The server returned compressed content; Herdr cannot currently read this page',
    '网页读取超时': 'Web page loading timed out',
    '服务端返回了二进制内容，无法显示正文': 'The server returned binary content; cannot display it as text',
    '最终地址：{url}\n\n': 'Final URL: {url}\n\n',
    '网页请求失败：HTTP {code}': 'Web request failed: HTTP {code}',
    '网页读取失败：{error}': 'Failed to read web page: {error}',
    '无法读取 Peek 设置：{error}': 'Cannot read Peek settings: {error}',
    'preview_mode 只能是 "macos" 或 "herdr"': 'preview_mode must be "macos" or "herdr"',
    '无效的预览方式': 'Invalid preview mode',
    '系统 Quick Look 仅支持本机 macOS；此环境使用 Herdr 预览': 'System Quick Look requires local macOS; use Herdr preview in this environment',
    '此 {key} 写法无法自动修改，请使用普通引号；原设置未改动': 'Cannot update this {key} syntax automatically; use ordinary quotes. Settings were not changed.',
    '无法保存设置：{error}': 'Cannot save settings: {error}',
    'language 只能是 "en" 或 "zh"': 'language must be "en" or "zh"',
    '系统速览不可用': 'System Quick Look is unavailable',
    '已取消速览': 'Preview cancelled',
    '系统速览已提前退出': 'System Quick Look exited early',
    '系统速览未确认窗口焦点': 'System Quick Look window focus was not confirmed',
    '系统速览启动超时': 'System Quick Look startup timed out',
    '系统速览打开失败：{error}': 'Failed to open system Quick Look: {error}',
    '系统预览未获得焦点': 'System preview did not gain focus',
    '无效的 HTTP(S) 链接': 'Invalid HTTP(S) URL',
    '请从 Herdr 打开 Herdr Peek': 'Open Herdr Peek from Herdr',
    '活动窗格已变化，请从目标 CLI 重新打开速览': 'The active pane changed; reopen Peek from the target terminal',
    '当前不是本机 macOS 环境': 'This is not a local macOS environment',
    '未找到系统速览组件': 'System Quick Look helper was not found',
    '，本次使用 Herdr 浮窗；已保存偏好不变。': '; using the Herdr popup this time. Your saved preference is unchanged.',
    '默认应用打开': 'Open in app',
    '切换预览': 'Preview mode',
    '复制地址': 'Copy address',
    '关闭': 'Close',
    '窗口大小已变化，请 Esc 退出后重新打开 Herdr Peek。': 'Window size changed. Press Esc and reopen Herdr Peek.',
    '正在打开预览… Esc 可取消': 'Opening preview… Esc to cancel',
    '当前画面未找到文件或 HTTP(S) 链接。Esc 返回，滚到可见区域后重试。': 'No files or HTTP(S) links found. Press Esc, scroll the target into view, then retry.',
    'Herdr Peek · 点击 / 输入编号直达 · Tab / Enter · Esc 取消 · {count} 处': 'Herdr Peek · Click / type a label · Tab / Enter · Esc to cancel · {count} targets',
    ' · 已输入 {keys}': ' · Typed {keys}',
    'macOS 系统': 'macOS',
    'Herdr 浮窗': 'Herdr popup',
    ' [{mode} · Ctrl+P] ': ' [{mode} · Ctrl+P] ',
    '系统 Quick Look 仅支持本机 macOS；当前使用 Herdr 浮窗。': 'System Quick Look requires local macOS; currently using the Herdr popup.',
    '未找到 macOS 速览组件；请重新运行 bootstrap.sh，当前使用 Herdr 浮窗。': 'macOS Quick Look helper is missing; run bootstrap.sh again. Currently using the Herdr popup.',
    '已保存：': 'Saved: ',
    'macOS 系统速览': 'macOS Quick Look',
    '；下次选择文件或链接时生效。': '. Applies to the next file or link you select.',
    '{error}，已改用 Herdr 预览。 ': '{error}; switched to Herdr preview. ',
    '正在读取网页… Esc 可取消': 'Loading web page… Esc to cancel',
    '仅显示前 2 MiB。 ': 'Showing only the first 2 MiB. ',
    '网页正文预览；需 JavaScript 或登录的页面可能不完整。Ctrl+F 搜索 · Esc 关闭': 'Web text preview; pages requiring JavaScript or login may be incomplete. Ctrl+F to search · Esc to close',
    'Ctrl+F 搜索 · Esc / q 关闭': 'Ctrl+F to search · Esc / q to close',
    '此网页暂时无法在 Herdr 内预览。\n\n{reason}': 'This web page cannot currently be previewed in Herdr.\n\n{reason}',
    '此文件暂时无法在 Herdr 内预览。\n\n{reason}': 'This file cannot currently be previewed in Herdr.\n\n{reason}',
    '可用默认应用打开（Ctrl+O） · 复制地址 · Esc 关闭': 'Open in app (Ctrl+O) · Copy address · Esc to close',
    '已提交系统默认应用打开请求。Esc 可关闭预览。': 'Open request sent to the system default app. Esc to close this preview.',
    '默认应用打开请求未确认：{error}': 'Default app open request unconfirmed: {error}',
    '已复制完整链接。': 'Full URL copied.',
    '已复制完整路径（不含终端显示折行）。': 'Full path copied (without terminal line wraps).',
    '折行未能可靠还原；仅标记当前能确认的路径。': 'Could not reliably restore line wraps; only confirmed paths are marked.',
    '活动窗格已变化，请从目标 CLI 重新打开': 'The active pane changed; reopen from the target terminal',
    '无法确认原画面的布局：{error}': 'Cannot determine the original layout: {error}',
    '来源窗格已不在当前画面': 'The source pane is no longer visible',
    '画面与路径位置不一致，请重新打开 Herdr Peek': 'Screen and path positions do not match; reopen Herdr Peek',
    '用法：python3 run.py peek|peek-ui': 'Usage: python3 run.py peek|peek-ui',
    '搜索：': 'Search: ',
    '反向搜索：': 'Search backward: ',
    '界面语言已保存。': 'Interface language saved.',
}


def get_language():
    return _language


def set_language(language):
    global _language
    if language not in ('en', 'zh'):
        raise ValueError('language must be "en" or "zh"')
    _language = language


def tr(message, **values):
    template = message if _language == 'zh' else ENGLISH[message]
    return template.format(**values) if values else template
