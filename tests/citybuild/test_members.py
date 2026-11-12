"""The roster is read from the city's own members page, by rule, with no person filling it in.

The pages are the towns' live members pages, saved and trimmed on 2026-11-11. Each test lists the
members exactly as that page prints them.
"""

from __future__ import annotations

import json

import pytest

from civiccast_brief.citybuild import members
from civiccast_brief.citybuild.build import CONTACT_EMAIL, PUBLISHER_EMAIL, build_city
from civiccast_brief.citybuild.fetch import FetchError, Page
from civiccast_brief.citybuild.members import find_members, governing_body, read_members
from civiccast_brief.citybuild.search import Search
from civiccast_brief.meeting.roster import load_roster

from .conftest import HERE

PAGES = HERE / "members"
ERIE = "https://www.erieco.gov/"
ERIE_GOVERNMENT = "https://www.erieco.gov/27/Government"
ERIE_COUNCIL = "https://www.erieco.gov/318/Town-Council"
BOULDER = "https://bouldercolorado.gov/"
BOULDER_COUNCIL = "https://bouldercolorado.gov/government/city-council"

EXPECTED = {
    "erie-town-council.html": [
        ("Andrew J. Moore", "Andrew", "Moore", "Mayor"),
        ("Anil Pesaramelli", "Anil", "Pesaramelli", "Council Member"),
        ("John Mortellaro", "John", "Mortellaro", "Council Member"),
        ("Dan Hoback", "Dan", "Hoback", "Council Member"),
        ("Brandon Bell", "Brandon", "Bell", "Mayor Pro Tem"),
        ("Emily Baer", "Emily", "Baer", "Council Member"),
        ("Brian O'Connor", "Brian", "O'Connor", "Council Member"),
    ],
    "lafayette-city-council.html": [
        ("Saul Tapia Vega", "Saul", "Tapia Vega", "Mayor"),
        ("Tim Barnes", "Tim", "Barnes", "Mayor Pro Tem"),
        ("David Fridland", "David", "Fridland", "Councilor"),
        ("Crystal Gallegos", "Crystal", "Gallegos", "Councilor"),
        ("Kyle Beaulieu", "Kyle", "Beaulieu", "Councilor"),
        ("Adam Gianola", "Adam", "Gianola", "Councilor"),
        ("Annmarie Jensen", "Annmarie", "Jensen", "Councilor"),
    ],
    "louisville-meet-the-council.html": [
        ("Chris Leh", "Chris", "Leh", "Mayor"),
        ("Caleb Dickinson", "Caleb", "Dickinson", "Council Member, Ward I"),
        ("Josh Cooperman", "Josh", "Cooperman", "Council Member, Ward I"),
        ("Deborah Fahey", "Deborah", "Fahey", "Council Member, Ward II"),
        ("Judi Kern", "Judi", "Kern", "Council Member, Ward II"),
        ("Barbara Hamlington", "Barbara", "Hamlington", "Mayor Pro Tem, Ward III"),
        ("Dietrich Hoefner", "Dietrich", "Hoefner", "Council Member, Ward III"),
    ],
    "superior-town-council.html": [
        ("Mark Lacis", "Mark", "Lacis", "Mayor"),
        ("Heather Cracraft", "Heather", "Cracraft", "Council Member"),
        ("Jenn Kaaoush", "Jenn", "Kaaoush", "Mayor Pro Tem"),
        ("Stephanie Miller", "Stephanie", "Miller", "Council Member"),
        ("Neal Shah", "Neal", "Shah", "Council Member"),
    ],
    "nederland-board-of-trustees.html": [
        ("Billy Giblin", "Billy", "Giblin", "Trustee"),
        ("Nichole Sterling", "Nichole", "Sterling", "Mayor"),
        ("Luke Miller", "Luke", "Miller", "Mayor Pro Tem"),
        ("Kristopher Larsen", "Kristopher", "Larsen", "Trustee"),
        ("Andrew Dewart", "Andrew", "Dewart", "Trustee"),
        ("Andrew Joslin", "Andrew", "Joslin", "Trustee"),
    ],
    "bouldercounty-commissioners.html": [
        ("Claire Levy", "Claire", "Levy", "County Commissioner"),
        ("Marta Loachamin", "Marta", "Loachamin", "County Commissioner"),
        ("Ashley Stolzmann", "Ashley", "Stolzmann", "County Commissioner"),
    ],
    "boulder-council.html": [
        ("Taishya Adams", "Taishya", "Adams", "City Council Member"),
        ("Matt Benjamin", "Matt", "Benjamin", "City Council Member"),
        ("Aaron Brockett", "Aaron", "Brockett", "Mayor"),
        ("Rob Kaplan", "Rob", "Kaplan", "City Council Member"),
        ("Tina Marquis", "Tina", "Marquis", "City Council Member"),
        ("Ryan Schuchard", "Ryan", "Schuchard", "City Council Member"),
        ("Nicole Speer", "Nicole", "Speer", "City Council Member"),
        ("Tara Winer", "Tara", "Winer", "Mayor Pro Tem"),
    ],
}


def _read(name: str, body: str = "") -> tuple[list[tuple[str, str, str, str]], list[str]]:
    found, untitled = read_members((PAGES / name).read_text(encoding="utf-8"), body)
    return [(m.name, m.first, m.last, m.role) for m in found], untitled


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_towns_page_gives_its_members_as_printed(name):
    found, untitled = _read(name)
    assert found == EXPECTED[name]
    assert untitled == []


def test_names_printed_with_no_title_get_the_boards_member_title():
    """Lyons prints the mayor and pro tem with titles and the five trustees as bare names. The
    town's 2016 statements ("Statement from Mayor Sullivan, Board of Trustees") are not members."""
    found, untitled = _read("lyons-board-of-trustees.html", "Board of Trustees")
    assert found == [
        ("Mark Browning", "Mark", "Browning", "Mayor"),
        ("Jen Wingard", "Jen", "Wingard", "Mayor Pro Tem"),
        ("Leigh Williams", "Leigh", "Williams", "Trustee"),
        ("Mariah McCreary", "Mariah", "McCreary", "Trustee"),
        ("Amy Schwartz", "Amy", "Schwartz", "Trustee"),
        ("Greg Lowell", "Greg", "Lowell", "Trustee"),
        ("Julie Jacobs", "Julie", "Jacobs", "Trustee"),
    ]
    assert untitled == [
        "Leigh Williams",
        "Mariah McCreary",
        "Amy Schwartz",
        "Greg Lowell",
        "Julie Jacobs",
    ]


def test_a_member_listed_twice_keeps_the_seat_title():
    """Jamestown lists Annamaria Boehms as Mayor Pro-Tem and again as Trustee, and Michal Duffy
    with no title between titled trustees; staff below the board are not members."""
    found, untitled = _read("jamestown-board-of-trustees.html", "Board of Trustees")
    assert found == [
        ("Michael Box", "Michael", "Box", "Mayor"),
        ("Annamaria Boehms", "Annamaria", "Boehms", "Mayor Pro Tem"),
        ("Michal Duffy", "Michal", "Duffy", "Trustee"),
        ("Erin Jobe", "Erin", "Jobe", "Trustee"),
        ("Steven Oncley", "Steven", "Oncley", "Trustee"),
        ("Roger Loving", "Roger", "Loving", "Trustee"),
        ("Megan Quitter", "Megan", "Quitter", "Trustee"),
    ]
    assert untitled == ["Michal Duffy"]


def test_the_governing_body_is_picked_over_a_board_listed_first():
    """Superior's search listed the Planning Commission first; the County's has no City Council."""
    assert governing_body(["Planning Commission", "Town Council", "Board of Education"]) == (
        "Town Council"
    )
    assert governing_body(["Board of County Commissioners", "Planning Commission"]) == (
        "Board of County Commissioners"
    )
    assert governing_body(["Board of Education"]) is None


def _erie(web) -> None:
    web.fixture(ERIE, "members/erie-home.html")
    web.fixture(ERIE_GOVERNMENT, "members/erie-government.html")
    web.fixture(ERIE_COUNCIL, "members/erie-town-council.html")


def test_the_members_page_is_found_from_the_home_page(web):
    """Erie's members are two links in: home, Government, Town Council."""
    _erie(web)
    pauses: list[int] = []
    found = find_members(ERIE, "Town Council", web.fetcher(), pause=lambda: pauses.append(1))
    assert found.problem == ""
    assert found.page == ERIE_COUNCIL
    assert [m.name for m in found.members][:2] == ["Andrew J. Moore", "Anil Pesaramelli"]
    assert len(found.members) == 7
    assert found.asked[:1] == [ERIE] and ERIE_GOVERNMENT in found.asked
    assert len(pauses) == len(found.asked) - 1  # one pause between each two pages
    assert len(found.asked) <= members.MAX_PAGES


def test_a_site_that_turns_the_program_away_is_read_in_the_browser(web):
    """bouldercolorado.gov answers a plain client 403 and opens in Chrome (2026-11-11)."""
    web.add(BOULDER, "<html>Forbidden</html>", status=403)
    web.add(BOULDER_COUNCIL, "<html>Forbidden</html>", status=403)
    opened: list[str] = []
    pages = {
        BOULDER: f'<html><a href="{BOULDER_COUNCIL}">Council Homepage</a></html>',
        BOULDER_COUNCIL: (PAGES / "boulder-council.html").read_text(encoding="utf-8"),
    }

    def open_in_browser(url: str) -> Page:
        opened.append(url)
        if url not in pages:
            raise FetchError(f"{url} not saved")
        return Page(url=url, status=200, content=pages[url].encode(), content_type="text/html")

    found = find_members(
        BOULDER, "City Council", web.fetcher(), open_in_browser=open_in_browser, pause=lambda: None
    )
    assert opened == [BOULDER, BOULDER_COUNCIL]
    assert found.page == BOULDER_COUNCIL
    assert [m.role for m in found.members].count("Mayor") == 1
    assert len(found.members) == 8


def test_a_site_with_no_members_page_says_so(web):
    web.add(ERIE, "<html><p>Welcome</p></html>")
    found = find_members(ERIE, "Town Council", web.fetcher(), pause=lambda: None)
    assert found.members == []
    assert found.problem == f"no page on {ERIE} lists the Town Council members with their titles"


def _search() -> Search:
    found = Search(name="Erie", state="Colorado", website=ERIE)
    found.bodies = ["Planning Commission", "Town Council"]
    return found


def test_city_find_fills_the_roster_and_the_podcast_address(web, tmp_path, monkeypatch):
    monkeypatch.setattr(members, "PAUSE_S", 0)
    _erie(web)
    from datetime import date

    built = build_city(tmp_path, _search(), fetcher=web.fetcher(), today=date(2026, 11, 11))
    roster = json.loads((tmp_path / "roster.json").read_text(encoding="utf-8"))
    assert roster["body"] == "Town Council"
    assert roster["sources"] == [ERIE_COUNCIL]
    assert roster["retrieved"] == "2026-11-11"
    assert roster["members"][0] == {
        "name": "Andrew J. Moore",
        "first": "Andrew",
        "last": "Moore",
        "role": "Mayor",
    }
    assert len(load_roster(tmp_path / "roster.json").members) == 7  # the meeting run can load it
    city = json.loads((tmp_path / "city.json").read_text(encoding="utf-8"))
    assert city["podcast"]["owner_email"] == PUBLISHER_EMAIL
    assert city["contact_email"] == CONTACT_EMAIL
    assert built.to_fill == []


def test_an_empty_roster_from_an_earlier_build_is_filled_and_a_full_one_kept(
    web, tmp_path, monkeypatch
):
    monkeypatch.setattr(members, "PAUSE_S", 0)
    _erie(web)
    (tmp_path / "roster.json").write_text(
        json.dumps({"city": "Erie", "body": "City Council", "members": []}), encoding="utf-8"
    )
    (tmp_path / "city.json").write_text(
        json.dumps(
            {"name": "Erie", "edited": True, "contact_email": "", "podcast": {"owner_email": ""}}
        ),
        encoding="utf-8",
    )
    build_city(tmp_path, _search(), fetcher=web.fetcher())
    assert len(json.loads((tmp_path / "roster.json").read_text(encoding="utf-8"))["members"]) == 7
    city = json.loads((tmp_path / "city.json").read_text(encoding="utf-8"))
    assert city == {
        "name": "Erie",
        "edited": True,
        "contact_email": CONTACT_EMAIL,
        "podcast": {"owner_email": PUBLISHER_EMAIL},
    }

    kept = {"city": "Erie", "body": "Town Council", "members": [{"name": "Only Editor"}]}
    (tmp_path / "roster.json").write_text(json.dumps(kept), encoding="utf-8")
    build_city(tmp_path, _search(), fetcher=web.fetcher())
    assert json.loads((tmp_path / "roster.json").read_text(encoding="utf-8")) == kept


def test_a_town_with_no_website_lists_the_roster_with_the_reason(web, tmp_path):
    """Ward has no website (checked 2026-11-11)."""
    found = Search(name="Ward", state="Colorado", website="")
    built = build_city(tmp_path, found, fetcher=web.fetcher())
    assert built.to_fill == [
        "roster.json members: the City Council members (the city has no website the search found)"
    ]


def test_a_page_that_says_too_many_requests_is_asked_again_after_a_wait(web):
    """Jamestown answered HTTP 429 to the second and third pages of the crawl (2026-11-11)."""
    home = "https://jamestownco.org/"
    board = "https://jamestownco.org/board-of-trustees/"
    web.add(home, f'<html><a href="{board}">Board of Trustees</a></html>')
    web.fixture(board, "members/jamestown-board-of-trustees.html")
    plain = web.fetcher()
    refusals = {board: 2}

    class Busy:
        def get(self, url: str) -> Page:
            if refusals.get(url):
                refusals[url] -= 1
                raise FetchError(f"{url} answered HTTP 429")
            return plain.get(url)

    waits: list[float] = []
    found = find_members(home, "", Busy(), pause=lambda: None, wait=waits.append)  # type: ignore[arg-type]
    assert waits == list(members.RETRY_S)
    assert found.page == board
    assert found.body == "Board of Trustees"
    assert len(found.members) == 7


def test_a_second_page_with_other_names_is_named_in_the_note(web):
    """Jamestown's /town-board/ (changed 2026-08-31) lists Louis Maurer; its /board-of-trustees/
    (changed 2025-12-30) lists Megan Quitter in that seat."""
    home = "https://jamestownco.org/"
    board = "https://jamestownco.org/board-of-trustees/"
    newer = "https://jamestownco.org/town-board/"
    web.add(home, f'<html><a href="{newer}">Town Board</a><a href="{board}">Trustees</a></html>')
    web.fixture(board, "members/jamestown-board-of-trustees.html")
    text = (PAGES / "jamestown-board-of-trustees.html").read_text(encoding="utf-8")
    web.add(newer, text.replace("Megan Quitter", "Louis Maurer"))
    found = find_members(home, "Board of Trustees", web.fetcher(), pause=lambda: None)
    assert found.page == newer
    assert found.differ == [(board, ["Megan Quitter"])]
    from datetime import date

    note = members.roster_from_found("Jamestown", "CO", found, date(2026, 11, 11))["note"]
    assert f"{board} also lists Board of Trustees members, with Megan Quitter in place" in note
