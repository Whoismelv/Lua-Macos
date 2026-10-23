#!/usr/bin/env python3
# language: python3.9+, file: licensetools_mac.py, runtime: stdlib-only (pywebview>=5.0 for gui)
# Steam license kit for macOS: probe public appinfo, write stplug-in lua, pin manifests.
# Mac paths first, Windows fallback kept so the same file runs on the bench.
import argparse
import gzip
import json
import os
import re
import shutil
import ssl
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
APP_VERSION = "1.2.0"
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
        return None if r is None else r / "depotcache"

    def steam_exe(self):
        r = self.root()
        if r is None:
            return None
        if sys.platform == "darwin":
            macos = Path("/Applications/Steam.app/Contents/MacOS")
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
        return subprocess.run(["pgrep", "-f", "Steam.app/Contents/MacOS/steam"],
                              capture_output=True).returncode == 0

    def stop(self):
        if winreg is not None:
            subprocess.run(["taskkill", "/F", "/IM", "steam.exe", "/T"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "Steam.app/Contents/MacOS/steam"], capture_output=True)
        time.sleep(1.2)

    def start(self):
        if winreg is not None:
            exe = self.steam_exe()
            if exe and exe.exists():
                subprocess.Popen([str(exe)], cwd=str(self.root()))
        else:
            subprocess.Popen(["open", "-a", "Steam"])

def restart_steam():
    ctx = SteamCtx()
    ctx.stop()
    ctx.start()
    return {"ok": True, "restarted": True}

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
    app = Path("/Applications/Steam.app")
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
    }

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
        rows.append({"depot": str(depot_id), "os": osname, "manifest": gid,
                     "size": size if size.isdigit() else "", "branch": branch})
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

def _http_bytes(url, timeout=180):
    base = {"User-Agent": "licensetools/1.2",
            "Accept": "application/vnd.github.raw, application/json, */*"}
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
        for f in st.glob("*.lua"):
            stat = f.stat()
            items.append({"appid": app_id_from_name(f), "file": f.name, "bytes": stat.st_size,
                          "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))})
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
            apps.append({"appid": appid, "name": d.get("name", ""),
                         "state": int(d.get("StateFlags", "0") or 0),
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
                    t.extractall(dest)
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
    for p in dest.rglob("*"):
        if p.is_file():
            try:
                os.chmod(p, 0o755)
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
    if not tool:
        return {"ok": False, "error": "steamcmd unavailable and the automatic download failed"}
    args = [str(tool), "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
            "+login", "anonymous", "+app_info_print", str(appid), "+quit"]
    try:
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

    def check(name, cond, detail=""):
        if not cond:
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

    print(json.dumps({"ok": True, "checks": checks}, indent=2))
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

def read_appmanifest(appid):
    sa = steam_apps_dir()
    if sa is None:
        return None
    f = sa / f"appmanifest_{int(appid)}.acf"
    return parse_acf(f) if f.exists() else None

def write_appmanifest(appid, name, installdir, buildid, depots, size_on_disk=0):
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    now = int(time.time())
    lines = ['"AppState"', "{"]
    lines += [f'\t"appid"\t\t"{int(appid)}"',
              '\t"Universe"\t\t"1"',
              f'\t"name"\t\t"{name or appid}"',
              '\t"StateFlags"\t\t"4"',
              f'\t"installdir"\t\t"{installdir}"',
              f'\t"LastUpdated"\t\t"{now}"',
              '\t"LastPlayed"\t\t"0"',
              f'\t"SizeOnDisk"\t\t"{int(size_on_disk)}"',
              f'\t"StagingPath"\t\t"{sa / "downloading" / str(int(appid))}"',
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
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"ok": True, "file": str(path)}

def install_to_library(appid, branch="public", want_os="mac", user="", password="",
                       progress=None):
    aid = int(appid)
    probe = probe_app(aid, branch)
    if not probe.get("ok"):
        return {"ok": False, "stage": "probe", **probe}
    rows = select_rows(probe["rows"], want_os, False)
    rows = [r for r in rows if r["manifest"]]
    if not rows:
        return {"ok": False, "error": f"no depot manifests for os={want_os}"}
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found - install Steam first"}
    installdir = probe.get("installdir") or probe.get("name") or str(aid)
    target = sa / "common" / installdir
    depots = [int(r["depot"]) for r in rows]
    keys = keys_for_depots(depots, progress)
    cfg = macsteam_config_add(aid, depots=depots, keys=keys)
    missing = [d for d in depots if d not in keys]
    if progress:
        progress(f"loader config: {len(depots)} depot(s), {len(keys)} key(s)")
        if missing:
            progress("no depot key found for: " + ", ".join(str(d) for d in missing))
    if not (user and password):
        if progress:
            progress("handing the install to Steam - it pulls the depots itself")
        restart_steam()
        fb = steam_install_url(aid)
        return {"ok": True, "appid": str(aid), "mode": "steam", "dir": str(target),
                "depots": depots, "keys": len(keys), "missing_keys": missing,
                "config": cfg, "steam": fb,
                "note": "Steam downloads into steamapps/common and writes the acf"}
    target.mkdir(parents=True, exist_ok=True)
    res = fetch_game(aid, branch, rows, want_os, True, user, password,
                     str(target), None, None, progress, flat=True)
    if not res.get("ok"):
        res["stage"] = "download"
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
        return {"ok": False, "stage": "download", "target": str(target),
                "error": "depot download wrote no files",
                "hint": "no content came back - press Download files again, or let Steam install it"}
    acf = write_appmanifest(aid, probe.get("name"), installdir,
                            probe.get("buildid", ""), rows, size)
    return {"ok": bool(acf.get("ok")), "appid": str(aid), "dir": str(target),
            "installdir": installdir, "files_bytes": size,
            "depots": [r["depot"] for r in rows],
            "manifest": acf.get("file"), "error": acf.get("error")}

def repair_install_state(appid):
    sa = steam_apps_dir()
    if sa is None:
        return {"ok": False, "error": "steam library not found"}
    acf = read_appmanifest(appid)
    installdir = (acf or {}).get("installdir", "")
    common = sa / "common" / installdir if installdir else None
    files = 0
    if common and common.is_dir():
        files = sum(1 for p in common.rglob("*") if p.is_file())
    stale = bool(acf) and files == 0
    if stale:
        f = sa / f"appmanifest_{int(appid)}.acf"
        try:
            f.unlink()
        except OSError:
            pass
    return {"ok": True, "acf": bool(acf), "installdir": installdir,
            "files": files, "stale_removed": stale}

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
    steam_app = Path("/Applications/Steam.app")
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
    steam_app = Path("/Applications/Steam.app")
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
    steam_app = Path("/Applications/Steam.app")
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
            LOGS.add("steam restarting - game will show in your library")
            restart_steam()
            steps.append({"step": "restart", "ok": True})
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
            return {"ok": True, "appid": aid, "steps": steps,
                    "hint": "in your library now - press Download files to put the game on disk"}
        res = self.grant(aid, branch, want_os, True)
        steps.append({"step": "grant", "ok": res.get("ok"),
                      "dest": res.get("dest"), "depots": res.get("depots"),
                      "error": res.get("error")})
        if not res.get("ok"):
            return {"ok": False, "stage": "grant", "error": res.get("error"),
                    "steps": steps}
        LOGS.add("steam restarting - game will show in your library")
        restart_steam()
        steps.append({"step": "restart", "ok": True})
        return {"ok": True, "appid": aid, "steps": steps,
                "hint": "game should appear in your library now"}

    def install(self, appid, user="", password=""):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        if not _FETCH_LOCK.acquire(blocking=False):
            return {"ok": False, "error": "a download is already running"}
        u, p = (user or "").strip(), (password or "").strip()

        def worker():
            try:
                LOGS.add(f"install {aid} - downloading game content into the steam library...")
                rep = repair_install_state(int(aid))
                if rep.get("stale_removed"):
                    LOGS.add("cleared a stale install record with no files")
                res = install_to_library(int(aid), "public", "mac", u, p,
                                         progress=lambda m: LOGS.add(str(m)))
                if res.get("ok"):
                    if res.get("mode") == "steam":
                        LOGS.add("steam is downloading the depots itself - watch the downloads page")
                        if res.get("missing_keys"):
                            LOGS.add("no key for: " + ", ".join(str(d) for d in res["missing_keys"]))
                    else:
                        LOGS.add(f"install {aid} done - {res.get('files_bytes', 0)} bytes "
                                 f"in {res.get('dir')}")
                        LOGS.add("restarting steam so the play button uses the new files")
                        restart_steam()
                else:
                    LOGS.add("install failed: " + str(res.get("error")))
                    if res.get("hint"):
                        LOGS.add("hint: " + str(res.get("hint")))
                    fb = steam_install_url(int(aid))
                    if fb.get("ok"):
                        LOGS.add("asked steam to download it instead")
                FETCH_RESULT["install"] = res
            except Exception as exc:
                LOGS.add("install crashed: " + str(exc))
                FETCH_RESULT["install"] = {"ok": False, "error": str(exc)}
            finally:
                _FETCH_LOCK.release()

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "queued": True, "appid": aid}

    def install_status(self):
        return FETCH_RESULT.get("install") or {"ok": None, "running": _FETCH_LOCK.locked()}

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
        p = str(path or "").strip()
        if not p:
            p = self._pick_lua()
            if not p:
                return {"ok": False, "cancelled": True}
        res = import_lua_file(p)
        LOGS.add(("lua " + str(res.get("appid")) + " imported")
                 if res.get("ok") else ("lua import failed: " + str(res.get("error"))))
        return res

    def repair(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        res = repair_install_state(int(aid))
        LOGS.add("repair " + aid + ": " + json.dumps(res)[:240])
        return res

    def uninstall(self, appid):
        aid = str(appid or "").strip()
        if not aid.isdigit():
            return {"ok": False, "error": "appid must be numeric"}
        LOGS.add(f"uninstall {aid}")
        res = uninstall_game(int(aid), True)
        if res.get("ok"):
            LOGS.add("removed: " + (", ".join(res.get("removed") or []) or "nothing on disk"))
            restart_steam()
        else:
            LOGS.add("uninstall failed: " + str(res.get("error")))
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
        LOGS.add("steam restarted")
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
<header><b>LICENSETOOLS</b><span id="h-ver" class="mono" style="color:#7C8797">v{{VER}}</span><span id="h-root">detecting...</span><span id="h-loader"></span></header>
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
    <span class="muted">depot content:</span>
    <input id="f-user" placeholder="steam user" style="width:160px">
    <input id="f-pass" placeholder="password" type="password" style="width:160px">
    <button id="b-fetch">Fetch Content</button>
    <button id="b-lua">Import .lua</button>
    <span class="muted">Download files writes the depot keys and lets Steam install it; add a login to pull with DepotDownloader instead</span>
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
    <button id="b-m-keys">Refresh depot keys</button>
    <button id="b-m-unloader">Remove loader from Steam</button>
    <span class="muted">Removes the injected dylib, the plist entry, and the macsteam config - Steam goes back to a clean install</span>
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
function busy(on){["#b-probe","#b-grant","#b-install","#b-search","#b-restart","#b-fetch","#b-lua","#b-ldref","#b-ldmac","#b-ldst","#b-m-repair","#b-m-del","#b-m-open","#b-m-common","#b-m-unloader","#b-m-paths"].forEach(s=>{const el=$(s);if(el)el.disabled=on})}
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
    const r=await call("install",id,$("#f-user").value,$("#f-pass").value);
    if(!r.ok)return say("download failed to start: "+(r.error||""));
    say("downloading "+id+" into steamapps/common - watch the log");
    await new Promise(res=>setTimeout(res,1200));
  }catch(e){say("download failed: "+e)}
  finally{busy(false)}
}
async function doFetch(){
  const id=$("#appid").value.trim();
  if(!/^\d+$/.test(id))return say("appid must be numeric");
  busy(true);say("fetch "+id+" queued - progress shows in the log...");
  try{
    const r=await call("fetch",id,$("#f-user").value,$("#f-pass").value);
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
      r.rows.map(x=>`<tr><td class="mono">${x.depot}</td><td>${x.os}</td>`+
        `<td class="mono">${x.manifest||"-"}</td><td class="muted">${x.size||"-"}</td></tr>`).join("")+
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
    ["injected dylib",(p.dylib_exists?"ok  ":"missing  ")+p.dylib]
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
$("#b-m-common").onclick=async()=>{
  try{
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
