#!/usr/bin/env bash
# Build "LuaTools Mac.app" and "LuaToolsMac.dmg" on macOS.
# usage: bash build_macos.sh
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="LuaTools Mac"
BUNDLE="LuaToolsMac"
PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found — install with: brew install python@3.12" >&2
  exit 1
fi

echo "== venv =="
"$PY" -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pyinstaller "pywebview>=5.0"

echo "== clean =="
rm -rf build dist "$BUNDLE.spec"

echo "== pyinstaller =="
pyinstaller \
  --noconfirm --clean --windowed \
  --name "$BUNDLE" \
  --icon icon.icns \
  --add-data "luatools_mac.py:." \
  --add-data "luatools_mac_gui.py:." \
  --add-data "ui:ui" \
  --hidden-import webview.platforms.cocoa \
  --collect-submodules webview \
  --osx-bundle-identifier "com.luatools.mac" \
  luatools_mac_gui.py

APP="dist/$BUNDLE.app"
if [ ! -d "$APP" ]; then
  echo "build failed: $APP missing" >&2
  exit 1
fi

if [ ! -f "$APP/Contents/Resources/ui/index.html" ]; then
  echo "build failed: ui/index.html missing from the bundle" >&2
  exit 1
fi

echo "== selftest =="
"$APP/Contents/MacOS/$BUNDLE" --selftest

echo "== ad-hoc sign =="
codesign --force --deep --sign - "$APP" || echo "codesign skipped"

echo "== dmg =="
rm -rf dmg-stage && mkdir dmg-stage
cp -R "$APP" dmg-stage/
ln -s /Applications dmg-stage/Applications
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder dmg-stage \
  -ov -format UDZO \
  "dist/LuaToolsMac.dmg"
rm -rf dmg-stage

echo
echo "done:"
ls -lh dist/LuaToolsMac.dmg
echo
echo "first launch: right-click the app -> Open, or run:"
echo "  xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
