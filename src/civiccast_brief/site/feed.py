"""The podcast feed: RSS 2.0 with the iTunes and Podcasting 2.0 (podcast namespace) tags.

Sources (see the NOTICE comment in site/__init__.py):
- civiccast-native civiccast/podcast/rss.py (Apache-2.0): the RSS 2.0 skeleton, the enclosure and
  item layout, and the rule that only episodes with hosted audio are listed.
- Craft kit app/src/lib/feeds.mjs: channel layout, itunes:duration in seconds, the podcast:guid
  rule (UUID v5 of the feed address without its scheme, in the Podcast Index namespace), and
  the RFC 822 date rule.
Elements added here: content:encoded, podcast:chapters, podcast:transcript and per-item
itunes:explicit and itunes:episodeType. The Apple Podcasts and Spotify rules the feed keeps are
listed in site/directories.py; the city settings are checked against them when they load.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape

from civiccast_brief.site.directories import EPISODE_DESCRIPTION_MAX_CHARS
from civiccast_brief.site.model import City, Episode
from civiccast_brief.site.timecode import clock
from civiccast_brief.site.urls import Urls, timestamp_link

PODCAST_GUID_NAMESPACE = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")
FEED_PATH = "/feed.xml"


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _cdata(html: str) -> str:
    return "<![CDATA[" + html.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def podcast_guid(feed_url: str) -> str:
    """Podcast Index podcast:guid: UUID v5 of the feed address with the scheme and trailing slashes removed."""
    without_scheme = feed_url.split("://", 1)[-1].rstrip("/")
    return str(uuid.uuid5(PODCAST_GUID_NAMESPACE, without_scheme))


def _rfc822(moment: datetime) -> str:
    return format_datetime(moment)


def _item_html(city: City, urls: Urls, episode: Episode) -> str:
    page = urls.absolute(urls.page_path("episodes", episode.id))
    parts = [f"<p>{escape(episode.dek)}</p>"] if episode.dek else []
    parts.append(f"<p>{escape(city.disclaimer)}</p>")
    if episode.claims:
        # Apple allows only p, ol, ul, li and a in episode notes, so no heading tag.
        parts.append("<p>Recap:</p>")
    for claim in episode.claims:
        cite = ""
        if episode.video_url:
            link = timestamp_link(episode.video_url, claim.seconds)
            if link:
                cite = f' (<a href="{_attr(link)}">video at {clock(claim.seconds)}</a>)'
        parts.append(f"<p>{escape(claim.text)}{cite}</p>")
    parts.append(f'<p><a href="{_attr(page)}">Episode page</a></p>')
    return "".join(parts)


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _description(city: City, episode: Episode) -> str:
    lead = episode.dek or episode.display_headline
    text = f"{_sentence(lead)} {city.disclaimer}"
    if len(text) > EPISODE_DESCRIPTION_MAX_CHARS:
        text = text[: EPISODE_DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
    return text


def build_feed(city: City, urls: Urls, episodes: tuple[Episode, ...], now: datetime) -> str:
    """Return the feed XML. ``episodes`` must already be approved and newest first."""
    feed_url = urls.absolute(FEED_PATH)
    podcast = city.podcast
    site_root = urls.absolute("/")
    artwork = urls.absolute(artwork_path(city))

    image_tag = (
        f"<image><url>{escape(artwork)}</url><title>{escape(podcast.name)}</title>"
        f"<link>{escape(site_root)}</link></image>"
    )
    owner = (
        f"<itunes:name>{escape(podcast.owner_name)}</itunes:name>"
        f"<itunes:email>{escape(podcast.owner_email)}</itunes:email>"
    )
    channel = [
        f"<title>{escape(podcast.name)}</title>",
        f"<link>{escape(site_root)}</link>",
        f'<atom:link href="{_attr(feed_url)}" rel="self" type="application/rss+xml"/>',
        f"<description>{escape(podcast.description or city.tagline)}</description>",
        f"<language>{escape(podcast.language)}</language>",
        f"<lastBuildDate>{_rfc822(now.astimezone(UTC))}</lastBuildDate>",
        f"<itunes:author>{escape(podcast.author)}</itunes:author>",
        f"<itunes:owner>{owner}</itunes:owner>",
        f'<itunes:image href="{_attr(artwork)}"/>',
        image_tag,
        _category(podcast.category, podcast.subcategory),
        f"<itunes:explicit>{'true' if podcast.explicit else 'false'}</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
        "<podcast:locked>no</podcast:locked>",
        f"<podcast:guid>{podcast_guid(feed_url)}</podcast:guid>",
    ]

    items: list[str] = []
    for episode in episodes:
        page = urls.absolute(urls.page_path("episodes", episode.id))
        audio = urls.resolve(episode.audio.url)
        item = [
            f"<title>{escape(episode.display_headline)}</title>",
            f"<link>{escape(page)}</link>",
            f'<guid isPermaLink="true">{escape(page)}</guid>',
            f"<pubDate>{_rfc822(episode.published)}</pubDate>",
            f"<description>{escape(_description(city, episode))}</description>",
            f"<content:encoded>{_cdata(_item_html(city, urls, episode))}</content:encoded>",
            f'<enclosure url="{_attr(audio)}" length="{episode.audio.bytes}" type="{_attr(episode.audio.type)}"/>',
            f"<itunes:duration>{round(episode.audio.duration_s)}</itunes:duration>",
            "<itunes:episodeType>full</itunes:episodeType>",
            f"<itunes:explicit>{'true' if podcast.explicit else 'false'}</itunes:explicit>",
        ]
        if episode.chapters_url:
            item.append(
                f'<podcast:chapters url="{_attr(urls.resolve(episode.chapters_url))}" '
                'type="application/json+chapters"/>'
            )
        if episode.vtt_url:
            item.append(
                f'<podcast:transcript url="{_attr(urls.resolve(episode.vtt_url))}" '
                f'type="text/vtt" language="{_attr(podcast.language.split("-")[0])}"/>'
            )
        items.append("<item>\n" + "\n".join(f"  {line}" for line in item) + "\n</item>")

    body = "\n".join(f"  {line}" for line in channel)
    item_xml = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0" '
        'xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        "<channel>\n"
        f"{body}\n" + (f"{item_xml}\n" if items else "") + "</channel>\n</rss>\n"
    )


def _category(category: str, subcategory: str) -> str:
    """itunes:category, with the subcategory nested inside it when there is one."""
    if not subcategory:
        return f'<itunes:category text="{_attr(category)}"/>'
    return (
        f'<itunes:category text="{_attr(category)}">'
        f'<itunes:category text="{_attr(subcategory)}"/></itunes:category>'
    )


def artwork_path(city: City) -> str:
    """Site path of the podcast artwork file for a city."""
    suffix = city.artwork_source.suffix.lower() if city.artwork_source else ".png"
    if suffix == ".jpeg":
        suffix = ".jpg"
    return f"/assets/podcast-art-{city.slug}{suffix}"
