"""Static images for a city, drawn from logo A with the standard library (no image library, no fonts).

Logo A is the "cast" mark: a speaker at the dais (a dot on a line) sending out three arcs. It is
defined on a 64 x 64 grid, the same numbers as the SVG in templates/_logo.html. This module draws it
into PNG files:

- the podcast artwork, 1400 x 1400, RGB with no alpha (Apple's rules, see directories.py);
- the share image (Open Graph and Twitter cards), 1200 x 630;
- the favicon PNG, 180 x 180 (also used as the Apple touch icon).

Each shape is drawn with a signed distance, so edges are smooth without supersampling. Only the
pixels near each shape are computed; the rest of a row is the background colour. A city can supply
its own PNG or JPEG in city.json instead.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

SHARE_SIZE = (1200, 630)
ARTWORK_SIZE = (1400, 1400)
FAVICON_SIZE = 180
DOCKET = (0x23, 0x40, 0xC4)  # --docket, the ballot blue
SURFACE = (0xFF, 0xFF, 0xFF)  # --surface
PAPER = (0xF6, 0xF7, 0xFA)  # --paper

Rgb = tuple[int, int, int]


@dataclass(frozen=True)
class _Shape:
    """One part of the mark, in 64-unit grid space."""

    distance: Callable[[float, float], float]
    #: For a grid y, the grid x ranges that may be inside the shape (with a margin).
    spans: Callable[[float, float], list[tuple[float, float]]]
    opacity: float
    colour: Rgb


def _circle_span(cx: float, cy: float, radius: float, y: float) -> list[tuple[float, float]]:
    dy = y - cy
    if abs(dy) > radius:
        return []
    half = math.sqrt(radius * radius - dy * dy)
    return [(cx - half, cx + half)]


def _dot(cx: float, cy: float, radius: float, colour: Rgb) -> _Shape:
    return _Shape(
        distance=lambda x, y: math.hypot(x - cx, y - cy) - radius,
        spans=lambda y, pad: _circle_span(cx, cy, radius + pad, y),
        opacity=1.0,
        colour=colour,
    )


def _rounded_rect(
    x0: float, y0: float, x1: float, y1: float, radius: float, colour: Rgb, opacity: float = 1.0
) -> _Shape:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius

    def distance(x: float, y: float) -> float:
        qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
        outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
        return outside + min(max(qx, qy), 0.0) - radius

    def spans(y: float, pad: float) -> list[tuple[float, float]]:
        if y0 - pad <= y <= y1 + pad:
            return [(x0 - pad, x1 + pad)]
        return []

    return _Shape(distance, spans, opacity, colour)


def _arc(
    cx: float, cy: float, radius: float, half_width: float, opacity: float, colour: Rgb
) -> _Shape:
    """A quarter arc from straight up to straight right of (cx, cy), with round caps."""

    def distance(x: float, y: float) -> float:
        if x >= cx and y <= cy:
            return abs(math.hypot(x - cx, y - cy) - radius) - half_width
        top = math.hypot(x - cx, y - (cy - radius))
        right = math.hypot(x - (cx + radius), y - cy)
        return min(top, right) - half_width

    def spans(y: float, pad: float) -> list[tuple[float, float]]:
        outer = radius + half_width + pad
        inner = max(0.0, radius - half_width - pad)
        found = []
        dy = cy - y
        if 0 <= dy <= outer:
            low = cx + math.sqrt(max(0.0, inner * inner - dy * dy)) if dy < inner else cx
            found.append((low, cx + math.sqrt(outer * outer - dy * dy)))
        found += _circle_span(cx, cy - radius, half_width + pad, y)
        found += _circle_span(cx + radius, cy, half_width + pad, y)
        return found

    return _Shape(distance, spans, opacity, colour)


def _mark(colour: Rgb, *, tile: Rgb | None) -> tuple[_Shape, ...]:
    """Logo A. With ``tile``, the rounded square behind the mark is drawn too."""
    shapes: list[_Shape] = []
    if tile is not None:
        shapes.append(_rounded_rect(0, 0, 64, 64, 14, tile))
    shapes += [
        _dot(19, 42, 5.5, colour),
        _arc(19, 42, 13, 2.5, 1.0, colour),
        _arc(19, 42, 24, 2.5, 0.8, colour),
        _arc(19, 42, 34.5, 2.5, 0.55, colour),
        _rounded_rect(11, 51, 53, 55.5, 2.25, colour),
    ]
    return tuple(shapes)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def draw(
    width: int,
    height: int,
    background: Rgb,
    shapes: tuple[_Shape, ...],
    *,
    origin: tuple[float, float],
    scale: float,
) -> bytes:
    """Draw grid-space shapes onto a solid background. ``scale`` is pixels per grid unit."""
    ox, oy = origin
    pad = 1.0 / scale  # one pixel of margin, in grid units, for the soft edge
    blank = bytes(background) * width
    rows: list[bytes] = []
    for py in range(height):
        gy = (py + 0.5 - oy) / scale
        row: bytearray | None = None
        for shape in shapes:
            ranges = shape.spans(gy, pad)
            if not ranges:
                continue
            if row is None:
                row = bytearray(blank)
            done: set[int] = set()
            for low, high in ranges:
                start = max(0, math.floor(ox + low * scale))
                stop = min(width, math.ceil(ox + high * scale) + 1)
                for px in range(start, stop):
                    if px in done:
                        continue
                    done.add(px)
                    d = shape.distance((px + 0.5 - ox) / scale, gy) * scale
                    cover = min(1.0, max(0.0, 0.5 - d)) * shape.opacity
                    if cover <= 0.0:
                        continue
                    at = px * 3
                    for channel in range(3):
                        under = row[at + channel]
                        row[at + channel] = round(under + (shape.colour[channel] - under) * cover)
        rows.append(bytes(row) if row is not None else blank)
    return _png(width, height, rows)


def gradient_png(width: int, height: int) -> bytes:
    """A plain RGB PNG in the brand blue with logo A, any size. Used by tests as a stand-in image."""
    size = min(width, height) * 0.7
    return draw(
        width,
        height,
        DOCKET,
        _mark(SURFACE, tile=None),
        origin=((width - size) / 2, (height - size) / 2),
        scale=size / 64,
    )


@lru_cache(maxsize=1)
def share_png() -> bytes:
    """1200 x 630: the blue tile with the mark, centred on the paper colour."""
    width, height = SHARE_SIZE
    size = 380
    return draw(
        width,
        height,
        PAPER,
        _mark(SURFACE, tile=DOCKET),
        origin=((width - size) / 2, (height - size) / 2),
        scale=size / 64,
    )


@lru_cache(maxsize=1)
def artwork_png() -> bytes:
    """1400 x 1400 podcast art: the mark in white on full-bleed blue, RGB with no alpha."""
    width, height = ARTWORK_SIZE
    return draw(width, height, DOCKET, _mark(SURFACE, tile=None), origin=(0, 0), scale=width / 64)


@lru_cache(maxsize=1)
def favicon_png() -> bytes:
    """180 x 180 favicon and touch icon: the mark on full-bleed blue."""
    size = FAVICON_SIZE
    return draw(size, size, DOCKET, _mark(SURFACE, tile=None), origin=(0, 0), scale=size / 64)
