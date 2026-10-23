#!/usr/bin/env python3
# language: python3, file: make_icon.py, runtime: stdlib only
# Generates icon.icns (PNG-based ICNS) without any image library.
import struct
import zlib
from pathlib import Path

SIZES = [(16, "icp4"), (32, "icp5"), (64, "icp6"), (128, "ic07"),
         (256, "ic08"), (512, "ic09"), (1024, "ic10")]

BG = (15, 23, 42, 255)        # slate 900
CARD = (24, 38, 60, 255)
ACCENT = (47, 111, 78, 255)   # engine green
LIGHT = (236, 253, 245, 255)  # mint white


def rounded(x, y, w, h, r):
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def png(w, h, px):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(px[y * w * 4:(y + 1) * w * 4])
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def render(size):
    s = float(size)
    px = bytearray(size * size * 4)
    r_out = s * 0.22
    r_in = s * 0.17
    pad = s * 0.10
    bar_l = s * 0.17          # stroke width
    x0 = s * 0.30
    y0 = s * 0.28
    y1 = s * 0.74
    for y in range(size):
        for x in range(size):
            i = (y * size + x) * 4
            col = (0, 0, 0, 0)
            if rounded(x + 0.5, y + 0.5, s, s, r_out):
                col = BG
                if rounded(x + 0.5 - pad, y + 0.5 - pad, s - 2 * pad, s - 2 * pad, r_in):
                    col = CARD
            # monogram: L
            if x0 <= x <= x0 + bar_l and y0 <= y <= y1:
                col = LIGHT
            if x0 <= x <= x0 + s * 0.34 and y1 - bar_l <= y <= y1:
                col = LIGHT
            # monogram: T
            tx = s * 0.52
            if tx <= x <= tx + s * 0.30 and y0 <= y <= y0 + bar_l:
                col = ACCENT
            if tx + s * 0.15 - bar_l / 2 <= x <= tx + s * 0.15 + bar_l / 2 and y0 <= y <= y1:
                col = ACCENT
            px[i:i + 4] = bytes(col)
    return png(size, size, px)


def main():
    body = bytearray()
    for size, tag in SIZES:
        data = render(size)
        body += tag.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    out = Path(__file__).with_name("icon.icns")
    out.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + bytes(body))
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
