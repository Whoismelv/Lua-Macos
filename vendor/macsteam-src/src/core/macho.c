#include "macho.h"
#include "../util/log.h"
#include <mach-o/dyld.h>
#include <mach-o/fat.h>
#include <mach/machine.h>
#include <libkern/OSByteOrder.h>
#include <CommonCrypto/CommonDigest.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

int sx_wait_for_image_ex(const char *name, int timeout_ms,
                         const struct mach_header_64 **out_mh, intptr_t *out_slide,
                         char *out_path, size_t path_size) {
    int elapsed = 0;
    while (elapsed < timeout_ms) {
        uint32_t count = _dyld_image_count();
        for (uint32_t i = 0; i < count; i++) {
            const char *img_name = _dyld_get_image_name(i);
            if (img_name && strstr(img_name, name)) {
                *out_mh = (const struct mach_header_64 *)_dyld_get_image_header(i);
                *out_slide = _dyld_get_image_vmaddr_slide(i);
                if (out_path && path_size > 0) {
                    strncpy(out_path, img_name, path_size - 1);
                    out_path[path_size - 1] = '\0';
                }
                return 0;
            }
        }
        usleep(100000);
        elapsed += 100;
    }
    return -1;
}

int sx_get_segment(const struct mach_header_64 *mh, intptr_t slide,
                   const char *segname, uintptr_t *out_base, size_t *out_size) {
    if (!mh || !segname) return -1;

    const uint8_t *ptr = (const uint8_t *)mh + sizeof(struct mach_header_64);
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        const struct load_command *lc = (const struct load_command *)ptr;
            // zero/short cmdsize would spin forever
            if (lc->cmdsize < sizeof(struct load_command)) break;
        if (lc->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *seg = (const struct segment_command_64 *)lc;
            if (strncmp(seg->segname, segname, 16) == 0) {
                *out_base = (uintptr_t)(seg->vmaddr + slide);
                *out_size = (size_t)seg->vmsize;
                return 0;
            }
        }
        ptr += lc->cmdsize;
    }
    return -1;
}

static int hash_range(FILE *f, uint64_t off, uint64_t size, char *out_hex, size_t out_size) {
    if (out_size < 65) return -1;
    if (fseeko(f, (off_t)off, SEEK_SET) != 0) return -1;

    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);

    uint8_t buf[65536];
    uint64_t left = size;
    while (left > 0) {
        size_t want = left < sizeof(buf) ? (size_t)left : sizeof(buf);
        size_t got = fread(buf, 1, want, f);
        if (got == 0) return -1;
        CC_SHA256_Update(&ctx, buf, (CC_LONG)got);
        left -= got;
    }

    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &ctx);
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++)
        snprintf(out_hex + i * 2, 3, "%02x", digest[i]);
    return 0;
}

int sx_hash_arm64_slice(const char *path, char *out_hex, size_t out_size) {
    if (!path || !out_hex || out_size < 65) return -1;

    FILE *f = fopen(path, "rb");
    if (!f) { SX_ERR("hash: cannot open '%s'", path); return -1; }

    uint32_t magic = 0;
    if (fread(&magic, 1, sizeof(magic), f) != sizeof(magic)) { fclose(f); return -1; }

    if (magic == MH_MAGIC_64 || magic == MH_CIGAM_64) {
        fseeko(f, 0, SEEK_END);
        off_t end = ftello(f);
        int rc = (end > 0) ? hash_range(f, 0, (uint64_t)end, out_hex, out_size) : -1;
        fclose(f);
        return rc;
    }

    // Fat header + arch table are big-endian.
    int is64 = (magic == FAT_MAGIC_64 || magic == FAT_CIGAM_64);
    if (magic != FAT_MAGIC && magic != FAT_CIGAM && !is64) {
        SX_ERR("hash: '%s' is neither thin arm64 nor fat", path);
        fclose(f);
        return -1;
    }

    uint32_t nfat = 0;
    if (fread(&nfat, 1, sizeof(nfat), f) != sizeof(nfat)) { fclose(f); return -1; }
    nfat = OSSwapBigToHostInt32(nfat);

    uint64_t off = 0, size = 0;
    int found = 0;
    for (uint32_t i = 0; i < nfat && !found; i++) {
        if (is64) {
            struct fat_arch_64 a;
            if (fread(&a, 1, sizeof(a), f) != sizeof(a)) break;
            if ((cpu_type_t)OSSwapBigToHostInt32(a.cputype) == CPU_TYPE_ARM64) {
                off = OSSwapBigToHostInt64(a.offset);
                size = OSSwapBigToHostInt64(a.size);
                found = 1;
            }
        } else {
            struct fat_arch a;
            if (fread(&a, 1, sizeof(a), f) != sizeof(a)) break;
            if ((cpu_type_t)OSSwapBigToHostInt32(a.cputype) == CPU_TYPE_ARM64) {
                off = OSSwapBigToHostInt32(a.offset);
                size = OSSwapBigToHostInt32(a.size);
                found = 1;
            }
        }
    }

    if (!found) {
        SX_ERR("hash: no arm64 slice in '%s'", path);
        fclose(f);
        return -1;
    }

    int rc = hash_range(f, off, size, out_hex, out_size);
    fclose(f);
    return rc;
}
