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
python -m pip install --quiet pyinstaller "pywebview>=5.0" certifi

echo "== vendor: DepotDownloader =="
ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
  DD_ASSET="DepotDownloader-macos-arm64.zip"
else
  DD_ASSET="DepotDownloader-macos-x64.zip"
fi
rm -rf vendor/dd && mkdir -p vendor/dd
curl -fsSL --retry 3 -o /tmp/dd.zip \
  "https://github.com/SteamRE/DepotDownloader/releases/latest/download/$DD_ASSET"
unzip -q -o /tmp/dd.zip -d vendor/dd
find vendor/dd -type f -name 'DepotDownloader*' -exec chmod +x {} \;
test -x vendor/dd/DepotDownloader || { echo "vendor: DepotDownloader missing" >&2; exit 1; }

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
  --add-data "vendor/dd:vendor/dd" \
  --collect-data certifi \
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

if [ ! -x "$APP/Contents/Resources/vendor/dd/DepotDownloader" ]; then
  echo "build failed: bundled DepotDownloader missing" >&2
  exit 1
fi

echo "== selftest =="
"$APP/Contents/MacOS/$BUNDLE" --selftest

echo "== ad-hoc sign =="
codesign --force --deep --sign - "$APP" || echo "codesign skipped"

echo "== prune build dirs =="
rm -rf build .build-venv "$HOME/.cache/pip"
df -h / | tail -1

echo "== dmg =="
rm -rf dmg-stage && mkdir dmg-stage
cp -R "$APP" dmg-stage/
ln -s /Applications dmg-stage/Applications
mkdir -p dist
if ! hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder dmg-stage \
  -ov -format UDZO \
  "dist/LuaToolsMac.dmg"; then
  echo "srcfolder image failed - retrying with a fixed-size image"
  TMPIMG="/tmp/luatools-tmp.dmg"
  MNT="$(mktemp -d /tmp/luatools-mnt.XXXXXX)"
  rm -f "$TMPIMG"
  hdiutil create -size 3g -fs HFS+ -volname "$APP_NAME" -ov "$TMPIMG"
  hdiutil attach "$TMPIMG" -mountpoint "$MNT" -nobrowse
  cp -R dmg-stage/. "$MNT"/
  hdiutil detach "$MNT" -quiet
  rm -rf "$MNT"
  hdiutil convert "$TMPIMG" -format UDZO -o "dist/LuaToolsMac.dmg"
  rm -f "$TMPIMG"
fi
rm -rf dmg-stage

echo
echo "done:"
ls -lh dist/LuaToolsMac.dmg
echo
echo "first launch: right-click the app -> Open, or run:"
echo "  xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
