# LicenseTools for macOS

أدوات ترخيص ستيم لنظام macOS — توليد ملفات الـ lua، تثبيتها في `config/stplug-in`، تثبيت المانيفست، ونزول المحتوى عبر DepotDownloader.

A Steam license kit for macOS: probe public appinfo, generate `stplug-in` lua (`addappid` / `setManifestid`), pin manifests, fetch depot content.

---

## بالعربي

### الشغلة بتشتغل هيك

1. `loader status` — بتشوف اذا في loader مثبت عندك (محرك الـ lua اللي بيفضح `addappid`).
2. `loader install --from macsteam` — بتنزّل macSteam (Apple Silicon) وتحطه جاهز.
3. `grant APPID` — بتجيب الديبوتات mac + shared من appinfo، وبتكتب `config/stplug-in/APPID.lua`.
4. `restart` — ستيم بيرجع وبيحمّل الـ lua، واللعبة بتظهر بمكتبتك.

> ملاحظة: مجلد `stplug-in` لحاله ما بيعمل شي على ستيم نظيف — لازم يكون في loader شغّال (macSteam او SteamTools).

### أوامر

| الأمر | الشغلة |
|---|---|
| `detect` | جذر ستيم، الـ loader، الملفات المثبتة |
| `probe APPID` | الديبوتات والـ manifest gids من appinfo |
| `grant APPID` | توليد + تثبيت الـ lua (`--os mac` `--branch` `--no-pin` `--dry-run`) |
| `remove APPID` | مسح ملف الـ lua |
| `loader status` / `loader install` | فحص وتثبيت الـ loader |
| `fetch APPID` | نزول المحتوى عبر DepotDownloader (`--user` / `--pass`) |
| `library` / `list` / `restart` / `ensure` / `settings` / `gui` | الباقي مثل ما هو |

الوقت اللي بتحفظ فيه ملفات الـ lua القديمة: `~/.licensetools/vault/`.

---

## English

### Flow

1. `loader status` — see whether a lua license loader is present (the engine that exposes `addappid`).
2. `loader install --from macsteam` — stage macSteam (Apple Silicon) for injection.
3. `grant APPID` — probe mac + shared depots from appinfo, write `config/stplug-in/APPID.lua`.
4. `restart` — Steam reloads stplug-in and the game shows as owned in your library.

The `stplug-in` folder alone does nothing on a stock client — a loader (macSteam or SteamTools) is required.

### Commands

```
detect                          steam root, loader, installed luas
probe APPID [--os mac] [--branch public] [--appinfo FILE] [--csv OUT]
grant APPID [--os mac] [--branch B] [--no-pin] [--dry-run] [--out FILE]
remove APPID
install FILE [--appid N]        local .lua / .manifest / .zip
loader status | loader install [--from macsteam|steamtools]
fetch APPID [--user U --pass P] DepotDownloader content download
library | list | restart | ensure | settings | gui
```

Vault: `~/.licensetools/vault/` keeps every generated lua per branch.

### Install

Grab `LicenseTools.dmg` from the releases page, drag to Applications. First launch: right-click → Open (ad-hoc signed).

Requires macOS on Apple Silicon for the macSteam loader path. Steam must be installed.

### Build from source

```bash
bash build_licensetools.sh   # -> dist/LicenseTools.dmg
python3 licensetools_mac.py --selftest
```

Bundled: DepotDownloader (vendored at build time). steamcmd and the loader are fetched on first use into `~/.licensetools/tools/`.

---

@oll.fx and killywilly cooked something dangerous https://discord.gg/ojailbreaks
