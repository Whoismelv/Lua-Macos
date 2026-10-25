#ifndef MACSTEAM_CORE_MACHO_H
#define MACSTEAM_CORE_MACHO_H

#include <stdint.h>
#include <stddef.h>
#include <mach-o/loader.h>

// Polls every 100ms. out_path optional (may be NULL).
int sx_wait_for_image_ex(const char *name, int timeout_ms,
                         const struct mach_header_64 **out_mh, intptr_t *out_slide,
                         char *out_path, size_t path_size);

int sx_get_segment(const struct mach_header_64 *mh, intptr_t slide,
                   const char *segname, uintptr_t *out_base, size_t *out_size);

// SHA-256. out_hex needs 65 bytes.
int sx_hash_arm64_slice(const char *path, char *out_hex, size_t out_size);

#endif // MACSTEAM_CORE_MACHO_H
