# LuaTools Mac — build & run

Two files matter:

| file | role |
|---|---|
| `luatools_mac.py` | engine — all 28 CLI commands (lua install, manifests, vault, toml, injector, fetch-game, apply-api, shortcuts) |
| `luatools_mac_gui.py` | desktop UI (tkinter, stdlib only) — Library / Install / Tools + console |

## run without building

```bash
chmod +x run_mac.command
./run_mac.command
```

or: `python3 luatools_mac_gui.py`

## build the .app + .dmg

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Outputs `dist/LuaToolsMac.app` and `dist/LuaToolsMac.dmg`.
Needs Xcode Command Line Tools (`xcode-select --install`) for `codesign`, and `hdiutil` (ships with macOS).

If macOS refuses the unsigned app:

```bash
xattr -dr com.apple.quarantine "/Applications/LuaTools Mac.app"
```

## no Mac handy?

Push the folder to GitHub — `.github/workflows/build-dmg.yml` builds the DMG on a macOS runner and uploads it as an artifact.

## what the UI does

- **Library** — reads every `appmanifest_*.acf` across all Steam libraries, shows AppID / name / state / build id, opens game folders, writes Steam shortcuts, removes lua.
- **Install** — type a game name (Steam store search) or AppID → **Detect depots** (steamcmd appinfo probe fills the mac depot list) → **Fetch content → Apply steam_api → Add to library → Launch**.
- **Tools** — detect Steam, rewrite `opensteamtool.toml`, migrate depotcache, restart Steam, arm the CDP injector + bridge (127.0.0.1:6767), fetch the web plugin, dump the vault, Denuvo listings.

## hard limits (native macOS, no CrossOver)
- Lua scripts and manifest ids do nothing to a stock Mac Steam client — there is no mac build of OpenSteamTool / Millennium. Nothing in this app pretends otherwise.
- "Appears in the library" = a Steam shortcut entry. The download is done by `DepotDownloader` from the depot manifests you supply, not by Valve's license servers.
- Game must ship a macOS build and a Steamworks API dylib that a replacement (`libsteam_api.dylib`) can satisfy.
