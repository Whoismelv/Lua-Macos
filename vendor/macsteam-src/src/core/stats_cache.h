// Steam stats parser
#ifndef MACSTEAM_CORE_STATS_CACHE_H
#define MACSTEAM_CORE_STATS_CACHE_H

#include <stdint.h>
#include <stddef.h>

#define SX_STATS_MAX_STATS   512
#define SX_STATS_ACH_BITS    32

typedef struct {
    uint32_t stat_id;
    uint32_t value;
    int      has_ach_times;
    uint32_t unlock_time[SX_STATS_ACH_BITS];
} sx_stat_entry_t;

typedef struct {
    uint32_t        crc;
    int             stat_count;
    sx_stat_entry_t stats[SX_STATS_MAX_STATS];
} sx_stats_snapshot_t;

// Returns 1 parsed, 0 no/empty blob, -1 parse error.
int sx_stats_cache_load(int app_id, uint32_t account_id, sx_stats_snapshot_t *out);

int sx_stats_schema_path(int app_id, char *buf, size_t bufsz);

#endif // MACSTEAM_CORE_STATS_CACHE_H
