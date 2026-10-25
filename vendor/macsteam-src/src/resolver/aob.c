// AOB pattern scanner
#include "aob.h"
#include "../util/log.h"
#include "../util/hex.h"
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <mach-o/fat.h>
#include <mach-o/loader.h>

int sx_pattern_from_hex(const char *hex_str, sx_pattern_t *out) {
    if (!hex_str || !out) return -1;
    memset(out, 0, sizeof(*out));

    int count = 0;
    const char *p = hex_str;
    while (*p) {
        while (*p == ' ') p++;
        if (!*p) break;
        if (p[0] == '?' && p[1] == '?') { count++; p += 2; }
        else if (sx_hex_nibble(p[0]) >= 0 && sx_hex_nibble(p[1]) >= 0) { count++; p += 2; }
        else return -1;
    }

    if (count == 0) return -1;

    out->bytes = (uint8_t *)calloc((size_t)count, 1);
    out->mask  = (uint8_t *)calloc((size_t)count, 1);
    if (!out->bytes || !out->mask) { sx_pattern_free(out); return -1; }
    out->length = count;

    int idx = 0;
    p = hex_str;
    while (*p) {
        while (*p == ' ') p++;
        if (!*p) break;
        if (p[0] == '?' && p[1] == '?') {
            out->bytes[idx] = 0;
            out->mask[idx] = 0;
            p += 2; idx++;
        } else {
            out->bytes[idx] = (uint8_t)((sx_hex_nibble(p[0]) << 4) | sx_hex_nibble(p[1]));
            out->mask[idx] = 0xFF;
            p += 2; idx++;
        }
    }
    return 0;
}

void sx_pattern_free(sx_pattern_t *pat) {
    if (!pat) return;
    free(pat->bytes); pat->bytes = NULL;
    free(pat->mask);  pat->mask = NULL;
    pat->length = 0;
}

static int find_anchor(const sx_pattern_t *pat, uint8_t *anchor_byte) {
    for (int i = 0; i < pat->length; i++) {
        if (pat->mask[i] == 0xFF) {
            *anchor_byte = pat->bytes[i];
            return i;
        }
    }
    return -1;
}

static inline int pattern_match(const uint8_t *ptr, const sx_pattern_t *pat) {
    int len = pat->length;
    const uint8_t *pb = pat->bytes;
    const uint8_t *pm = pat->mask;

    int i = 0;
    for (; i + 4 <= len; i += 4) {
        uint32_t data = *(const uint32_t *)(ptr + i);
        uint32_t mask = *(const uint32_t *)(pm + i);
        uint32_t expect = *(const uint32_t *)(pb + i);
        if ((data & mask) != expect) return 0;
    }
    for (; i < len; i++) {
        if ((ptr[i] & pm[i]) != pb[i]) return 0;
    }
    return 1;
}

uintptr_t sx_aob_scan_unique(uintptr_t base, size_t size, const sx_pattern_t *pat) {
    if (!pat || pat->length <= 0 || (size_t)pat->length > size) return 0;

    uint8_t anchor_byte = 0;
    int anchor_off = find_anchor(pat, &anchor_byte);
    if (anchor_off < 0) return 0; // all wildcards, can't scan

    uintptr_t result = 0;
    int matches = 0;

    size_t scan_end = size - (size_t)pat->length;
    const uint8_t *mem = (const uint8_t *)base;

    for (size_t i = 0; i <= scan_end; i += 4) {
        if (mem[i + anchor_off] != anchor_byte) continue;
        if (pattern_match(mem + i, pat)) {
            matches++;
            if (matches == 1) {
                result = base + i;
            } else {
                return 0; // not unique
            }
        }
    }

    return (matches == 1) ? result : 0;
}

static int find_text_segment(const uint8_t *mh_ptr, const uint8_t *map_end,
                             uint64_t *out_fileoff, uint64_t *out_vmaddr,
                             uint64_t *out_filesize) {
    // File may be truncated mid-update
    if (mh_ptr + sizeof(struct mach_header_64) > map_end) return -1;
    const struct mach_header_64 *mh = (const struct mach_header_64 *)mh_ptr;
    if (mh->magic != MH_MAGIC_64) return -1;

    const uint8_t *ptr = mh_ptr + sizeof(struct mach_header_64);
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        if (ptr + sizeof(struct load_command) > map_end) return -1;
        const struct load_command *lc = (const struct load_command *)ptr;
        if (lc->cmdsize < sizeof(struct load_command)) return -1;
        if (ptr + lc->cmdsize > map_end) return -1;
        if (lc->cmd == LC_SEGMENT_64 &&
            lc->cmdsize >= sizeof(struct segment_command_64)) {
            const struct segment_command_64 *seg = (const struct segment_command_64 *)lc;
            if (strncmp(seg->segname, "__TEXT", 16) == 0) {
                *out_fileoff = seg->fileoff;
                *out_vmaddr = seg->vmaddr;
                *out_filesize = seg->filesize;
                return 0;
            }
        }
        ptr += lc->cmdsize;
    }
    return -1;
}

uintptr_t sx_aob_scan_file(const char *file_path, intptr_t slide,
                           const sx_pattern_t *pat) {
    if (!file_path || !pat || pat->length <= 0) return 0;

    int fd = open(file_path, O_RDONLY);
    if (fd < 0) {
        SX_WARN("aob_file: cannot open '%s'", file_path);
        return 0;
    }

    struct stat st;
    if (fstat(fd, &st) != 0) { close(fd); return 0; }
    size_t file_size = (size_t)st.st_size;

    void *map = mmap(NULL, file_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (map == MAP_FAILED) {
        SX_WARN("aob_file: mmap failed for '%s'", file_path);
        return 0;
    }

    const uint8_t *file_base = (const uint8_t *)map;
    const uint8_t *map_end = file_base + file_size;
    const uint8_t *mh_ptr = NULL;

    if (file_size < sizeof(uint32_t)) {
        SX_WARN("aob_file: '%s' too small (%zu bytes)", file_path, file_size);
        munmap(map, file_size);
        return 0;
    }

    uint32_t magic = *(const uint32_t *)file_base;
    if (magic == FAT_MAGIC_64 || magic == FAT_CIGAM_64) {
        const struct fat_header *fh = (const struct fat_header *)file_base;
        uint32_t narch = OSSwapBigToHostInt32(fh->nfat_arch);
        const struct fat_arch_64 *archs = (const struct fat_arch_64 *)(file_base + sizeof(struct fat_header));
        if (sizeof(struct fat_header) + (uint64_t)narch * sizeof(struct fat_arch_64) > file_size)
            narch = 0;
        for (uint32_t i = 0; i < narch; i++) {
            cpu_type_t cputype = (cpu_type_t)OSSwapBigToHostInt32(archs[i].cputype);
            cpu_subtype_t cpusubtype = (cpu_subtype_t)OSSwapBigToHostInt32(archs[i].cpusubtype);
            if (cputype == CPU_TYPE_ARM64 && (cpusubtype & ~CPU_SUBTYPE_MASK) == CPU_SUBTYPE_ARM64_ALL) {
                uint64_t offset = OSSwapBigToHostInt64(archs[i].offset);
                if (offset <= file_size && offset + sizeof(struct mach_header_64) <= file_size)
                    mh_ptr = file_base + offset;
                break;
            }
        }
    } else if (magic == FAT_MAGIC || magic == FAT_CIGAM) {
        const struct fat_header *fh = (const struct fat_header *)file_base;
        uint32_t narch = OSSwapBigToHostInt32(fh->nfat_arch);
        const struct fat_arch *archs = (const struct fat_arch *)(file_base + sizeof(struct fat_header));
        if (sizeof(struct fat_header) + (uint64_t)narch * sizeof(struct fat_arch) > file_size)
            narch = 0;
        for (uint32_t i = 0; i < narch; i++) {
            cpu_type_t cputype = (cpu_type_t)OSSwapBigToHostInt32(archs[i].cputype);
            cpu_subtype_t cpusubtype = (cpu_subtype_t)OSSwapBigToHostInt32(archs[i].cpusubtype);
            if (cputype == CPU_TYPE_ARM64 && (cpusubtype & ~CPU_SUBTYPE_MASK) == CPU_SUBTYPE_ARM64_ALL) {
                uint32_t offset = OSSwapBigToHostInt32(archs[i].offset);
                if ((uint64_t)offset + sizeof(struct mach_header_64) <= file_size)
                    mh_ptr = file_base + offset;
                break;
            }
        }
    } else if (magic == MH_MAGIC_64) {
        const struct mach_header_64 *mh = (const struct mach_header_64 *)file_base;
        if (mh->cputype == CPU_TYPE_ARM64)
            mh_ptr = file_base;
    }

    if (!mh_ptr) {
        SX_WARN("aob_file: no arm64 slice found in '%s'", file_path);
        munmap(map, file_size);
        return 0;
    }

    uint64_t text_fileoff = 0, text_vmaddr = 0, text_filesize = 0;
    if (find_text_segment(mh_ptr, map_end, &text_fileoff, &text_vmaddr, &text_filesize) != 0) {
        SX_WARN("aob_file: __TEXT segment not found in arm64 slice");
        munmap(map, file_size);
        return 0;
    }

    const uint8_t *text_data = mh_ptr + text_fileoff;
    if (text_data < file_base || text_data > map_end ||
        (uint64_t)text_filesize > (uint64_t)(map_end - text_data)) {
        SX_WARN("aob_file: __TEXT extent out of bounds (truncated file?)");
        munmap(map, file_size);
        return 0;
    }
    size_t scan_size = (size_t)text_filesize;

    uintptr_t hit = sx_aob_scan_unique((uintptr_t)text_data, scan_size, pat);
    uintptr_t result_offset = hit ? (hit - (uintptr_t)text_data) : 0;
    munmap(map, file_size);

    if (!hit) return 0;

    uintptr_t runtime_addr = (uintptr_t)text_vmaddr + result_offset + (uintptr_t)slide;
    return runtime_addr;
}
