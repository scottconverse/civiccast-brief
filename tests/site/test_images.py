"""Generated share and podcast images: a valid PNG of the right size, built with the standard library."""

from __future__ import annotations

import struct
import zlib

from civiccast_brief.site.directories import artwork_problems
from civiccast_brief.site.images import (
    ARTWORK_SIZE,
    DOCKET,
    FAVICON_SIZE,
    PAPER,
    SHARE_SIZE,
    SURFACE,
    artwork_png,
    favicon_png,
    share_png,
)


def _decode(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    width = height = 0
    idat = b""
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        crc = struct.unpack(">I", data[position + 8 + length : position + 12 + length])[0]
        assert zlib.crc32(kind + body) == crc, kind
        if kind == b"IHDR":
            width, height, depth, colour, *_ = struct.unpack(">IIBBBBB", body)
            assert (depth, colour) == (8, 2)
        if kind == b"IDAT":
            idat += body
        position += 12 + length
    return width, height, zlib.decompress(idat)


def test_share_image_has_the_social_card_size() -> None:
    width, height, raw = _decode(share_png())
    assert (width, height) == SHARE_SIZE
    assert len(raw) == height * (1 + 3 * width)


def test_podcast_artwork_is_square() -> None:
    width, height, raw = _decode(artwork_png())
    assert (width, height) == ARTWORK_SIZE
    assert width == height
    assert len(raw) == height * (1 + 3 * width)


def _pixel(width: int, raw: bytes, x: int, y: int) -> tuple[int, int, int]:
    start = y * (1 + 3 * width) + 1 + 3 * x
    return raw[start], raw[start + 1], raw[start + 2]


def test_favicon_is_a_180_pixel_square_with_the_mark_on_blue() -> None:
    width, height, raw = _decode(favicon_png())
    assert (width, height) == (FAVICON_SIZE, FAVICON_SIZE) == (180, 180)
    assert len(raw) == height * (1 + 3 * width)
    assert _pixel(width, raw, 2, 2) == DOCKET
    colours = {_pixel(width, raw, x, y) for y in range(0, height, 6) for x in range(0, width, 6)}
    assert SURFACE in colours, "the white mark is drawn"


def test_artwork_corner_is_blue_and_share_corner_is_paper() -> None:
    width, _, raw = _decode(artwork_png())
    assert _pixel(width, raw, 0, 0) == DOCKET
    width, _, raw = _decode(share_png())
    assert _pixel(width, raw, 0, 0) == PAPER


def test_generated_artwork_passes_the_directory_rules() -> None:
    assert artwork_problems(artwork_png(), ".png") == []
