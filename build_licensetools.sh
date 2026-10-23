#!/usr/bin/env bash
# Build "LicenseTools.app" and "LicenseTools.dmg" on macOS.
# usage: bash build_licensetools.sh
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="LicenseTools"
BUNDLE="LicenseTools"
PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found — install with: brew install python@3.12" >&2
  exit 1
fi

echo "== venv =="
if [ ! -x .build-venv/bin/python ]; then
  "$PY" -m venv .build-venv
fi
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
curl -fsSL --retry 3 -o /tmp/dd-lic.zip \
  "https://github.com/SteamRE/DepotDownloader/releases/latest/download/$DD_ASSET"
unzip -q -o /tmp/dd-lic.zip -d vendor/dd
find vendor/dd -type f -name 'DepotDownloader*' -exec chmod +x {} \;
test -x vendor/dd/DepotDownloader || { echo "vendor: DepotDownloader missing" >&2; exit 1; }

echo "== offline selftest =="
python licensetools_mac.py --selftest

echo "== clean =="
rm -rf build-lic dist-lic "$BUNDLE.spec"

echo "== pyinstaller =="
pyinstaller \
  --noconfirm --clean --windowed \
  --name "$BUNDLE" \
  --icon icon.icns \
  --add-data "licensetools_mac.py:." \
  --add-data "vendor/dd:vendor/dd" \
  --collect-data certifi \
  --hidden-import webview.platforms.cocoa \
  --collect-submodules webview \
  --osx-bundle-identifier "com.whoismelv.licensetools" \
  --distpath dist-lic --workpath build-lic \
  licensetools_mac.py

APP="dist-lic/$BUNDLE.app"
if [ ! -d "$APP" ]; then
  echo "build failed: $APP missing" >&2
  exit 1
fi

if [ ! -x "$APP/Contents/Resources/vendor/dd/DepotDownloader" ]; then
  echo "build failed: bundled DepotDownloader missing from the bundle" >&2
  exit 1
fi

echo "== selftest (frozen) =="
"$APP/Contents/MacOS/$BUNDLE" --selftest

echo "== ad-hoc sign =="
codesign --force --deep --sign - "$APP" || echo "codesign skipped"

echo "== dmg =="
rm -rf dmg-stage-lic && mkdir dmg-stage-lic
cp -R "$APP" dmg-stage-lic/
ln -s /Applications dmg-stage-lic/Applications
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder dmg-stage-lic \
  -ov -format UDZO \
  "dist/LicenseTools.dmg"
rm -rf dmg-stage-lic

echo
echo "done:"
ls -lh dist/LicenseTools.dmg
echo
echo "first launch: right-click the app -> Open, or run:"
echo "  xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
