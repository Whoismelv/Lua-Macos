// File and path helpers
#include "file.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pwd.h>

uint8_t *sx_slurp_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long sz = ftell(f);
    if (sz <= 0 || sz > (16 * 1024 * 1024)) { fclose(f); return NULL; }
    rewind(f);

    uint8_t *buf = (uint8_t *)malloc((size_t)sz);
    if (!buf) { fclose(f); return NULL; }
    size_t rd = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    if (rd != (size_t)sz) { free(buf); return NULL; }

    *out_len = (size_t)sz;
    return buf;
}

int sx_file_patch_bytes(const char *path, const uint8_t *find, size_t find_len,
                        const uint8_t *replace, size_t write_len) {
    if (!path || !find || find_len == 0 || !replace || write_len == 0)
        return -1;

    size_t fsize = 0;
    uint8_t *buf = sx_slurp_file(path, &fsize);
    if (!buf) return -1;

    long match = -1;
    for (long i = 0; i <= (long)fsize - (long)find_len; i++) {
        if (memcmp(buf + i, find, find_len) == 0) {
            match = i;
            break;
        }
    }
    free(buf);
    if (match < 0) return 0;

    FILE *f = fopen(path, "r+b");
    if (!f) return -1;

    if (fseek(f, match, SEEK_SET) != 0) {
        fclose(f);
        return -1;
    }
    size_t wrote = fwrite(replace, 1, write_len, f);
    int close_rc = fclose(f);
    return (wrote == write_len && close_rc == 0) ? 1 : -1;
}

void sx_file_config_path(char *buf, size_t bufsz) {
    const char *env = getenv("MACSTEAM_CONFIG");
    if (env && env[0]) {
        snprintf(buf, bufsz, "%s", env);
        return;
    }
    const char *home = sx_resolve_home();
    if (!home) home = "/tmp";
    sx_macsteam_support_path(buf, bufsz, home, "config.yaml");
}

const char *sx_resolve_home(void) {
    const char *home = getenv("HOME");
    if (home && home[0]) return home;
    struct passwd *pw = getpwuid(getuid());
    return (pw && pw->pw_dir) ? pw->pw_dir : NULL;
}

void sx_steam_appcache_path(char *buf, size_t bufsz, const char *home, const char *rel) {
    snprintf(buf, bufsz, "%s/Library/Application Support/Steam/appcache/%s", home, rel);
}

void sx_macsteam_support_path(char *buf, size_t bufsz, const char *home, const char *rel) {
    if (rel && rel[0])
        snprintf(buf, bufsz, "%s/Library/Application Support/macsteam/%s", home, rel);
    else
        snprintf(buf, bufsz, "%s/Library/Application Support/macsteam", home);
}
