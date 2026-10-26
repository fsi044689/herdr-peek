#!/bin/sh
set -eu
cd "$(dirname "$0")"
python=${PYTHON:-}
if [ -z "$python" ]; then
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            python=$candidate
            break
        fi
    done
fi
if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo 'Herdr QuickView requires Python 3.11+. Install it, then retry; or run PYTHON=python3.11 sh bootstrap.sh.' >&2
    exit 1
fi
if [ -d .venv ] && { [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; }; then
    "$python" -m venv --clear .venv
else
    "$python" -m venv .venv
fi
.venv/bin/python -m pip install .
sh native/build.sh
