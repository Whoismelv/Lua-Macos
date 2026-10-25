// Logging
#include "log.h"
#include "file.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>

int sx_log_level = SX_LVL_INFO;
FILE *sx_log_file = NULL;

#define SX_ONCE_CAP 256
struct sx_once_entry { const void *site; unsigned long key; int used; };
static struct sx_once_entry sx_once_tbl[SX_ONCE_CAP];

int sx_log_once_seen(const void *site, unsigned long key) {
    unsigned long h = ((unsigned long)(uintptr_t)site * 2654435761UL) ^
                      (key * 40503UL);
    for (int probe = 0; probe < SX_ONCE_CAP; probe++) {
        int i = (int)((h + (unsigned long)probe) % SX_ONCE_CAP);
        if (!sx_once_tbl[i].used) {
            sx_once_tbl[i].site = site;
            sx_once_tbl[i].key  = key;
            sx_once_tbl[i].used = 1;
            return 1; // first time seen
        }
        if (sx_once_tbl[i].site == site && sx_once_tbl[i].key == key)
            return 0; // already logged
    }
    return 1; // table full, fail open (log it)
}

void sx_log_init(void) {
    const char *env = getenv("MACSTEAM_LOG_LEVEL");
    if (env) {
        int v = atoi(env);
        if (v >= 0 && v <= 3)
            sx_log_level = v;
    }

    if (sx_log_file) return; // already opened

    const char *home = sx_resolve_home();
    if (!home) home = "/tmp";

    char dir[512], path[512];
    sx_macsteam_support_path(dir, sizeof(dir), home, NULL);
    mkdir(dir, 0755);
    snprintf(path, sizeof(path), "%s/macsteam.log", dir);
    sx_log_file = fopen(path, "a");
    if (sx_log_file) setlinebuf(sx_log_file);
}
