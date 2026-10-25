// Package record injection
#include "package.h"
#include "../config/config.h"
#include "../util/log.h"

typedef int (*fn_UtlVecGrow)(void *vec, int grow_by);
static fn_UtlVecGrow g_utlvec_grow = NULL;

#define PKGPARSE_GROW_BL_OFF    0x258

static fn_UtlVecGrow decode_bl(uintptr_t bl_site) {
    uint32_t w = *(uint32_t *)bl_site;
    if ((w & 0xFC000000u) != 0x94000000u)
        return NULL;
    int32_t imm26 = (int32_t)(w & 0x03FFFFFFu);
    if (imm26 & 0x02000000)
        imm26 |= 0xFC000000;
    return (fn_UtlVecGrow)(bl_site + ((intptr_t)imm26 << 2));
}

void sx_pkg_set_helpers(uintptr_t pkg_parse) {
    if (!pkg_parse) {
        SX_WARN("[pkg] PkgParse address unresolved, package injection disabled");
        return;
    }
    g_utlvec_grow = decode_bl(pkg_parse + PKGPARSE_GROW_BL_OFF);
    if (g_utlvec_grow) {
        SX_DBG("[pkg] PkgParse: derived UtlVecGrow(sub_F029C) @ %p",
               (void *)g_utlvec_grow);
    } else {
        SX_WARN("[pkg] PkgParse: BL decode failed @ +0x%x, package injection "
                "disabled", PKGPARSE_GROW_BL_OFF);
    }
}

static int utlvec_append_unique_int(CUtlVecInt_t *vec, int32_t val) {
    for (int32_t i = 0; i < vec->count; i++) {
        if (vec->base && vec->base[i] == val)
            return 0;
    }

    if (vec->count >= vec->cap) {
        if (!g_utlvec_grow) return 0;
        g_utlvec_grow(vec, (vec->count + 1) - vec->cap);
        if (vec->count >= vec->cap)
            return 0;
    }
    if (!vec->base)
        return 0;
    vec->base[vec->count] = val;
    vec->count++;
    return 1;
}

void sx_pkg_inject(CPackageInfo_t *pkg) {
    sx_config_t *g_cfg = sx_config_current;
    if (!g_cfg) return;

    uint32_t pkgid = pkg->packageId;
    if (!sx_config_has_package(g_cfg, (int)pkgid))
        return;

    int apps_added = 0, depots_added = 0;

    for (int i = 0; i < g_cfg->app_count; i++) {
        uint32_t appid = (uint32_t)g_cfg->app_ids[i];
        if (appid == 0) continue;

        apps_added += utlvec_append_unique_int(&pkg->apps, (int32_t)appid);

        int depots[SX_CONFIG_MAX_DEPOTS];
        int nd = sx_config_app_depots(g_cfg, (int)appid, depots,
                                      SX_CONFIG_MAX_DEPOTS);
        for (int d = 0; d < nd; d++)
            depots_added += utlvec_append_unique_int(&pkg->depots, depots[d]);
    }

    if (apps_added || depots_added) {
        SX_LOG("PkgParse: package %u INJECTED %d app(s) + %d depot(s) "
               "(record now has %d apps, %d depots)",
               pkgid, apps_added, depots_added, pkg->apps.count, pkg->depots.count);
    }
}
