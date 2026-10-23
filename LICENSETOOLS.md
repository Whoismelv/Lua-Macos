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

Want the actual game files too? Press **Download files** — the app pulls the game depots straight into
`~/Library/Application Support/Steam/steamapps/common/<installdir>`, writes a real `appmanifest_<appid>.acf`,
and restarts Steam so the Play button launches real content. DepotDownloader needs a Steam login that owns the
game; without one it asks Steam to download instead.
The **Loader** tab keeps the same actions manually if you ever need to re-inject.
The **Maintenance** tab is GUI-only housekeeping: show the resolved Steam paths, repair a stale install record,
delete a game (record + files + staging + library cache + license line), open the Steam folder or the game
folder in Finder, and remove the loader from `Steam.app` to get a clean client back.
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

### What changed in 1.0.9

The loader injects depots into a package from the `DepotKeys:` groups in its config. Before this build the app wrote only `Apps:` and `PackageIds:`, so Steam saw the game as owned but with **zero depots** — the install finished instantly with 0 bytes and Play said the executable was missing.

- The config now gets a `DepotKeys:` group per app, one line per depot.
- **Import .lua** (Grant tab): pick a SteamTools-style `.lua`; the app installs it into `config/stplug-in` and copies its app id, depots and 64-hex depot keys into the loader config. Keys already on disk are reused automatically on every grant/unlock.
- Depot lines in the log now carry the real DepotDownloader error per depot instead of `install failed: None`.
- **Download files** writes the loader config first and stops with a clear message if the depot pull lands 0 bytes.
- CLI: `import-lua FILE`.

### ما الجديد في 1.0.9

الـ loader يضيف الـ depots إلى الـ package من مجموعات `DepotKeys:` داخل الـ config. قبل هذا الإصدار كان التطبيق يكتب `Apps:` و`PackageIds:` فقط، فكان Steam يرى اللعبة مملوكة لكن **بدون أي depot** — لذلك ينتهي التنزيل فورًا بحجم 0 بايت ويقول Play إن الملف التنفيذي غير موجود.

- الآن يُكتب لكل لعبة قسم `DepotKeys:` بسطر لكل depot.
- زر **Import .lua** في تبويب Grant: اختر ملف `.lua` بنمط SteamTools، فيُثبَّت في `config/stplug-in` ويُنسخ منه appid والـ depots ومفاتيح الـ depot (64 حرف hex) إلى config الـ loader. وأي مفاتيح موجودة على القرص تُستخدم تلقائيًا عند كل grant/unlock.
- سطور الـ depot في السجل تحمل الآن خطأ DepotDownloader الحقيقي لكل depot بدل `install failed: None`.
- زر **Download files** يكتب config الـ loader أولًا ويتوقف برسالة واضحة إذا نزل 0 بايت.
- في الـ CLI: `import-lua FILE`.

---

@oll.fx and killywilly cooked something dangerous https://discord.gg/ojailbreaks
