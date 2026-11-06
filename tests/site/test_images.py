"""Generated share and podcast images: a valid PNG of the right size, built with the standard library."""

from __future__ import annotations

import struct
import zlib

from civiccast_brief.site.images import ARTWORK_SIZE, SHARE_SIZE, artwork_png, share_png


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
