#!/usr/bin/env python3
# language: python3.9+, file: luatools_mac_gui.py, runtime: pywebview + luatools_mac
# Native WKWebView shell for the LuaTools engine. The UI is ui/index.html; every call here
# maps to one engine function — no duplicated logic.
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

APP_TITLE = "LuaTools"
APP_VERSION = "1.0.3"
HERE = Path(__file__).resolve().parent


def resource(name):
    roots = [HERE, HERE / "_internal", HERE.parent / "Resources", HERE.parent / "Frameworks",
             Path(getattr(sys, "_MEIPASS", "") or HERE)]
    for root in roots:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def load_engine():
    try:
        import luatools_mac as engine
        return engine
    except ImportError:
        pass
    import importlib.util
    for root in [HERE, HERE / "_internal", HERE.parent / "Frameworks",
                 Path(getattr(sys, "_MEIPASS", "") or HERE), Path.cwd()]:
        f = root / "luatools_mac.py"
        if f.exists():
            spec = importlib.util.spec_from_file_location("luatools_mac", str(f))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["luatools_mac"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("luatools_mac.py not found next to the GUI")


def crash_log_path():
    return Path.home() / ".luatools-mac" / "gui-crash.log"


lt = load_engine()


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


def osascript(lines, timeout=120):
    args = ["osascript"]
    for line in lines:
        args += ["-e", line]
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def pick_files_native():
    if sys.platform != "darwin":
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        files = filedialog.askopenfilenames(
            title="Add to LuaTools",
            filetypes=[("LuaTools files", "*.lua *.manifest *.zip"), ("All files", "*.*")])
        root.destroy()
        return list(files)
    out = osascript([
        'set theFiles to choose file with prompt "Select .lua, .manifest or .zip" '
        "with multiple selections allowed",
        'set acc to ""',
        "repeat with f in theFiles",
        "set acc to acc & (POSIX path of f) & linefeed",
        "end repeat",
        "return acc",
    ])
    return [line for line in out.splitlines() if line.strip()]


def pick_folder_native():
    if sys.platform != "darwin":
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory()
        root.destroy()
        return folder or ""
    return osascript(['set f to choose folder with prompt "Select a folder"',
                      "return POSIX path of f"])


def safe_settings():
    raw = lt.load_settings() or {}
    keep = ("account", "username", "steam_path", "mode")
    return {k: raw.get(k) for k in keep if raw.get(k)}


class Api:
    def __init__(self):
        self.serve_thread = None
        self.lock = threading.Lock()

    # ---------- state ----------
    def bootstrap(self):
        lib = lt.library_list()
        settings = safe_settings()
        root = ""
        try:
            root = str(lt.SteamCtx().root() or "")
        except Exception as exc:
            LOGS.add(f"steam detect failed: {exc}")
        LOGS.add(f"engine online · {sys.platform}")
        LOGS.add(f"steam root: {root or 'not detected — set it in Settings'}")
        return {
            "ok": True,
            "library": lib.get("apps", []) if lib.get("ok") else [],
            "count": lib.get("count", 0) if lib.get("ok") else 0,
            "steam_root": root,
            "plugin": Path(lt.ST_FRONTEND_DIR).exists(),
            "settings": settings,
            "platform": sys.platform,
            "version": APP_VERSION,
            "engine": str(getattr(lt, "__file__", "bundled")),
            "user": settings.get("account") or settings.get("username") or "Local account",
        }

    def logs_since(self, index):
        lines, size = LOGS.since(index)
        return {"lines": lines, "index": size}

    # ---------- store ----------
    def search(self, term):
        term = (term or "").strip()
        if not term:
            return []
        url = ("https://store.steampowered.com/api/storesearch/?term="
               + urllib.parse.quote(term) + "&l=english&cc=US")
        req = urllib.request.Request(url, headers={"User-Agent": "luatools-mac"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        hits = [{"appid": str(i["id"]), "name": i["name"]} for i in data.get("items", [])][:25]
        LOGS.add(f"search: {len(hits)} hits for “{term}”")
        return hits

    # ---------- depots ----------
    def probe(self, appid):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "AppID must be numeric"}
        LOGS.add(f"probe {appid}: asking steamcmd…")
        res = lt.probe_app(appid)
        if not res.get("ok"):
            LOGS.add("probe failed: " + str(res.get("error")))
            return res
        out = lt.ST_CACHE_DIR / "out" / f"{res['appid']}_depots.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(lt.rows_to_csv(res["rows"]))
        res["csv"] = str(out)
        LOGS.add(f"probe {appid}: {res['mac']} mac depot(s), {res['count']} total, "
                 f"build {res['buildid']}")
        for row in res["rows"]:
            if row.get("os") == "mac":
                LOGS.add(f"  mac depot {row['depot']} · manifest {row['manifest']}")
        return res

    # ---------- content ----------
    def fetch(self, appid, depots):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "AppID must be numeric"}
        if lt._dd_tool() is None:
            LOGS.add("DepotDownloader missing — installing…")
            install = lt.install_depotdownloader(safe_settings().get("auth_token", ""))
            LOGS.add("install-dd: " + json.dumps(install)[:600])
            if not install.get("ok"):
                return install
        args = type("A", (), {
            "appid": appid,
            "depots": (depots or "").strip() or "auto",
            "os": "mac",
            "include_shared": True,
            "user": "",
            "pass_": "",
            "out": "",
        })()
        LOGS.add(f"fetch {appid}: downloading mac depots (this can take a while)")
        res = lt.cmd_fetch_game(args)
        for item in res.get("results", []):
            if item.get("error"):
                LOGS.add(f"  depot {item.get('depot')}: ERROR {item['error']}")
            else:
                tail = (item.get("tail") or "").strip().splitlines()[-3:]
                for line in tail:
                    LOGS.add("  " + line)
        return res

    def apply(self, appid, game_dir, dylib):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "AppID must be numeric"}
        if not dylib:
            return {"ok": False, "error": "steam_api.dylib is required"}
        target = game_dir or str(lt.ST_CACHE_DIR / "games" / appid)
        LOGS.add(f"apply {appid}: swapping {Path(dylib).name} into {target}")
        res = lt.apply_api(int(appid), target, dylib, {"configs.overlay.ini": ""})
        LOGS.add("apply: " + json.dumps(res)[:600])
        return res

    def shortcut(self, appid, game_dir, name=""):
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return {"ok": False, "error": "AppID must be numeric"}
        exe = game_dir or str(lt.ST_CACHE_DIR / "games" / appid)
        label = name or f"App {appid}"
        LOGS.add(f"shortcut {appid}: {exe}")
        res = lt.add_shortcut(int(appid), exe, label)
        LOGS.add("shortcut: " + json.dumps(res)[:600])
        return res

    def launch(self, target):
        LOGS.add(f"launch: {target}")
        return lt.cmd_launch(type("A", (), {"appid": "0", "path": target})())

    def open_folder(self, appid):
        return lt.cmd_open(type("A", (), {"appid": str(appid)})())

    def remove_lua(self, appid):
        LOGS.add(f"remove lua {appid}")
        return lt.uninstall_lua(int(appid))

    # ---------- files ----------
    def pick_files(self):
        return pick_files_native()

    def pick_folder(self):
        return pick_folder_native()

    def add_files(self, paths):
        added = 0
        for path in paths or []:
            p = Path(path)
            suffix = p.suffix.lower()
            LOGS.add(f"add: {p.name}")
            if suffix == ".lua":
                res = lt.install_lua(str(p))
            elif suffix == ".manifest":
                res = lt.install_manifest(str(p))
            elif suffix == ".zip":
                res = lt.install_zip(str(p))
            else:
                LOGS.add(f"  skipped unsupported file: {p.name}")
                continue
            added += 1
            LOGS.add("  " + json.dumps(res)[:400])
        return {"ok": True, "added": added}

    # ---------- tools ----------
    def tool(self, name):
        name = str(name or "")
        LOGS.add(f"tool: {name}")
        try:
            if name == "detect":
                ctx = lt.SteamCtx()
                LOGS.add(f"root={ctx.root()}  depotcache={ctx.depot_cache_dir()}")
                return {"ok": True, "root": str(ctx.root() or "")}
            if name == "toml":
                root = lt.SteamCtx().root()
                return {"ok": bool(root) and lt.toml_ensure_lua_path(root)}
            if name == "migrate":
                return lt.migrate_depotcache()
            if name == "restart":
                ctx = lt.SteamCtx()
                ctx.stop_steam()
                ctx.start_steam()
                return {"ok": True, "restarted": True}
            if name == "serve":
                if self.serve_thread and self.serve_thread.is_alive():
                    return {"ok": True, "already": True}
                self.serve_thread = threading.Thread(target=lambda: lt.cmd_serve(None), daemon=True)
                self.serve_thread.start()
                return {"ok": True, "bridge": "http://127.0.0.1:6767"}
            if name == "plugin":
                return lt.fetch_plugin(safe_settings().get("auth_token", ""))
            if name == "vault":
                return lt.vault_list()
            if name == "denuvo":
                return lt.denuvo_listings(safe_settings().get("auth_token", ""))
            if name == "install-dd":
                return lt.install_depotdownloader(safe_settings().get("auth_token", ""))
            if name == "install-steamcmd":
                return lt.install_steamcmd()
            if name == "ensure-tools":
                return lt.ensure_tools()
            if name == "settings":
                LOGS.add(json.dumps(lt.load_settings(), indent=2)[:2000])
                return {"ok": True}
            return {"ok": False, "error": f"unknown tool {name}"}
        except Exception as exc:
            LOGS.add(f"tool {name} failed: {exc!r}")
            return {"ok": False, "error": str(exc)}

    def save_steam_path(self, path):
        settings = lt.load_settings()
        settings["steam_path"] = (path or "").strip()
        lt.save_settings(settings)
        LOGS.add("steam path saved: " + settings["steam_path"])
        return {"ok": True, "path": settings["steam_path"]}


def _provision_tools():
    try:
        lt.ensure_tools()
    except Exception as exc:
        try:
            LOGS.add("tool provisioning failed: %r" % (exc,))
        except Exception:
            pass


def run():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--engine-selftest" in sys.argv or "--selftest" in sys.argv:
        api = Api()
        data = api.bootstrap()
        print("engine:", data["engine"])
        print("steam root:", data["steam_root"] or "not detected")
        print("library:", data["count"])
        print("selftest ok")
        return

    index = resource("ui/index.html")
    if index is None:
        raise RuntimeError("ui/index.html is missing from the bundle")

    import webview
    api = Api()
    threading.Thread(target=_provision_tools, daemon=True).start()
    webview.create_window(
        APP_TITLE,
        str(index),
        js_api=api,
        width=1180,
        height=800,
        min_size=(1040, 700),
        background_color="#08090B",
        text_select=False,
        confirm_close=False,
    )
    webview.start(debug=os.environ.get("LUATOOLS_DEBUG") == "1")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        import traceback
        log = crash_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        msg = f"LuaTools failed to start: {exc} — log: {log}"
        if sys.platform == "darwin":
            safe = msg.replace('"', "'")[:400]
            try:
                subprocess.Popen(["osascript", "-e",
                                  f'display dialog "{safe}" buttons {{"OK"}} with icon caution'])
            except Exception:
                pass
        print(msg, file=sys.stderr)
        sys.exit(1)
