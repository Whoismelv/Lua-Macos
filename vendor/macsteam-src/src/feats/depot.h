// Depot decryption key injection
#ifndef MACSTEAM_FEATS_DEPOT_H
#define MACSTEAM_FEATS_DEPOT_H

#include <stdint.h>

void sx_depot_set_helpers(uintptr_t ensure_capacity, uintptr_t seek_put);

int sx_depot_inject_key(void *outBuf, uint32_t depot_id);

#endif // MACSTEAM_FEATS_DEPOT_H
