#!/usr/bin/env bash
# Build "LicenseTools.app" and "LicenseTools.dmg" on macOS.
# usage: bash build_licensetools.sh
#        LT_STAGE=loader bash build_licensetools.sh   # only the loader payload
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="LicenseTools"
BUNDLE="LicenseTools"
PY="${PYTHON:-python3}"
STAGE="${LT_STAGE:-all}"

fail() {
  echo "build failed: $*" >&2
  echo "::error::build failed: $*"
  exit 1
}

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found — install with: brew install python@3.12" >&2
  exit 1
fi

if [ "$STAGE" != "loader" ]; then
echo "== venv =="
if [ ! -x .build-venv/bin/python ]; then
  "$PY" -m venv .build-venv
fi
# shellcheck disable=SC1091
source .build-venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pyinstaller "pywebview>=5.0" certifi pycryptodome zstandard
fi

if [ "$STAGE" != "loader" ]; then
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
fi

echo "== vendor: macSteam loader =="
mkdir -p vendor/macsteam/signatures/macos.arm64
MS_DYLIB="vendor/macsteam/macsteam.dylib"

if [ -s "$MS_DYLIB" ]; then
  echo "payload: already vendored ($(wc -c < "$MS_DYLIB" | tr -d ' ') bytes)"
elif [ -f vendor/macsteam-src/Makefile ]; then
  echo "payload: building from vendor/macsteam-src"
  if ! command -v clang >/dev/null 2>&1; then
    fail "clang is missing on this runner"
  fi
  if ! make -C vendor/macsteam-src MIN_VER=14.0 -j"$(sysctl -n hw.ncpu)" \
       > /tmp/macsteam-build.log 2>&1; then
    tail -n 40 /tmp/macsteam-build.log >&2
    echo "::error::loader build failed: $(tail -n 4 /tmp/macsteam-build.log | tr '\n' ' ' | tr -s ' ' | cut -c1-400)"
    fail "the vendored loader did not compile"
  fi
  cp vendor/macsteam-src/out/macsteam.dylib "$MS_DYLIB"
  echo "payload: built $(wc -c < "$MS_DYLIB" | tr -d ' ') bytes"
  rm -rf vendor/macsteam-src/build vendor/macsteam-src/out vendor/macsteam-src/vendor/dobby
else
  echo "payload: source missing - falling back to the upstream release"
  rm -f "$MS_DYLIB"
  mkdir -p vendor/macsteam/signatures/macos.arm64
  MS_URL="$(curl -fsSL --retry 3 --max-time 60 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: licensetools' \
    https://api.github.com/repos/crghofficial/macsteam/releases/latest 2>/dev/null \
    | sed -n 's/.*"browser_download_url": *"\([^"]*\.dmg\)".*/\1/p' | head -n1)"
  if [ -n "$MS_URL" ]; then
    MS_TMP="/tmp/macsteam-lic.dmg"
    MS_MNT="$(mktemp -d /tmp/msmnt.XXXXXX)"
    if curl -fsSL --retry 3 --max-time 300 -o "$MS_TMP" "$MS_URL" \
       && hdiutil attach -nobrowse -readonly -mountpoint "$MS_MNT" "$MS_TMP" >/dev/null 2>&1; then
      MS_DYLIB_SRC="$(find "$MS_MNT" -name 'macsteam.dylib' -type f 2>/dev/null | head -n1)"
      if [ -n "$MS_DYLIB_SRC" ]; then
        cp "$MS_DYLIB_SRC" "$MS_DYLIB"
      fi
      hdiutil detach "$MS_MNT" -quiet >/dev/null 2>&1 || true
    fi
    rm -rf "$MS_MNT" "$MS_TMP"
  fi
  if [ ! -s "$MS_DYLIB" ]; then
    fail "no loader payload, and none could be fetched"
  fi
fi

MS_SIGS="$(find vendor/macsteam/signatures -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$MS_SIGS" -lt 1 ]; then
  fail "no signature json under vendor/macsteam/signatures"
fi
echo "signatures: $MS_SIGS file(s)"
lipo -info "$MS_DYLIB" 2>/dev/null || true

MS_VER="$(sed -n 's/.*MACSTEAM_VERSION[[:space:]]*"\([^"]*\)".*/\1/p' \
  vendor/macsteam-src/src/version.h 2>/dev/null | head -n1)"
if [ -n "$MS_VER" ]; then
  printf '%s\n' "$MS_VER" > vendor/macsteam/macsteam.dylib.version
  echo "payload version: $MS_VER"
fi
MS_BYTES="$(wc -c < "$MS_DYLIB" | tr -d ' ')"
if [ "$MS_BYTES" -lt 4096 ]; then
  fail "the loader payload is suspiciously small ($MS_BYTES bytes)"
fi

echo "== vendor: stand-in library (prebuilt, so no clang is needed on the user's mac) =="
rm -rf vendor/stub && mkdir -p vendor/stub
touch vendor/stub/.keep
if command -v clang >/dev/null 2>&1; then
  python licensetools_mac.py --emit-stub vendor/stub/steam_stub.c >/dev/null
  if clang -dynamiclib -O2 -fvisibility=hidden \
      -install_name "@rpath/libsteam_api.dylib" \
      -arch arm64 -arch x86_64 \
      -o vendor/stub/libsteam_api.dylib vendor/stub/steam_stub.c; then
    codesign -f -s - vendor/stub/libsteam_api.dylib || true
    lipo -info vendor/stub/libsteam_api.dylib || true
    ls -lh vendor/stub/libsteam_api.dylib
  else
    fail "the prebuilt stand-in did not compile"
  fi
  rm -f vendor/stub/steam_stub.c
else
  echo "vendor: clang missing on the runner (runtime build path stays active)"
fi

if [ "$STAGE" = "loader" ]; then
  echo "== loader stage done: $(wc -c < "$MS_DYLIB" | tr -d ' ') byte payload, $MS_SIGS signature file(s) =="
  exit 0
fi

echo "== offline selftest =="
python licensetools_mac.py --selftest

echo "== clean =="
rm -rf build-lic dist-lic "$BUNDLE.spec"

echo "== depot key table =="
KEYDB_ARG=()
if curl -fsSL --max-time 240 -o depotkeys.json \
  "https://raw.githubusercontent.com/SteamAutoCracks/ManifestHub/main/depotkeys.json"; then
  KEYDB_SIZE=$(wc -c < depotkeys.json | tr -d ' ')
  if [ "$KEYDB_SIZE" -gt 1000000 ]; then
    echo "key table: $KEYDB_SIZE bytes, bundling"
    KEYDB_ARG=(--add-data "depotkeys.json:.")
  else
    echo "key table: too small ($KEYDB_SIZE), runtime fetch stays active"
  fi
else
  echo "key table: download failed, runtime fetch stays active"
fi

echo "== pyinstaller =="
pyinstaller \
  --noconfirm --clean --windowed \
  --name "$BUNDLE" \
  --icon icon.icns \
  --add-data "vendor/dd:vendor/dd" \
  --add-data "vendor/macsteam:vendor/macsteam" \
  --add-data "vendor/stub:vendor/stub" \
  "${KEYDB_ARG[@]}" \
  --collect-data certifi \
  --hidden-import webview.platforms.cocoa \
  --hidden-import Crypto.Cipher.AES \
  --hidden-import zstandard \
  --collect-submodules webview \
  --osx-bundle-identifier "com.whoismelv.licensetools" \
  --distpath dist-lic --workpath build-lic \
  licensetools_mac.py

APP="dist-lic/$BUNDLE.app"
if [ ! -d "$APP" ]; then
  fail "$APP missing"
fi

if [ ! -x "$APP/Contents/Resources/vendor/dd/DepotDownloader" ]; then
  fail "bundled DepotDownloader missing from the bundle"
fi

STUB_IN_APP="$APP/Contents/Resources/vendor/stub/libsteam_api.dylib"
if [ ! -f "$STUB_IN_APP" ]; then
  fail "the prebuilt stand-in is not inside the bundle"
fi
echo "stand-in inside the bundle:"
lipo -info "$STUB_IN_APP" || true

MS_IN_APP="$APP/Contents/Resources/vendor/macsteam/macsteam.dylib"
if [ ! -s "$MS_IN_APP" ]; then
  fail "the loader payload is not inside the bundle"
fi
MS_SIG_IN_APP="$(find "$APP/Contents/Resources/vendor/macsteam" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$MS_SIG_IN_APP" -lt 1 ]; then
  fail "no signature json inside the bundle"
fi
echo "loader inside the bundle: $(lipo -archs "$MS_IN_APP" 2>/dev/null || echo '?') + $MS_SIG_IN_APP signature file(s)"
ls -lh "$MS_IN_APP"

echo "== selftest (frozen) =="
PROBE="$("$APP/Contents/MacOS/$BUNDLE" payload 2>&1 | tr '\n' ' ' | tr -s ' ' | cut -c1-1800 || true)"
echo "payload probe: $PROBE"
echo "::notice::payload probe: $PROBE"
if ! "$APP/Contents/MacOS/$BUNDLE" --selftest > /tmp/lic-frozen.json 2>&1; then
  tail -n 40 /tmp/lic-frozen.json >&2
  fail "frozen selftest: $(tr -d '\n' < /tmp/lic-frozen.json | tr -s ' ' | tail -c 900)"
fi
tail -n 4 /tmp/lic-frozen.json

echo "== ad-hoc sign =="
codesign --force --deep --sign - "$APP" || echo "codesign skipped"

echo "== prune build dirs =="
rm -rf build-lic .build-venv
df -h / | tail -1

echo "== dmg =="
rm -rf dmg-stage-lic && mkdir dmg-stage-lic
cp -R "$APP" dmg-stage-lic/
ln -s /Applications dmg-stage-lic/Applications
mkdir -p dist
if ! hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder dmg-stage-lic \
  -ov -format UDZO \
  "dist/LicenseTools.dmg"; then
  echo "srcfolder image failed - retrying with a fixed-size image"
  TMPIMG="/tmp/licensetools-tmp.dmg"
  MNT="$(mktemp -d /tmp/licensetools-mnt.XXXXXX)"
  rm -f "$TMPIMG"
  hdiutil create -size 3g -fs HFS+ -volname "$APP_NAME" -ov "$TMPIMG"
  hdiutil attach "$TMPIMG" -mountpoint "$MNT" -nobrowse
  cp -R dmg-stage-lic/. "$MNT"/
  hdiutil detach "$MNT" -quiet
  rm -rf "$MNT"
  hdiutil convert "$TMPIMG" -format UDZO -o "dist/LicenseTools.dmg"
  rm -f "$TMPIMG"
fi
rm -rf dmg-stage-lic

echo
echo "done:"
ls -lh dist/LicenseTools.dmg
echo
echo "first launch: right-click the app -> Open, or run:"
echo "  xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
