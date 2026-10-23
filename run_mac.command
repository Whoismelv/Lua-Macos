#!/usr/bin/env bash
# Double-click to run LuaTools Mac straight from source.
# Creates a local venv with pywebview on first run, then opens the window.
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Python 3 is required. Install it from python.org or run: brew install python@3.12" buttons {"OK"}'
  exit 1
fi

VENV=".venv"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV" || exit 1
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet "pywebview>=5.0" || {
    osascript -e 'display dialog "Could not install pywebview. Run: pip3 install pywebview" buttons {"OK"}'
    exit 1
  }
fi

exec "$VENV/bin/python" luatools_mac_gui.py
