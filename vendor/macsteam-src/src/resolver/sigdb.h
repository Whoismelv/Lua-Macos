// Signature database for a Steam build
#ifndef MACSTEAM_RESOLVER_SIGDB_H
#define MACSTEAM_RESOLVER_SIGDB_H

#include <stdint.h>
#include <stddef.h>
#include "anchor.h"

typedef struct {
    char        name[128];
    char        aob_hex[4096];
    uintptr_t   reference_va;
    int         deprecated;
    int32_t     match_offset;   // bytes back from pattern hit to function start
    sx_anchor_t anchor;         // update-safe fallback when AOB misses
} sx_sig_entry_t;

typedef struct {
    sx_sig_entry_t *signatures;
    int             sig_count;
    int             schema_version;      // file format revision
    int             sigdb_version;       // signature-set revision for this Steam build
    uint64_t        steam_build;         // Steam client build id this profile targets
    char            steam_build_date[32];
} sx_sigdb_t;

int sx_sigdb_load(const char *path, sx_sigdb_t *out);
void sx_sigdb_free(sx_sigdb_t *p);

uint64_t sx_sigdb_peek_build(const char *path);

#endif // MACSTEAM_RESOLVER_SIGDB_H
