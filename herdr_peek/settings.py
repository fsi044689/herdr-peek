from __future__ import annotations

import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

from .core import PeekError
from .i18n import tr


def local_macos():
    return sys.platform == 'darwin' and not os.environ.get('SSH_CONNECTION') and not os.environ.get('SSH_TTY')


def config_path():
    directory = os.environ.get('HERDR_PLUGIN_CONFIG_DIR')
    if directory:
        return Path(directory) / 'config.toml'
    herdr_config = os.environ.get('HERDR_CONFIG_PATH')
    root = Path(herdr_config).parent if herdr_config else Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'herdr'
    return root/'plugins/config/sylar.herdr-peek/config.toml'


def read_settings():
    try:
        data = tomllib.loads(config_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise PeekError(tr('无法读取 QuickView 设置：{error}', error=exc)) from exc
    return data


def load_mode():
    default = 'macos' if local_macos() else 'herdr'
    mode = read_settings().get('preview_mode', default)
    if mode not in ('macos', 'herdr'):
        raise PeekError(tr('preview_mode 只能是 "macos" 或 "herdr"'))
    return mode


def load_language():
    language = read_settings().get('language', 'en')
    if language not in ('en', 'zh'):
        raise PeekError(tr('language 只能是 "en" 或 "zh"'))
    return language


def save_mode(mode):
    if mode not in ('macos', 'herdr'):
        raise PeekError(tr('无效的预览方式'))
    if mode == 'macos' and not local_macos():
        raise PeekError(tr('系统 Quick Look 仅支持本机 macOS；此环境使用 Herdr 预览'))
    save_setting('preview_mode', mode, 'macos|herdr')


def save_language(language):
    if language not in ('en', 'zh'):
        raise PeekError(tr('language 只能是 "en" 或 "zh"'))
    save_setting('language', language, 'en|zh')


def save_setting(key, value, choices):
    path = config_path().resolve()
    try:
        load_mode()
        load_language()
        text = path.read_text() if path.exists() else ''
        tomllib.loads(text)
        # 仅修改目标顶层字段，保留其他内容和注释。
        table = re.search(r'^[ \t]*\[', text, re.M)
        boundary = table.start() if table else len(text)
        top, rest = text[:boundary], text[boundary:]
        line = f'{key} = "{value}"'
        if re.search(rf'^{key}\s*=', top, re.M):
            top = re.sub(rf'''^({key}\s*=\s*)(["'])(?:{choices})\2''',
                         lambda match: match[1] + '"' + value + '"', top, count=1, flags=re.M)
            text = top + rest
        else:
            text = line + '\n' + text
        if tomllib.loads(text).get(key) != value:
            raise PeekError(tr('此 {key} 写法无法自动修改，请使用普通引号；原设置未改动', key=key))
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.peek-', suffix='.toml')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        raise PeekError(tr('无法保存设置：{error}', error=exc)) from exc
