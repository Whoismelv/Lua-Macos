// Depot decryption key hook
#include "hooks.h"
#include "../feats/depot.h"
#include <stdint.h>

static void *orig_GetDepotDecryptionKey = NULL;

typedef int (*fn_GetDepotDecryptionKey)(void *self, uint32_t depot_id,
                                        void *outBuf);

void sx_hooks_depot_set_helpers(uintptr_t ensure_capacity, uintptr_t seek_put) {
    sx_depot_set_helpers(ensure_capacity, seek_put);
}

static int hook_GetDepotDecryptionKey(void *self, uint32_t depot_id,
                                       void *outBuf) {
    if (outBuf && sx_depot_inject_key(outBuf, depot_id))
        return 1;

    fn_GetDepotDecryptionKey orig =
        (fn_GetDepotDecryptionKey)orig_GetDepotDecryptionKey;
    return orig(self, depot_id, outBuf);
}

static sx_hook_def_t g_hooks[] = {
    {
        .name     = "GetDepotDecryptionKey",
        .sig_name = "CCMInterface::GetDepotDecryptionKey",
        .hook_fn  = (void *)hook_GetDepotDecryptionKey,
        .orig_fn  = &orig_GetDepotDecryptionKey,
        .optional = 1,
    },
};

int sx_hooks_depot_count(void) {
    return (int)(sizeof(g_hooks) / sizeof(g_hooks[0]));
}

sx_hook_def_t *sx_hooks_depot_defs(void) {
    return g_hooks;
}
