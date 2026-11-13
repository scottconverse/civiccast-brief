"""A pasted YouTube link or handle becomes the channel id, from saved pages. No live network."""

from __future__ import annotations

import pytest

from civiccast_brief.citybuild.youtube import ChannelError, resolve_channel

from .conftest import CHANNEL, HERE, Web

LOVELAND = "UC9OMF1xKFd0h7Li6bBU9Awg"


def loveland_page(web: Web, path: str) -> None:
    web.add(
        "https://www.youtube.com/" + path,
        (HERE / "youtube-cityofloveland-channel.html").read_bytes(),
    )


@pytest.mark.parametrize(
    "pasted",
    [
        LOVELAND,
        f"  {LOVELAND}  ",
        f"https://www.youtube.com/channel/{LOVELAND}",
        f"youtube.com/channel/{LOVELAND}/videos",
    ],
)
def test_an_id_or_a_channel_link_needs_no_web_request(web, pasted):
    assert resolve_channel(pasted, web.fetcher()) == LOVELAND
    assert web.asked == []


@pytest.mark.parametrize(
    ("pasted", "page"),
    [
        ("https://www.youtube.com/@CityofLoveland", "@CityofLoveland"),
        ("@CityofLoveland", "@CityofLoveland"),
        ("www.youtube.com/@CityofLoveland", "@CityofLoveland"),
        ("https://www.youtube.com/user/CityofLoveland", "user/CityofLoveland"),
        ("https://www.youtube.com/c/CityofLoveland", "c/CityofLoveland"),
    ],
)
def test_a_handle_or_name_is_read_from_the_channel_page_once(web, pasted, page):
    loveland_page(web, page)  # a saved capture of the public page of @CityofLoveland
    assert resolve_channel(pasted, web.fetcher()) == LOVELAND
    assert web.asked == ["https://www.youtube.com/" + page]


@pytest.mark.parametrize(
    "html",
    [
        f'<head><link rel="canonical" href="https://www.youtube.com/channel/{CHANNEL}"></head>',
        f'<script>{{"externalId":"{CHANNEL}","keywords":"x"}}</script>',
        f'<meta itemprop="identifier" content="{CHANNEL}">',
    ],
)
def test_each_place_the_page_names_its_channel_is_read(web, html):
    web.add("https://www.youtube.com/@Town", html)
    assert resolve_channel("@Town", web.fetcher()) == CHANNEL


def test_a_page_without_an_id_says_what_to_paste_instead(web):
    web.add("https://www.youtube.com/@Town", "<html>consent wall</html>")
    with pytest.raises(ChannelError, match="Copy channel ID"):
        resolve_channel("@Town", web.fetcher())
    with pytest.raises(ChannelError, match="Copy channel ID"):
        resolve_channel("@Gone", web.fetcher())  # a 404


@pytest.mark.parametrize(
    "pasted",
    [
        "",
        "   ",
        "hello",
        "https://example.com/@Town",
        "https://www.youtube.com/playlist?list=PLabc",
    ],
)
def test_something_else_is_refused_without_a_web_request(web, pasted):
    with pytest.raises(ChannelError, match="channel id"):
        resolve_channel(pasted, web.fetcher())
    assert web.asked == []
