// Hex encoding helpers
#ifndef MACSTEAM_UTIL_HEX_H
#define MACSTEAM_UTIL_HEX_H

#include <stdint.h>

int sx_hex_nibble(int c);
int sx_hex_decode(const char *hex, uint8_t *out, int max_out);

#endif // MACSTEAM_UTIL_HEX_H
