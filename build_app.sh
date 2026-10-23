#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

swift test
swift build -c release

BIN_DIR="$(swift build -c release --show-bin-path)"
APP="$ROOT/build/SteamManifestManager.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

cp "$BIN_DIR/SteamManifestManager" "$APP/Contents/MacOS/SteamManifestManager"
cp "$ROOT/Packaging/Info.plist" "$APP/Contents/Info.plist"

codesign --force --sign - "$APP"

echo "Built: $APP"
