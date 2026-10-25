// Package injection hook
#include "hooks.h"
#include "../feats/package.h"
#include "../steam_types.h"
#include <stdint.h>

void sx_hooks_package_set_helpers(uintptr_t pkg_parse) {
    sx_pkg_set_helpers(pkg_parse);
}

static void *orig_PkgParse = NULL;
typedef int64_t (*fn_PkgParse)(void *pkgInfo, void *sha, int cln, void *buf);

static int64_t hook_PkgParse(void *pkgInfo, void *sha, int cln, void *buf) {
    fn_PkgParse orig = (fn_PkgParse)orig_PkgParse;
    int64_t ret = orig(pkgInfo, sha, cln, buf);

    if (ret != 1 || !pkgInfo)
        return ret;

    sx_pkg_inject((CPackageInfo_t *)pkgInfo);
    return ret;
}

static sx_hook_def_t g_hooks[] = {
    {
        .name     = "PkgParse",
        .sig_name = "CPackageInfo::BParseFromBuffer",
        .hook_fn  = (void *)hook_PkgParse,
        .orig_fn  = &orig_PkgParse,
        .optional = 0,
    },
};

int sx_hooks_package_count(void) {
    return (int)(sizeof(g_hooks) / sizeof(g_hooks[0]));
}

sx_hook_def_t *sx_hooks_package_defs(void) {
    return g_hooks;
}
