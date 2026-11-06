"""Page list for one build: the context each template gets, and the path each page is written to."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from civiccast_brief.site import seo
from civiccast_brief.site.feed import FEED_PATH
from civiccast_brief.site.model import City, Episode, SiteInput
from civiccast_brief.site.render import date_long, file_size
from civiccast_brief.site.timecode import clock, timecode
from civiccast_brief.site.urls import Urls, timestamp_link

PAGE_HOW = "/how-this-is-made/"
PAGE_ABOUT = "/about/"
PAGE_CORRECTIONS = "/corrections/"
PAGE_SEARCH = "/search/"
SEARCH_INDEX_PATH = "/search-index.json"
SITEMAP_PATH = "/sitemap.xml"
ROBOTS_PATH = "/robots.txt"


@dataclass(frozen=True)
class PageSpec:
    path: str
    template: str
    context: dict[str, Any]
    scripts: tuple[str, ...] = field(default_factory=tuple)


def episode_path(urls: Urls, episode: Episode, *parts: str) -> str:
    return urls.page_path("episodes", episode.id, *parts)


def _description(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _base(city: City, urls: Urls, *, path: str, title: str, description: str) -> dict[str, Any]:
    return {
        "city": city,
        "page": {
            "title": title,
            "description": _description(description),
            "canonical": urls.absolute(path),
            "og_type": "website",
            "og_image": urls.absolute(seo.share_image_path(city)),
            "json_ld": [],
            "nav": "",
            "published": None,
        },
        "feed_href": urls.href(FEED_PATH),
        "feed_url": urls.absolute(FEED_PATH),
    }


def _episode_view(city: City, urls: Urls, episode: Episode) -> dict[str, Any]:
    audio_url = urls.resolve(episode.audio.url)
    video = episode.video_url
    claims = []
    for claim in episode.claims:
        claims.append(
            {
                "id": claim.id,
                "topic": claim.topic,
                "text": claim.text,
                "quote": claim.quote,
                "timestamp": timecode(claim.seconds),
                "link": timestamp_link(video, claim.seconds),
            }
        )
    chapters = [
        {
            "title": chapter.title,
            "clock": clock(chapter.start),
            "start": int(chapter.start),
            "href": f"{audio_url}#t={int(chapter.start)}",
        }
        for chapter in episode.chapters
    ]
    script = [
        {"speaker": city.hosts.get(line.speaker, line.speaker), "text": line.text}
        for line in episode.script
    ]
    return {
        "episode": episode,
        "headline": episode.display_headline,
        "claims": claims,
        "chapters": chapters,
        "script": script,
        "audio": {
            "url": audio_url,
            "length": clock(episode.audio.duration_s),
            "duration_s": round(episode.audio.duration_s, 2),
            "size": file_size(episode.audio.bytes),
        },
        "video_url": video,
        "chapters_url": urls.resolve(episode.chapters_url) if episode.chapters_url else None,
        "vtt_url": urls.resolve(episode.vtt_url) if episode.vtt_url else None,
        "transcript_href": (
            urls.href(episode_path(urls, episode, "transcript"))
            if episode.transcript is not None
            else None
        ),
        "meeting_date": date_long(episode.meeting_date),
        "published_date": date_long(episode.published),
        "corrections": [
            {"date": date_long(item.date), "note": item.note} for item in episode.corrections
        ],
    }


def _card(urls: Urls, episode: Episode) -> dict[str, Any]:
    return {
        "headline": episode.display_headline,
        "href": urls.href(episode_path(urls, episode)),
        "board": episode.board,
        "meeting_date": date_long(episode.meeting_date),
        "published_date": date_long(episode.published),
        "dek": episode.dek,
        "length": clock(episode.audio.duration_s),
        "sample": episode.sample,
        "claims": len(episode.claims),
    }


def build_pages(site: SiteInput, urls: Urls, now: datetime) -> tuple[list[PageSpec], list[str]]:
    """Return every HTML page and the sitemap paths for the site.

    The second value is the list of page paths that the sitemap lists.
    """
    city = site.city
    episodes = site.episodes
    pages: list[PageSpec] = []

    home = _base(
        city,
        urls,
        path="/",
        title=city.site_title,
        description=f"{city.tagline} Recaps, audio and transcripts of {city.name} meetings.",
    )
    home["page"]["nav"] = "home"
    home["page"]["json_ld"] = [seo.script_block(seo.home_podcast_series(city, urls))]
    home["latest"] = _card(urls, episodes[0]) if episodes else None
    home["others"] = [_card(urls, item) for item in episodes[1:]]
    pages.append(PageSpec("/", "home.html", home))

    for episode in episodes:
        path = episode_path(urls, episode)
        dek = episode.dek or (episode.claims[0].text if episode.claims else city.tagline)
        context = _base(
            city,
            urls,
            path=path,
            title=f"{episode.display_headline} | {city.site_title}",
            description=dek,
        )
        context["page"]["og_type"] = "article"
        context["page"]["published"] = episode.published.isoformat()
        context["page"]["nav"] = "episode"
        context["page"]["json_ld"] = [
            seo.script_block(seo.episode_news_article(city, urls, episode)),
            seo.script_block(seo.episode_podcast_episode(city, urls, episode)),
        ]
        context.update(_episode_view(city, urls, episode))
        context["tags"] = list(episode.tags)
        context["dek"] = episode.dek
        pages.append(PageSpec(path, "episode.html", context, ("/assets/player.js",)))

        if episode.transcript is not None:
            lines = [
                {
                    "timestamp": timecode(line.seconds),
                    "link": timestamp_link(episode.video_url, line.seconds),
                    "speaker": ("Unknown speaker" if line.speaker == "unknown" else line.speaker),
                    "text": line.text,
                }
                for line in episode.transcript
            ]
            transcript = _base(
                city,
                urls,
                path=episode_path(urls, episode, "transcript"),
                title=f"Transcript: {episode.meeting_title}, {date_long(episode.meeting_date)} | {city.site_title}",
                description=f"Speaker-labelled transcript of the {episode.meeting_title} on {date_long(episode.meeting_date)}, with timestamps linked to the meeting video.",
            )
            transcript["page"]["nav"] = "episode"
            transcript["episode"] = {
                "id": episode.id,
                "headline": episode.display_headline,
                "href": urls.href(episode_path(urls, episode)),
                "meeting_title": episode.meeting_title,
                "meeting_date": date_long(episode.meeting_date),
                "video_url": episode.video_url,
            }
            transcript["lines"] = lines
            pages.append(
                PageSpec(episode_path(urls, episode, "transcript"), "transcript.html", transcript)
            )

    search = _base(
        city,
        urls,
        path=PAGE_SEARCH,
        title=f"Search | {city.site_title}",
        description="Search every episode's recap, title and topics.",
    )
    search["page"]["nav"] = "search"
    search["index_href"] = urls.href(SEARCH_INDEX_PATH)
    pages.append(PageSpec(PAGE_SEARCH, "search.html", search, ("/assets/search.js",)))

    corrections_items = [
        {
            "date": date_long(item.date),
            "sort": item.date.isoformat(),
            "headline": episode.display_headline,
            "href": urls.href(episode_path(urls, episode)),
            "note": item.note,
        }
        for episode in episodes
        for item in episode.corrections
    ]
    corrections_items.sort(key=lambda row: row["sort"], reverse=True)
    corrections = _base(
        city,
        urls,
        path=PAGE_CORRECTIONS,
        title=f"Corrections | {city.site_title}",
        description="Every correction made to an episode, newest first, with the note that explains it.",
    )
    corrections["page"]["nav"] = "corrections"
    corrections["items"] = corrections_items
    pages.append(PageSpec(PAGE_CORRECTIONS, "corrections.html", corrections))

    how = _base(
        city,
        urls,
        path=PAGE_HOW,
        title=f"How this is made | {city.site_title}",
        description="How each episode is made, which parts are AI-written and which steps check the recap.",
    )
    how["page"]["nav"] = "how"
    how["disclaimer"] = city.disclaimer
    pages.append(PageSpec(PAGE_HOW, "how.html", how))

    about = _base(
        city,
        urls,
        path=PAGE_ABOUT,
        title=f"About | {city.site_title}",
        description=f"What {city.site_title} is, who publishes it, and what it does not do.",
    )
    about["page"]["nav"] = "about"
    about["paragraphs"] = list(city.about)
    pages.append(PageSpec(PAGE_ABOUT, "about.html", about))

    sitemap_paths = [page.path for page in pages]
    return pages, sitemap_paths
