"""Static images for a city: one share image (Open Graph and Twitter cards) and one podcast artwork.

Each city gets one share image and one square podcast artwork. Both are generated with the standard
library (no image library, no fonts): a teal gradient with a gold rule, so the file works without any
text rendering. A city can supply its own PNG or JPEG in city.json instead. Per-episode images are not
generated; the share metadata for every episode uses the city's image.
"""

from __future__ import annotations

import struct
import zlib

SHARE_SIZE = (1200, 630)
ARTWORK_SIZE = (1400, 1400)
TOP = (0x0D, 0x4F, 0x5C)  # ink teal, from the Craft kit palette
BOTTOM = (0x0E, 0x7C, 0x8B)  # teal, from the Craft kit palette
GOLD = (0xE8, 0xA8, 0x38)  # accent rule, from the Craft kit palette


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def gradient_png(width: int, height: int) -> bytes:
    """A vertical teal gradient with a gold rule across the lower third, as PNG bytes."""
    bar_top = int(height * 0.74)
    bar_bottom = bar_top + max(4, height // 120)
    bar_left = int(width * 0.08)
    bar_right = int(width * 0.08) + max(40, width // 8)
    gold_run = bytes(GOLD) * (bar_right - bar_left)
    raw = bytearray()
    for y in range(height):
        t = y / max(1, height - 1)
        color = bytes(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True))
        row = bytearray(color * width)
        if bar_top <= y < bar_bottom:
            row[bar_left * 3 : bar_right * 3] = gold_run
        raw.append(0)
        raw.extend(row)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def share_png() -> bytes:
    return gradient_png(*SHARE_SIZE)


def artwork_png() -> bytes:
    return gradient_png(*ARTWORK_SIZE)
