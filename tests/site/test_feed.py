"""Podcast feed: well-formed RSS 2.0 with the iTunes and Podcasting 2.0 tags a directory needs."""

from __future__ import annotations

import hashlib
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

from site_support import BASE_URL

from civiccast_brief.site.feed import podcast_guid

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _feed(out: Path) -> ET.Element:
    return ET.parse(out / "feed.xml").getroot()


def _craft_podcast_guid(feed_url: str) -> str:
    """The Craft kit's uuidv5 written out by hand (SHA-1 over namespace bytes plus name)."""
    namespace = bytes.fromhex("ead4c236bf5858c6a2c6a6b28d128cb6")
    name = feed_url.split("://", 1)[-1].rstrip("/").encode("utf-8")
    digest = bytearray(hashlib.sha1(namespace + name).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    h = digest.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def test_feed_is_well_formed_rss_2_0(built: tuple[Path, object]) -> None:
    out, _ = built
    root = _feed(out)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None


def test_channel_has_the_required_podcast_tags(built: tuple[Path, object]) -> None:
    out, _ = built
    channel = _feed(out).find("channel")
    assert channel is not None

    def text(path: str) -> str:
        element = channel.find(path, NS)
        assert element is not None, path
        return (element.text or "").strip()

    assert text("title") == "CivicCast Brief: Example City"
    assert text("link") == f"{BASE_URL}/"
    assert text("language") == "en-us"
    assert text("description")
    assert text("itunes:author") == "CivicCast Brief"
    assert text("itunes:owner/itunes:name") == "CivicCast Brief"
    assert text("itunes:owner/itunes:email") == "podcast@example.com"
    assert channel.find("itunes:image", NS).get("href").endswith("/assets/podcast-art-example.png")  # type: ignore[union-attr]
    assert text("image/url").endswith("/assets/podcast-art-example.png")
    assert channel.find("itunes:category", NS).get("text") == "Government"  # type: ignore[union-attr]
    assert text("itunes:explicit") == "false"
    assert text("itunes:type") == "episodic"
    assert text("podcast:locked") == "no"
    assert text("atom:link") == ""  # the self link carries its address in the href attribute
    self_link = channel.find("atom:link", NS)
    assert self_link is not None and self_link.get("rel") == "self"
    assert self_link.get("href") == f"{BASE_URL}/feed.xml"


def test_podcast_guid_matches_the_craft_rule(built: tuple[Path, object]) -> None:
    out, _ = built
    channel = _feed(out).find("channel")
    assert channel is not None
    guid = channel.find("podcast:guid", NS)
    assert guid is not None
    expected = _craft_podcast_guid(f"{BASE_URL}/feed.xml")
    assert guid.text == expected
    assert podcast_guid(f"{BASE_URL}/feed.xml") == expected
    assert uuid.UUID(expected).version == 5


def test_items_have_enclosure_length_duration_guid_date_and_text(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    channel = _feed(out).find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert len(items) == 2
    # Newest first: the Oct 20 meeting was published after the Oct 6 sample.
    first, second = items
    assert first.findtext("title") == "Council approves a test annexation and two ordinances"
    assert second.findtext("title") == "SAMPLE: Planning commission reviews a test zoning map"

    enclosure = first.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == "https://media.example.com/example-council.mp3"
    assert enclosure.get("length") == "603200"
    assert enclosure.get("type") == "audio/mpeg"
    assert first.findtext("itunes:duration", namespaces=NS) == "75"
    assert first.findtext("itunes:explicit", namespaces=NS) == "false"
    assert first.findtext("itunes:episodeType", namespaces=NS) == "full"

    guid = first.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "true"
    assert guid.text == f"{BASE_URL}/episodes/2026-10-20-example-council/"
    assert first.findtext("link") == guid.text

    date_value = parsedate_to_datetime(first.findtext("pubDate") or "")
    assert date_value.utcoffset().total_seconds() == -6 * 3600  # type: ignore[union-attr]

    encoded = first.findtext("content:encoded", namespaces=NS) or ""
    assert "Recap" in encoded
    assert "The council approved the annexation resolution 6-1" in encoded
    assert "t=5502s" in encoded  # citations point at the video, in the article text


def test_chapters_and_transcript_tags_are_present_where_hosted(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    channel = _feed(out).find("channel")
    assert channel is not None
    first, second = channel.findall("item")
    chapters = first.find("podcast:chapters", NS)
    assert chapters is not None
    assert chapters.get("url") == "https://media.example.com/example-council-chapters.json"
    assert chapters.get("type") == "application/json+chapters"
    transcript = first.find("podcast:transcript", NS)
    assert transcript is not None
    assert transcript.get("type") == "text/vtt"
    assert transcript.get("url") == "https://media.example.com/example-council.vtt"
    assert transcript.get("language") == "en"
    # The sample has neither file, so it carries neither tag.
    assert second.find("podcast:chapters", NS) is None
    assert second.find("podcast:transcript", NS) is None


def test_feed_escapes_text_and_keeps_cdata_safe(tmp_path: Path) -> None:
    from site_support import FIXTURES, NOW

    from civiccast_brief.site.build import build_site

    episodes = tmp_path / "episodes"
    import shutil

    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    data["recap"]["claims"][0]["text"] = "Quoted ]]> text & <b>markup</b>"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    root = _feed(out)  # still well-formed XML
    encoded = root.find("channel/item/content:encoded", NS)
    assert encoded is not None and encoded.text is not None
    assert "]]>" not in encoded.text.replace("]]]]><![CDATA[>", "")
    assert "markup" in encoded.text
