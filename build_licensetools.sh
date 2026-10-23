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

echo "== vendor: macSteam loader =="
rm -rf vendor/macsteam && mkdir -p vendor/macsteam
set +e
MS_JSON="$(curl -fsSL --retry 3 --max-time 60 \
  -H 'Accept: application/vnd.github+json' \
  -H 'User-Agent: licensetools' \
  https://api.github.com/repos/crghofficial/macsteam/releases/latest 2>/dev/null)"
MS_URL="$(printf '%s' "$MS_JSON" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
a = [x for x in d.get("assets", []) if x["name"].lower().endswith((".dmg", ".zip"))]
print(a[0]["browser_download_url"] if a else "")
' 2>/dev/null)"
if [ -n "$MS_URL" ]; then
  MS_TMP="/tmp/macsteam-lic.${MS_URL##*.}"
  curl -fsSL --retry 3 --max-time 300 -o "$MS_TMP" "$MS_URL" || true
  if [ -s "$MS_TMP" ]; then
    MS_MNT="$(mktemp -d /tmp/msmnt.XXXXXX)"
    case "$MS_TMP" in
      *.dmg) hdiutil attach -nobrowse -readonly -mountpoint "$MS_MNT" "$MS_TMP" >/dev/null 2>&1 || true ;;
      *.zip) unzip -q -o "$MS_TMP" -d "$MS_MNT" >/dev/null 2>&1 || true ;;
    esac
    MS_DYLIB="$(find "$MS_MNT" -name 'macsteam.dylib' -type f 2>/dev/null | head -n1)"
    MS_SIG="$(find "$MS_MNT" -type d -name signatures 2>/dev/null | head -n1)"
    if [ -n "$MS_DYLIB" ]; then
      cp "$MS_DYLIB" vendor/macsteam/macsteam.dylib
      if [ -n "$MS_SIG" ]; then
        mkdir -p vendor/macsteam/signatures
        cp -R "$MS_SIG"/. vendor/macsteam/signatures/
      fi
      echo "vendored: macsteam.dylib + $(find vendor/macsteam/signatures -name '*.json' 2>/dev/null | wc -l | tr -d ' ') signature files"
    else
      echo "vendor: payload had no macsteam.dylib (runtime network fallback stays active)"
    fi
    hdiutil detach "$MS_MNT" -quiet >/dev/null 2>&1 || true
    rm -rf "$MS_MNT" "$MS_TMP"
  else
    echo "vendor: download failed (runtime network fallback stays active)"
  fi
else
  echo "vendor: release lookup failed (runtime network fallback stays active)"
fi
set -e

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
  --add-data "vendor/macsteam:vendor/macsteam" \
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

echo "== prune build dirs =="
rm -rf build-lic .build-venv
df -h / | tail -1

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
