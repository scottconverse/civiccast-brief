"""Small original illustrations for stories with no video, drawn as inline SVG.

Every story card without a video still gets a picture. These are simple geometric scenes in the
site's palette, one per topic (see :mod:`civiccast_brief.site.topics`), plus a civic fallback.
They are written by :mod:`civiccast_brief.site.build` to ``/assets/topics/`` so the page only
points at the site's own copy. No external service, stock photo or copied artwork is used.

Each file is 640 x 360, the card's 16:9 shape, and keeps its main shapes away from the edges so
it still reads as a small thumbnail on a phone.
"""

from __future__ import annotations

from collections.abc import Callable

GRAPHIC_DIR = "/assets/topics"
FALLBACK = "civic"

PAPER = "#0e1120"
SURFACE = "#1b2036"
INK = "#e8eaf3"
MUTED = "#8b94b5"
BLUE = "#8ea2ff"
TEAL = "#3dbdbd"
PURPLE = "#b985d6"
ORANGE = "#f0a868"
GREEN = "#6fcf97"

_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" width="640" height="360" '
    'role="img" preserveAspectRatio="xMidYMid slice">'
)
_CLOSE = "</svg>"


def _frame(accent: str, body: str) -> str:
    """The shared backdrop: dark paper, a soft glow, and a faint dot grid."""
    return (
        _OPEN + f"<defs><radialGradient id='g' cx='50%' cy='30%' r='75%'>"
        f"<stop offset='0' stop-color='{accent}' stop-opacity='.22'/>"
        f"<stop offset='1' stop-color='{accent}' stop-opacity='0'/></radialGradient></defs>"
        + f"<rect width='640' height='360' fill='{PAPER}'/>"
        + "<rect width='640' height='360' fill='url(#g)'/>"
        + "<g fill='#ffffff' opacity='.07'>"
        + "".join(
            f"<circle cx='{x}' cy='{y}' r='1.6'/>"
            for x in range(48, 620, 48)
            for y in range(40, 340, 48)
        )
        + "</g>"
        + body
        + _CLOSE
    )


def _budget() -> str:
    """Money: a rising bar chart, coin stacks and a dollar disc."""
    bars = "".join(
        f"<rect x='{132 + index * 58}' y='{258 - height}' width='40' height='{height}' rx='7' "
        f"fill='{BLUE}' opacity='{0.32 + index * 0.14}'/>"
        for index, height in enumerate((62, 104, 148, 196))
    )
    coins = "".join(
        f"<g transform='translate({452 + stack * 52},{300 - level * 13})'>"
        f"<ellipse cx='0' cy='6' rx='24' ry='9' fill='{ORANGE}' opacity='.55'/>"
        f"<rect x='-24' y='-7' width='48' height='13' fill='{ORANGE}' opacity='.8'/>"
        f"<ellipse cx='0' cy='-7' rx='24' ry='9' fill='{ORANGE}'/>"
        "</g>"
        for stack, levels in enumerate((1, 2, 3))
        for level in range(levels)
    )
    return _frame(
        ORANGE,
        bars
        + coins
        + f"<circle cx='512' cy='92' r='52' fill='{SURFACE}' stroke='{ORANGE}' stroke-width='4'/>"
        + f"<text x='512' y='112' text-anchor='middle' font-family='ui-monospace,monospace' "
        f"font-size='58' font-weight='700' fill='{ORANGE}'>$</text>"
        + f"<path d='M120 258H560' stroke='{INK}' stroke-width='3' stroke-linecap='round' "
        "opacity='.55'/>",
    )


def _transit() -> str:
    """A road running to the horizon, with a bus and a traffic light."""
    return _frame(
        BLUE,
        f"<path d='M250 360 L330 150 H310 L150 360 Z' fill='{SURFACE}'/>"
        f"<path d='M390 360 L310 150 H330 L490 360 Z' fill='{SURFACE}'/>"
        f"<path d='M320 340V300M320 268V228M320 196V164' stroke='{ORANGE}' stroke-width='8' "
        "stroke-linecap='round' stroke-dasharray='18 14'/>"
        f"<rect x='250' y='150' width='140' height='5' fill='{MUTED}' opacity='.7'/>"
        # Bus, seen from the front.
        f"<g transform='translate(404 212)'>"
        f"<rect x='0' y='0' width='112' height='116' rx='14' fill='{BLUE}'/>"
        f"<rect x='12' y='16' width='88' height='46' rx='6' fill='{PAPER}' opacity='.85'/>"
        f"<rect x='12' y='74' width='88' height='24' rx='4' fill='{PAPER}' opacity='.45'/>"
        f"<circle cx='22' cy='118' r='11' fill='{INK}'/><circle cx='90' cy='118' r='11' fill='{INK}'/>"
        "</g>"
        # Traffic light.
        f"<g transform='translate(126 96)'>"
        f"<rect x='-16' y='-8' width='32' height='112' rx='10' fill='{SURFACE}'/>"
        f"<circle cx='0' cy='16' r='9' fill='#e05b5b'/>"
        f"<circle cx='0' cy='46' r='9' fill='{ORANGE}' opacity='.5'/>"
        f"<circle cx='0' cy='76' r='9' fill='{GREEN}' opacity='.9'/>"
        f"<rect x='-4' y='104' width='8' height='150' fill='{SURFACE}'/>"
        "</g>",
    )


def _utilities() -> str:
    """Water: a drop, ripples, and a pipe with a valve."""
    return _frame(
        TEAL,
        f"<path d='M320 62 C320 62 252 148 252 196 a68 68 0 0 0 136 0 C388 148 320 62 320 62 Z' "
        f"fill='{TEAL}' opacity='.22'/>"
        f"<path d='M320 86 C320 86 266 158 266 196 a54 54 0 0 0 108 0 C374 158 320 86 320 86 Z' "
        f"fill='{TEAL}' opacity='.45'/>"
        f"<path d='M298 190 a24 24 0 0 0 16 34' stroke='{INK}' stroke-width='7' "
        "stroke-linecap='round' fill='none' opacity='.8'/>"
        f"<path d='M180 286 H460' stroke='{MUTED}' stroke-width='18' stroke-linecap='round'/>"
        f"<path d='M180 264 H460' stroke='{SURFACE}' stroke-width='9' stroke-linecap='round'/>"
        f"<circle cx='320' cy='286' r='27' fill='{SURFACE}' stroke='{TEAL}' stroke-width='5'/>"
        f"<path d='M320 272V300M306 286H334' stroke='{TEAL}' stroke-width='5' stroke-linecap='round'/>"
        + "".join(
            f"<path d='M{132 + row * 18} {318 + row * 8} q16 -10 32 0 t32 0' stroke='{TEAL}' "
            "stroke-width='4' fill='none' opacity='.5'/>"
            for row in range(2)
        )
        + f"<path d='M470 318 q16 -10 32 0 t32 0' stroke='{TEAL}' stroke-width='4' fill='none' "
        "opacity='.5'/>",
    )


def _people(x: int, scale: float, colour: str) -> str:
    """One seated speaker: head, shoulders and a microphone."""
    return (
        f"<g transform='translate({x} 0) scale({scale})'>"
        f"<circle cx='0' cy='208' r='30' fill='{colour}'/>"
        f"<path d='M-58 360 C-58 292 -28 258 0 258 C28 258 58 292 58 360 Z' fill='{colour}' "
        "opacity='.8'/>"
        f"<path d='M56 286 l34 -40' stroke='{INK}' stroke-width='6' stroke-linecap='round'/>"
        f"<rect x='88' y='226' width='12' height='26' rx='6' fill='{INK}'/>"
        "</g>"
    )


def _public_comment() -> str:
    """People at microphones, with speech bubbles."""
    return _frame(
        PURPLE,
        _people(206, 0.86, MUTED)
        + _people(320, 1.0, PURPLE)
        + _people(434, 0.86, MUTED)
        + f"<g transform='translate(112 72)'><rect width='146' height='84' rx='18' fill='{SURFACE}' "
        f"stroke='{PURPLE}' stroke-width='3'/><path d='M34 84 l-4 26 30 -26 Z' fill='{SURFACE}' "
        f"stroke='{PURPLE}' stroke-width='3'/>"
        f"<path d='M24 32H120M24 52H96' stroke='{INK}' stroke-width='6' stroke-linecap='round' "
        "opacity='.7'/></g>"
        + f"<g transform='translate(396 48)'><rect width='130' height='74' rx='18' fill='{SURFACE}' "
        f"stroke='{BLUE}' stroke-width='3'/><path d='M96 74 l4 24 -28 -24 Z' fill='{SURFACE}' "
        f"stroke='{BLUE}' stroke-width='3'/>"
        f"<path d='M24 28H106M24 48H82' stroke='{INK}' stroke-width='6' stroke-linecap='round' "
        "opacity='.7'/></g>",
    )


def _government() -> str:
    """A lectern, a ballot box and a check: a decision being made."""
    return _frame(
        BLUE,
        f"<rect x='150' y='196' width='196' height='126' rx='12' fill='{SURFACE}' "
        f"stroke='{BLUE}' stroke-width='3'/>"
        f"<path d='M206 196 l18 -44 h48 l18 44' fill='{SURFACE}' stroke='{BLUE}' stroke-width='3'/>"
        f"<path d='M232 152 l-30 44' stroke='{MUTED}' stroke-width='7' stroke-linecap='round'/>"
        f"<path d='M254 152 l-10 44' stroke='{MUTED}' stroke-width='7' stroke-linecap='round'/>"
        f"<circle cx='248' cy='120' r='9' fill='{INK}'/>"
        f"<path d='M248 129v18' stroke='{INK}' stroke-width='6' stroke-linecap='round'/>"
        f"<rect x='398' y='212' width='128' height='110' rx='12' fill='{SURFACE}' "
        f"stroke='{BLUE}' stroke-width='3'/>"
        f"<rect x='420' y='232' width='84' height='12' rx='6' fill='{PAPER}'/>"
        f"<path d='M446 262 l24 26 40 -54' stroke='{GREEN}' stroke-width='10' fill='none' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        + "".join(
            f"<path d='M{118 + index * 40} 318h24' stroke='{MUTED}' stroke-width='6' "
            "stroke-linecap='round' opacity='.5'/>"
            for index in range(11)
        ),
    )


def _land_use() -> str:
    """A town skyline over a parcel map."""
    return _frame(
        ORANGE,
        # Parcel map.
        f"<g stroke='{ORANGE}' stroke-width='2' opacity='.35' fill='none'>"
        "<path d='M96 218 l112 42 132 -70 120 52M96 218 v92 h364M208 260 v50M340 190 v120M460 242 v68'/>"
        "</g>"
        # Buildings.
        f"<rect x='150' y='150' width='72' height='92' rx='6' fill='{MUTED}'/>"
        f"<rect x='236' y='104' width='86' height='138' rx='6' fill='{ORANGE}' opacity='.85'/>"
        f"<rect x='336' y='136' width='62' height='106' rx='6' fill='{BLUE}' opacity='.85'/>"
        f"<rect x='410' y='170' width='84' height='72' rx='6' fill='{SURFACE}' stroke='{MUTED}' "
        "stroke-width='3'/>"
        + "".join(
            f"<rect x='{250 + col * 22}' y='{122 + row * 26}' width='10' height='12' rx='2' "
            f"fill='{PAPER}' opacity='.75'/>"
            for row in range(3)
            for col in range(3)
        )
        + "".join(
            f"<rect x='{350 + col * 18}' y='{152 + row * 24}' width='8' height='10' rx='2' "
            f"fill='{PAPER}' opacity='.7'/>"
            for row in range(3)
            for col in range(2)
        )
        + f"<path d='M116 318H524' stroke='{INK}' stroke-width='3' stroke-linecap='round' "
        "opacity='.5'/>",
    )


def _parks() -> str:
    """Hills, trees, a sun and a trail."""
    tree = (
        f"<g transform='translate({0} 0)'>"
        f"<rect x='-5' y='238' width='10' height='54' fill='#8a6a4a'/>"
        f"<path d='M0 150 L38 232 H-38 Z' fill='{GREEN}' opacity='.85'/>"
        f"<path d='M0 188 L32 250 H-32 Z' fill='{GREEN}'/>"
        "</g>"
    )
    return _frame(
        GREEN,
        f"<circle cx='516' cy='92' r='38' fill='{ORANGE}' opacity='.9'/>"
        f"<path d='M0 300 Q150 220 320 292 T640 278 V360 H0 Z' fill='{SURFACE}'/>"
        f"<path d='M0 336 Q180 268 360 330 T640 318 V360 H0 Z' fill='{GREEN}' opacity='.32'/>"
        f"<path d='M250 360 q30 -46 92 -52 q68 -6 106 -54' stroke='{ORANGE}' stroke-width='7' "
        "fill='none' stroke-linecap='round' stroke-dasharray='2 16'/>"
        + tree.replace("translate(0 0)", "translate(148 20)")
        + tree.replace("translate(0 0)", "translate(196 46) scale(.8)")
        + tree.replace("translate(0 0)", "translate(96 52) scale(.7)")
        + f"<path d='M430 300 l14 -24 M438 300 l-6 -30' stroke='{INK}' stroke-width='4' "
        "stroke-linecap='round' opacity='.65'/>",
    )


def _public_safety() -> str:
    """A shield, a beacon and its light."""
    return _frame(
        BLUE,
        f"<path d='M320 44 L430 84 V190 C430 258 382 302 320 326 C258 302 210 258 210 190 "
        f"V84 Z' fill='{SURFACE}' stroke='{BLUE}' stroke-width='5'/>"
        f"<path d='M320 74 L400 104 V190 C400 240 366 274 320 294 C274 274 240 240 240 190 "
        f"V104 Z' fill='{BLUE}' opacity='.22'/>"
        f"<path d='M320 128 l20 42 46 6 -34 32 9 46 -41 -23 -41 23 9 -46 -34 -32 46 -6 Z' "
        f"fill='{ORANGE}'/>"
        f"<circle cx='520' cy='112' r='16' fill='#e05b5b'/>"
        f"<path d='M520 128v40M520 168h-56' stroke='{MUTED}' stroke-width='6' "
        "stroke-linecap='round' opacity='.7'/>"
        f"<path d='M556 74 a52 52 0 0 1 0 74M584 48 a88 88 0 0 1 0 126' stroke='{ORANGE}' "
        "stroke-width='6' fill='none' stroke-linecap='round' opacity='.55'/>"
        f"<path d='M110 318h94M110 292h58' stroke='{MUTED}' stroke-width='6' "
        "stroke-linecap='round' opacity='.5'/>",
    )


def _housing() -> str:
    """A row of homes and an apartment block."""
    house = (
        "<g transform='translate(0 0)'>"
        f"<path d='M-58 214 L0 170 L58 214 Z' fill='{ORANGE}'/>"
        f"<rect x='-46' y='212' width='92' height='78' rx='5' fill='{SURFACE}' "
        f"stroke='{ORANGE}' stroke-width='3'/>"
        f"<rect x='-16' y='244' width='32' height='46' rx='4' fill='{ORANGE}' opacity='.7'/>"
        "</g>"
    )
    return _frame(
        ORANGE,
        f"<rect x='356' y='126' width='118' height='164' rx='8' fill='{SURFACE}' "
        f"stroke='{PURPLE}' stroke-width='3'/>"
        + "".join(
            f"<rect x='{374 + col * 30}' y='{148 + row * 34}' width='16' height='18' rx='3' "
            f"fill='{BLUE}' opacity='{0.45 + 0.15 * ((row + col) % 3)}'/>"
            for row in range(4)
            for col in range(3)
        )
        + house.replace("translate(0 0)", "translate(166 62)")
        + house.replace("translate(0 0)", "translate(276 92) scale(.78)")
        + f"<path d='M84 318H560' stroke='{INK}' stroke-width='4' stroke-linecap='round' "
        "opacity='.45'/>",
    )


def _civic() -> str:
    """The fallback: the cast mark, a speaker at the dais sending out three arcs."""
    return _frame(
        BLUE,
        f"<g stroke='{BLUE}' fill='none' stroke-width='9' stroke-linecap='round'>"
        "<path d='M250 226 a38 38 0 0 1 38 -38'/>"
        "<path d='M250 226 a72 72 0 0 1 72 -72' opacity='.75'/>"
        "<path d='M250 226 a106 106 0 0 1 106 -106' opacity='.5'/>"
        "</g>"
        f"<circle cx='250' cy='226' r='17' fill='{INK}'/>"
        f"<rect x='158' y='244' width='184' height='14' rx='7' fill='{INK}' opacity='.85'/>"
        f"<rect x='226' y='258' width='48' height='70' fill='{INK}' opacity='.55'/>"
        f"<circle cx='486' cy='150' r='28' fill='{SURFACE}' stroke='{BLUE}' stroke-width='4'/>"
        f"<path d='M472 150 l10 10 20 -22' stroke='{GREEN}' stroke-width='6' fill='none' "
        "stroke-linecap='round' stroke-linejoin='round'/>",
    )


SCENES: dict[str, Callable[[], str]] = {
    "budget": _budget,
    "transit": _transit,
    "utilities": _utilities,
    "public-comment": _public_comment,
    "government": _government,
    "land-use": _land_use,
    "parks": _parks,
    "public-safety": _public_safety,
    "housing": _housing,
    FALLBACK: _civic,
}


def graphic_slug(topic_slug: str) -> str:
    """The illustration for a topic; the civic fallback for any topic without one."""
    return topic_slug if topic_slug in SCENES else FALLBACK


def asset_path(slug: str) -> str:
    return f"{GRAPHIC_DIR}/{graphic_slug(slug)}.svg"


def slugs() -> tuple[str, ...]:
    return tuple(sorted(SCENES))


def svg(slug: str) -> str:
    """The SVG file for a topic slug (or the fallback), as text."""
    return SCENES[graphic_slug(slug)]()
