"""Page list for one build: the context each template gets, and the path each page is written to."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from civiccast_brief.site import seo
from civiccast_brief.site.feed import FEED_PATH, artwork_path
from civiccast_brief.site.model import Article, City, Episode, SiteInput
from civiccast_brief.site.render import date_long, file_size
from civiccast_brief.site.timecode import clock, timecode
from civiccast_brief.site.topics import TOPICS, slugify, topics_for
from civiccast_brief.site.urls import Urls, timestamp_link

PAGE_HOW = "/how-this-is-made/"
PAGE_ABOUT = "/about/"
PAGE_SEARCH = "/search/"
PAGE_PODCAST = "/podcast/"
PAGE_BOARDS = "/boards/"
PAGE_TOPICS = "/topics/"
PAGE_ARCHIVE = "/archive/"
SEARCH_INDEX_PATH = "/search-index.json"
SITEMAP_PATH = "/sitemap.xml"
ROBOTS_PATH = "/robots.txt"
FAVICON_SVG = "/assets/favicon.svg"
FAVICON_PNG = "/assets/favicon.png"

#: Cloudflare Web Analytics site token for civiccastbrief.org (a public value; it is in every page).
ANALYTICS_TOKEN = "584d3422251846a3a68db1030f40f942"
ANALYTICS_SCRIPT = "https://static.cloudflareinsights.com/beacon.min.js"
ANALYTICS_SCRIPT_ORIGIN = "https://static.cloudflareinsights.com"
ANALYTICS_CONNECT_ORIGIN = "https://cloudflareinsights.com"

REPUBLISH_NOTICE = (
    "Free to republish. Local outlets may reprint this story with credit to CivicCast Brief "
    "and a link to this page."
)

MONTHS_SHORT = (
    "Jan.",
    "Feb.",
    "March",
    "April",
    "May",
    "June",
    "July",
    "Aug.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
)
ALSO_COUNT = 3
MORE_COUNT = 3


@dataclass(frozen=True)
class PageSpec:
    path: str
    template: str
    context: dict[str, Any]
    scripts: tuple[str, ...] = field(default_factory=tuple)


def episode_path(urls: Urls, episode: Episode, *parts: str) -> str:
    return urls.page_path("episodes", episode.id, *parts)


def board_path(urls: Urls, board: str) -> str:
    return urls.page_path("boards", slugify(board))


def topic_path(urls: Urls, slug: str) -> str:
    return urls.page_path("topics", slug)


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_path(urls: Urls, key: str) -> str:
    return urls.page_path("archive", key)


def month_name(key: str) -> str:
    year, month = key.split("-")
    return date(int(year), int(month), 1).strftime("%B %Y")


def short_date(day: date) -> str:
    """Nov. 3"""
    return f"{MONTHS_SHORT[day.month - 1]} {day.day}"


def _description(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _base(city: City, urls: Urls, *, path: str, title: str, description: str) -> dict[str, Any]:
    signup = city.signup_url if city.email_signup else None
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
        "signup_url": signup,
        "form_action": _origin(signup) if signup else None,
        "analytics": {
            "token": ANALYTICS_TOKEN,
            "script": ANALYTICS_SCRIPT,
            "script_origin": ANALYTICS_SCRIPT_ORIGIN,
            "connect_origin": ANALYTICS_CONNECT_ORIGIN,
        },
        "favicon_svg": urls.href(FAVICON_SVG),
        "favicon_png": urls.href(FAVICON_PNG),
    }


_TALLY = re.compile(r"(\d+)\s*[-–]\s*(\d+)")


def vote_badge(result: str, tally: str | None) -> dict[str, str]:
    """A vote badge: "split" when anyone voted no, otherwise "pass"."""
    text = tally or result
    match = _TALLY.search(tally or "")
    split = bool(match and int(match.group(2)) > 0)
    lowered = result.lower()
    if any(word in lowered for word in ("fail", "denied", "rejected")):
        split = True
    label = f"{tally} · {result}" if tally else result
    return {"kind": "split" if split else "pass", "label": label, "short": text}


def _cite(video: str | None, seconds: float) -> dict[str, Any]:
    return {"timestamp": timecode(seconds), "link": timestamp_link(video, seconds)}


def _article_view(article: Article, video: str | None) -> dict[str, Any]:
    return {
        "headline": article.headline,
        "dek": article.dek,
        "uses_documents": any(p.pages for s in article.sections for p in s.paragraphs),
        "sections": [
            {
                "heading": section.heading,
                "paragraphs": [
                    {
                        "text": paragraph.text,
                        "cites": [_cite(video, cite.seconds) for cite in paragraph.cites],
                        "pages": [
                            {
                                "label": f"{page.kind} p. {page.page}",
                                "link": f"{page.url}#page={page.page}",
                            }
                            for page in paragraph.pages
                        ],
                    }
                    for paragraph in section.paragraphs
                ],
            }
            for section in article.sections
        ],
        "votes": [
            {
                "motion": vote.motion,
                **vote_badge(vote.result, vote.tally),
                **_cite(video, vote.seconds),
            }
            for vote in article.votes
        ],
        "quotes": [
            {"text": quote.text, "speaker": quote.speaker, **_cite(video, quote.seconds)}
            for quote in article.quotes
        ],
    }


def _topic_chips(urls: Urls, episode: Episode) -> list[dict[str, str]]:
    return [
        {"name": topic.short, "href": urls.href(topic_path(urls, topic.slug)), "slug": topic.slug}
        for topic in topics_for(episode)
    ]


def _episode_view(city: City, urls: Urls, episode: Episode) -> dict[str, Any]:
    audio_url = urls.resolve(episode.audio.url)
    video = episode.video_url
    claims = [
        {
            "id": claim.id,
            "topic": claim.topic,
            "text": claim.text,
            "quote": claim.quote,
            **_cite(video, claim.seconds),
        }
        for claim in episode.claims
    ]
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
        "dek": episode.display_dek,
        "article": _article_view(episode.article, video) if episode.article is not None else None,
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
        "board_href": urls.href(board_path(urls, episode.board)),
        "topics": _topic_chips(urls, episode),
        "tags": list(episode.tags),
        "republish": REPUBLISH_NOTICE,
    }


def _card(urls: Urls, episode: Episode) -> dict[str, Any]:
    """One episode as a list row or a lead story."""
    first_vote = None
    if episode.article is not None and episode.article.votes:
        vote = episode.article.votes[0]
        first_vote = vote_badge(vote.result, vote.tally)
    return {
        "id": episode.id,
        "headline": episode.display_headline,
        "href": urls.href(episode_path(urls, episode)),
        "board": episode.board,
        "board_href": urls.href(board_path(urls, episode.board)),
        "meeting_title": episode.meeting_name,
        "meeting_date": date_long(episode.meeting_date),
        "iso_date": episode.meeting_date.isoformat(),
        "short_date": short_date(episode.meeting_date),
        "day": f"{MONTHS_SHORT[episode.meeting_date.month - 1]} {episode.meeting_date.day:02d}",
        "published_date": date_long(episode.published),
        "dek": episode.display_dek,
        "length": clock(episode.audio.duration_s),
        "sample": episode.sample,
        "claims": len(episode.claims),
        "vote": first_vote,
        "month": month_key(episode.meeting_date),
    }


def _by_month(urls: Urls, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    months: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for card in cards:
        months.setdefault(card["month"], []).append(card)
    return [
        {
            "key": key,
            "name": month_name(key),
            "href": urls.href(month_path(urls, key)),
            "rows": rows,
        }
        for key, rows in months.items()
    ]


def _podcast_links(city: City, urls: Urls) -> list[dict[str, Any]]:
    links = [{"label": "RSS feed", "href": urls.href(FEED_PATH), "solid": True, "external": False}]
    if city.apple_podcasts_url:
        links.append(
            {
                "label": "Apple Podcasts",
                "href": city.apple_podcasts_url,
                "solid": False,
                "external": True,
            }
        )
    if city.spotify_url:
        links.append(
            {"label": "Spotify", "href": city.spotify_url, "solid": False, "external": True}
        )
    return links


def _list_page(
    city: City,
    urls: Urls,
    *,
    path: str,
    title: str,
    heading: str,
    kicker: str,
    intro: str,
    nav: str,
    cards: list[dict[str, Any]],
) -> PageSpec:
    context = _base(city, urls, path=path, title=f"{title} | {city.site_title}", description=intro)
    context["page"]["nav"] = nav
    context.update(
        {"heading": heading, "kicker": kicker, "intro": intro, "months": _by_month(urls, cards)}
    )
    return PageSpec(path, "list.html", context)


def build_pages(site: SiteInput, urls: Urls, now: datetime) -> tuple[list[PageSpec], list[str]]:
    """Return every HTML page and the sitemap paths for the site.

    The second value is the list of page paths that the sitemap lists.
    """
    city = site.city
    episodes = site.episodes
    pages: list[PageSpec] = []
    cards = [_card(urls, episode) for episode in episodes]
    card_by_id = {card["id"]: card for card in cards}

    # Topics with at least one episode, in the fixed order, with their episodes.
    topic_cards: dict[str, list[dict[str, Any]]] = {topic.slug: [] for topic in TOPICS}
    for episode in episodes:
        for topic in topics_for(episode):
            topic_cards[topic.slug].append(card_by_id[episode.id])
    used_topics = [
        {
            "slug": topic.slug,
            "name": topic.name,
            "short": topic.short,
            "href": urls.href(topic_path(urls, topic.slug)),
            "count": len(topic_cards[topic.slug]),
        }
        for topic in TOPICS
        if topic_cards[topic.slug]
    ]

    boards: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for card in sorted(cards, key=lambda c: c["board"]):
        boards.setdefault(card["board"], []).append(card)
    for rows in boards.values():
        rows.sort(key=lambda c: c["iso_date"], reverse=True)
    board_list = [
        {
            "name": name,
            "href": urls.href(board_path(urls, name)),
            "count": len(rows),
            "latest": rows[0],
        }
        for name, rows in boards.items()
    ]

    # Front page.
    home = _base(
        city,
        urls,
        path="/",
        title=city.site_title,
        description=f"{city.tagline} News stories, audio and transcripts of {city.name} meetings.",
    )
    home["page"]["nav"] = "meetings"
    home["page"]["json_ld"] = [seo.script_block(seo.home_podcast_series(city, urls))]
    home["lead"] = cards[0] if cards else None
    home["also"] = cards[1 : 1 + ALSO_COUNT]
    home["months"] = _by_month(urls, cards)
    home["chips"] = used_topics
    home["boards"] = board_list
    home["podcast_links"] = _podcast_links(city, urls)
    home["podcast_href"] = urls.href(PAGE_PODCAST)
    home["archive_href"] = urls.href(PAGE_ARCHIVE)
    home["republish"] = REPUBLISH_NOTICE
    pages.append(PageSpec("/", "home.html", home))

    for episode in episodes:
        path = episode_path(urls, episode)
        dek = episode.display_dek or (episode.claims[0].text if episode.claims else city.tagline)
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
        context["more"] = [card for card in boards[episode.board] if card["id"] != episode.id][
            :MORE_COUNT
        ]
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
                title=f"Transcript: {episode.meeting_name}, {date_long(episode.meeting_date)} | {city.site_title}",
                description=f"Speaker-labelled transcript of the {episode.meeting_name} on {date_long(episode.meeting_date)}, with timestamps linked to the meeting video.",
            )
            transcript["page"]["nav"] = "episode"
            transcript["episode"] = {
                "id": episode.id,
                "headline": episode.display_headline,
                "href": urls.href(episode_path(urls, episode)),
                "meeting_title": episode.meeting_name,
                "meeting_date": date_long(episode.meeting_date),
                "video_url": episode.video_url,
            }
            transcript["lines"] = lines
            pages.append(
                PageSpec(episode_path(urls, episode, "transcript"), "transcript.html", transcript)
            )

    # Boards.
    boards_index = _base(
        city,
        urls,
        path=PAGE_BOARDS,
        title=f"Boards | {city.site_title}",
        description=f"Every {city.name} board and commission this site covers, with its meetings.",
    )
    boards_index["page"]["nav"] = "boards"
    boards_index["items"] = [
        {
            "name": board["name"],
            "href": board["href"],
            "note": f"{board['count']} meeting{'s' if board['count'] != 1 else ''} · latest {board['latest']['meeting_date']}",
        }
        for board in board_list
    ]
    boards_index["heading"] = "Boards"
    boards_index["intro"] = (
        f"The {city.name} public bodies this site covers. Each page lists every meeting written up."
    )
    pages.append(PageSpec(PAGE_BOARDS, "index_list.html", boards_index))
    for name, rows in boards.items():
        pages.append(
            _list_page(
                city,
                urls,
                path=board_path(urls, name),
                title=name,
                heading=name,
                kicker="Board",
                intro=f"Every {name} meeting written up by {city.site_title}, newest first.",
                nav="boards",
                cards=rows,
            )
        )

    # Topics.
    topics_index = _base(
        city,
        urls,
        path=PAGE_TOPICS,
        title=f"Topics | {city.site_title}",
        description="Meetings sorted by subject: land use, housing, transit, utilities, budget and more.",
    )
    topics_index["page"]["nav"] = "topics"
    topics_index["items"] = [
        {
            "name": topic["name"],
            "href": topic["href"],
            "note": f"{topic['count']} meeting{'s' if topic['count'] != 1 else ''}",
        }
        for topic in used_topics
    ]
    topics_index["heading"] = "Topics"
    topics_index["intro"] = "Each meeting is filed under the subjects its recap covers."
    pages.append(PageSpec(PAGE_TOPICS, "index_list.html", topics_index))
    for topic in used_topics:
        pages.append(
            _list_page(
                city,
                urls,
                path=topic_path(urls, topic["slug"]),
                title=topic["name"],
                heading=topic["name"],
                kicker="Topic",
                intro=f"{city.name} meetings that covered {topic['name'].lower()}, newest first.",
                nav="topics",
                cards=topic_cards[topic["slug"]],
            )
        )

    # Monthly archive.
    months = _by_month(urls, cards)
    archive = _base(
        city,
        urls,
        path=PAGE_ARCHIVE,
        title=f"Archive | {city.site_title}",
        description=f"Every {city.name} meeting written up, by month.",
    )
    archive["page"]["nav"] = "archive"
    archive["items"] = [
        {
            "name": month["name"],
            "href": month["href"],
            "note": f"{len(month['rows'])} meeting{'s' if len(month['rows']) != 1 else ''}",
        }
        for month in months
    ]
    archive["heading"] = "Archive"
    archive["intro"] = "Every meeting written up, by the month it was held."
    pages.append(PageSpec(PAGE_ARCHIVE, "index_list.html", archive))
    for month in months:
        pages.append(
            _list_page(
                city,
                urls,
                path=month_path(urls, month["key"]),
                title=month["name"],
                heading=month["name"],
                kicker="Archive",
                intro=f"{city.name} meetings held in {month['name']}.",
                nav="archive",
                cards=month["rows"],
            )
        )

    # Podcast.
    podcast = _base(
        city,
        urls,
        path=PAGE_PODCAST,
        title=f"Podcast | {city.site_title}",
        description=city.podcast.description,
    )
    podcast["page"]["nav"] = "podcast"
    podcast["podcast_links"] = _podcast_links(city, urls)
    podcast["episodes"] = cards
    podcast["artwork_href"] = urls.href(artwork_path(city))
    pages.append(PageSpec(PAGE_PODCAST, "podcast.html", podcast))

    search = _base(
        city,
        urls,
        path=PAGE_SEARCH,
        title=f"Search | {city.site_title}",
        description="Search every meeting's story, title and topics.",
    )
    search["page"]["nav"] = "search"
    search["index_href"] = urls.href(SEARCH_INDEX_PATH)
    pages.append(PageSpec(PAGE_SEARCH, "search.html", search, ("/assets/search.js",)))

    how = _base(
        city,
        urls,
        path=PAGE_HOW,
        title=f"How this is made | {city.site_title}",
        description="How each story and episode is made, which parts are AI-written and which steps check them.",
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
    about["republish"] = REPUBLISH_NOTICE
    pages.append(PageSpec(PAGE_ABOUT, "about.html", about))

    sitemap_paths = [page.path for page in pages]
    return pages, sitemap_paths
