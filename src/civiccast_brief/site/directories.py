"""What Apple Podcasts and Spotify require of a feed, written as checks the site build runs.

Sources, read November 10, 2026:
- Apple, A Podcaster's Guide to RSS: help.apple.com/itc/podcasts_connect/en.lproj/itcb54353390.html
- Apple, Podcast requirements: podcasters.apple.com/support/823-podcast-requirements
- Apple, Validate your podcast: podcasters.apple.com/support/829-validate-your-podcast
- Apple, Show cover template: podcasters.apple.com/support/5514-show-cover-template
- Apple, Apple Podcasts categories: podcasters.apple.com/support/1691-apple-podcasts-categories
- Spotify, Podcast Delivery Specification v1.10 (May 2025)
- Spotify, Adding your email address to your RSS feed:
  support.spotify.com/us/podcasters/article/adding-your-email-address-to-your-rss-feed/

The rules that matter here:
- Show art: square, 1400 to 3000 pixels a side, JPEG or PNG, RGB with no transparency, and a
  file name ending that matches the file (Apple). Spotify asks only for a square image.
- Category: one of Apple's categories, spelled exactly, with an optional subcategory of it.
- Language: an ISO 639 code (Apple), such as "en" or "en-us".
- Owner email: Spotify sends its ownership code to the feed's itunes:email, so it must be an inbox
  someone reads.
- Show description: at most 4,000 bytes (Apple). Episode description: at most 10,000 characters,
  and only the tags p, ol, ul, li and a (Apple).
- Chapter titles: 45 characters or fewer (Apple's guide). Apple's submission checks do not
  include it, so the build does not refuse a longer one; the script writer is told the limit.
- Episode audio: MP3 (audio/mpeg) or AAC (audio/x-m4a). Spotify plays only MP3 from a feed.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

#: Apple's categories and their subcategories, spelled as the feed must write them.
APPLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Arts": ("Books", "Design", "Fashion & Beauty", "Food", "Performing Arts", "Visual Arts"),
    "Business": (
        "Careers",
        "Entrepreneurship",
        "Investing",
        "Management",
        "Marketing",
        "Non-Profit",
    ),
    "Comedy": ("Comedy Interviews", "Improv", "Stand-Up"),
    "Education": ("Courses", "How To", "Language Learning", "Self-Improvement"),
    "Fiction": ("Comedy Fiction", "Drama", "Science Fiction"),
    "Government": (),
    "History": (),
    "Health & Fitness": (
        "Alternative Health",
        "Fitness",
        "Medicine",
        "Mental Health",
        "Nutrition",
        "Sexuality",
    ),
    "Kids & Family": ("Education for Kids", "Parenting", "Pets & Animals", "Stories for Kids"),
    "Leisure": (
        "Animation & Manga",
        "Automotive",
        "Aviation",
        "Crafts",
        "Games",
        "Hobbies",
        "Home & Garden",
        "Video Games",
    ),
    "Music": ("Music Commentary", "Music History", "Music Interviews"),
    "News": (
        "Business News",
        "Daily News",
        "Entertainment News",
        "News Commentary",
        "Politics",
        "Sports News",
        "Tech News",
    ),
    "Religion & Spirituality": (
        "Buddhism",
        "Christianity",
        "Hinduism",
        "Islam",
        "Judaism",
        "Religion",
        "Spirituality",
    ),
    "Science": (
        "Astronomy",
        "Chemistry",
        "Earth Sciences",
        "Life Sciences",
        "Mathematics",
        "Natural Sciences",
        "Nature",
        "Physics",
        "Social Sciences",
    ),
    "Society & Culture": (
        "Documentary",
        "Personal Journals",
        "Philosophy",
        "Places & Travel",
        "Relationships",
    ),
    "Sports": (
        "Baseball",
        "Basketball",
        "Cricket",
        "Fantasy Sports",
        "Football",
        "Golf",
        "Hockey",
        "Rugby",
        "Running",
        "Soccer",
        "Swimming",
        "Tennis",
        "Volleyball",
        "Wilderness",
        "Wrestling",
    ),
    "Technology": (),
    "True Crime": (),
    "TV & Film": ("After Shows", "Film History", "Film Interviews", "Film Reviews", "TV Reviews"),
}

ART_MIN = 1400
ART_MAX = 3000
SHOW_DESCRIPTION_MAX_BYTES = 4000
EPISODE_DESCRIPTION_MAX_CHARS = 10_000
TITLE_MAX = 255
CHAPTER_TITLE_MAX = 45
AUDIO_TYPES = ("audio/mpeg", "audio/x-m4a")
#: ISO 639 language, with an optional region or script: "en", "en-us", "es-419".
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$")


def category_problem(category: str, subcategory: str) -> tuple[str, str] | None:
    """The setting and the reason when a category pair is not one Apple lists, else None."""
    if category not in APPLE_CATEGORIES:
        return "category", f"{category!r} is not an Apple Podcasts category"
    if subcategory and subcategory not in APPLE_CATEGORIES[category]:
        allowed = ", ".join(APPLE_CATEGORIES[category]) or "none"
        return "subcategory", f"{subcategory!r} is not under {category!r} (allowed: {allowed})"
    return None


@dataclass(frozen=True)
class ImageFacts:
    kind: str  # "png" or "jpeg"
    width: int
    height: int
    #: True for three-channel color with no alpha (PNG color type 2, or a 3-component JPEG).
    rgb: bool


def image_facts(data: bytes) -> ImageFacts | None:
    """Read the size and color layout of a PNG or JPEG from its header, or None if it is neither."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 26:
        width, height, _depth, color_type = struct.unpack(">IIBB", data[16:26])
        # A palette PNG (type 3) with a tRNS chunk has transparency; treat palettes as not RGB.
        return ImageFacts("png", width, height, rgb=color_type == 2)
    if data.startswith(b"\xff\xd8"):
        at = 2
        while at + 4 <= len(data):
            if data[at] != 0xFF:
                return None
            marker = data[at + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                at += 2
                continue
            length = struct.unpack(">H", data[at + 2 : at + 4])[0]
            # Start-of-frame markers carry the size; C4, C8 and CC are other tables.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if at + 10 > len(data):
                    return None
                height, width = struct.unpack(">HH", data[at + 5 : at + 9])
                components = data[at + 9]
                return ImageFacts("jpeg", width, height, rgb=components == 3)
            at += 2 + length
    return None


def artwork_problems(data: bytes, suffix: str) -> list[str]:
    """Every way a show-art file breaks Apple's rules (Spotify's are a subset)."""
    facts = image_facts(data)
    if facts is None:
        return ["is not a PNG or JPEG file"]
    problems: list[str] = []
    expected = {"png": (".png",), "jpeg": (".jpg", ".jpeg")}[facts.kind]
    if suffix.lower() not in expected:
        problems.append(f"is a {facts.kind.upper()} file but its name ends in {suffix}")
    if facts.width != facts.height:
        problems.append(f"must be square; it is {facts.width}x{facts.height}")
    if not (ART_MIN <= facts.width <= ART_MAX and ART_MIN <= facts.height <= ART_MAX):
        problems.append(
            f"must be {ART_MIN} to {ART_MAX} pixels a side; it is {facts.width}x{facts.height}"
        )
    if not facts.rgb:
        problems.append("must be RGB color with no transparency")
    return problems
