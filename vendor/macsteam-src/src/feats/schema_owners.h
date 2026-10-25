// Dynamic schema owner discovery via Steam Store reviews
// Based on the approach from SLSsteam by AceSLS (https://github.com/AceSLS/SLSsteam)
#ifndef MACSTEAM_FEATS_SCHEMA_OWNERS_H
#define MACSTEAM_FEATS_SCHEMA_OWNERS_H

#include <stdint.h>

#define SCHEMA_OWNERS_MAX 20
#define SCHEMA_OWNERS_COOLDOWN_MINUTES 10

// Cap the reviews fetch so a hung endpoint can't park the stats worker thread.
#define SCHEMA_OWNERS_FETCH_TIMEOUT_SEC 8.0

typedef struct {
    uint64_t ids[SCHEMA_OWNERS_MAX];
    int      count;
} sx_owner_list_t;

// Fetch reviewer Steam IDs for a game from the Store reviews API.
// Returns owner IDs in out. Respects per-app cooldowns and blacklists.
void sx_schema_owners_get(int appId, sx_owner_list_t *out);

// Record that a given owner failed for an app (private profile, no stats).
void sx_schema_owners_blacklist(int appId, uint64_t owner);

// Record that a given owner succeeded for an app.
void sx_schema_owners_set_preferred(int appId, uint64_t owner);

// Get the preferred (last successful) owner for an app, or 0 if none.
uint64_t sx_schema_owners_preferred(int appId);

#endif
