// AOB pattern scanner
#ifndef MACSTEAM_RESOLVER_AOB_H
#define MACSTEAM_RESOLVER_AOB_H

#include <stdint.h>
#include <stddef.h>
#include <mach-o/loader.h>

typedef struct {
    uint8_t *bytes;
    uint8_t *mask;
    int      length;
} sx_pattern_t;

int sx_pattern_from_hex(const char *hex_str, sx_pattern_t *out);

void sx_pattern_free(sx_pattern_t *pat);

uintptr_t sx_aob_scan_unique(uintptr_t base, size_t size, const sx_pattern_t *pat);
uintptr_t sx_aob_scan_file(const char *file_path, intptr_t slide,
                           const sx_pattern_t *pat);

#endif // MACSTEAM_RESOLVER_AOB_H
