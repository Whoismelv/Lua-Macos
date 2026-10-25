// Hex encoding helpers
#include "hex.h"

int sx_hex_nibble(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int sx_hex_decode(const char *hex, uint8_t *out, int max_out) {
    int count = 0;
    for (int i = 0; hex[i] && hex[i+1] && count < max_out; i += 2) {
        int hi = sx_hex_nibble((unsigned char)hex[i]);
        int lo = sx_hex_nibble((unsigned char)hex[i+1]);
        if (hi < 0 || lo < 0) return -1;
        out[count++] = (uint8_t)((hi << 4) | lo);
    }
    return count;
}
