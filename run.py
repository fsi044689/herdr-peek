#!/usr/bin/env python3
import sys

if sys.version_info < (3, 11):
    print('Herdr QuickView requires Python 3.11+. Rebuild with PYTHON=python3.11 sh bootstrap.sh.', file=sys.stderr)
    sys.exit(1)

from herdr_quickview.i18n import set_language, tr
from herdr_quickview.settings import load_language

from herdr_quickview.core import QuickViewError


def main():
    try:
        set_language(load_language())
    except QuickViewError:
        set_language('en')
    if len(sys.argv) == 2 and sys.argv[1] == "quickview":
        from herdr_quickview.quickview import launch_quickview
        launch_quickview()
    elif len(sys.argv) == 2 and sys.argv[1] == "quickview-ui":
        from herdr_quickview.quickview import main as quickview_main
        quickview_main()
    else:
        raise QuickViewError(tr("用法：python3 run.py quickview|quickview-ui"))


if __name__ == "__main__":
    try:
        main()
    except (QuickViewError, ImportError) as exc:
        print(f"Herdr QuickView: {exc}", file=sys.stderr)
        sys.exit(1)
