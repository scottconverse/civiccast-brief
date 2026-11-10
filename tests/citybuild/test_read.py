"""Reading one recent meeting of a body, from each kind of source."""

from __future__ import annotations

from datetime import date

from civiccast_brief.citybuild.read import body_matches, committee_for, read_latest
from civiccast_brief.sources import make_source

from .conftest import (
    CHANNEL,
    FLAGSTAFF,
    PRIMEGOV,
    SVCM,
    SVCM_NEWEST,
    SVVSD,
    SVVSD_180,
    SVVSD_PACKET,
    TPCA_ROOT,
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
    assert "no meeting of committee 12" in proof.problem
    hpc = read_latest(
        source, "Historic Preservation Commission", web.fetcher(), today=date(2026, 9, 30)
    )
    # 1 October is after the day given, so the January meeting (a PDF agenda not saved here) is the
    # one tried, and its failure is named.
    assert not hpc.ok
    assert "2026-01-01" in hpc.problem


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
    assert proof.meeting_date == date(2026, 10, 29)
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
