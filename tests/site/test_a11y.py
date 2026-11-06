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


def _css_tokens() -> dict[str, str]:
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    root = css.split(":root", 1)[1].split("}", 1)[0]
    return {name: value for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", root)}


def _luminance(hex_colour: str) -> float:
    hex_colour = hex_colour.strip().lstrip("#")
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(c) for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _ratio(a: str, b: str) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_text_colours_meet_wcag_aa_on_every_background_they_sit_on() -> None:
    tokens = _css_tokens()
    backgrounds = {
        "white": tokens["--c-white"],
        "cream": tokens["--c-cream"],
        "mist": tokens["--c-mist"],
        "sample": tokens["--c-sample"],
        "ink": tokens["--c-ink-teal"],
    }
    text = {
        "navy": tokens["--c-navy"],
        "slate": tokens["--c-slate"],
        "teal": tokens["--c-teal"],
        "ink-teal": tokens["--c-ink-teal"],
        "error": tokens["--c-error"],
        "sample-ink": tokens["--c-sample-ink"],
    }
    on = {
        "white": ["navy", "slate", "teal", "ink-teal", "error"],
        "cream": ["navy", "slate", "teal", "ink-teal"],
        "mist": ["navy", "slate", "teal", "ink-teal"],
        "sample": ["sample-ink"],
        "ink": ["white"],
    }
    failures = []
    for background, names in on.items():
        for name in names:
            background_value = backgrounds[background]
            text_value = {**text, "white": tokens["--c-white"]}[name]
            ratio = _ratio(text_value, background_value)
            if ratio < 4.5:
                failures.append(f"{name} on {background}: {ratio:.2f}")
    assert failures == []
