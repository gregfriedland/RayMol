#!/bin/bash
# fetch_macos_python.sh — download a relocatable CPython 3.13 (python-build-standalone)
# for embedding in the self-contained macOS PyMOLViewer.app. Extracts to
# deps_macos/python-standalone/python (gitignored). Re-run to refresh.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO/deps_macos/python-standalone"
# python-build-standalone, arm64 macOS, install_only (relocatable; libpython +
# stdlib + lib-dynload; install_name @rpath/libpython3.13.dylib; stdlib at
# python/lib/python3.13 — matches the shared bridge's config.home=<res>/python).
URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260602/cpython-3.13.13+20260602-aarch64-apple-darwin-install_only.tar.gz"

mkdir -p "$DEST"
cd "$DEST"
echo "Downloading $URL"
curl -fL -o py.tar.gz "$URL"
tar -xzf py.tar.gz            # -> ./python/{bin,include,lib,...}
rm -f py.tar.gz

test -f python/lib/libpython3.13.dylib || { echo "ERROR: libpython3.13.dylib missing"; exit 1; }
test -d python/lib/python3.13          || { echo "ERROR: stdlib dir missing"; exit 1; }
"$DEST/python/bin/python3.13" -m pip install --no-deps --only-binary=:all: \
    --require-hashes -r "$REPO/scripts/requirements-macos-viewer.txt"
echo "OK: $DEST/python"
lipo -archs python/lib/libpython3.13.dylib
