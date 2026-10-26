#!/bin/sh
set -eu
[ "$(uname -s)" = Darwin ] || exit 0
cd "$(dirname "$0")/.."
if ! xcrun --find clang >/dev/null 2>&1; then
    echo "Herdr QuickView: macOS build tools not found; this installation only supports Herdr preview." >&2
    exit 0
fi
app=".native/Herdr Peek.app/Contents"
mkdir -p "$app/MacOS"
xcrun clang -fobjc-arc -framework Cocoa -framework Quartz native/quicklook.m -o "$app/MacOS/herdr-peek-preview.tmp"
mv "$app/MacOS/herdr-peek-preview.tmp" "$app/MacOS/herdr-peek-preview"
cp native/Info.plist "$app/Info.plist"
/usr/bin/codesign --force --sign - --identifier dev.sylar.herdr-peek.preview "$app/.."
