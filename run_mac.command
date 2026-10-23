#!/usr/bin/env bash
# Double-click to run LuaTools Mac straight from source (no build, no deps).
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Python 3 is required. Install it from python.org or run: brew install python@3.12" buttons {"OK"}'
  exit 1
fi
exec "$PY" luatools_mac_gui.py
