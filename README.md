# LuaTools Mac

Native macOS front-end for the LuaTools engine (reversed from `LuaTools.dll` 1.3.2 and ported to Python stdlib).
No CrossOver, no Wine, no game emulation — the mac build runs on macOS.

| file | role |
|---|---|
| `luatools_mac.py` | engine — 31 CLI commands (lua install, manifests, vault, toml, injector, probe, fetch-game, apply-api, shortcuts) |
| `luatools_mac_gui.py` | desktop shell — pywebview window, `Api` bridge into the engine |
| `ui/index.html` | the interface (single file, no CDN, no build step) |
| `build_macos.sh` | builds `LuaToolsMac.app` + `LuaToolsMac.dmg` |
| `run_mac.command` | run straight from source, no build |
| `icon.icns` | app icon |

## run without building

```bash
chmod +x run_mac.command
./run_mac.command
```

The first run creates `.venv/` and installs `pywebview` (the only dependency; it drives the system WKWebView).

## build the .app + .dmg

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Outputs `dist/LuaToolsMac.app` and `dist/LuaToolsMac.dmg` (drag-to-Applications layout included).
Needs Xcode Command Line Tools (`xcode-select --install`); `hdiutil` ships with macOS.

Unsigned build blocked by Gatekeeper:

```bash
xattr -dr com.apple.quarantine "/Applications/LuaTools Mac.app"
```

No Mac at hand? `.github/workflows/build-dmg.yml` builds the DMG on a macOS runner and uploads it as an artifact;
`release.yml` attaches it to a tag.

## one-time tools

```bash
python3 luatools_mac.py install-dd     # DepotDownloader binary for this arch (no dotnet needed)
brew install --cask steamcmd           # optional: auto-detect depots/manifests
```

## the pipeline

```
search name/AppID  ->  probe (steamcmd appinfo: mac depots + manifest gids)
                   ->  fetch-game (DepotDownloader pulls the mac depots)
                   ->  apply-api (swap libsteam_api.dylib + steam_settings)
                   ->  shortcut (entry appears in the Steam library)
                   ->  launch
```

CLI equivalents:

```bash
python3 luatools_mac.py probe 620 --out depots.csv
python3 luatools_mac.py fetch-game 620 depots.csv --os mac
python3 luatools_mac.py apply-api 620 --dir "~/620/623" --lib ~/Downloads/libsteam_api.dylib
python3 luatools_mac.py shortcut 620 --exe "/path/Portal 2.app" --name "Portal 2"
python3 luatools_mac.py launch 620
```

`fetch-game` accepts `auto` instead of a csv — it probes steamcmd and filters to mac + shared depots.

## UI

- **Overview** — luas on board, mac depots ready, Steam detected; the five most recent library entries, readiness rows, and the drop zone.
- **Add a game** — store search by name, **Detect depots**, **Fetch content**, **Apply steam_api**, **Add to library**, **Launch**.
- **Library** — every `appmanifest_*.acf` across all Steam libraries: AppID / name / state / build id, open folder, add shortcut, remove lua.
- **Depots** — the depot ladder (mac / shared / win / linux counts) plus the manifest table from SteamCMD.
- **Fixes** — detect Steam, rewrite `opensteamtool.toml`, migrate depotcache, restart Steam, arm CDP injector + bridge (`127.0.0.1:6767`), fetch web plugin, vault, Denuvo listings, install DepotDownloader.
- **Logs** — engine output streamed from the Python log buffer.
- **Settings** — Steam root override, build info.

Every control maps to one engine function; the shell duplicates no logic. `luatools_mac_gui.py --selftest`
prints engine state without opening a window (used by CI).

## verified on real data

- `probe 620` (Portal 2) → mac depot `623`, manifest `2276453204441793614`, build `25240193`
- `probe 480` → 1 depot; `fetch-game 480 auto` → DepotDownloader downloaded 797,632 bytes into `481/`
- `shortcut` round-trip on a binary `shortcuts.vdf`; `apply-api`/`restore-api` swap and restore a dylib

## hard limits (native macOS, no CrossOver)

- Lua scripts and manifest ids do nothing to a stock Mac Steam client — there is no mac build of OpenSteamTool / Millennium.
- "Appears in the library" = a Steam shortcut entry. Content is downloaded by DepotDownloader from the depot manifests you supply, not by Valve's license servers.
- The game must ship a macOS build. `probe` reports `mac: 0` for Windows-only titles — those cannot run natively.
- Encrypted depots need a depot key (community lua files / SteamDB); `probe` supplies the manifest gid only.
