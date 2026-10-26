#!/bin/sh
set -eu
root=${HERDR_PLUGIN_ROOT:?Launch from the Herdr plugin menu}
python="$root/.venv/bin/python"
if [ ! -x "$python" ]; then
    echo 'Herdr QuickView is not built. Run sh bootstrap.sh in the plugin directory, then try again.' >&2
    exit 1
fi
exec "$python" "$root/run.py" "$@"
