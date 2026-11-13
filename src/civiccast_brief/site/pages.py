"""Page list for one build: the context each template gets, and the path each page is written to."""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from civiccast_brief.site import seo
from civiccast_brief.site.feed import FEED_PATH, artwork_path
from civiccast_brief.site.model import Article, City, Claim, Episode, SiteInput, Story
from civiccast_brief.site.render import date_long, file_size
from civiccast_brief.site.stills import youtube_id
from civiccast_brief.site.timecode import clock, timecode
from civiccast_brief.site.titles import MONTHS_SHORT, split_meeting_label
from civiccast_brief.site.topics import TOPICS, slugify, topics_for
from civiccast_brief.site.urls import Urls, timestamp_link

PAGE_HOW = "/how-this-is-made/"
PAGE_ABOUT = "/about/"
#: Cloudflare Pages answers an unknown address with this page and a 404 status. Without it,
#: Pages serves the front page with a 200.
PAGE_NOT_FOUND = "/404.html"
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

TOP_CARDS = 2
"""Story cards under the lead story on the front page."""
MORE_COUNT = 3
SIDEBAR_EPISODES = 4
SIDEBAR_BOARDS = 6
BOARD_COLORS = 6
"""Plain color blocks for stories with no still image; one class per board (c0 to c5)."""

Item = Episode | Story


@dataclass(frozen=True)
class PageSpec:
    path: str
    template: str
    context: dict[str, Any]
    scripts: tuple[str, ...] = field(default_factory=tuple)


def episode_path(urls: Urls, episode: Episode, *parts: str) -> str:
    return urls.page_path("episodes", episode.id, *parts)


def story_path(urls: Urls, story: Story, *parts: str) -> str:
    return urls.page_path("stories", story.id, *parts)


def item_path(urls: Urls, item: Item) -> str:
    """The page of a story or an episode: /episodes/<id>/ when there is audio, else /stories/<id>/."""
    return episode_path(urls, item) if isinstance(item, Episode) else story_path(urls, item)


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
            "noindex": False,
        },
        "feed_href": urls.href(FEED_PATH),
        "feed_url": urls.absolute(FEED_PATH),
        "signup_url": signup,
        "desk_signin": f"{city.desk_url.rstrip('/')}/sign-in" if city.desk_url else None,
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


def _topic_chips(urls: Urls, item: Item) -> list[dict[str, str]]:
    return [
        {"name": topic.short, "href": urls.href(topic_path(urls, topic.slug)), "slug": topic.slug}
        for topic in topics_for(item)
    ]


def _claims_view(claims: tuple[Claim, ...], video: str | None) -> list[dict[str, Any]]:
    """The recap lines. A roundup line that starts "[September 22, 2026 meeting]" gets that
    meeting as a label of its own, not as a bracketed start of the sentence."""
    view = []
    for claim in claims:
        meeting, text = split_meeting_label(claim.text)
        view.append(
            {
                "id": claim.id,
                "topic": claim.topic,
                "meeting": f"{meeting} meeting" if meeting else None,
                "text": text,
                "quote": claim.quote,
                **_cite(video, claim.seconds),
            }
        )
    return view


def _story_page_view(
    urls: Urls, item: Item, video: str | None, claims: tuple[Claim, ...]
) -> dict[str, Any]:
    return {
        "episode": item,
        "headline": item.display_headline,
        "dek": item.display_dek,
        "article": _article_view(item.article, video) if item.article is not None else None,
        "claims": _claims_view(claims, video),
        "video_url": video,
        "meeting_date": date_long(item.meeting_date),
        "published_date": date_long(item.published),
        "board_href": urls.href(board_path(urls, item.board)),
        "topics": _topic_chips(urls, item),
        "republish": REPUBLISH_NOTICE,
    }


def _story_view(urls: Urls, story: Story) -> dict[str, Any]:
    """The story page context for a story with no episode: no audio, chapters or script."""
    return {
        **_story_page_view(urls, story, story.video_url, story.claims),
        "tags": [],
        "chapters": [],
        "script": [],
        "audio": None,
        "chapters_url": None,
        "vtt_url": None,
        "transcript_href": None,
    }


def _episode_view(city: City, urls: Urls, episode: Episode) -> dict[str, Any]:
    audio_url = urls.resolve(episode.audio.url)
    video = episode.video_url
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
        **_story_page_view(urls, episode, video, episode.claims),
        "chapters": chapters,
        "script": script,
        "audio": {
            "url": audio_url,
            "length": clock(episode.audio.duration_s),
            "duration_s": round(episode.audio.duration_s, 2),
            "size": file_size(episode.audio.bytes),
        },
        "chapters_url": urls.resolve(episode.chapters_url) if episode.chapters_url else None,
        "vtt_url": urls.resolve(episode.vtt_url) if episode.vtt_url else None,
        "transcript_href": (
            urls.href(episode_path(urls, episode, "transcript"))
            if episode.transcript is not None
            else None
        ),
        "tags": list(episode.tags),
    }


def _card(
    urls: Urls,
    item: Item,
    *,
    stills: Mapping[str, str] | None = None,
    colors: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """One story or episode as a list row, a story card or the lead story."""
    first_vote = None
    if item.article is not None and item.article.votes:
        vote = item.article.votes[0]
        first_vote = vote_badge(vote.result, vote.tally)
    audio = item.audio if isinstance(item, Episode) else None
    still = (stills or {}).get(youtube_id(item.video_url) or "")
    return {
        "id": item.id,
        "headline": item.display_headline,
        "href": urls.href(item_path(urls, item)),
        "board": item.board,
        "board_href": urls.href(board_path(urls, item.board)),
        "meeting_title": item.meeting_name,
        "meeting_date": date_long(item.meeting_date),
        "iso_date": item.meeting_date.isoformat(),
        "short_date": short_date(item.meeting_date),
        "day": f"{MONTHS_SHORT[item.meeting_date.month - 1]} {item.meeting_date.day:02d}",
        "published_date": date_long(item.published),
        "dek": item.display_dek,
        "has_audio": audio is not None,
        "length": clock(audio.duration_s) if audio is not None else None,
        "sample": item.sample,
        "claims": len(item.claims),
        "vote": first_vote,
        "month": month_key(item.meeting_date),
        "still": urls.href(still) if still else None,
        "color": (colors or {}).get(item.board, 0),
        "top": False,
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


def _newest_first(item: Item) -> tuple[date, bool, datetime, str]:
    """Newest meeting first; on the same day a written story comes before a roundup's recap."""
    return (item.meeting_date, item.article is not None, item.published, item.id)


def _month_groups(
    urls: Urls, cards: list[dict[str, Any]], newest_month: str | None
) -> list[dict[str, Any]]:
    """Month groups for the front page list. The newest month reads "Earlier in October"; a group
    whose stories are all shown above the list has no rows to show (``has_rows`` is False)."""
    groups = _by_month(urls, cards)
    for group in groups:
        earlier = group["key"] == newest_month
        group["label"] = f"Earlier in {group['name'].split()[0]}" if earlier else group["name"]
        group["has_rows"] = any(not row["top"] for row in group["rows"])
    return groups


def build_pages(
    site: SiteInput,
    urls: Urls,
    now: datetime,
    stills: Mapping[str, str] | None = None,
) -> tuple[list[PageSpec], list[str]]:
    """Return every HTML page and the sitemap paths for the site.

    The second value is the list of page paths that the sitemap lists. ``stills`` maps a YouTube
    video id to the site path of its still image.
    """
    city = site.city
    episodes = site.episodes
    pages: list[PageSpec] = []
    items: list[Item] = sorted([*episodes, *site.stories], key=_newest_first, reverse=True)
    colors = {
        name: index % BOARD_COLORS for index, name in enumerate(sorted({i.board for i in items}))
    }
    cards = [_card(urls, item, stills=stills, colors=colors) for item in items]
    for card in cards[: 1 + TOP_CARDS]:
        card["top"] = True
    card_by_id = {card["id"]: card for card in cards}

    # Topics with at least one story, in the fixed order, with their stories.
    topic_cards: dict[str, list[dict[str, Any]]] = {topic.slug: [] for topic in TOPICS}
    for item in items:
        for topic in topics_for(item):
            topic_cards[topic.slug].append(card_by_id[item.id])
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

    episode_cards = [card_by_id[episode.id] for episode in episodes]

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
    home["top"] = cards[1 : 1 + TOP_CARDS]
    home["months"] = _month_groups(urls, cards, cards[0]["month"] if cards else None)
    home["chips"] = used_topics
    home["boards"] = board_list
    by_size = sorted(board_list, key=lambda b: (-b["count"], b["name"]))
    home["boards_shown"] = by_size[:SIDEBAR_BOARDS]
    home["boards_more"] = by_size[SIDEBAR_BOARDS:]
    home["boards_href"] = urls.href(PAGE_BOARDS)
    home["board_names"] = [board["name"] for board in board_list]
    home["podcast_episodes"] = episode_cards[:SIDEBAR_EPISODES]
    home["podcast_links"] = _podcast_links(city, urls)
    home["podcast_href"] = urls.href(PAGE_PODCAST)
    home["archive_href"] = urls.href(PAGE_ARCHIVE)
    home["republish"] = REPUBLISH_NOTICE
    pages.append(PageSpec("/", "home.html", home, ("/assets/front.js",)))

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

    for story in site.stories:
        path = story_path(urls, story)
        context = _base(
            city,
            urls,
            path=path,
            title=f"{story.display_headline} | {city.site_title}",
            description=story.display_dek or city.tagline,
        )
        context["page"]["og_type"] = "article"
        context["page"]["published"] = story.published.isoformat()
        context["page"]["nav"] = "episode"
        context["page"]["json_ld"] = [
            seo.script_block(seo.episode_news_article(city, urls, story, section="stories"))
        ]
        context.update(_story_view(urls, story))
        context["more"] = [card for card in boards[story.board] if card["id"] != story.id][
            :MORE_COUNT
        ]
        pages.append(PageSpec(path, "episode.html", context))

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
    podcast["episodes"] = episode_cards
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

    missing = _base(
        city,
        urls,
        path=PAGE_NOT_FOUND,
        title=f"Page not found | {city.site_title}",
        description=f"There is no page at this address on {city.site_title}.",
    )
    missing["page"]["noindex"] = True
    pages.append(PageSpec(PAGE_NOT_FOUND, "notfound.html", missing))

    # A story whose meeting now has an episode: its old address goes to the episode page.
    by_id = {episode.id: episode for episode in episodes}
    for story_id in site.merged_stories:
        target = urls.page_path("episodes", by_id[story_id].id)
        pages.append(
            PageSpec(
                urls.page_path("stories", story_id),
                "redirect.html",
                {"target": urls.href(target), "canonical": urls.absolute(target)},
            )
        )

    for page in pages:
        if page.template != "redirect.html":
            page.context["topic_chips"] = used_topics
            page.context["today"] = f"{now:%A}, {date_long(now)}"
    return pages, sitemap_paths
