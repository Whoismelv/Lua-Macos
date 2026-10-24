# LicenseTools for macOS

أدوات ترخيص ستيم لنظام macOS — توليد ملفات الـ lua، تثبيتها في `config/stplug-in`، تثبيت المانيفست، ونزول المحتوى مباشرة من شبكة Steam (CDN) بدون حساب وبدون رمز إيميل.

A Steam license kit for macOS: probe public appinfo, generate `stplug-in` lua (`addappid` / `setManifestid`), pin manifests, fetch depot content.

---

## بالعربي

### الشغلة بتشتغل هيك — زر واحد، كل شيء جوّه البرنامج

1. افتح **LicenseTools** واكتب اسم اللعبة أو الـ appid.
2. اضغط **Fetch → Library**.
3. البرنامج بيعمل كل شي لحاله: بيفحص الـ loader، وإذا مش منصّب بينصّبه من الـ payload المخزّن جوّاه (بدون تحميل ولا برنامج خارجي)، بيجيب الديبوتات، بيكتب الـ lua + إعدادات macSteam، وبيعيد تشغيل ستيم.
4. اللعبة بتظهر بالمكتبة. خلص.

> بدك ملفات اللعبة نفسها؟ اضغط **Download files** — بينزل الـ depots مباشرة من شبكة Steam جوّه
> `~/Library/Application Support/Steam/steamapps/common/<installdir>`، كل ملف متحقَّق منه بالـ hash، وبيعدّل
> صلاحية التنفيذ على ملفات `Contents/MacOS`.
> زر **Update game** بيعمل نفس الشي بس بيتخطى الملفات اللي حجمها و hash تبعها صح — ما بتمسح ولا بتحمّل من جديد.
> بعد التنزيل البرنامج بيستبدل مكتبة `libsteam_api.dylib` جوّه اللعبة بنسخة مولَّدة من الرموز اللي اللعبة نفسها بتستوردها،
> فما بيسألك فحص ملكية. زر **Restore DRM files** بيرجّع الأصلية، وزر **Skip Steam DRM** بيعيد العملية يدوياً.
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
| `fetch APPID` | نزول المحتوى عبر DepotDownloader (`--user` / `--pass`) — للمستخدمين المتقدّمين فقط |
| `library` / `list` / `restart` / `ensure` / `settings` / `gui` | الباقي مثل ما هو |

الوقت اللي بتحفظ فيه ملفات الـ lua القديمة: `~/.licensetools/vault/`.

---

## English

### Flow — one button, everything inside the app

1. Open **LicenseTools** and type the game name or appid.
2. Press **Fetch → Library**.
3. The app does the rest by itself: checks the loader, injects it from the payload bundled inside the app (no downloads, no external program), probes depots, writes the lua + macSteam config, and restarts Steam.
4. The game shows up in your library. Done.

Want the actual game files too? Press **Download files** — the app pulls the depots straight from the Steam CDN into
`~/Library/Application Support/Steam/steamapps/common/<installdir>`, verifies every file against the manifest SHA-1,
and restarts Steam so the Play button launches real content. No account, no password, no Steam Guard code.
**Update game** re-checks the installed files and only re-pulls what actually changed.
Right after a download the app replaces the game's `libsteam_api.dylib` with one generated from the exact symbols the
game imports, so the ownership check answers yes. **Restore DRM files** puts the original back;
**Skip Steam DRM** runs the same step on its own.
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

Bundled: DepotDownloader (vendored at build time) and `pycryptodome` for the AES fast path. steamcmd and the loader are fetched on first use into `~/.licensetools/tools/`.

### What changed in 1.5.1

- **Fixed a hard crash on launch and on `--selftest`.** The CommonCrypto binding declared twelve argument types for an eleven-parameter function, so on Apple silicon the key pointer landed in the wrong register and the process died with `SIGSEGV` (exit 139) before printing anything. The declaration now matches `CCCrypt` exactly, every call is bounds-checked, and the first result is verified against the pure-Python AES before the native path is trusted — a binding that answers wrong is dropped for the rest of the session.
- **`libzstd` is bound properly too** (`c_size_t` return plus `ZSTD_isError`), so a `VSZa` chunk can no longer return a truncated length.
- **`--selftest` now exits non-zero when a check fails.** It used to print `ok: false` and still return success, which is why a red run looked green in the build log.

### ما الجديد في 1.5.1

- **انهيار كامل عند التشغيل وعند `--selftest` انتصلح.** ربط CommonCrypto كان يعرّف اثني عشر نوع وسيط لدالة ذات أحد عشر، فعلى معالجات Apple كان مؤشر المفتاح يوصل بمكان غلط والعملية تموت بـ `SIGSEGV` (exit 139) قبل ما تطبع أي شي. التعريف صار مطابق لـ `CCCrypt` بالضبط، وكل نداء متحقَّق من حدوده، وأول نتيجة بتتقارن مع الـ AES المكتوب ببايثون قبل ما يُوثق بالمسار الأصلي — وربط بيرجّع نتيجة غلط بينشال لبقية الجلسة.
- **`libzstd` صار مربوط صح كمان** (`c_size_t` للنتيجة مع `ZSTD_isError`)، فحزمة `VSZa` ما عادت تقدر ترجّع طول مقطوع.
- **`--selftest` صار يخرج بكود فشل لما يطلع في check فاشل.** قبل كان يطبع `ok: false` ويرجّع نجاح، ولهذا كان الـ run الأحمر يبان أخضر بسجل البناء.

### What changed in 1.5.0

1.5.0 is the release where Play actually launches, and where Steam stops fighting the app over the library.

- **The game's ownership check is replaced, not argued with.** Some mac builds load `libsteam_api.dylib` and ask it whether you own the app. `Skip Steam DRM` (Maintenance) scans the game binary with `nm -u`, collects the exact `Steam*` symbols it imports, generates a matching C source, compiles it with `clang` as a `arm64 + x86_64` fat dylib, ad-hoc codesigns it, and drops it over the original. The original is kept as `libsteam_api.dylib.orig`, and `steam_appid.txt` is written next to the executable and at the install root so `SteamAPI_Init` knows which app it is. Done automatically after **Download files**.
- **Symbols are read, never guessed.** A hand-written stand-in misses one export and the game dies with `symbol not found`. The generated one exports exactly what the binary imports, plus every interface accessor, so `SteamAPI_SteamUser_v023()` returns a live object pointer instead of `NULL`.
- **`Restore DRM files`** puts the untouched original back, deletes `steam_appid.txt` and the marker, and re-signs the bundle.
- **Steam quit/launch was rewritten.** The old quit sent one Apple event and gave up after a few seconds, so Steam was very often still alive when the app wrote the library record — which is why the record vanished on the next launch. Now: Apple event → `SIGTERM` on the real pids → `SIGKILL` → orphan `steamwebhelper` cleanup, each with its own wait, and the install **refuses to write** when Steam will not close instead of losing the record silently.
- **Launch tries three routes** (`open -b com.valvesoftware.steam`, `open -a Steam`, then the bundle path) and reports which one worked. `Restart Steam` now reports the real outcome in the log instead of claiming success.
- **`steamapps/depotcache` path fixed.** Manifests dropped by the app were written one directory too high, so Steam never saw them while the paths report showed a correct count.
- **Manifest symlinks are validated.** A mirror serving `{"name": "...", "link": "/Users/you/Library"}` can no longer redirect the following file writes outside the install folder: absolute links and `..` are refused, and every write re-checks containment after resolution.
- **`installdir` is validated** before joining, so a hostile appinfo cannot turn Download or Delete into writes or deletes outside `steamapps/common`.
- **Multi-library aware.** The appmanifest lookup, install target, repair and uninstall now find the library that actually owns the app instead of always assuming the first one.
- **The queued library record is cleaned up** on every early return (missing key, missing manifest, Steam still open, failed download), so a phantom 0-byte entry no longer sits in the library forever.
- **Smaller fixes:** `state` parsing can't crash the whole library list any more, `pkill -f` is gone in favour of exact pids, `zstandard` is bundled for `VSZa` chunks, the steamcmd tar fallback refuses links and absolute paths, the lua importer only reads what the file dialog returns, and probe rows are escaped before they hit the webview table.

### ما الجديد في 1.5.0

الإصدار 1.5.0 هو اللي بيخلّي زر Play يفتح اللعبة فعلاً، وبيوقف ستيم عن مسح سجل المكتبة.

- **فحص الملكية جوّه اللعبة صار مُستبدَل، مش مُتفاوَض عليه.** بعض إصدارات الماك بتحمّل `libsteam_api.dylib` وتسألها إذا اللعبة مملوكة. زر **Skip Steam DRM** (تبويب Maintenance) بيفحص ملف اللعبة بـ `nm -u`، بيجمع رموز `Steam*` اللي بتستوردها بالظبط، بيولّد ملف C مطابق، بيبنيه بـ `clang` كـ dylib fat `arm64 + x86_64`، بيوقّعه ad-hoc، وبيحطّه مكان الأصلي. الأصلي محفوظ باسم `libsteam_api.dylib.orig`، و`steam_appid.txt` بينكتب جانب الملف التنفيذي وفي جذر التثبيت عشان `SteamAPI_Init` يعرف أي تطبيق. بينعمل تلقائياً بعد **Download files**.
- **الرموز بتتقرأ، ما بتتخمّن.** stand-in مكتوب بالإيد بينسى رمز واحد فتقع اللعبة بـ `symbol not found`. المولَّد بيصدّر اللي اللعبة بتستورده بالظبط، زائد كل accessors الواجهات، فـ `SteamAPI_SteamUser_v023()` بترجّع مؤشر حقيقي بدل `NULL`.
- **`Restore DRM files`** بيرجّع الأصلي، بيمسح `steam_appid.txt` والـ marker، وبيعيد توقيع الحزمة.
- **إغلاق وفتح ستيم انكتبوا من جديد.** الإغلاق القديم كان يبعت Apple event واحد ويستسلم بعد ثوانٍ، فستيم كان يظل شغّال وقت كتابة سجل المكتبة — ولهذا السجل كان يختفي بعد إعادة التشغيل. هلق: Apple event → `SIGTERM` على الـ pids الحقيقية → `SIGKILL` → تنظيف `steamwebhelper` اليتيم، كل خطوة بانتظارها، والتثبيت **بيرفض يكتب** إذا ستيم ما انغلق بدل ما يضيّع السجل بصمت.
- **الفتح بيجرّب ثلاث طرق** (`open -b com.valvesoftware.steam`، `open -a Steam`، بعدها مسار الحزمة) وبيقول أي وحدة نجحت. و**Restart Steam** صار يبلّغ بالنتيجة الحقيقية بالسجل بدل ما يدّعي النجاح.
- **مسار `steamapps/depotcache` انتصلح.** المانيفست اللي كان البرنامج يحطّه كان بمجلد أعلى بمستوى، فستيم ما كان يشوفه بينما تقرير المسارات يعرض عدد صحيح.
- **الـ symlinks بالمانيفست صارت متحقَّقة.** مرآة تخدم `{"name": "...", "link": "/Users/you/Library"}` ما عاد تقدر تحوّل كتابة الملفات برّا مجلد التثبيت: الروابط المطلقة و`..` مرفوضة، وكل كتابة بتعيد التحقق من الاحتواء بعد الـ resolution.
- **`installdir` صار متحقَّق** قبل الدمج، فـ appinfo خبيث ما بيقدر يحوّل التنزيل أو الحذف لكتابة أو مسح برّا `steamapps/common`.
- **دعم أكثر من مكتبة.** البحث عن الـ appmanifest، ومكان التثبيت، والإصلاح، والحذف صاروا يلقون المكتبة اللي فعلاً بتملك اللعبة بدل ما يفترضوا الأولى دايماً.
- **سجل المكتبة المؤقّت بينمسح** بكل خروج مبكر (مفتاح ناقص، مانيفست ناقص، ستيم ما انغلق، تنزيل فاشل)، فما بيظل سجل وهمي بحجم 0 بالمكتبة للأبد.
- **إصلاحات أصغر:** قراءة `state` ما عادت تقدر تفشّل قائمة المكتبة كلها، `pkill -f` انشال لصالح pids دقيقة، `zstandard` صار محمول لحزم `VSZa`، فكّ tar لستيم-سي إم دي بيرفض الروابط والمسارات المطلقة، مستورد الـ lua بيقرأ بس اللي رجعه ملف الحوار، وصفوف الـ probe صارت مهرَّبة قبل ما تدخل جدول الويب.

### What changed in 1.4.0

Both old download paths were dead ends, so 1.4.0 stops using either of them.

- **Steam-side download was unfixable.** The loader hooks `CAppInfo::GetManifestRequestCode` and asks `gmrc.wudrm.com` for the code; that host answers `502`, the hook falls through, Valve's CM returns `0` for an app you do not own, and the CDN manifest request comes back `401` — which Steam shows as `NO INTERNET CONNECTION`. Every mirror of that service is dead too.
- **DepotDownloader needed a login, and the login needed an email code.** A Steam Guard code cannot be typed into a background downloader, so that path always ended at `InitializeSteam failed`. Both login fields are gone from the window.
- **The app now pulls the depots itself.** It reads the staged manifest, walks the chunk table, fetches each chunk from the Steam CDN (`/depot/<id>/chunk/<sha1>`, no account, no request code), decrypts it with the depot key and decompresses it. Depots are AES-256-CBC: the first 16 bytes decrypt with AES-ECB into the CBC IV, the rest is CBC plus PKCS7. Payloads are `VZa` (raw LZMA1 with a 5-byte property block and a CRC/size footer), `VSZa` (zstd) or a plain zip — all three are handled.
- **Every file is hash-verified** against the SHA-1 in the manifest while it is written. A mismatch is reported per file instead of being written silently. Directory and symlink entries are created, manifest `\` separators become `/`, and `Contents/MacOS/*` gets `0755` so the binary actually launches.
- **Chunks are cached** at `~/.licensetools/chunks/<depot>/<sha1>`, so an interrupted download resumes instead of starting over.
- **Update game** (Maintenance) is the new button: it re-reads the manifest and skips any file whose size and SHA-1 already match, so a patch pulls only the changed bytes instead of a fresh 700 MB.
- **AES backends, in order:** CommonCrypto through `ctypes` (native on macOS, no dependency), then `pycryptodome` if bundled, then a pure-Python AES-256 fallback that is verified against the FIPS-197 test vector. `--selftest` now proves the AES block, the VZip frame decoder, the path flattener and the traversal refusal.

### ما الجديد في 1.4.0

طريقتي التنزيل القديمتين كانتا مسدودتين، فالإصدار 1.4.0 ما عاد يستخدم ولا وحدة منهن.

- **التنزيل من جهة Steam غير قابل للإصلاح.** الـ loader يعترض `CAppInfo::GetManifestRequestCode` ويسأل `gmrc.wudrm.com` عن الكود؛ المضيف يرد `502`، الاعتراض يمرّ بدون نتيجة، خادم Valve يرد `0` لأن اللعبة غير مملوكة، وطلب الـ manifest من الـ CDN يرجع `401` — وSteam يترجمها `NO INTERNET CONNECTION`. كل بدائل هذه الخدمة ميتة كذلك.
- **DepotDownloader بدّه تسجيل دخول، والتسجيل بدّه رمز إيميل.** رمز Steam Guard ما بينكتب داخل برنامج بيشتغل بالخلفية، فالمسار دايماً بينتهي بـ `InitializeSteam failed`. خانات الاسم وكلمة السر انشالت من الواجهة.
- **البرنامج صار بينزّل الـ depots بنفسه.** بيقرأ المانيفست المثبَّت، بيمشي على جدول الـ chunks، بيجيب كل chunk من شبكة Steam (`/depot/<id>/chunk/<sha1>` بدون حساب وبدون request code)، بيفك تشفيرها بمفتاح الـ depot وبيفك ضغطها. التشفير AES-256-CBC: أول 16 بايت تنفك بـ AES-ECB لتصير الـ IV، والباقي CBC مع PKCS7. الحِزم ثلاثة أنواع: `VZa` (LZMA1 خام مع بلوك خصائص 5 بايت وfooter فيه CRC والحجم)، `VSZa` (zstd)، أو zip عادي — والثلاثة مدعومين.
- **كل ملف متحقَّق منه بالـ hash** مقابل SHA-1 الموجود بالمانيفست وقت الكتابة. أي اختلاف بيطلع بالـ log كملف مرفوض بدل ما ينكتب بصمت. المجلدات والـ symlinks بتنعمل، وفواصل `\` بالمانيفست بتنقلب `/`، وملفات `Contents/MacOS/*` بتاخد صلاحية `0755` عشان الملف التنفيذي يشتغل فعلاً.
- **الـ chunks مخزّنة** بـ `~/.licensetools/chunks/<depot>/<sha1>`، فإذا انقطع التنزيل بيكمّل من وين وقف بدل ما يبدأ من الصفر.
- **Update game** (تبويب Maintenance) هو الزر الجديد: بيعيد قراءة المانيفست وبيتخطى أي ملف حجمه و SHA-1 تبعه مطابقين، فتحديث اللعبة بينزل الفروقات بس بدل 700 MB من جديد.
- **محرّكات فك التشفير بالترتيب:** CommonCrypto عبر `ctypes` (أصلي على macOS وبدون أي مكتبة)، بعدها `pycryptodome` إذا كان محمّل، بعدها AES-256 بلغة بايثون صافية متحقَّق منه مقابل متجه FIPS-197. و`--selftest` صار يفحص بلوك AES وفكّ حزمة VZip وتسطيح المسارات ورفض `..`.



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
