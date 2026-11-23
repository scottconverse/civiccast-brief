"""Fetcher's one retry: CivicClerk document GETs are asked again, nothing else is.

No test reaches the network: a MockTransport answers each attempt from a list.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from civiccast_brief.citybuild.fetch import Fetcher, FetchError

DOCUMENT = "https://superiorco.api.civicclerk.com/v1/Events?$top=200"
OTHER = "https://www.example.gov/agenda.pdf"


def make_fetcher(
    answers: Iterator[int | Exception],
) -> tuple[Fetcher, list[str], list[float]]:
    """A fetcher whose transport answers each request with the next status or exception."""
    asked: list[str] = []
    waited: list[float] = []
    pending = iter(answers)

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        answer = next(pending)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, content=b"ok")

    client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    return Fetcher(client=client, sleep=waited.append), asked, waited


def test_a_civicclerk_500_then_200_succeeds_after_a_five_second_wait() -> None:
    fetcher, asked, waited = make_fetcher(iter([500, 200]))

    page = fetcher.get(DOCUMENT)

    assert page.status == 200
    assert page.content == b"ok"
    assert asked == [DOCUMENT, DOCUMENT]
    assert waited == [5.0]


def test_a_civicclerk_timeout_then_200_succeeds_after_a_five_second_wait() -> None:
    fetcher, asked, waited = make_fetcher(iter([httpx.ReadTimeout("slow"), 200]))

    page = fetcher.get(DOCUMENT)

    assert page.status == 200
    assert asked == [DOCUMENT, DOCUMENT]
    assert waited == [5.0]


def test_three_civicclerk_500s_raise_after_three_attempts_and_two_waits() -> None:
    fetcher, asked, waited = make_fetcher(iter([500, 500, 500]))

    with pytest.raises(FetchError, match="answered HTTP 500"):
        fetcher.get(DOCUMENT)

    assert asked == [DOCUMENT, DOCUMENT, DOCUMENT]
    assert waited == [5.0, 15.0]


def test_a_civicclerk_404_is_tried_once() -> None:
    fetcher, asked, waited = make_fetcher(iter([404]))

    with pytest.raises(FetchError, match="answered HTTP 404"):
        fetcher.get(DOCUMENT)

    assert asked == [DOCUMENT]
    assert waited == []


def test_a_500_from_another_host_is_tried_once_with_no_wait() -> None:
    fetcher, asked, waited = make_fetcher(iter([500]))

    with pytest.raises(FetchError, match="answered HTTP 500"):
        fetcher.get(OTHER)

    assert asked == [OTHER]
    assert waited == []
