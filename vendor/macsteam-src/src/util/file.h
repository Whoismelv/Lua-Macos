// File and path helpers
#ifndef MACSTEAM_UTIL_FILE_H
#define MACSTEAM_UTIL_FILE_H

#include <stdint.h>
#include <stddef.h>

uint8_t *sx_slurp_file(const char *path, size_t *out_len);

const char *sx_resolve_home(void);

void sx_steam_appcache_path(char *buf, size_t bufsz, const char *home, const char *rel);

void sx_macsteam_support_path(char *buf, size_t bufsz, const char *home, const char *rel);

void sx_file_config_path(char *buf, size_t bufsz);

int sx_file_patch_bytes(const char *path, const uint8_t *find, size_t find_len,
                        const uint8_t *replace, size_t write_len);

#endif // MACSTEAM_UTIL_FILE_H
