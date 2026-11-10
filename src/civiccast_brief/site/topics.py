"""Topics and boards: the short fixed topic list, and how an episode is placed on it.

Claim topics in a recap are free text ("Copper Peak concept plan amendment", "RTD station",
"Public comment - library hours"). The site maps them onto a short fixed list by keywords, so a
reader can filter by a handful of subjects. An episode's topics come from its claim topics, its
tags and its article topics. An episode that matches none of them is filed under Government.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from civiccast_brief.site.model import Episode


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    short: str
    """Chip label."""
    keywords: tuple[str, ...]


TOPICS: tuple[Topic, ...] = (
    Topic(
        "land-use",
        "Land use and zoning",
        "Land use",
        (
            "zoning",
            "zone",
            "rezon",
            "annex",
            "concept plan",
            "land use",
            "subdivision",
            "plat",
            "site plan",
            "development",
            "planning",
            "comprehensive plan",
            "metro district",
        ),
    ),
    Topic(
        "housing",
        "Housing",
        "Housing",
        ("housing", "affordable", "apartment", "homeless", "rent", "dwelling", "residential"),
    ),
    Topic(
        "transit",
        "Transit and streets",
        "Transit",
        (
            "transit",
            "rtd",
            "street",
            "road",
            "traffic",
            "bike",
            "bus",
            "rail",
            "transportation",
            "parking",
            "sidewalk",
        ),
    ),
    Topic(
        "utilities",
        "Utilities and water",
        "Utilities",
        (
            "water",
            "utilit",
            "sewer",
            "electric",
            "power",
            "broadband",
            "nextlight",
            "compost",
            "trash",
            "waste",
            "recycl",
            "stormwater",
        ),
    ),
    Topic(
        "budget",
        "Budget and taxes",
        "Budget",
        ("budget", "tax", "fee", "fund", "appropriat", "bond", "revenue", "spending", "financ"),
    ),
    Topic(
        "parks",
        "Parks and environment",
        "Parks",
        (
            "park",
            "open space",
            "trail",
            "environment",
            "climate",
            "tree",
            "prairie",
            "wildlife",
            "recreation",
            "sustainab",
        ),
    ),
    Topic(
        "public-safety",
        "Public safety",
        "Public safety",
        ("police", "fire", "public safety", "emergency", "crime", "safety"),
    ),
    Topic(
        "public-comment", "Public comment", "Public comment", ("public comment", "public invited")
    ),
    Topic(
        "government",
        "Government",
        "Government",
        (
            "council",
            "ordinance",
            "resolution",
            "minutes",
            "proclamation",
            "appoint",
            "election",
            "survey",
            "board",
            "commission",
        ),
    ),
)
TOPIC_BY_SLUG = {topic.slug: topic for topic in TOPICS}
FALLBACK = "government"


def _matches(topic: Topic, text: str) -> bool:
    return any(re.search(rf"\b{re.escape(word)}", text) for word in topic.keywords)


def topics_for(episode: Episode) -> tuple[Topic, ...]:
    """The episode's topics, in the fixed list's order. Never empty."""
    labels = [claim.topic for claim in episode.claims if claim.topic]
    labels += list(episode.tags)
    if episode.article is not None:
        labels += list(episode.article.topics)
    text = " | ".join(labels).lower()
    found = tuple(topic for topic in TOPICS if _matches(topic, text))
    return found or (TOPIC_BY_SLUG[FALLBACK],)


def slugify(text: str) -> str:
    """Lower case, letters and digits joined by hyphens: "City Council" -> "city-council"."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "board"
