from datetime import date, datetime
from types import SimpleNamespace as NS

from civiccast_brief.sources import Source
from civiccast_brief.weekly.find import Agenda, Listers, find_meetings, match_body, youtube_id

COUNCIL = "City Council"
PZ = "Planning and Zoning Commission"
PORTAL = Source(kind="primegov", bodies=(COUNCIL, PZ), url="https://longmont.primegov.com")
CHANNEL = Source(kind="youtube", bodies=(COUNCIL,), channel_id="UC" + "a" * 22)
FIRST, LAST = date(2026, 10, 27), date(2026, 11, 10)


def _meeting(title, start, video=None, documents=()):
    return NS(title=title, start=start, video_url=video, documents=list(documents))


def test_match_body_takes_the_named_body_or_the_longest_listed_one():
    bodies = ["Commission", PZ, COUNCIL]
    assert match_body("City Council Regular Session", bodies) == COUNCIL
    assert match_body("Planning and Zoning Commission Meeting", bodies) == PZ
    assert match_body("Golf Course Advisory Board", bodies) == ""
    # A listed name inside another word is not a match.
    assert match_body("Councilmember town hall", [COUNCIL]) == ""


def test_youtube_id_reads_watch_and_short_links_only():
    assert youtube_id("https://www.youtube.com/watch?v=oWZpzjzjwnE") == "oWZpzjzjwnE"
    assert youtube_id("https://youtu.be/Fcdk3MPz8Ic") == "Fcdk3MPz8Ic"
    assert youtube_id("https://vimeo.com/123456789") == ""
    assert youtube_id("https://www.youtube.com/watch?v=short") == ""
    assert youtube_id(None) == ""


def test_portal_meetings_keep_their_agenda_and_say_why_one_cannot_run():
    council = _meeting(
        "City Council Regular Session",
        datetime(2026, 11, 3, 18, 0),
        "https://www.youtube.com/watch?v=oWZpzjzjwnE",
    )
    board = _meeting("Planning and Zoning Commission", datetime(2026, 11, 5, 19, 0))
    cancelled = _meeting(
        "CANCELLED: City Council Study Session",
        datetime(2026, 11, 10, 18, 0),
        "https://www.youtube.com/watch?v=Fcdk3MPz8Ic",
    )
    other = _meeting("Golf Course Advisory Board", datetime(2026, 11, 4, 17, 0))
    agenda = Agenda("https://longmont.primegov.com/agenda/1", ("1. Call to order",))
    seen = []

    def primegov(url, first, last):
        seen.append((url, first, last))
        return [council, board, cancelled, other]

    listers = Listers(
        primegov=primegov,
        primegov_agenda=lambda url, m: agenda if m is council else None,
    )
    found, problems = find_meetings([PORTAL], FIRST, LAST, listers)

    assert problems == []
    assert seen == [("https://longmont.primegov.com", FIRST, LAST)]
    assert [(m.day, m.body, m.video) for m in found] == [
        (date(2026, 11, 3), COUNCIL, "oWZpzjzjwnE"),
        (date(2026, 11, 5), PZ, ""),
        (date(2026, 11, 10), COUNCIL, "Fcdk3MPz8Ic"),
    ]
    assert found[0].agenda == agenda and found[0].reason == ""
    assert found[1].reason == "the portal lists no video for this meeting"
    assert found[2].reason == "the meeting was cancelled"


def test_channel_videos_must_be_meetings_in_the_window():
    videos = [
        NS(video_id="oWZpzjzjwnE", title="City Council Regular Session", published="2026-11-04T02:10:00+00:00"),
        NS(video_id="AAAAAAAAAAA", title="Longmont holiday parade 2026", published="2026-11-05T00:00:00+00:00"),
        NS(video_id="BBBBBBBBBBB", title="City Council Study Session", published="2026-10-01T00:00:00+00:00"),
        NS(video_id="CCCCCCCCCCC", title="Planning and Zoning Commission Meeting", published="2026-11-06T00:00:00Z"),
    ]  # fmt: skip
    found, problems = find_meetings([CHANNEL], FIRST, LAST, Listers(youtube=lambda cid: videos))

    assert problems == []
    assert [(m.video, m.body) for m in found] == [("oWZpzjzjwnE", COUNCIL), ("CCCCCCCCCCC", "")]
    assert found[1].reason.startswith("the title names no body this source covers")


def test_a_channel_video_is_on_the_day_its_title_names_not_the_day_it_went_up():
    """Lafayette's 2 November 2026 workshop went up at 05:56 UTC on 3 November; Boulder's
    13 October zoning board video went up on 3 November."""
    videos = [
        NS(video_id="DDDDDDDDDDD", title="City Council Workshop - November 2, 2026", published="2026-11-03T05:56:35Z"),
        NS(video_id="EEEEEEEEEEE", title="10-13-26 City Council Meeting", published="2026-11-03T19:59:30Z"),
        NS(video_id="FFFFFFFFFFF", title="City Council Regular Session", published="2026-11-04T02:10:00Z"),
    ]  # fmt: skip
    found, _ = find_meetings([CHANNEL], FIRST, LAST, Listers(youtube=lambda cid: videos))
    days = {m.video: m.day for m in found}
    assert days["DDDDDDDDDDD"] == date(2026, 11, 2)
    # Posted inside the days, of a meeting before them: listed, on its own day.
    assert days["EEEEEEEEEEE"] == date(2026, 10, 13)
    # No day in the title: the day it went up.
    assert days["FFFFFFFFFFF"] == date(2026, 11, 4)


def test_the_same_video_from_portal_and_channel_is_one_meeting_with_the_agenda():
    agenda = Agenda("https://longmont.primegov.com/agenda/1", ("1. Call to order",))
    council = _meeting(
        "City Council Regular Session",
        datetime(2026, 11, 3, 18, 0),
        "https://www.youtube.com/watch?v=oWZpzjzjwnE",
    )
    video = NS(
        video_id="oWZpzjzjwnE",
        title="City Council Regular Session",
        published="2026-11-03T20:00:00-07:00",
    )
    listers = Listers(
        primegov=lambda url, first, last: [council],
        primegov_agenda=lambda url, m: agenda,
        youtube=lambda cid: [video],
    )
    # The channel is read first here, so the agenda has to be merged into what it found.
    found, _ = find_meetings([CHANNEL, PORTAL], FIRST, LAST, listers)
    assert len(found) == 1
    assert found[0].key == "youtube:oWZpzjzjwnE"
    assert found[0].agenda == agenda


def test_granicus_clips_and_a_broken_or_unread_source_are_reported():
    archive = Source(kind="granicus", bodies=(COUNCIL,), host="example.granicus.com", view_id="2")
    clips = [
        NS(clip_id=4410, name="Regular Meeting", date="November 3, 2026"),
        NS(clip_id=4300, name="Regular Meeting", date="September 1, 2026"),
    ]

    def broken(url, first, last):
        raise RuntimeError("portal answered 503")

    listers = Listers(primegov=broken, granicus=lambda host, view: clips)
    found, problems = find_meetings([PORTAL, archive, CHANNEL], FIRST, LAST, listers)

    assert [(m.key, m.body, m.granicus_view_id) for m in found] == [
        ("granicus:example.granicus.com/4410", COUNCIL, 2)
    ]
    assert problems == [
        "primegov:longmont.primegov.com: portal answered 503",
        f"youtube:{CHANNEL.channel_id}: no reader for youtube sources",
    ]


def test_agenda_text_is_the_format_the_pipeline_reads(tmp_path):
    from civiccast_brief.meeting.agenda import load_agenda

    agenda = Agenda(
        "https://longmont.primegov.com/agenda/1", ("1. Call to order", "9.B Golf Course lease")
    )
    path = tmp_path / "agenda.txt"
    path.write_text(agenda.text(date(2026, 11, 10)), encoding="utf-8")
    loaded = load_agenda(path)
    assert loaded.source == "https://longmont.primegov.com/agenda/1"
    assert "Golf Course lease" in loaded.text
    assert "golf" in loaded.vocabulary


def test_the_portals_documents_go_with_the_meeting_and_survive_the_merge():
    council = _meeting(
        "City Council Regular Session",
        datetime(2026, 11, 3, 18, 0),
        "https://www.youtube.com/watch?v=oWZpzjzjwnE",
    )
    video = NS(
        video_id="oWZpzjzjwnE",
        title="City Council Regular Session",
        published="2026-11-03T20:00:00-07:00",
    )
    asked = []

    def documents(url, meeting, body):
        asked.append((url, meeting, body))
        return {"format": 1, "documents": []}, ["packet: 12 pages"]

    listers = Listers(
        primegov=lambda url, first, last: [council],
        primegov_documents=documents,
        youtube=lambda cid: [video],
    )
    # The channel is read first, so the documents have to be merged into what it found.
    found, _ = find_meetings([CHANNEL, PORTAL], FIRST, LAST, listers)
    assert len(found) == 1 and found[0].documents is not None
    assert asked == []  # nothing is fetched until the run gets to the meeting
    assert found[0].documents() == ({"format": 1, "documents": []}, ["packet: 12 pages"])
    assert asked == [("https://longmont.primegov.com", council, COUNCIL)]


def test_a_source_kind_with_no_lister_is_named_not_a_crash():
    from civiccast_brief.sources import make_source

    clerk = make_source("civicclerk", {"tenant": "erieco", "bodies": "Town Council"})
    found, problems = find_meetings([clerk], FIRST, LAST, Listers())
    assert found == []
    assert problems == ["civicclerk:erieco: no reader for civicclerk sources"]
