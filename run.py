#!/usr/bin/env python3
import sys

if sys.version_info < (3, 11):
    print('Herdr QuickView requires Python 3.11+. Rebuild with PYTHON=python3.11 sh bootstrap.sh.', file=sys.stderr)
    sys.exit(1)

from herdr_peek.i18n import set_language, tr
from herdr_peek.settings import load_language

from herdr_peek.core import PeekError


def main():
    try:
        set_language(load_language())
    except PeekError:
        set_language('en')
    if len(sys.argv) == 2 and sys.argv[1] == "peek":
        from herdr_peek.peek import launch_peek
        launch_peek()
    elif len(sys.argv) == 2 and sys.argv[1] == "peek-ui":
        from herdr_peek.peek import main as peek_main
        peek_main()
    else:
        raise PeekError(tr("用法：python3 run.py peek|peek-ui"))


if __name__ == "__main__":
    try:
        main()
    except (PeekError, ImportError) as exc:
        print(f"Herdr QuickView: {exc}", file=sys.stderr)
        sys.exit(1)
