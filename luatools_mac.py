#!/usr/bin/env python3
# language: python3.9+, file: luatools_mac.py, runtime: stdlib only
# reversed from LuaTools 1.3.2 IL (LuaTools.dll) and ported to macOS paths first, Windows fallback kept.
import argparse, base64, itertools, json, os, re, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request, uuid, webbrowser, zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    if os.name == "nt":
        import winreg
    else:
        winreg = None
except ImportError:
    winreg = None

# ---------- paths ----------

ST_CACHE_DIR = Path.home() / ".luatools-mac"
ST_VAULT_DIR = ST_CACHE_DIR / "vault"
ST_FRONTEND_DIR = ST_CACHE_DIR / "plugin"
SETTINGS_PATH = ST_CACHE_DIR / "settings.json"
LOADED_PATH = ST_CACHE_DIR / "loaded.json"

CDP_PORT = 8080
BRIDGE_PORT = 6767
CDP_MARKER = ".cef-enable-remote-debugging"
CDP_WIN_JUNCTION_TARGET = "C:\\fuckass\\folder\\that\\shall\\never\\exist\\die\\millennium"
UA_BACKEND_CHECK = "secretgoonpoon"
API_BASE = "https://lua.tools"
CHECK_APIS_URL = "http://167.235.229.108/check_apis?appid="

BRIDGE_MAP = {
    "HasLuaToolsForApp": ("GET", "/has/{appid}"),
    "CheckApisForApp": ("POST", "/check-sources/{appid}"),
    "StartAddViaLuaToolsFromUrl": ("POST", "/download/{appid}"),
    "GetAddViaLuaToolsStatus": ("GET", "/download-status/{appid}"),
    "CancelAddViaLuaTools": ("POST", "/cancel/{appid}"),
    "DeleteLuaToolsForApp": ("POST", "/remove/{appid}"),
    "StartLuaToolsAdd": ("POST", "/add/{appid}"),
    "GetLuaToolsAddStatus": ("GET", "/add-status/{appid}"),
    "PickLuaToolsAddSource": ("POST", "/add-source/{appid}"),
    "OpenSettings": ("POST", "/open/settings"),
    "OpenFix": ("POST", "/open/fix/{appid}"),
    "RestartSteam": ("POST", "/restart-steam"),
    "OpenExternalUrl": ("POST", "/open-url"),
    "CheckForUpdatesNow": ("POST", "/check-updates"),
    "ReadLoadedApps": ("GET", "/loaded-apps"),
    "DismissLoadedApps": ("POST", "/loaded-apps"),
    "GetApiList": ("GET", "/api-list"),
    "GetIconDataUrl": ("GET", "/icon"),
    "GetGamesDatabase": ("GET", "/games-database"),
    "Logger": ("POST", "/log"),
}

APPID_FILE_RE = re.compile(r"^\s*(\d+)")
BUILDID_FILE_RE = re.compile(r"^\s*\d+_(\d+)\s*$")
ADDAPPID_RE = re.compile(
    r'addappid\s*\(\s*(\d+)\s*(?:,\s*\d+\s*(?:,\s*"([^"]*)")?)?\s*\)[ \t]*(?:--[ \t]*(.*?)[ \t]*)?$',
    re.I)
SETMANIFEST_RE = re.compile(r'setManifestid\s*\(\s*(\d+)\s*,\s*"(\d+)"\s*(?:,\s*(\d+))?', re.I)
COMMENT_TAIL_RE = re.compile(r"\s*\(\d+\)\s*\S*\s*$")
SETMANIFEST_LINE_RE = re.compile(r"^(\s*)(setManifestid\s*\()", re.I)

# ---------- settings / state ----------

def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}

def save_settings(s):
    ST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))

def load_json_list(path):
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []

def save_json_list(path, vals):
    ST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vals, indent=2))

def record_loaded(appid):
    loaded = load_json_list(LOADED_PATH)
    if appid not in loaded:
        loaded.append(appid)
        save_json_list(LOADED_PATH, loaded)

def stamp_now(path):
    now = time.time()
    try:
        os.utime(path, (now, now))
    except OSError:
        pass

def app_id_from_name(p):
    m = APPID_FILE_RE.match(Path(p).stem)
    return int(m.group(1)) if m else None

def build_id_from_name(p):
    m = BUILDID_FILE_RE.match(Path(p).stem)
    return m.group(1) if m else None

# ---------- steam discovery (mac first, win fallback) ----------

class SteamCtx:
    def __init__(self):
        self.settings = load_settings()

    def root(self):
        override = (self.settings.get("steam_path") or "").strip()
        if override:
            return Path(override).expanduser().resolve()
        top = Path.home() / "Library" / "Application Support" / "Steam"
        if sys.platform == "darwin":
            return top
        if winreg is not None:
            raw = self._win_reg()
            if raw:
                return Path(raw).resolve()
        return top if top.is_dir() else None

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

    def st_plugin_dir(self):
        r = self.root()
        return None if r is None else r / "config" / "stplug-in"

    def depot_cache_dir(self):
        r = self.root()
        return None if r is None else r / "depotcache"

    def legacy_depot_cache_dir(self):
        r = self.root()
        return None if r is None else r / "config" / "depotcache"

    def steam_exe(self):
        r = self.root()
        if r is None:
            return None
        if sys.platform == "darwin":
            return Path("/Applications/Steam.app/Contents/MacOS/steam")
        return r / "steam.exe"

    def valid(self):
        r = self.root()
        if r is None:
            return False
        if winreg is not None:
            exe = self.steam_exe()
            return bool(exe and exe.exists())
        return (r / "config").is_dir()

    def steam_running(self):
        if winreg is not None:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                                 capture_output=True, text=True)
            return "steam.exe" in out.stdout.lower()
        return subprocess.run(["pgrep", "-f", "Steam.app/Contents/MacOS/steam"],
                              capture_output=True).returncode == 0

    def stop_steam(self):
        if winreg is not None:
            subprocess.run(["taskkill", "/F", "/IM", "steam.exe", "/T"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "Steam.app/Contents/MacOS/steam"], capture_output=True)
        time.sleep(1.2)

    def start_steam(self):
        if winreg is not None:
            exe = self.steam_exe()
            if exe and exe.exists():
                subprocess.Popen([str(exe)], cwd=str(self.root()))
        else:
            subprocess.Popen(["open", "-a", "Steam"])

# ---------- opensteamtool.toml editor (line-parity with C# writer) ----------

def _is_header_named(line, name):
    t = line.strip()
    return (not t.startswith("#")) and re.match(r"^\[\s*" + re.escape(name) + r"\s*\]", t) is not None

def _is_any_header(line):
    t = line.strip()
    return (not t.startswith("#")) and re.match(r"^\[[^\\[].*\]", t) is not None

def toml_ensure_lua_path(root: Path):
    path = root / "opensteamtool.toml"
    wanted = 'paths = ["config/stplug-in"]'
    if not path.exists():
        path.write_text("[lua]\n" + wanted + "\n")
        return
    lines = path.read_text().splitlines()
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
        m = re.match(r"^paths\s*=", t)
        if not m:
            continue
        if re.search(r"config[/\\]+stplug-in", t, re.I):
            return
        end = t.rfind("]")
        if end < 0:
            return
        prefix, tail = t[:end].rstrip(), t[end:]
        sep = " " if prefix.endswith("[") else ", "
        indent = raw[:len(raw) - len(raw.lstrip())]
        lines[i] = indent + prefix + sep + '"config/stplug-in"' + tail
        path.write_text("\n".join(lines) + "\n")
        return

def toml_set_cloud(root: Path, enabled):
    path = root / "opensteamtool.toml"
    val = "true" if enabled else "false"
    if not path.exists():
        path.write_text("[cloud]\nenabled = " + val + "\n")
        return
    lines = path.read_text().splitlines()
    idx = next((i for i, l in enumerate(lines) if _is_header_named(l, "cloud")), None)
    if idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[cloud]", "enabled = " + val]
    else:
        nxt = next((i for i, l in enumerate(lines[idx + 1:], idx + 1) if _is_any_header(l)), len(lines))
        placed = False
        for i in range(idx + 1, nxt):
            raw = lines[i]
            t = raw.strip()
            if t and not t.startswith("#") and re.match(r"^enabled\s*=", t):
                indent = raw[:len(raw) - len(raw.lstrip())]
                lines[i] = indent + "enabled = " + val
                placed = True
        if not placed:
            lines.insert(idx + 1, "enabled = " + val)
    path.write_text("\n".join(lines) + "\n")

# ---------- CDP marker (junction hack on Win, plain file on mac) ----------

def _is_reparse(p: Path):
    try:
        return p.is_symlink() or (os.path.realpath(str(p)) != os.path.abspath(str(p)))
    except OSError:
        return False

def ensure_cdp_marker(root: Path):
    path = root / CDP_MARKER
    if winreg is not None:
        if _is_reparse(path):
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
        subprocess.run(["cmd.exe", "/c", "mklink", "/j", str(path), CDP_WIN_JUNCTION_TARGET],
                       capture_output=True)
    else:
        if not path.exists():
            path.write_text("")

def remove_cdp_marker(root: Path):
    path = root / CDP_MARKER
    if not path.exists() and not path.is_dir():
        return
    if winreg is not None and _is_reparse(path):
        subprocess.run(["cmd.exe", "/c", "rmdir", str(path)], capture_output=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink()

# ---------- lua parser (grammar parity with LuaFileParser) ----------

def clean_comment(raw):
    if raw is None or not raw.strip():
        return None
    t = raw.strip()
    if any(k in t.lower() for k in ("setmanifestid", "addappid", "addtoken")):
        return None
    t = COMMENT_TAIL_RE.sub("", t).strip()
    return t or None

def parse_lua(text, appid_from_name=0):
    enabled, key_by_id, comment_by_id, manifests = [], {}, {}, {}
    commented_manifests, sizes = {}, {}
    disabled, disabled_key_by_id = [], {}
    for rawline in text.split("\n"):
        line = rawline.strip()
        is_comment = line.startswith("--")
        body = line.lstrip(" -") if is_comment else line
        m = SETMANIFEST_RE.search(body)
        if m and m.group(1).isdigit():
            depot = int(m.group(1))
            (commented_manifests if is_comment else manifests)[depot] = m.group(2)
            if m.group(3) and int(m.group(3)) > 0:
                sizes.setdefault(depot, int(m.group(3)))
        m = ADDAPPID_RE.match(body)
        if not m or not m.group(1).isdigit():
            continue
        i = int(m.group(1))
        key = m.group(2) or None
        cmt = clean_comment(m.group(3))
        if cmt and (i not in comment_by_id or len(cmt) > len(comment_by_id[i])):
            comment_by_id[i] = cmt
        if is_comment:
            if i in disabled_key_by_id:
                prev = disabled_key_by_id[i]
                disabled_key_by_id[i] = prev or key
            else:
                disabled_key_by_id[i] = key
                disabled.append(i)
        else:
            if i not in key_by_id:
                key_by_id[i] = key
                enabled.append(i)
            else:
                key_by_id[i] = key_by_id[i] or key
    entries = [dict(id=i, key=key_by_id.get(i), manifest=manifests.get(i),
                    commented=commented_manifests.get(i), comment=comment_by_id.get(i),
                    size=sizes.get(i)) for i in enabled]
    main_app = entries[0]["id"] if entries else appid_from_name
    return {"main_app": main_app, "entries": entries, "manifests": manifests,
            "commented_manifests": commented_manifests, "disabled": disabled, "sizes": sizes}

def comment_out_pins(text):
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("--"):
            out.append(line)
        else:
            out.append(SETMANIFEST_LINE_RE.sub("\\1-- \\2", line))
    return "\n".join(out)

# ---------- vault ----------

def vault_write(appid, build_id, text, source=None):
    d = ST_VAULT_DIR / str(appid)
    d.mkdir(parents=True, exist_ok=True)
    if build_id:
        (d / f"{build_id}.lua").write_text(text, encoding="utf-8")
        idx_path = d / "index.json"
        try:
            idx = json.loads(idx_path.read_text())
        except Exception:
            idx = {}
        idx.setdefault("builds", {})[build_id] = {"source": source, "ts": time.time()}
        idx_path.write_text(json.dumps(idx, indent=2))
    else:
        (d / "default.lua").write_text(text, encoding="utf-8")

def vault_list(appid=None):
    def one(p):
        idx = {}
        f = p / "index.json"
        if f.exists():
            try:
                idx = json.loads(f.read_text())
            except Exception:
                idx = {}
        return {"builds": sorted(idx.get("builds", {}).keys()),
                "default": (p / "default.lua").exists()}
    if appid:
        d = ST_VAULT_DIR / str(appid)
        if not d.is_dir():
            return {"appid": appid, "builds": [], "default": False}
        return {"appid": appid, **one(d)}
    out = {}
    if ST_VAULT_DIR.is_dir():
        for p in ST_VAULT_DIR.iterdir():
            if p.is_dir() and p.name.isdigit():
                out[int(p.name)] = one(p)
    return out

# ---------- installers (parity with LuaInstaller) ----------

def install_lua(src_path, appid=None, force_locked=False, source=None):
    ctx = SteamCtx()
    st = ctx.st_plugin_dir()
    if st is None:
        return {"ok": False, "error": "steam root not configured — run 'detect'"}
    appid = appid or app_id_from_name(src_path)
    if not appid:
        return {"ok": False, "error": f"no appid in filename: {Path(src_path).name} (pass --appid)"}
    st.mkdir(parents=True, exist_ok=True)
    text = Path(src_path).read_text(encoding="utf-8", errors="replace")
    build_id = build_id_from_name(src_path)
    auto_update = bool(ctx.settings.get("auto_update_apps", True))
    keep_pins = (not auto_update) or force_locked or (build_id is not None)
    dest = st / f"{appid}.lua"
    dest.write_text(text if keep_pins else comment_out_pins(text), encoding="utf-8")
    stamp_now(dest)
    vault_write(appid, build_id, text, source)
    record_loaded(appid)
    r = ctx.root()
    notes = []
    if r and r.exists():
        try:
            toml_ensure_lua_path(r)
            ensure_cdp_marker(r)
        except Exception as e:
            notes.append(f"toml/marker: {e}")
    return {"ok": True, "appid": appid, "dest": str(dest),
            "pins_kept": keep_pins, "build": build_id, "notes": notes}

def install_manifest(src):
    ctx = SteamCtx()
    dc = ctx.depot_cache_dir()
    if dc is None:
        return {"ok": False, "error": "steam root not configured"}
    dc.mkdir(parents=True, exist_ok=True)
    dest = dc / Path(src).name  # first-write-wins parity
    if dest.exists():
        return {"ok": True, "skipped": True, "dest": str(dest)}
    shutil.copyfile(src, dest)
    stamp_now(dest)
    return {"ok": True, "skipped": False, "dest": str(dest)}

def install_zip(zip_path, appid=None, force_locked=False, source=None):
    ctx = SteamCtx()
    st, dc = ctx.st_plugin_dir(), ctx.depot_cache_dir()
    if st is None or dc is None:
        return {"ok": False, "error": "steam root not configured"}
    st.mkdir(parents=True, exist_ok=True)
    dc.mkdir(parents=True, exist_ok=True)
    appid = appid or app_id_from_name(zip_path)
    if appid is None:
        try:
            with zipfile.ZipFile(zip_path) as z:
                for n in z.namelist():
                    if n.lower().endswith(".lua"):
                        appid = app_id_from_name(n)
                        if appid:
                            break
        except zipfile.BadZipFile:
            return {"ok": False, "error": "bad zip"}
    if appid is None:
        return {"ok": False, "error": "cannot resolve appid from zip filename or entries"}
    lua_installed, manifests, errors = False, 0, []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.split("/")[-1]
            if not name:
                continue
            is_lua = name.lower().endswith(".lua")
            is_manifest = name.lower().endswith(".manifest")
            if not (is_lua or is_manifest):
                continue
            if is_lua:
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / name
                    tmp.write_bytes(z.read(info))
                    res = install_lua(str(tmp), appid, force_locked, source)
                    lua_installed = lua_installed or res.get("ok", False)
                    if not res.get("ok"):
                        errors.append(name)
            else:
                dest = dc / name
                if dest.exists():
                    manifests += 1
                    continue
                dest.write_bytes(z.read(info))
                stamp_now(dest)
                manifests += 1
    return {"ok": True, "lua": lua_installed, "manifests": manifests,
            "appid": appid, "errors": errors}

def migrate_depotcache():
    ctx = SteamCtx()
    legacy, dc = ctx.legacy_depot_cache_dir(), ctx.depot_cache_dir()
    if legacy is None or dc is None:
        return {"ok": False, "error": "steam root not configured"}
    moved, skipped = 0, 0
    if legacy.is_dir():
        dc.mkdir(parents=True, exist_ok=True)
        for f in list(legacy.iterdir()):
            if f.is_file():
                if (dc / f.name).exists():
                    skipped += 1
                else:
                    shutil.copy2(f, dc / f.name)
                    moved += 1
                f.unlink()
        if not any(legacy.iterdir()):
            try:
                legacy.rmdir()
            except OSError:
                pass
    return {"ok": True, "moved": moved, "skipped": skipped}

def uninstall_lua(appid):
    ctx = SteamCtx()
    st = ctx.st_plugin_dir()
    if st is None:
        return {"ok": False, "error": "steam root not configured"}
    f = st / f"{appid}.lua"
    if f.exists():
        f.unlink()
        return {"ok": True, "removed": str(f)}
    return {"ok": True, "removed": None, "reason": "not installed"}

# ---------- remote api ----------

def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.status

def api_download_manifest(appid, source=None):
    q = f"?appid={appid}" + (f"&source={urllib.parse.quote(source)}" if source else "")
    return http_get(API_BASE + "/api/manifest/download" + q)

def api_manifest_punk(depot, manifest):
    return http_get(f"{API_BASE}/api/givemethemanifestpunk/{depot}/{manifest}")

def api_generate_dlc(appid, base=None):
    q = f"?appid={appid}" + (f"&base={base}" if base else "")
    return http_get(API_BASE + "/api/dlc/generate" + q)

def check_sources(appid):
    req = urllib.request.Request(CHECK_APIS_URL + str(appid),
                                 headers={"User-Agent": UA_BACKEND_CHECK})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or "{}")
    except Exception:
        return {}

# ---------- background download queue ----------

_jobs = {}
_jobs_lock = threading.Lock()

def _job(appid):
    with _jobs_lock:
        j = _jobs.get(appid)
        if j is None:
            j = {"status": "idle", "msg": "", "cancel": threading.Event()}
            _jobs[appid] = j
        return j

def start_download(appid):
    j = _job(appid)
    if j["status"] in ("queued", "working"):
        return
    j["status"], j["msg"] = "queued", ""
    j["cancel"].clear()
    threading.Thread(target=_worker, args=(appid, bool(SteamCtx().settings.get("force_locked", False)), j), daemon=True).start()

def _worker(appid, force_locked, j):
    try:
        j["status"] = "working"
        data, status = api_download_manifest(appid)
        if j["cancel"].is_set():
            j["status"], j["msg"] = "cancelled", ""
            return
        staging = ST_CACHE_DIR / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        zp = staging / f"{appid}.zip"
        zp.write_bytes(data)
        res = install_zip(str(zp), appid, force_locked, source="luatools-mac")
        zp.unlink()
        j["status"] = "done" if res.get("ok") else "error"
        j["msg"] = f"lua={res.get('lua')} manifests={res.get('manifests')} errors={res.get('errors')}"
    except urllib.error.HTTPError as e:
        j["status"], j["msg"] = "error", f"HTTP {e.code} (source/token required?)"
    except Exception as e:
        j["status"], j["msg"] = "error", str(e)

# ---------- websocket / CDP client ----------

class CDP:
    def __init__(self, ws_url):
        u = urllib.parse.urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake failed")
            resp += chunk
        head, self.buf = resp.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0].decode("latin1")
        if " 101 " not in status:
            raise ConnectionError("ws upgrade refused: " + status)
        self.lock = threading.Lock()

    def _recv_exact(self, n):
        buf = bytearray(self.buf)
        self.buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError("closed")
            buf.extend(chunk)
        out = bytes(buf[:n])
        self.buf = bytes(buf[n:])
        return out

    def recv_frame(self):
        h = self._recv_exact(2)
        opcode = h[0] & 0x0F
        length = h[1] & 0x7F
        masked = bool(h[1] & 0x80)
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length)
        if masked:
            mask = payload[:4]
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload[4:]))
        if opcode == 0x9:
            self.send_frame(0xA, payload)
            return self.recv_frame()
        if opcode == 0xA:
            return self.recv_frame()
        return opcode, payload

    def send_frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode()
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def evaluate(self, expr, json_id, timeout=10.0):
        with self.lock:
            self.send_frame(0x1, json.dumps({"id": json_id, "method": "Runtime.evaluate",
                                             "params": {"expression": expr,
                                                        "returnByValue": True}}))
            self.sock.settimeout(timeout)
            deadline = time.time() + timeout
            try:
                while time.time() < deadline:
                    opcode, payload = self.recv_frame()
                    if opcode != 0x1:
                        continue
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        continue
                    if msg.get("id") == json_id:
                        return msg
                return None
            except socket.timeout:
                return None
            finally:
                self.sock.settimeout(10)

# ---------- CEF injector loop (parity with CefInjectorService) ----------

_ids = itertools.count()
_cdps, _cdps_lock = {}, threading.Lock()
_polyfill = ""
_luatools_js = ""

INLINE_POLYFILL = (
    "\n(function(){\nvar real=window.Millennium;\nvar pending={},ready={},reqId=0;\n"
    "function ltCall(p,m,a){\n  var i='_ltr_'+(++reqId);\n"
    "  pending[i]={method:m,args:a,ts:Date.now()};\n"
    "  return new Promise(function(rv,rj){\n    var mx=100;\n"
    "    function ck(){\n      var r=ready[i];\n"
    "      if(r!==undefined){delete ready[i];if(r.e){rj(new Error(r.e))}else{rv(r.v)}return}\n"
    "      if(--mx>0){setTimeout(ck,50)}else{delete pending[i];rv({success:true})}\n"
    "    }\n    ck();\n  });\n}\n"
    "if(real&&typeof real.callServerMethod==='function'){\n"
    "  var realCall=real.callServerMethod.bind(real);\n"
    "  real.callServerMethod=function(p,m,a){return p==='luatools'?ltCall(p,m,a):realCall(p,m,a)};\n"
    "  real._pending=pending;\n  real._readyResponses=ready;\n  window.Millennium=real;\n"
    "}else{\n  window.Millennium={_pending:pending,_readyResponses:ready,"
    "callServerMethod:function(p,m,a){return ltCall(p,m,a)}};\n}\n})();\n")

def load_frontend_js():
    global _polyfill, _luatools_js
    for cand in (ST_FRONTEND_DIR / "public" / "luatools.js",
                 ST_FRONTEND_DIR / "luatools.js",
                 Path(__file__).parent / "public" / "luatools.js",
                 Path(__file__).parent / "luatools.js"):
        if cand.exists():
            _luatools_js = cand.read_text()
            break
    pf = None
    for cand in (Path(__file__).parent / "public" / "millennium-polyfill.js",
                 Path(__file__).parent / "millennium-polyfill.js",
                 ST_FRONTEND_DIR / "millennium-polyfill.js"):
        if cand.exists():
            pf = cand
            break
    _polyfill = pf.read_text() if pf else INLINE_POLYFILL

def _evict(tid):
    with _cdps_lock:
        ws = _cdps.pop(tid, None)
    if ws:
        try:
            ws.sock.close()
        except OSError:
            pass

def bridge_call(method, args_raw):
    m = BRIDGE_MAP.get(method or "")
    if not m:
        return json.dumps({"success": True})
    verb, tmpl = m
    if args_raw:
        try:
            for k, v in json.loads(args_raw).items():
                tmpl = tmpl.replace("{" + k + "}", str(v))
        except Exception:
            pass
    bridge = bridge_dispatch(verb, tmpl)
    return json.dumps(bridge)

def injector_loop():
    tick = 0
    live = []
    script = _polyfill + "\n" + _luatools_js
    while True:
        try:
            if tick % 7 == 0:
                try:
                    raw, _ = http_get(f"http://127.0.0.1:{CDP_PORT}/json", timeout=2)
                    tabs = json.loads(raw)
                except Exception:
                    tabs = []
                seen = set()
                for tab in tabs:
                    url = (tab.get("url") or "")
                    tid = tab.get("id") or ""
                    ws_url = tab.get("webSocketDebuggerUrl") or ""
                    if "store.steampowered.com" not in url.lower() or not tid or not ws_url:
                        continue
                    seen.add(tid)
                    ws = _cdps.get(tid)
                    if ws is None:
                        try:
                            ws = CDP(ws_url)
                        except Exception:
                            continue
                        with _cdps_lock:
                            _cdps[tid] = ws
                    ready = ws.evaluate("String(window.__LuaToolsReady === true)", next(_ids))
                    got = (ready or {}).get("result", {}).get("result", {}).get("value")
                    if got != "true":
                        ws.evaluate(script, next(_ids))
                    live.append(tid)
                for gone in list(_cdps):
                    if gone not in seen:
                        _evict(gone)
            for tid in live:
                ws = _cdps.get(tid)
                if ws is None:
                    continue
                pend = ws.evaluate("JSON.stringify(Object.values(window.Millennium._pending||{}).map("
                                   "function(r){return{m:r.method,a:JSON.stringify(r.args)}}))", next(_ids))
                raw = (pend or {}).get("result", {}).get("result", {}).get("value")
                if not raw or raw in ("[]", "null"):
                    continue
                items = json.loads(raw)
                responses = []
                for it in items:
                    try:
                        responses.append({"v": json.loads(bridge_call(it.get("m"), it.get("a")))})
                    except Exception as e:
                        responses.append({"e": str(e)})
                expr = ("(function(r){var ks=Object.keys(window.Millennium._pending);"
                        "for(var i=0;i<ks.length&&i<r.length;i++){var id=ks[i];"
                        "window.Millennium._readyResponses[id]=r[i];"
                        "delete window.Millennium._pending[id];}})(" + json.dumps(responses) + ");")
                ws.evaluate(expr, next(_ids))
            tick += 1
            time.sleep(0.15)
        except Exception:
            time.sleep(1.0)
            tick = 0

# ---------- bridge http server ----------

def bridge_dispatch(verb, path):
    ctx = SteamCtx()
    parts = [p for p in urllib.parse.urlparse(path).path.lstrip("/").split("/") if p]
    if not parts:
        return {"success": True}
    head = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    try:
        appid = int(arg) if arg.isdigit() else None
        if head == "has" and appid:
            st = ctx.st_plugin_dir()
            return {"exists": bool(st and (st / f"{appid}.lua").exists())}
        if head == "check-sources" and appid:
            return check_sources(appid)
        if head in ("download", "add") and appid:
            start_download(appid)
            return {"started": True}
        if head in ("download-status", "add-status") and appid:
            j = _job(appid)
            return {"status": j["status"], "msg": j["msg"]}
        if head == "cancel" and appid:
            _job(appid)["cancel"].set()
            return {"cancelled": True}
        if head == "remove" and appid:
            return uninstall_lua(appid)
        if head == "loaded-apps" and verb == "GET":
            return {"appIds": load_json_list(LOADED_PATH)}
        if head == "api-list":
            return {"sources": ["CleanManfiests", "SmokeAPI", "Steamless"]}
        if head == "icon":
            svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#1a9fff"/></svg>'
            return {"dataUrl": "data:image/svg+xml;base64," + base64.b64encode(svg).decode()}
        if head == "games-database":
            return {"games": []}
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True}

class Bridge(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._json(bridge_dispatch("GET", self.path))
        except Exception as e:
            self._json({"success": False, "error": str(e)}, 500)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "ignore") if n else "{}"
            self._json(bridge_dispatch_post(self.path, body))
        except Exception as e:
            self._json({"success": False, "error": str(e)}, 500)

def bridge_dispatch_post(path, body):
    ctx = SteamCtx()
    parts = [p for p in urllib.parse.urlparse(path).path.lstrip("/").split("/") if p]
    head = parts[0] if parts else ""
    arg = parts[1] if len(parts) > 1 else ""
    try:
        appid = int(arg) if arg.isdigit() else None
        if head == "loaded-apps":
            save_json_list(LOADED_PATH, [])
            return {"success": True}
        if head == "restart-steam":
            ctx.stop_steam()
            ctx.start_steam()
            return {"success": True}
        if head == "open-url":
            try:
                webbrowser.open(json.loads(body).get("url", ""))
            except Exception:
                pass
            return {"success": True}
        if head == "open" and parts[-1] and appid:
            if head == "open" and parts[1] == "fix":
                st = ctx.st_plugin_dir()
                if st and (st / f"{appid}.lua").exists():
                    subprocess.run(["open", "-R", str(st / f"{appid}.lua")])
                return {"success": True}
            if head == "open" and parts[1] == "settings":
                subprocess.run(["open", str(ST_CACHE_DIR)])
                return {"success": True}
        if head == "log":
            print("[bridge-log]", body)
            return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
    return bridge_dispatch("POST", path)

# ---------- dual-platform lua generator ----------

def parse_depot_rows(text):
    rows = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or raw.lower().startswith("appid"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t|]+", raw)]
        if len(parts) < 2:
            continue
        depot = parts[0]
        if not depot.isdigit():
            continue
        oslist = (parts[1] or "").lower()
        oslist = {"windows": "win", "macos": "mac", "osx": "mac", "mac": "mac", "linux": "linux", "win": "win"}.get(oslist, oslist) or "shared"
        manifest = parts[2].strip().strip('"') if len(parts) > 2 else ""
        size, key = "", ""
        for tok in parts[3:]:
            t = tok.strip().strip('"')
            if not t:
                continue
            if t.isdigit() and not size:
                size = t
            elif not key:
                key = t
        rows.append({"depot": depot, "os": oslist, "manifest": manifest, "size": size, "key": key or None})
    return rows

def gen_dual_lua(appid, rows):
    groups = {}
    for r in rows:
        groups.setdefault(r["os"], []).append(r)
    order = [k for k in ("shared", "win", "mac", "linux") if k in groups]
    order += [k for k in groups if k not in order]
    lines = [f"addappid({appid})", f"addappid({appid}, 1)"]
    manifest_files = []
    for osname in order:
        label = {"shared": "shared depots (pinned once, all OS)",
                 "win": "windows depots", "mac": "macos depots",
                 "linux": "linux depots"}.get(osname, osname)
        lines.append(f"-- {label}")
        for r in groups[osname]:
            lines.append(f"addappid({r['depot']}, 0)")
            if r["manifest"]:
                size_field = f', "{r["size"]}"' if r["size"] else ""
                lines.append(f'setManifestid({r["depot"]}, "{r["manifest"]}"{size_field})')
                manifest_files.append(f"{r['depot']}_{r['manifest']}.manifest")
            else:
                lines.append(f"-- setManifestid({r['depot']}, ...)  # no manifest id supplied")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", manifest_files

def cmd_dual(a):
    appid = int(a.appid)
    src = Path(a.depots)
    text = src.read_text(encoding="utf-8", errors="replace") if src.exists() else a.depots
    rows = parse_depot_rows(text)
    if not rows:
        print(json.dumps({"ok": False, "error": "no depot rows parsed"}))
        return
    lua_text, files = gen_dual_lua(appid, rows)
    out = Path(a.out) if a.out else ST_CACHE_DIR / "out" / f"{appid}.lua"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lua_text, encoding="utf-8")
    print(json.dumps({"ok": True, "lua": str(out), "manifests_to_fetch": files,
                      "rows": len(rows), "groups": sorted({r["os"] for r in rows})}, indent=2))
    if a.install:
        print(json.dumps(install_lua(str(out), appid, a.force_locked), indent=2))



def cmd_detect(_):
    ctx = SteamCtx()
    print(json.dumps({
        "platform": sys.platform, "root": str(ctx.root()), "valid": ctx.valid(),
        "st_plugin": str(ctx.st_plugin_dir()), "depotcache": str(ctx.depot_cache_dir()),
        "legacy_depotcache": str(ctx.legacy_depot_cache_dir()),
        "steam_exe": str(ctx.steam_exe()), "steam_running": ctx.steam_running(),
    }, indent=2))

def cmd_status(_):
    ctx = SteamCtx()
    st, dc = ctx.st_plugin_dir(), ctx.depot_cache_dir()
    print(json.dumps({
        "root": str(ctx.root()), "valid": ctx.valid(),
        "lua_installed": sorted(int(f.stem) for f in st.glob("*.lua")) if st and st.exists() else [],
        "manifests": len(list(dc.glob("*.manifest"))) if dc and dc.exists() else 0,
        "loaded": load_json_list(LOADED_PATH),
        "vault": vault_list(),
        "steam_running": ctx.steam_running(),
    }, indent=2))

def cmd_info(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    d = parse_lua(text, int(args.appid) if args.appid else 0)
    print(json.dumps(d, indent=2))

def num(x):
    return int(x) if x and str(x).isdigit() else None

def cmd_install_lua(a):
    print(json.dumps(install_lua(a.file, num(a.appid), a.force_locked), indent=2))

def cmd_install_zip(a):
    print(json.dumps(install_zip(a.zip, num(a.appid), a.force_locked), indent=2))

def cmd_install_manifest(a):
    print(json.dumps(install_manifest(a.file), indent=2))

def cmd_migrate(_):
    print(json.dumps(migrate_depotcache(), indent=2))

def cmd_toml(_):
    r = SteamCtx().root()
    if not r or not r.exists():
        sys.exit("steam root missing — set with: settings --steam-path ...")
    toml_ensure_lua_path(r)
    print(json.dumps({"toml": str(r / "opensteamtool.toml"), "written": True}))

def cmd_restart(_):
    ctx = SteamCtx()
    ctx.stop_steam()
    ctx.start_steam()

def cmd_serve(_):
    ctx = SteamCtx()
    r = ctx.root()
    if r and r.exists():
        ensure_cdp_marker(r)
    load_frontend_js()
    threading.Thread(target=injector_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Bridge)
    print(f"bridge http://127.0.0.1:{BRIDGE_PORT}  |  CDP injector on :{CDP_PORT} (store tabs)  |  ctrl-c stops")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()

def cmd_vault(a):
    print(json.dumps(vault_list(a.appid), indent=2))

def cmd_settings(a):
    s = load_settings()
    if a.steam_path:
        s["steam_path"] = a.steam_path
    if a.no_auto_update:
        s["auto_update_apps"] = False
    if a.auto_update:
        s["auto_update_apps"] = True
    if a.force_locked:
        s["force_locked"] = True
    if getattr(a, "token", ""):
        s["auth_token"] = a.token
    save_settings(s)
    print(json.dumps({"saved": str(SETTINGS_PATH), "settings": load_settings()}, indent=2))

def cmd_uninstall_lua(a):
    print(json.dumps(uninstall_lua(a.appid), indent=2))

# ---------- steam library (SteamLibraryService parity) ----------

def read_vdf_paths(root: Path):
    sa = root / "steamapps"
    found = [sa] if sa.is_dir() else []
    vdf = sa / "libraryfolders.vdf"
    if vdf.exists():
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            p = m.group(1).replace("\\\\", "\\").replace("/", "\\")
            t = Path(p) / "steamapps"
            if t.is_dir() and all(str(t).lower() != str(x).lower() for x in found):
                found.append(t)
    return found

ACF_RE = re.compile(r'"(\w+)"\s+"([^"]*)"')

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
        return {"ok": False, "error": "steam root not configured"}
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

# ---------- remote fetch (lua.tools endpoints) ----------

def auth_headers(token):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
        h["apikey"] = token
    return h

def fetch_to_depotcache(depot, manifest, token=""):
    ctx = SteamCtx()
    dc = ctx.depot_cache_dir()
    if dc is None:
        return {"ok": False, "error": "steam root not configured"}
    dc.mkdir(parents=True, exist_ok=True)
    dest = dc / f"{depot}_{manifest}.manifest"
    if dest.exists():
        return {"ok": True, "skipped": True, "dest": str(dest)}
    url = f"{API_BASE}/api/givemethemanifestpunk/{depot}/{manifest}"
    try:
        data, _ = http_get(url, headers=auth_headers(token), timeout=120)
        dest.write_bytes(data)
        stamp_now(dest)
        return {"ok": True, "dest": str(dest), "bytes": len(data)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "dest": str(dest)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fetch_manifest_zip(appid, source=None, token=""):
    staging = ST_CACHE_DIR / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    zp = staging / f"{appid}_manifest.zip"
    url = API_BASE + "/api/manifest/download?appid=" + str(appid)
    if source:
        url += "&source=" + urllib.parse.quote(source)
    try:
        data, _ = http_get(url, headers=auth_headers(token), timeout=300)
        zp.write_bytes(data)
        res = install_zip(str(zp), appid, source=source or "luatools-mac")
        zp.unlink()
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fetch_dlc_generate(appid, base=None, token=""):
    url = API_BASE + "/api/dlc/generate?appid=" + str(appid) + (("&base=" + str(base)) if base else "")
    try:
        data, _ = http_get(url, headers=auth_headers(token), timeout=120)
        staging = ST_CACHE_DIR / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        f = staging / f"{appid}.lua"
        f.write_bytes(data)
        res = install_lua(str(f), appid)
        f.unlink()
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}

def denuvo_listings(token=""):
    try:
        data, _ = http_get(API_BASE + "/api/denuvo/listings", headers=auth_headers(token), timeout=30)
        return {"ok": True, "data": json.loads(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def denuvo_fixes(appid, token=""):
    try:
        data, _ = http_get(API_BASE + "/api/denuvo/fixes?appid=" + urllib.parse.quote(str(appid)),
                           headers=auth_headers(token), timeout=30)
        return {"ok": True, "data": json.loads(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_fix_index():
    p = ST_CACHE_DIR / "applied_fixes.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"fixes": []}

def record_fix(appid, name, n_files):
    if not name:
        return
    d = check_fix_index()
    d.setdefault("fixes", []).append({"appid": appid, "name": name,
                                      "files": n_files, "ts": time.time()})
    ST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (ST_CACHE_DIR / "applied_fixes.json").write_text(json.dumps(d, indent=2))

# ---------- LTSP plugin fetch (portable JS front-end for CDP injection) ----------

def gh_latest_asset(repo, want_asset, token=""):
    url = "https://api.github.com/repos/" + repo + "/releases/latest"
    h = auth_headers(token)
    h["Accept"] = "application/vnd.github+json"
    h["User-Agent"] = "luatools-mac"
    try:
        data, _ = http_get(url, headers=h, timeout=30)
    except Exception as e:
        return None, str(e)
    rel = json.loads(data)
    for a in rel.get("assets", []):
        if want_asset(a.get("name", "")):
            return a, None
    return None, "asset not found in latest release"

def NormalizeFrontend(base: Path):
    if (base / "public").exists():
        return
    for sub in list(base.iterdir()):
        if sub.is_dir() and (sub / "public").exists():
            for child in list(sub.iterdir()):
                shutil.move(str(child), str(base / child.name))
            shutil.rmtree(sub, ignore_errors=True)
            return

def fetch_plugin(token=""):
    stf = ST_FRONTEND_DIR
    stf.mkdir(parents=True, exist_ok=True)
    asset, err = gh_latest_asset("madoiscool/LTSP", lambda n: n.lower() == "plugin.zip", token)
    if asset is None:
        return {"ok": False, "error": err}
    zp = stf / "plugin.zip"
    try:
        data, _ = http_get(asset["browser_download_url"], timeout=300,
                           headers={"User-Agent": "luatools-mac"})
        zp.write_bytes(data)
        with zipfile.ZipFile(zp) as z:
            z.extractall(stf)
        zp.unlink()
        NormalizeFrontend(stf)
        js = stf / "public" / "luatools.js"
        if not js.exists():
            js = stf / "luatools.js"
        return {"ok": js.exists(), "frontend": str(stf),
                "luatools_js": str(js) if js.exists() else None,
                "tag": asset.get("browser_download_url", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- external tool spacers (DepotDownloader / SteamAutoCrack) ----------

TOOLS_DIR = ST_CACHE_DIR / "tools"

def _find_tool(name):
    env = os.environ.get(name.upper() + "_PATH", "")
    if env and Path(env).exists():
        return Path(env)
    p = TOOLS_DIR / name
    if p.exists():
        return p
    return shutil.which(name)

def run_depotdownloader(lua_file, token="", extra=None):
    tool = _find_tool("depotdownloader")
    if tool is None:
        return {"ok": False,
                "error": "depotdownloader not found — put it in ~/.luatools-mac/tools/ or PATH"}
    d = parse_lua(Path(lua_file).read_text(encoding="utf-8", errors="replace"))
    args = [str(tool), "-app", str(d["main_app"])]
    depots = sorted({e["id"] for e in d["entries"] if e["id"] != d["main_app"]})
    if depots:
        args += ["-depot", ",".join(map(str, depots))]
    if extra:
        args += extra
    try:
        pr = subprocess.run(args, capture_output=True, text=True)
        return {"ok": pr.returncode == 0, "exit": pr.returncode,
                "stdout": pr.stdout[-4000:], "stderr": pr.stderr[-2000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def run_autocrack(appid, installdir=None):
    tool = _find_tool("SteamAutoCrack")
    if tool is None:
        return {"ok": False,
                "error": "SteamAutoCrack not found — put it in ~/.luatools-mac/tools/ or PATH"}
    ctx = SteamCtx()
    lib = read_vdf_paths(ctx.root())
    for l in lib:
        f = l / f"appmanifest_{appid}.acf"
        if f.exists():
            d = parse_acf(f)
            installdir = installdir or (l / d.get("installdir", ""))
            break
    if not installdir or not Path(installdir).exists():
        return {"ok": False, "error": f"no install dir for app {appid}"}
    try:
        pr = subprocess.run([str(tool), str(Path(installdir))], capture_output=True, text=True)
        record_fix(appid, "SteamAutoCrack", 0)
        return {"ok": pr.returncode == 0, "exit": pr.returncode,
                "stdout": pr.stdout[-4000:], "stderr": pr.stderr[-2000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def cmd_library(_):
    print(json.dumps(library_list(), indent=2, ensure_ascii=False))

def cmd_fetch_manifest(a):
    print(json.dumps(fetch_to_depotcache(a.depot, a.manifest, load_settings().get("auth_token", "")), indent=2))

def cmd_fetch_zip(a):
    print(json.dumps(fetch_manifest_zip(int(a.appid), a.source, load_settings().get("auth_token", "")), indent=2))

def cmd_generate_dlc(a):
    print(json.dumps(fetch_dlc_generate(int(a.appid), a.base, load_settings().get("auth_token", "")), indent=2))

def cmd_check_apis(a):
    print(json.dumps(check_sources(a.appid), indent=2))

def cmd_denuvo(a):
    tok = load_settings().get("auth_token", "")
    r = denuvo_fixes(a.appid, tok) if a.appid else denuvo_listings(tok)
    print(json.dumps(r, indent=2, ensure_ascii=False))

def cmd_fix_index(_):
    print(json.dumps(check_fix_index(), indent=2))

def cmd_plugin(_):
    print(json.dumps(fetch_plugin(load_settings().get("auth_token", "")), indent=2))
    load_frontend_js()

def cmd_downloader(a):
    print(json.dumps(run_depotdownloader(a.lua, extra=a.arg or None), indent=2, ensure_ascii=False))

def cmd_autocrack(a):
    print(json.dumps(run_autocrack(int(a.appid)), indent=2, ensure_ascii=False))

# ---------- binary VDF (shortcuts.vdf) — Steam library entry writer ----------

BVDF_END, BVDF_DICT, BVDF_STR, BVDF_U32, BVDF_U64 = 0x08, 0x00, 0x01, 0x02, 0x07

def bvdf_parse(buf, pos, out):
    while pos < len(buf):
        tag = buf[pos]
        if tag == BVDF_END:
            return pos + 1
        end = buf.index(b"\x00", pos + 1)
        key = buf[pos + 1:end].decode("utf-8", "replace")
        pos = end + 1
        if tag == BVDF_DICT:
            child = {}
            pos = bvdf_parse(buf, pos, child)
            out[key] = child
        elif tag == BVDF_STR:
            end = buf.index(b"\x00", pos)
            out[key] = buf[pos:end].decode("utf-8", "replace")
            pos = end + 1
        elif tag == BVDF_U32:
            out[key] = struct.unpack("<I", buf[pos:pos + 4])[0]
            pos += 4
        elif tag == BVDF_U64:
            out[key] = struct.unpack("<Q", buf[pos:pos + 8])[0]
            pos += 8
        else:
            raise ValueError(f"unknown vdf tag {tag:#x} at {pos}")
    return pos

def bvdf_bytes(tree):
    out = bytearray()
    for key, val in tree.items():
        k = key.encode("utf-8") + b"\x00"
        if isinstance(val, dict):
            out += bytes([BVDF_DICT]) + k + bvdf_bytes(val) + bytes([BVDF_END])
        elif isinstance(val, bool):
            out += bytes([BVDF_U32]) + k + struct.pack("<I", int(val))
        elif isinstance(val, int):
            out += bytes([BVDF_U32]) + k + struct.pack("<I", val & 0xFFFFFFFF)
        else:
            out += bytes([BVDF_STR]) + k + str(val).encode("utf-8") + b"\x00"
    return bytes(out)

def read_shortcuts(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return {"shortcuts": {}}
    root = {}
    bvdf_parse(path.read_bytes(), 0, root)
    root.setdefault("shortcuts", {})
    return root

def write_shortcuts(path: Path, tree):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".vdf.bak"))
    path.write_bytes(bytes([BVDF_DICT]) + b"shortcuts\x00" + bvdf_bytes(tree["shortcuts"]) + bytes([BVDF_END]))

def steam_userdata_dirs(root: Path):
    cands = []
    for d in (root / "userdata").glob("*"):
        if (d / "config").is_dir():
            cands.append((d.stat().st_mtime if d.exists() else 0, d / "config" / "shortcuts.vdf"))
    cands.sort(reverse=True)
    return [c[1] for c in cands]

def shortcut_entry(appid, exe, name, startdir="", icon="", launch="", hidden=0):
    return {
        "appid": int(appid),
        "AppName": name,
        "Exe": exe,
        "StartDir": startdir or str(Path(exe).parent),
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch,
        "IsHidden": int(hidden),
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
    }

def add_shortcut(appid, exe, name, vdf=None):
    ctx = SteamCtx()
    root = ctx.root()
    if root is None:
        return {"ok": False, "error": "steam root not found"}
    targets = [Path(vdf)] if vdf else steam_userdata_dirs(root)
    if not targets:
        return {"ok": False, "error": "no userdata/*/config found — launch Steam once"}
    written, replaced = [], 0
    for p in targets:
        tree = read_shortcuts(p)
        entries = tree["shortcuts"]
        key = str(max([int(k) for k in entries if k.isdigit()] or [-1]) + 1)
        for k, v in list(entries.items()):
            if isinstance(v, dict) and v.get("appid") == int(appid):
                replaced += 1
                entries[k] = shortcut_entry(appid, str(exe), name)
                break
        else:
            entries[key] = shortcut_entry(appid, str(exe), name)
        write_shortcuts(p, tree)
        written.append(str(p))
    return {"ok": True, "files": written, "replaced": replaced}

# ---------- steam_api.dylib swap (per-game native mac) ----------

API_SWAP_DIR = ST_CACHE_DIR / "api-backup"

def find_api_libs(game_dir: Path):
    pats = ("libsteam_api.dylib", "steam_api.dylib", "libsteam_api64.dylib")
    found = []
    for p in game_dir.rglob("*"):
        if p.is_file() and p.name.lower() in pats:
            found.append(p)
    return found

def apply_api(appid, game_dir, replacement, extra_settings=None):
    gd = Path(game_dir)
    if not gd.exists():
        return {"ok": False, "error": f"game dir not found: {gd}"}
    libs = find_api_libs(gd)
    if not libs:
        return {"ok": False, "error": "no steam_api dylib inside game folder"}
    rep = Path(replacement)
    if not rep.exists():
        return {"ok": False, "error": f"replacement dylib missing: {rep}"}
    API_SWAP_DIR.mkdir(parents=True, exist_ok=True)
    swaps = []
    for lib in libs:
        bak = API_SWAP_DIR / f"{appid}_{lib.name}.orig"
        if not bak.exists():
            shutil.copy2(lib, bak)
            (API_SWAP_DIR / f"{appid}_{lib.name}.path").write_text(str(lib))
        shutil.copy2(rep, lib)
        if os.name != "nt":
            os.chmod(lib, 0o755)
        swaps.append({"lib": str(lib), "backup": str(bak)})
    # Goldberg-style settings dir next to the game binary's parent
    settings = gd / "steam_settings"
    settings.mkdir(exist_ok=True)
    (settings / "steam_appid.txt").write_text(str(appid))
    (gd / "steam_appid.txt").write_text(str(appid))
    (settings / "configs.appid").write_text(str(appid))
    (settings / "configs.user.ini").write_text(
        "[user::general]\naccount_name=Goldberg\naccount_steamid=76561197960287930\nlanguage=english\n")
    for k, v in (extra_settings or {}).items():
        (settings / k).write_text(str(v))
    return {"ok": True, "swapped": swaps, "settings": str(settings)}

def restore_api(appid):
    if not API_SWAP_DIR.exists():
        return {"ok": False, "error": "no backups recorded"}
    restored = []
    for bak in API_SWAP_DIR.glob(f"{appid}_*.orig"):
        name = bak.name.split("_", 1)[1][:-5]
        idx = API_SWAP_DIR / f"{appid}_{name}.path"
        if idx.exists():
            target = Path(idx.read_text().strip())
            shutil.copy2(bak, target)
            restored.append(str(target))
    return {"ok": bool(restored), "restored": restored}

# ---------- steamcmd appinfo probe (finds real mac depots + manifest gids) ----------

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

def _oslist_matches(oslist, target):
    if not oslist:
        return False
    aliases = {"macos": ("macos", "mac", "osx"), "windows": ("windows", "win")}.get(target, (target,))
    if isinstance(oslist, dict):
        return any(oslist.get(a) for a in aliases)
    parts = re.split(r"[\s,]+", str(oslist).lower())
    return any(a in parts for a in aliases)

def probe_app(appid, steamcmd=None):
    tool = steamcmd or _find_tool("steamcmd") or shutil.which("steamcmd")
    if not tool:
        return {"ok": False, "error": "steamcmd not found — brew install --cask steamcmd"}
    args = [str(tool), "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
            "+login", "anonymous", "+app_info_print", str(appid), "+quit"]
    try:
        pr = subprocess.run(args, capture_output=True, text=True, timeout=180,
                            input="", errors="replace")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out = (pr.stdout or "") + "\n" + (pr.stderr or "")
    start = out.find('"')
    if start < 0:
        return {"ok": False, "error": "empty steamcmd output", "tail": out[-400:]}
    tree = vdf_parse(out[start:])
    app = tree.get(str(appid)) or next((v for k, v in tree.items()
                                        if isinstance(v, dict) and "depots" in v), None)
    if not isinstance(app, dict) or "depots" not in app:
        return {"ok": False, "error": "appinfo has no depots section (app may be delisted)",
                "tail": out[-400:]}
    rows, seen = [], set()
    for depot_id, depot in (app["depots"] or {}).items():
        if not str(depot_id).isdigit() or not isinstance(depot, dict):
            continue
        oslist = (depot.get("config") or {}).get("oslist", "")
        hits = [o for o in ("mac", "win", "linux")
                if _oslist_matches(oslist, "macos" if o == "mac" else
                                   "windows" if o == "win" else "linux")]
        osname = hits[0] if len(hits) == 1 else "shared"
        gid = (((depot.get("manifests") or {}).get("public") or {}).get("gid"))
        if not gid or depot_id in seen:
            continue
        seen.add(depot_id)
        rows.append({"depot": depot_id, "os": osname, "manifest": str(gid), "size": "", "key": None})
    mac = [r for r in rows if r["os"] == "mac"]
    return {"ok": True, "appid": str(appid), "buildid": (app.get("depots") or {})
            .get("branches", {}).get("public", {}).get("buildid", ""),
            "count": len(rows), "mac": len(mac), "rows": rows}

def rows_to_csv(rows):
    lines = ["# depot,os,manifest,size,depotkey"]
    for r in rows:
        lines.append(f"{r['depot']},{r['os']},{r['manifest']},,{r.get('key') or ''}")
    return "\n".join(lines) + "\n"

# ---------- DepotDownloader installer (self-contained binary, no dotnet needed) ----------

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
    want = dd_asset_name()
    asset, err = gh_latest_asset("SteamRE/DepotDownloader", lambda n: n == want, token)
    if asset is None:
        return {"ok": False, "error": err, "wanted": want}
    dest = TOOLS_DIR / "depotdownloader"
    dest.mkdir(parents=True, exist_ok=True)
    zp = dest / want
    try:
        data, _ = http_get(asset["browser_download_url"], timeout=600,
                           headers={"User-Agent": "luatools-mac"})
        zp.write_bytes(data)
        with zipfile.ZipFile(zp) as z:
            z.extractall(dest)
        zp.unlink()
        cands = [p for p in dest.rglob("*")
                 if p.is_file() and p.name.lower().startswith("depotdownloader")]
        if not cands:
            return {"ok": False, "error": "binary not found in archive", "dir": str(dest)}
        exe = sorted(cands, key=lambda p: len(p.parts))[0]
        if os.name != "nt":
            for p in dest.rglob("*"):
                if p.is_file() and not p.name.lower().endswith((".zip",)):
                    try:
                        os.chmod(p, 0o755)
                    except OSError:
                        pass
        return {"ok": True, "tool": str(exe), "asset": want, "bytes": len(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- native mac game fetch (DepotDownloader orchestrator) ----------

def _dd_tool():
    for name in ("DepotDownloader", "DepotDownloader.exe", "depotdownloader"):
        t = _find_tool(name)
        if t:
            return t
    if TOOLS_DIR.exists():
        for p in sorted(TOOLS_DIR.rglob("*")):
            if p.is_file() and p.name.lower().startswith("depotdownloader"):
                return p
    return None

def cmd_install_dd(_a):
    print(json.dumps(install_depotdownloader(load_settings().get("auth_token", "")),
                     indent=2, ensure_ascii=False))

def cmd_probe(a):
    res = probe_app(a.appid)
    if res.get("ok") and a.out:
        Path(a.out).write_text(rows_to_csv(res["rows"]))
        res["csv"] = a.out
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return res

def cmd_fetch_game(a):
    appid = int(a.appid)
    src = Path(a.depots)
    if a.depots.strip().lower() in ("auto", "-", "@auto"):
        probe = probe_app(appid)
        if not probe.get("ok"):
            res = {"ok": False, "stage": "probe", **probe}
            print(json.dumps(res, indent=2))
            return res
        rows = probe["rows"]
        print(json.dumps({"probe": {"mac": probe["mac"], "total": probe["count"],
                                    "buildid": probe["buildid"]}}, indent=2))
    else:
        text = src.read_text(encoding="utf-8", errors="replace") if src.exists() else a.depots
        rows = parse_depot_rows(text)
    if not rows:
        res = {"ok": False, "error": "no depot rows"}
        print(json.dumps(res))
        return res
    target_os = (a.os or "mac").lower()
    os_ok = lambda o: o == target_os or (a.include_shared and o == "shared")
    want = [r for r in rows if os_ok(r["os"])]
    if not want:
        res = {"ok": False, "error": f"no depots for os={target_os}"}
        print(json.dumps(res))
        return res
    tool = _dd_tool()
    if tool is None:
        res = {"ok": False,
               "error": "DepotDownloader missing — run: install-dd",
               "depots_planned": [{"depot": r["depot"], "manifest": r["manifest"]} for r in want]}
        print(json.dumps(res, indent=2))
        return res
    outroot = Path(a.out or str(ST_CACHE_DIR / "games" / str(appid)))
    outroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if a.user and a.pass_:
        env["STEAM_USER"], env["STEAM_PASS"] = a.user, a.pass_
    results = []
    for r in want:
        args = [str(tool), "-app", str(appid), "-depot", r["depot"],
                "-dir", str(outroot / r["depot"])]
        if r["manifest"]:
            args += ["-manifest", r["manifest"]]
        if r.get("key"):
            args += ["-depotkey", r["key"]]
        try:
            pr = subprocess.run(args, capture_output=True, text=True, timeout=3600, env=env)
            results.append({"depot": r["depot"], "exit": pr.returncode,
                            "out": str(outroot / r["depot"]),
                            "tail": (pr.stderr or pr.stdout or "")[-600:]})
        except Exception as e:
            results.append({"depot": r["depot"], "error": str(e)})
    res = {"ok": all(x.get("exit") == 0 for x in results if "exit" in x),
           "root": str(outroot), "results": results}
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return res

def cmd_open(a):
    game_dir = ST_CACHE_DIR / "games" / str(a.appid)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(game_dir)])
    else:
        subprocess.Popen(["explorer", str(game_dir)])

def cmd_shortcut(a):
    exe = a.exe or str(ST_CACHE_DIR / "games" / str(a.appid))
    name = a.name or f"App {a.appid}"
    print(json.dumps(add_shortcut(int(a.appid), exe, name, a.vdf or None), indent=2, ensure_ascii=False))

def cmd_apply_api(a):
    gd = a.dir or str(ST_CACHE_DIR / "games" / str(a.appid))
    print(json.dumps(apply_api(int(a.appid), gd, a.lib, {"configs.overlay.ini": ""}),
                     indent=2, ensure_ascii=False))

def cmd_restore_api(a):
    print(json.dumps(restore_api(int(a.appid)), indent=2))

def cmd_launch(a):
    target = a.path or str(ST_CACHE_DIR / "games" / str(a.appid))
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        os.startfile(target) if hasattr(os, "startfile") else subprocess.Popen(["explorer", target])
    print(json.dumps({"ok": True, "launched": target}))

def main():
    ap = argparse.ArgumentParser(prog="luatools-mac", description="LuaTools 1.3.2 engine — native macOS port")

    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("toml").set_defaults(fn=cmd_toml)
    sub.add_parser("restart").set_defaults(fn=cmd_restart)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    p = sub.add_parser("info"); p.add_argument("file"); p.add_argument("--appid", default="0"); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("install-lua"); p.add_argument("file"); p.add_argument("--appid", default="0")
    p.add_argument("--force-locked", action="store_true"); p.set_defaults(fn=cmd_install_lua)
    p = sub.add_parser("install-zip"); p.add_argument("zip"); p.add_argument("--appid", default="0")
    p.add_argument("--force-locked", action="store_true"); p.set_defaults(fn=cmd_install_zip)
    p = sub.add_parser("install-manifest"); p.add_argument("file"); p.set_defaults(fn=cmd_install_manifest)
    p = sub.add_parser("uninstall-lua"); p.add_argument("appid"); p.set_defaults(fn=cmd_uninstall_lua)
    p = sub.add_parser("vault"); p.add_argument("--appid", default="0"); p.set_defaults(fn=cmd_vault)
    p = sub.add_parser("dual"); p.add_argument("appid"); p.add_argument("depots")
    p.add_argument("--out", default=""); p.add_argument("--install", action="store_true")
    p.add_argument("--force-locked", action="store_true"); p.set_defaults(fn=cmd_dual)
    p = sub.add_parser("library"); p.set_defaults(fn=cmd_library)
    p = sub.add_parser("fetch-manifest"); p.add_argument("depot"); p.add_argument("manifest"); p.set_defaults(fn=cmd_fetch_manifest)
    p = sub.add_parser("fetch-zip"); p.add_argument("appid"); p.add_argument("--source", default=""); p.set_defaults(fn=cmd_fetch_zip)
    p = sub.add_parser("generate-dlc"); p.add_argument("appid"); p.add_argument("--base", default=""); p.set_defaults(fn=cmd_generate_dlc)
    p = sub.add_parser("check-apis"); p.add_argument("appid"); p.set_defaults(fn=cmd_check_apis)
    p = sub.add_parser("denuvo"); p.add_argument("--appid", default=""); p.set_defaults(fn=cmd_denuvo)
    p = sub.add_parser("fix-index"); p.set_defaults(fn=cmd_fix_index)
    p = sub.add_parser("plugin"); p.set_defaults(fn=cmd_plugin)
    p = sub.add_parser("downloader"); p.add_argument("lua"); p.add_argument("--arg", action="append"); p.set_defaults(fn=cmd_downloader)
    p = sub.add_parser("autocrack"); p.add_argument("appid"); p.set_defaults(fn=cmd_autocrack)
    p = sub.add_parser("fetch-game"); p.add_argument("appid"); p.add_argument("depots")
    p.add_argument("--os", dest="os", default="mac")
    p.add_argument("--include-shared", action="store_true", default=True)
    p.add_argument("--no-shared", dest="include_shared", action="store_false")
    p.add_argument("--user", default=""); p.add_argument("--pass", dest="pass_", default="")
    p.add_argument("--out", default=""); p.set_defaults(fn=cmd_fetch_game)
    p = sub.add_parser("open"); p.add_argument("appid"); p.set_defaults(fn=cmd_open)
    p = sub.add_parser("probe"); p.add_argument("appid"); p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("install-dd"); p.set_defaults(fn=cmd_install_dd)
    p = sub.add_parser("shortcut"); p.add_argument("appid")
    p.add_argument("--exe", default=""); p.add_argument("--name", default="")
    p.add_argument("--vdf", default=""); p.set_defaults(fn=cmd_shortcut)
    p = sub.add_parser("apply-api"); p.add_argument("appid")
    p.add_argument("--dir", default=""); p.add_argument("--lib"); p.set_defaults(fn=cmd_apply_api)
    p = sub.add_parser("restore-api"); p.add_argument("appid"); p.set_defaults(fn=cmd_restore_api)
    p = sub.add_parser("launch"); p.add_argument("appid")
    p.add_argument("--path", default=""); p.set_defaults(fn=cmd_launch)
    p = sub.add_parser("settings")
    p.add_argument("--steam-path", default="")
    p.add_argument("--token", default="")
    p.add_argument("--no-auto-update", action="store_true")
    p.add_argument("--auto-update", action="store_true")
    p.add_argument("--force-locked", action="store_true")
    p.set_defaults(fn=cmd_settings)
    args = ap.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
