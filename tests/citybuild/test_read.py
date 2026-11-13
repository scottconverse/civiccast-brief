"""Reading one recent meeting of a body, from each kind of source."""

from __future__ import annotations

from datetime import date

from civiccast_brief.citybuild.read import body_matches, committee_for, read_latest
from civiccast_brief.sources import make_source

from .conftest import (
    BOCC_1105,
    BOULDER_COUNTY,
    CHANNEL,
    ERIE,
    FLAGSTAFF,
    LAFAYETTE,
    LOVELAND,
    LYONS,
    PRIMEGOV,
    SVCM,
    SVCM_NEWEST,
    SVVSD,
    SVVSD_180,
    SVVSD_PACKET,
    TPCA_ROOT,
    add_boulder_county,
    add_erie,
    add_loveland,
    add_primegov,
    add_tpca_drive,
    feed,
    gdoc_export,
    text_pdf,
)

TODAY = date(2026, 11, 10)


def test_body_names_match_with_or_without_the_part_in_parentheses():
    assert body_matches(
        "Longmont Urban Renewal Authority (LURA)", "LURA and Longmont Urban Renewal Authority"
    )
    assert not body_matches("Water Board", "Board of Water Commissioners")
    rows = [(25, "Water Board"), (48, "Longmont Urban Renewal Authority (LURA)")]
    assert committee_for("Longmont Urban Renewal Authority", rows) == 48
    assert committee_for("water board", rows) == 25
    assert committee_for("Library Advisory Board", rows) is None


def test_a_title_may_leave_off_the_city_name_and_the_word_meeting():
    body = "LOVELAND CITY COUNCIL MEETING"
    for title in (
        "City Council Meeting November 4, 2026",
        "City Council - Oct 20 2026",
        "City Council 11-10-2026",
        "City Council",
    ):
        assert body_matches(body, title, "Loveland"), title
    assert body_matches(
        "Loveland Planning Commission Meeting", "Planning Commission Nov 9, 2026", "Loveland"
    )
    assert body_matches(
        "City of Loveland Planning Commission", "Planning Commission Nov 9, 2026", "Loveland"
    )
    # Another board, a longer name, or a name cut to one word is not a match.
    assert not body_matches(body, "Council Study Session 11-10-2026", "Loveland")
    assert not body_matches(body, "City Council Study Session November 4, 2026", "Loveland")
    assert not body_matches(body, "City Council Marketing Plan", "Loveland")
    assert not body_matches(
        "Loveland Council Meeting", "Council Study Session 11-10-2026", "Loveland"
    )
    assert not body_matches(
        "Loveland Planning Commission", "Historic Preservation Commission", "Loveland"
    )
    assert not body_matches(
        "Planning Commission Meeting", "Historic Preservation Commission Nov 4 2026"
    )
    # Without the city's name only the trailing word goes.
    assert not body_matches(body, "City Council Meeting November 4, 2026")
    assert body_matches("City Council Meeting", "City Council - Oct 20 2026")


def test_primegov_reads_the_newest_past_meetings_html_agenda(web):
    add_primegov(web)
    # The same meeting's PDF agenda answers too: the HTML agenda is still the one read.
    web.add(
        PRIMEGOV + "/Public/CompiledDocument?meetingTemplateId=16826&compileOutputType=1",
        text_pdf("City Council Study Session", "Agenda", "September 29, 2026"),
        "application/pdf",
    )
    source = make_source("primegov", {"url": PRIMEGOV, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=date(2026, 9, 30))
    assert proof.ok, proof.problem
    # The 29 September executive session has only a PDF agenda that is not saved here; the study
    # session the same evening has the HTML agenda, so the reader takes that one.
    assert proof.meeting_date == date(2026, 9, 29)
    assert proof.what == "agenda"
    assert proof.document == PRIMEGOV + "/Portal/Meeting?meetingTemplateId=16826"
    assert proof.lines[0].endswith("agenda items")
    assert int(proof.lines[0].split()[0]) > 3


def test_primegov_skips_cancelled_and_future_meetings(web):
    add_primegov(web)
    source = make_source("primegov", {"url": PRIMEGOV, "bodies": ["Golf Course Advisory Board"]})
    proof = read_latest(
        source, "Golf Course Advisory Board", web.fetcher(), today=date(2026, 9, 30)
    )
    assert not proof.ok
    assert "no meeting of committee 12 on or before 2026-09-30 in 2015-2026" in proof.problem
    hpc = read_latest(
        source, "Historic Preservation Commission", web.fetcher(), today=date(2026, 9, 30)
    )
    # 1 October is after the day given, so the January meeting (a PDF agenda not saved here) is the
    # one tried, and its failure is named.
    assert not hpc.ok
    assert "2026-01-01" in hpc.problem


def test_primegov_looks_back_years_for_a_board_that_last_met_before_last_year(web):
    """Longmont's Marijuana Licensing Authority last met on 12 July 2024 (portal read 2026-11-11).
    The 2024 archive is the first year back with a meeting, so earlier years are not asked."""
    add_primegov(web)
    web.fixture(
        PRIMEGOV + "/api/v2/PublicPortal/ListArchivedMeetings?year=2024",
        "primegov-archived-2024-mla.json",
        "application/json",
    )
    agenda = PRIMEGOV + "/Public/CompiledDocument?meetingTemplateId=12805&compileOutputType=1"
    web.fixture(agenda, "primegov-mla-agenda-2024-07-12.pdf", "application/pdf")
    source = make_source("primegov", {"url": PRIMEGOV, "bodies": ["Marijuana Licensing Authority"]})
    proof = read_latest(
        source, "Marijuana Licensing Authority", web.fetcher(), today=date(2026, 11, 11)
    )
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2024, 7, 12)
    assert proof.title == "Marijuana Licensing Authority Hearing"
    assert proof.what == "agenda"
    assert proof.document == agenda
    assert proof.lines[1:3] == ["1", "MARIJUANA LICENSING AUTHORITY (MLA)"]
    assert not any("year=2023" in url for url in web.asked)


def test_primegov_names_a_body_the_portal_does_not_list(web):
    add_primegov(web)
    source = make_source("primegov", {"url": PRIMEGOV, "bodies": ["Visit Longmont"]})
    proof = read_latest(source, "Visit Longmont", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert "no committee named 'Visit Longmont'" in proof.problem


def test_a_page_of_dated_documents_reads_the_newest(web):
    web.fixture(SVCM, "svcm-board-meetings.html")
    web.add(
        gdoc_export(SVCM_NEWEST),
        "St. Vrain Community Montessori\nBoard Meeting Minutes\nSeptember 10, 2026\nCall to order",
        "text/plain",
    )
    source = make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]})
    proof = read_latest(source, "SVCM Board", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 9, 10)
    assert proof.what == "minutes"
    assert proof.lines[0] == "first date in it: 2026-09-10"
    assert "Board Meeting Minutes" in proof.lines


ORDER_PAGE = "https://school.test/board"


def test_the_newest_dated_document_is_read_and_its_agenda_before_its_minutes(web):
    web.add(
        ORDER_PAGE,
        '<a href="/a/2026-08-01-minutes.pdf">Minutes August 1, 2026</a>'
        '<a href="/a/2026-10-02-minutes.pdf">Minutes October 2, 2026</a>'
        '<a href="/a/2026-10-02-agenda.pdf">Agenda October 2, 2026</a>'
        '<a href="/a/2026-12-01-agenda.pdf">Agenda December 1, 2026</a>',
    )
    for name in (
        "2026-08-01-minutes",
        "2026-10-02-minutes",
        "2026-10-02-agenda",
        "2026-12-01-agenda",
    ):
        web.add(f"https://school.test/a/{name}.pdf", text_pdf(name), "application/pdf")
    source = make_source("webpage", {"url": ORDER_PAGE, "bodies": ["Board"]})
    proof = read_latest(source, "Board", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.document == "https://school.test/a/2026-10-02-agenda.pdf"
    assert proof.meeting_date == date(2026, 10, 2)


def test_meeting_pages_are_followed_newest_first(web):
    web.add(
        ORDER_PAGE,
        '<a href="/meetings/one/">Board Meeting September 3, 2026</a>'
        '<a href="/meetings/two/">Board Meeting October 8, 2026</a>'
        '<a href="/other/x/">Concert October 9, 2026</a>',
    )
    for name in ("one", "two"):
        web.add(
            f"https://school.test/meetings/{name}/",
            f'<a href="/d/{name}-agenda.pdf">Agenda</a>',
        )
        web.add(
            f"https://school.test/d/{name}-agenda.pdf",
            text_pdf(f"Agenda {name}"),
            "application/pdf",
        )
    source = make_source(
        "webpage", {"url": ORDER_PAGE, "follow": "/meetings/", "bodies": ["Board"]}
    )
    proof = read_latest(source, "Board", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.document == "https://school.test/d/two-agenda.pdf"
    assert proof.meeting_date == date(2026, 10, 8)


def test_a_pdf_is_read_with_its_page_count(web):
    web.fixture(FLAGSTAFF, "flagstaff-board-documents.html")
    pdf = text_pdf("Flagstaff Academy Board of Directors", "Meeting Agenda", "November 3, 2026")
    for url in [u for u in _flagstaff_documents()]:
        web.add(url, pdf, "application/pdf")
    source = make_source("webpage", {"url": FLAGSTAFF, "bodies": ["Flagstaff Academy Board"]})
    proof = read_latest(source, "Flagstaff Academy Board", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 11, 3)
    assert proof.what == "agenda"
    assert proof.lines[0] == "1 pages, first date in it: 2026-11-03"


def _flagstaff_documents() -> list[str]:
    from civiccast_brief.citybuild.documents import document_links
    from civiccast_brief.citybuild.page import parse

    from .conftest import HERE

    text = (HERE / "flagstaff-board-documents.html").read_text(encoding="utf-8")
    return [link.url for link in document_links(parse(text, FLAGSTAFF)[1])]


def test_a_sign_in_page_is_not_taken_for_the_document(web):
    web.fixture(SVCM, "svcm-board-meetings.html")
    source = make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]})
    for url in _svcm_exports():
        web.add(url, "<html>Sign in</html>")
    proof = read_latest(source, "SVCM Board", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert "may need a sign-in" in proof.problem


def _svcm_exports() -> list[str]:
    from civiccast_brief.citybuild.documents import document_links, download_url
    from civiccast_brief.citybuild.page import parse

    from .conftest import HERE

    text = (HERE / "svcm-board-meetings.html").read_text(encoding="utf-8")
    return [download_url(link.url) for link in document_links(parse(text, SVCM)[1])]


def test_a_list_of_meeting_pages_is_followed_to_the_agenda(web):
    web.fixture(SVVSD, "svvsd-2026-board-meetings.html")
    web.fixture(SVVSD_180, "svvsd-meeting-180.html")
    web.add(
        SVVSD_PACKET,
        text_pdf("Board of Education Regular Meeting", "November 11, 2026", "Agenda"),
        "application/pdf",
    )
    source = make_source(
        "webpage", {"url": SVVSD, "follow": "/boardmeetings/", "bodies": ["Board of Education"]}
    )
    proof = read_latest(source, "Board of Education", web.fetcher(), today=date(2026, 11, 11))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 11, 11)
    assert proof.document == SVVSD_PACKET
    assert proof.title == "Board of Education Regular Meeting"


def test_a_followed_page_that_will_not_open_is_named(web):
    web.fixture(SVVSD, "svvsd-2026-board-meetings.html")
    source = make_source(
        "webpage", {"url": SVVSD, "follow": "/boardmeetings/", "bodies": ["Board of Education"]}
    )
    proof = read_latest(source, "Board of Education", web.fetcher(), today=date(2026, 11, 11))
    assert not proof.ok
    assert "could not be read" in proof.problem
    assert proof.problem.count(";") == 3  # four meeting pages tried, newest first


def test_a_drive_folder_is_walked_to_the_newest_agenda(web):
    add_tpca_drive(web)
    # The service ticket is newer and readable, but an agenda comes before any other file.
    web.add(
        "https://drive.google.com/uc?export=download&id=1qWbdBErR5yZtmQqR-I9Ue5ZUQgtYvehP",
        text_pdf("Service Ticket 125746", "November 6, 2026"),
        "application/pdf",
    )
    source = make_source("gdrive", {"folder_id": TPCA_ROOT, "bodies": ["TPCA Board"]})
    proof = read_latest(source, "TPCA Board", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.title == "November Agenda"
    assert proof.meeting_date == date(2026, 11, 5)
    assert proof.what == "agenda"
    assert proof.lines[0] == "Drive says modified 2026-11-05"
    assert any("Twin Peaks Classical Academy" in line for line in proof.lines)


def test_youtube_without_captions_names_the_newest_matching_video(web):
    web.add(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
        feed(
            ("aaaaaaaaaaa", "City Council Regular Session 11/18/2026", "2026-11-18T03:00:00+00:00"),
            ("bbbbbbbbbbb", "City Council Regular Session 10/28/2026", "2026-10-29T03:00:00+00:00"),
            (
                "ccccccccccc",
                "Planning and Zoning Commission 11/5/2026",
                "2026-11-06T03:00:00+00:00",
            ),
        ),
        "application/atom+xml",
    )
    source = make_source("youtube", {"channel_id": CHANNEL, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=TODAY)
    assert proof.ok
    assert proof.document == "https://www.youtube.com/watch?v=bbbbbbbbbbb"
    # The meeting day is the one in the title, not the day the video went up.
    assert proof.meeting_date == date(2026, 10, 28)
    assert proof.what.startswith("video (captions not fetched")
    missing = read_latest(source, "Water Board", web.fetcher(), today=TODAY)
    assert not missing.ok and "names 'Water Board'" in missing.problem


def test_a_page_that_does_not_answer_is_a_problem_not_a_crash(web):
    source = make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]})
    proof = read_latest(source, "SVCM Board", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert "HTTP 404" in proof.problem
    assert proof.report() == [
        f"NOT READ  SVCM Board (webpage, {SVCM})",
        f"  why: {SVCM} answered HTTP 404",
    ]


def test_a_pdf_with_a_broken_index_is_read_without_flooding_the_output(web, caplog):
    import logging

    from civiccast_brief.citybuild.documents import read_document

    pdf = text_pdf("Water Board", "Agenda", "October 21, 2026")
    broken = pdf.replace(b"startxref\n", b"startxref\n9", 1)  # pypdf warns, then rebuilds it
    web.add("https://school.test/broken.pdf", broken, "application/pdf")
    with caplog.at_level(logging.WARNING):
        got = read_document("https://school.test/broken.pdf", web.fetcher())
    assert got.first_date == date(2026, 10, 21)
    assert [r.getMessage() for r in caplog.records if r.name.startswith("pypdf")] == []


def test_a_followed_list_reads_a_meeting_before_a_notice_and_skips_cancelled_ones(web):
    base = "https://www.svvsd.org/boardmeetings/"
    events = base + "board-of-education-member-events-for-november-10/"
    web.fixture(SVVSD, "svvsd-2026-board-meetings.html")
    # The monthly notice of events board members attend is newer and has a readable PDF.
    web.add(events, '<html><a href="https://www.svvsd.org/n.pdf">November events</a></html>')
    web.add(
        "https://www.svvsd.org/n.pdf",
        text_pdf("Member events", "November 1, 2026"),
        "application/pdf",
    )
    web.fixture(base + "board-of-education-regular-meeting-179/", "svvsd-meeting-180.html")
    web.add(
        SVVSD_PACKET,
        text_pdf("Board of Education Regular Meeting", "October 28, 2026", "Agenda"),
        "application/pdf",
    )
    source = make_source(
        "webpage", {"url": SVVSD, "follow": "/boardmeetings/", "bodies": ["Board of Education"]}
    )
    proof = read_latest(source, "Board of Education", web.fetcher(), today=date(2026, 11, 10))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 28)
    assert proof.title == "Board of Education Regular Meeting"
    assert events not in web.asked
    assert not any("study-session-119" in url for url in web.asked)  # cancelled on the list


# ----- CivicClerk, eScribe and CivicPlus (fixtures trimmed from the live sites, 2026-11-10) ------


def _clerk_file(file_id: int) -> str:
    from civiccast_brief.citybuild import platforms

    return platforms.clerk_file_url(ERIE, file_id)


def test_civicclerk_reads_the_newest_agenda_of_the_body(web):
    add_erie(web, TODAY)
    web.add(
        _clerk_file(2197), text_pdf("Town Council Meeting", "November 10, 2026"), "application/pdf"
    )
    source = make_source("civicclerk", {"tenant": ERIE, "bodies": ["Town Council"]})
    proof = read_latest(source, "Town Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert (proof.meeting_date, proof.what) == (date(2026, 11, 10), "agenda")
    assert proof.document == _clerk_file(2197)
    assert proof.title == "Town Council Meeting (Town Council)"


def test_civicclerk_skips_cancelled_and_future_meetings(web):
    # Asked on 9 November, a portal that still lists the 10 November meeting has it skipped; the
    # 3 November study session has no files, so 27 October is the newest Town Council agenda.
    today = date(2026, 11, 9)
    add_erie(web, today)
    web.add(
        _clerk_file(2179), text_pdf("Town Council Meeting", "October 27, 2026"), "application/pdf"
    )
    web.add(
        _clerk_file(2162), text_pdf("Joint Study Session", "October 21, 2026"), "application/pdf"
    )
    council = make_source("civicclerk", {"tenant": ERIE, "bodies": ["Town Council"]})
    proof = read_latest(council, "Town Council", web.fetcher(), today=today)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 27)
    # The 4 November Planning Commission meeting was CANCELED and has no agenda.
    planning = make_source("civicclerk", {"tenant": ERIE, "bodies": ["Planning Commission"]})
    proof = read_latest(planning, "Planning Commission", web.fetcher(), today=today)
    assert proof.ok, proof.problem
    assert (proof.meeting_date, proof.document) == (date(2026, 10, 21), _clerk_file(2162))


def test_civicclerk_follows_the_apis_next_page_links_to_an_older_meeting(web):
    """Erie's API answers 15 meetings at a time with a link to the next 15 (saved on 2026-11-11;
    the fourth page's own next link is cut off). The Board of Adjustment/Board of Appeals last
    met on 22 July 2026, on the fourth page."""
    import json

    from civiccast_brief.citybuild import platforms

    from .conftest import HERE

    today = date(2026, 11, 11)
    url = platforms.clerk_events_url(ERIE, today)
    for n in (1, 2, 3, 4):
        name = f"civicclerk-erie-events-2026-11-11-page{n}.json"
        web.fixture(url, name, "application/json")
        url = json.loads((HERE / name).read_text(encoding="utf-8")).get("@odata.nextLink")
    web.add(
        _clerk_file(1051),
        text_pdf("Board of Adjustment/Board of Appeals", "July 22, 2026"),
        "application/pdf",
    )
    body = "Board of Adjustment/Board of Appeals"
    source = make_source("civicclerk", {"tenant": ERIE, "bodies": [body]})
    proof = read_latest(source, body, web.fetcher(), today=today)
    assert proof.ok, proof.problem
    assert (proof.meeting_date, proof.document) == (date(2026, 7, 22), _clerk_file(1051))
    assert len([u for u in web.asked if "/Events?" in u]) == 4


def test_escribe_reads_the_newest_past_html_agenda(web):
    add_boulder_county(web)
    today = date(2026, 11, 6)
    source = make_source(
        "escribe", {"tenant": BOULDER_COUNTY, "bodies": ["Board of County Commissioners"]}
    )
    proof = read_latest(source, "Board of County Commissioners", web.fetcher(), today=today)
    assert proof.ok, proof.problem
    # The 10 November meeting is after "today" and is skipped.
    assert (proof.meeting_date, proof.what) == (date(2026, 11, 5), "agenda")
    assert proof.title == "Board of County Commissioners - Regular Meeting - November 05, 2026"
    assert proof.lines == [
        "13 agenda items",
        "1. Call to Order",
        "2. 9:00 a.m. Morning Session",
        "3. Business Meeting",
    ]


def test_escribe_skips_a_cancelled_meeting(web):
    import json

    from civiccast_brief.citybuild import platforms

    from .conftest import HERE

    add_boulder_county(web)
    calendar = json.loads((HERE / "escribe-bouldercounty-calendar.json").read_text("utf-8"))
    for row in calendar["d"]:
        if row["StartDate"].startswith("2026/11/05"):
            row["MeetingName"] = "CANCELLED - " + row["MeetingName"]
    web.add(
        platforms.escribe_base(BOULDER_COUNTY) + platforms.ESCRIBE_CALENDAR,
        json.dumps(calendar),
        "application/json",
    )
    source = make_source("escribe", {"tenant": BOULDER_COUNTY, "bodies": ["BOCC"]})
    proof = read_latest(
        source, "Board of County Commissioners", web.fetcher(), today=date(2026, 11, 6)
    )
    # The 27 October pages are not saved, so nothing reads, and 5 November was never asked for.
    assert not proof.ok
    assert proof.problem.startswith("2026-10-27: ")
    assert not any(BOCC_1105 in url for url in web.asked)


def test_civicplus_reads_a_meeting_agenda_before_a_newer_notice(web):
    web.fixture(f"https://{LYONS}/AgendaCenter", "civicplus-lyons-agendacenter.html")
    workshop = f"https://{LYONS}/AgendaCenter/ViewFile/Agenda/_11032026-2871"
    web.add(workshop, text_pdf("Special Budget Workshop", "November 3, 2026"), "application/pdf")
    source = make_source("civicplus", {"site": LYONS, "bodies": ["Board of Trustees"]})
    proof = read_latest(source, "Board of Trustees", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    # The 10 November "Public Hearing Notice" is newer, and the 16 November notice is in the
    # future; the 3 November workshop agenda is read.
    assert (proof.meeting_date, proof.document) == (date(2026, 11, 3), workshop)
    assert proof.title == "November 3, 2026 Special Board of Trustees Budget Workshop Agenda"
    assert not any("_11102026" in url or "_11162026" in url for url in web.asked)


def test_civicplus_falls_back_to_the_archive_center(web):
    # Lafayette has no Agenda Center (404); its Archive Center lists the council's packets. The
    # 2 November packet does not open, so the 27 October workshop is read.
    web.fixture(f"https://{LAFAYETTE}/Archive.aspx", "civicplus-lafayette-archive.html")
    workshop = f"https://{LAFAYETTE}/ArchiveCenter/ViewFile/Item/8168"
    web.add(workshop, text_pdf("City Council Workshop", "October 27, 2026"), "application/pdf")
    source = make_source("civicplus", {"site": LAFAYETTE, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert (proof.meeting_date, proof.document) == (date(2026, 10, 27), workshop)
    assert f"https://{LAFAYETTE}/ArchiveCenter/ViewFile/Item/8182" in web.asked
    # The Spanish copy ("Nobiembre 02 del 2026", item 8185) has no English date and is not read.
    assert not any(url.endswith("/8185") for url in web.asked)


def test_civicplus_names_where_it_looked_when_nothing_matches(web):
    web.fixture(f"https://{LAFAYETTE}/Archive.aspx", "civicplus-lafayette-archive.html")
    source = make_source("civicplus", {"site": LAFAYETTE, "bodies": ["Library Board"]})
    proof = read_latest(source, "Library Board", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert proof.problem.startswith("/AgendaCenter did not answer; /Archive.aspx lists ")
    assert proof.problem.endswith("none for 'Library Board'")


def test_granicus_reads_the_newest_past_clip_of_the_body(web, monkeypatch):
    from civiccast_brief.meeting import granicus

    host = "townofsuperior.granicus.com"
    clips = [
        granicus.Clip(host, 13, 901, "Planning Commission", "December 16, 2025"),
        granicus.Clip(host, 13, 950, "Planning Commission", "October 6, 2026"),
        granicus.Clip(host, 13, 960, "Planning Commission", "December 1, 2026"),
        granicus.Clip(host, 13, 940, "Town Board", "October 20, 2026"),
    ]
    monkeypatch.setattr(granicus, "list_clips", lambda h, v: clips)
    source = make_source(
        "granicus", {"host": host, "view_id": 13, "bodies": ["Planning Commission"]}
    )
    proof = read_latest(source, "Planning Commission", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    # The December 2026 clip is after today, and "December" sorts after "October" as text: the
    # newest past clip is still the October one.
    assert proof.meeting_date == date(2026, 10, 6)
    assert proof.document == granicus.video_url(host, 13, 950)


def test_a_livestream_set_up_weeks_ahead_is_dated_by_its_title(web):
    # BVSD sets up its board livestreams weeks ahead: the December meeting's video went up in
    # late October, after the October meeting: its publish date says nothing about when it meets.
    web.add(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
        feed(
            (
                "ddddddddddd",
                "December 8, 2026 Board of Education Meeting",
                "2026-10-30T18:00:00+00:00",
            ),
            (
                "eeeeeeeeeee",
                "October 27, 2026 Board of Education Meeting",
                "2026-10-28T03:00:00+00:00",
            ),
            ("fffffffffff", "Board of Education Study Session", "2026-10-01T03:00:00+00:00"),
        ),
        "application/atom+xml",
    )
    source = make_source("youtube", {"channel_id": CHANNEL, "bodies": ["Board of Education"]})
    proof = read_latest(source, "Board of Education", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.document == "https://www.youtube.com/watch?v=eeeeeeeeeee"
    assert proof.meeting_date == date(2026, 10, 27)


# ----- CivicWeb (fixtures trimmed from Loveland's live portal, 2026-11-12) -----------------------

LOVELAND_TODAY = date(2026, 11, 12)
LOVELAND_PORTAL = f"https://{LOVELAND}.civicweb.net"


def _loveland(web, body, today=LOVELAND_TODAY):
    add_loveland(web, today)
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": [body]})
    return read_latest(source, body, web.fetcher(), today=today)


def _documents_asked(web):
    return [u.rsplit("/meetings/", 1)[1].split("/")[0] for u in web.asked if "/meetings/" in u]


def test_civicweb_reads_the_newest_past_agenda_of_the_body(web):
    proof = _loveland(web, "City Council")
    assert proof.ok, proof.problem
    # The 10 November study session is newer than the 4 November meeting; the 17 and 19 November
    # council meetings are after "today".
    assert (proof.meeting_date, proof.what) == (date(2026, 11, 10), "agenda")
    assert proof.title == "LOVELAND CITY COUNCIL STUDY SESSION - 10 Nov 2026"
    assert proof.document == (
        f"{LOVELAND_PORTAL}/document/522976/"
        "LOVELAND%20CITY%20COUNCIL%20STUDY%20SESSION%20-%2010%20Nov%202026.html"
    )
    assert proof.lines[:3] == ["AGENDA", "TUESDAY, NOVEMBER 10, 2026", "STUDY SESSION 7:30 P.M."]
    assert _documents_asked(web) == ["18423"]


def test_civicweb_lists_the_agendas_numbered_items(web):
    proof = _loveland(web, "Planning Commission")
    assert proof.ok, proof.problem
    assert (proof.meeting_date, proof.title) == (
        date(2026, 11, 9),
        "Planning Commission - 09 Nov 2026",
    )
    assert proof.lines == [
        "5 agenda items",
        "I. CALL TO ORDER",
        "II. PLEDGE OF ALLEGIANCE",
        "III. ROLL CALL",
    ]


def test_civicweb_skips_cancelled_unpublished_and_future_meetings(web):
    # 18078 (11 Nov) is CANCELLED but published, 18433 (18 Nov) is unpublished and not yet held:
    # the 14 October meeting is the newest one to read.
    proof = _loveland(web, "Open Lands and Trails Advisory Commission")
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 14)
    assert proof.lines[:2] == ["3 agenda items", "1. CALL TO ORDER (5:30 PM)"]
    assert _documents_asked(web) == ["18077"]
    # 18220 (11 Nov) is unpublished and 18196 (11 Nov) is cancelled; neither is asked for.
    proof = _loveland(web, "Loveland Communication Advisory Board")
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 14)
    assert "18220" not in _documents_asked(web) and "18196" not in _documents_asked(web)


def test_civicweb_reads_the_meeting_day_it_is_asked_for(web):
    # Asked on 4 November, the council meeting that day is the newest; "today" itself counts.
    proof = _loveland(web, "City Council", today=date(2026, 11, 4))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 11, 4)
    assert proof.title == "LOVELAND CITY COUNCIL MEETING - 04 Nov 2026"
    assert proof.lines == [
        "6 agenda items",
        "1. INTRODUCTION",
        "1.1. CALL TO ORDER",
        "1.2. PLEDGE OF ALLEGIANCE",
    ]


def test_civicweb_goes_on_to_an_older_meeting_when_the_newest_has_no_agenda(web):
    import json

    from civiccast_brief.citybuild import platforms

    from .conftest import HERE

    add_loveland(web, LOVELAND_TODAY)
    rows = json.loads((HERE / "civicweb-loveland-docs-18423.json").read_text("utf-8"))
    packet_only = [row for row in rows if row["DocumentType"] != 1]
    assert packet_only  # the packet is posted; the agenda is not
    web.add(
        platforms.civicweb_documents_url(LOVELAND, 18421),
        (HERE / "civicweb-loveland-docs-18421.json").read_bytes(),
        "application/json",
    )
    web.add(
        platforms.civicweb_documents_url(LOVELAND, 18423),
        json.dumps(packet_only),
        "application/json",
    )
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=LOVELAND_TODAY)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 11, 4)
    assert _documents_asked(web) == ["18423", "18421"]


def test_civicweb_says_when_the_body_has_no_meeting_or_its_documents_do_not_answer(web):
    proof = _loveland(web, "Zoning Board of Adjustment")
    assert not proof.ok
    assert proof.problem == (
        "no published 'Zoning Board of Adjustment' meeting in the portal's last 120 days "
        "up to 2026-11-12"
    )
    # A portal whose document list for the meeting does not answer is named, not passed over.
    web = type(web)()
    add_loveland(web, LOVELAND_TODAY, docs=())
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": ["Planning Commission"]})
    proof = read_latest(source, "Planning Commission", web.fetcher(), today=LOVELAND_TODAY)
    assert not proof.ok
    assert proof.problem.startswith("2026-11-09: ") and "answered HTTP 404" in proof.problem


def test_civicweb_portal_that_does_not_answer_is_not_read(web):
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=LOVELAND_TODAY)
    assert not proof.ok
    assert "answered HTTP 404" in proof.problem
