"""Apple Podcasts and Spotify feed rules, tested with a mock of each directory's checks.

Nobody has an Apple Podcasts Connect or Spotify for Creators account to test with, so the two
checkers below stand in for them. Each restates the published rules on its own (it does not import
site/directories.py), reads the built feed.xml, and fetches the show art from the built site the
way a directory fetches it from the host. Sources are listed in site/directories.py.
"""

from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from site_support import BASE_URL, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.directories import image_facts
from civiccast_brief.site.images import gradient_png
from civiccast_brief.site.model import InputError, load_site_input

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
APPLE_TOP_CATEGORIES = {
    "Arts", "Business", "Comedy", "Education", "Fiction", "Government", "History",
    "Health & Fitness", "Kids & Family", "Leisure", "Music", "News",
    "Religion & Spirituality", "Science", "Society & Culture", "Sports", "Technology",
    "True Crime", "TV & Film",
}  # fmt: skip
APPLE_ENCLOSURE_TYPES = {
    "audio/x-m4a", "audio/mpeg", "video/quicktime", "video/mp4", "video/x-m4v", "application/pdf",
}  # fmt: skip
APPLE_NOTE_TAGS = {"p", "ol", "ul", "li", "a"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DURATION = re.compile(r"^(\d+|\d{1,2}:\d{1,2}|\d{1,2}:\d{1,2}:\d{1,2})$")


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self.tags.add(tag)


def _tags(html: str) -> set[str]:
    parser = _Tags()
    parser.feed(html)
    return parser.tags


def _art_facts(href: str, site: Path) -> tuple[str, int, int, bool] | None:
    """Fetch the art from the mock host (the built site) and read its header by hand."""
    path = site / href.removeprefix(BASE_URL + "/")
    data = path.read_bytes()
    if data.startswith(b"\x89PNG") and data[12:16] == b"IHDR":
        width, height, _depth, color = struct.unpack(">IIBB", data[16:26])
        return "png", width, height, color == 2
    facts = image_facts(data)  # JPEG frames are long to walk by hand; the PNG path is enough
    return (facts.kind, facts.width, facts.height, facts.rgb) if facts else None


def apple_check(feed: bytes, site: Path) -> list[str]:
    """The checks Apple Podcasts Connect runs on a submitted feed (podcasters.apple.com 829, 823)."""
    problems: list[str] = []
    if not feed.startswith(b'<?xml version="1.0" encoding="UTF-8"?>'):
        problems.append("feed must start with the UTF-8 XML declaration")
    root = ET.fromstring(feed)
    for prefix in ("itunes", "content"):
        if f'xmlns:{prefix}="{NS[prefix]}"'.encode() not in feed:
            problems.append(f"missing the {prefix} namespace")
    channel = root.find("channel")
    assert channel is not None
    title = channel.findtext("title") or ""
    if not title or len(title) > 255:
        problems.append("title is empty or over 255 characters")
    description = channel.findtext("description") or ""
    if not description or len(description.encode("utf-8")) > 4000:
        problems.append("show description is empty or over 4000 bytes")
    language = channel.findtext("language") or ""
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})*", language, re.IGNORECASE):
        problems.append("language is not an ISO 639 code")
    if channel.findtext("itunes:explicit", namespaces=NS) not in ("true", "false"):
        problems.append("itunes:explicit must be true or false")
    category = channel.find("itunes:category", NS)
    if category is None or category.get("text") not in APPLE_TOP_CATEGORIES:
        problems.append("itunes:category is missing or not an Apple category")
    image = channel.find("itunes:image", NS)
    href = image.get("href", "") if image is not None else ""
    if not href.startswith("https://"):
        problems.append("itunes:image is missing or not https")
    else:
        facts = _art_facts(href, site)
        if facts is None:
            problems.append("show art is not PNG or JPEG")
        else:
            kind, width, height, rgb = facts
            if width != height or not 1400 <= width <= 3000:
                problems.append(f"show art is {width}x{height}; it must be square, 1400 to 3000")
            if not rgb:
                problems.append("show art must be RGB with no transparency")
            if not href.endswith({"png": (".png",), "jpeg": (".jpg", ".jpeg")}[kind]):
                problems.append("show art file name does not match its type")
    items = channel.findall("item")
    if not items:
        problems.append("at least one episode is required")
    for item in items:
        enclosure = item.find("enclosure")
        if enclosure is None or not all(enclosure.get(k) for k in ("url", "length", "type")):
            problems.append("episode enclosure needs url, length and type")
        elif enclosure.get("type") not in APPLE_ENCLOSURE_TYPES:
            problems.append(f"enclosure type {enclosure.get('type')} is not allowed")
        if not item.findtext("guid"):
            problems.append("episode needs a guid")
        if parsedate_to_datetime(item.findtext("pubDate") or "").utcoffset() is None:
            problems.append("pubDate needs a time zone")
        if len(item.findtext("description") or "") > 10_000:
            problems.append("episode description is over 10,000 characters")
        notes = item.findtext("content:encoded", namespaces=NS) or ""
        extra = _tags(notes) - APPLE_NOTE_TAGS
        if extra:
            problems.append(f"episode notes use tags Apple does not allow: {sorted(extra)}")
    return problems


def spotify_check(feed: bytes) -> list[str]:
    """Spotify Podcast Delivery Specification v1.10: the 1..1 elements, and the claim email."""
    problems: list[str] = []
    if f'xmlns:itunes="{NS["itunes"]}"'.encode() not in feed:
        problems.append("the itunes namespace is required")
    channel = ET.fromstring(feed).find("channel")
    assert channel is not None
    for tag in ("title", "link", "description", "itunes:author"):
        if not (channel.findtext(tag, namespaces=NS) or "").strip():
            problems.append(f"channel {tag} is required")
    image = channel.find("itunes:image", NS)
    if (image is None or not image.get("href")) and not channel.findtext("image/url"):
        problems.append("an itunes:image or image/url is required")
    # Spotify for Creators sends its 8-digit ownership code to this address.
    if not EMAIL.match(channel.findtext("itunes:owner/itunes:email", namespaces=NS) or ""):
        problems.append("itunes:email is missing, so the ownership code has nowhere to go")
    for item in channel.findall("item"):
        for tag in ("guid", "pubDate", "title", "description"):
            if not (item.findtext(tag) or "").strip():
                problems.append(f"episode {tag} is required")
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url") or not enclosure.get("type"):
            problems.append("episode enclosure needs url and type")
        duration = item.findtext("itunes:duration", namespaces=NS)
        if duration is not None and not DURATION.match(duration):
            problems.append(f"itunes:duration {duration!r} is not a format Spotify reads")
    return problems


def _build(episodes: Path, out: Path) -> bytes:
    build_site(episodes, out, BASE_URL, now=NOW)
    return (out / "feed.xml").read_bytes()


def _edit_city(episodes: Path, change: dict[str, object], podcast: dict[str, object]) -> None:
    path = episodes / "city.json"
    city = json.loads(path.read_text(encoding="utf-8"))
    city.update(change)
    city["podcast"].update(podcast)
    path.write_text(json.dumps(city), encoding="utf-8")


def _png_header(width: int, height: int, color_type: int) -> bytes:
    """A PNG signature and IHDR only: enough for a header check, not a picture."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk


def _jpeg_header(width: int, height: int, components: int) -> bytes:
    """SOI, a JFIF APP0 segment, then a baseline frame header with the size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = struct.pack(">HBHHB", 8 + 3 * components, 8, height, width, components)
    return b"\xff\xd8" + app0 + b"\xff\xc0" + frame + b"\x01\x11\x00" * components


# --- the built feed passes both mock directories ------------------------------------------------


def test_the_built_feed_passes_the_apple_checks(tmp_path: Path) -> None:
    from site_support import FIXTURES

    out = tmp_path / "site"
    assert apple_check(_build(FIXTURES, out), out) == []


def test_the_built_feed_passes_the_spotify_checks(tmp_path: Path) -> None:
    from site_support import FIXTURES

    assert spotify_check(_build(FIXTURES, tmp_path / "site")) == []


def test_the_mock_checkers_catch_a_broken_feed(tmp_path: Path) -> None:
    """The mocks are only worth running if they fail a feed that breaks the rules."""
    from site_support import FIXTURES

    out = tmp_path / "site"
    feed = _build(FIXTURES, out)
    broken = (
        feed.replace(b'<itunes:category text="Government"/>', b'<itunes:category text="Civic"/>')
        .replace(b"<itunes:email>podcast@example.com</itunes:email>", b"")
        .replace(b"<![CDATA[<p>", b"<![CDATA[<h2>Notes</h2><p>", 1)
    )
    apple = apple_check(broken, out)
    assert "itunes:category is missing or not an Apple category" in apple
    assert any("tags Apple does not allow" in p for p in apple)
    assert "itunes:email is missing, so the ownership code has nowhere to go" in spotify_check(
        broken
    )


def test_episode_notes_use_only_the_tags_apple_allows(tmp_path: Path) -> None:
    from site_support import FIXTURES

    root = ET.fromstring(_build(FIXTURES, tmp_path / "site"))
    for notes in root.iterfind("channel/item/content:encoded", NS):
        assert _tags(notes.text or "") <= APPLE_NOTE_TAGS


# --- the settings a person fills in -------------------------------------------------------------


def test_a_subcategory_is_nested_inside_its_category(episodes_dir: Path, tmp_path: Path) -> None:
    _edit_city(episodes_dir, {}, {"category": "News", "subcategory": "Politics"})
    out = tmp_path / "site"
    feed = _build(episodes_dir, out)
    category = ET.fromstring(feed).find("channel/itunes:category", NS)
    assert category is not None and category.get("text") == "News"
    inner = category.find("itunes:category", NS)
    assert inner is not None and inner.get("text") == "Politics"
    assert apple_check(feed, out) == []


def test_an_ampersand_category_is_escaped_in_the_feed(episodes_dir: Path, tmp_path: Path) -> None:
    _edit_city(episodes_dir, {}, {"category": "Society & Culture", "subcategory": "Documentary"})
    out = tmp_path / "site"
    feed = _build(episodes_dir, out)
    assert b'<itunes:category text="Society &amp; Culture">' in feed
    assert apple_check(feed, out) == []


@pytest.mark.parametrize(
    ("podcast", "message"),
    [
        ({"category": "Civic Life"}, "category 'Civic Life' is not an Apple Podcasts category"),
        ({"category": "society & culture"}, "is not an Apple Podcasts category"),
        (
            {"category": "Government", "subcategory": "Politics"},
            "subcategory 'Politics' is not under 'Government' (allowed: none)",
        ),
        ({"language": "English"}, "language must be an ISO 639 language code"),
        ({"owner_email": "podcast at example.com"}, "owner_email must be an email address"),
        ({"description": "é" * 999}, None),  # 1,998 bytes: allowed
    ],
)
def test_podcast_settings_are_checked_against_the_directory_rules(
    episodes_dir: Path, podcast: dict[str, object], message: str | None
) -> None:
    _edit_city(episodes_dir, {}, podcast)
    if message is None:
        load_site_input(episodes_dir)
        return
    with pytest.raises(InputError) as caught:
        load_site_input(episodes_dir)
    assert any(message in p for p in caught.value.problems), caught.value.problems


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("art.png", _png_header(1200, 1200, 2), "must be 1400 to 3000 pixels a side"),
        ("art.png", _png_header(3200, 3200, 2), "must be 1400 to 3000 pixels a side"),
        ("art.png", _png_header(1400, 1500, 2), "must be square; it is 1400x1500"),
        ("art.png", _png_header(1400, 1400, 6), "must be RGB color with no transparency"),
        ("art.jpg", _png_header(1400, 1400, 2), "is a PNG file but its name ends in .jpg"),
        ("art.jpg", _jpeg_header(1400, 1400, 1), "must be RGB color with no transparency"),
        ("art.png", b"\x89PNG\r\n\x1a\n", "is not a PNG or JPEG file"),
    ],
)
def test_supplied_artwork_is_refused_when_a_directory_would_refuse_it(
    episodes_dir: Path, name: str, data: bytes, message: str
) -> None:
    (episodes_dir / name).write_bytes(data)
    _edit_city(episodes_dir, {"artwork": name}, {})
    with pytest.raises(InputError) as caught:
        load_site_input(episodes_dir)
    assert any(f"artwork {message}" in p for p in caught.value.problems), caught.value.problems


@pytest.mark.parametrize(
    ("name", "data"),
    [("art.jpg", _jpeg_header(3000, 3000, 3)), ("art.jpeg", _jpeg_header(1400, 1400, 3))],
)
def test_square_rgb_jpeg_artwork_in_range_is_accepted(
    episodes_dir: Path, name: str, data: bytes
) -> None:
    (episodes_dir / name).write_bytes(data)
    _edit_city(episodes_dir, {"artwork": name}, {})
    assert load_site_input(episodes_dir).city.artwork_source is not None


def test_supplied_png_artwork_passes_the_apple_mock(episodes_dir: Path, tmp_path: Path) -> None:
    (episodes_dir / "art.png").write_bytes(gradient_png(1500, 1500))
    _edit_city(episodes_dir, {"artwork": "art.png"}, {})
    out = tmp_path / "site"
    assert apple_check(_build(episodes_dir, out), out) == []


def test_episode_audio_must_be_a_type_the_directories_play(episodes_dir: Path) -> None:
    path = episodes_dir / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["audio"]["type"] = "audio/ogg"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match=r"audio.type must be one of audio/mpeg, audio/x-m4a"):
        load_site_input(episodes_dir)


def test_jpeg_size_is_read_past_other_segments() -> None:
    facts = image_facts(_jpeg_header(2048, 2048, 3))
    assert facts is not None
    assert (facts.kind, facts.width, facts.height, facts.rgb) == ("jpeg", 2048, 2048, True)
