# LicenseTools for macOS

أدوات ترخيص ستيم لنظام macOS — توليد ملفات الـ lua، تثبيتها في `config/stplug-in`، تثبيت المانيفست، ونزول المحتوى عبر DepotDownloader.

A Steam license kit for macOS: probe public appinfo, generate `stplug-in` lua (`addappid` / `setManifestid`), pin manifests, fetch depot content.

---

## بالعربي

### الشغلة بتشتغل هيك — كل شيء من داخل البرنامج، بدون terminal

1. افتح **LicenseTools** — تبويب **Loader** → اضغط **Install macSteam Loader** (بينزّل تلقائي ويحطه بـ Applications).
2. افتح **macSteam Config** → اضغط **Install** → وافق على الإذن → اقفل ستيم.
3. تبويب **Grant** → اكتب الـ appid أو اضغط Search واختار → **Grant** → **Restart Steam** → اللعبة بالمكتبكة.
4. بدك ملفات اللعبة نفسها؟ حط اسم حسابك وكلمة السر وتبويب Grant → **Fetch Content** — بينزل على ~/.licensetools/games/.

> مجلد `stplug-in` لحاله ما بيعمل شي على ستيم نظيف — لازم يكون في loader شغّال (macSteam او SteamTools). كل الأزرار فوق من داخل البرنامج.

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

### Flow — everything is a button in the app, no terminal needed

1. Open **LicenseTools** → **Loader** tab → **Install macSteam Loader** (auto-downloads the current fork, stages it into Applications).
2. Open **macSteam Config** → press **Install** → approve the permission prompt → quit Steam.
3. **Grant** tab → type the appid or hit Search → **Grant** → **Restart Steam** → game shows as owned.
4. Want the actual game files too? Fill your Steam user/pass on the Grant tab → **Fetch Content** — downloads into `~/.licensetools/games/`.

The `stplug-in` folder alone does nothing on a stock client — a loader (macSteam or SteamTools) is required. Every step above is a button; the CLI commands below are the same engine for power users.

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
