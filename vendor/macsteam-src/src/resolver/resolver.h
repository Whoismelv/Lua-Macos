// Signature resolver
#ifndef MACSTEAM_RESOLVER_RESOLVER_H
#define MACSTEAM_RESOLVER_RESOLVER_H

#include <stdint.h>
#include <stddef.h>
#include <mach-o/loader.h>
#include "sigdb.h"

typedef struct {
    const char *name;
    uintptr_t   address;
} sx_resolved_t;

typedef struct {
    sx_resolved_t *entries;
    int            count;
    int            capacity;
} sx_resolve_result_t;

int sx_is_arm64_prologue(uintptr_t addr);

int sx_resolve_all_ex(const struct mach_header_64 *mh, intptr_t slide,
                      const char *dylib_path,
                      sx_sigdb_t *sigdb, sx_resolve_result_t *out);
uintptr_t sx_resolve_find(const sx_resolve_result_t *result, const char *name);
void sx_resolve_result_free(sx_resolve_result_t *result);

#endif // MACSTEAM_RESOLVER_RESOLVER_H
