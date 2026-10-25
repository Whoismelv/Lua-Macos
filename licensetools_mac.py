#!/usr/bin/env python3
# language: python3.9+, file: licensetools_mac.py, runtime: stdlib-only (pywebview>=5.0 for gui)
# Steam license kit for macOS: probe public appinfo, write stplug-in lua, pin manifests.
# Mac paths first, Windows fallback kept so the same file runs on the bench.
import argparse
import concurrent.futures
import gzip
import hashlib
import io
import json
import lzma
import os
import plistlib
import re
import shlex
import shutil
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

try:
    if os.name == "nt":
        import winreg
    else:
        winreg = None
except ImportError:
    winreg = None

CACHE_DIR = Path.home() / ".licensetools"
VAULT_DIR = CACHE_DIR / "vault"
GAMES_DIR = CACHE_DIR / "games"
TOOLS_DIR = CACHE_DIR / "tools"
SETTINGS_PATH = CACHE_DIR / "settings.json"
LOADED_PATH = CACHE_DIR / "loaded.json"
STEM = "stplug-in"
TOML_NAME = "opensteamtool.toml"
UA = "licensetools-mac"
APP_VERSION = "1.6.0"
APP_REPO = "Whoismelv/Lua-Macos"
APP_ASSET_NAMES = ("LicenseTools.dmg", "LuaToolsMac.dmg")
STEAMCMD_URLS = {
    "darwin": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz",
    "linux": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
    "win32": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
}
APPID_FILE_RE = re.compile(r"^\s*(\d+)")
ACF_RE = re.compile(r'"(\w+)"\s+"([^"]*)"')

# ---------- settings / state ----------

def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_settings(s):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")

def load_json_list(path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []

def save_json_list(path, vals):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vals, indent=2), encoding="utf-8")

def record_loaded(appid):
    loaded = load_json_list(LOADED_PATH)
    if appid not in loaded:
        loaded.append(appid)
        save_json_list(LOADED_PATH, loaded)

def drop_loaded(appid):
    loaded = [x for x in load_json_list(LOADED_PATH) if x != appid]
    save_json_list(LOADED_PATH, loaded)

def stamp_now(path):
    try:
        now = time.time()
        os.utime(path, (now, now))
    except OSError:
        pass

def app_id_from_name(p):
    m = APPID_FILE_RE.match(Path(p).stem)
    return int(m.group(1)) if m else None

def _appid(raw):
    s = str(raw or "").strip()
    if not s.isdigit():
        sys.exit("appid must be numeric")
    return int(s)

def http_get(url, headers=None, timeout=60):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()).read()
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            raise RuntimeError("TLS verify failed - pip install certifi") from exc
        raise

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return ssl.create_default_context()

# ---------- self update ----------

def _vtuple(text):
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) or (0,)


def _app_bundle_path():
    """The .app this process is running out of, or None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app" and p.is_dir():
            return p
    return None


def app_update_check(timeout=30):
    url = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
    try:
        data = json.loads(http_get(url, headers={"Accept": "application/vnd.github+json"},
                                   timeout=timeout))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "current": APP_VERSION}
    latest = str(data.get("tag_name") or "").lstrip("vV")
    assets = {a.get("name"): a for a in (data.get("assets") or []) if a.get("name")}
    picked = next((assets[n] for n in APP_ASSET_NAMES if n in assets), None)
    return {
        "ok": True, "current": APP_VERSION, "latest": latest or APP_VERSION,
        "newer": bool(latest) and _vtuple(latest) > _vtuple(APP_VERSION),
        "asset": ({"name": picked["name"], "bytes": int(picked.get("size") or 0),
                   "url": picked["browser_download_url"]} if picked else None),
        "page": data.get("html_url"), "published": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
    }


def _download_to(url, dest, progress=None, expect=0):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    got = 0
    with urllib.request.urlopen(req, timeout=180, context=_ssl_context()) as r, \
            open(part, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0) or int(expect or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if progress and total and got % (4 << 20) < (1 << 16):
                progress(f"downloaded {got * 100 // total}%")
    if expect and got != int(expect):
        part.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch: got {got} byte(s), expected {int(expect)}")
    os.replace(part, dest)
    return got


def _bundle_id(path):
    """CFBundleIdentifier of an .app, or "" when it cannot be read."""
    try:
        with open(Path(path) / "Contents" / "Info.plist", "rb") as fh:
            return str(plistlib.load(fh).get("CFBundleIdentifier") or "")
    except Exception:
        return ""


def _pick_app(apps, wanted_name="", wanted_id=""):
    """Choose the bundle to install: same name first, then same bundle id, then the first."""
    apps = [p for p in apps if p.suffix == ".app"]
    if wanted_name:
        for p in apps:
            if p.name == wanted_name:
                return p
    if wanted_id:
        for p in apps:
            if _bundle_id(p) == wanted_id:
                return p
    return apps[0] if apps else None


def _swap_script(new_bundle, dest, pid):
    """A detached shard that waits for this process to die, then swaps and reopens.

    The running bundle cannot replace itself, and macOS will not let a live process
    overwrite its own executable. So the swap runs outside, after the pid is gone.
    """
    return f"""#!/bin/sh
pid={int(pid)}
new={shlex.quote(str(new_bundle))}
dest={shlex.quote(str(dest))}
i=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 0.3
  i=$((i+1))
  [ "$i" -gt 400 ] && break
done
sleep 0.6
rm -rf "$dest.old"
if [ -e "$dest" ]; then mv "$dest" "$dest.old"; fi
if ditto "$new" "$dest"; then
  xattr -dr com.apple.quarantine "$dest" >/dev/null 2>&1 || true
  rm -rf "$dest.old"
  open "$dest"
else
  rm -rf "$dest"
  mv "$dest.old" "$dest"
  open "$dest"
fi
rm -rf "$new"
rm -f "$0"
"""


def _exit_soon(delay=1.6):
    time.sleep(delay)
    os._exit(0)


def app_update_apply(progress=None):
    say = progress or (lambda m: None)
    dest = _app_bundle_path()
    if dest is None:
        return {"ok": False, "error": "running from source - update with git pull"}
    if sys.platform != "darwin":
        return {"ok": False, "error": "the in-app update runs on macOS only"}
    if "AppTranslocation" in str(dest):
        return {"ok": False, "error": "macOS is running a translated copy of the app - "
                                      "move it to /Applications and open it from there"}
    info = app_update_check()
    if not info.get("ok"):
        return info
    if not info.get("newer"):
        return {"ok": True, "up_to_date": True, "current": info["current"],
                "latest": info["latest"], "page": info.get("page")}
    asset = info.get("asset")
    if not asset:
        return {"ok": False, "error": "the latest release has no dmg",
                "page": info.get("page")}
    work = CACHE_DIR / "update"
    dmg = work / asset["name"]
    try:
        say(f"downloading {asset['name']} ({asset['bytes'] // (1024 * 1024)} MB)")
        _download_to(asset["url"], dmg, progress, expect=asset.get("bytes") or 0)
    except Exception as exc:
        return {"ok": False, "error": f"download failed: {exc}"}
    mount = work / "mnt"
    staging = work / "stage"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(mount, ignore_errors=True)
    try:
        mount.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    res = _run_quiet(["hdiutil", "attach", "-nobrowse", "-noverify", "-readonly",
                      "-mountpoint", str(mount), str(dmg)], timeout=300)
    if res is None or res.returncode != 0:
        detail = (getattr(res, "stderr", "") or "").strip() if res is not None else ""
        return {"ok": False, "error": ("could not open the dmg: " + detail).strip()}
    try:
        apps = [p for p in mount.iterdir() if p.suffix == ".app" and p.is_dir()]
        if not apps:
            return {"ok": False, "error": "no .app inside the dmg"}
        pick = _pick_app(apps, dest.name, _bundle_id(dest))
        if pick is None:
            return {"ok": False, "error": "no .app inside the dmg"}
        say("staging the new build")
        staging.mkdir(parents=True, exist_ok=True)
        copy = _run_quiet(["ditto", str(pick), str(staging / dest.name)], timeout=600)
        if copy is None or copy.returncode != 0:
            return {"ok": False, "error": "copy out of the dmg failed"}
    finally:
        _run_quiet(["hdiutil", "detach", str(mount), "-force"], timeout=120)
    new = staging / dest.name
    if not (new / "Contents" / "Info.plist").is_file():
        return {"ok": False, "error": "the dmg did not contain a usable app"}
    wanted_id = _bundle_id(dest)
    if wanted_id and _bundle_id(new) != wanted_id:
        return {"ok": False, "error": "the dmg holds a different app - refusing to swap"}
    if not os.access(dest.parent, os.W_OK):
        _run_quiet(["open", str(dmg)], timeout=60)
        return {"ok": False, "staged": str(new), "dmg": str(dmg),
                "error": f"{dest.parent} is not writable - the dmg was opened, drag the app to "
                         f"where you want it"}
    script = work / "swap.sh"
    try:
        script.write_text(_swap_script(new, dest, os.getpid()), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": f"could not start the swap: {exc}"}
    threading.Thread(target=_exit_soon, daemon=True).start()
    return {"ok": True, "relaunching": True, "from": info["current"],
            "to": info["latest"], "staged": str(new), "dmg": str(dmg)}


# ---------- steam discovery ----------

class SteamCtx:
    def __init__(self):
        self.settings = load_settings()

    def root(self):
        override = (self.settings.get("steam_path") or "").strip()
        if override:
            return Path(override).expanduser().resolve()
        mac = Path.home() / "Library" / "Application Support" / "Steam"
        if sys.platform == "darwin":
            return mac if mac.is_dir() else None
        if winreg is not None:
            raw = self._win_reg()
            if raw:
                return Path(raw).resolve()
        return mac if mac.is_dir() else None

    @staticmethod
    def _win_reg():
        probes = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]
        for hive, subkey, value in probes:
            try:
                with winreg.OpenKey(hive, subkey) as k:
                    raw = winreg.QueryValueEx(k, value)[0]
                if raw and str(raw).strip():
                    return str(raw).strip().replace("/", "\\")
            except OSError:
                continue
        return None

    def plugin_dir(self):
        r = self.root()
        return None if r is None else r / "config" / STEM

    def depot_cache(self):
        r = self.root()
        return None if r is None else r / "steamapps" / "depotcache"

    def steam_exe(self):
        r = self.root()
        if r is None:
            return None
        if sys.platform == "darwin":
            macos = steam_app_path() / "Contents" / "MacOS"
            for name in ("steam_osx", "steam"):
                if (macos / name).exists():
                    return macos / name
            return macos / "steam_osx"
        return r / "steam.exe"

    def valid(self):
        r = self.root()
        if r is None:
            return False
        if winreg is not None:
            exe = self.steam_exe()
            return bool(exe and exe.exists())
        return (r / "config").is_dir()

    def running(self):
        if winreg is not None:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                                 capture_output=True, text=True)
            return "steam.exe" in out.stdout.lower()
        return _mac_steam_pids() != []

    def stop(self, timeout=25):
        return steam_quit(timeout)

    def start(self, timeout=25):
        return steam_launch(timeout)

STEAM_BUNDLE_ID = "com.valvesoftware.steam"

def steam_app_path():
    for raw in ("/Applications/Steam.app", "~/Applications/Steam.app"):
        p = Path(raw).expanduser()
        if p.is_dir():
            return p
    return Path("/Applications/Steam.app")

def _run_quiet(argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None

def _pgrep(flags):
    res = _run_quiet(["pgrep"] + flags, timeout=10)
    if res is None or res.returncode != 0:
        return []
    return sorted({t for t in res.stdout.split() if t.isdigit()})

def _mac_steam_pids():
    pids = set(_pgrep(["-x", "steam_osx"]))
    pids.update(_pgrep(["-x", "Steam"]))
    pids.update(_pgrep(["-f", "Steam.app/Contents/MacOS/steam"]))
    return sorted(pids)

def _mac_helper_pids():
    return _pgrep(["-x", "steamwebhelper"])

def _wait_gone(seconds, probe=None):
    probe = probe or _mac_steam_pids
    deadline = time.time() + max(1.0, seconds)
    while time.time() < deadline:
        if not probe():
            return True
        time.sleep(0.4)
    return not probe()

def steam_quit(timeout=25):
    if winreg is not None:
        before = SteamCtx().running()
        _run_quiet(["taskkill", "/F", "/IM", "steam.exe", "/T"], timeout=20)
        deadline = time.time() + 8
        while time.time() < deadline and SteamCtx().running():
            time.sleep(0.4)
        return {"ok": not SteamCtx().running(), "was_running": before,
                "method": "taskkill"}
    pids = _mac_steam_pids()
    if not pids:
        for pid in _mac_helper_pids():
            _run_quiet(["kill", "-KILL", pid], timeout=5)
        return {"ok": True, "was_running": False, "method": "already-quit"}

    _run_quiet(["osascript", "-e",
                f'tell application id "{STEAM_BUNDLE_ID}" to quit'], timeout=15)
    if _wait_gone(max(4, timeout * 0.4)):
        return {"ok": True, "was_running": True, "method": "apple-event"}

    for pid in _mac_steam_pids():
        _run_quiet(["kill", "-TERM", pid], timeout=5)
    if _wait_gone(max(3, timeout * 0.25)):
        return {"ok": True, "was_running": True, "method": "term"}

    for pid in _mac_steam_pids():
        _run_quiet(["kill", "-KILL", pid], timeout=5)
    if _wait_gone(6):
        return {"ok": True, "was_running": True, "method": "kill"}

    for pid in _mac_helper_pids():
        _run_quiet(["kill", "-KILL", pid], timeout=5)
    if _wait_gone(5):
        return {"ok": True, "was_running": True, "method": "kill+helper"}

    left = _mac_steam_pids()
    return {"ok": False, "was_running": True, "method": "failed", "pids": left,
            "error": "steam is still running - quit it from the menu bar"}

def steam_launch(timeout=25, wait=True):
    if winreg is not None:
        exe = SteamCtx().steam_exe()
        root = SteamCtx().root()
        if exe and exe.exists():
            try:
                subprocess.Popen([str(exe)], cwd=str(root))
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "launched": True}
        return {"ok": False, "error": "steam.exe not found"}
    if _mac_steam_pids():
        return {"ok": True, "launched": False, "running": True,
                "method": "already-running"}
    errors = []
    for argv in (["open", "-b", STEAM_BUNDLE_ID],
                 ["open", "-a", "Steam"],
                 ["open", str(steam_app_path())]):
        res = _run_quiet(argv, timeout=25)
        if res is not None and res.returncode == 0:
            break
        errors.append((res.stderr.strip() if res is not None and res.stderr
                       else (argv[0] + " failed")))
    else:
        return {"ok": False, "error": " | ".join(errors[-2:]) or "open failed"}
    if not wait:
        return {"ok": True, "launched": True, "running": False, "method": "open"}
    deadline = time.time() + max(6, timeout * 0.6)
    while time.time() < deadline and not _mac_steam_pids():
        time.sleep(0.5)
    return {"ok": True, "launched": True, "running": bool(_mac_steam_pids()),
            "method": "open"}

def steam_wait_ready(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _mac_steam_pids():
            return True
        time.sleep(0.5)
    return bool(_mac_steam_pids())

def restart_steam(timeout=25):
    quit_info = steam_quit(timeout)
    launch_info = steam_launch(timeout)
    ok = bool(quit_info.get("ok") and launch_info.get("ok"))
    return {"ok": ok, "restarted": ok, "quit": quit_info, "launch": launch_info,
            "error": quit_info.get("error") or launch_info.get("error")}

def mac_steam_root():
    return Path.home() / "Library" / "Application Support" / "Steam"

def library_roots():
    roots = []
    root = SteamCtx().root()
    if root is not None:
        roots.append(root)
    else:
        roots.append(mac_steam_root())
    base = roots[0]
    vdf = base / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            data = vdf_parse(vdf.read_text(encoding="utf-8", errors="replace"))
            folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
            for key, val in folders.items():
                raw = val.get("path") if isinstance(val, dict) else val
                if isinstance(raw, str) and raw.strip():
                    roots.append(Path(raw.strip()))
        except Exception:
            pass
    seen, out = set(), []
    for r in roots:
        try:
            key = str(r)
        except Exception:
            continue
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def paths_report():
    root = SteamCtx().root()
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    app = steam_app_path()
    exe = SteamCtx().steam_exe()
    keys = key_status()
    return {
        "platform": sys.platform,
        "home": str(Path.home()),
        "steam_root": str(root) if root else None,
        "steam_root_expected": str(mac_steam_root()),
        "steam_root_exists": bool(root),
        "steamapps": str(root / "steamapps") if root else None,
        "steamapps_exists": bool(root and (root / "steamapps").is_dir()),
        "common": str(root / "steamapps" / "common") if root else None,
        "libraries": [str(r) for r in library_roots()],
        "steam_app": str(app) if app.is_dir() else None,
        "steam_exe": str(exe) if exe else None,
        "macsteam_dir": str(cfg),
        "macsteam_dir_exists": cfg.is_dir(),
        "macsteam_config": str(cfg / "config.yaml"),
        "macsteam_config_exists": (cfg / "config.yaml").is_file(),
        "dylib": str(app / "Contents" / "MacOS" / "macsteam.dylib"),
        "dylib_exists": (app / "Contents" / "MacOS" / "macsteam.dylib").is_file(),
        "depot_keys_path": keys["path"],
        "depot_keys_exists": keys["exists"],
        "depot_keys_count": keys["keys"],
        "depot_keys_bytes": keys["bytes"],
        "depot_keys_age_hours": keys["age_hours"],
        "depot_keys_error": keys["error"],
        "depotcache": str(root / "steamapps" / "depotcache") if root else None,
        "depotcache_manifests": _depotcache_count(root),
    }

def _depotcache_count(root):
    if root is None:
        return 0
    d = root / "steamapps" / "depotcache"
    try:
        return sum(1 for p in d.glob("*.manifest") if p.stat().st_size > 1000)
    except OSError:
        return 0

# ---------- opensteamtool.toml editor (line-parity with the C# writer) ----------

def _is_header_named(line, name):
    t = line.strip()
    return (not t.startswith("#")) and re.match(r"^\[\s*" + re.escape(name) + r"\s*\]", t) is not None

def _is_any_header(line):
    t = line.strip()
    return (not t.startswith("#")) and re.match(r"^\[[^\[].*\]", t) is not None

def ensure_lua_path(root: Path):
    path = root / TOML_NAME
    wanted = 'paths = ["config/' + STEM + '"]'
    if not path.exists():
        path.write_text("[lua]\n" + wanted + "\n")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, l in enumerate(lines) if _is_header_named(l, "lua")), None)
    if idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[lua]", wanted]
        path.write_text("\n".join(lines) + "\n")
        return
    nxt = next((i for i, l in enumerate(lines[idx + 1:], idx + 1) if _is_any_header(l)), len(lines))
    has_paths = any(l.strip() and not l.strip().startswith("#") and re.match(r"^paths\s*=", l.strip())
                    for l in lines[idx + 1:nxt])
    if not has_paths:
        lines.insert(idx + 1, wanted)
        path.write_text("\n".join(lines) + "\n")
        return
    for i in range(idx + 1, nxt):
        raw = lines[i]
        t = raw.strip()
        if not re.match(r"^paths\s*=", t):
            continue
        if re.search(r"config[/\\]+" + re.escape(STEM), t, re.I):
            return
        end = t.rfind("]")
        if end < 0:
            return
        prefix, tail = t[:end].rstrip(), t[end:]
        sep = " " if prefix.endswith("[") else ", "
        indent = raw[:len(raw) - len(raw.lstrip())]
        lines[i] = indent + prefix + sep + '"config/' + STEM + '"' + tail
        path.write_text("\n".join(lines) + "\n")
        return

def loader_status(root):
    if root is None:
        return "steam-not-found"
    p = root / TOML_NAME
    if not p.exists():
        return "not-configured"
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    return "configured" if STEM in body else "incomplete"

# ---------- text VDF (appinfo) parser ----------

def vdf_tokens(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            out = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    out.append(text[j + 1])
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    out.append(text[j])
                    j += 1
            yield "".join(out)
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield text[i:j]
            i = j

def vdf_parse(text):
    stack = [{}]
    pending = None
    for tok in vdf_tokens(text):
        if tok == "{":
            child = {}
            if pending is not None:
                stack[-1][pending] = child
            stack.append(child)
            pending = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            pending = None
        elif pending is None:
            pending = tok
        else:
            stack[-1][pending] = tok
            pending = None
    return stack[0]

def _find_app_node(tree, appid, depth=0):
    if not isinstance(tree, dict) or depth > 4:
        return None
    if isinstance(tree.get("depots"), dict) and ("appid" in tree or "common" in tree):
        return tree
    node = tree.get(str(appid))
    if isinstance(node, dict) and isinstance(node.get("depots"), dict):
        return node
    for value in tree.values():
        if isinstance(value, dict):
            found = _find_app_node(value, appid, depth + 1)
            if found is not None:
                return found
    return None

def _extract_appinfo(out, appid):
    # steamcmd can print a banner, restart itself, or truncate the head of the
    # appinfo block; try every occurrence of the app key, newest first.
    pattern = re.compile(r'(?m)^[ \t]*"%s"[ \t]*\r?$' % re.escape(str(appid)))
    starts = [m.start() for m in pattern.finditer(out)]
    if not starts:
        starts = [m.start() for m in re.finditer(r'"%s"\s*\{' % re.escape(str(appid)), out)]
    for start in reversed(starts):
        tree = vdf_parse(out[start:])
        app = tree.get(str(appid))
        if isinstance(app, dict) and isinstance(app.get("depots"), dict):
            return app
    quote = out.find('"')
    if quote < 0:
        return None
    return _find_app_node(vdf_parse(out[quote:]), appid)

def _oslist_matches(oslist, target):
    if not oslist:
        return False
    aliases = {"macos": ("macos", "mac", "osx"), "windows": ("windows", "win")}.get(target, (target,))
    if isinstance(oslist, dict):
        return any(oslist.get(a) for a in aliases)
    parts = re.split(r"[\s,]+", str(oslist).lower())
    return any(a in parts for a in aliases)

def probe_from_text(appid, text, branch="public"):
    if '"' not in text:
        return {"ok": False, "error": "no appinfo text in steamcmd output"}
    app = _extract_appinfo(text, appid)
    if not isinstance(app, dict) or not isinstance(app.get("depots"), dict):
        return {"ok": False, "error": "appinfo has no depots section (app may be delisted)"}
    depots = app["depots"]
    bnode = (depots.get("branches") or {}).get(branch) or {}
    buildid = str(bnode.get("buildid", "")) if isinstance(bnode, dict) else ""
    rows = []
    for depot_id, depot in depots.items():
        if not str(depot_id).isdigit() or not isinstance(depot, dict):
            continue
        oslist = (depot.get("config") or {}).get("oslist", "")
        hits = [o for o in ("mac", "win", "linux")
                if _oslist_matches(oslist, "macos" if o == "mac" else
                                   "windows" if o == "win" else "linux")]
        osname = hits[0] if len(hits) == 1 else "shared"
        node = (depot.get("manifests") or {}).get(branch)
        if isinstance(node, dict):
            gid = str(node.get("gid") or "")
        else:
            gid = str(node or "") if node else ""
        size = str(depot.get("size") or "")
        dl = ""
        if isinstance(node, dict):
            dl = str(node.get("download") or "")
        rows.append({"depot": str(depot_id), "os": osname, "manifest": gid,
                     "size": size if size.isdigit() else "",
                     "download": dl if dl.isdigit() else "", "branch": branch})
    if not rows:
        return {"ok": False, "error": "no numeric depots in appinfo"}
    common = app.get("common") if isinstance(app.get("common"), dict) else {}
    return {"ok": True, "appid": str(appid), "branch": branch, "buildid": buildid,
            "count": len(rows), "rows": rows,
            "name": str(common.get("name") or ""),
            "installdir": str(common.get("installdir") or ""),
            "size": str(common.get("size") or "")}

def select_rows(rows, want_os="mac", shared=True):
    if want_os == "all":
        return list(rows)
    return [r for r in rows if r["os"] == want_os or (shared and r["os"] == "shared")]

# ---------- lua generation ----------

LABELS = {"shared": "shared depots (all OS)", "mac": "macos depots",
          "win": "windows depots", "linux": "linux depots"}

def gen_lua(appid, rows, branch="public", pin=True, buildid=""):
    groups = {}
    for r in rows:
        groups.setdefault(r["os"], []).append(r)
    order = [k for k in ("shared", "mac", "win", "linux") if k in groups]
    order += [k for k in groups if k not in order]
    head = f"-- licensetools - appid {appid} - branch {branch}"
    if buildid:
        head += f" - build {buildid}"
    lines = [head, f"addappid({appid})", f"addappid({appid}, 1)", ""]
    files = []
    for osname in order:
        lines.append(f"-- {LABELS.get(osname, osname)}")
        for r in groups[osname]:
            lines.append(f"addappid({r['depot']}, 0)")
            if r["manifest"]:
                stmt = f'setManifestid({r["depot"]}, "{r["manifest"]}"'
                if r["size"]:
                    stmt += f", {r['size']}"
                stmt += ")"
                lines.append(stmt if pin else "-- " + stmt)
                files.append(f"{r['depot']}_{r['manifest']}.manifest")
            else:
                lines.append(f"-- no {branch} manifest for depot {r['depot']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", files

# ---------- vault / install / remove ----------

def vault_write(appid, branch, text):
    d = VAULT_DIR / str(appid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{branch}.lua").write_text(text, encoding="utf-8")
    idx_path = d / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        idx = {}
    idx.setdefault("branches", {})[branch] = {"ts": time.time(), "source": "licensetools"}
    idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

def vault_list(appid=None):
    def one(p):
        idx = {}
        f = p / "index.json"
        if f.exists():
            try:
                idx = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                idx = {}
        return {"branches": sorted(idx.get("branches", {}).keys()),
                "files": sorted(x.name for x in p.glob("*.lua"))}
    if appid:
        d = VAULT_DIR / str(appid)
        if not d.is_dir():
            return {"appid": appid, "branches": [], "files": []}
        return {"appid": appid, **one(d)}
    out = {}
    if VAULT_DIR.is_dir():
        for p in VAULT_DIR.iterdir():
            if p.is_dir() and p.name.isdigit():
                out[int(p.name)] = one(p)
    return out

def install_lua_text(appid, text, branch="public"):
    ctx = SteamCtx()
    st = ctx.plugin_dir()
    if st is None:
        return {"ok": False, "error": "steam root not found - set with: settings --steam-path ..."}
    st.mkdir(parents=True, exist_ok=True)
    dest = st / f"{int(appid)}.lua"
    dest.write_text(text, encoding="utf-8")
    stamp_now(dest)
    vault_write(int(appid), branch, text)
    record_loaded(int(appid))
    notes = []
    r = ctx.root()
    if r and r.exists():
        try:
            ensure_lua_path(r)
        except Exception as exc:
            notes.append(f"toml: {exc}")
    return {"ok": True, "appid": int(appid), "dest": str(dest),
            "lines": text.count("\n"), "notes": notes}

LUA_ADD_RE = re.compile(
    r'addappid\s*\(\s*(\d+)\s*(?:,\s*\d+\s*(?:,\s*"([0-9a-fA-F]+)"\s*)?)?\)')
LUA_MANIFEST_RE = re.compile(r'setManifestid\s*\(\s*(\d+)\s*,\s*"(\d+)"')

def lua_scan(text):
    ids, keys, manifests = [], {}, {}
    for m in LUA_ADD_RE.finditer(text or ""):
        aid = int(m.group(1))
        if aid not in ids:
            ids.append(aid)
        key = (m.group(2) or "").lower()
        if len(key) == 64:
            keys[aid] = key
    for m in LUA_MANIFEST_RE.finditer(text or ""):
        manifests[int(m.group(1))] = m.group(2)
    main = ids[0] if ids else None
    depots = [i for i in ids if i != main]
    return {"main": main, "ids": ids, "depots": depots,
            "keys": keys, "manifests": manifests}

def _lua_scan_dirs():
    dirs = []
    plugin = SteamCtx().plugin_dir()
    if plugin:
        dirs.append(plugin)
    home = Path.home()
    dirs.append(home / "Library" / "Application Support" / "macsteam")
    dirs.append(CACHE_DIR / "vault")
    dirs.append(home / "Downloads")
    out = []
    for d in dirs:
        try:
            if d and d.is_dir() and d not in out:
                out.append(d)
        except OSError:
            continue
    return out

def local_depot_keys(depots):
    want = {int(d) for d in depots}
    out = {}
    seen = 0
    for root in _lua_scan_dirs():
        try:
            files = sorted(root.rglob("*.lua"))
        except OSError:
            continue
        for f in files:
            seen += 1
            if seen > 500:
                break
            try:
                if f.stat().st_size > 4 * 1024 * 1024:
                    continue
                found = lua_scan(f.read_text(encoding="utf-8", errors="replace"))["keys"]
            except OSError:
                continue
            for d, k in found.items():
                if d in want and d not in out:
                    out[d] = k
            if len(out) == len(want):
                return out
    return out

DEPOT_KEYS_SOURCES = (
    "https://raw.githubusercontent.com/SteamAutoCracks/ManifestHub/main/depotkeys.json",
    "https://cdn.jsdelivr.net/gh/SteamAutoCracks/ManifestHub@main/depotkeys.json",
    "https://fastly.jsdelivr.net/gh/SteamAutoCracks/ManifestHub@main/depotkeys.json",
    "https://raw.githack.com/SteamAutoCracks/ManifestHub/main/depotkeys.json",
    "https://ghproxy.net/https://raw.githubusercontent.com/SteamAutoCracks/ManifestHub/main/depotkeys.json",
    "https://api.github.com/repos/SteamAutoCracks/ManifestHub/contents/depotkeys.json",
)
_DEPOT_DB = {"data": None, "stamp": 0.0, "path": None, "source": "",
             "error": "", "bytes": 0}

def _ssl_contexts():
    out = []
    try:
        import certifi
        out.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    try:
        out.append(ssl.create_default_context())
    except Exception:
        pass
    try:
        out.append(ssl._create_unverified_context())
    except Exception:
        pass
    return out

def _http_bytes(url, timeout=180, accept="application/vnd.github.raw, application/json, */*"):
    base = {"User-Agent": f"licensetools/{APP_VERSION}", "Accept": accept}
    last = ""
    for encoding in ("gzip", "identity"):
        headers = dict(base, **{"Accept-Encoding": encoding})
        for ctx in _ssl_contexts():
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    raw = r.read()
                    if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    return raw, ""
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
    return b"", last

def _table_from_blob(blob):
    data = json.loads(blob.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("not a json object")
    return {str(k): str(v).lower() for k, v in data.items()
            if isinstance(v, str) and len(v) == 64}

def _bundled_keys():
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass))
    try:
        roots.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    for base in roots:
        p = base / "depotkeys.json"
        try:
            if p.is_file() and p.stat().st_size > 100_000:
                return p
        except OSError:
            continue
    return None

def depot_key_db(refresh=False, progress=None, max_age=7 * 86400):
    path = CACHE_DIR / "depotkeys.json"
    now = time.time()
    if not refresh and _DEPOT_DB["data"] is not None and now - _DEPOT_DB["stamp"] < 3600:
        return _DEPOT_DB["data"]
    if not path.exists():
        src = _bundled_keys()
        if src:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, path)
                LOGS.add("depot key table: using the bundled copy")
            except OSError as exc:
                LOGS.add("bundled key table copy failed: " + str(exc))
    fresh = path.exists() and (now - path.stat().st_mtime) < max_age
    if refresh or not fresh:
        errors = []
        for url in DEPOT_KEYS_SOURCES:
            host = urllib.parse.urlsplit(url).netloc
            if progress:
                progress("fetching the depot key table from " + host + "...")
            blob, err = _http_bytes(url)
            if err:
                errors.append(f"{host}: {err}")
                continue
            if len(blob) < 100_000:
                errors.append(f"{host}: short reply {len(blob)}B")
                continue
            try:
                table = _table_from_blob(blob)
            except Exception as exc:
                errors.append(f"{host}: bad json {exc}")
                continue
            if len(table) < 1000:
                errors.append(f"{host}: only {len(table)} keys")
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(blob)
                tmp.replace(path)
            except OSError as exc:
                errors.append(f"{host}: cache write {exc}")
            _DEPOT_DB.update({"data": table, "stamp": now, "path": str(path),
                              "source": url, "error": "", "bytes": len(blob)})
            LOGS.add(f"depot key table: {len(table)} keys via {host}")
            return table
        _DEPOT_DB["error"] = " | ".join(errors[-3:]) or "no source answered"
        LOGS.add("depot key table download failed: " + _DEPOT_DB["error"])
    if not path.exists():
        return {}
    try:
        blob = path.read_bytes()
        table = _table_from_blob(blob)
    except Exception as exc:
        LOGS.add("depot key table unreadable: " + str(exc))
        return {}
    if not table:
        return {}
    _DEPOT_DB.update({"data": table, "stamp": now, "path": str(path),
                      "bytes": len(blob)})
    return table

def key_status():
    path = CACHE_DIR / "depotkeys.json"
    data = _DEPOT_DB.get("data") or {}
    age = None
    size = 0
    try:
        if path.exists():
            size = path.stat().st_size
            age = round((time.time() - path.stat().st_mtime) / 3600.0, 1)
    except OSError:
        pass
    return {"path": str(path), "exists": path.exists(), "bytes": size,
            "age_hours": age, "keys": len(data),
            "source": _DEPOT_DB.get("source", ""),
            "error": _DEPOT_DB.get("error", "")}

def keys_for_depots(depots, progress=None):
    want = [int(d) for d in depots]
    if not want:
        return {}
    out = local_depot_keys(want)
    missing = [d for d in want if d not in out]
    if missing:
        table = depot_key_db(progress=progress)
        for d in missing:
            key = table.get(str(d))
            if key:
                out[d] = key
    return out

def import_lua_file(path):
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"not found: {p}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    scan = lua_scan(text)
    if scan["main"] is None:
        return {"ok": False, "error": "no addappid(...) lines in that file"}
    main = scan["main"]
    inst = install_lua_text(main, text, "public")
    cfg = macsteam_config_add(main, depots=scan["depots"], keys=scan["keys"])
    LOGS.add(f"imported lua for {main}: {len(scan['depots'])} depot(s), "
             f"{len(scan['keys'])} key(s)")
    return {"ok": bool(inst.get("ok")), "appid": main,
            "depots": len(scan["depots"]), "keys": len(scan["keys"]),
            "lua": inst.get("dest"), "config": cfg,
            "error": inst.get("error") or cfg.get("error")}

def remove_lua(appid):
    st = SteamCtx().plugin_dir()
    if st is None:
        return {"ok": False, "error": "steam root not found"}
    f = st / f"{int(appid)}.lua"
    removed = None
    if f.exists():
        f.unlink()
        removed = str(f)
    drop_loaded(int(appid))
    return {"ok": True, "removed": removed,
            "reason": None if removed else "not installed"}

def installed_list():
    st = SteamCtx().plugin_dir()
    items = []
    if st and st.is_dir():
        for f in sorted(st.glob("*.lua")):
            try:
                blob = f.stat()
            except OSError:
                continue
            items.append({"appid": app_id_from_name(f), "file": f.name, "bytes": blob.st_size,
                          "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(blob.st_mtime))})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "items": items, "loaded": load_json_list(LOADED_PATH)}

# ---------- local manifest / zip install ----------

def install_manifest_file(src):
    dc = SteamCtx().depot_cache()
    if dc is None:
        return {"ok": False, "error": "steam root not found"}
    dc.mkdir(parents=True, exist_ok=True)
    dest = dc / Path(src).name
    if dest.exists():
        return {"ok": True, "skipped": True, "dest": str(dest)}
    shutil.copyfile(src, dest)
    stamp_now(dest)
    return {"ok": True, "skipped": False, "dest": str(dest)}

def install_zip_file(zip_path, appid=None, branch="public"):
    dc = SteamCtx().depot_cache()
    if dc is None:
        return {"ok": False, "error": "steam root not found"}
    dc.mkdir(parents=True, exist_ok=True)
    aid = appid or app_id_from_name(zip_path)
    if aid is None:
        try:
            with zipfile.ZipFile(zip_path) as z:
                for n in z.namelist():
                    if n.lower().endswith(".lua"):
                        aid = app_id_from_name(n)
                        if aid:
                            break
        except zipfile.BadZipFile:
            return {"ok": False, "error": "bad zip"}
    if aid is None:
        return {"ok": False, "error": "cannot resolve appid from zip"}
    lua_ok, manifests, errors = False, 0, []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.split("/")[-1]
            if not name:
                continue
            low = name.lower()
            if low.endswith(".lua"):
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / name
                    tmp.write_bytes(z.read(info))
                    res = install_local(tmp, aid, branch)
                    lua_ok = lua_ok or res.get("ok", False)
                    if not res.get("ok"):
                        errors.append(name)
            elif low.endswith(".manifest"):
                dest = dc / name
                if not dest.exists():
                    dest.write_bytes(z.read(info))
                    stamp_now(dest)
                manifests += 1
    return {"ok": True, "lua": lua_ok, "manifests": manifests,
            "appid": aid, "errors": errors}

def install_local(path, appid=None, branch="public"):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"missing: {p}"}
    suffix = p.suffix.lower()
    if suffix == ".lua":
        aid = appid or app_id_from_name(p)
        if not aid:
            return {"ok": False, "error": f"no appid in filename: {p.name} (pass --appid)"}
        st = SteamCtx().plugin_dir()
        if st is None:
            return {"ok": False, "error": "steam root not found"}
        st.mkdir(parents=True, exist_ok=True)
        text = p.read_text(encoding="utf-8", errors="replace")
        dest = st / f"{int(aid)}.lua"
        dest.write_text(text, encoding="utf-8")
        stamp_now(dest)
        vault_write(int(aid), branch, text)
        record_loaded(int(aid))
        return {"ok": True, "kind": "lua", "appid": int(aid), "dest": str(dest)}
    if suffix == ".manifest":
        return install_manifest_file(p)
    if suffix == ".zip":
        return install_zip_file(p, appid, branch)
    return {"ok": False, "error": f"unsupported file type: {suffix or '(none)'}"}

# ---------- steam library ----------

def _norm_lib_path(raw):
    p = raw.replace("\\\\", "\\")
    if sys.platform == "darwin" or winreg is None:
        return Path(p.replace("\\", "/")).expanduser()
    return Path(p).expanduser()

def read_vdf_paths(root: Path):
    sa = root / "steamapps"
    found = [sa] if sa.is_dir() else []
    vdf = sa / "libraryfolders.vdf"
    if vdf.exists():
        for m in re.finditer(r'"path"\s+"([^"]+)"',
                             vdf.read_text(encoding="utf-8", errors="replace")):
            t = _norm_lib_path(m.group(1)) / "steamapps"
            if t.is_dir() and all(str(t).lower() != str(x).lower() for x in found):
                found.append(t)
    return found

def parse_acf(path: Path):
    out = {}
    try:
        for k, v in ACF_RE.findall(path.read_text(encoding="utf-8", errors="replace")):
            out.setdefault(k, v)
    except Exception:
        pass
    return out

def library_list():
    ctx = SteamCtx()
    r = ctx.root()
    if r is None or not r.exists():
        return {"ok": False, "error": "steam root not found"}
    apps, seen = [], set()
    for lib in read_vdf_paths(r):
        for f in sorted(lib.glob("appmanifest_*.acf")):
            stem = f.stem.replace("appmanifest_", "")
            appid = int(stem) if stem.isdigit() else 0
            if appid == 0 or appid in seen:
                continue
            seen.add(appid)
            d = parse_acf(f)
            try:
                state = int(d.get("StateFlags", "0") or 0)
            except (TypeError, ValueError):
                state = 0
            apps.append({"appid": appid, "name": d.get("name", "") or f.stem,
                         "state": state,
                         "installdir": d.get("installdir", ""), "buildid": d.get("buildid", "")})
    return {"ok": True, "apps": apps, "count": len(apps)}

# ---------- steamcmd provisioning + probe ----------

def _find_tool(name):
    env = os.environ.get(name.upper() + "_PATH", "")
    if env and Path(env).is_file():
        return Path(env)
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "vendor" / "dd"
        if bundled.is_dir():
            for p in sorted(bundled.iterdir()):
                if (p.is_file() and p.name.lower().startswith(name.lower())
                        and p.suffix.lower() not in (".pdb", ".xml", ".zip")):
                    return p
    for rel in (name, f"{name}.exe", f"{name}.sh",
                f"dd/{name}", f"dd/{name}.exe",
                f"steamcmd/{name}", f"steamcmd/{name}.sh", f"steamcmd/{name}.exe"):
        p = TOOLS_DIR / rel
        if p.is_file():
            return p
    if TOOLS_DIR.exists():
        cands = []
        for p in TOOLS_DIR.rglob("*"):
            if not p.is_file():
                continue
            low = p.name.lower()
            if p.suffix in (".pdb", ".xml", ".zip", ".gz", ".tar"):
                continue
            if low == name.lower() or low.startswith(name.lower()):
                cands.append(p)
        if cands:
            exact = [p for p in cands if p.stem.lower() == name.lower()]
            return sorted(exact or cands, key=lambda p: len(p.parts))[0]
    return shutil.which(name)

def steamcmd_url():
    return STEAMCMD_URLS.get(sys.platform, STEAMCMD_URLS["linux"])

def _safe_extract(tar, dest):
    dest = Path(dest).resolve()
    for member in tar.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            continue
        target = (dest / member.name).resolve()
        if target != dest and dest not in target.parents:
            continue
        tar.extract(member, dest)

def install_steamcmd():
    dest = TOOLS_DIR / "steamcmd"
    dest.mkdir(parents=True, exist_ok=True)
    url = steamcmd_url()
    tmp = dest / Path(url).name
    try:
        data = http_get(url, timeout=600)
        tmp.write_bytes(data)
        if url.endswith(".tar.gz"):
            with tarfile.open(tmp, "r:gz") as t:
                try:
                    t.extractall(dest, filter="data")
                except TypeError:
                    _safe_extract(t, dest)
        else:
            with zipfile.ZipFile(tmp) as z:
                z.extractall(dest)
        tmp.unlink(missing_ok=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    prefer = "steamcmd.exe" if sys.platform == "win32" else "steamcmd.sh"
    cands = [p for p in dest.rglob("*") if p.is_file() and
             p.name.lower() in ("steamcmd.sh", "steamcmd", "steamcmd.exe")]
    if not cands:
        return {"ok": False, "error": "steamcmd binary missing from archive", "dir": str(dest)}
    exe = sorted(cands, key=lambda p: (p.name != prefer, len(p.parts)))[0]
    try:
        os.chmod(exe, 0o755)
    except OSError:
        pass
    return {"ok": True, "tool": str(exe), "source": "valve", "bytes": len(data)}

def probe_app(appid, branch="public", appinfo_file=None):
    if appinfo_file:
        f = Path(appinfo_file)
        if not f.exists():
            return {"ok": False, "error": f"no such file: {f}"}
        return probe_from_text(appid, f.read_text(encoding="utf-8", errors="replace"), branch)
    tool = _find_tool("steamcmd") or shutil.which("steamcmd")
    if not tool:
        got = install_steamcmd()
        tool = got.get("tool") if got.get("ok") else None
    if not tool:        return {"ok": False, "error": "steamcmd unavailable and the automatic download failed"}
    args = [str(tool), "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
            "+login", "anonymous", "+app_info_print", str(appid), "+quit"]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pr = subprocess.run(args, capture_output=True, text=True, errors="replace",
                            timeout=180, input="", cwd=str(CACHE_DIR))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "steamcmd timed out after 180s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    out = (pr.stdout or "") + "\n" + (pr.stderr or "")
    res = probe_from_text(appid, out, branch)
    if not res.get("ok"):
        res["tail"] = out[-400:]
    return res

def rows_to_csv(rows):
    lines = ["# depot,os,manifest,size"]
    for r in rows:
        lines.append(f"{r['depot']},{r['os']},{r['manifest']},{r['size']}")
    return "\n".join(lines) + "\n"

# ---------- selftest ----------

FIXTURE = '''"480"
{
\t"common"
\t{
\t\t"name" "Spacewar"
\t\t"oslist" "windows,macos"
\t}
\t"depots"
\t{
\t\t"branches"
\t\t{
\t\t\t"public"
\t\t\t{
\t\t\t\t"buildid" "991122"
\t\t\t}
\t\t}
\t\t"481"
\t\t{
\t\t\t"manifests"
\t\t\t{
\t\t\t\t"public"
\t\t\t\t{
\t\t\t\t\t"gid" "7301047157666712084"
\t\t\t\t}
\t\t\t}
\t\t\t"config"
\t\t\t{
\t\t\t\t"oslist" "windows,macos"
\t\t\t}
\t\t}
\t\t"482"
\t\t{
\t\t\t"manifests"
\t\t\t{
\t\t\t\t"public"
\t\t\t\t{
\t\t\t\t\t"gid" "7193846755746789123"
\t\t\t\t}
\t\t\t}
\t\t\t"config"
\t\t\t{
\t\t\t\t"oslist" "windows"
\t\t\t}
\t\t}
\t\t"483"
\t\t{
\t\t\t"manifests"
\t\t\t{
\t\t\t\t"public"
\t\t\t\t{
\t\t\t\t\t"gid" "7193846755746789999"
\t\t\t\t}
\t\t\t}
\t\t\t"config"
\t\t\t{
\t\t\t\t"oslist" "macos"
\t\t\t}
\t\t}
\t\t"480"
\t\t{
\t\t\t"depotfromapp" "480"
\t\t}
\t}
}
'''

def selftest():
    checks = []
    failed = []

    def check(name, cond, detail=""):
        if not cond:
            failed.append(name)
            print(json.dumps({"ok": False, "check": name, "detail": detail}))
            return False
        checks.append(name)
        return True

    res = probe_from_text(480, FIXTURE, "public")
    if not check("probe parses appinfo", res.get("ok") is True, json.dumps(res)):
        return 1
    check("probe buildid", res.get("buildid") == "991122", str(res.get("buildid")))
    check("probe row count", res.get("count") == 4, str(res.get("count")))
    mac_rows = select_rows(res["rows"], "mac")
    check("mac selection drops win-only depot",
          {r["depot"] for r in mac_rows} == {"480", "481", "483"},
          json.dumps([r["depot"] for r in mac_rows]))
    lua, files = gen_lua(480, mac_rows, "public", pin=True, buildid=res["buildid"])
    check("lua app lines", "addappid(480, 1)" in lua, lua)
    check("lua depot lines", "addappid(481, 0)" in lua, lua)
    check("lua pinned manifest",
          'setManifestid(481, "7301047157666712084")' in lua, lua)
    check("lua win depot absent", "addappid(482, 0)" not in lua, lua)
    check("manifest file names",
          "481_7301047157666712084.manifest" in files, json.dumps(files))
    lua2, _ = gen_lua(480, mac_rows, "public", pin=False)
    check("no-pin comments manifests",
          '-- setManifestid(481, "7301047157666712084")' in lua2, lua2)

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        ensure_lua_path(tdir)
        body = (tdir / TOML_NAME).read_text(encoding="utf-8")
        check("toml written", STEM in body, body)
        ensure_lua_path(tdir)
        body2 = (tdir / TOML_NAME).read_text(encoding="utf-8")
        check("toml idempotent", body2.count("config/" + STEM) == 1, body2)
        globals()["VAULT_DIR"] = tdir / "vault"
        vault_write(999999, "public", lua)
        v = vault_list(999999)
        check("vault roundtrip", "public" in v.get("branches", []), json.dumps(v))
        globals()["VAULT_DIR"] = VAULT_DIR

    key = bytes(range(32))
    block = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    check("aes-256 block decrypt", _pure_ecb_decrypt_nopad(key, block) == plain)
    check("aes-256 backend decrypt", _aes_ecb_decrypt_nopad(key, block) == plain)
    iv = bytes(range(16))
    check("aes-256 cbc chain", _aes_cbc_decrypt(key, iv, block) is not None)

    payload = b"licensetools chunk probe " * 400
    raw_filter = {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20,
                  "lc": 3, "lp": 0, "pb": 2}
    packer = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=[raw_filter])
    stream = packer.compress(payload) + packer.flush()
    props = b"\x5d" + struct.pack("<I", 1 << 20)
    frame = (b"VZa" + b"\x00\x00\x00\x00" + props + stream +
             struct.pack("<II", zlib.crc32(payload) & 0xFFFFFFFF, len(payload)) + b"zv")
    try:
        back = _vzip_decompress(frame)
    except Exception as exc:
        back = b"<" + str(exc).encode() + b">"
    check("vzip frame decode", back == payload, f"{len(back)} bytes back")
    check("cdn host list", bool(cdn_hosts()))
    check("manifest path flatten",
          _safe_rel("Contents\\MacOS\\StardewValley") == "Contents/MacOS/StardewValley")
    check("manifest path traversal refused", _safe_rel("../../etc/passwd") is None)
    check("installdir rejects traversal", clean_installdir("../../Documents") is None)
    check("installdir rejects nesting", clean_installdir("a/b") is None)
    check("installdir accepts a name", clean_installdir("Stardew Valley") == "Stardew Valley")

    with tempfile.TemporaryDirectory() as td:
        inside = Path(td) / "game"
        inside.mkdir()
        check("containment inside", _contained(inside, inside / "a" / "b"))
        outside = Path(td) / "elsewhere"
        outside.mkdir()
        link = inside / "hop"
        try:
            os.symlink(outside, link)
            escaped = not _contained(inside, link / "x")
        except (OSError, NotImplementedError):
            escaped = True
        check("containment follows symlinks out", escaped)

    stub_src = _stub_source(["SteamAPI_ISteamApps_BIsSubscribedApp",
                             "SteamAPI_ISteamUser_GetSteamID",
                             "SteamAPI_ISteamApps_GetAppID",
                             "SteamAPI_SteamUser_v023"], 413150)
    check("stub exports init", "SteamAPI_Init(void)" in stub_src)
    check("stub emits matching symbol", "SteamAPI_ISteamApps_BIsSubscribedApp" in stub_src)
    check("stub carries the appid", "413150ULL" in stub_src)
    check("stub has one definition per symbol",
          stub_src.count("SteamAPI_Init(void)") == 1, stub_src.count("SteamAPI_Init(void)"))
    check("stub is brace balanced", stub_src.count("{") == stub_src.count("}"),
          f"{stub_src.count('{')}/{stub_src.count('}')}")

    native = _cc_crypt(_CC_OP_DECRYPT, _CC_OPT_ECB, key, b"\x00" * 16, block)
    check("native aes path agrees with the pure one",
          native is None or native == plain, repr(native))
    check("bundled stand-in lookup does not raise", _bundled_stub() is None
          or _bundled_stub().is_file())

    check("version compare is numeric",
          _vtuple("1.5.10") > _vtuple("1.5.9") and _vtuple("1.5.3") < _vtuple("2.0"),
          f"{_vtuple('1.5.10')} vs {_vtuple('1.5.9')}")
    fake_release = {"tag_name": "v9.9.9", "html_url": "https://example.invalid/rel",
                    "published_at": "2026-01-01T00:00:00Z", "body": "notes",
                    "assets": [{"name": "LuaToolsMac.dmg", "size": 10,
                                "browser_download_url": "https://example.invalid/a"},
                               {"name": "LicenseTools.dmg", "size": 20,
                                "browser_download_url": "https://example.invalid/b"}]}
    real_http = http_get
    globals()["http_get"] = lambda *a, **k: json.dumps(fake_release).encode("utf-8")
    up = app_update_check()
    check("update check sees a newer tag",
          up.get("ok") is True and up.get("newer") is True and up.get("latest") == "9.9.9",
          json.dumps(up))
    check("update check prefers the licensetools dmg",
          (up.get("asset") or {}).get("name") == "LicenseTools.dmg",
          json.dumps(up.get("asset")))
    globals()["http_get"] = lambda *a, **k: json.dumps(
        dict(fake_release, tag_name="v" + APP_VERSION)).encode("utf-8")
    same = app_update_check()
    check("same version is not an update",
          same.get("ok") is True and same.get("newer") is False, json.dumps(same))
    globals()["http_get"] = real_http

    from_source = app_update_apply()
    check("self update refuses to run from source",
          from_source.get("ok") is False and "source" in str(from_source.get("error")),
          json.dumps(from_source))
    swap = _swap_script("/tmp/new/LicenseTools.app", "/Applications/License Tools.app", 4242)
    check("swap waits for the running pid", 'kill -0 "$pid"' in swap and "pid=4242" in swap)
    check("swap quotes a path with a space",
          "'/Applications/License Tools.app'" in swap, swap.splitlines()[2])
    check("swap copies, reopens, and has a rollback",
          "ditto \"$new\" \"$dest\"" in swap and "open \"$dest\"" in swap
          and "mv \"$dest.old\" \"$dest\"" in swap)
    check("gui has the app update button", 'id="b-app-update"' in GUI_HTML)
    check("gui has the header update chip", 'id="h-new"' in GUI_HTML)
    check("gui calls the update entry points",
          'call("app_update"' in GUI_HTML and 'call("app_update_install"' in GUI_HTML)
    check("gui disables the update buttons while busy",
          '"#b-app-update","#h-new"' in GUI_HTML)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _fake_app(name, bundle_id):
            p = root / name
            (p / "Contents").mkdir(parents=True)
            with open(p / "Contents" / "Info.plist", "wb") as fh:
                plistlib.dump({"CFBundleIdentifier": bundle_id}, fh)
            return p

        other = _fake_app("Other.app", "com.example.other")
        ours = _fake_app("LicenseTools.app", "com.whoismelv.licensetools")
        check("bundle id reads back",
              _bundle_id(ours) == "com.whoismelv.licensetools", _bundle_id(ours))
        check("pick prefers the matching name",
              _pick_app([other, ours], "LicenseTools.app").name == "LicenseTools.app")
        check("pick falls back to the bundle id",
              _pick_app([other, ours], "Renamed.app",
                        "com.whoismelv.licensetools").name == "LicenseTools.app")
        check("pick takes the only app", _pick_app([other]).name == "Other.app")
        check("pick survives an empty dmg", _pick_app([]) is None)

    print(json.dumps({"ok": not failed, "checks": checks,
                      "failed": failed}, indent=2))
    if failed:
        print("selftest failed")
        return 1
    print("selftest ok")
    return 0

# ---------- DepotDownloader provisioning + content fetch ----------

DD_ERRORS = (
    ("is not available from this account", "not_owned",
     "account does not own the app - sign in with an account that does"),
    ("no subscription", "not_owned", "account has no subscription for this app"),
    ("content is encrypted", "depot_key", "depot needs a depot key - pass it via --arg -depotkey KEY"),
    ("invalid password", "bad_login", "steam rejected the password"),
    ("login failure", "bad_login", "steam rejected the login - check the account"),
    ("no manifest", "no_manifest", "no manifest gid for this depot - probe again"),
)

def classify_dd(text):
    low = str(text or "").lower()
    for needle, code, hint in DD_ERRORS:
        if needle in low:
            return code, hint
    return None, None

def gh_latest_asset(repo, want_name, token=""):
    url = "https://api.github.com/repos/" + repo + "/releases/latest"
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        data = json.loads(http_get(url, headers=h, timeout=30))
    except Exception as exc:
        return None, str(exc)
    for a in data.get("assets", []):
        if a.get("name") == want_name:
            return a, None
    return None, f"asset {want_name} not in latest release"

def dd_asset_name():
    import platform
    mach = platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "DepotDownloader-macos-arm64.zip" if arm else "DepotDownloader-macos-x64.zip"
    if sys.platform.startswith("linux"):
        return "DepotDownloader-linux-arm64.zip" if arm else "DepotDownloader-linux-x64.zip"
    return "DepotDownloader-windows-arm64.zip" if arm else "DepotDownloader-windows-x64.zip"

def install_depotdownloader(token=""):
    tool = _find_tool("DepotDownloader")
    if tool:
        return {"ok": True, "tool": str(tool), "source": "found"}
    want = dd_asset_name()
    dest = TOOLS_DIR / "dd"
    dest.mkdir(parents=True, exist_ok=True)
    zp = dest / want
    asset, err = gh_latest_asset("SteamRE/DepotDownloader", want, token)
    url = asset["browser_download_url"] if asset else (
        f"https://github.com/SteamRE/DepotDownloader/releases/latest/download/{want}")
    try:
        data = http_get(url, timeout=600)
        zp.write_bytes(data)
        with zipfile.ZipFile(zp) as z:
            z.extractall(dest)
        zp.unlink(missing_ok=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "asset": want, "url": url,
                "api_error": err}
    cands = [p for p in dest.rglob("*") if p.is_file() and
             p.name.lower().startswith("depotdownloader") and
             p.suffix.lower() not in (".pdb", ".xml", ".zip")]
    exact = [p for p in cands if p.stem.lower() == "depotdownloader"]
    if not (exact or cands):
        return {"ok": False, "error": "binary not found in archive", "dir": str(dest)}
    exe = sorted(exact or cands, key=lambda p: (len(p.parts), p.name != "DepotDownloader"))[0]
    if os.name != "nt":
        for p in dest.rglob("*"):
            if p.is_file():
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass
    return {"ok": True, "tool": str(exe), "asset": want, "bytes": len(data)}

def ensure_tools():
    out = {}
    sc = _find_tool("steamcmd")
    if sc:
        out["steamcmd"] = {"ok": True, "tool": str(sc), "source": "found"}
    else:
        out["steamcmd"] = install_steamcmd()
    dd = _find_tool("DepotDownloader")
    if dd:
        out["depotdownloader"] = {"ok": True, "tool": str(dd), "source": "found"}
    else:
        out["depotdownloader"] = install_depotdownloader(load_settings().get("auth_token", ""))
    return out

def fetch_game(appid, branch="public", rows=None, want_os="mac", shared=True,
               user="", password="", out_root=None, extra=None, appinfo_file=None,
               progress=None, flat=False):
    if rows is None:
        probe = probe_app(appid, branch, appinfo_file)
        if not probe.get("ok"):
            return {"ok": False, "stage": "probe", **probe}
        rows = select_rows(probe["rows"], want_os, shared)
    want = [r for r in rows if r["manifest"]]
    skipped = [r["depot"] for r in rows if not r["manifest"]]
    if not want:
        return {"ok": False, "error": f"no depots with manifests for os={want_os} branch={branch}"}
    tool = _find_tool("DepotDownloader")
    install_err = None
    if tool is None:
        got = install_depotdownloader(load_settings().get("auth_token", ""))
        tool = got.get("tool") if got.get("ok") else None
        if tool is None:
            install_err = got.get("error") or got.get("api_error")
    if tool is None:
        return {"ok": False, "error": "DepotDownloader missing - run: ensure",
                "detail": install_err}
    outroot = Path(out_root) if out_root else GAMES_DIR / str(appid)
    outroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if user and password:
        env["STEAM_USER"], env["STEAM_PASS"] = user, password
    results = []
    for r in want:
        if progress:
            progress(f"depot {r['depot']} - manifest {r['manifest']} - downloading...")
        ddir = outroot if flat else outroot / r["depot"]
        args = [str(tool), "-app", str(appid), "-depot", r["depot"],
                "-manifest", r["manifest"], "-dir", str(ddir)]
        if user and password:
            args += ["-username", user, "-password", password]
        else:
            args += ["-anonymous"]
        if extra:
            args += list(extra)
        try:
            pr = subprocess.run(args, capture_output=True, text=True, errors="replace",
                                timeout=3600, env=env, cwd=str(outroot))
            raw = (pr.stderr or "") + "\n" + (pr.stdout or "")
            tail = raw.strip()[-1200:]
            code, hint = classify_dd(tail)
            item = {"depot": r["depot"], "manifest": r["manifest"],
                    "exit": pr.returncode, "out": str(ddir), "tail": tail}
            if code:
                item["error_code"], item["hint"] = code, hint
            if pr.returncode != 0 and progress:
                lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                tail_lines = lines[-4:] or ["no output"]
                progress(f"depot {r['depot']} exit {pr.returncode} - "
                         + " | ".join(x[:160] for x in tail_lines))
            results.append(item)
        except Exception as exc:
            results.append({"depot": r["depot"], "error": str(exc),
                            "error_code": "run_failed", "hint": str(exc)})
            if progress:
                progress(f"depot {r['depot']} could not start - {exc}")
    ok = all(x.get("exit") == 0 for x in results if "exit" in x)
    failed = next((x for x in results if x.get("error_code")), None)
    res = {"ok": ok, "root": str(outroot), "results": results, "skipped_depots": skipped}
    if failed:
        res["error_code"] = failed["error_code"]
        res["hint"] = failed.get("hint")
    elif not ok:
        bad = next((x for x in results if x.get("exit") not in (0, None)), {})
        last = next((ln.strip() for ln in reversed((bad.get("tail") or "").splitlines())
                     if ln.strip()), "")
        res["error_code"] = "download_failed"
        res["hint"] = last or "DepotDownloader exited non-zero - check the depot lines in the log"
    if not ok:
        res["error"] = res.get("hint") or res.get("error_code") or "download failed"
    return res

# ---------- install downloaded content into the steam library ----------

def steam_apps_dir():
    for r in library_roots():
        sa = r / "steamapps"
        if sa.is_dir():
            return sa
    root = SteamCtx().root()
    if root is None:
        return None
    sa = root / "steamapps"
    sa.mkdir(parents=True, exist_ok=True)
    return sa

def all_steamapps_dirs():
    out = []
    for r in library_roots():
        sa = r / "steamapps"
        try:
            if sa.is_dir() and all(str(sa) != str(x) for x in out):
                out.append(sa)
        except OSError:
            continue
    if not out:
        first = steam_apps_dir()
        if first is not None:
            out.append(first)
    return out

def find_app_acf(appid):
    for sa in all_steamapps_dirs():
        f = sa / f"appmanifest_{int(appid)}.acf"
        try:
            if f.is_file():
                return f
        except OSError:
            continue
    return None

def clean_installdir(value):
    rel = _safe_rel(value)
    if not rel or "/" in rel or rel in (".", ".."):
        return None
    return rel

def read_appmanifest(appid):
    f = find_app_acf(appid)
    return parse_acf(f) if f is not None else None

def write_appmanifest(appid, name, installdir, buildid, depots, size_on_disk=0):
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    now = int(time.time())
    lines = ['"AppState"', "{"]
    lines += [f'\t"appid"\t\t"{int(appid)}"',
              '\t"Universe"\t\t"1"',
              f'\t"name"\t\t"{_vdf_quote(name or appid)}"',
              '\t"StateFlags"\t\t"4"',
              f'\t"installdir"\t\t"{_vdf_quote(installdir)}"',
              f'\t"LastUpdated"\t\t"{now}"',
              '\t"LastPlayed"\t\t"0"',
              f'\t"SizeOnDisk"\t\t"{int(size_on_disk)}"',
              f'\t"buildid"\t\t"{buildid}"']
    lines.append('\t"InstalledDepots"')
    lines.append("\t{")
    for d in depots:
        lines += [f'\t\t"{d["depot"]}"', "\t\t{",
                  f'\t\t\t"manifest"\t\t"{d["manifest"]}"',
                  f'\t\t\t"size"\t\t"{int(d.get("size") or 0)}"', "\t\t}"]
    lines.append("\t}")
    lines.append('\t"MountedDepots"')
    lines.append("\t{")
    for d in depots:
        lines.append(f'\t\t"{d["depot"]}"\t\t"{d["manifest"]}"')
    lines.append("\t}")
    lines.append('\t"SharedDepots"')
    lines.append("\t{")
    lines.append("\t}")
    lines.append("}")
    path = sa / f"appmanifest_{int(appid)}.acf"
    tmp = path.with_suffix(".acf.tmp")
    try:
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "file": str(path)}

MANIFEST_REPOS = (
    "Fairyvmos/bruh-hub",
    "Auiowu/ManifestAutoUpdate",
    "tymolu233/ManifestAutoUpdate",
    "luomojim/ManifestAutoUpdate",
    "MineRPG/ManifestAutoUpdate",
    "TOP-01/ManifestAutoUpdate",
    "hansaes/ManifestAutoUpdate",
    "bingyu50/ManifestAutoUpdate",
)
MANIFEST_MAGIC = b"\xd0\x17\xf6\x71"
_MANIFEST_HINT = {"repo": "", "appid": ""}

def depotcache_dir():
    sa = steam_apps_dir()
    if sa is None:
        return None
    d = sa / "depotcache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d

def _manifest_repos(appid):
    hint = _MANIFEST_HINT.get("repo")
    if hint and _MANIFEST_HINT.get("appid") == str(int(appid)) and hint in MANIFEST_REPOS:
        return (hint,) + tuple(r for r in MANIFEST_REPOS if r != hint)
    return MANIFEST_REPOS

def _list_branch_files(repo, appid):
    url = (f"https://api.github.com/repos/{repo}/contents/?ref={int(appid)}")
    blob, err = _http_bytes(url, timeout=60, accept="application/vnd.github+json")
    if err or not blob:
        return [], err or "empty"
    try:
        items = json.loads(blob.decode("utf-8", "replace"))
    except Exception as exc:
        return [], f"bad json {exc}"
    if not isinstance(items, list):
        return [], "unexpected listing"
    return [str(i.get("name") or "") for i in items if isinstance(i, dict)], ""

def _gid_from_name(name, depot):
    prefix = f"{int(depot)}_"
    if not name.startswith(prefix) or not name.endswith(".manifest"):
        return ""
    gid = name[len(prefix):-len(".manifest")]
    return gid if gid.isdigit() else ""

def _pb_varint(buf, i, end):
    shift = 0
    out = 0
    while i < end:
        byte = buf[i]
        i += 1
        out |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return out, i
    raise ValueError("truncated varint")

def manifest_info(path):
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if blob[:4] != MANIFEST_MAGIC:
        return {"ok": False, "error": "bad magic"}
    files = 0
    total = 0
    i = 8
    end = len(blob)
    try:
        while i < end:
            key, i = _pb_varint(blob, i, end)
            field, wire = key >> 3, key & 7
            if field != 1 or wire != 2:
                break
            length, i = _pb_varint(blob, i, end)
            if i + length > end:
                break
            node = blob[i:i + length]
            i += length
            files += 1
            j = 0
            while j < len(node):
                k2, j = _pb_varint(node, j, len(node))
                f2, w2 = k2 >> 3, k2 & 7
                if w2 == 0:
                    v2, j = _pb_varint(node, j, len(node))
                    if f2 == 2:
                        total += v2
                elif w2 == 2:
                    l2, j = _pb_varint(node, j, len(node))
                    j += l2
                elif w2 == 5:
                    j += 4
                elif w2 == 1:
                    j += 8
                else:
                    break
    except (ValueError, IndexError):
        pass
    return {"ok": files > 0, "files": files, "bytes": total}

def fetch_manifest(appid, depot, gid, progress=None):
    cache = depotcache_dir()
    if cache is None:
        return None, gid, "steam library not found"
    dest = cache / f"{int(depot)}_{gid}.manifest"
    try:
        if dest.is_file() and dest.stat().st_size > 1000:
            with dest.open("rb") as fh:
                if fh.read(4) == MANIFEST_MAGIC:
                    return dest, gid, ""
    except OSError:
        pass
    errors = []
    repos = _manifest_repos(appid)
    for repo in repos:
        url = (f"https://raw.githubusercontent.com/{repo}/{int(appid)}/"
               f"{int(depot)}_{gid}.manifest")
        blob, err = _http_bytes(url, timeout=120)
        if err:
            errors.append(f"{repo.split('/')[0]}: {err}")
            continue
        if len(blob) < 1000 or blob[:4] != MANIFEST_MAGIC:
            errors.append(f"{repo.split('/')[0]}: bad payload {len(blob)}B")
            continue
        try:
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(blob)
            tmp.replace(dest)
        except OSError as exc:
            errors.append(f"{repo.split('/')[0]}: write {exc}")
            continue
        _MANIFEST_HINT.update({"repo": repo, "appid": str(int(appid))})
        if progress:
            progress(f"manifest {depot}: {len(blob) // 1024} KB from {repo}")
        return dest, gid, ""
    for repo in repos[:3]:
        names, lerr = _list_branch_files(repo, appid)
        if lerr:
            errors.append(f"{repo.split('/')[0]} list: {lerr}")
            continue
        for name in names:
            alt = _gid_from_name(name, depot)
            if not alt or alt == gid:
                continue
            url = (f"https://raw.githubusercontent.com/{repo}/{int(appid)}/{name}")
            blob, err = _http_bytes(url, timeout=120)
            if err or len(blob) < 1000 or blob[:4] != MANIFEST_MAGIC:
                continue
            dest = cache / name
            try:
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(blob)
                tmp.replace(dest)
            except OSError:
                continue
            _MANIFEST_HINT.update({"repo": repo, "appid": str(int(appid))})
            if progress:
                progress(f"manifest {depot}: using {alt} from {repo}")
            return dest, alt, ""
    return None, gid, " | ".join(errors[-2:]) or "no mirror carried this manifest"

def stage_manifests(appid, rows, progress=None):
    staged, missing, errors = [], [], []
    for r in rows:
        depot = int(r["depot"])
        gid = str(r.get("manifest") or "")
        path, used_gid, err = fetch_manifest(appid, depot, gid or "0", progress)
        if path is None:
            missing.append(depot)
            errors.append(f"{depot}: {err}")
            continue
        if used_gid and used_gid != gid:
            r["manifest"] = used_gid
        info = manifest_info(path)
        if not info.get("ok"):
            errors.append(f"{depot}: manifest unreadable ({info.get('error')})")
        elif progress:
            progress(f"manifest {depot}: {info['files']} files, "
                     f"{info['bytes'] // (1024 * 1024)} MB")
            size = str(r.get("size") or "")
            if size.isdigit() and int(size) > 0:
                drift = abs(info["bytes"] - int(size)) / float(int(size))
                if drift > 0.02:
                    progress(f"manifest {depot}: size {info['bytes']} vs appinfo {size}")
        staged.append(depot)
    return {"ok": bool(staged), "staged": staged, "missing": missing,
            "errors": errors}

# ---------- steam cdn chunk pipeline (no account, no request code) ----------

CDN_SERVERS_URL = ("https://api.steampowered.com/IContentServerDirectoryService/"
                   "GetServersForSteamPipe/v1/?cell_id=0")
_FALLBACK_CDN = (
    "fastly.cdn.steampipe.steamcontent.com",
    "cloudflare.cdn.steampipe.steamcontent.com",
    "akamai.cdn.steampipe.steamcontent.com",
    "steampipe.steamcontent.com",
)
_CDN_HOSTS = []
_FLAG_DIR = 64
_FLAG_SYMLINK = 256

def _make_sbox():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        q &= 0xFF
        x = (q
             ^ (((q << 1) | (q >> 7)) & 0xFF)
             ^ (((q << 2) | (q >> 6)) & 0xFF)
             ^ (((q << 3) | (q >> 5)) & 0xFF)
             ^ (((q << 4) | (q >> 4)) & 0xFF))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox

def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p

_AES_SBOX = _make_sbox()
_AES_INV = [0] * 256
for _i, _v in enumerate(_AES_SBOX):
    _AES_INV[_v] = _i
_M9 = [_gmul(x, 9) for x in range(256)]
_M11 = [_gmul(x, 11) for x in range(256)]
_M13 = [_gmul(x, 13) for x in range(256)]
_M14 = [_gmul(x, 14) for x in range(256)]

def _aes256_expand(key):
    if len(key) != 32:
        raise ValueError("aes-256 needs a 32 byte key")
    nk, nr = 8, 14
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_AES_SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = (rcon << 1) ^ (0x1B if rcon & 0x80 else 0)
            rcon &= 0xFF
        elif i % nk == 4:
            temp = [_AES_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(nr + 1)]

_INV_SHIFT = [((i % 4) + 4 * (((i // 4) - (i % 4)) % 4)) for i in range(16)]

def _aes_decrypt_block(rk, block):
    s = [block[i] ^ rk[14][i] for i in range(16)]
    for rnd in range(13, 0, -1):
        s = [s[_INV_SHIFT[i]] for i in range(16)]
        s = [_AES_INV[b] for b in s]
        s = [s[i] ^ rk[rnd][i] for i in range(16)]
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c:4 * c + 4]
            out[4 * c + 0] = _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3]
            out[4 * c + 1] = _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3]
            out[4 * c + 2] = _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3]
            out[4 * c + 3] = _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]
        s = out
    s = [s[_INV_SHIFT[i]] for i in range(16)]
    s = [_AES_INV[b] for b in s]
    return bytes(s[i] ^ rk[0][i] for i in range(16))

def _pure_cbc_decrypt(key, iv, data):
    rk = _aes256_expand(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data) - len(data) % 16, 16):
        block = data[off:off + 16]
        plain = _aes_decrypt_block(rk, block)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = block
    return bytes(out)

def _pure_ecb_decrypt_nopad(key, data):
    rk = _aes256_expand(key)
    out = bytearray()
    for off in range(0, len(data) - len(data) % 16, 16):
        out += _aes_decrypt_block(rk, data[off:off + 16])
    return bytes(out)

_CC = {"tried": False, "lib": None}
_CC_OP_DECRYPT = 1
_CC_ALG_AES128 = 0
_CC_OPT_PKCS7 = 0x0001
_CC_OPT_ECB = 0x0002

def _common_crypto():
    # CCCrypt(CCOperation, CCAlgorithm, CCOptions, const void *key, size_t keyLength,
    #         const void *iv, const void *dataIn, size_t dataInLength,
    #         void *dataOut, size_t dataOutAvailable, size_t *dataOutMoved)
    # exactly eleven parameters - one extra entry here shifts every register on arm64.
    if _CC["tried"]:
        return _CC["lib"]
    _CC["tried"] = True
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        path = ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib"
        lib = ctypes.CDLL(path)
        fn = lib.CCCrypt
        fn.restype = ctypes.c_int32
        fn.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_char_p, ctypes.c_size_t,
                       ctypes.c_char_p,
                       ctypes.c_char_p, ctypes.c_size_t,
                       ctypes.c_void_p, ctypes.c_size_t,
                       ctypes.POINTER(ctypes.c_size_t)]
        _CC["lib"] = fn
    except Exception:
        _CC["lib"] = None
    return _CC["lib"]

def _cc_crypt(op, options, key, iv, data):
    fn = _common_crypto()
    if fn is None:
        return None
    import ctypes
    key = bytes(key)
    iv = bytes(iv)[:16]
    if len(iv) != 16:
        return None
    data = bytes(data)
    if not data:
        return b""
    if len(key) not in (16, 24, 32):
        return None
    out = ctypes.create_string_buffer(len(data) + 32)
    moved = ctypes.c_size_t(0)
    try:
        status = fn(op, _CC_ALG_AES128, options, key, len(key), iv,
                    data, len(data),
                    ctypes.cast(out, ctypes.c_void_p), ctypes.c_size_t(len(out)),
                    ctypes.byref(moved))
    except Exception:
        _CC["lib"] = None
        return None
    if status != 0:
        return None
    if moved.value > len(data) + 32:
        return None
    got = out.raw[:moved.value]
    if _CC.get("verified", "unset") == "unset":
        _CC["verified"] = "probing"
        try:
            if options & _CC_OPT_ECB:
                probe = _pure_ecb_decrypt_nopad(bytes(range(32)),
                                                bytes.fromhex("8ea2b7ca516745bfeafc49904b496089"))
            else:
                probe = _pure_cbc_decrypt(bytes(range(32)), b"\x00" * 16,
                                          bytes.fromhex("8ea2b7ca516745bfeafc49904b496089"))
            check = _cc_crypt(_CC_OP_DECRYPT, options, bytes(range(32)),
                              b"\x00" * 16,
                              bytes.fromhex("8ea2b7ca516745bfeafc49904b496089"))
            _CC["verified"] = bool(check is not None and check == probe)
        except Exception:
            _CC["verified"] = False
        if _CC["verified"] is not True:
            _CC["lib"] = None
            return None
    elif _CC["verified"] is False:
        return None
    return got

def _aes_cbc_decrypt(key, iv, data):
    if len(data) % 16:
        data = data[:len(data) - len(data) % 16]
    res = _cc_crypt(_CC_OP_DECRYPT, _CC_OPT_PKCS7, key, iv, data)
    if res is not None:
        return res
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except Exception:
        pass
    return _pure_cbc_decrypt(key, iv, data)

def _aes_ecb_decrypt_nopad(key, data):
    res = _cc_crypt(_CC_OP_DECRYPT, _CC_OPT_ECB, key, b"\x00" * 16, data)
    if res is not None:
        return res
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB).decrypt(data)
    except Exception:
        pass
    return _pure_ecb_decrypt_nopad(key, data)

def cdn_hosts(refresh=False):
    global _CDN_HOSTS
    if _CDN_HOSTS and not refresh:
        return list(_CDN_HOSTS)
    hosts = []
    try:
        blob, err = _http_bytes(CDN_SERVERS_URL, timeout=60)
        if not err and blob:
            data = json.loads(blob.decode("utf-8", "replace"))
            for item in (data.get("response") or {}).get("servers") or []:
                host = str((item or {}).get("host") or "").strip()
                if host:
                    hosts.append(host)
    except Exception:
        hosts = []
    if not hosts:
        hosts = list(_FALLBACK_CDN)
    seen, out = set(), []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            out.append(host)
    _CDN_HOSTS = out
    return list(out)

def _vzip_decompress(buf):
    if buf[:3] != b"VZa" or buf[-2:] != b"zv":
        raise ValueError("not a vzip frame")
    crc, size = struct.unpack("<II", buf[-10:-2])
    props = buf[7:12]
    head = props[0]
    lc = head % 9
    rest = head // 9
    lp = rest % 5
    pb = rest // 5
    dict_size = struct.unpack("<I", props[1:5])[0]
    dec = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": max(dict_size, 4096),
                  "lc": lc, "lp": lp, "pb": pb}])
    out = dec.decompress(buf[12:-10], max_length=size)
    if len(out) != size:
        raise ValueError(f"vzip short decode {len(out)}/{size}")
    if zlib.crc32(out) & 0xFFFFFFFF != crc:
        raise ValueError("vzip crc mismatch")
    return out

def _vzstd_decompress(buf):
    if buf[:4] != b"VSZa" or buf[-3:] != b"zsv":
        raise ValueError("not a vzstd frame")
    size = struct.unpack("<i", buf[-11:-7])[0]
    payload = buf[8:-15]
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(payload, max_output_size=size)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util
        name = ctypes.util.find_library("zstd")
        lib = ctypes.CDLL(name) if name else None
        if lib is None:
            raise OSError("no libzstd")
        lib.ZSTD_decompress.restype = ctypes.c_size_t
        lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.c_char_p, ctypes.c_size_t]
        lib.ZSTD_isError.restype = ctypes.c_uint
        lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        out = ctypes.create_string_buffer(max(size, 1))
        got = lib.ZSTD_decompress(ctypes.cast(out, ctypes.c_void_p),
                                  ctypes.c_size_t(size),
                                  payload, ctypes.c_size_t(len(payload)))
        if lib.ZSTD_isError(got) or got > size:
            raise OSError("zstd failed")
        return out.raw[:got]
    except Exception as exc:
        raise ValueError(f"zstd payload needs libzstd ({exc})")

def _zip_decompress(buf):
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("empty zip chunk")
        return zf.read(names[0])

def chunk_plaintext(blob, key, expect=None):
    if len(blob) < 32:
        raise ValueError(f"chunk too small ({len(blob)} bytes)")
    iv = _aes_ecb_decrypt_nopad(key, blob[:16])
    plain = _aes_cbc_decrypt(key, iv, blob[16:])
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    if plain[:4] == b"VSZa":
        plain = _vzstd_decompress(plain)
    elif plain[:3] == b"VZa":
        plain = _vzip_decompress(plain)
    elif plain[:4] == b"PK\x03\x04":
        plain = _zip_decompress(plain)
    if expect is not None and len(plain) != expect:
        raise ValueError(f"chunk size {len(plain)} != {expect}")
    return plain

def parse_manifest(path):
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if blob[:4] != MANIFEST_MAGIC:
        return {"ok": False, "error": "bad manifest magic"}
    files = []
    total = 0
    chunk_count = 0
    i = 8
    end = len(blob)
    try:
        while i < end:
            key, i = _pb_varint(blob, i, end)
            field, wire = key >> 3, key & 7
            if field != 1 or wire != 2:
                break
            length, i = _pb_varint(blob, i, end)
            if i + length > end:
                break
            body = blob[i:i + length]
            i += length
            entry = {"name": "", "size": 0, "flags": 0, "sha": b"",
                     "link": "", "chunks": []}
            j = 0
            while j < len(body):
                key2, j = _pb_varint(body, j, len(body))
                f2, w2 = key2 >> 3, key2 & 7
                if w2 == 0:
                    val, j = _pb_varint(body, j, len(body))
                    if f2 == 2:
                        entry["size"] = val
                    elif f2 == 3:
                        entry["flags"] = val
                elif w2 == 2:
                    ln, j = _pb_varint(body, j, len(body))
                    node = body[j:j + ln]
                    j += ln
                    if f2 == 1:
                        entry["name"] = node.decode("utf-8", "replace")
                    elif f2 == 5:
                        entry["sha"] = node
                    elif f2 == 7:
                        entry["link"] = node.decode("utf-8", "replace")
                    elif f2 == 6:
                        ch = {"id": b"", "crc": 0, "off": 0, "ulen": 0, "clen": 0}
                        k2 = 0
                        while k2 < len(node):
                            key3, k2 = _pb_varint(node, k2, len(node))
                            f3, w3 = key3 >> 3, key3 & 7
                            if w3 == 0:
                                v3, k2 = _pb_varint(node, k2, len(node))
                                if f3 == 3:
                                    ch["off"] = v3
                                elif f3 == 4:
                                    ch["ulen"] = v3
                                elif f3 == 5:
                                    ch["clen"] = v3
                            elif w3 == 2:
                                l3, k2 = _pb_varint(node, k2, len(node))
                                if f3 == 1:
                                    ch["id"] = node[k2:k2 + l3]
                                k2 += l3
                            elif w3 == 5:
                                if f3 == 2:
                                    ch["crc"] = struct.unpack("<I", node[k2:k2 + 4])[0]
                                k2 += 4
                            elif w3 == 1:
                                k2 += 8
                            else:
                                break
                        entry["chunks"].append(ch)
                        chunk_count += 1
                elif w2 == 5:
                    j += 4
                elif w2 == 1:
                    j += 8
                else:
                    break
            total += entry["size"]
            files.append(entry)
    except (ValueError, IndexError):
        pass
    if not files:
        return {"ok": False, "error": "no file entries in manifest"}
    named = sum(1 for f in files if f["name"])
    if named < len(files) // 2:
        return {"ok": False, "error": "manifest filenames are encrypted - cannot map paths"}
    return {"ok": True, "files": files, "bytes": total,
            "chunks": chunk_count, "count": len(files)}

def _safe_rel(name):
    parts = []
    for piece in str(name or "").replace("\\", "/").split("/"):
        if piece in ("", ".", ".."):
            if piece == "..":
                return None
            continue
        parts.append(piece)
    return "/".join(parts) if parts else None

def _chunk_cache_dir(depot):
    root = Path.home() / ".licensetools" / "chunks" / str(int(depot))
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        return None

def _prune_chunks(limit=6 * 1024 ** 3, keep_depot=None):
    base = Path.home() / ".licensetools" / "chunks"
    if not base.is_dir():
        return 0
    rows = []
    total = 0
    try:
        for depot_dir in base.iterdir():
            if not depot_dir.is_dir():
                continue
            for f in depot_dir.iterdir():
                try:
                    st = f.stat()
                except OSError:
                    continue
                if not f.is_file():
                    continue
                rows.append((st.st_mtime, st.st_size, f))
                total += st.st_size
    except OSError:
        return 0
    if total <= limit:
        return 0
    rows.sort()
    removed = 0
    for _mtime, size, f in rows:
        if total <= limit:
            break
        if keep_depot is not None and f.parent.name == str(int(keep_depot)):
            continue
        try:
            f.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    return removed

_STICKY_HOST = {"host": ""}

def _host_order(hosts):
    hosts = list(hosts or cdn_hosts())
    sticky = _STICKY_HOST["host"]
    if sticky and sticky in hosts:
        hosts.remove(sticky)
        hosts.insert(0, sticky)
    out, seen = [], set()
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out[:12]

def fetch_chunk(depot, chunk_id, expect_len, cache, hosts=None):
    sha = chunk_id.hex()
    dest = cache / sha if cache is not None else None
    if dest is not None and dest.is_file():
        try:
            if dest.stat().st_size == expect_len:
                return dest, ""
        except OSError:
            pass
    errors = []
    for host in _host_order(hosts):
        url = f"https://{host}/depot/{int(depot)}/chunk/{sha}"
        blob, err = _http_bytes(url, timeout=120, accept="*/*")
        if err:
            errors.append(f"{host.split('.')[0]}: {err}")
            continue
        if not blob:
            errors.append(f"{host.split('.')[0]}: empty reply")
            continue
        if expect_len and len(blob) != expect_len:
            errors.append(f"{host.split('.')[0]}: {len(blob)}/{expect_len} bytes")
            continue
        _STICKY_HOST["host"] = host
        if dest is None:
            return blob, ""
        try:
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(blob)
            tmp.replace(dest)
        except OSError as exc:
            errors.append(f"write {exc}")
            continue
        return dest, ""
    return None, " | ".join(errors[-2:]) or "no cdn host answered"

def _contained(root, path):
    try:
        r = Path(root).resolve()
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return p == r or r in p.parents

def _sha1_file(path, block=1 << 20):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            while True:
                buf = fh.read(block)
                if not buf:
                    break
                h.update(buf)
    except OSError:
        return ""
    return h.hexdigest()

def download_depot(depot, manifest_path, target_root, key, progress=None,
                   workers=6, verify_existing=True):
    parsed = parse_manifest(manifest_path)
    if not parsed.get("ok"):
        return {"ok": False, "depot": int(depot), "error": parsed.get("error")}
    if isinstance(key, str):
        try:
            key = bytes.fromhex(key.strip())
        except ValueError:
            key = b""
    if not key or len(key) != 32:
        return {"ok": False, "depot": int(depot),
                "error": "no 32 byte depot key - cannot decrypt"}
    root = Path(target_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "depot": int(depot), "error": str(exc)}
    cache = _chunk_cache_dir(depot)
    hosts = cdn_hosts()
    todo = []
    skipped = 0
    for entry in parsed["files"]:
        rel = _safe_rel(entry["name"])
        if rel is None:
            continue
        dest = root / rel
        if entry["flags"] & _FLAG_DIR:
            try:
                dest.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            continue
        if entry["flags"] & _FLAG_SYMLINK:
            link = str(entry.get("link") or "").replace("\\", "/")
            if not link or link.startswith("/") or ".." in link.split("/"):
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                os.symlink(link, dest)
            except OSError:
                pass
            continue
        if not _contained(root, dest.parent):
            continue
        if verify_existing and dest.is_file() and dest.stat().st_size == entry["size"]:
            want = entry["sha"].hex() if entry["sha"] else ""
            if not want or _sha1_file(dest) == want:
                skipped += 1
                continue
        todo.append((entry, dest))
    unique = {}
    for entry, _dest in todo:
        for ch in entry["chunks"]:
            if ch["id"] and ch["id"] not in unique:
                unique[ch["id"]] = ch["clen"]
    total = len(unique)
    if progress:
        progress(f"depot {int(depot)}: {parsed['count']} files, "
                 f"{parsed['bytes'] // (1024 * 1024)} MB, "
                 f"{total} chunk(s) to pull, {skipped} file(s) already good")
    if not total and not todo:
        return {"ok": True, "depot": int(depot), "files": 0,
                "bytes": 0, "skipped": skipped, "chunks": 0}
    done = {"n": 0, "bytes": 0}
    lock = threading.Lock()
    failures = []

    def pull(item):
        chunk_id, clen = item
        path, err = fetch_chunk(depot, chunk_id, clen, cache, hosts)
        with lock:
            done["n"] += 1
            if path is None:
                failures.append(f"{chunk_id.hex()[:12]}: {err}")
            else:
                done["bytes"] += clen
            if progress and (done["n"] % 100 == 0 or done["n"] == total):
                progress(f"depot {int(depot)}: {done['n']}/{total} chunks, "
                         f"{done['bytes'] // (1024 * 1024)} MB")

    if total:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            list(pool.map(pull, list(unique.items())))
    if failures:
        return {"ok": False, "depot": int(depot),
                "error": f"{len(failures)} chunk(s) failed",
                "detail": failures[:3]}
    written = 0
    hashed = 0
    mismatch = []
    for entry, dest in todo:
        if not _contained(root, dest.parent):
            return {"ok": False, "depot": int(depot),
                    "error": f"{entry['name']}: path escapes the install folder"}
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        digest = hashlib.sha1()
        try:
            with dest.open("wb") as fh:
                for ch in sorted(entry["chunks"], key=lambda c: c["off"]):
                    src = cache / ch["id"].hex() if cache is not None else None
                    raw = src.read_bytes() if src is not None and src.is_file() else None
                    if raw is None:
                        raw, err = fetch_chunk(depot, ch["id"], ch["clen"], None, hosts)
                        if raw is None:
                            raise OSError(f"chunk lost: {err}")
                    data = chunk_plaintext(raw, key, ch["ulen"])
                    fh.seek(ch["off"])
                    fh.write(data)
                    digest.update(data)
                fh.truncate(entry["size"])
        except (OSError, ValueError) as exc:
            return {"ok": False, "depot": int(depot),
                    "error": f"{entry['name']}: {exc}"}
        written += 1
        got = digest.hexdigest()
        if not entry["sha"] or entry["size"] == 0:
            hashed += 1
        elif got == entry["sha"].hex():
            hashed += 1
        else:
            mismatch.append(entry["name"])
        rel = _safe_rel(entry["name"]) or ""
        if rel.startswith("Contents/MacOS/"):
            try:
                if _contained(root, dest):
                    os.chmod(dest, 0o755)
            except OSError:
                pass
    if progress:
        progress(f"depot {int(depot)}: {written} file(s) written, "
                 f"{hashed} hash-verified")
    if mismatch:
        return {"ok": False, "depot": int(depot),
                "error": f"{len(mismatch)} file(s) failed the sha1 check",
                "detail": mismatch[:5], "files": written, "skipped": skipped}
    return {"ok": True, "depot": int(depot), "files": written,
            "skipped": skipped, "chunks": total,
            "bytes": sum(int(e["size"]) for e, _d in todo),
            "verified": hashed}

def download_app(appid, rows, target_root, progress=None, keys=None,
                 verify_existing=True):
    keys = keys or {}
    total = {"files": 0, "bytes": 0, "skipped": 0}
    done = []
    pruned = _prune_chunks(keep_depot=int(rows[0]["depot"]) if rows else None)
    if pruned and progress:
        progress(f"chunk cache trimmed - {pruned} old chunk(s) removed")
    for r in rows:
        depot = int(r["depot"])
        key = keys.get(depot) or keys.get(str(depot)) or ""
        path = depotcache_dir() / f"{depot}_{r['manifest']}.manifest" if depotcache_dir() else None
        if path is None or not path.is_file():
            return {"ok": False, "stage": "manifest", "depot": depot,
                    "error": f"manifest for depot {depot} is not on disk"}
        res = download_depot(depot, path, target_root, key, progress,
                             verify_existing=verify_existing)
        if not res.get("ok"):
            return {"ok": False, "stage": "download", **res}
        total["files"] += res.get("files", 0)
        total["bytes"] += res.get("bytes", 0)
        total["skipped"] += res.get("skipped", 0)
        done.append(depot)
    return {"ok": True, "depots": done, **total}

def current_steamid():
    root = SteamCtx().root()
    if root is None:
        return "0"
    f = root / "config" / "loginusers.vdf"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "0"
    best = "0"
    for block in re.finditer(r'"(7656\d{13})"\s*\{([^}]*)\}', text):
        sid, body = block.group(1), block.group(2)
        if re.search(r'"MostRecent"\s*"1"', body):
            return sid
        best = sid
    return best

def _vdf_quote(value):
    return str(value if value is not None else "").replace("\\", "\\\\").replace('"', '\\"')

def write_pending_appmanifest(appid, name, installdir, buildid, rows, total, download):
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    now = int(time.time())
    total = int(total or 0)
    download = int(download or total or 0)
    lines = ['"AppState"', "{"]
    lines += [f'\t"appid"\t\t"{int(appid)}"',
              '\t"Universe"\t\t"1"',
              f'\t"name"\t\t"{_vdf_quote(name or appid)}"',
              '\t"StateFlags"\t\t"2"',
              f'\t"installdir"\t\t"{_vdf_quote(installdir)}"',
              f'\t"LastUpdated"\t\t"{now}"',
              '\t"LastPlayed"\t\t"0"',
              '\t"SizeOnDisk"\t\t"0"',
              '\t"StagingSize"\t\t"0"',
              f'\t"buildid"\t\t"{buildid}"',
              f'\t"LastOwner"\t\t"{current_steamid()}"',
              '\t"UpdateResult"\t\t"0"',
              f'\t"BytesToDownload"\t\t"{download}"',
              '\t"BytesDownloaded"\t\t"0"',
              f'\t"BytesToStage"\t\t"{total}"',
              '\t"BytesStaged"\t\t"0"',
              f'\t"TargetBuildID"\t\t"{buildid}"',
              '\t"AutoUpdateBehavior"\t\t"0"',
              '\t"AllowOtherDownloadsWhileRunning"\t\t"0"',
              '\t"ScheduledAutoUpdate"\t\t"0"',
              '\t"InstalledDepots"', "\t{"]
    for d in rows:
        lines += [f'\t\t"{int(d["depot"])}"', "\t\t{",
                  f'\t\t\t"manifest"\t\t"{d["manifest"]}"',
                  f'\t\t\t"size"\t\t"{int(d.get("size") or 0)}"', "\t\t}"]
    lines += ["\t}", '\t"SharedDepots"', "\t{", "\t}",
              '\t"UserConfig"', "\t{", "\t}",
              '\t"MountedConfig"', "\t{", "\t}", "}"]
    path = sa / f"appmanifest_{int(appid)}.acf"
    try:
        tmp = path.with_suffix(".acf.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "file": str(path)}

def install_to_library(appid, branch="public", want_os="mac", user="", password="",
                       progress=None, update=False, skip_drm=False):
    aid = int(appid)
    probe = probe_app(aid, branch)
    if not probe.get("ok"):
        return {"ok": False, "stage": "probe", **probe}
    rows = select_rows(probe["rows"], want_os, False)
    rows = [r for r in rows if r["manifest"]]
    if not rows:
        return {"ok": False, "error": f"no depot manifests for os={want_os}"}
    installdir = clean_installdir(probe.get("installdir") or probe.get("name") or aid)
    if installdir is None:
        return {"ok": False, "stage": "probe",
                "error": f"appinfo has an unusable installdir: {probe.get('installdir')!r}"}
    existing = find_app_acf(aid)
    sa = existing.parent if existing is not None else steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found - install Steam first"}
    target = sa / "common" / installdir
    depots = [int(r["depot"]) for r in rows]
    keys = keys_for_depots(depots, progress)
    cfg = macsteam_config_add(aid, depots=depots, keys=keys)
    missing = [d for d in depots if d not in keys]
    if progress:
        progress(f"loader config: {len(depots)} depot(s), {len(keys)} key(s)")
        if missing:
            progress("no depot key found for: " + ", ".join(str(d) for d in missing))
    absent = [d for d in depots if not (keys.get(d) or keys.get(str(d)))]
    if absent:
        return {"ok": False, "stage": "keys", "target": str(target),
                "error": "no depot key for " + ", ".join(str(d) for d in absent),
                "hint": "press Refresh depot keys, then Download files again",
                "config": cfg}
    if progress:
        progress("staging depot manifests so Steam never asks the CM for them")
    staged = stage_manifests(aid, rows, progress)
    if staged["missing"]:
        msg = ", ".join(str(d) for d in staged["missing"])
        if progress:
            progress("manifest missing for: " + msg)
        return {"ok": False, "stage": "manifest", "target": str(target),
                "error": f"no manifest on any mirror for depot(s) {msg}",
                "manifests": staged, "config": cfg}
    total = sum(int(r["size"]) for r in rows if str(r.get("size", "")).isdigit())
    download_size = sum(int(r["download"]) for r in rows
                        if str(r.get("download", "")).isdigit())
    pending = write_pending_appmanifest(aid, probe.get("name"), installdir,
                                        probe.get("buildid", ""), rows,
                                        total, download_size)
    if progress:
        progress("queued the library entry")
    if progress:
        progress("closing steam first so it cannot overwrite the new library record")
    quit_info = steam_quit()
    if progress:
        progress("steam: " + ("closed" if quit_info.get("ok")
                              else "still open - " + str(quit_info.get("error"))))
    if not quit_info.get("ok"):
        _drop_pending(pending)
        return {"ok": False, "stage": "steam", "target": str(target),
                "error": "Steam would not close, so the library record would be lost",
                "hint": "quit Steam from the menu bar, then press Download files again",
                "steam": quit_info}
    if progress:
        progress("pulling the depots from the Steam CDN myself - no account needed")
    res = download_app(aid, rows, target, progress, keys=keys, verify_existing=True)
    if not res.get("ok"):
        _drop_pending(pending)
        steam_launch()
        res["target"] = str(target)
        return res
    size = 0
    for p in target.rglob("*"):
        if p.is_file():
            try:
                size += p.stat().st_size
            except OSError:
                pass
    if size == 0:
        _drop_pending(pending)
        steam_launch()
        return {"ok": False, "stage": "download", "target": str(target),
                "error": "depot download wrote no files",
                "hint": "press Download files again"}
    acf = write_appmanifest(aid, probe.get("name"), installdir,
                            probe.get("buildid", ""), rows, size)
    if progress:
        progress(f"installed {size // (1024 * 1024)} MB into {target}")
        progress("library entry: " + str(acf.get("file") or acf.get("error")))
    drm = {"ok": False, "skipped": True}
    if not skip_drm and sys.platform == "darwin":
        if progress:
            progress("matching the steam library the game links against...")
        drm = drm_skip(target, aid, progress)
        if progress:
            if drm.get("needs_tools"):
                progress("drm layer: left untouched, no build tools on this mac")
            elif drm.get("ok") and not drm.get("skipped"):
                progress("drm layer: patched " + ", ".join(drm.get("patched") or []))
            elif drm.get("skipped"):
                progress("drm layer: no dylib in this game")
            else:
                progress("drm layer: " + str(drm.get("error") or "unavailable"))
    launch_info = steam_launch()
    if progress:
        progress("steam: " + ("opened - check your library" if launch_info.get("ok")
                              else "could not open - " + str(launch_info.get("error"))))
    return {"ok": bool(acf.get("ok")), "appid": str(aid), "mode": "local",
            "dir": str(target), "installdir": installdir, "files_bytes": size,
            "depots": depots, "keys": len(keys),
            "files": res.get("files", 0), "skipped": res.get("skipped", 0),
            "manifests": staged, "config": cfg, "drm": drm,
            "steam": {"quit": quit_info, "launch": launch_info},
            "manifest": acf.get("file"), "error": acf.get("error")}

def _drop_pending(pending):
    try:
        f = (pending or {}).get("file")
        if f and Path(f).is_file():
            Path(f).unlink()
    except OSError:
        pass

# ---------- steam api stand-in (the game's ownership check) ----------

STUB_NAME = "libsteam_api.dylib"
STUB_MARK = ".licensetools-stub"
STUB_APPID_FILE = "steam_appid.txt"

_STUB_ACCESSORS = [
    "SteamAPI_SteamUser_v023", "SteamAPI_SteamUser_v022", "SteamAPI_SteamUser_v021",
    "SteamAPI_SteamUser_v020", "SteamAPI_SteamApps_v008", "SteamAPI_SteamApps_v007",
    "SteamAPI_SteamFriends_v018", "SteamAPI_SteamFriends_v017",
    "SteamAPI_SteamUtils_v010", "SteamAPI_SteamUtils_v009",
    "SteamAPI_SteamRemoteStorage_v016", "SteamAPI_SteamRemoteStorage_v014",
    "SteamAPI_SteamUserStats_v012", "SteamAPI_SteamUserStats_v011",
    "SteamAPI_SteamMatchmaking_v009", "SteamAPI_SteamMatchmakingServers_v002",
    "SteamAPI_SteamNetworking_v006", "SteamAPI_SteamNetworkingSockets_v012",
    "SteamAPI_SteamNetworkingSockets_v009", "SteamAPI_SteamNetworkingUtils_v004",
    "SteamAPI_SteamNetworkingMessages_v002",
    "SteamAPI_SteamUGC_v021", "SteamAPI_SteamUGC_v020",
    "SteamAPI_SteamInput_v006", "SteamAPI_SteamInventory_v003",
    "SteamAPI_SteamGameServer_v015", "SteamAPI_SteamGameServer_v014",
    "SteamAPI_SteamGameServerStats_v001", "SteamAPI_SteamScreenshots_v003",
    "SteamAPI_SteamMusic_v001", "SteamAPI_SteamVideo_v004",
    "SteamAPI_SteamHTMLSurface_v005", "SteamAPI_SteamController_v008",
    "SteamAPI_SteamParties_v002", "SteamAPI_SteamTimeline_v002",
    "SteamAPI_SteamRemotePlay_v001", "SteamAPI_SteamParentalSettings_v001",
    "SteamAPI_SteamAppList_v001", "SteamAPI_SteamNetworkingSocketsSerialized_v005",
]

_STUB_CORE = r'''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>

#define VIS(v) __attribute__((visibility("default"))) v __attribute__((used))

static char g_vtbl[256 * 8] __attribute__((aligned(16)));
static uint64_t g_appid;

typedef uint64_t (*slot_fn)(void);
static uint64_t slot_zero(void) { return 0; }

static const char *stub_exe_dir(void)
{
    static char buf[4096];
    static int ready;
    if (!ready) {
        uint32_t sz = (uint32_t)sizeof(buf);
        if (_NSGetExecutablePath(buf, &sz) == 0) {
            char *slash = strrchr(buf, '/');
            if (slash)
                *slash = 0;
        } else {
            buf[0] = 0;
        }
        ready = 1;
    }
    return buf;
}

static uint64_t stub_read_appid(void)
{
    if (g_appid)
        return g_appid;
    const char *env = getenv("SteamAppId");
    if (!env)
        env = getenv("SteamGameId");
    if (env)
        g_appid = strtoull(env, NULL, 10);
    if (g_appid)
        return g_appid;

    char path[8200];
    const char *dir = stub_exe_dir();
    if (dir && dir[0]) {
        snprintf(path, sizeof(path), "%s/steam_appid.txt", dir);
        FILE *fh = fopen(path, "r");
        if (fh) {
            unsigned long long v = 0;
            if (fscanf(fh, "%llu", &v) == 1)
                g_appid = v;
            fclose(fh);
        }
    }
    if (g_appid)
        return g_appid;
    FILE *fh = fopen("steam_appid.txt", "r");
    if (fh) {
        unsigned long long v = 0;
        if (fscanf(fh, "%llu", &v) == 1)
            g_appid = v;
        fclose(fh);
    }
    return g_appid;
}

static void *stub_object(void)
{
    static int filled;
    if (!filled) {
        for (int i = 0; i < 256; i++)
            ((slot_fn *)g_vtbl)[i] = slot_zero;
        filled = 1;
    }
    return g_vtbl;
}

/* local file store - gives the game somewhere real to write its saves */
static const char *stub_savedir(void)
{
    static char buf[4096];
    static int ready;
    if (!ready) {
        const char *home = getenv("HOME");
        if (!home)
            home = "/tmp";
        snprintf(buf, sizeof(buf), "%s/Library/Application Support/LicenseTools/%llu/remote",
                 home, (unsigned long long)stub_read_appid());
        for (char *p = buf + 1; *p; p++) {
            if (*p == '/') {
                *p = 0;
                mkdir(buf, 0700);
                *p = '/';
            }
        }
        mkdir(buf, 0700);
        ready = 1;
    }
    return buf;
}

static const char *stub_savepath(const char *name)
{
    static char buf[8192];
    snprintf(buf, sizeof(buf), "%s/%s", stub_savedir(), (name && *name) ? name : "unnamed");
    return buf;
}

VIS(int) SteamAPI_ISteamRemoteStorage_FileWrite(void *s, const char *name,
                                               const void *data, int cb)
{
    (void)s;
    if (!name || !data || cb < 0)
        return 0;
    FILE *fh = fopen(stub_savepath(name), "wb");
    if (!fh)
        return 0;
    size_t put = fwrite(data, 1, (size_t)cb, fh);
    fclose(fh);
    return (int)put;
}

VIS(int) SteamAPI_ISteamRemoteStorage_FileRead(void *s, const char *name,
                                               void *buf, int cb)
{
    (void)s;
    if (!name || !buf || cb < 0)
        return 0;
    FILE *fh = fopen(stub_savepath(name), "rb");
    if (!fh)
        return 0;
    size_t got = fread(buf, 1, (size_t)cb, fh);
    fclose(fh);
    return (int)got;
}

VIS(int) SteamAPI_ISteamRemoteStorage_FileExists(void *s, const char *name)
{
    (void)s;
    FILE *fh = name ? fopen(stub_savepath(name), "rb") : NULL;
    if (!fh)
        return 0;
    fclose(fh);
    return 1;
}

VIS(int) SteamAPI_ISteamRemoteStorage_FilePersisted(void *s, const char *name)
{
    return SteamAPI_ISteamRemoteStorage_FileExists(s, name);
}

VIS(int) SteamAPI_ISteamRemoteStorage_FileDelete(void *s, const char *name)
{
    (void)s;
    return name && remove(stub_savepath(name)) == 0;
}

VIS(int) SteamAPI_ISteamRemoteStorage_FileForget(void *s, const char *name)
{
    return SteamAPI_ISteamRemoteStorage_FileDelete(s, name);
}

VIS(int32_t) SteamAPI_ISteamRemoteStorage_GetFileSize(void *s, const char *name)
{
    (void)s;
    if (!name)
        return 0;
    FILE *fh = fopen(stub_savepath(name), "rb");
    if (!fh)
        return 0;
    fseek(fh, 0, SEEK_END);
    long n = ftell(fh);
    fclose(fh);
    return (int32_t)n;
}

VIS(int) SteamAPI_ISteamRemoteStorage_GetQuota(void *s, uint64_t *total,
                                               uint64_t *avail)
{
    (void)s;
    if (total)
        *total = 1073741824ULL;
    if (avail)
        *avail = 1073741824ULL;
    return 1;
}

VIS(int) SteamAPI_ISteamRemoteStorage_GetFileCount(void *s) { (void)s; return 0; }

VIS(const char *) SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(void *s, int i,
                                                                  int32_t *size)
{
    (void)s;
    (void)i;
    if (size)
        *size = 0;
    return "";
}

VIS(uint64_t) SteamAPI_ISteamRemoteStorage_GetFileTimestamp(void *s, const char *name)
{
    (void)s;
    (void)name;
    return 0;
}

VIS(uint32_t) SteamAPI_ISteamRemoteStorage_GetSyncPlatforms(void *s, const char *name)
{
    (void)s;
    (void)name;
    return 1;
}

VIS(int) SteamAPI_ISteamRemoteStorage_SetSyncPlatforms(void *s, const char *name,
                                                       uint32_t platforms)
{
    (void)s;
    (void)name;
    (void)platforms;
    return 1;
}

VIS(int) SteamAPI_ISteamRemoteStorage_GetLocalFileChangeCount(void *s)
{
    (void)s;
    return 0;
}

VIS(int) SteamAPI_ISteamRemoteStorage_GetLocalFileChange(void *s, int i, int *kind,
                                                         int *flags)
{
    (void)s;
    (void)i;
    if (kind)
        *kind = 0;
    if (flags)
        *flags = 0;
    return 0;
}

VIS(const char *) SteamAPI_ISteamRemoteStorage_GetCStringFromError(void *s, int e)
{
    (void)s;
    (void)e;
    return "";
}

VIS(int) SteamAPI_ISteamApps_GetAppInstallDir(void *s, uint32_t appid, char *dir,
                                              uint32_t cb)
{
    (void)s;
    (void)appid;
    const char *src = stub_exe_dir();
    if (!dir || cb == 0)
        return 0;
    uint32_t i = 0;
    while (src[i] && i + 1 < cb) {
        dir[i] = src[i];
        i++;
    }
    dir[i] = 0;
    return (int)i;
}

VIS(int) SteamAPI_Init(void) { stub_read_appid(); return 1; }
VIS(int) SteamAPI_InitFlat(char *err) { (void)err; return SteamAPI_Init(); }
VIS(int) SteamAPI_InitSafe(void) { return SteamAPI_Init(); }
VIS(void) SteamAPI_Shutdown(void) { }
VIS(int) SteamAPI_IsSteamRunning(void) { return 1; }
VIS(int) SteamAPI_RestartAppIfNecessary(uint32_t appid) { (void)appid; return 0; }
VIS(void) SteamAPI_RunCallbacks(void) { }
VIS(void) SteamAPI_ManualDispatch_RunFrame(int pipe) { (void)pipe; }
VIS(int) SteamAPI_ManualDispatch_GetNextCallback(int pipe, void *msg) { (void)pipe; (void)msg; return 0; }
VIS(void) SteamAPI_ManualDispatch_FreeLastCallback(int pipe) { (void)pipe; }
VIS(void) SteamAPI_RegisterCallback(void *cb, int id) { (void)cb; (void)id; }
VIS(void) SteamAPI_UnregisterCallback(void *cb) { (void)cb; }
VIS(void) SteamAPI_RegisterCallResult(void *cb, uint64_t handle) { (void)cb; (void)handle; }
VIS(void) SteamAPI_UnregisterCallResult(void *cb, uint64_t handle) { (void)cb; (void)handle; }
VIS(const char *) SteamAPI_GetSteamInstallPath(void) { return "/tmp"; }
VIS(uint32_t) SteamAPI_GetHSteamUser(void) { return 1; }
VIS(uint32_t) SteamAPI_GetHSteamPipe(void) { return 1; }
VIS(void) SteamAPI_SetTryCatchCallbacks(int v) { (void)v; }
VIS(void *) SteamInternal_CreateInterface(const char *ver) { (void)ver; return stub_object(); }
VIS(int) SteamInternal_SteamAPI_Init(const char *p, char *err) { (void)p; (void)err; return SteamAPI_Init(); }
VIS(void *) SteamInternal_FindOrCreateUserInterface(unsigned u, const char *v) { (void)u; (void)v; return stub_object(); }
VIS(void *) SteamInternal_FindOrCreateGameServerInterface(unsigned u, const char *v) { (void)u; (void)v; return stub_object(); }
VIS(void *) SteamInternal_ContextInit(void *ctx) { (void)ctx; return stub_object(); }
VIS(void) SteamAPI_UseBreakpadCrashHandler(const char *a, const char *b, const char *c, int d, void *e, void *f) { (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; }
VIS(void) SteamAPI_SetBreakpadAppID(uint32_t appid) { (void)appid; }
VIS(void) SteamAPI_SetMiniDumpComment(const char *c) { (void)c; }
'''

_STUB_CORE_NAMES = {
    "SteamAPI_Init", "SteamAPI_InitFlat", "SteamAPI_InitSafe", "SteamAPI_Shutdown",
    "SteamAPI_IsSteamRunning", "SteamAPI_RestartAppIfNecessary", "SteamAPI_RunCallbacks",
    "SteamAPI_ManualDispatch_RunFrame", "SteamAPI_ManualDispatch_GetNextCallback",
    "SteamAPI_ManualDispatch_FreeLastCallback", "SteamAPI_RegisterCallback",
    "SteamAPI_UnregisterCallback", "SteamAPI_RegisterCallResult",
    "SteamAPI_UnregisterCallResult", "SteamAPI_GetSteamInstallPath",
    "SteamAPI_GetHSteamUser", "SteamAPI_GetHSteamPipe", "SteamAPI_SetTryCatchCallbacks",
    "SteamInternal_CreateInterface", "SteamInternal_SteamAPI_Init",
    "SteamInternal_FindOrCreateUserInterface", "SteamInternal_FindOrCreateGameServerInterface",
    "SteamInternal_ContextInit", "SteamAPI_UseBreakpadCrashHandler",
    "SteamAPI_SetBreakpadAppID", "SteamAPI_SetMiniDumpComment",
    "SteamAPI_ISteamRemoteStorage_FileWrite", "SteamAPI_ISteamRemoteStorage_FileRead",
    "SteamAPI_ISteamRemoteStorage_FileExists", "SteamAPI_ISteamRemoteStorage_FilePersisted",
    "SteamAPI_ISteamRemoteStorage_FileDelete", "SteamAPI_ISteamRemoteStorage_FileForget",
    "SteamAPI_ISteamRemoteStorage_GetFileSize", "SteamAPI_ISteamRemoteStorage_GetQuota",
    "SteamAPI_ISteamRemoteStorage_GetFileCount",
    "SteamAPI_ISteamRemoteStorage_GetFileNameAndSize",
    "SteamAPI_ISteamRemoteStorage_GetFileTimestamp",
    "SteamAPI_ISteamRemoteStorage_GetSyncPlatforms",
    "SteamAPI_ISteamRemoteStorage_SetSyncPlatforms",
    "SteamAPI_ISteamRemoteStorage_GetLocalFileChangeCount",
    "SteamAPI_ISteamRemoteStorage_GetLocalFileChange",
    "SteamAPI_ISteamRemoteStorage_GetCStringFromError",
    "SteamAPI_ISteamApps_GetAppInstallDir",
}

_STUB_ACCESSOR = "VIS(void *) {name}(void) {{ return stub_object(); }}\n"
_STUB_APPID_FN = "VIS(uint64_t) {name}(void *self) {{ (void)self; return stub_read_appid(); }}\n"
_STUB_STEAMID_FN = ("VIS(uint64_t) {name}(void *self) {{ (void)self; "
                    "return 76561198000000000ULL + stub_read_appid(); }}\n")
_STUB_YES_FN = "VIS(int) {name}(void *self) {{ (void)self; return 1; }}\n"
_STUB_NO_FN = "VIS(uint64_t) {name}(void *self) {{ (void)self; return 1; }}\n"
_STUB_FLOAT_FN = "VIS(double) {name}(void *self) {{ (void)self; return 1.0; }}\n"
_STUB_STR_FN = 'VIS(const char *) {name}(void *self) {{ (void)self; return ""; }}\n'


def _stub_kind(name):
    if name.endswith("_GetAppID") or name.endswith("_GetAppID_") or "GetAppID" in name:
        return "appid"
    if name.endswith("_GetSteamID") or name.endswith("_GetCSteamID"):
        return "steamid"
    if name.endswith("_GetStatFloat") or name.endswith("_GetStat"):
        return "float"
    if any(name.endswith(sfx) for sfx in (
            "_GetPersonaName", "_GetPlayerNickname", "_GetSteamUILanguage",
            "_GetCurrentGameLanguage", "_GetAvailableGameLanguages",
            "_GetCurrentBetaName", "_GetFriendPersonaName", "_GetClanName",
            "_GetAchievementName", "_GetAchievementDisplayAttribute",
            "_GetAppInstallDir", "_GetAppOwner", "_GetGameLanguage",
            "_GetSavedGameName")):
        return "str"
    if any(name.endswith(sfx) for sfx in (
            "_BIsSubscribed", "_BIsSubscribedApp", "_BLoggedOn", "_BLoggedOn_",
            "_BIsDlcInstalled", "_BIsAppInstalled", "_BGetDLCDataByIndex",
            "_BIsTimedTrial", "_BIsVACBanned", "_BAnon", "_BGetAchievement",
            "_BGetStat", "_BIsOverlayEnabled", "_BIsSteamRunning",
            "_BIsGameOverlayActive", "_StoreStats", "_RequestCurrentStats",
            "_BSetAchievement", "_BUpdateAvgRateStat", "_BActivateGameOverlay",
            "_BCommit")):
        return "yes"
    return "ptr"


def _stub_source(symbols, appid):
    out = [_STUB_CORE, f"\nstatic const unsigned long long k_appid = {int(appid)}ULL;\n"]
    out.append("VIS(uint64_t) stub_appid_under_test(void) { return k_appid; }\n")
    seen = set()
    for name in list(_STUB_ACCESSORS) + list(symbols):
        if not name or name in seen or not name.startswith("Steam"):
            continue
        seen.add(name)
        if name in _STUB_CORE_NAMES:
            continue
        if name.startswith("SteamAPI_Steam"):
            out.append(_STUB_ACCESSOR.format(name=name))
            continue
        kind = _stub_kind(name)
        if kind == "appid":
            out.append(_STUB_APPID_FN.format(name=name))
        elif kind == "steamid":
            out.append(_STUB_STEAMID_FN.format(name=name))
        elif kind == "float":
            out.append(_STUB_FLOAT_FN.format(name=name))
        elif kind == "yes":
            out.append(_STUB_YES_FN.format(name=name))
        elif kind == "str":
            out.append(_STUB_STR_FN.format(name=name))
        else:
            out.append(_STUB_NO_FN.format(name=name))
    return "".join(out)


def _bundle_executable(bundle):
    info = bundle / "Contents" / "Info.plist"
    if info.is_file():
        res = _run_quiet(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleExecutable",
                          str(info)], timeout=20)
        if res is not None and res.returncode == 0 and res.stdout.strip():
            exe = bundle / "Contents" / "MacOS" / res.stdout.strip()
            if exe.is_file():
                return exe
    macos = bundle / "Contents" / "MacOS"
    if macos.is_dir():
        for p in sorted(macos.iterdir()):
            try:
                if p.is_file() and os.access(p, os.X_OK):
                    return p
            except OSError:
                continue
    return None


def _undefined_steam_symbols(binary):
    res = _run_quiet(["nm", "-u", str(binary)], timeout=120)
    if res is None or res.returncode != 0:
        return []
    found = set()
    for line in res.stdout.splitlines():
        bits = line.split()
        if not bits:
            continue
        sym = bits[-1]
        if sym.startswith("_Steam"):
            found.add(sym[1:])
    return sorted(found)


def _app_bundles(root):
    return sorted(p for p in Path(root).rglob("*.app")
                  if p.is_dir() and (p / "Contents").is_dir())


def _game_binary(root):
    for bundle in _app_bundles(root):
        exe = _bundle_executable(bundle)
        if exe is not None:
            return exe
    cands = []
    for p in sorted(Path(root).rglob("*")):
        try:
            if p.is_file() and os.access(p, os.X_OK) and p.stat().st_size > 65536:
                if p.suffix.lower() in (".dylib", ".so", ".txt", ".json", ".png",
                                        ".dll", ".dat", ".pak", ".bundle"):
                    continue
                cands.append(p)
        except OSError:
            continue
    return cands[0] if cands else None


def _build_stub(source, out_path):
    work = Path(source).parent
    for arches in (["-arch", "arm64", "-arch", "x86_64"], []):
        argv = ["clang", "-dynamiclib", "-O2", "-fvisibility=hidden",
                "-install_name", "@rpath/" + STUB_NAME] + arches + \
               ["-o", str(out_path), str(source)]
        res = _run_quiet(argv, timeout=300)
        if res is not None and res.returncode == 0 and Path(out_path).is_file():
            _run_quiet(["codesign", "-f", "-s", "-", str(out_path)], timeout=120)
            lipo = _run_quiet(["lipo", "-info", str(out_path)], timeout=30)
            arch = (lipo.stdout.strip() if lipo is not None and lipo.returncode == 0
                    else "native")
            return {"ok": True, "dylib": str(out_path), "arch": arch,
                    "bytes": Path(out_path).stat().st_size, "work": str(work)}
    last = _run_quiet(["clang", "--version"], timeout=30)
    hint = "install Xcode Command Line Tools: xcode-select --install"
    if last is not None and last.returncode == 0:
        hint = "clang refused to build the stand-in library"
    return {"ok": False, "error": hint}


def _appid_file_dirs(target, exe):
    dirs = [Path(target)]
    if exe is not None:
        dirs.append(exe.parent)
    out = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def _entitlements(path):
    """The entitlements blob already on disk, as raw plist bytes."""
    res = _run_quiet(["codesign", "-d", "--entitlements", ":-", str(path)], timeout=60)
    if res is None or res.returncode != 0 or not res.stdout.strip():
        return b""
    blob = res.stdout.encode("utf-8", "replace")
    if b"<?xml" not in blob:
        return b""
    return blob


def _bundled_stub():
    """A stand-in compiled at build time, for Macs without clang."""
    roots = []
    base = getattr(sys, "_MEIPASS", "")
    if base:
        roots.append(Path(base) / "vendor" / "stub")
    roots.append(Path(__file__).resolve().parent / "vendor" / "stub")
    for r in roots:
        p = r / STUB_NAME
        if p.is_file() and p.stat().st_size > 1024:
            return p
    return None


def _resign(path, work):
    """Re-sign ad hoc, keeping the entitlements and dropping the runtime flag.

    The vendor signature dies the moment a library inside the bundle changes.
    Keeping the entitlements matters (sandbox, JIT, camera). Dropping the
    hardened runtime matters more: library validation refuses to load a dylib
    that is not signed by the same team, and an ad hoc stub has no team.
    """
    inner = Path(str(path))
    is_bundle = inner.is_dir()
    ent = _entitlements(inner)
    argv = ["codesign", "--force", "--sign", "-"]
    if ent:
        ent_file = Path(work) / ("entitlements-" + inner.name.replace(" ", "_") + ".plist")
        try:
            ent_file.write_bytes(ent)
            argv += ["--entitlements", str(ent_file)]
        except OSError:
            pass
    argv.append(str(inner))
    res = _run_quiet(argv, timeout=300)
    return {"ok": res is not None and res.returncode == 0,
            "entitlements": bool(ent), "bundle": is_bundle,
            "error": None if res is None else (res.stderr or "").strip()[:200]}


def _sign_tree(root, exe, work):
    """Sign the innermost code first, then the bundles that contain it."""
    out = []
    targets = []
    if exe is not None:
        targets.append(exe)
    for bundle in _app_bundles(root):
        targets.append(bundle)
    for t in targets:
        out.append({"path": str(t), **_resign(t, work)})
    return out


def drm_skip(target, appid, progress=None):
    root = Path(target)
    if not root.is_dir():
        return {"ok": False, "error": f"install folder missing: {root}"}
    exe = _game_binary(root)
    symbols = _undefined_steam_symbols(exe) if exe is not None else []
    libs = sorted(p for p in root.rglob(STUB_NAME) if p.is_file())
    if not libs:
        return {"ok": True, "skipped": True, "dylibs": [],
                "reason": "this game does not ship " + STUB_NAME}
    if progress:
        progress(f"game imports {len(symbols)} steam symbol(s), "
                 f"{len(libs)} librar(y/ies) to match")
    work = Path.home() / ".licensetools" / "stub" / str(int(appid))
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    src = work / "steam_stub.c"
    try:
        src.write_text(_stub_source(symbols, appid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    built = _build_stub(src, work / STUB_NAME)
    if not built.get("ok"):
        bundled = _bundled_stub()
        if bundled is not None:
            try:
                shutil.copy2(bundled, work / STUB_NAME)
                built = {"ok": True, "dylib": str(work / STUB_NAME),
                         "arch": "bundled", "prebuilt": True,
                         "bytes": (work / STUB_NAME).stat().st_size}
                if progress:
                    progress("using the stand-in that ships inside the app")
            except OSError:
                built = {"ok": False, "error": "bundled stand-in could not be copied"}
    if not built.get("ok"):
        missing = _run_quiet(["clang", "--version"], timeout=30)
        if missing is None or missing.returncode != 0:
            return {"ok": True, "skipped": True, "needs_tools": True,
                    "symbols": len(symbols), "dylibs": [str(p) for p in libs],
                    "hint": "nothing was changed - the game keeps its original library. "
                            "Only install the tools if a game refuses to start: "
                            "xcode-select --install"}
        return {"ok": False, "error": built.get("error"), "symbols": len(symbols)}
    report = {"ok": True, "symbols": len(symbols), "arch": built.get("arch"),
              "patched": [], "backups": [], "errors": []}
    for lib in libs:
        backup = lib.with_name(lib.name + ".orig")
        try:
            if not backup.is_file():
                shutil.copy2(lib, backup)
                report["backups"].append(str(backup))
            shutil.copy2(work / STUB_NAME, lib)
            _run_quiet(["codesign", "-f", "-s", "-", str(lib)], timeout=120)
            (lib.parent / (lib.name + STUB_MARK)).write_text(str(int(appid)),
                                                             encoding="utf-8")
            report["patched"].append(str(lib))
        except OSError as exc:
            report["errors"].append(f"{lib}: {exc}")
    for d in _appid_file_dirs(root, exe):
        try:
            (d / STUB_APPID_FILE).write_text(str(int(appid)), encoding="utf-8")
            report.setdefault("appid_files", []).append(str(d / STUB_APPID_FILE))
        except OSError as exc:
            report["errors"].append(f"{d / STUB_APPID_FILE}: {exc}")
    signs = _sign_tree(root, exe, work)
    report["signed"] = [s for s in signs if s.get("ok")]
    report["kept_entitlements"] = sum(1 for s in signs if s.get("entitlements"))
    report["ok"] = not report["errors"]
    return report


def drm_restore(target):
    root = Path(target)
    if not root.is_dir():
        return {"ok": False, "error": f"install folder missing: {root}"}
    undone, errors = [], []
    for backup in sorted(root.rglob(STUB_NAME + ".orig")):
        live = backup.with_name(backup.name[: -len(".orig")])
        try:
            shutil.copy2(backup, live)
            backup.unlink()
            undone.append(str(live))
        except OSError as exc:
            errors.append(f"{live}: {exc}")
    for marker in sorted(root.rglob(STUB_NAME + STUB_MARK)):
        try:
            marker.unlink()
        except OSError:
            pass
    for f in sorted(root.rglob(STUB_APPID_FILE)):
        try:
            f.unlink()
        except OSError:
            pass
    work = Path.home() / ".licensetools" / "stub" / "restore"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if undone:
        _sign_tree(root, _game_binary(root), work)
    return {"ok": not errors, "restored": undone, "errors": errors}


def drm_status(target):
    root = Path(target) if target else None
    if root is None or not root.is_dir():
        return {"ok": False, "error": "install folder not found"}
    libs = sorted(p for p in root.rglob(STUB_NAME) if p.is_file())
    rows = []
    for lib in libs:
        marker = lib.parent / (lib.name + STUB_MARK)
        rows.append({"dylib": str(lib), "ours": marker.is_file(),
                     "backup": lib.with_name(lib.name + ".orig").is_file()})
    return {"ok": True, "dylibs": rows, "count": len(rows),
            "patched": sum(1 for r in rows if r["ours"])}


def repair_install_state(appid):
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    acf_file = find_app_acf(appid)
    acf = parse_acf(acf_file) if acf_file is not None else None
    if acf_file is not None:
        sa = acf_file.parent
    installdir = (acf or {}).get("installdir", "")
    rel = clean_installdir(installdir) if installdir else None
    common = sa / "common" / rel if rel else None
    files = 0
    if common and common.is_dir():
        files = sum(1 for p in common.rglob("*") if p.is_file())
    stale = bool(acf) and files == 0
    if stale and acf_file is not None:
        try:
            acf_file.unlink()
        except OSError:
            pass
    restaged = {"ok": False, "staged": [], "missing": []}
    probe = probe_app(int(appid))
    if probe.get("ok"):
        rows = [r for r in select_rows(probe["rows"], "mac", False)
                if r.get("manifest")]
        if rows:
            restaged = stage_manifests(int(appid), rows, progress=LOGS.add)
            if restaged.get("ok") and not acf:
                total = sum(int(r["size"]) for r in rows
                            if str(r.get("size", "")).isdigit())
                download = sum(int(r["download"]) for r in rows
                               if str(r.get("download", "")).isdigit())
                write_pending_appmanifest(int(appid), probe.get("name"), installdir,
                                          probe.get("buildid", ""), rows,
                                          total, download)
    return {"ok": True, "acf": bool(acf), "installdir": installdir,
            "files": files, "stale_removed": stale,
            "manifests": restaged.get("staged", []),
            "manifests_missing": restaged.get("missing", [])}

def steam_install_url(appid):
    if sys.platform != "darwin":
        return {"ok": False, "error": "macOS only"}
    url = f"steam://install/{int(appid)}"
    try:
        subprocess.run(["open", url], capture_output=True, timeout=30)
        return {"ok": True, "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}

def open_in_finder(path):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"not found: {p}", "path": str(p)}
    if sys.platform == "darwin":
        subprocess.run(["open", str(p)], capture_output=True, timeout=30)
    elif sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.run(["xdg-open", str(p)], capture_output=True, timeout=30)
    return {"ok": True, "path": str(p)}

def open_steam_folder(which="steamapps"):
    root = SteamCtx().root()
    if root is None:
        return {"ok": False,
                "error": "steam folder not found - open Steam once so it creates its library"}
    targets = {
        "steamapps": root / "steamapps",
        "common": root / "steamapps" / "common",
        "root": root,
        "macsteam": Path.home() / "Library" / "Application Support" / "macsteam",
    }
    target = targets.get(str(which or "steamapps").strip(), targets["steamapps"])
    return open_in_finder(target)

def uninstall_game(appid, delete_files=True):
    aid = int(appid)
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    acf = read_appmanifest(aid) or {}
    installdir = acf.get("installdir", "") or ""
    removed = []
    manifest = sa / f"appmanifest_{aid}.acf"
    if manifest.exists():
        try:
            manifest.unlink()
            removed.append(str(manifest))
        except OSError as exc:
            return {"ok": False, "error": f"could not delete install record: {exc}"}
    if delete_files and installdir:
        game_dir = sa / "common" / installdir
        if game_dir.is_dir():
            shutil.rmtree(game_dir, ignore_errors=True)
            removed.append(str(game_dir))
    staging = sa / "downloading" / str(aid)
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        removed.append(str(staging))
    root = SteamCtx().root()
    if root is not None:
        for cache in (root / "userdata").glob(f"*/config/librarycache/{aid}.json"):
            try:
                cache.unlink()
                removed.append(str(cache))
            except OSError:
                pass
    cfg = macsteam_config_remove(aid)
    return {"ok": True, "appid": str(aid), "installdir": installdir,
            "removed": removed, "config": cfg}

def uninstall_loader():
    if sys.platform != "darwin":
        return {"ok": False, "error": "macOS only"}
    steam_app = steam_app_path()
    if not steam_app.is_dir():
        alt = Path.home() / "Applications" / "Steam.app"
        if alt.is_dir():
            steam_app = alt
        else:
            return {"ok": False, "error": "Steam.app not found in /Applications"}
    subprocess.run(["pkill", "-f", "steam_osx"], capture_output=True)
    removed = []
    dylib = steam_app / "Contents" / "MacOS" / "macsteam.dylib"
    if dylib.exists():
        try:
            dylib.unlink()
            removed.append(str(dylib))
        except OSError as exc:
            return {"ok": False, "error": f"could not delete dylib: {exc}"}
    plist = steam_app / "Contents" / "Info.plist"
    for key in (":LSEnvironment:DYLD_INSERT_LIBRARIES", ":LSEnvironment"):
        pr = _plistbuddy(["-c", f"Delete {key}"], plist)
        if pr is not None and pr.returncode == 0:
            removed.append(key)
    for target in (steam_app / "Contents" / "MacOS" / "steam_osx", steam_app):
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(target)],
                       capture_output=True, timeout=180)
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    if cfg.is_dir():
        shutil.rmtree(cfg, ignore_errors=True)
        removed.append(str(cfg))
    root = SteamCtx().root()
    if root is not None:
        for f in (root / "steam.cfg",
                  root / "Steam.AppBundle" / "Steam" / "Contents" / "MacOS" / "steam.cfg"):
            if f.exists():
                try:
                    f.unlink()
                    removed.append(str(f))
                except OSError:
                    pass
    lsr = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
           "LaunchServices.framework/Support/lsregister")
    if Path(lsr).exists():
        subprocess.run([lsr, "-f", str(steam_app)], capture_output=True, timeout=60)
    return {"ok": True, "removed": removed, "steam_app": str(steam_app),
            "hint": "Steam is back to a clean install - reopen it from Applications"}

# ---------- license loaders (stplug-in engine / macSteam) ----------

MACSTEAM_REPOS = (
    "crghofficial/macsteam",
    "ElfariaKensinton/macsteam-backup",
)
STEAMTOOLS_SITE = "https://steamtools.net/"

def bundled_macsteam():
    roots = []
    base = getattr(sys, "_MEIPASS", "")
    if base:
        roots.append(Path(base) / "vendor" / "macsteam")
    roots.append(Path(__file__).resolve().parent / "vendor" / "macsteam")
    for r in roots:
        d = r / "macsteam.dylib"
        if d.is_file():
            return r
    return None

def loader_report():
    ctx = SteamCtx()
    root = ctx.root()
    families = []
    st = ctx.plugin_dir()
    if st and st.is_dir():
        luas = sorted(st.glob("*.lua"))
        families.append({
            "family": "stplug-in",
            "engine": "SteamTools-compatible lua engine",
            "toml": loader_status(root),
            "lua_files": len(luas),
            "sample": [f.name for f in luas[:5]],
        })
    ms = macsteam_status()
    if ms["injected"] or ms["config_dir"]:
        families.append({
            "family": "macSteam",
            "injected": ms["injected"],
            "dylib": ms["dylib"],
            "config": ms["config_dir"],
        })
    return {"ok": True, "steam_root": str(root) if root else None,
            "platform": sys.platform, "families": families,
            "hint": None if families else
            "no loader detected - run: loader install --from macsteam (mac arm64) "
            "or --from steamtools"}

def _plistbuddy(args, plist):
    try:
        return subprocess.run(["/usr/libexec/PlistBuddy"] + args + [str(plist)],
                              capture_output=True, text=True, timeout=60)
    except Exception:
        return None

def macsteam_status():
    steam_app = steam_app_path()
    dylib = steam_app / "Contents" / "MacOS" / "macsteam.dylib"
    insert = None
    if sys.platform == "darwin":
        pr = _plistbuddy(["-c", "Print :LSEnvironment:DYLD_INSERT_LIBRARIES"],
                         steam_app / "Contents" / "Info.plist")
        if pr is not None and pr.returncode == 0:
            insert = (pr.stdout or "").strip() or None
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    return {"injected": bool(dylib.exists() and insert and str(dylib) in insert),
            "dylib": str(dylib) if dylib.exists() else None,
            "insert": insert,
            "config_dir": str(cfg) if cfg.is_dir() else None}

def _fetch_repo_signatures(repo, dest):
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    try:
        url = f"https://api.github.com/repos/{repo}/contents/signatures/macos.arm64"
        items = json.loads(http_get(url, headers={"Accept": "application/vnd.github+json"},
                                    timeout=30))
        for it in items if isinstance(items, list) else []:
            if it.get("type") == "file" and it.get("name", "").endswith(".json"):
                names.append((it["name"], it["download_url"]))
    except Exception:
        names = []
    if not names:
        try:
            flat = json.loads(http_get(
                f"https://data.jsdelivr.com/v1/packages/gh/{repo}@main?structure=flat",
                timeout=30))
            for f in flat.get("files", []):
                p = f.get("name", "")
                if p.startswith("/signatures/macos.arm64/") and p.endswith(".json"):
                    n = p.rsplit("/", 1)[-1]
                    names.append((n, f"https://cdn.jsdelivr.net/gh/{repo}@main{p}"))
        except Exception:
            names = []
    out = []
    for name, link in names:
        p = dest / name
        try:
            if not p.exists():
                p.write_bytes(http_get(link, timeout=60))
            out.append(str(p))
        except Exception:
            continue
    return out

def macsteam_inject(dylib_src, sig_roots):
    if sys.platform != "darwin":
        return {"ok": False, "error": "macsteam injection runs on macOS only"}
    steam_app = steam_app_path()
    exe = steam_app / "Contents" / "MacOS" / "steam_osx"
    plist = steam_app / "Contents" / "Info.plist"
    if not steam_app.is_dir():
        return {"ok": False, "error": "Steam.app not found in /Applications - install Steam first"}
    if not exe.exists():
        return {"ok": False, "error": "Steam.app layout unexpected (steam_osx missing)"}
    subprocess.run(["pkill", "-f", "steam_osx"], capture_output=True)
    target = steam_app / "Contents" / "MacOS" / "macsteam.dylib"
    probe = steam_app / "Contents" / "MacOS" / ".licensetools-write-probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except Exception as exc:
        return {"ok": False, "error": f"Steam.app is not writable: {exc}",
                "hint": "run once as an admin user, or fix ownership of /Applications/Steam.app"}
    try:
        if target.exists():
            target.unlink()
        shutil.copy2(dylib_src, target)
    except Exception as exc:
        return {"ok": False, "error": f"copy payload failed: {exc}",
                "hint": "Steam.app may need admin rights - move it to /Applications or chown it"}
    subprocess.run(["xattr", "-cr", str(target)], capture_output=True)
    subprocess.run(["xattr", "-d", "com.apple.quarantine", str(target)],
                   capture_output=True)
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    cfg.mkdir(parents=True, exist_ok=True)
    sig_dest = cfg / "signatures"
    copied = 0
    if sig_roots:
        if sig_dest.exists():
            shutil.rmtree(sig_dest)
        arch_dest = sig_dest / "macos.arm64"
        arch_dest.mkdir(parents=True, exist_ok=True)
        for root in sig_roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for src in root.rglob("*.json"):
                try:
                    shutil.copy2(src, arch_dest / src.name)
                    copied += 1
                except Exception:
                    pass
    if not copied:
        return {"ok": False, "error": "no signature json found - cannot resolve hooks",
                "hint": "signatures/macos.arm64/*.json must exist"}
    cur = None
    pr_cur = _plistbuddy(["-c", "Print :LSEnvironment:DYLD_INSERT_LIBRARIES"], plist)
    if pr_cur is not None and pr_cur.returncode == 0:
        cur = (pr_cur.stdout or "").strip() or None
    existing = [p for p in (cur or "").split(":")
                if p and p != str(target) and Path(p).exists()]
    val = ":".join([str(target)] + existing)
    pr = _plistbuddy(["-c", f"Set :LSEnvironment:DYLD_INSERT_LIBRARIES {val}"], plist)
    if pr is None or pr.returncode != 0:
        _plistbuddy(["-c", "Add :LSEnvironment dict"], plist)
        pr = _plistbuddy(["-c",
                          f"Add :LSEnvironment:DYLD_INSERT_LIBRARIES string {val}"], plist)
        if pr is None or pr.returncode != 0:
            pr = _plistbuddy(["-c", f"Set :LSEnvironment:DYLD_INSERT_LIBRARIES {val}"],
                             plist)
            if pr is None or pr.returncode != 0:
                return {"ok": False, "error": "Info.plist patch failed",
                        "detail": (pr.stderr if pr else "")[:300]}
    for u in (target, exe, steam_app):
        subprocess.run(["codesign", "-f", "-s", "-", str(u)], capture_output=True)
    body = "BootStrapperInhibitUpdateOnLaunch=enable\n"
    steam_root = Path.home() / "Library" / "Application Support" / "Steam"
    for p in (steam_root / "steam.cfg",
              steam_root / "Steam.AppBundle" / "Steam" / "Contents" / "MacOS" / "steam.cfg"):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        except Exception:
            pass
    try:
        (cfg / "dylib.version").write_text("licensetools", encoding="utf-8")
    except Exception:
        pass
    subprocess.run([
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister", "-f", str(steam_app)],
        capture_output=True)
    return {"ok": True, "dylib": str(target), "signatures": copied,
            "config_dir": str(cfg)}

def _ms_parse(text):
    apps, packages, depot = [], [], {}
    section = cur = None
    for raw in text.splitlines():
        line = raw.replace("\r", "")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        t = line.strip()
        if indent == 0:
            section = cur = None
            if t.startswith("Apps:"):
                section = "apps"
            elif t.startswith("PackageIds:"):
                section = "packages"
            elif t.startswith("DepotKeys:"):
                section = "depot"
            continue
        if section == "apps" and indent == 2 and t.startswith("-"):
            tok = t[1:].strip().split("#")[0].strip().split()[0] if t[1:].strip() else ""
            if tok.isdigit() and int(tok) not in apps:
                apps.append(int(tok))
        elif section == "packages" and indent == 2 and t.startswith("-"):
            tok = t[1:].strip().split("#")[0].strip().split()[0] if t[1:].strip() else ""
            if tok.isdigit() and int(tok) not in packages:
                packages.append(int(tok))
        elif section == "depot" and indent == 2 and t.endswith(":"):
            tok = t[:-1].strip()
            if tok.isdigit():
                cur = int(tok)
                depot.setdefault(cur, {})
        elif section == "depot" and indent == 4 and cur is not None and ":" in t:
            k, v = t.split(":", 1)
            if k.strip().isdigit():
                depot[cur][int(k.strip())] = v.strip().strip("'\"")
    return apps, packages, depot

def _ms_serialize(apps, packages, depot):
    out = ["HideWhatsNew: no", ""]
    if apps:
        out.append("Apps:")
        out += [f"  - {a}" for a in apps]
        out.append("")
    if packages:
        out.append("PackageIds:")
        out += [f"  - {p}" for p in packages]
        out.append("")
    if depot:
        out.append("DepotKeys:")
        for app, deps in depot.items():
            out.append(f"  {app}:")
            for d, k in deps.items():
                out.append(f"    {d}: {k}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"

def _ms_section(lines, header):
    start = next((i for i, l in enumerate(lines)
                  if l.strip().startswith(header) and not l.startswith((" ", "\t"))), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        raw = lines[end]
        if raw.strip() and not raw.startswith((" ", "\t", "-")):
            break
        end += 1
    return start, end

def _ms_ensure_list(lines, header, items):
    bounds = _ms_section(lines, header)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        lines.extend(f"  - {i}" for i in items)
        return True
    start, end = bounds
    body = lines[start + 1:end]
    have = set()
    for raw in body:
        tok = raw.strip().lstrip("-").strip().split("#")[0].strip().split()
        if tok and tok[0].isdigit():
            have.add(int(tok[0]))
    missing = [i for i in items if int(i) not in have]
    if not missing:
        return False
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = [f"  - {i}" for i in missing]
    return True

def _ms_drop_from_list(lines, header, item):
    bounds = _ms_section(lines, header)
    if bounds is None:
        return False
    start, end = bounds
    keep, dropped = [], False
    for raw in lines[start + 1:end]:
        tok = raw.strip().lstrip("-").strip().split("#")[0].strip().split()
        if tok and tok[0].isdigit() and int(tok[0]) == int(item):
            dropped = True
            continue
        keep.append(raw)
    if dropped:
        lines[start + 1:end] = keep
    return dropped

def _ms_ensure_depot_group(lines, appid, depots, keys=None):
    appid = int(appid)
    keys = {int(k): str(v).strip() for k, v in (keys or {}).items()}
    depots = [int(d) for d in depots]
    if not depots:
        return False
    bounds = _ms_section(lines, "DepotKeys:")
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("DepotKeys:")
        bounds = (len(lines) - 1, len(lines))
    start, end = bounds
    group = None
    for i in range(start + 1, end):
        raw = lines[i]
        if len(raw) - len(raw.lstrip(" ")) == 2 and raw.strip().endswith(":"):
            tok = raw.strip()[:-1].strip()
            if tok.isdigit() and int(tok) == appid:
                group = i
                break
    changed = False
    if group is None:
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = [f"  {appid}:"]
        group = insert_at
        changed = True
        end += 1
    have = {}
    j = group + 1
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            j += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= 2:
            break
        if indent == 4 and ":" in raw:
            k, v = raw.strip().split(":", 1)
            if k.strip().isdigit():
                have[int(k.strip())] = v.strip().strip("'\"")
        j += 1
    for d, key in keys.items():
        if d in have and len(key) == 64 and have[d].lower() != key:
            for i in range(group + 1, j):
                raw = lines[i]
                if len(raw) - len(raw.lstrip(" ")) != 4 or ":" not in raw:
                    continue
                k = raw.strip().split(":", 1)[0].strip()
                if k.isdigit() and int(k) == d:
                    lines[i] = f"    {d}: {key}"
                    have[d] = key
                    changed = True
                    break
    missing = [d for d in depots if d not in have]
    if missing:
        at = group + 1 if not have else j
        lines[at:at] = [f"    {d}: {keys.get(d, '0')}" for d in missing]
        changed = True
    return changed

def macsteam_config_add(appid, packages=(20200,), depots=(), keys=None):
    if sys.platform != "darwin":
        return {"ok": False, "skipped": "macsteam config exists on macOS only"}
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    cfg.mkdir(parents=True, exist_ok=True)
    f = cfg / "config.yaml"
    text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
    lines = text.replace("\r\n", "\n").split("\n")
    if not any(l.strip().startswith("HideWhatsNew") for l in lines):
        lines.insert(0, "HideWhatsNew: no")
        lines.insert(1, "")
    changed = _ms_ensure_list(lines, "Apps:", [int(appid)])
    changed = _ms_ensure_list(lines, "PackageIds:", [int(p) for p in packages]) or changed
    changed = _ms_ensure_depot_group(lines, appid, depots, keys) or changed
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip("\n") + "\n")
    try:
        f.write_text(out, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"config write failed: {exc}"}
    apps, _pkgs, depot = _ms_parse(out)
    return {"ok": True, "file": str(f), "apps": len(apps),
            "depots": len(depot.get(int(appid), {})), "changed": changed}

def _ms_drop_depot_group(lines, appid):
    bounds = _ms_section(lines, "DepotKeys:")
    if bounds is None:
        return False
    start, end = bounds
    for i in range(start + 1, end):
        raw = lines[i]
        if len(raw) - len(raw.lstrip(" ")) == 2 and raw.strip().endswith(":"):
            if raw.strip()[:-1].strip() == str(int(appid)):
                j = i + 1
                while j < end and (not lines[j].strip() or
                                   len(lines[j]) - len(lines[j].lstrip(" ")) > 2):
                    j += 1
                del lines[i:j]
                return True
    return False

def macsteam_config_remove(appid):
    if sys.platform != "darwin":
        return {"ok": False, "skipped": "macOS only"}
    cfg = Path.home() / "Library" / "Application Support" / "macsteam"
    f = cfg / "config.yaml"
    if not f.exists():
        return {"ok": True, "removed": False, "reason": "no config file"}
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    lines = text.replace("\r\n", "\n").split("\n")
    had = _ms_drop_from_list(lines, "Apps:", appid)
    dropped_keys = _ms_drop_depot_group(lines, appid)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip("\n") + "\n")
    try:
        f.write_text(out, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"config write failed: {exc}"}
    apps, _pkgs, _depot = _ms_parse(out)
    return {"ok": True, "removed": had, "keys_removed": dropped_keys,
            "file": str(f), "apps": len(apps)}

def _stage_macsteam_app(archive, dest):
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        apps = sorted(p for p in dest.rglob("*.app") if p.is_dir())
        return apps[0] if apps else None
    if archive.suffix.lower() == ".dmg" and sys.platform == "darwin":
        mount = dest / "_mnt"
        mount.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly",
                            "-mountpoint", str(mount), str(archive)],
                           check=True, capture_output=True, timeout=180)
            apps = sorted(p for p in mount.rglob("*.app") if p.is_dir())
            if not apps:
                return None
            out = dest / apps[0].name
            if out.exists():
                shutil.rmtree(out)
            shutil.copytree(apps[0], out)
            return out
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"],
                           capture_output=True)
    return None

def loader_install_macsteam():
    bundled = bundled_macsteam()
    if bundled is not None:
        dylib = bundled / "macsteam.dylib"
        sig_roots = [p for p in bundled.rglob("signatures") if p.is_dir()]
        res = {"ok": True, "repo": "bundled", "version": "vendored",
               "asset": "inside app", "staged": str(bundled),
               "dylib_src": str(dylib), "signatures": 0}
        if sys.platform == "darwin":
            fresh = _fetch_repo_signatures(MACSTEAM_REPOS[0],
                                           TOOLS_DIR / "loader" / "macsteam"
                                           / "signatures" / "macos.arm64")
            if fresh:
                sig_roots.append(TOOLS_DIR / "loader" / "macsteam" / "signatures")
            inj = macsteam_inject(dylib, sig_roots)
            res.update({k: inj.get(k) for k in
                        ("ok", "error", "hint", "dylib", "signatures", "config_dir")})
            res["injected"] = bool(inj.get("ok"))
            res["next"] = ("loader ready - press Fetch in the app"
                           if inj.get("ok") else "injection failed - see error")
            return res
        res["next"] = "bundled payload present (injection happens on macOS)"
        return res
    tried = []
    for repo in MACSTEAM_REPOS:
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            data = json.loads(http_get(api,
                                       headers={"Accept": "application/vnd.github+json"},
                                       timeout=30))
        except Exception as exc:
            tried.append(f"{repo}: release fetch failed ({exc})")
            continue
        all_assets = data.get("assets", [])
        bundles = [a for a in all_assets
                   if a.get("name", "").lower().endswith((".zip", ".dmg"))]
        raw_dylibs = [a for a in all_assets
                      if a.get("name", "").lower().endswith(".dylib")]
        if not bundles and not raw_dylibs:
            tried.append(f"{repo}: no bundle or dylib asset in latest release")
            continue
        dest = TOOLS_DIR / "loader" / "macsteam"
        dest.mkdir(parents=True, exist_ok=True)
        archive_name = bundles[0]["name"] if bundles else raw_dylibs[0]["name"]
        if bundles:
            asset = bundles[0]
            archive = dest / asset["name"]
            try:
                archive.write_bytes(http_get(asset["browser_download_url"], timeout=600))
                src = _stage_macsteam_app(archive, dest)
                archive.unlink(missing_ok=True)
            except Exception as exc:
                tried.append(f"{repo}: {exc}")
                continue
            if src is None:
                tried.append(f"{repo}: no .app found inside {asset['name']}")
                continue
        dylib = next(iter(sorted(dest.rglob("macsteam.dylib"))), None)
        if dylib is None and raw_dylibs:
            ra = raw_dylibs[0]
            try:
                dylib = dest / ra["name"]
                dylib.write_bytes(http_get(ra["browser_download_url"], timeout=600))
            except Exception as exc:
                tried.append(f"{repo}: dylib asset failed ({exc})")
                dylib = None
        if dylib is None:
            found = sorted(dest.rglob("*.dylib"))
            dylib = found[0] if found else None
        if dylib is None:
            tried.append(f"{repo}: no macsteam.dylib inside {archive_name}")
            continue
        sig_roots = [p for p in dest.rglob("signatures") if p.is_dir()]
        fresh = _fetch_repo_signatures(repo, dest / "signatures" / "macos.arm64")
        if fresh:
            sig_roots.append(dest / "signatures")
        result = {"ok": True, "repo": repo, "version": data.get("tag_name"),
                  "asset": bundles[0]["name"] if bundles else raw_dylibs[0]["name"],
                  "staged": str(dest), "dylib_src": str(dylib),
                  "signatures": len(fresh)}
        if sys.platform == "darwin":
            inj = macsteam_inject(dylib, sig_roots)
            result.update({k: inj.get(k) for k in
                           ("ok", "error", "hint", "dylib", "signatures", "config_dir")})
            result["injected"] = bool(inj.get("ok"))
            if not inj.get("ok"):
                result["next"] = "injection failed - see error above"
                return result
            result["next"] = "loader ready - press Fetch in the app, game lands in your library"
        else:
            result["next"] = "staged (injection happens on macOS)"
        return result
    return {"ok": False, "error": "no macsteam source reachable",
            "tried": tried,
            "manual": f"https://github.com/{MACSTEAM_REPOS[0]}/releases"}

def loader_install_steamtools():
    ctx = SteamCtx()
    root = ctx.root()
    if root is None:
        return {"ok": False, "error": "steam root not found"}
    st = ctx.plugin_dir()
    if st is not None:
        st.mkdir(parents=True, exist_ok=True)
    ensure_lua_path(root)
    return {"ok": True, "download": STEAMTOOLS_SITE,
            "note": "SteamTools mac client is paid software - install it from the site; "
                    "every lua licensetools grants already lands in config/stplug-in "
                    "and loads on boot",
            "prepared": {"stplug": str(st), "toml": str(root / TOML_NAME)}}

def cmd_loader(a):
    if a.action == "status":
        out = loader_report()
    elif a.family == "macsteam":
        out = loader_install_macsteam()
    else:
        out = loader_install_steamtools()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1

# ---------- CLI commands ----------

def cmd_detect(_a):
    ctx = SteamCtx()
    root = ctx.root()
    lib = library_list()
    print(json.dumps({
        "platform": sys.platform,
        "root": str(root) if root else None,
        "valid": ctx.valid(),
        "loader": loader_status(root),
        "stplug": str(ctx.plugin_dir()) if ctx.plugin_dir() else None,
        "depotcache": str(ctx.depot_cache()) if ctx.depot_cache() else None,
        "steam_running": ctx.running(),
        "installed_lua": installed_list()["items"],
        "library_count": lib.get("count", 0) if lib.get("ok") else 0,
        "steamcmd": str(_find_tool("steamcmd") or ""),
        "depotdownloader": str(_find_tool("DepotDownloader") or ""),
        "cache": str(CACHE_DIR),
    }, indent=2, ensure_ascii=False))

def cmd_probe(a):
    res = probe_app(_appid(a.appid), a.branch, a.appinfo or None)
    if res.get("ok"):
        res["rows"] = select_rows(res["rows"], a.os, True)
        res["selected"] = len(res["rows"])
        if a.csv:
            Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
            Path(a.csv).write_text(rows_to_csv(res["rows"]), encoding="utf-8")
            res["csv"] = a.csv
    print(json.dumps(res, indent=2, ensure_ascii=False))

def cmd_grant(a):
    aid = _appid(a.appid)
    res = probe_app(aid, a.branch, a.appinfo or None)
    if not res.get("ok"):
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 1
    rows = select_rows(res["rows"], a.os, not a.no_shared)
    if not rows:
        print(json.dumps({"ok": False,
                          "error": f"no depots for os={a.os} branch={a.branch}"}))
        return 1
    lua, files = gen_lua(aid, rows, a.branch, pin=not a.no_pin,
                         buildid=res.get("buildid", ""))
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(lua, encoding="utf-8")
    if a.dry_run:
        sys.stdout.write(lua)
        return 0
    inst = install_lua_text(aid, lua, a.branch)
    inst["depots"] = len(rows)
    inst["manifest_files"] = files
    inst["buildid"] = res.get("buildid", "")
    if inst.get("ok"):
        inst["hint"] = "run: restart  (steam reloads stplug-in on boot)"
    print(json.dumps(inst, indent=2, ensure_ascii=False))
    return 0 if inst.get("ok") else 1

def cmd_manifests(a):
    aid = _appid(a.appid)
    probe = probe_app(aid, a.branch)
    if not probe.get("ok"):
        print(json.dumps(probe, indent=2, ensure_ascii=False))
        return 1
    rows = select_rows(probe["rows"], a.os, False)
    rows = [r for r in rows if r.get("manifest")]
    res = stage_manifests(aid, rows,
                          progress=lambda m: print(m, file=sys.stderr))
    res["appid"] = str(aid)
    res["cache"] = str(depotcache_dir() or "")
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1

def cmd_remove(a):
    print(json.dumps(remove_lua(_appid(a.appid)), indent=2))

def cmd_list(_a):
    out = installed_list()
    out["vault"] = vault_list()
    print(json.dumps(out, indent=2, ensure_ascii=False))

def cmd_install(a):
    aid = _appid(a.appid) if a.appid else None
    print(json.dumps(install_local(a.file, aid, a.branch), indent=2, ensure_ascii=False))

def cmd_import_lua(a):
    res = import_lua_file(a.file)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1

def cmd_library(_a):
    print(json.dumps(library_list(), indent=2, ensure_ascii=False))

def cmd_restart(_a):
    print(json.dumps(restart_steam(), indent=2))

def cmd_ensure(_a):
    print(json.dumps(ensure_tools(), indent=2, ensure_ascii=False))

def cmd_settings(a):
    s = load_settings()
    if a.steam_path is not None:
        s["steam_path"] = a.steam_path.strip()
        save_settings(s)
    if a.token:
        s["auth_token"] = a.token
        save_settings(s)
    print(json.dumps({"settings": load_settings(),
                      "root": str(SteamCtx().root() or "")}, indent=2, ensure_ascii=False))

def cmd_fetch(a):
    aid = _appid(a.appid)
    rows = None
    if a.appinfo:
        probe = probe_app(aid, a.branch, a.appinfo)
        if probe.get("ok"):
            rows = select_rows(probe["rows"], a.os, not a.no_shared)
    res = fetch_game(aid, a.branch, rows, a.os, not a.no_shared,
                     a.user, a.pass_, a.out or None, a.arg or None,
                     a.appinfo or None)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1

def cmd_keydb(a):
    table = depot_key_db(refresh=bool(a.refresh),
                         progress=lambda m: print(m, file=sys.stderr))
    print(json.dumps({"status": key_status(), "keys": len(table)},
                     indent=2, ensure_ascii=False))
    return 0

def cmd_keys(a):
    aid = _appid(a.appid)
    probe = probe_app(aid, a.branch)
    if not probe.get("ok"):
        print(json.dumps(probe, indent=2, ensure_ascii=False))
        return 1
    rows = select_rows(probe["rows"], a.os, not a.no_shared)
    depots = [int(r["depot"]) for r in rows]
    keys = keys_for_depots(depots, progress=lambda m: print(m, file=sys.stderr))
    out = [{"depot": d, "key": keys.get(d, ""),
            "found": d in keys} for d in depots]
    print(json.dumps({"appid": aid, "depots": out,
                      "found": sum(1 for x in out if x["found"])},
                     indent=2, ensure_ascii=False))
    return 0

def cmd_app_update(a):
    if not getattr(a, "apply", False):
        info = app_update_check()
        if not info.get("ok"):
            print("check failed:", info.get("error"))
            return 1
        print(f"current {info['current']}")
        print(f"latest  {info['latest']}")
        print("update available" if info.get("newer") else "up to date")
        if info.get("asset"):
            print("asset   ", info["asset"]["name"],
                  f"({info['asset']['bytes'] // (1024 * 1024)} MB)")
        if info.get("page"):
            print("page    ", info["page"])
        return 0
    res = app_update_apply(lambda m: print(m, flush=True))
    if res.get("relaunching"):
        print(f"staged {res.get('from')} -> {res.get('to')}, reopening")
        return 0
    if res.get("up_to_date"):
        print("already on the newest build")
        return 0
    print("update failed:", res.get("error"))
    return 1


def cmd_selftest(_a):
    return selftest()

# ---------- pywebview GUI ----------

class Logs:
    def __init__(self):
        self.lines = []
        self.lock = threading.Lock()

    def add(self, msg):
        with self.lock:
            for line in str(msg).splitlines() or [""]:
                self.lines.append(line)

    def since(self, index):
        with self.lock:
            try:
                index = max(0, int(index))
            except (TypeError, ValueError):
                index = 0
            return self.lines[index:], len(self.lines)

LOGS = Logs()
_FETCH_LOCK = threading.Lock()
FETCH_RESULT = {}

class Api:
    def bootstrap(self):
        ctx = SteamCtx()
        root = ctx.root()
        lib = library_list()
        LOGS.add(f"engine online - {sys.platform} - v{APP_VERSION}")
        LOGS.add(f"steam root: {root or 'not detected'}")
        LOGS.add(f"loader: {loader_status(root)}")
        return {"ok": True, "root": str(root or ""), "valid": ctx.valid(),
                "loader": loader_status(root),
                "library": lib.get("apps", []) if lib.get("ok") else [],
                "count": lib.get("count", 0) if lib.get("ok") else 0,
                "platform": sys.platform, "version": APP_VERSION,
                "installed": installed_list()["items"]}

    def library(self):
        lib = library_list()
        return {"ok": bool(lib.get("ok")), "apps": lib.get("apps", []),
                "count": lib.get("count", 0)}

    def search(self, term):
        term = (term or "").strip()
        if not term:
            return []
        url = ("https://store.steampowered.com/api/storesearch/?term="
               + urllib.parse.quote(term) + "&l=english&cc=US")
        try:
            data = json.loads(http_get(url, timeout=20))
        except Exception as exc:
            LOGS.add(f"search failed: {exc}")
            raise
        hits = [{"appid": str(i["id"]), "name": i["name"]}
                for i in data.get("items", [])][:25]
        LOGS.add(f"search: {len(hits)} hits for {term}")
        return hits

    def probe(self, appid, branch, want_os):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        branch = (branch or "public").strip()
        want_os = (want_os or "mac").strip()
        LOGS.add(f"probe {appid} - branch {branch} - os {want_os}")
        res = probe_app(int(appid), branch)
        if not res.get("ok"):
            LOGS.add("probe failed: " + str(res.get("error")))
            return res
        rows = select_rows(res["rows"], want_os)
        res["rows"] = rows
        LOGS.add(f"probe {appid}: {len(rows)} depot(s) - build {res.get('buildid') or '?'}")
        return res

    def grant(self, appid, branch, want_os, pin):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        branch = (branch or "public").strip() or "public"
        want_os = (want_os or "mac").strip() or "mac"
        pin = bool(pin)
        LOGS.add(f"grant {appid} - branch {branch} - pin {pin}")
        res = probe_app(int(appid), branch)
        if not res.get("ok"):
            LOGS.add("grant failed at probe: " + str(res.get("error")))
            return res
        rows = select_rows(res["rows"], want_os)
        if not rows:
            err = {"ok": False, "error": f"no depots for os={want_os} branch={branch}"}
            LOGS.add(err["error"])
            return err
        lua, files = gen_lua(int(appid), rows, branch, pin=pin,
                             buildid=res.get("buildid", ""))
        inst = install_lua_text(int(appid), lua, branch)
        inst["depots"] = len(rows)
        inst["manifest_files"] = files
        depots = [int(r["depot"]) for r in rows]
        keys = keys_for_depots(depots)
        cfg = macsteam_config_add(int(appid), depots=depots, keys=keys)
        inst["config"] = cfg
        inst["depot_keys"] = len(keys)
        if not cfg.get("ok"):
            inst["config_error"] = cfg.get("error") or cfg.get("skipped")
        LOGS.add(f"loader config: {len(depots)} depot(s), {len(keys)} key(s)")
        missing = [d for d in depots if d not in keys]
        if missing:
            LOGS.add("no key for depot(s): " + ", ".join(map(str, missing))
                     + " - steam will report encrypted content for those")
        LOGS.add(("installed " + inst["dest"]) if inst.get("ok")
                 else ("grant failed: " + str(inst.get("error"))))
        return inst

    def unlock(self, appid, branch="public", want_os="mac"):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        branch = (branch or "public").strip() or "public"
        want_os = (want_os or "mac").strip() or "mac"
        steps = []
        if sys.platform == "darwin":
            ms = macsteam_status()
            if not ms["injected"]:
                LOGS.add("loader not injected - installing macSteam automatically...")
                inst = loader_install_macsteam()
                steps.append({"step": "loader", "ok": inst.get("ok"),
                              "repo": inst.get("repo"), "version": inst.get("version"),
                              "error": inst.get("error")})
                for line in inst.get("tried", []):
                    LOGS.add("  " + line)
                if not inst.get("ok"):
                    return {"ok": False, "stage": "loader",
                            "error": inst.get("error"), "steps": steps}
            else:
                steps.append({"step": "loader", "ok": True, "note": "already injected"})
            probe = probe_app(int(aid), branch)
            depots, keys = [], {}
            if probe.get("ok"):
                rows = select_rows(probe["rows"], want_os)
                depots = [int(r["depot"]) for r in rows]
                keys = keys_for_depots(depots)
            else:
                LOGS.add("probe failed before config: " + str(probe.get("error")))
            cfg = macsteam_config_add(int(aid), depots=depots, keys=keys)
            steps.append({"step": "config", **cfg})
            if not cfg.get("ok"):
                return {"ok": False, "stage": "config", "error": cfg.get("error"),
                        "steps": steps}
            LOGS.add(f"app {aid} added to the loader config - {len(depots)} depot(s), "
                     f"{len(keys)} key(s)")
            missing = [d for d in depots if d not in keys]
            if missing:
                LOGS.add("no key for depot(s): " + ", ".join(map(str, missing)))
            if probe.get("ok"):
                mrows = [r for r in select_rows(probe["rows"], want_os, False)
                         if r.get("manifest")]
                st = stage_manifests(int(aid), mrows, progress=LOGS.add)
                steps.append({"step": "manifests", "ok": st.get("ok"),
                              "staged": st.get("staged"),
                              "missing": st.get("missing")})
                if st.get("missing"):
                    LOGS.add("manifest missing for depot(s): " +
                             ", ".join(str(d) for d in st["missing"]))
            rep = repair_install_state(int(aid))
            if rep.get("stale_removed"):
                LOGS.add("cleared a stale install record with no files on disk")
            steps.append({"step": "state", **rep})
            ctx = SteamCtx()
            if ctx.plugin_dir() is not None and ctx.plugin_dir().is_dir():
                extra = self.grant(aid, branch, want_os, True)
                steps.append({"step": "grant", "ok": extra.get("ok"),
                              "dest": extra.get("dest"), "depots": extra.get("depots"),
                              "error": extra.get("error")})
            LOGS.add("restarting steam so it picks up the new config")
            rs = restart_steam()
            steps.append({"step": "restart", **rs})
            if not rs.get("ok"):
                LOGS.add("steam restart: " + str(rs.get("error") or "failed"))
            return {"ok": True, "appid": aid, "steps": steps,
                    "hint": "in your library now - press Download files to put the game on disk"}
        res = self.grant(aid, branch, want_os, True)
        steps.append({"step": "grant", "ok": res.get("ok"),
                      "dest": res.get("dest"), "depots": res.get("depots"),
                      "error": res.get("error")})
        if not res.get("ok"):
            return {"ok": False, "stage": "grant", "error": res.get("error"),
                    "steps": steps}
        LOGS.add("restarting steam so it picks up the new config")
        rs = restart_steam()
        steps.append({"step": "restart", **rs})
        if not rs.get("ok"):
            LOGS.add("steam restart: " + str(rs.get("error") or "failed"))
        return {"ok": True, "appid": aid, "steps": steps,
                "hint": "game should appear in your library now"}

    def install(self, appid, user="", password="", update=False):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        if not _FETCH_LOCK.acquire(blocking=False):
            return {"ok": False, "error": "a download is already running"}
        mode = bool(update)

        def worker():
            try:
                FETCH_RESULT["install"] = None
                LOGS.add(f"install {aid} - pulling the depots straight from the Steam CDN")
                rep = repair_install_state(int(aid))
                if rep.get("stale_removed"):
                    LOGS.add("cleared a stale install record with no files")
                res = install_to_library(int(aid), "public", "mac", progress=lambda m: LOGS.add(str(m)),
                                         update=mode)
                if res.get("ok"):
                    LOGS.add(f"install {aid} done - {res.get('files', 0)} file(s), "
                             f"{res.get('files_bytes', 0) // (1024 * 1024)} MB")
                    if res.get("skipped"):
                        LOGS.add(f"{res['skipped']} file(s) were already up to date")
                    drm = res.get("drm") or {}
                    if drm.get("patched"):
                        LOGS.add("steam library replaced - the game will not ask for a check")
                    elif drm.get("needs_tools"):
                        LOGS.add("library left untouched: no build tools on this mac - "
                                 "the game was not changed")
                    elif drm.get("skipped"):
                        LOGS.add("no steam library in this game - nothing to replace")
                    elif drm.get("error"):
                        LOGS.add("library replace failed: " + str(drm.get("error")))
                else:
                    LOGS.add("install failed at " + str(res.get("stage")) + ": " +
                             str(res.get("error")))
                    if res.get("hint"):
                        LOGS.add("hint: " + str(res.get("hint")))
                    if res.get("detail"):
                        LOGS.add("detail: " + str(res.get("detail")))
                FETCH_RESULT["install"] = res
            except Exception as exc:
                LOGS.add("install crashed: " + str(exc))
                FETCH_RESULT["install"] = {"ok": False, "error": str(exc)}
            finally:
                _FETCH_LOCK.release()

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "queued": True, "appid": aid, "update": mode}

    def update(self, appid):
        return self.install(appid, "", "", True)

    def install_status(self):
        return FETCH_RESULT.get("install") or {"ok": None, "running": _FETCH_LOCK.locked()}

    def manifests_status(self):
        return FETCH_RESULT.get("manifests") or {"ok": None}

    def open_folder(self, which="steamapps"):
        res = open_steam_folder(which)
        LOGS.add(("opened " + res["path"]) if res.get("ok")
                 else ("open failed: " + str(res.get("error"))))
        return res

    def paths(self):
        return paths_report()

    def refresh_keys(self):
        try:
            table = depot_key_db(refresh=True,
                                 progress=lambda m: LOGS.add(m))
        except Exception as exc:
            LOGS.add("depot key refresh crashed: " + str(exc))
            return {"ok": False, "error": str(exc), "keys": 0}
        st = key_status()
        LOGS.add(f"depot key table: {len(table)} keys - {st['path']}")
        if not table and st.get("error"):
            LOGS.add("key table error: " + st["error"])
        return {"ok": bool(table), "keys": len(table), "status": st,
                "error": None if table else (st.get("error") or "table came back empty")}

    def _pick_lua(self):
        try:
            import webview
            wins = getattr(webview, "windows", None) or []
            if not wins:
                return ""
            picked = wins[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Lua files (*.lua)", "All files (*.*)"))
        except Exception:
            return ""
        if not picked:
            return ""
        return picked[0] if isinstance(picked, (list, tuple)) else str(picked)

    def import_lua(self, path=""):
        p = self._pick_lua()
        if not p:
            return {"ok": False, "cancelled": True}
        res = import_lua_file(p)
        LOGS.add(("lua " + str(res.get("appid")) + " imported")
                 if res.get("ok") else ("lua import failed: " + str(res.get("error"))))
        return res

    def app_update(self):
        res = app_update_check()
        if res.get("ok"):
            LOGS.add(f"app {res['current']} - newest is {res.get('latest')}"
                     + (" - update available" if res.get("newer") else " - up to date"))
        else:
            LOGS.add("update check failed: " + str(res.get("error")))
        return res

    def app_update_install(self):
        LOGS.add("checking for a newer build of the app itself")
        res = app_update_apply(lambda m: LOGS.add(str(m)))
        if res.get("relaunching"):
            LOGS.add(f"new build staged: {res.get('from')} -> {res.get('to')}")
            LOGS.add("the app closes now and reopens from the new bundle")
        elif res.get("up_to_date"):
            LOGS.add("already on the newest build")
        else:
            LOGS.add("update failed: " + str(res.get("error")))
        return res

    def drm_patch(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        acf = read_appmanifest(int(aid))
        installdir = clean_installdir((acf or {}).get("installdir", "") or "")
        sa = steam_apps_dir()
        if acf is None or installdir is None or sa is None:
            return {"ok": False, "error": "install the game first"}
        target = sa / "common" / installdir
        LOGS.add("replacing the steam library in " + str(target))
        res = drm_skip(target, int(aid), lambda m: LOGS.add(str(m)))
        if res.get("needs_tools"):
            LOGS.add("nothing was changed - this mac has no build tools")
            LOGS.add("the game keeps its original library and still runs")
            LOGS.add("only install them if a game refuses to start: xcode-select --install")
        elif res.get("skipped"):
            LOGS.add("this game has no " + STUB_NAME + " - nothing to replace")
        elif res.get("ok"):
            LOGS.add(f"patched {len(res.get('patched') or [])} librar(y/ies), "
                     f"{res.get('symbols', 0)} symbol(s) matched")
        else:
            LOGS.add("patch failed: " + str(res.get("error")))
        return res

    def drm_restore(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        acf = read_appmanifest(int(aid))
        installdir = clean_installdir((acf or {}).get("installdir", "") or "")
        sa = steam_apps_dir()
        if acf is None or installdir is None or sa is None:
            return {"ok": False, "error": "install the game first"}
        res = drm_restore(sa / "common" / installdir)
        LOGS.add(f"restored {len(res.get('restored') or [])} original librar(y/ies)")
        return res

    def drm_status(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        acf = read_appmanifest(int(aid))
        installdir = clean_installdir((acf or {}).get("installdir", "") or "")
        sa = steam_apps_dir()
        if acf is None or installdir is None or sa is None:
            return {"ok": False, "error": "not installed"}
        return drm_status(sa / "common" / installdir)

    def repair(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        res = repair_install_state(int(aid))
        LOGS.add("repair " + aid + ": " + json.dumps(res)[:240])
        return res

    def manifests(self, appid, os_name="mac"):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        want = str(os_name or "mac").strip() or "mac"

        def worker():
            try:
                FETCH_RESULT["manifests"] = None
                LOGS.add(f"manifests {aid} - staging depot files for {want}")
                probe = probe_app(int(aid), "public")
                if not probe.get("ok"):
                    LOGS.add("manifest stage failed at probe: " + str(probe.get("error")))
                    FETCH_RESULT["manifests"] = probe
                    return
                rows = [r for r in select_rows(probe["rows"], want, False)
                        if r.get("manifest")]
                if not rows:
                    out = {"ok": False, "error": f"no depot manifests for os={want}"}
                    LOGS.add(out["error"])
                    FETCH_RESULT["manifests"] = out
                    return
                res = stage_manifests(int(aid), rows,
                                      progress=lambda m: LOGS.add(str(m)))
                res["appid"] = aid
                res["cache"] = str(depotcache_dir() or "")
                res["rows"] = len(rows)
                if res.get("ok"):
                    installdir = probe.get("installdir") or probe.get("name") or aid
                    total = sum(int(r["size"]) for r in rows
                                if str(r.get("size", "")).isdigit())
                    download = sum(int(r["download"]) for r in rows
                                   if str(r.get("download", "")).isdigit())
                    acf = write_pending_appmanifest(int(aid), probe.get("name"), installdir,
                                                    probe.get("buildid", ""), rows,
                                                    total, download)
                    res["acf"] = acf
                    LOGS.add("manifests staged: " + ", ".join(str(d) for d in res["staged"]))
                    LOGS.add("library entry: " + str(acf.get("file") or acf.get("error")))
                else:
                    LOGS.add("manifest stage failed: " +
                             (", ".join(res.get("errors") or []) or "unknown"))
                FETCH_RESULT["manifests"] = res
            except Exception as exc:
                LOGS.add("manifest stage crashed: " + str(exc))
                FETCH_RESULT["manifests"] = {"ok": False, "error": str(exc)}

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "queued": True, "appid": aid, "os": want}

    def uninstall(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        LOGS.add(f"uninstall {aid}")
        quit_info = steam_quit()
        if not quit_info.get("ok"):
            LOGS.add("steam is still open - close it and try again")
            return {"ok": False, "error": quit_info.get("error") or "steam still running"}
        res = uninstall_game(int(aid), True)
        if res.get("ok"):
            LOGS.add("removed: " + (", ".join(res.get("removed") or []) or "nothing on disk"))
            steam_launch()
        else:
            LOGS.add("uninstall failed: " + str(res.get("error")))
            steam_launch()
        return res

    def remove_loader(self):
        LOGS.add("removing the loader from Steam.app")
        res = uninstall_loader()
        LOGS.add(("loader removed: " + ", ".join(res.get("removed") or []))
                 if res.get("ok") else ("loader removal failed: " + str(res.get("error"))))
        return res

    def remove(self, appid):
        try:
            aid = int(appid)
        except (TypeError, ValueError):
            return {"ok": False, "error": "appid must be numeric"}
        LOGS.add(f"remove {aid}")
        res = remove_lua(aid)
        LOGS.add("remove: " + json.dumps(res)[:300])
        return res

    def installed(self):
        return installed_list()

    def restart(self):
        LOGS.add("restarting steam...")
        res = restart_steam()
        if res.get("ok"):
            LOGS.add("steam closed and reopened")
        else:
            LOGS.add("steam restart failed: " + str(res.get("error") or "still running"))
        return res

    def loader(self):
        rep = loader_report()
        fams = rep.get("families", [])
        LOGS.add("loader check: " + (", ".join(f["family"] for f in fams)
                                     if fams else "none detected"))
        return rep

    def loader_install(self, family):
        family = (family or "macsteam").strip()
        LOGS.add(f"loader install --from {family} (auto download)...")
        if family == "steamtools":
            res = loader_install_steamtools()
        else:
            res = loader_install_macsteam()
        if res.get("ok"):
            LOGS.add(f"loader ready - {res.get('repo') or 'steamtools'} "
                     f"{res.get('version', '')} -> {res.get('config_app') or res.get('staged', '')}")
        else:
            LOGS.add("loader install failed: " + str(res.get("error", "")))
            for line in res.get("tried", []):
                LOGS.add("  " + line)
        return res

    def fetch(self, appid, user, password):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        if not _FETCH_LOCK.acquire(blocking=False):
            return {"ok": False, "error": "a fetch is already running"}
        u, p = (user or "").strip(), (password or "").strip()

        def worker():
            try:
                LOGS.add(f"fetch {aid} - probing depots + downloading content...")
                res = fetch_game(int(aid), "public", None, "mac", True,
                                 u, p, None, None, None,
                                 progress=lambda m: LOGS.add(str(m)))
                if res.get("ok"):
                    LOGS.add(f"fetch {aid} done - {len(res.get('results', []))} depot(s) "
                             f"at {res.get('root', '')}")
                else:
                    LOGS.add(f"fetch {aid} failed: {res.get('error') or res.get('error_code') or ''}"
                             + (f" - {res.get('hint')}" if res.get("hint") else ""))
            except Exception as exc:
                LOGS.add(f"fetch {aid} crashed: {exc}")
            finally:
                _FETCH_LOCK.release()

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "started": True, "appid": aid}

    def logs_since(self, index):
        lines, size = LOGS.since(index)
        return {"lines": lines, "index": size}

GUI_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LicenseTools</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#08090B;--panel:#111419;--line:#232830;--txt:#E7ECF3;--dim:#8A94A6;--acc:#2F6F4E;--warn:#E0A030}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:13px/1.45 -apple-system,"SF Pro Text",system-ui,sans-serif}
header{display:flex;gap:14px;align-items:baseline;padding:14px 18px;border-bottom:1px solid var(--line)}
header b{font-size:15px;letter-spacing:.06em}
header span{color:var(--dim);font-size:12px}
#h-new{margin-left:auto;display:none;background:#14202b;color:#7FC4FF;border:1px solid #26455C;border-radius:999px;padding:4px 12px;font-size:12px;cursor:pointer}
#h-new:hover{border-color:#3E6D8E;color:#A9DAFF}
nav{display:flex;gap:8px;padding:10px 18px;border-bottom:1px solid var(--line);align-items:center}
nav .sp{flex:1}
button{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:6px 12px;font:inherit;cursor:pointer}
button:hover{border-color:#3A424F}
button.pri{background:var(--acc);border-color:var(--acc)}
button:disabled{opacity:.5;cursor:default}
input,select{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font:inherit}
input:focus,select:focus{outline:none;border-color:var(--acc)}
main{padding:16px 18px;min-height:50vh}
section{display:none}
section.on{display:block}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.muted{color:var(--dim)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
#hits button{margin:2px 4px 2px 0}
.rowbtn{padding:3px 9px;font-size:12px}
.empty{color:var(--dim);padding:16px 0}
#status{padding:8px 18px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
#log{margin:0;padding:10px 18px 16px;max-height:170px;overflow:auto;background:#0B0D10;border-top:1px solid var(--line);font:11px/1.5 ui-monospace,Menlo,monospace;color:#9AA7BA;white-space:pre-wrap}
#probeout{margin-top:14px}
label.chk{display:flex;gap:6px;align-items:center;color:var(--dim)}
</style></head><body>
<header><b>LICENSETOOLS</b><span id="h-ver" class="mono" style="color:#7C8797">v{{VER}}</span><span id="h-root">detecting...</span><span id="h-loader"></span><button id="h-new" class="mono" style="display:none">update</button></header>
<nav>
  <button class="tab pri" data-t="grant">Grant</button>
  <button class="tab" data-t="library">Library</button>
  <button class="tab" data-t="installed">Installed</button>
  <button class="tab" data-t="loader">Loader</button>
  <button class="tab" data-t="maint">Maintenance</button>
  <span class="sp"></span>
  <button id="b-restart">Restart Steam</button>
</nav>
<main>
<section id="t-grant" class="on">
  <div class="toolbar">
    <input id="q" placeholder="search store..." style="width:230px">
    <button id="b-search">Search</button>
    <input id="appid" placeholder="appid" class="mono" style="width:110px">
    <input id="branch" value="public" class="mono" style="width:110px" title="branch">
    <select id="os">
      <option value="mac">mac depots</option>
      <option value="all">all depots</option>
      <option value="win">windows depots</option>
      <option value="linux">linux depots</option>
    </select>
    <label class="chk"><input type="checkbox" id="pin" checked> pin manifests</label>
    <button id="b-probe">Probe</button>
    <button id="b-grant" class="pri">Fetch &rarr; Library</button>
    <button id="b-install" class="pri">Download files</button>
  </div>
  <div class="toolbar">
    <span class="muted">content:</span>
    <button id="b-fetch">Fetch Content</button>
    <button id="b-lua">Import .lua</button>
    <span class="muted">Download files pulls the depots from the Steam CDN and verifies every file - no account, no code</span>
  </div>
  <div id="hits" class="muted"></div>
  <div id="probeout"></div>
</section>
<section id="t-library">
  <table><thead><tr><th>AppID</th><th>Name</th><th>Build</th><th>State</th></tr></thead>
  <tbody id="lib"></tbody></table>
</section>
<section id="t-installed">
  <table><thead><tr><th>AppID</th><th>File</th><th>Modified</th><th></th></tr></thead>
  <tbody id="inst"></tbody></table>
</section>
<section id="t-loader">
  <div class="toolbar">
    <button id="b-ldref">Refresh Status</button>
    <button id="b-ldmac" class="pri">Install macSteam Loader</button>
    <button id="b-ldst">Prepare SteamTools</button>
  </div>
  <div id="ldout"></div>
</section>
<section id="t-maint">
  <div class="toolbar">
    <input id="m-appid" placeholder="appid" style="width:120px">
    <button id="b-m-repair">Repair install record</button>
    <button id="b-m-del">Delete game</button>
    <button id="b-m-open">Open Steam folder</button>
    <button id="b-m-common">Open game files</button>
  </div>
  <div class="toolbar">
    <button id="b-m-paths">Show Steam paths</button>
    <button id="b-m-manifests">Stage manifests</button>
    <button id="b-m-update" class="pri">Update game</button>
    <button id="b-m-keys">Refresh depot keys</button>
  </div>
  <div class="toolbar">
    <button id="b-m-drm" class="pri">Skip Steam DRM</button>
    <button id="b-m-drmoff">Restore DRM files</button>
    <button id="b-m-unloader">Remove loader from Steam</button>
    <span class="muted">Removes the injected dylib, the plist entry, and the macsteam config - Steam goes back to a clean install</span>
  </div>
  <div class="toolbar">
    <button id="b-app-update" class="pri">Update this app</button>
    <span id="m-ver" class="muted mono">v{{VER}}</span>
    <span class="muted">Checks GitHub, downloads the new build, swaps the app in place, and reopens it - no deleting, no dragging</span>
  </div>
  <div id="pathsout"></div>
  <div class="muted" style="margin-top:8px">Delete game removes the install record, the game folder, the staging folder, the library cache entry, and the license line.</div>
</section>
</main>
<div id="status">booting...</div>
<pre id="log"></pre>
<script>
let api=null,logIdx=0;
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const say=t=>{$("#status").textContent=t};
function busy(on){["#b-probe","#b-grant","#b-install","#b-search","#b-restart","#b-fetch","#b-lua","#b-ldref","#b-ldmac","#b-ldst","#b-m-repair","#b-m-del","#b-m-open","#b-m-common","#b-m-unloader","#b-m-paths","#b-m-manifests","#b-m-update","#b-m-drm","#b-m-drmoff","#b-app-update","#h-new"].forEach(s=>{const el=$(s);if(el)el.disabled=on})}
async function call(fn,...args){if(!api)throw new Error("bridge not ready");return api[fn](...args)}
document.querySelectorAll("button.tab").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("button.tab").forEach(x=>x.classList.toggle("pri",x===b));
  document.querySelectorAll("main section").forEach(s=>s.classList.toggle("on",s.id==="t-"+b.dataset.t));
});
function renderLib(apps){
  $("#lib").innerHTML=(apps||[]).map(a=>
    `<tr><td class="mono">${a.appid}</td><td>${esc(a.name)}</td><td class="mono">${esc(a.buildid||"-")}</td><td class="muted">${a.state}</td></tr>`).join("")
    ||`<tr><td colspan="4" class="empty">no appmanifest files found</td></tr>`;
}
function renderInstalled(items){
  $("#inst").innerHTML=(items||[]).map(x=>{
    const btn=x.appid!=null?`<button class="rowbtn rm" data-id="${x.appid}">Remove</button>`:"";
    return `<tr><td class="mono">${x.appid==null?"-":x.appid}</td><td class="mono">${esc(x.file)}</td><td class="muted">${esc(x.mtime)}</td><td>${btn}</td></tr>`;
  }).join("")||`<tr><td colspan="4" class="empty">nothing installed yet</td></tr>`;
  document.querySelectorAll(".rm").forEach(b=>b.onclick=async()=>{
    try{
      const r=await call("remove",Number(b.dataset.id));
      say(r.ok?("removed "+b.dataset.id):("remove failed: "+(r.error||"")));
    }catch(e){say("remove failed: "+e)}
    await refreshInstalled();
  });
}
async function refreshInstalled(){
  try{renderInstalled((await call("installed")).items)}catch(e){say("installed list failed: "+e)}
}
function famDetail(f){
  if(f.family==="stplug-in")return `toml ${esc(f.toml)} - ${f.lua_files} lua files`;
  if(f.family==="macSteam")return `config ${esc(f.config||"-")} - dylib ${esc((f.dylibs||[]).join(", ")||"not injected yet")}`;
  return esc(JSON.stringify(f));
}
function renderLoader(r){
  const fams=r.families||[];
  $("#ldout").innerHTML=
    `<table><thead><tr><th>Loader</th><th>Detail</th></tr></thead><tbody>`+
    (fams.length?fams.map(f=>`<tr><td>${esc(f.family)}</td><td class="mono">${famDetail(f)}</td></tr>`).join("")
     :`<tr><td colspan="2" class="empty">${esc(r.hint||"no loader detected - press Install macSteam")}</td></tr>`)+
    `</tbody></table>`;
}
async function ldRefresh(){
  try{
    const r=await call("loader");
    renderLoader(r);
    $("#h-loader").textContent="loader: "+((r.families||[]).map(f=>f.family).join(", ")||"none");
  }catch(e){say("loader check failed: "+e)}
}
async function ldInstall(family){
  busy(true);say("installing "+family+" loader - downloading...");
  try{
    const r=await call("loader_install",family);
    if(!r.ok)return say("loader install failed: "+(r.error||""));
    say((r.injected?"loader injected - ":"loader staged at ")+
        (r.dylib||r.staged||"")+(r.version?" ("+r.version+")":""));
    if(r.next)say(r.next);
  }catch(e){say("loader install failed: "+e)}
  await ldRefresh();
  busy(false);
}
async function doInstall(){
  let id=$("#appid").value.trim();
  if(!/^\d+$/.test(id)){
    const term=id||$("#q").value.trim();
    if(!term)return say("type a game name or appid first");
    say("searching "+term+"...");
    const hits=await call("search",term);
    if(!hits||!hits.length)return say("no match for "+term);
    id=hits[0].appid;$("#appid").value=id;say("picked "+hits[0].name+" ("+id+")");
  }
  busy(true);
  try{
    const r=await call("install",id);
    if(!r.ok)return say("download failed to start: "+(r.error||""));
    say("pulling "+id+" from the Steam CDN - progress in the log...");
    await watchInstall();
  }catch(e){say("download failed: "+e)}
  finally{busy(false)}
}
async function watchInstall(){
  let r={ok:null};
  for(let i=0;i<3600;i++){
    await new Promise(z=>setTimeout(z,1500));
    r=await call("install_status");
    if(r&&r.ok!==null&&r.ok!==undefined)break;
  }
  if(!r||r.ok==null)return say("download still running - check the log");
  if(r.ok){
    say("installed: "+r.files+" file(s), "+Math.round((r.files_bytes||0)/1048576)+" MB - press Play");
    await refreshInstalled();
    try{renderPaths(await call("paths"))}catch(_){}
  }else{
    say("install failed at "+(r.stage||"?")+": "+(r.error||""));
  }
  return r;
}
async function doFetch(){
  const id=$("#appid").value.trim();
  if(!/^\d+$/.test(id))return say("appid must be numeric");
  busy(true);say("fetch "+id+" queued - progress shows in the log...");
  try{
    const r=await call("fetch",id,"","");
    if(!r.ok)say("fetch failed: "+(r.error||""));
  }catch(e){say("fetch failed: "+e)}
  busy(false);
}
async function doImportLua(){
  busy(true);say("pick the .lua file...");
  try{
    const r=await call("import_lua","");
    if(r.cancelled)say("cancelled");
    else if(r.ok)say(`lua imported for ${r.appid} - ${r.depots} depot(s), ${r.keys} key(s)`);
    else say("lua import failed: "+(r.error||""));
  }catch(e){say("lua import failed: "+e)}
  busy(false);
}
async function boot(){
  try{
    const d=await call("bootstrap");
    $("#h-root").textContent=d.root||"steam not detected";
    $("#h-loader").textContent="loader: "+d.loader;
    say(`library ${d.count} games - ${d.platform} - v${d.version}`);
    renderLib(d.library);
    renderInstalled(d.installed);
    await ldRefresh();
    checkAppUpdate();
  }catch(e){say("bootstrap failed: "+e)}
}
async function doSearch(){
  const term=$("#q").value.trim();
  if(!term)return say("type something to search");
  say("searching store...");
  try{
    const hits=await call("search",term);
    $("#hits").innerHTML=hits.map(h=>
      `<button class="rowbtn pick" data-id="${h.appid}">${esc(h.name)} - ${h.appid}</button>`).join("")
      ||"no hits";
    document.querySelectorAll(".pick").forEach(b=>b.onclick=()=>{
      $("#appid").value=b.dataset.id;$("#hits").textContent="";
      say("appid "+b.dataset.id+" loaded - probe or grant");
    });
    say(hits.length+" hits");
  }catch(e){say("search failed: "+e)}
}
async function doProbe(){
  const id=$("#appid").value.trim();
  if(!/^\d+$/.test(id))return say("appid must be numeric");
  busy(true);say("probing "+id+" via steamcmd (first run downloads it)...");
  $("#probeout").innerHTML="";
  try{
    const r=await call("probe",id,$("#branch").value||"public",$("#os").value);
    if(!r.ok)return say("probe failed: "+(r.error||""));
    say(`${r.rows.length} depots - build ${r.buildid||"?"} - branch ${r.branch}`);
    $("#probeout").innerHTML=
      `<table><thead><tr><th>Depot</th><th>OS</th><th>Manifest</th><th>Size</th></tr></thead><tbody>`+
      r.rows.map(x=>`<tr><td class="mono">${esc(x.depot)}</td><td>${esc(x.os)}</td>`+
        `<td class="mono">${esc(x.manifest||"-")}</td><td class="muted">${esc(x.size||"-")}</td></tr>`).join("")+
      `</tbody></table>`;
  }catch(e){say("probe failed: "+e)}
  busy(false);
}
async function doGrant(){
  let id=$("#appid").value.trim();
  busy(true);
  try{
    if(!/^\d+$/.test(id)){
      const term=id||$("#q").value.trim();
      if(!term){say("type a game name or appid first");return}
      say("searching "+term+"...");
      const hits=await call("search",term);
      if(!hits||!hits.length){say("no match for "+term);return}
      id=hits[0].appid;
      $("#appid").value=id;
      say("picked "+hits[0].name+" ("+id+")");
    }
    say("fetching "+id+" - loader check, license, library...");
    const r=await call("unlock",id,$("#branch").value||"public",$("#os").value);
    if(!r.ok){say("fetch failed at "+r.stage+": "+(r.error||""));return}
    say(id+" unlocked - "+r.steps.length+" steps ok - steam restarting");
    if(r.hint)say(r.hint);
    await refreshInstalled();
    await ldRefresh();
  }catch(e){say("fetch failed: "+e)}
  finally{busy(false)}
}
$("#b-search").onclick=doSearch;
$("#q").onkeydown=e=>{if(e.key==="Enter")doSearch()};
$("#b-probe").onclick=doProbe;
$("#b-grant").onclick=doGrant;
$("#b-install").onclick=doInstall;
$("#b-restart").onclick=async()=>{
  busy(true);say("restarting steam...");
  try{await call("restart");say("steam restarted")}catch(e){say("restart failed: "+e)}
  busy(false);
};
$("#b-ldref").onclick=ldRefresh;
$("#b-ldmac").onclick=()=>ldInstall("macsteam");
$("#b-ldst").onclick=()=>ldInstall("steamtools");
$("#b-fetch").onclick=doFetch;
$("#b-lua").onclick=doImportLua;
$("#b-m-open").onclick=async()=>{
  try{
    const r=await call("open_folder","steamapps");
    say(r.ok?("opened "+r.path):("open failed: "+(r.error||"")));
  }catch(e){say("open failed: "+e)}
};function renderPaths(p){
  const rows=[
    ["steam root",p.steam_root||("NOT FOUND (expected "+p.steam_root_expected+")")],
    ["steamapps",p.steamapps||"-"],
    ["common",p.common||"-"],
    ["libraries",(p.libraries||[]).join("  |  ")||"-"],
    ["Steam.app",p.steam_app||"not installed"],
    ["steam binary",p.steam_exe||"-"],
    ["macsteam config",(p.macsteam_config_exists?"ok  ":"missing  ")+p.macsteam_config],
    ["injected dylib",(p.dylib_exists?"ok  ":"missing  ")+p.dylib],
    ["depotcache",p.depotcache||"-"],
    ["manifests in depotcache",String(p.depotcache_manifests==null?0:p.depotcache_manifests)]
  ];
  $("#pathsout").innerHTML=`<table><thead><tr><th>Item</th><th>Path</th></tr></thead><tbody>`+
    rows.map(([k,v])=>`<tr><td>${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join("")+
    `</tbody></table>`;
}
$("#b-m-paths").onclick=async()=>{
  try{
    const p=await call("paths");
    renderPaths(p);
    say(p.steam_root_exists?"steam folder found":"steam folder not found - open Steam once");
  }catch(e){say("path check failed: "+e)}
};
$("#b-m-manifests").onclick=async()=>{
  const id=$("#m-appid").value.trim();
  if(!/^\d+$/.test(id))return say("appid must be numeric");
  busy(true);say("staging depot manifests for "+id+" - watch the log...");
  try{
    const q=await call("manifests",id,$("#os").value||"mac");
    if(!q.ok)return say("manifest stage failed: "+(q.error||""));
    let r={ok:null};
    for(let i=0;i<120;i++){
      await new Promise(z=>setTimeout(z,1000));
      r=await call("manifests_status");
      if(r&&r.ok!==null&&r.ok!==undefined)break;
    }
    if(!r||r.ok==null)return say("manifest stage still running - check the log");
    if(r.ok){
      say("manifests staged: "+(r.staged||[]).join(", ")+" - press Download files");
      try{renderPaths(await call("paths"))}catch(_){}
    }else{
      say("manifest stage failed: "+((r.errors||[]).join(" | ")||r.error||""));
    }
  }catch(e){say("manifest stage failed: "+e)}
  finally{busy(false)}
};
$("#b-m-update").onclick=async()=>{
  const id=$("#m-appid").value.trim();
  if(!/^\d+$/.test(id))return say("appid must be numeric");
  busy(true);say("checking "+id+" - only changed files get re-pulled...");
  try{
    const r=await call("update",id);
    if(!r.ok)return say("update failed to start: "+(r.error||""));
    await watchInstall();
  }catch(e){say("update failed: "+e)}
  finally{busy(false)}
};
$("#b-m-common").onclick=async()=>{  try{
    const r=await call("open_folder","common");
    say(r.ok?("opened "+r.path):("open failed: "+(r.error||"")));
  }catch(e){say("open failed: "+e)}
};
$("#b-m-keys").onclick=async()=>{
  busy(true);say("refreshing the depot key table...");
  try{
    const r=await call("refresh_keys");
    if(r.ok)say("depot key table: "+r.keys+" keys"+(r.status&&r.status.path?(" - "+r.status.path):""));
    else say("refresh failed: "+(r.error||"")+(r.status&&r.status.path?(" ["+r.status.path+"]"):""));
  }catch(e){say("refresh failed: "+e)}
  busy(false);
};
$("#b-m-drm").onclick=async()=>{
  const id=maintId();if(!id)return;
  busy(true);say("matching the steam library in "+id+" against what the game imports...");
  try{
    const r=await call("drm_patch",id);
    if(r.needs_tools)say("nothing changed - no build tools on this mac. the game still runs as it is; install them only if a game refuses to start");
    else if(r.skipped)say("this game ships no libsteam_api.dylib - nothing to replace");
    else if(r.ok)say("patched "+(r.patched||[]).length+" librar(y/ies) with "+(r.symbols||0)+" matched symbol(s)");
    else say("patch failed: "+(r.error||""));
  }catch(e){say("patch failed: "+e)}
  busy(false);
};
$("#b-m-drmoff").onclick=async()=>{
  const id=maintId();if(!id)return;
  busy(true);say("putting the original steam library back...");
  try{
    const r=await call("drm_restore",id);
    say(r.ok?("restored "+(r.restored||[]).length+" file(s)"):("restore failed: "+((r.errors||[]).join(" | ")||r.error||"")));
  }catch(e){say("restore failed: "+e)}
  busy(false);
};
function maintId(){
  const raw=($("#m-appid").value||$("#appid").value||"").trim();
  if(!/^\d+$/.test(raw)){say("type the appid first");return null}
  return raw;
}
$("#b-m-repair").onclick=async()=>{
  const id=maintId();if(!id)return;
  try{
    const r=await call("repair",id);
    say(r.ok?(`repair ${id}: ${r.files} files, record ${r.acf?"kept":"missing"}${r.stale_removed?", stale record cleared":""}`):("repair failed: "+(r.error||"")));
  }catch(e){say("repair failed: "+e)}
  await refreshInstalled();
};
$("#b-m-del").onclick=async()=>{
  const id=maintId();if(!id)return;
  if(!confirm("Delete game "+id+" and everything it downloaded?"))return;
  busy(true);
  try{
    const r=await call("uninstall",id);
    say(r.ok?(`deleted ${id} - ${(r.removed||[]).length} paths removed`):("delete failed: "+(r.error||"")));
  }catch(e){say("delete failed: "+e)}
  busy(false);
  await refreshInstalled();
};
$("#b-m-unloader").onclick=async()=>{
  if(!confirm("Remove the loader from Steam.app? Steam goes back to a clean install."))return;
  busy(true);
  try{
    const r=await call("remove_loader");
    say(r.ok?"loader removed - reopen Steam":("remove failed: "+(r.error||"")));
  }catch(e){say("remove failed: "+e)}
  busy(false);
  await ldRefresh();
};
async function checkAppUpdate(){
  try{
    const u=await call("app_update");
    if(!u||!u.ok||!u.newer)return;
    const el=$("#h-new");
    el.textContent="v"+u.latest+" available";
    el.style.display="inline-block";
    el.onclick=installAppUpdate;
    say("a newer build is out: v"+u.latest+" - Maintenance, then Update this app");
  }catch(e){}
}
async function installAppUpdate(){
  busy(true);say("checking for a newer build of this app...");
  try{
    const r=await call("app_update_install");
    if(r.relaunching){say("new build staged ("+r.from+" to "+r.to+") - the app reopens in a second");return}
    if(r.up_to_date){say("already on the newest build (v"+r.current+")")}
    else{say("update failed: "+(r.error||"unknown"))}
  }catch(e){say("update failed: "+e)}
  busy(false);
}
$("#b-app-update").onclick=installAppUpdate;
$("#h-new").onclick=installAppUpdate;
async function pollLogs(){
  if(!api)return;
  try{
    const r=await call("logs_since",logIdx);
    if(r.lines&&r.lines.length){
      $("#log").textContent+=r.lines.join("\n")+"\n";
      logIdx=r.index;
      const el=$("#log");el.scrollTop=el.scrollHeight;
    }
  }catch(e){}
}
function ready(){api=window.pywebview.api;boot();setInterval(pollLogs,1200);pollLogs()}
window.addEventListener("pywebviewready",ready);
if(window.pywebview&&window.pywebview.api)ready();
</script></body></html>"""

def run_gui():
    try:
        import webview
    except ImportError:
        sys.exit("pywebview missing - pip install 'pywebview>=5.0'")
    index = CACHE_DIR / "ui" / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(GUI_HTML.replace("{{VER}}", APP_VERSION), encoding="utf-8")
    threading.Thread(target=_provision_quiet, daemon=True).start()
    webview.create_window(
        "LicenseTools", str(index), js_api=Api(),
        width=1180, height=800, min_size=(1040, 700),
        background_color="#08090B", text_select=False, confirm_close=False)
    webview.start(debug=os.environ.get("LICETOOLS_DEBUG") == "1")

def _provision_quiet():
    try:
        res = ensure_tools()
        for name, r in res.items():
            LOGS.add(f"tool {name}: " + ("ok " + str(r.get("tool", "")) if r.get("ok")
                                         else "failed " + str(r.get("error", ""))))
    except Exception as exc:
        LOGS.add(f"tool provisioning failed: {exc}")
    try:
        table = depot_key_db(progress=lambda m: LOGS.add(str(m)))
        if table:
            LOGS.add(f"depot key table ready: {len(table)} keys")
    except Exception as exc:
        LOGS.add(f"depot key table failed: {exc}")

def cmd_gui(_a):
    run_gui()
    return 0

# ---------- argparse ----------

def build_argparser():
    ap = argparse.ArgumentParser(
        prog="licensetools",
        description="Steam license kit for macOS: probe appinfo, grant stplug-in lua, pin manifests")
    ap.add_argument("--selftest", action="store_true", help="run offline fixture checks and exit")
    ap.add_argument("--version", action="version", version="licensetools " + APP_VERSION)
    ap.add_argument("--emit-stub", metavar="PATH",
                    help="write the stand-in library source to PATH and exit")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("library").set_defaults(fn=cmd_library)
    sub.add_parser("restart").set_defaults(fn=cmd_restart)
    sub.add_parser("ensure").set_defaults(fn=cmd_ensure)
    sub.add_parser("gui").set_defaults(fn=cmd_gui)

    p = sub.add_parser("loader", help="detect or install a license loader")
    p.add_argument("action", choices=("status", "install"))
    p.add_argument("--from", dest="family", default="macsteam",
                   choices=("macsteam", "steamtools"))
    p.set_defaults(fn=cmd_loader)

    p = sub.add_parser("probe", help="read depot/manifest gids for an appid")
    p.add_argument("appid")
    p.add_argument("--branch", default="public")
    p.add_argument("--os", dest="os", default="all", choices=("mac", "win", "linux", "all"))
    p.add_argument("--appinfo", default="", help="parse a saved steamcmd dump instead of running it")
    p.add_argument("--csv", default="", help="also write rows to this CSV")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("grant", help="generate + install stplug-in lua for an appid")
    p.add_argument("appid")
    p.add_argument("--branch", default="public")
    p.add_argument("--os", dest="os", default="mac", choices=("mac", "win", "linux", "all"))
    p.add_argument("--no-shared", dest="no_shared", action="store_true")
    p.add_argument("--no-pin", dest="no_pin", action="store_true",
                   help="comment out setManifestid so steam tracks the live branch")
    p.add_argument("--appinfo", default="", help="parse a saved steamcmd dump instead of running it")
    p.add_argument("--out", default="", help="also write the lua to this path")
    p.add_argument("--dry-run", action="store_true", help="print the lua and exit")
    p.set_defaults(fn=cmd_grant)

    p = sub.add_parser("remove", help="delete an installed stplug-in lua")
    p.add_argument("appid")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("install", help="install a local .lua / .manifest / .zip")
    p.add_argument("file")
    p.add_argument("--appid", default="")
    p.add_argument("--branch", default="public")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("import-lua",
                       help="install a .lua and copy its depot keys into the loader config")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import_lua)

    p = sub.add_parser("manifests", help="stage depot manifests into steamapps/depotcache")
    p.add_argument("appid")
    p.add_argument("--branch", default="public")
    p.add_argument("--os", default="mac")
    p.set_defaults(fn=cmd_manifests)

    p = sub.add_parser("keydb", help="show or refresh the depot key table")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_keydb)

    p = sub.add_parser("keys", help="resolve depot decryption keys for an appid")
    p.add_argument("appid")
    p.add_argument("--branch", default="public")
    p.add_argument("--os", dest="os", default="mac", choices=("mac", "win", "linux", "all"))
    p.add_argument("--no-shared", dest="no_shared", action="store_true")
    p.set_defaults(fn=cmd_keys)

    p = sub.add_parser("fetch", help="download depot content with DepotDownloader")
    p.add_argument("appid")
    p.add_argument("--branch", default="public")
    p.add_argument("--os", dest="os", default="mac", choices=("mac", "win", "linux", "all"))
    p.add_argument("--no-shared", dest="no_shared", action="store_true")
    p.add_argument("--appinfo", default="")
    p.add_argument("--user", default="")
    p.add_argument("--pass", dest="pass_", default="")
    p.add_argument("--out", default="")
    p.add_argument("--arg", action="append", default=[],
                   help="extra DepotDownloader arg, repeatable (e.g. --arg -depotkey --arg KEY)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("settings")
    p.add_argument("--steam-path", default=None)
    p.add_argument("--token", default="")
    p.set_defaults(fn=cmd_settings)

    p = sub.add_parser("app-update", help="check for or install a newer build of this app")
    p.add_argument("--apply", action="store_true",
                   help="download the newest build, swap the running app, and reopen it")
    p.set_defaults(fn=cmd_app_update)

    return ap

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = build_argparser()
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.emit_stub:
        Path(args.emit_stub).write_text(_stub_source([], 0), encoding="utf-8")
        print(args.emit_stub)
        return
    fn = getattr(args, "fn", None)
    if fn is None:
        if getattr(sys, "frozen", False):
            run_gui()
            return
        ap.print_help()
        return
    sys.exit(fn(args) or 0)

if __name__ == "__main__":
    main()
