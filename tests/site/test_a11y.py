"""Accessibility checks on the built pages: landmarks, one heading, labels, link text, and contrast."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from civiccast_brief.site.build import STATIC_DIR


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1 = 0
        self.landmarks: set[str] = set()
        self.ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.inputs: list[dict[str, str | None]] = []
        self.blank_links: list[str] = []
        self.images_without_alt = 0
        self._link_text: list[str] | None = None
        self._link_attrs: dict[str, str | None] = {}
        self.lang: str | None = None

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang")
        if tag == "h1":
            self.h1 += 1
        if tag in ("header", "nav", "main", "footer"):
            self.landmarks.add(tag)
        if tag == "img" and values.get("alt") is None:
            self.images_without_alt += 1
        if values.get("id"):
            self.ids.add(values["id"])  # type: ignore[arg-type]
        if tag == "label" and values.get("for"):
            self.labels_for.add(values["for"])  # type: ignore[arg-type]
        if tag == "input":
            self.inputs.append(values)
        if tag == "a" and values.get("target") == "_blank":
            self._link_text = []
            self._link_attrs = values

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if self._link_text is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag == "a" and self._link_text is not None:
            text = " ".join("".join(self._link_text).split())
            if "new tab" not in text and "opens" not in text:
                self.blank_links.append(text or str(self._link_attrs.get("href")))
            self._link_text = None


def _pages(out: Path) -> list[Path]:
    return sorted(out.rglob("*.html"))


def test_every_page_has_one_heading_landmarks_and_a_language(built: tuple[Path, object]) -> None:
    out, _ = built
    for file in _pages(out):
        page = _Page()
        page.feed(file.read_text(encoding="utf-8"))
        assert page.lang == "en", file
        assert page.h1 == 1, file
        assert {"header", "nav", "main", "footer"} <= page.landmarks, file
        assert "main" in page.ids or "main" in re.findall(
            r'id="main"', file.read_text(encoding="utf-8")
        ), file


def test_skip_link_points_at_a_real_target(built: tuple[Path, object]) -> None:
    out, _ = built
    for file in _pages(out):
        html = file.read_text(encoding="utf-8")
        assert 'href="#main"' in html, file
        assert 'id="main"' in html, file


def test_every_form_control_has_a_label(built: tuple[Path, object]) -> None:
    out, _ = built
    page = _Page()
    page.feed((out / "search/index.html").read_text(encoding="utf-8"))
    assert page.inputs, "search form has an input"
    for attrs in page.inputs:
        assert attrs.get("id") in page.labels_for, attrs


def test_links_that_open_a_new_tab_say_so(built: tuple[Path, object]) -> None:
    out, _ = built
    for file in _pages(out):
        page = _Page()
        page.feed(file.read_text(encoding="utf-8"))
        assert page.blank_links == [], file


def test_no_image_is_missing_alt_text(built: tuple[Path, object]) -> None:
    out, _ = built
    for file in _pages(out):
        page = _Page()
        page.feed(file.read_text(encoding="utf-8"))
        assert page.images_without_alt == 0, file


def _token_block(css: str, selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    body = match.group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def _themes() -> dict[str, dict[str, str]]:
    """Light tokens, and the dark tokens from both the media query and the data-theme switch."""
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    light = _token_block(css, ":root")
    media = _token_block(css, ':root:not([data-theme="light"])')
    forced = _token_block(css, ':root[data-theme="dark"]')
    assert media == forced, "the two dark-mode blocks must match"
    return {"light": light, "dark": {**light, **media}}


def _luminance(hex_colour: str) -> float:
    hex_colour = hex_colour.strip().lstrip("#")
    if len(hex_colour) == 3:
        hex_colour = "".join(c * 2 for c in hex_colour)
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(c) for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _ratio(a: str, b: str) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


#: Text colour on background colour, as the stylesheet pairs them.
PAIRS = [
    ("--ink", "--paper"),
    ("--ink", "--surface"),
    ("--ink", "--frame"),
    ("--ink", "--split-soft"),
    ("--muted", "--paper"),
    ("--muted", "--surface"),
    ("--muted", "--frame"),
    ("--docket", "--surface"),
    ("--docket", "--paper"),
    ("--docket", "--docket-soft"),
    ("--surface", "--docket"),
    ("--surface", "--ink"),
    ("--pass", "--pass-soft"),
    ("--split", "--split-soft"),
]


def test_text_colours_meet_wcag_aa_in_light_and_dark_mode() -> None:
    themes = _themes()
    assert themes["light"]["--paper"] == "#f6f7fa"
    assert themes["dark"]["--paper"] == "#0e1120"
    failures = []
    for theme, tokens in themes.items():
        for text, background in PAIRS:
            ratio = _ratio(tokens[text], tokens[background])
            if ratio < 4.5:
                failures.append(f"{theme}: {text} on {background}: {ratio:.2f}")
    assert failures == []


def test_static_assets_include_fonts_with_their_licences() -> None:
    fonts = STATIC_DIR / "fonts"
    files = {path.name for path in fonts.iterdir()}
    for family in ("bricolage-grotesque", "public-sans", "source-serif-4", "ibm-plex-mono"):
        assert f"LICENSE-{family}.txt" in files, family
        assert any(name.startswith(family) and name.endswith(".woff2") for name in files), family
