"""Turn what a person pastes for a YouTube channel into its channel id (``UC`` and 22 more).

Accepted: the id itself, ``youtube.com/channel/UC...``, ``youtube.com/@Handle`` or ``@Handle``,
``youtube.com/user/Name`` and ``youtube.com/c/Name``. Only a handle or name needs the web: one
public read of the channel page, through the same reader the city search uses
(:func:`civiccast_brief.citybuild.search.channel_id`). No API key, no sign-in, no cookies.
"""

from __future__ import annotations

import re
from typing import Final

from civiccast_brief.citybuild import detect
from civiccast_brief.citybuild.fetch import Fetcher, FetchError
from civiccast_brief.citybuild.search import channel_id as read_channel_page

_ID: Final = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_ASK: Final = (
    "Paste the channel id instead: it starts with UC and is 24 characters. On the channel's "
    "page, open About > Share channel > Copy channel ID. A link like "
    "https://www.youtube.com/channel/UC... also works."
)


class ChannelError(ValueError):
    """The channel id could not be found. The message says what to paste instead."""


def resolve_channel(text: str, fetcher: Fetcher) -> str:
    """The channel id for ``text``; reads at most one page, and only for a handle or name."""
    value = text.strip()
    if not value:
        raise ChannelError("Enter the YouTube channel id or its address. " + _ASK)
    if _ID.match(value):
        return value
    if value.startswith("@"):
        value = "https://www.youtube.com/" + value
    elif re.match(r"^(?:www\.|m\.)?youtube\.com/", value, re.IGNORECASE):
        value = "https://" + value
    found = detect.classify(value)
    if found is None or found.platform != "youtube":
        raise ChannelError(f"{text.strip()!r} is not a YouTube channel address. " + _ASK)
    if found.key:
        return found.key
    if not found.extra or found.extra.startswith("playlist:"):
        raise ChannelError(f"{text.strip()!r} names a playlist, not a channel. " + _ASK)
    try:
        channel = read_channel_page(found, fetcher)
    except FetchError as exc:
        raise ChannelError(
            f"YouTube's page for {found.extra} did not answer ({exc}). " + _ASK
        ) from exc
    if not channel:
        raise ChannelError(f"No channel id was found on YouTube's page for {found.extra}. " + _ASK)
    return channel
