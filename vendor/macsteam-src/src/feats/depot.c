#include "depot.h"
#include "../config/config.h"
#include "../constants.h"
#include "../util/hex.h"
#include "../util/log.h"
#include <string.h>
#include <stdint.h>
#include <pthread.h>

typedef void *(*fn_EnsureCapacity)(void *buf, int num);
typedef void *(*fn_SeekPut)(void *buf, int seek_type, int offset);

static fn_EnsureCapacity g_ensure_capacity = NULL;
static fn_SeekPut        g_seek_put        = NULL;

#define UTLBUF_SEEK_HEAD 0
#define UTLBUF_PMEMORY   0

void sx_depot_set_helpers(uintptr_t ensure_capacity, uintptr_t seek_put) {
    g_ensure_capacity = (fn_EnsureCapacity)ensure_capacity;
    g_seek_put        = (fn_SeekPut)seek_put;
}

#define MAX_TRACKED_DEPOTS 128
#define REJECT_LOOP_THRESHOLD 8

static struct {
    uint32_t depot_id;
    int count;
} g_inject_count[MAX_TRACKED_DEPOTS];
static int g_inject_count_n = 0;

// Multiple worker threads hit this concurrently.
static pthread_mutex_t g_inject_count_lock = PTHREAD_MUTEX_INITIALIZER;

static int bump_inject_count(uint32_t depot_id) {
    pthread_mutex_lock(&g_inject_count_lock);
    int n = 1;
    int done = 0;
    for (int i = 0; i < g_inject_count_n; i++) {
        if (g_inject_count[i].depot_id == depot_id) {
            n = ++g_inject_count[i].count;
            done = 1;
            break;
        }
    }
    if (!done && g_inject_count_n < MAX_TRACKED_DEPOTS) {
        g_inject_count[g_inject_count_n].depot_id = depot_id;
        g_inject_count[g_inject_count_n].count = 1;
        g_inject_count_n++;
    }
    pthread_mutex_unlock(&g_inject_count_lock);
    return n;
}

int sx_depot_inject_key(void *outBuf, uint32_t depot_id) {
    sx_config_t *g_cfg = sx_config_current;
    if (!g_cfg)
        return 0;

    char key_hex[65];
    if (sx_config_get_depot_key_any(g_cfg, (int)depot_id, key_hex, sizeof(key_hex)) != 0)
        return 0;

    uint8_t key_raw[32];
    int decoded = sx_hex_decode(key_hex, key_raw, 32);
    if (decoded != 32) {
        SX_WARN("RequestDepotDecryptionKey: depot %u key decode failed (%d bytes)", depot_id, decoded);
        return 0;
    }

    int n = bump_inject_count(depot_id);
    if (n == REJECT_LOOP_THRESHOLD) {
        SX_WARN("RequestDepotDecryptionKey: depot %u injected %dx, key may be wrong", depot_id, n);
    }

    if (!g_ensure_capacity || !g_seek_put) {
        SX_WARN("RequestDepotDecryptionKey: depot %u CUtlBuffer helpers unresolved, fallback to CM", depot_id);
        return 0;
    }

    g_ensure_capacity(outBuf, 128);

    uint8_t *buf = (uint8_t *)outBuf;
    void *mem = *(void **)(buf + UTLBUF_PMEMORY);
    if (!mem) {
        SX_WARN("RequestDepotDecryptionKey: depot %u EnsureCapacity did not allocate, fallback to CM", depot_id);
        return 0;
    }

    memcpy(mem, key_raw, UTLBUF_KEY_SIZE);
    g_seek_put(outBuf, UTLBUF_SEEK_HEAD, UTLBUF_KEY_SIZE);

    SX_LOG("GetDepotDecryptionKey: INJECTED key for depot %u [%d/%d]",
           depot_id, n, REJECT_LOOP_THRESHOLD);

    return 1;
}
