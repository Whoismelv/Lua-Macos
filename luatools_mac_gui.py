#!/usr/bin/env python3
# language: python3.9+, file: luatools_mac_gui.py, runtime: stdlib only (tkinter + luatools_mac)
# GUI shell for the LuaTools engine. Every button maps to one engine call — no duplicated logic.
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "LuaTools Mac"
ACCENT = "#2f6f4e"
BG = "#f4f5f7"
CARD = "#ffffff"
MUTED = "#6b7280"


def load_engine():
    here = Path(__file__).resolve().parent
    for base in (here, here / "_internal", Path.cwd()):
        f = base / "luatools_mac.py"
        if f.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("luatools_mac", str(f))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["luatools_mac"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("luatools_mac.py not found next to the GUI")


lt = load_engine()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x680")
        self.minsize(860, 600)
        self.configure(bg=BG)
        self.logq = queue.Queue()
        self.serve_thread = None
        self._style()
        self._header()
        self._tabs()
        self.after(120, self._drain_log)
        self.refresh_library()
        self.log("engine online · " + sys.platform)
        self.log("steam root: " + str(self.steam_root() or "not detected — set it in Tools"))

    # ---------- chrome ----------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground="#111827", font=("Helvetica", 12))
        st.configure("TFrame", background=CARD)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TLabel", background=CARD, foreground="#111827")
        st.configure("H1.TLabel", font=("Helvetica", 20, "bold"), background=BG)
        st.configure("Muted.TLabel", foreground=MUTED, background=BG, font=("Helvetica", 11))
        st.configure("CardMuted.TLabel", foreground=MUTED, background=CARD, font=("Helvetica", 11))
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(18, 9), font=("Helvetica", 12))
        st.configure("TButton", padding=(12, 8))
        st.configure("Accent.TButton", background=ACCENT, foreground="white",
                     borderwidth=0, focusthickness=0)
        st.map("Accent.TButton", background=[("active", "#255d41")])
        st.configure("Treeview", rowheight=26, fieldbackground=CARD, background=CARD)
        st.configure("Treeview.Heading", font=("Helvetica", 11, "bold"))

    def _header(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="x", padx=22, pady=(18, 6))
        ttk.Label(wrap, text=APP_TITLE, style="H1.TLabel").pack(side="left")
        self.status = ttk.Label(wrap, text="● idle", style="Muted.TLabel")
        self.status.pack(side="right")

    def _tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=8)
        self.tab_library = ttk.Frame(nb, style="TFrame")
        self.tab_install = ttk.Frame(nb, style="TFrame")
        self.tab_tools = ttk.Frame(nb, style="TFrame")
        nb.add(self.tab_library, text="Library")
        nb.add(self.tab_install, text="Install")
        nb.add(self.tab_tools, text="Tools")
        self._build_library()
        self._build_install()
        self._build_tools()
        self._build_log()

    def _build_log(self):
        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", padx=16, pady=(0, 14))
        self.logbox = tk.Text(box, height=9, bg="#0f172a", fg="#cbd5e1", insertbackground="#cbd5e1",
                              relief="flat", font=("Menlo", 11), padx=12, pady=10, wrap="word")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.configure(state="disabled")

    # ---------- library ----------
    def _build_library(self):
        p = self.tab_library
        bar = tk.Frame(p, bg=CARD)
        bar.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Label(bar, text="Installed in Steam", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.refresh_library).pack(side="right")
        cols = ("appid", "name", "state", "build")
        self.tree = ttk.Treeview(p, columns=cols, show="headings", selectmode="browse")
        for c, w, t in (("appid", 100, "AppID"), ("name", 420, "Name"),
                        ("state", 110, "State"), ("build", 170, "BuildID")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=14, pady=6)

        row = tk.Frame(p, bg=CARD)
        row.pack(fill="x", padx=14, pady=(4, 14))
        ttk.Button(row, text="Open folder", command=self.open_selected).pack(side="left")
        ttk.Button(row, text="Add to library", command=self.shortcut_selected).pack(side="left", padx=8)
        ttk.Button(row, text="Remove lua", command=self.remove_selected).pack(side="left")
        ttk.Button(row, text="Use AppID in Install tab", command=self.push_selected).pack(side="right")

    def refresh_library(self):
        data = lt.library_list()
        self.tree.delete(*self.tree.get_children())
        if not data.get("ok"):
            self.log("library: " + str(data.get("error")))
            return
        for a in data["apps"]:
            self.tree.insert("", "end", values=(a["appid"], a["name"], a["state"], a["buildid"]))
        self.log(f"library: {data['count']} apps")

    def selected_appid(self):
        sel = self.tree.selection()
        return int(self.tree.item(sel[0], "values")[0]) if sel else None

    def open_selected(self):
        appid = self.selected_appid()
        if appid:
            self.bg(lambda: lt.cmd_open(type("A", (), {"appid": str(appid)})()))

    def shortcut_selected(self):
        appid = self.selected_appid()
        if not appid:
            return
        row = self.tree.item(self.tree.selection()[0], "values")
        guess = self.guess_bundle(appid, row[1])
        self.bg(lambda: lt.add_shortcut(appid, guess or str(lt.ST_CACHE_DIR / "games" / str(appid)),
                                        row[1]))

    def guess_bundle(self, appid, name):
        for lib in lt.read_vdf_paths(self.steam_root()) if self.steam_root() else []:
            acf = lib / f"appmanifest_{appid}.acf"
            if acf.exists():
                d = lt.parse_acf(acf)
                folder = lib / "common" / d.get("installdir", "")
                for app in folder.glob("*.app"):
                    return str(app)
                return str(folder)
        return str(lt.ST_CACHE_DIR / "games" / str(appid))

    def remove_selected(self):
        appid = self.selected_appid()
        if appid and messagebox.askyesno("Remove", f"Remove lua + manifests for {appid}?"):
            self.bg(lambda: lt.uninstall_lua(appid))
            self.after(600, self.refresh_library)

    def push_selected(self):
        appid = self.selected_appid()
        if appid:
            self.appid_var.set(str(appid))

    # ---------- install ----------
    def _build_install(self):
        p = self.tab_install
        top = tk.Frame(p, bg=CARD)
        top.pack(fill="x", padx=14, pady=(14, 6))
        ttk.Label(top, text="Find a game — type a name or AppID", font=("Helvetica", 14, "bold")).pack(anchor="w")
        row = tk.Frame(p, bg=CARD)
        row.pack(fill="x", padx=14, pady=6)
        self.query_var = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.query_var, font=("Helvetica", 13))
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda _e: self.search_store())
        ttk.Button(row, text="Search", style="Accent.TButton", command=self.search_store).pack(side="left", padx=8)

        self.results = tk.Listbox(p, height=7, font=("Helvetica", 12), activestyle="none",
                                  bg="#fbfbfc", relief="flat", highlightthickness=1,
                                  highlightbackground="#e5e7eb")
        self.results.pack(fill="x", padx=14, pady=6)
        self.results.bind("<<ListboxSelect>>", self.pick_result)
        self._search_hits = []

        form = tk.LabelFrame(p, text=" Install target ", bg=CARD, fg="#111827", padx=12, pady=10)
        form.pack(fill="x", padx=14, pady=8)
        self.appid_var = tk.StringVar()
        self.depots_var = tk.StringVar()
        self.dir_var = tk.StringVar()
        self.lib_var = tk.StringVar()
        self._row(form, "AppID", self.appid_var, None, 0)
        self._row(form, "Depot list (csv)", self.depots_var, self.pick_file, 1)
        self._row(form, "Game folder / .app", self.dir_var, self.pick_dir, 2)
        self._row(form, "steam_api.dylib", self.lib_var, self.pick_dylib, 3)

        act = tk.Frame(p, bg=CARD)
        act.pack(fill="x", padx=14, pady=(4, 14))
        ttk.Button(act, text="Detect depots", command=self.do_probe).pack(side="left")
        ttk.Button(act, text="1. Fetch content", command=self.do_fetch).pack(side="left", padx=8)
        ttk.Button(act, text="2. Apply steam_api", command=self.do_apply).pack(side="left", padx=8)
        ttk.Button(act, text="3. Add to library", command=self.do_shortcut).pack(side="left")
        ttk.Button(act, text="Launch", style="Accent.TButton", command=self.do_launch).pack(side="right")

    def _row(self, parent, label, var, picker, r):
        ttk.Label(parent, text=label, style="CardMuted.TLabel", width=18, anchor="w").grid(
            row=r, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
        if picker:
            ttk.Button(parent, text="…", width=3, command=picker).grid(row=r, column=2)
        parent.columnconfigure(1, weight=1)

    def pick_file(self):
        f = filedialog.askopenfilename(filetypes=[("Depot csv", "*.txt *.csv"), ("All", "*.*")])
        if f:
            self.depots_var.set(f)

    def pick_dir(self):
        f = filedialog.askdirectory()
        if f:
            self.dir_var.set(f)

    def pick_dylib(self):
        f = filedialog.askopenfilename(filetypes=[("dylib", "*.dylib"), ("All", "*.*")])
        if f:
            self.lib_var.set(f)

    def search_store(self):
        q = self.query_var.get().strip()
        if not q:
            return
        if q.isdigit():
            self.appid_var.set(q)
            self.log(f"appid set: {q}")
            return
        self.status.configure(text="● searching")
        self.bg(lambda: self._store_search(q), quiet=True)

    def _store_search(self, term):
        url = ("https://store.steampowered.com/api/storesearch/?term="
               + urllib.parse.quote(term) + "&l=english&cc=US")
        req = urllib.request.Request(url, headers={"User-Agent": "luatools-mac"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        hits = [(i["id"], i["name"]) for i in data.get("items", [])][:25]
        self.after(0, lambda: self._fill_results(hits))

    def _fill_results(self, hits):
        self._search_hits = hits
        self.results.delete(0, "end")
        for appid, name in hits:
            self.results.insert("end", f"{appid:>8}   {name}")
        self.status.configure(text=f"● {len(hits)} results")
        self.log(f"search: {len(hits)} hits")

    def pick_result(self, _e=None):
        sel = self.results.curselection()
        if sel:
            self.appid_var.set(str(self._search_hits[sel[0]][0]))

    # ---------- install actions ----------
    def _need(self, var, label):
        if not var.get().strip():
            messagebox.showinfo("Missing", f"{label} is empty")
            return False
        return True

    def do_probe(self):
        if not self._need(self.appid_var, "AppID"):
            return

        def work():
            res = lt.probe_app(self.appid_var.get().strip())
            if res.get("ok"):
                out = lt.ST_CACHE_DIR / "out" / f"{res['appid']}_depots.csv"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(lt.rows_to_csv(res["rows"]))
                self.after(0, lambda: self.depots_var.set(str(out)))
                self.log(f"probe: {res['mac']} mac depot(s), {res['count']} total, "
                         f"build {res['buildid']} -> {out}")
                self.log(lt.rows_to_csv([r for r in res["rows"] if r["os"] in ("mac", "shared")]))
            else:
                self.log("probe failed: " + str(res.get("error")))
            return res
        self.bg(work, quiet=True)

    def do_fetch(self):
        if not self._need(self.appid_var, "AppID"):
            return
        depots = self.depots_var.get().strip() or "auto"
        a = type("A", (), {"appid": self.appid_var.get(), "depots": depots,
                           "os": "mac", "include_shared": True, "user": "", "pass_": "", "out": ""})

        def work():
            if lt._dd_tool() is None:
                self.log("DepotDownloader missing — installing…")
                r = lt.install_depotdownloader(lt.load_settings().get("auth_token", ""))
                self.log(json.dumps(r, indent=2)[:1200])
            return lt.cmd_fetch_game(a)
        self.bg(work)
        self.log(f"fetching mac depots for {self.appid_var.get().strip()} — this can take a while")

    def do_apply(self):
        if not (self._need(self.appid_var, "AppID") and self._need(self.lib_var, "dylib")):
            return
        gd = self.dir_var.get().strip() or str(lt.ST_CACHE_DIR / "games" / self.appid_var.get())
        self.bg(lambda: lt.apply_api(int(self.appid_var.get()), gd, self.lib_var.get(),
                                     {"configs.overlay.ini": ""}))

    def do_shortcut(self):
        appid = self.appid_var.get().strip()
        if not appid.isdigit():
            messagebox.showinfo("Missing", "AppID must be numeric")
            return
        exe = self.dir_var.get().strip() or str(lt.ST_CACHE_DIR / "games" / appid)
        self.bg(lambda: lt.add_shortcut(int(appid), exe, f"App {appid}"))

    def do_launch(self):
        target = self.dir_var.get().strip() or str(lt.ST_CACHE_DIR / "games" / self.appid_var.get().strip())
        self.bg(lambda: lt.cmd_launch(type("A", (), {"appid": "0", "path": target})()))

    # ---------- tools ----------
    def _build_tools(self):
        p = self.tab_tools
        ttk.Label(p, text="Engine", font=("Helvetica", 14, "bold")).pack(anchor="w", padx=14, pady=(14, 4))
        grid = tk.Frame(p, bg=CARD)
        grid.pack(fill="x", padx=14)
        btns = [
            ("Detect Steam", self.t_detect),
            ("Write opensteamtool.toml", self.t_toml),
            ("Migrate depotcache", self.t_migrate),
            ("Restart Steam", self.t_restart),
            ("Start injector", self.t_serve),
            ("Fetch web plugin", self.t_plugin),
            ("Vault dump", self.t_vault),
            ("Denuvo listings", self.t_denuvo),
            ("Install DepotDownloader", self.t_install_dd),
            ("Settings", self.t_settings),
        ]
        for i, (label, cmd) in enumerate(btns):
            ttk.Button(grid, text=label, command=cmd).grid(row=i // 3, column=i % 3,
                                                           sticky="ew", padx=5, pady=5)
        for c in range(3):
            grid.columnconfigure(c, weight=1)
        ttk.Label(p, text="Steam root", style="CardMuted.TLabel").pack(anchor="w", padx=14, pady=(14, 0))
        self.root_var = tk.StringVar(value=str(self.steam_root() or ""))
        r = tk.Frame(p, bg=CARD)
        r.pack(fill="x", padx=14, pady=4)
        ttk.Entry(r, textvariable=self.root_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="…", width=3, command=self.pick_root).pack(side="left", padx=4)
        ttk.Button(r, text="Save", command=self.save_root).pack(side="left")
        ttk.Label(p, text="Bridge runs on 127.0.0.1:6767 while the injector is on.",
                  style="CardMuted.TLabel").pack(anchor="w", padx=14, pady=(10, 14))

    def steam_root(self):
        return lt.SteamCtx().root()

    def pick_root(self):
        f = filedialog.askdirectory()
        if f:
            self.root_var.set(f)

    def save_root(self):
        s = lt.load_settings()
        s["steam_path"] = self.root_var.get().strip()
        lt.save_settings(s)
        self.log("steam path saved: " + s["steam_path"])

    def t_detect(self):
        ctx = lt.SteamCtx()
        self.log(f"root={ctx.root()}  depotcache={ctx.depot_cache_dir()}")
        self.root_var.set(str(ctx.root() or ""))

    def t_toml(self):
        r = self.steam_root()
        if r:
            ok = lt.toml_ensure_lua_path(r)
            self.log(f"toml lua path: {ok}")

    def t_migrate(self):
        self.bg(lt.migrate_depotcache)

    def t_restart(self):
        def cycle():
            ctx = lt.SteamCtx()
            ctx.stop_steam()
            ctx.start_steam()
            return {"ok": True, "restarted": True}
        self.bg(cycle)

    def t_serve(self):
        if self.serve_thread and self.serve_thread.is_alive():
            self.log("injector already running")
            return
        self.serve_thread = threading.Thread(target=lambda: lt.cmd_serve(None), daemon=True)
        self.serve_thread.start()
        self.log("bridge http://127.0.0.1:6767 · injector armed")

    def t_plugin(self):
        self.bg(lambda: lt.fetch_plugin(lt.load_settings().get("auth_token", "")))

    def t_vault(self):
        self.bg(lambda: lt.vault_list())

    def t_denuvo(self):
        self.bg(lambda: lt.denuvo_listings(lt.load_settings().get("auth_token", "")))

    def t_install_dd(self):
        self.bg(lambda: lt.install_depotdownloader(lt.load_settings().get("auth_token", "")))

    def t_settings(self):
        self.log(json.dumps(lt.load_settings(), indent=2))

    # ---------- plumbing ----------
    def bg(self, fn, quiet=False):
        def work():
            try:
                res = fn()
                if not quiet and res is not None:
                    self.log(json.dumps(res, indent=2, ensure_ascii=False, default=str)[:4000])
                self.after(0, lambda: self.status.configure(text="● idle"))
            except Exception as e:
                self.log("ERROR " + repr(e))
                self.after(0, lambda: self.status.configure(text="● error"))
        self.status.configure(text="● working")
        threading.Thread(target=work, daemon=True).start()

    def log(self, msg):
        self.logq.put(str(msg))

    def _drain_log(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.logbox.configure(state="normal")
                self.logbox.insert("end", msg.rstrip() + "\n")
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        app = App()
        app.update()
        app.destroy()
        print("selftest ok")
    else:
        App().mainloop()
