// Ownership spoofing
#ifndef MACSTEAM_FEATS_APPS_H
#define MACSTEAM_FEATS_APPS_H

#include <stdint.h>
#include "../steam_types.h"

void sx_apps_force_ready(void);

int sx_apps_spoof_ownership(uint32_t appId, int origResult,
                            AppOwnershipInfo_t *info, uint32_t ownerAccountId);

int sx_apps_is_owned_for_depot(uint32_t appId, uint32_t depotCtxAppId, int origResult);

uint32_t sx_apps_inject_subscribed(uint32_t *appids, uint32_t max_apps, uint32_t count);

int sx_apps_is_subscribed(uint32_t appId, int origResult);

#endif // MACSTEAM_FEATS_APPS_H
