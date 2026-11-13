"""Search metadata: JSON-LD blocks, the sitemap, robots.txt and the client-side search index."""

from __future__ import annotations

import json
from xml.sax.saxutils import escape

from civiccast_brief.site.feed import FEED_PATH, artwork_path
from civiccast_brief.site.model import City, Episode, Story
from civiccast_brief.site.timecode import iso_duration
from civiccast_brief.site.titles import split_meeting_label
from civiccast_brief.site.urls import Urls


def script_block(data: dict[str, object]) -> str:
    """A JSON-LD script tag. '<' is escaped so the JSON can never close the tag early."""
    text = json.dumps(data, ensure_ascii=False, indent=None).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{text}</script>'


def share_image_path(city: City) -> str:
    """Site path of the share image for a city. The build writes the file to this path."""
    suffix = city.share_image_source.suffix.lower() if city.share_image_source else ".png"
    if suffix == ".jpeg":
        suffix = ".jpg"
    return f"/assets/share-{city.slug}{suffix}"


def _publisher(city: City) -> dict[str, object]:
    return {"@type": "Organization", "name": city.publisher}


def episode_news_article(
    city: City, urls: Urls, episode: Episode | Story, section: str = "episodes"
) -> dict[str, object]:
    page = urls.absolute(urls.page_path(section, episode.id))
    published = episode.published.isoformat()
    return {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": episode.display_headline,
        "description": episode.dek or city.tagline,
        "datePublished": published,
        "dateModified": published,
        "url": page,
        "mainEntityOfPage": page,
        "image": urls.absolute(share_image_path(city)),
        "articleSection": episode.board,
        "keywords": ", ".join(episode.tags),
        "inLanguage": city.podcast.language,
        "isAccessibleForFree": True,
        "author": _publisher(city),
        "publisher": {
            "@type": "Organization",
            "name": city.site_title,
            "url": urls.absolute("/"),
        },
    }


def episode_podcast_episode(city: City, urls: Urls, episode: Episode) -> dict[str, object]:
    page = urls.absolute(urls.page_path("episodes", episode.id))
    return {
        "@context": "https://schema.org",
        "@type": "PodcastEpisode",
        "name": episode.display_headline,
        "description": episode.dek or city.disclaimer,
        "datePublished": episode.published.isoformat(),
        "url": page,
        "duration": iso_duration(episode.audio.duration_s),
        "associatedMedia": {
            "@type": "AudioObject",
            "contentUrl": urls.resolve(episode.audio.url),
            "encodingFormat": episode.audio.type,
            "duration": iso_duration(episode.audio.duration_s),
        },
        "partOfSeries": podcast_series(city, urls),
    }


def podcast_series(city: City, urls: Urls) -> dict[str, object]:
    return {
        "@type": "PodcastSeries",
        "name": city.podcast.name,
        "url": urls.absolute("/"),
        "webFeed": urls.absolute(FEED_PATH),
        "description": city.podcast.description or city.tagline,
        "image": urls.absolute(artwork_path(city)),
        "author": {"@type": "Organization", "name": city.podcast.author},
    }


def home_podcast_series(city: City, urls: Urls) -> dict[str, object]:
    return {"@context": "https://schema.org", **podcast_series(city, urls)}


def sitemap_xml(urls: Urls, paths: list[str]) -> str:
    entries = "\n".join(
        f"  <url><loc>{escape(urls.absolute(path))}</loc></url>" for path in sorted(set(paths))
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def robots_txt(urls: Urls) -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {urls.absolute('/sitemap.xml')}\n"


def search_index(
    urls: Urls, episodes: tuple[Episode, ...], stories: tuple[Story, ...] = ()
) -> list[dict[str, object]]:
    """One record per story for the search page, newest first. Recap text is included; the full
    transcript is not. ``audio`` says whether the story has an episode to listen to."""
    items: list[Episode | Story] = [*episodes, *stories]
    items.sort(key=lambda item: (item.meeting_date, item.published, item.id), reverse=True)
    records: list[dict[str, object]] = []
    for item in items:
        records.append(
            {
                "id": item.id,
                "title": item.display_headline,
                "url": urls.href(
                    urls.page_path("episodes" if isinstance(item, Episode) else "stories", item.id)
                ),
                "date": item.meeting_date.isoformat(),
                "board": item.board,
                "dek": item.display_dek,
                "tags": list(item.tags),
                "sample": item.sample,
                "audio": isinstance(item, Episode),
                "text": " ".join(split_meeting_label(claim.text)[1] for claim in item.claims),
            }
        )
    return records


def search_index_json(
    urls: Urls, episodes: tuple[Episode, ...], stories: tuple[Story, ...] = ()
) -> str:
    return json.dumps(search_index(urls, episodes, stories), ensure_ascii=False, indent=1) + "\n"
