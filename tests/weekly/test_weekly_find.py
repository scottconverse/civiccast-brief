from datetime import date, datetime
from types import SimpleNamespace as NS

from civiccast_brief.sources import Source
from civiccast_brief.weekly.find import Agenda, Listers, find_meetings, match_body, youtube_id

COUNCIL = "City Council"
PZ = "Planning and Zoning Commission"
TRUSTEES = "Board of Trustees"
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


def test_match_body_treats_an_ampersand_and_and_the_same():
    body = "Planning and Community Development Commission"
    ampersand_body = "Planning & Community Development Commission"

    assert match_body(ampersand_body, [body]) == body
    assert match_body(body, [ampersand_body]) == ampersand_body


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


def _doc(body, day, title, url, what="agenda"):
    return NS(body=body, day=day, title=title, url=url, what=what)


def test_listed_documents_go_with_the_same_day_video_or_stand_alone():
    from civiccast_brief.sources import make_source

    no_video = "only the meeting's documents are listed; no source lists its video"
    clerk = make_source("civicclerk", {"tenant": "erieco", "bodies": "City Council"})
    folder = make_source("laserfiche", {"url": "https://lf.test/WebLink/Browse.aspx?id=1&dbid=0&repo=Town", "bodies": "City Council"})  # fmt: skip
    video = NS(video_id="oWZpzjzjwnE", title="City Council Regular Session - November 3, 2026", published="2026-11-03T20:00:00-07:00")  # fmt: skip
    rows = {
        clerk.id: [
            _doc(COUNCIL, date(2026, 11, 3), "Regular Meeting", "https://clerk.test/2197"),
            _doc(
                COUNCIL, date(2026, 11, 3), "Regular Meeting", "https://clerk.test/2198", "packet"
            ),
            _doc(COUNCIL, date(2026, 11, 10), "Study Session", "https://clerk.test/2210"),
        ],
        folder.id: [_doc(COUNCIL, date(2026, 11, 3), "Minutes", "https://lf.test/77", "minutes")],
    }
    read = []

    def read_documents(docs):
        read.append([d.url for d in docs])
        return {"format": 1, "documents": [{"url": d.url} for d in docs]}, [d.url for d in docs]

    listers = Listers(
        youtube=lambda cid: [video],
        documents=lambda source, first, last: rows[source.id],
        read_documents=read_documents,
    )
    found, problems = find_meetings([clerk, CHANNEL, folder], FIRST, LAST, listers)

    assert problems == []
    assert [(m.day, m.video, m.reason) for m in found] == [
        (date(2026, 11, 3), "oWZpzjzjwnE", ""),
        (date(2026, 11, 10), "", no_video),
    ]
    assert read == []  # nothing is fetched until the run gets to the meeting
    data, lines = found[0].documents()
    assert [d["url"] for d in data["documents"]] == [
        "https://clerk.test/2197",
        "https://clerk.test/2198",
        "https://lf.test/77",
    ]
    assert lines == [d["url"] for d in data["documents"]]
    assert found[1].documents()[0]["documents"] == [{"url": "https://clerk.test/2210"}]


def test_a_channel_video_of_a_body_another_city_source_lists_is_that_bodys():
    """Longmont's channel source named only the bodies in its newest titles at setup; its
    21 October 2026 Planning and Zoning Commission video was passed over."""
    videos = [
        NS(video_id="unNcPhKJ45o", title="Planning and Zoning Commission 10/21/26", published="2026-10-22T16:00:00Z"),
        NS(video_id="GGGGGGGGGGG", title="Golf Course Advisory Board Meeting", published="2026-11-04T00:00:00Z"),
    ]  # fmt: skip
    listers = Listers(youtube=lambda cid: videos, primegov=lambda url, first, last: [])
    found, _ = find_meetings([CHANNEL, PORTAL], date(2026, 10, 21), LAST, listers)
    by_video = {m.video: m for m in found}
    assert (by_video["unNcPhKJ45o"].body, by_video["unNcPhKJ45o"].reason) == (PZ, "")
    # A body no source of the city lists is still not run, with the reason.
    assert by_video["GGGGGGGGGGG"].body == ""
    assert by_video["GGGGGGGGGGG"].reason.startswith("the title names no body this source covers")


def test_a_civicweb_sources_documents_are_listed_with_the_meeting_day():
    from civiccast_brief.sources import make_source

    portal = make_source("civicweb", {"tenant": "cilovelandco", "bodies": "City Council"})
    rows = {
        portal.id: [
            _doc(COUNCIL, date(2026, 11, 4), "Council Meeting", "https://cw.test/1", "agenda"),
            _doc(COUNCIL, date(2026, 11, 4), "Council Meeting", "https://cw.test/2", "packet"),
        ]
    }
    listers = Listers(
        documents=lambda source, first, last: rows[source.id],
        read_documents=lambda docs: ({"documents": [{"url": d.url} for d in docs]}, []),
    )
    found, problems = find_meetings([portal], FIRST, LAST, listers)
    assert problems == []
    assert [(m.day, m.body) for m in found] == [(date(2026, 11, 4), COUNCIL)]
    assert [d["url"] for d in found[0].documents()[0]["documents"]] == [
        "https://cw.test/1",
        "https://cw.test/2",
    ]


def test_a_short_name_in_parentheses_does_not_hide_the_body():
    """Longmont's channel, 10 November 2026."""
    bodies = [
        "Longmont Housing Authority Advisory Board",
        "Longmont Housing Authority Board of Commissioners",
    ]
    title = "Longmont Housing Authority(LHA) Advisory Board of Commissioners Meeting, Nov. 10, 2026"
    assert match_body(title, bodies) == "Longmont Housing Authority Advisory Board"


def test_dateless_videos_are_assigned_to_the_lyons_meeting_agendas():
    from civiccast_brief.sources import make_source

    town = make_source("civicplus", {"site": "www.townoflyons.com", "bodies": TRUSTEES})
    channel = Source(kind="youtube", bodies=(TRUSTEES,), channel_id="UC" + "a" * 22)
    videos = [
        NS(
            video_id="regular0001",
            title="Regular Board of Trustees Meeting",
            published="2026-11-03T05:00:00Z",
        ),
        NS(
            video_id="budget00001",
            title="Board of Trustees Budget Workshop",
            published="2026-11-04T05:00:00Z",
        ),
    ]
    rows = {
        town.id: [
            _doc(
                TRUSTEES,
                date(2026, 11, 2),
                "Board of Trustees agenda",
                "https://townoflyons.test/agenda/11-02",
            ),
            _doc(
                TRUSTEES,
                date(2026, 11, 3),
                "November 3, 2026 Special Board of Trustees Budget Workshop Agenda",
                "https://townoflyons.test/agenda/11-03",
            ),
        ]
    }
    listers = Listers(
        youtube=lambda cid: videos,
        documents=lambda source, first, last: rows[source.id],
        read_documents=lambda docs: (
            {"documents": [{"url": docs[0].url}]},
            [docs[0].url],
        ),
    )

    found, problems = find_meetings([channel, town], FIRST, LAST, listers)
    by_video = {m.video: m for m in found if m.video}

    assert problems == []
    assert [
        (by_video[video].body, by_video[video].day, by_video[video].reason)
        for video in ("regular0001", "budget00001")
    ] == [
        (TRUSTEES, date(2026, 11, 2), ""),
        (TRUSTEES, date(2026, 11, 3), ""),
    ]
    assert by_video["regular0001"].documents()[0]["documents"] == [{"url": rows[town.id][0].url}]
    assert by_video["budget00001"].documents()[0]["documents"] == [{"url": rows[town.id][1].url}]


def test_a_dateless_video_joins_an_agenda_before_the_window():
    from civiccast_brief.sources import make_source

    town = make_source("civicplus", {"site": "www.townoflyons.com", "bodies": TRUSTEES})
    channel = Source(kind="youtube", bodies=(TRUSTEES,), channel_id="UC" + "a" * 22)
    video = NS(
        video_id="LLLLLLLLLLL",
        title="Regular Board of Trustees Meeting",
        published="2026-11-03T05:00:00Z",
    )
    agenda_day = date(2026, 11, 2)
    window_start = date(2026, 11, 3)
    rows = {
        town.id: [
            _doc(
                TRUSTEES,
                agenda_day,
                "Board of Trustees agenda",
                "https://townoflyons.test/agenda/11-02",
            )
        ]
    }

    def documents(source, first, last):
        return [row for row in rows[source.id] if first <= row.day <= last]

    listers = Listers(
        youtube=lambda cid: [video],
        documents=documents,
        read_documents=lambda docs: ({"documents": [{"url": docs[0].url}]}, []),
    )

    found, problems = find_meetings([channel, town], window_start, date(2026, 11, 16), listers)

    assert problems == []
    assert [(m.video, m.body, m.day, m.reason) for m in found] == [
        ("LLLLLLLLLLL", TRUSTEES, agenda_day, "")
    ]
    assert found[0].documents()[0]["documents"] == [{"url": rows[town.id][0].url}]


def test_a_dateless_video_stays_unrun_when_two_listed_days_remain_ambiguous():
    from civiccast_brief.sources import make_source

    town = make_source("civicplus", {"site": "www.townoflyons.com", "bodies": TRUSTEES})
    channel = Source(kind="youtube", bodies=(TRUSTEES,), channel_id="UC" + "a" * 22)
    video = NS(
        video_id="LLLLLLLLLLL",
        title="Board of Trustees Meeting",
        published="2026-11-03T05:00:00Z",
    )
    rows = {
        town.id: [
            _doc(
                TRUSTEES,
                date(2026, 11, 1),
                "Board of Trustees agenda",
                "https://townoflyons.test/11-01",
            ),
            _doc(
                TRUSTEES,
                date(2026, 11, 2),
                "Board of Trustees minutes",
                "https://townoflyons.test/11-02",
            ),
        ]
    }
    listers = Listers(
        youtube=lambda cid: [video],
        documents=lambda source, first, last: rows[source.id],
        read_documents=lambda docs: ({"documents": []}, []),
    )

    found, _ = find_meetings([channel, town], FIRST, LAST, listers)
    by_video = {m.video: m for m in found if m.video}

    assert (by_video["LLLLLLLLLLL"].day, by_video["LLLLLLLLLLL"].reason) == (
        date(2026, 11, 3),
        "the title names no meeting day",
    )


def test_a_channel_video_with_no_meeting_day_in_its_title_is_not_run():
    """'Senior Citizens Advisory Board - November 2026' went up on 9 November and the meeting run
    failed on it: the run takes the date from the title."""
    channel = Source(
        kind="youtube", bodies=("Senior Citizens Advisory Board",), channel_id="UC" + "a" * 22
    )
    videos = [NS(video_id="GGGGGGGGGGG", title="Senior Citizens Advisory Board - November 2026", published="2026-11-06T00:00:00Z")]  # fmt: skip
    found, _ = find_meetings([channel], FIRST, LAST, Listers(youtube=lambda cid: videos))
    assert [m.reason for m in found] == ["the title names no meeting day"]


def test_a_video_with_no_meeting_day_stays_unrun_when_another_source_names_its_body():
    """Longmont, 13 November 2026: the channel covers only the council, the portal lists the
    Senior Citizens Advisory Board, and the 'November 2026' video ran and failed."""
    senior = "Senior Citizens Advisory Board"
    portal = Source(kind="primegov", bodies=(COUNCIL, senior), url="https://longmont.primegov.com")
    videos = [NS(video_id="GGGGGGGGGGG", title=f"{senior} - November 2026", published="2026-11-06T00:00:00Z")]  # fmt: skip
    listers = Listers(primegov=lambda url, first, last: [], youtube=lambda cid: videos)
    found, _ = find_meetings([portal, CHANNEL], FIRST, LAST, listers)
    assert [(m.body, m.reason) for m in found] == [(senior, "the title names no meeting day")]


def test_a_channel_video_dated_with_an_ordinal_is_run():
    """Nederland, 16 November 2026: six videos were passed over with "the title names no meeting
    day" because the channel writes its dates "November 9th, 2026"."""
    channel = Source(kind="youtube", bodies=(TRUSTEES,), channel_id="UC" + "a" * 22)
    videos = [
        NS(
            video_id="GGGGGGGGGGG",
            title="Board of Trustees Regular Meeting November 9th, 2026 PART 1",
            published="2026-11-10T00:00:00Z",
        )
    ]
    found, _ = find_meetings([channel], FIRST, LAST, Listers(youtube=lambda cid: videos))
    assert [(m.body, m.day, m.reason) for m in found] == [(TRUSTEES, date(2026, 11, 9), "")]


def test_a_live_stream_listed_before_its_meeting_is_not_run_yet():
    """Longmont's channel listed the 17 November council stream on 12 November; the run asked
    for its captions and yt-dlp said the live event would begin in 4 days."""
    videos = [NS(video_id="QgImQ9GxWYo", title="City Council Regular Session - November 17, 2026", published="2026-11-09T00:00:00Z")]  # fmt: skip
    found, _ = find_meetings([CHANNEL], FIRST, LAST, Listers(youtube=lambda cid: videos))
    assert [(m.body, m.reason) for m in found] == [
        (COUNCIL, "the meeting is on 2026-11-17, after this run")
    ]


def test_two_videos_of_one_meeting_run_once():
    """Longmont, 3 and 5 November 2026: the portal linked one council video and the channel
    had another; the preservation commission went up twice. Each made a second story."""
    hpc = "Historic Preservation Commission"
    portal = Source(kind="primegov", bodies=(COUNCIL, hpc), url="https://longmont.primegov.com")
    channel = Source(kind="youtube", bodies=(COUNCIL, hpc), channel_id="UC" + "a" * 22)
    council = _meeting(
        "City Council Regular Session",
        datetime(2026, 11, 3, 18, 0),
        "https://www.youtube.com/watch?v=oWZpzjzjwnE",
    )
    videos = [
        NS(video_id="Q3Cpd52hWa4", title="City Council Regular Session - November 3, 2026", published="2026-11-04T02:00:00Z"),
        NS(video_id="SSSSSSSSSSS", title="City Council Study Session - November 3, 2026", published="2026-11-04T03:00:00Z"),
        NS(video_id="kqArjgcjLF0", title="Historic Preservation Commission - November 5, 2026", published="2026-11-06T00:00:00Z"),
        NS(video_id="yK8bes8CYTM", title="Historic Preservation Commission Nov. 5, 2026 Meeting", published="2026-11-06T01:00:00Z"),
        NS(video_id="P1PPPPPPPPP", title="Historic Preservation Commission - November 5, 2026 Part 2", published="2026-11-06T02:00:00Z"),
    ]  # fmt: skip
    listers = Listers(
        primegov=lambda url, first, last: [council], youtube=lambda cid: videos, city="Longmont"
    )
    found, _ = find_meetings([portal, channel], FIRST, LAST, listers)
    reasons = {m.video: m.reason for m in found}
    assert reasons == {
        "oWZpzjzjwnE": "",
        "Q3Cpd52hWa4": "another video of this meeting runs (oWZpzjzjwnE)",
        "SSSSSSSSSSS": "",
        "kqArjgcjLF0": "",
        "yK8bes8CYTM": "another video of this meeting runs (kqArjgcjLF0)",
        "P1PPPPPPPPP": "",
    }


def test_regular_alone_is_the_plain_meeting_and_the_portal_one_runs():
    """Superior, 26 October 2026: the portal's "Town Council Meeting" and the channel's "Town
    Council Regular Meeting 10/26/26" are one meeting, but were run and published twice."""
    council = "Town Council"
    portal = Source(kind="civicclerk", bodies=(council,), tenant="superiorco")
    channel = Source(
        kind="youtube", bodies=(council, "Planning Commission"), channel_id="UC" + "e" * 22
    )
    meetings = [
        NS(
            video_id="43",
            title="Town Council Meeting",
            published="2026-10-26T00:00:00Z",
            channel_id=council,
            media_url="https://cdn.test/43.mp4",
            captions_url="https://cdn.test/43.vtt",
        )
    ]
    videos = [
        NS(video_id="If5njrlpf7M", title="Town Council Regular Meeting 10/26/26", published="2026-10-27T00:00:00Z"),
        NS(video_id="SPECIAL0001", title="Town Council Special Meeting 10/26/26", published="2026-10-27T00:00:00Z"),
        NS(video_id="WORK0000001", title="Town Council Work Session 10/26/26", published="2026-10-27T00:00:00Z"),
        NS(video_id="STUDY000001", title="Town Council Study Session 10/26/26", published="2026-10-27T00:00:00Z"),
        NS(video_id="EXEC0000001", title="Town Council Executive Session 10/26/26", published="2026-10-27T00:00:00Z"),
        NS(video_id="PART0000002", title="Town Council Meeting - 10/26/26 Part 2", published="2026-10-27T00:00:00Z"),
        NS(video_id="NEXTDAY0001", title="Town Council Regular Meeting 10/27/26", published="2026-10-28T00:00:00Z"),
        NS(video_id="PLANNING001", title="Planning Commission Meeting 10/26/26", published="2026-10-27T00:00:00Z"),
    ]  # fmt: skip
    listers = Listers(
        civicclerk=lambda tenant, first, last: meetings,
        youtube=lambda cid: videos,
        city="Superior",
    )
    found, _ = find_meetings([channel, portal], date(2026, 10, 19), date(2026, 11, 2), listers)
    reasons = {m.video: m.reason for m in found}
    # The channel was read first, but the portal's meeting runs.
    assert reasons["civicclerk-superiorco-43"] == ""
    assert reasons["If5njrlpf7M"] == "another video of this meeting runs (civicclerk-superiorco-43)"
    # Every other kind of sitting, a "Part 2", another body's meeting and another day stay.
    for video in (
        "SPECIAL0001",
        "WORK0000001",
        "STUDY000001",
        "EXEC0000001",
        "PART0000002",
        "NEXTDAY0001",
        "PLANNING001",
    ):
        assert reasons[video] == ""


def test_a_channel_video_that_names_a_listed_body_is_kept_with_no_meeting_keyword():
    """Boulder's channel titles had no meeting keyword, so the Planning Board, the Board of
    Zoning Adjustment and the Landmarks Board were passed over without a word."""
    channel = Source(kind="youtube", bodies=(COUNCIL,), channel_id="UC" + "b" * 22)
    boards = Source(
        kind="webpage", bodies=("Planning Board", "Landmarks Board"), url="https://x.example/b"
    )
    videos = [
        NS(video_id="GGGGGGGGGGG", title="11-03-26 Planning Board Meeting", published="2026-11-04T00:00:00Z"),
        NS(video_id="HHHHHHHHHHH", title="11-04-26 Landmarks Board Meeting", published="2026-11-05T00:00:00Z"),
        NS(video_id="IIIIIIIIIII", title="Inside Boulder News - November 6, 2026", published="2026-11-06T00:00:00Z"),
    ]  # fmt: skip
    listers = Listers(youtube=lambda cid: videos, documents=lambda s, f, l: [])
    found, _ = find_meetings([channel, boards], FIRST, LAST, listers)
    assert [(m.video, m.body, m.reason) for m in found] == [
        ("GGGGGGGGGGG", "Planning Board", ""),
        ("HHHHHHHHHHH", "Landmarks Board", ""),
    ]


def test_a_one_body_channel_title_that_names_only_the_sitting_is_that_body():
    """The school board's channel titles its meetings "November 10, 2026 Regular Meeting"."""
    board = Source(kind="youtube", bodies=("Board of Education",), channel_id="UC" + "c" * 22)
    videos = [
        NS(video_id="JJJJJJJJJJJ", title="November 3, 2026 Regular Meeting", published="2026-11-04T00:00:00Z"),
        NS(video_id="KKKKKKKKKKK", title="October 28, 2026 Work Session", published="2026-10-29T00:00:00Z"),
        NS(video_id="LLLLLLLLLLL", title="3 de noviembre - Reunión del Consejo de Educación", published="2026-11-04T00:00:00Z"),
    ]  # fmt: skip
    found, _ = find_meetings([board], FIRST, LAST, Listers(youtube=lambda cid: videos))
    assert [(m.video, m.body) for m in found] == [
        ("KKKKKKKKKKK", "Board of Education"),
        ("JJJJJJJJJJJ", "Board of Education"),
    ]
    # A channel of two bodies cannot tell whose "Regular Meeting" it is; with no meeting
    # keyword either, it is passed over as before.
    two = Source(kind="youtube", bodies=(COUNCIL, PZ), channel_id="UC" + "c" * 22)
    found, _ = find_meetings([two], FIRST, LAST, Listers(youtube=lambda cid: videos[:1]))
    assert found == []


def test_match_body_reads_a_body_written_another_way():
    bodies = [
        "Water Board",
        "Planning and Zoning Commission",
        "Callahan House Board",
        "Art in Public Places Commission",
        "Housing and Human Services Advisory Board",
        "Library Advisory Board",
        "Longmont Housing Authority Advisory Board",
        "Longmont Housing Authority Board of Commissioners",
    ]
    city = "Longmont"
    assert match_body("Water Advisory Board - October 2026", bodies, city) == "Water Board"
    assert match_body("Longmont Planning & Zoning - Regular Session", bodies, city) == bodies[1]
    assert (
        match_body("Callahan House Advisory Board Meeting, Oct. 14, 2026", bodies, city)
        == bodies[2]
    )
    assert match_body("Arts in Public Places Commission - October 15", bodies, city) == bodies[3]
    assert (
        match_body("Art in Public Places Board Meeting, Oct. 15, 2026", bodies, city) == bodies[3]
    )
    assert match_body("Housing and Human Service Advisory Board", bodies, city) == bodies[4]
    hearing = "Housing and Human Services Advisory Education Hearing Oct. 22, 2026"
    assert match_body(hearing, bodies, city) == bodies[4]
    assert match_body("Library Board", bodies, city) == bodies[5]
    # Two bodies with one core: neither.
    assert match_body("Longmont Housing Authority Board - November 2026", bodies, city) == ""
    # One core word must match whole: "Water Resources" is not "Water".
    assert match_body("Water Resources Advisory Board", bodies, city) == ""


def test_match_body_reads_a_body_by_its_initials():
    bodies = ["Board of Trustees", "Planning & Community Development Commission"]
    assert match_body("August 24, 2026 PCDC Meeting", bodies) == bodies[1]
    assert match_body("August 10, 2026, PCDC & BOT Joint Meeting", bodies) == bodies[1]
    assert match_body("BOT Regular Meeting", bodies) == bodies[0]
    assert match_body("pcdc meeting", bodies) == ""  # initials are capitals


def test_two_videos_of_one_meeting_titled_two_ways_make_one_episode():
    """Longmont posted its 15 October Art in Public Places meeting twice, once as "Board"."""
    body = "Art in Public Places Commission"
    channel = Source(kind="youtube", bodies=(body,), channel_id="UC" + "d" * 22)
    videos = [
        NS(video_id="MMMMMMMMMMM", title="Arts in Public Places Commission - November 5, 2026", published="2026-11-06T00:00:00Z"),
        NS(video_id="NNNNNNNNNNN", title="Art in Public Places Board Meeting, Nov. 5, 2026", published="2026-11-07T00:00:00Z"),
    ]  # fmt: skip
    found, _ = find_meetings([channel], FIRST, LAST, Listers(youtube=lambda cid: videos))
    assert [(m.video, m.body) for m in found if not m.reason] == [("MMMMMMMMMMM", body)]
    assert [m.reason for m in found if m.reason] == [
        "another video of this meeting runs (MMMMMMMMMMM)"
    ]


NO_PORTAL_VIDEO = "the portal lists no video for this meeting"


def _boulder_portal():
    return Source(kind="primegov", bodies=(COUNCIL,), url="https://boulder.primegov.com")


def _special_portal(
    video=None, title="City Council Special Meeting", date_=datetime(2026, 11, 5, 18, 0)
):
    return _meeting(title, date_, video)


def _special_video(video_id="n8OxJlE_2ME", title="November 5, 2026 City Council Special Meeting"):
    return NS(video_id=video_id, title=title, published="2026-11-06T01:00:00Z")


def test_a_portal_meeting_with_no_video_gives_its_agenda_and_documents_to_the_same_days_video():
    """Boulder's 12 Nov 2026 PrimeGov entry had the agenda and packet but no video link; the
    channel carried the same meeting. They must be one meeting, with the records."""
    agenda = Agenda("https://boulder.primegov.com/agenda/1", ("1. Call to order",))
    portal = _special_portal()
    video = _special_video()

    def documents(url, meeting, body):
        return {"format": 1, "documents": [{"url": "https://boulder.test/packet"}]}, [
            "packet: 323 pages"
        ]

    listers = Listers(
        primegov=lambda url, first, last: [portal],
        primegov_agenda=lambda url, m: agenda,
        primegov_documents=documents,
        youtube=lambda cid: [video],
    )
    found, problems = find_meetings([_boulder_portal(), CHANNEL], FIRST, LAST, listers)

    assert problems == []
    assert len(found) == 1
    (meeting,) = found
    assert meeting.video == "n8OxJlE_2ME"
    assert meeting.reason == ""
    assert meeting.agenda == agenda
    assert meeting.documents() == (
        {"format": 1, "documents": [{"url": "https://boulder.test/packet"}]},
        ["packet: 323 pages"],
    )
    assert all(m.reason != NO_PORTAL_VIDEO for m in found)


def test_same_day_different_sessions_stay_apart_when_a_portal_meeting_has_no_video():
    sessions = [
        ("City Council Study Session", "City Council Regular Session"),
        ("City Council Special Meeting", "City Council Regular Session"),
        ("City Council Meeting Part 1", "City Council Meeting Part 2"),
    ]
    for portal_title, video_title in sessions:
        portal = _special_portal(title=portal_title)
        video = _special_video(title=f"November 5, 2026 {video_title}")
        found, _ = find_meetings(
            [_boulder_portal(), CHANNEL],
            FIRST,
            LAST,
            Listers(
                primegov=lambda url, f, l, portal=portal: [portal],
                youtube=lambda cid, video=video: [video],
            ),
        )
        assert [m.reason for m in found if m.reason] == [NO_PORTAL_VIDEO], (portal_title, found)
        assert [m.video for m in found if not m.reason] == ["n8OxJlE_2ME"]


def test_two_videos_that_could_match_keep_the_portal_meeting_unrun():
    """Two uploads of one title: the portal's records are not guessed onto either."""
    portal = _special_portal()
    videos = [
        _special_video("n8OxJlE_2ME"),
        _special_video("AAAAAAAAAAA"),
    ]
    listers = Listers(
        primegov=lambda url, first, last: [portal],
        primegov_documents=lambda url, m, body: ({"format": 1, "documents": []}, []),
        youtube=lambda cid: videos,
    )
    found, _ = find_meetings([_boulder_portal(), CHANNEL], FIRST, LAST, listers)

    assert NO_PORTAL_VIDEO in {m.reason for m in found}
    assert all(not m.documents for m in found if not m.reason)


def test_a_cancelled_portal_meeting_gives_nothing_to_a_video():
    portal = _special_portal(title="CANCELLED: City Council Special Meeting")
    video = _special_video()
    listers = Listers(
        primegov=lambda url, first, last: [portal],
        primegov_documents=lambda url, m, body: ({"format": 1, "documents": []}, []),
        youtube=lambda cid: [video],
    )
    found, _ = find_meetings([_boulder_portal(), CHANNEL], FIRST, LAST, listers)

    assert {(m.video, m.reason) for m in found} == {
        ("n8OxJlE_2ME", ""),
        ("", "the meeting was cancelled"),
    }
    video_entry = next(m for m in found if m.video)
    assert video_entry.documents is None
    assert all(m.reason != NO_PORTAL_VIDEO for m in found)


def test_a_video_that_already_has_documents_keeps_them_beside_the_portals():
    portal = _special_portal()
    own = _special_portal(video="https://www.youtube.com/watch?v=n8OxJlE_2ME")
    asked = {}

    def documents(url, meeting, body):
        asked[meeting.title] = True
        if meeting is portal:
            return {"format": 1, "documents": [{"url": "https://boulder.test/packet"}]}, ["packet"]
        return {"format": 1, "documents": [{"url": "https://boulder.test/minutes"}]}, ["minutes"]

    listers = Listers(
        primegov=lambda url, first, last: [own, portal],
        primegov_documents=documents,
    )
    found, _ = find_meetings([_boulder_portal()], FIRST, LAST, listers)

    assert len(found) == 1
    data, lines = found[0].documents()
    assert [d["url"] for d in data["documents"]] == [
        "https://boulder.test/minutes",
        "https://boulder.test/packet",
    ]
    assert lines == ["minutes", "packet"]


def test_two_portal_entries_for_one_video_keep_both_sets_of_records():
    """Two portal entries can match one video. The second join must chain onto the records the
    first added, not onto the snapshot taken before the loop."""
    first = _special_portal(title="City Council Special Meeting")
    second = _special_portal(title="City Council Special")
    video = _special_video()

    def documents(url, meeting, body):
        if meeting is first:
            return {"format": 1, "documents": [{"url": "https://boulder.test/one"}]}, ["one"]
        return {"format": 1, "documents": [{"url": "https://boulder.test/two"}]}, ["two"]

    listers = Listers(
        primegov=lambda url, f, last: [first, second],
        primegov_documents=documents,
        youtube=lambda cid: [video],
    )
    found, _ = find_meetings([_boulder_portal(), CHANNEL], FIRST, LAST, listers)

    assert len(found) == 1
    data, lines = found[0].documents()
    assert [d["url"] for d in data["documents"]] == [
        "https://boulder.test/one",
        "https://boulder.test/two",
    ]
    assert lines == ["one", "two"]


def test_chain_of_no_fetch_and_one_fetch_is_the_one_fetch():
    """A join with nothing on one side must not wrap the other side's fetch."""
    from civiccast_brief.weekly.find import _chain

    def fetch():
        return {"format": 1, "documents": [{"url": "x"}]}, ["x"]

    assert _chain(None, fetch) is fetch
    assert _chain(fetch, None) is fetch
    assert _chain(None, None) is None


def test_a_portal_meeting_with_no_video_joins_the_same_video_in_any_source_order():
    portal = _special_portal()
    video = _special_video()
    listers = Listers(
        primegov=lambda url, first, last: [portal],
        primegov_agenda=lambda url, m: Agenda("https://boulder.test/agenda", ("1. Call to order",)),
        primegov_documents=lambda url, m, body: ({"format": 1, "documents": [{"url": "p"}]}, []),
        youtube=lambda cid: [video],
    )
    results = []
    for sources in ([_boulder_portal(), CHANNEL], [CHANNEL, _boulder_portal()]):
        found, _ = find_meetings(sources, FIRST, LAST, listers)
        results.append(
            [
                (m.key, m.reason, m.agenda is not None, m.documents() if m.documents else None)
                for m in found
            ]
        )
    assert results[0] == results[1]
