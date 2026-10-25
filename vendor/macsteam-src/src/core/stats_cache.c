// Steam stats parser
#include "stats_cache.h"
#include "../constants.h"
#include "../util/log.h"
#include "../util/file.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <dirent.h>

enum {
    BKV_SECTION = 0x00,
    BKV_STRING  = 0x01,
    BKV_INT     = 0x02,
    BKV_FLOAT   = 0x03,
    BKV_UINT64  = 0x07,
    BKV_END     = 0x08,
    BKV_INT64   = 0x0A,
};

#define BKV_MAX_DEPTH 128

static int name_as_u32(const char *name, size_t n, uint32_t *out) {
    if (n == 0) return 0;
    uint64_t v = 0;
    for (size_t i = 0; i < n; i++) {
        if (name[i] < '0' || name[i] > '9') return 0;
        v = v * 10 + (uint32_t)(name[i] - '0');
        if (v > 0xFFFFFFFFull) return 0;
    }
    *out = (uint32_t)v;
    return 1;
}

static void build_usergamestats_path(char *buf, size_t bufsz, const char *stats_dir,
                                     uint32_t account, int app_id) {
    snprintf(buf, bufsz, "%s/UserGameStats_%u_%d.bin", stats_dir, account, app_id);
}

static uint32_t resolve_account_id_for_app(const char *stats_dir, int app_id,
                                           uint32_t hint) {
    char suffix[64];
    int slen = snprintf(suffix, sizeof(suffix), "_%d.bin", app_id);

    if (hint != 0) {
        char path[1200];
        build_usergamestats_path(path, sizeof(path), stats_dir, hint, app_id);
        if (access(path, R_OK) == 0) return hint;
        // Don't serve another account's stats.
        return 0;
    }

    DIR *d = opendir(stats_dir);
    if (!d) return 0;
    const char *prefix = "UserGameStats_";
    size_t plen = strlen(prefix);
    uint32_t found = 0;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        const char *n = ent->d_name;
        size_t nl = strlen(n);
        if (nl <= plen || strncmp(n, prefix, plen) != 0) continue;
        if (slen < 0 || nl < plen + (size_t)slen || strcmp(n + nl - slen, suffix) != 0) continue;
        const char *acct = n + plen;
        size_t acct_len = nl - plen - (size_t)slen;
        uint32_t v;
        if (name_as_u32(acct, acct_len, &v)) { found = v; break; }
    }
    closedir(d);
    return found;
}

static uint8_t *read_native_blob(int app_id, uint32_t account_hint,
                                 size_t *out_len) {
    *out_len = 0;
    const char *home = sx_resolve_home();
    if (!home) return NULL;

    char stats_dir[1024];
    sx_steam_appcache_path(stats_dir, sizeof(stats_dir), home, "stats");

    uint32_t account_id = resolve_account_id_for_app(stats_dir, app_id, account_hint);
    if (account_id == 0) return NULL;

    char path[1200];
    build_usergamestats_path(path, sizeof(path), stats_dir, account_id, app_id);

    return sx_slurp_file(path, out_len);
}

int sx_stats_schema_path(int app_id, char *buf, size_t bufsz) {
    const char *home = sx_resolve_home();
    if (!home) return -1;
    char rel[64];
    snprintf(rel, sizeof(rel), "stats/UserGameStatsSchema_%d.bin", app_id);
    sx_steam_appcache_path(buf, bufsz, home, rel);
    return 0;
}

typedef struct {
    const uint8_t *data;
    size_t         len;
    size_t         pos;
} bkv_cur_t;

static const char *bkv_read_name(bkv_cur_t *c, size_t *out_len) {
    size_t start = c->pos;
    while (c->pos < c->len && c->data[c->pos] != 0) c->pos++;
    if (c->pos >= c->len) return NULL;
    *out_len = c->pos - start;
    const char *s = (const char *)(c->data + start);
    c->pos++;
    return s;
}

static int bkv_read_u32(bkv_cur_t *c, uint32_t *out) {
    if (c->pos + 4 > c->len) return 0;
    uint32_t v;
    memcpy(&v, c->data + c->pos, 4);
    c->pos += 4;
    *out = v;
    return 1;
}

static int bkv_skip_scalar(bkv_cur_t *c, uint8_t type) {
    switch (type) {
    case BKV_STRING: {
        while (c->pos < c->len && c->data[c->pos] != 0) c->pos++;
        if (c->pos >= c->len) return 0;
        c->pos++;
        return 1;
    }
    case BKV_INT:
    case BKV_FLOAT:
        if (c->pos + 4 > c->len) return 0;
        c->pos += 4;
        return 1;
    case BKV_UINT64:
    case BKV_INT64:
        if (c->pos + 8 > c->len) return 0;
        c->pos += 8;
        return 1;
    default:
        return 0;
    }
}

static int bkv_skip_section(bkv_cur_t *c, int depth) {
    if (depth > BKV_MAX_DEPTH) return 0;
    while (c->pos < c->len) {
        uint8_t type = c->data[c->pos++];
        if (type == BKV_END) return 1;
        size_t nlen;
        if (!bkv_read_name(c, &nlen)) return 0;
        if (type == BKV_SECTION) {
            if (!bkv_skip_section(c, depth + 1)) return 0;
        } else if (!bkv_skip_scalar(c, type)) {
            return 0;
        }
    }
    return 0;
}

static int parse_ach_times(bkv_cur_t *c, sx_stat_entry_t *st, int depth) {
    if (depth > BKV_MAX_DEPTH) return 0;
    st->has_ach_times = 1;
    while (c->pos < c->len) {
        uint8_t type = c->data[c->pos++];
        if (type == BKV_END) return 1;
        size_t nlen;
        const char *name = bkv_read_name(c, &nlen);
        if (!name) return 0;

        if (type == BKV_INT) {
            uint32_t ts;
            if (!bkv_read_u32(c, &ts)) return 0;
            uint32_t bit;
            if (name_as_u32(name, nlen, &bit) && bit < SX_STATS_ACH_BITS)
                st->unlock_time[bit] = ts;
        } else if (type == BKV_SECTION) {
            if (!bkv_skip_section(c, depth + 1)) return 0;
        } else if (!bkv_skip_scalar(c, type)) {
            return 0;
        }
    }
    return 0;
}

static int parse_stat_section(bkv_cur_t *c, uint32_t stat_id,
                              sx_stats_snapshot_t *out, int depth) {
    if (depth > BKV_MAX_DEPTH) return 0;
    if (out->stat_count >= SX_STATS_MAX_STATS) {
        // Skip past it so the parser doesn't desync.
        return bkv_skip_section(c, depth);
    }

    sx_stat_entry_t st;
    memset(&st, 0, sizeof(st));
    st.stat_id = stat_id;
    int have_data = 0;

    while (c->pos < c->len) {
        uint8_t type = c->data[c->pos++];
        if (type == BKV_END) break;
        size_t nlen;
        const char *name = bkv_read_name(c, &nlen);
        if (!name) return 0;

        if (type == BKV_INT && nlen == 4 && memcmp(name, "data", 4) == 0) {
            if (!bkv_read_u32(c, &st.value)) return 0;
            have_data = 1;
        } else if (type == BKV_SECTION && nlen == 16 &&
                   memcmp(name, "AchievementTimes", 16) == 0) {
            if (!parse_ach_times(c, &st, depth + 1)) return 0;
        } else if (type == BKV_SECTION) {
            if (!bkv_skip_section(c, depth + 1)) return 0;
        } else if (!bkv_skip_scalar(c, type)) {
            return 0;
        }
    }

    if (!have_data && !st.has_ach_times)
        return 1;

    // A set bit with time 0 would emit set-but-zero; give it a nonzero time.
    if (st.has_ach_times) {
        for (int b = 0; b < SX_STATS_ACH_BITS; b++) {
            if ((st.value & (1u << b)) && st.unlock_time[b] == 0)
                st.unlock_time[b] = SX_ACH_FALLBACK_UNLOCK_TIME;
        }
    }

    out->stats[out->stat_count++] = st;
    return 1;
}

static int parse_cache_section(bkv_cur_t *c, sx_stats_snapshot_t *out, int depth) {
    if (depth > BKV_MAX_DEPTH) return 0;
    while (c->pos < c->len) {
        uint8_t type = c->data[c->pos++];
        if (type == BKV_END) return 1;
        size_t nlen;
        const char *name = bkv_read_name(c, &nlen);
        if (!name) return 0;

        if (type == BKV_INT && nlen == 3 && memcmp(name, "crc", 3) == 0) {
            if (!bkv_read_u32(c, &out->crc)) return 0;
            continue;
        }
        if (type == BKV_SECTION) {
            uint32_t stat_id;
            if (name_as_u32(name, nlen, &stat_id)) {
                if (!parse_stat_section(c, stat_id, out, depth + 1)) return 0;
            } else {
                // unexpected subsection, skip
                if (!bkv_skip_section(c, depth + 1)) return 0;
            }
            continue;
        }
        // PendingChanges + any other scalar: skip.
        if (!bkv_skip_scalar(c, type)) return 0;
    }
    return 0;
}

int sx_stats_cache_load(int app_id, uint32_t account_id, sx_stats_snapshot_t *out) {
    if (!out) return -1;
    memset(out, 0, sizeof(*out));

    size_t blob_len = 0;
    uint8_t *blob = read_native_blob(app_id, account_id, &blob_len);
    if (!blob || blob_len == 0) { free(blob); return 0; }

    bkv_cur_t c = { blob, blob_len, 0 };

    if (c.pos >= c.len || c.data[c.pos++] != BKV_SECTION) { free(blob); return -1; }
    size_t nlen;
    const char *root = bkv_read_name(&c, &nlen);
    if (!root || nlen != 5 || memcmp(root, "cache", 5) != 0) { free(blob); return -1; }

    int ok = parse_cache_section(&c, out, 1);
    free(blob);
    if (!ok) {
        SX_DBG("[statsstore] app %d: BKV parse failed (acct=%u)", app_id, account_id);
        return -1;
    }

    int unlocked = 0;
    for (int i = 0; i < out->stat_count; i++)
        if (out->stats[i].value) unlocked++;
    SX_DBG("[statsstore] app %d: %d stat(s), crc=0x%08X, %d with unlock bits",
           app_id, out->stat_count, out->crc, unlocked);
    return 1;
}
