# LicenseTools for macOS

أدوات ترخيص ستيم لنظام macOS — توليد ملفات الـ lua، تثبيتها في `config/stplug-in`، تثبيت المانيفست، ونزول المحتوى عبر DepotDownloader.

A Steam license kit for macOS: probe public appinfo, generate `stplug-in` lua (`addappid` / `setManifestid`), pin manifests, fetch depot content.

---

## بالعربي

### الشغلة بتشتغل هيك — زر واحد، كل شيء جوّه البرنامج

1. افتح **LicenseTools** واكتب اسم اللعبة أو الـ appid.
2. اضغط **Fetch → Library**.
3. البرنامج بيعمل كل شي لحاله: بيفحص الـ loader، وإذا مش منصّب بينصّبه من الـ payload المخزّن جوّاه (بدون تحميل ولا برنامج خارجي)، بيجيب الديبوتات، بيكتب الـ lua + إعدادات macSteam، وبيعيد تشغيل ستيم.
4. اللعبة بتظهر بالمكتبة. خلص.

> بدك ملفات اللعبة نفسها؟ حط اسم حسابك وكلمة السر واضغط **Fetch Content** — بينزل على `~/.licensetools/games/`.
> تبويب **Loader** فيه نفس الأزرار يدوياً لو احتجت تعيد التنصيب.
> الـ loader (macSteam dylib + signatures) متحمّل جوّه التطبيق وقت البناء — ما بتنزّل ولا برنامج تاني.

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

### Flow — one button, everything inside the app

1. Open **LicenseTools** and type the game name or appid.
2. Press **Fetch → Library**.
3. The app does the rest by itself: checks the loader, injects it from the payload bundled inside the app (no downloads, no external program), probes depots, writes the lua + macSteam config, and restarts Steam.
4. The game shows up in your library. Done.

Want the actual game files too? Fill your Steam user/pass → **Fetch Content** — downloads into `~/.licensetools/games/`.
The **Loader** tab keeps the same actions manually if you ever need to re-inject.
The loader payload (macSteam dylib + signatures) is vendored into the app at build time — nothing to install on your side.

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
