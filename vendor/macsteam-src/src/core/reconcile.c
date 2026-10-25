// Late-injection recovery
#include "reconcile.h"
#include "../util/log.h"
#include "../util/file.h"

#include <stdint.h>

static int sx_reconcile_after_inject(void);
static int sx_reconcile_run_pending_if_ready(void);

static uintptr_t g_engine_ref_fn = 0;

typedef void (*fn_clearmap)(void *map_base);
static fn_clearmap g_clearmap_fn = NULL;

typedef int64_t (*fn_readdisk)(void *cache_base);
static fn_readdisk g_readdisk_fn = NULL;

typedef void (*fn_appmgr_markdirty)(void *app_mgr, uint32_t appid, char flag);
typedef void (*fn_appmgr_op)(void *app_mgr);
static fn_appmgr_markdirty g_markdirty_fn = NULL;
static fn_appmgr_op g_recompute_fn = NULL;
static fn_appmgr_op g_emit_fn      = NULL;

static const int *g_refresh_app_ids = NULL;
static int        g_refresh_app_count = 0;

// Main thread only (recompute/emit aren't thread safe, much like C itself).
static int g_refresh_armed = 0;

static volatile int g_login_observed = 0;

void sx_reconcile_set_login_observed(void) {
    if (!g_login_observed) {
        g_login_observed = 1;
        SX_LOG("[reconcile] login observed (BLoggedOn). Deferred reconcile + library refresh may now run");
    }
}

int sx_reconcile_login_observed(void) {
    return g_login_observed;
}

static int g_reconcile_pending = 0;

#define APPMGR_DIRTY_COUNT_OFF 9140
#define ENGINE_REF_ADRP_OFF   0x40
#define CPKGINFOCACHE_OFF     4400
#define CPKGINFOCACHE_MAP_OFF 8

static uintptr_t decode_adrp_add(uintptr_t site) {
    uint32_t adrp = *(uint32_t *)site;
    uint32_t add  = *(uint32_t *)(site + 4);

    if ((adrp & 0x9F000000u) != 0x90000000u)
        return 0;
    if ((add & 0xFFC00000u) != 0x91000000u)
        return 0;

    uint32_t immlo = (adrp >> 29) & 0x3;
    uint32_t immhi = (adrp >> 5)  & 0x7FFFF;
    int64_t  imm   = (int64_t)((immhi << 2) | immlo);
    if (imm & (1LL << 20))
        imm -= (1LL << 21);
    uintptr_t page = site & ~(uintptr_t)0xFFF;
    uintptr_t adrp_target = page + (uintptr_t)(imm << 12);

    uint32_t adrp_rd = adrp & 0x1F;
    uint32_t add_rn  = (add >> 5) & 0x1F;
    if (adrp_rd != add_rn)
        return 0;

    uint32_t imm12 = (add >> 10) & 0xFFF;
    uint32_t sh    = (add >> 22) & 0x1;
    if (sh) imm12 <<= 12;

    return adrp_target + imm12;
}

static void *resolve_csteamengine(void) {
    if (!g_engine_ref_fn) {
        SX_WARN("[reconcile] CSteamEngine anchor unresolved");
        return NULL;
    }
    uintptr_t global_addr = decode_adrp_add(g_engine_ref_fn + ENGINE_REF_ADRP_OFF);
    if (!global_addr) {
        SX_WARN("[reconcile] ADRP+ADD decode failed at anchor+0x%x",
                ENGINE_REF_ADRP_OFF);
        return NULL;
    }
    void *engine = *(void **)global_addr;
    SX_DBG("[reconcile] qword_197B468 @ %p -> CSteamEngine %p",
           (void *)global_addr, engine);
    return engine;
}


// Repair pkg-20200 in packageinfo.vdf so the re-read doesn't drop it.
static const uint8_t PKG_VDF_CORRUPT_MARKER[] = {
    0x20, 0x20, 0x01, 0x00, 0x02,
    'b', 'i', 'l', 'l', 'i', 'n', 'g', 't', 'y', 'p', 'e'
};
static const uint8_t PKG_VDF_CLEAN_ID[] = { 0xE8, 0x4E, 0x00, 0x00 };

static int sx_reconcile_repair_packageinfo_vdf(void) {
    const char *home = sx_resolve_home();
    if (!home) return 0;

    char path[1024];
    sx_steam_appcache_path(path, sizeof(path), home, "packageinfo.vdf");

    int rc = sx_file_patch_bytes(path, PKG_VDF_CORRUPT_MARKER,
                                 sizeof(PKG_VDF_CORRUPT_MARKER),
                                 PKG_VDF_CLEAN_ID, sizeof(PKG_VDF_CLEAN_ID));
    if (rc == 1)
        SX_LOG("[reconcile] packageinfo.vdf repaired pkg 20200 (73760 -> 20200)");
    else if (rc < 0)
        SX_WARN("[reconcile] packageinfo.vdf repair failed");
    return rc == 1;
}


void sx_reconcile_set_addrs(uintptr_t engine_ref_fn,
                            uintptr_t clearmap_fn,
                            uintptr_t readdisk_fn) {
    g_engine_ref_fn = engine_ref_fn;
    g_clearmap_fn   = (fn_clearmap)clearmap_fn;
    g_readdisk_fn   = (fn_readdisk)readdisk_fn;

    if (!engine_ref_fn || !clearmap_fn || !readdisk_fn) {
        SX_WARN("[reconcile] disabled, unresolved addrs "
                "(engine_ref=%p clearmap=%p readdisk=%p)",
                (void *)engine_ref_fn, (void *)clearmap_fn, (void *)readdisk_fn);
    } else {
        SX_DBG("[reconcile] addrs set: engine_ref=%p clearmap=%p readdisk=%p",
               (void *)engine_ref_fn, (void *)clearmap_fn, (void *)readdisk_fn);
    }
}

void sx_reconcile_set_library_refresh_fns(uintptr_t markdirty_fn,
                                  uintptr_t recompute_fn,
                                  uintptr_t emit_fn) {
    g_markdirty_fn = (fn_appmgr_markdirty)markdirty_fn;
    g_recompute_fn = (fn_appmgr_op)recompute_fn;
    g_emit_fn      = (fn_appmgr_op)emit_fn;
    if (!markdirty_fn || !recompute_fn || !emit_fn) {
        SX_WARN("[reconcile] auto-refresh disabled, native trio unresolved "
                "(markdirty=%p recompute=%p emit=%p). Reconcile still runs but "
                "UI needs a manual online/offline toggle",
                (void *)markdirty_fn, (void *)recompute_fn, (void *)emit_fn);
    } else {
        SX_DBG("[reconcile] library refresh trio set: MarkAppDirty=%p "
               "RecomputeSubscribedApps=%p EmitAppLicensesChanged=%p",
               (void *)markdirty_fn, (void *)recompute_fn, (void *)emit_fn);
    }
}

void sx_reconcile_set_library_refresh_apps(const int *app_ids, int app_count) {
    g_refresh_app_ids   = app_ids;
    g_refresh_app_count = (app_ids && app_count > 0) ? app_count : 0;
    SX_DBG("[reconcile] library refresh appid list set: %d app(s)", g_refresh_app_count);
}

void sx_reconcile_arm_library_refresh(void) {
    if (!g_markdirty_fn || !g_recompute_fn || !g_emit_fn) {
        SX_WARN("[reconcile] cannot arm library refresh, native trio unresolved");
        return;
    }
    g_refresh_armed = 1;
    SX_LOG("[reconcile] library refresh armed. Native sub_B19024(x%d)+sub_B1986C+"
           "sub_B19100 will fire once from the next main-thread "
           "CheckAppOwnership/GetSubscribedApps", g_refresh_app_count);
}

int sx_reconcile_fire_library_refresh(void *app_mgr) {
    if (!g_refresh_armed)
        return 0;
    if (!g_login_observed)
        return 0;
    sx_reconcile_run_pending_if_ready();
    g_refresh_armed = 0;

    if (!app_mgr) {
        SX_WARN("[reconcile] library refresh dropped, no live CUserAppManager captured");
        return 0;
    }

    SX_LOG("[reconcile] firing native library refresh on CUserAppManager=%p (x%d apps)",
           app_mgr, g_refresh_app_count);

    for (int i = 0; i < g_refresh_app_count; i++) {
        uint32_t appid = (uint32_t)g_refresh_app_ids[i];
        g_markdirty_fn(app_mgr, appid, 0);
    }
    SX_LOG("[reconcile] marked %d configured appid(s) dirty (mgr+9140=%d)",
           g_refresh_app_count, *(volatile int *)((uint8_t *)app_mgr + APPMGR_DIRTY_COUNT_OFF));

    g_recompute_fn(app_mgr);
    g_emit_fn(app_mgr);
    return 1;
}

void sx_reconcile_arm_after_inject(void) {
    if (!g_engine_ref_fn || !g_clearmap_fn || !g_readdisk_fn) {
        SX_WARN("[reconcile] cannot arm reconcile, addresses unavailable");
        return;
    }
    g_reconcile_pending = 1;
    SX_LOG("[reconcile] reconcile armed (ClearMap+ReadFromDisk), runs from first post-login hook");
}

static int sx_reconcile_run_pending_if_ready(void) {
    if (!g_reconcile_pending)
        return 0;
    if (!g_login_observed)
        return 0;
    g_reconcile_pending = 0;
    SX_LOG("[reconcile] login complete. Running deferred package-cache "
           "reconcile now");
    return sx_reconcile_after_inject();
}

static int sx_reconcile_after_inject(void) {
    sx_reconcile_repair_packageinfo_vdf();

    void *engine = resolve_csteamengine();
    if (!engine) {
        SX_WARN("[reconcile] CSteamEngine unresolved, aborting reconcile");
        return -1;
    }

    void *cache    = (void *)((uint8_t *)engine + CPKGINFOCACHE_OFF);
    void *map_base = (void *)((uint8_t *)cache  + CPKGINFOCACHE_MAP_OFF);

    SX_LOG("[reconcile] engine=%p cache=%p map=%p, clearing map + re-reading disk",
           engine, cache, map_base);

    g_clearmap_fn(map_base);

    int64_t npkgs = g_readdisk_fn(cache);

    SX_LOG("[reconcile] ReadFromDisk re-read %lld package(s). PkgParse should have re-fired",
           (long long)npkgs);

    if (!g_refresh_armed) {
        SX_DBG("[reconcile] library refresh not armed, caller did not request the "
               "native AppLicensesChanged_t emit (UI may need manual toggle)");
    }

    return (int)npkgs;
}
