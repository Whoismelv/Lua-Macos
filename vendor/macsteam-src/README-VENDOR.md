# macsteam — vendored source

Snapshot of `crghofficial/macsteam` `main` taken **2026-10-25**.
Upstream is alive at that date (last push 2026-10-23, release `v0.8.2`);
this copy exists so the shipped app can carry the loader instead of
downloading it, and so the capability survives an upstream takedown.

What is here:

| Path | What it is |
|---|---|
| `src/` | the dylib itself — loader, resolver (AOB + anchor), hooks, feats |
| `Makefile` | builds `out/macsteam.dylib` (arm64 + x86_64 lipo) |
| `vendor/cJSON.{c,h}` | the only bundled dependency |
| `signatures/` | upstream signature database, kept for reference |
| `scripts/`, `project.yml` | upstream tooling, unused by our build |
| `LICENSE` | AGPL-3.0 |

What is **not** here, on purpose:

- `macsteam-app/`, `Macsteam.xcodeproj/`, `launcher/` — the Swift config app and
  its installer UI. Our app does that job (`macsteam_inject()`); the Swift side
  is not needed to produce the dylib.
- `vendor/dobby/` — the Makefile clones `jmpews/Dobby` at the pinned revision
  `5dfc8546954ce3b3198132ab13fddb89ee92cdd7` and builds it with cmake. Pinning
  matters: newer Dobby changes the arm64 trampoline layout and every hook breaks.
- `build/`, `out/` — generated.

Build:

```bash
make -j"$(sysctl -n hw.ncpu)"          # -> out/macsteam.dylib
make MIN_VER=14.0 -j"$(sysctl -n hw.ncpu)"   # if the runner SDK is older than 15
```

Requires Xcode Command Line Tools (clang, cmake, lipo, codesign) on macOS.
The result is placed at `../macsteam/macsteam.dylib` by `build_licensetools.sh`
and ends up inside `LicenseTools.app/Contents/Resources/vendor/macsteam/`.

Signature database stays in `../macsteam/signatures/macos.arm64/` — those are
data, not code, and they are what goes stale when Valve ships a new client.
See `../macsteam/RESTORE.md` for how to author one.

License: AGPL-3.0. Source stays in this repository alongside the binary it
builds, and `LICENSE` is kept verbatim.
