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

### What changed in 1.3.0

`NO INTERNET CONNECTION` in the Steam downloads page is not your network. The loader hooks `CAppInfo::GetManifestRequestCode` and fetches the code from `http://gmrc.wudrm.com/manifest/<manifest_id>`; that host answers `502`, so the hook falls through, Valve's CM returns no code for an app you don't own, the CDN manifest request comes back `401`, and Steam reports it as no connection. Every mirror of that service is dead, so the code path is unfixable from the outside. 1.3.0 removes the need for it:

- **Real depot manifests are staged into `steamapps/depotcache/<depot>_<gid>.manifest`** before Steam starts. Steam reads the local file and never asks the CM for a manifest request code, so the dead endpoint stops mattering.
- **`appmanifest_<appid>.acf` is written with `StateFlags 2`** (update required) plus `InstalledDepots` carrying the manifest id and size per depot — Steam sees a queued install and pulls the chunks itself.
- **Manifest sources**: a branch-per-appid mirror (`Fairyvmos/bruh-hub`, `Auiowu/ManifestAutoUpdate`, `tymolu233/ManifestAutoUpdate`) is tried in order; if the manifest id from appinfo is stale, the branch listing is read and the current `<depot>_*.manifest` is used instead, and the acf is rewritten with the corrected id.
- **Every staged manifest is parsed** before it counts: file count and total size are read out of the protobuf and compared against appinfo, so a wrong-branch or truncated file is reported instead of silently queued.
- **Download files** now stages manifests and writes the acf first, then restarts Steam and opens `steam://install/<appid>`.
- **Repair** re-stages the manifests, so a library that lost its depotcache is fixed without a Terminal.
- **Show Steam paths** prints `depotcache` and how many manifests are in it.
- CLI: `manifests APPID [--os mac|win|linux]`.

### ما الجديد في 1.3.0

رسالة `NO INTERNET CONNECTION` في صفحة التنزيلات ما هي مشكلة شبكة عندك. الـ loader يعترض `CAppInfo::GetManifestRequestCode` ويجيب الكود من `http://gmrc.wudrm.com/manifest/<manifest_id>`، وهذا المضيف يرد `502`، فيمرّ الاعتراض بدون نتيجة، وخادم Valve يرد بصفر لأن اللعبة غير مملوكة، فيرجع طلب الـ manifest من الـ CDN بـ `401`، وSteam يترجمها إلى «لا يوجد اتصال». كل بدائل هذه الخدمة ميتة، فالمسار نفسه غير قابل للإصلاح من الخارج. الإصدار 1.3.0 يلغي الحاجة إليه:

- **تنزيل ملفات manifest الحقيقية إلى `steamapps/depotcache/<depot>_<gid>.manifest`** قبل تشغيل Steam. Steam يقرأ الملف المحلي ولا يطلب manifest request code من الـ CM أصلًا، فالمضيف الميت ما يعود يفرق.
- **كتابة `appmanifest_<appid>.acf` بحالة `StateFlags 2`** (تحديث مطلوب) مع `InstalledDepots` يحمل رقم الـ manifest والحجم لكل depot — فيرى Steam تنزيلًا في الانتظار ويجلب الـ chunks بنفسه.
- **مصادر الـ manifest**: مرايا بنظام فرع-لكل-appid (`Fairyvmos/bruh-hub`، `Auiowu/ManifestAutoUpdate`، `tymolu233/ManifestAutoUpdate`) تُجرَّب بالترتيب؛ وإذا كان رقم الـ manifest في appinfo قديمًا يُقرأ فهرس الفرع ويُستخدم `<depot>_*.manifest` الحالي، ويُعاد كتابة الـ acf بالرقم المصحّح.
- **كل manifest يُفحص** قبل احتسابه: عدد الملفات والحجم الكلي يُقرآن من الـ protobuf ويُقارنان بـ appinfo، فملف فرع خاطئ أو ناقص يظهر كخطأ بدل ما يُدرج بصمت.
- **Download files** صار ينزّل الـ manifests ويكتب الـ acf أولًا، ثم يعيد تشغيل Steam ويفتح `steam://install/<appid>`.
- **Repair** يعيد تنزيل الـ manifests، فمكتبة فقدت الـ depotcache تُصلَح بدون Terminal.
- **Show Steam paths** يطبع `depotcache` وعدد الـ manifests داخله.
- في الـ CLI: `manifests APPID [--os mac|win|linux]`.

### What changed in 1.2.0

1.1.0 shipped the key table but a Mac with a strict TLS chain or a filtered network could end up with an empty table — the log said `0 key(s)` and Steam kept showing `CONTENT STILL ENCRYPTED`. 1.2.0 makes the table impossible to miss:

- Six download routes tried in order: GitHub raw, jsDelivr, Fastly jsDelivr, githack, ghproxy, GitHub contents API. First one that returns a valid table wins and is cached.
- TLS fallback chain: bundled `certifi` store, then the system store, then an unverified context — public read-only data, so a missing root certificate on the machine can't zero out the table.
- The key table is now **bundled inside the dmg** at build time and copied to `~/.licensetools/depotkeys.json` on first run, so keys exist even with no network at all.
- `gzip` and `identity` encodings both handled, so a proxy that mangles compression can't break the fetch.
- The window title bar shows the running version (`LICENSETOOLS v1.2.0`) — no more guessing which dmg is open.
- Key lookup also scans `~/Downloads`, `~/Library/Application Support/macsteam` and the tool's own vault for `.lua` files, not just `config/stplug-in`.
- **Show Steam paths** now prints the key table path, size, age, key count and the last fetch error, so a failed download is visible without a Terminal.
- CLI: `keydb [--refresh]`.

### ما الجديد في 1.2.0

الإصدار 1.1.0 نزّل جدول المفاتيح، لكن جهازًا بشهادة TLS ناقصة أو شبكة مفلترة قد ينتهي بجدول فارغ — السجل يقول `0 key(s)` وSteam يظل يعرض `CONTENT STILL ENCRYPTED`. هذا الإصدار يجعل الجدول مضمونًا:

- ستة مسارات تنزيل بالترتيب: GitHub raw، jsDelivr، Fastly jsDelivr، githack، ghproxy، GitHub contents API. أول جدول صالح يفوز ويُخزَّن.
- سلسلة بدائل TLS: مخزن `certifi` المضمّن، ثم مخزن النظام، ثم سياق بلا تحقق — بيانات عامة للقراءة فقط، فشهادة جذر ناقصة على الجهاز ما تعطّل الجدول.
- جدول المفاتيح صار **مضمّنًا داخل الـ dmg** وقت البناء ويُنسخ إلى `~/.licensetools/depotkeys.json` أول تشغيل، فالمفاتيح موجودة حتى بدون إنترنت.
- معالجة `gzip` و`identity` معًا، فأي بروكسي يعبث بالضغط ما يكسر الجلب.
- شريط العنوان يعرض الإصدار (`LICENSETOOLS v1.2.0`) — ما عاد فيه تخمين أي dmg مفتوح.
- البحث عن المفاتيح يمسح أيضًا `~/Downloads` و`~/Library/Application Support/macsteam` ومخزن التطبيق، لا `config/stplug-in` فقط.
- **Show Steam paths** يطبع مسار جدول المفاتيح وحجمه وعمره وعدد المفاتيح وآخر خطأ، فيظهر فشل التنزيل بدون Terminal.
- في الـ CLI: `keydb [--refresh]`.

### What changed in 1.1.0

`CONTENT STILL ENCRYPTED` in the Steam downloads page means the depots were injected but Steam got a zero decryption key. 1.1.0 fixes that:

- The app now resolves **real depot decryption keys** from a public key table (ManifestHub, ~288k depots), cached at `~/.licensetools/depotkeys.json` and refreshed every 7 days. First run pulls it in the background.
- Every grant / unlock / download writes those keys into the `DepotKeys:` group next to the depots, so Steam decrypts instead of stalling.
- **Refresh depot keys** (Maintenance tab) forces a table update.
- **Download files** now hands the install to Steam when no Steam login is typed in — an anonymous DepotDownloader cannot fetch a paid depot, which is what produced `InitializeSteam failed`. Type a login that owns the game and it uses DepotDownloader instead.
- Failed depot lines in the log now show the last four lines of DepotDownloader output, not just the final one.
- CLI: `keys APPID` prints the resolved key per depot.

### ما الجديد في 1.1.0

رسالة `CONTENT STILL ENCRYPTED` في صفحة التنزيلات معناها أن الـ depots انحقنت لكن Steam استلم مفتاح فك تشفير أصفار. هذا الإصدار يحلّها:

- التطبيق يجلب **مفاتيح فك تشفير الـ depot الحقيقية** من جدول مفاتيح عام (ManifestHub، حوالي 288 ألف depot)، ويخزّنها في `~/.licensetools/depotkeys.json` ويحدّثها كل 7 أيام. أول تشغيل ينزّلها في الخلفية.
- كل grant / unlock / download يكتب هذه المفاتيح داخل قسم `DepotKeys:` بجانب الـ depots، فيفك Steam التشفير بدل ما يتوقف.
- زر **Refresh depot keys** في تبويب Maintenance يحدّث الجدول فورًا.
- زر **Download files** صار يسلّم التنزيل إلى Steam إذا ما كتبت حساب Steam — لأن DepotDownloader المجهول ما يقدر يجيب depot مدفوع، وهذا سبب `InitializeSteam failed`. اكتب حسابًا يملك اللعبة فيستخدم DepotDownloader.
- سطور الـ depot الفاشلة في السجل تعرض آخر أربعة أسطر من مخرجات DepotDownloader بدل سطر واحد.
- في الـ CLI: `keys APPID` يطبع المفتاح لكل depot.

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
