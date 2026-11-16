"""The CivicClerk, eScribe and Vimeo video listings in the weekly run, from saved fixtures."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from civiccast_brief.meeting import direct
from civiccast_brief.record.video.youtube.listing import VideoListing
from civiccast_brief.sources import Source, make_source
from civiccast_brief.weekly.find import (
    NO_CAPTIONS,
    Found,
    Listers,
    find_meetings,
    match_body,
)
from civiccast_brief.weekly.run import Settings, Steps, run_week

FIXTURES = Path(__file__).parent / "fixtures"
FIRST, LAST = date(2026, 10, 17), date(2026, 11, 16)
COUNCIL = "Town Council"
PLANNING = "Planning Commission"
BOCC = "Board of County Commissioners"
SCHOOL = "Board of Education"


def _listings(kind: str) -> list[VideoListing]:
    rows = json.loads((FIXTURES / "video_listings.json").read_text(encoding="utf-8"))[kind]
    return [VideoListing(**row) for row in rows]


def _source(kind: str, tenant: str, *bodies: str) -> Source:
    return Source(kind=kind, bodies=bodies, tenant=tenant)


def _lister(kind: str):
    def list_videos(tenant, first, last):
        assert (first, last) == (FIRST, LAST)
        assert tenant
        return _listings(kind)

    return list_videos


def test_civicclerk_events_become_meetings_with_their_media_and_captions():
    listers = Listers(civicclerk=_lister("civicclerk"))
    found, problems = find_meetings(
        [_source("civicclerk", "erieco", COUNCIL, PLANNING)], FIRST, LAST, listers
    )

    assert problems == []
    assert [(m.body, m.day, m.video) for m in found] == [
        (PLANNING, date(2026, 11, 9), "civicclerk-erieco-102"),
        (COUNCIL, date(2026, 11, 10), "civicclerk-erieco-101"),
    ]
    planning = found[0]
    assert planning.key == "civicclerk:erieco/civicclerk-erieco-102"
    assert planning.media_url.endswith("planning-2026-11-09.mp4")
    assert planning.captions_url.endswith("planning-2026-11-09.vtt")
    assert planning.reason == ""
    assert found[1].reason == NO_CAPTIONS
    # The Tree Advisory Board is another body's event on the same tenant.
    assert all("Tree" not in m.title for m in found)


def test_an_event_whose_category_names_the_body_is_the_sources():
    # "Regular Meeting" alone names no body; the CivicClerk category does.
    listing = VideoListing(
        video_id="9",
        title="Regular Meeting",
        published="2026-11-10T19:30:00Z",
        channel_id=COUNCIL,
        source_method="civicclerk",
        media_url="https://cdn.test/9.mp4",
        captions_url="https://cdn.test/9.vtt",
    )
    listers = Listers(civicclerk=lambda tenant, first, last: [listing])
    found, _ = find_meetings([_source("civicclerk", "erieco", COUNCIL)], FIRST, LAST, listers)
    assert [(m.body, m.video, m.reason) for m in found] == [(COUNCIL, "civicclerk-erieco-9", "")]


def test_escribe_meetings_take_the_calendar_day_and_the_players_captions():
    listers = Listers(escribe=_lister("escribe"))
    found, problems = find_meetings(
        [_source("escribe", "bouldercounty", BOCC)], FIRST, LAST, listers
    )

    assert problems == []
    assert [(m.body, m.day, m.video, m.reason) for m in found] == [
        (BOCC, date(2026, 11, 5), "escribe-bouldercounty-meeting-101", "")
    ]
    assert found[0].media_url.startswith("https://video.isilive.ca/bouldercounty/")
    assert found[0].captions_url == found[0].media_url + ".vtt"


def test_vimeo_meetings_take_the_day_their_title_names():
    listers = Listers(vimeo=_lister("vimeo"))
    found, problems = find_meetings([_source("vimeo", "svvsd", SCHOOL)], FIRST, LAST, listers)

    assert problems == []
    assert [(m.day, m.video, m.reason) for m in found] == [
        (date(2026, 10, 28), "vimeo-svvsd-222222222", NO_CAPTIONS),
        (date(2026, 11, 11), "vimeo-svvsd-111111111", ""),
    ]


def test_a_kind_with_no_lister_is_named_once():
    found, problems = find_meetings(
        [_source("civicclerk", "erieco", COUNCIL)], FIRST, LAST, Listers()
    )
    assert found == []
    assert problems == ["civicclerk:erieco: no reader for civicclerk sources"]


def test_civicclerk_documents_still_join_the_same_days_video():
    def documents(source, first, last):
        return [
            NS(
                body=PLANNING,
                day=date(2026, 11, 9),
                title="Regular Meeting",
                url="https://clerk.test/102/agenda",
                what="agenda",
            )
        ]

    listers = Listers(
        civicclerk=_lister("civicclerk"),
        documents=documents,
        read_documents=lambda docs: ({"format": 1, "documents": []}, ["agenda: 2 pages"]),
    )
    found, problems = find_meetings(
        [_source("civicclerk", "erieco", COUNCIL, PLANNING)], FIRST, LAST, listers
    )

    assert problems == []
    planning = next(m for m in found if m.body == PLANNING)
    assert planning.media_url and planning.documents is not None
    assert planning.documents() == ({"format": 1, "documents": []}, ["agenda: 2 pages"])


def _run_found(tmp_path, meeting_found, body):
    city = tmp_path / "city"
    city.mkdir()
    (city / "roster.json").write_text(json.dumps({"body": body}), encoding="utf-8")
    calls = []

    def meeting(argv):
        calls.append(list(argv))
        return 1  # the run stops here; only the command line is looked at

    def unused(argv):
        raise AssertionError(argv)

    steps = Steps(
        meeting=meeting,
        script=unused,
        episode=unused,
        article=unused,
        review=unused,
        audio=unused,
        site=unused,
    )
    settings = Settings(city=city, work=tmp_path / "work", db=tmp_path / "desk.db", site=None)
    run_week(settings, [meeting_found], LAST, steps)
    (argv,) = calls
    return argv


def test_the_meeting_command_gets_the_media_the_captions_and_the_known_day(tmp_path):
    meeting_found = Found(
        PLANNING,
        date(2026, 11, 9),
        "Planning Commission Meeting",
        "civicclerk:erieco",
        "civicclerk-erieco-102",
        media_url="https://cpmedia.azureedge.net/erieco/planning-2026-11-09.mp4",
        captions_url="https://cpmedia.azureedge.net/erieco/planning-2026-11-09.vtt",
    )

    argv = _run_found(tmp_path, meeting_found, PLANNING)

    assert argv[-2:] == ["--", "civicclerk-erieco-102"]
    assert argv[argv.index("--media-url") + 1] == meeting_found.media_url
    assert argv[argv.index("--captions-url") + 1] == meeting_found.captions_url
    assert argv[argv.index("--title") + 1] == meeting_found.title
    assert argv[argv.index("--meeting-date") + 1] == "November 9, 2026"
    assert "--granicus" not in argv and "--cablecast" not in argv


def test_a_direct_capture_reads_the_sources_public_vtt(tmp_path, monkeypatch):
    vtt = (FIXTURES / "captions.vtt").read_text(encoding="utf-8")
    monkeypatch.setattr(direct, "_fetch", lambda url: vtt)

    captured = direct.capture("https://cdn.test/9.mp4", "https://cdn.test/9.vtt", tmp_path / "raw")

    assert captured.caption_reused is False
    assert [item["start_ms"] for item in captured.items] == [1000, 5000]
    assert "Town Council is called to order" in captured.items[0]["text"]
    again = direct.capture("https://cdn.test/9.mp4", "https://cdn.test/9.vtt", tmp_path / "raw")
    assert again.caption_reused is True


def test_a_direct_capture_without_captions_says_the_pipeline_does_not_transcribe(tmp_path):
    with pytest.raises(direct.DirectError, match="does not transcribe audio"):
        direct.capture("https://cdn.test/9.mp4", None, tmp_path / "raw")


def test_a_vimeo_source_is_made_from_its_account_name_or_address():
    from_address = make_source(
        "vimeo", {"tenant": "https://vimeo.com/svvsd", "bodies": "Board of Education"}
    )
    assert from_address.id == "vimeo:svvsd"
    assert from_address.address == "https://vimeo.com/svvsd"
    assert from_address.to_json()["tenant"] == "svvsd"
    assert make_source("vimeo", {"tenant": "svvsd", "bodies": "Board"}) == Source(
        kind="vimeo", bodies=("Board",), tenant="svvsd"
    )


def test_match_body_is_used_for_the_listing_titles():
    assert match_body("2026-11-11 Board of Education Regular Meeting", [SCHOOL]) == SCHOOL
