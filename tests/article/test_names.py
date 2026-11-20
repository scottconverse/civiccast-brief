"""Names print from the record, never from the speech-to-text."""

from __future__ import annotations

from civiccast_brief.article.names import (
    fill_roll_call_names,
    fixes,
    load_people,
    repair_article,
    repair_text,
)
from civiccast_brief.article.writer import (
    Article,
    DocPage,
    Paragraph,
    Quote,
    Section,
    Vote,
    check_article,
    quote_words,
)
from civiccast_brief.meeting.roster import roster_from_dict
from civiccast_brief.script.recap import Claim, Recap

ROSTER = roster_from_dict(
    {
        "city": "Superior",
        "body": "Town Council",
        "state": "CO",
        "members": [
            {"name": "Mark Lacis", "first": "Mark", "last": "Lacis", "role": "Mayor"},
            {
                "name": "Jenn Kaaoush",
                "first": "Jenn",
                "last": "Kaaoush",
                "role": "Mayor Pro Tem",
            },
            {
                "name": "Stephanie Miller",
                "first": "Stephanie",
                "last": "Miller",
                "role": "Council Member",
            },
            {
                "name": "Heather Cracraft",
                "first": "Heather",
                "last": "Cracraft",
                "role": "Council Member",
            },
        ],
    }
)
PACKET = (
    "Agenda Item Name: Second Reading - An Ordinance of the Town Council\n"
    "Meeting Date: September 28, 2026\n"
    "Presented By: Martin Toth, Asst. Town Manager, Hannah Hippely, Planning and\n"
    "Building Director\n"
    "Presented For: 2nd Reading\n"
)
PAGES = {"d1p79": DocPage("d1p79", "packet", "https://example.test/packet", 79, PACKET)}
PEOPLE = load_people([(ROSTER, "cities/superior/roster.json")], PAGES, "documents.json")
RECAP = Recap(
    city="Superior",
    body="Town Council",
    meeting_date="September 28, 2026",
    video_id="civicclerk-superiorco-42",
    claims=(
        Claim(
            id="c01",
            topic="Fire Code",
            text="The council heard the second reading of the fire code ordinance.",
            timestamp="0:48:31",
            quote="the first reading of this ordinance was on August 20th",
        ),
    ),
)


def test_a_caption_spelling_is_repaired_from_the_packet() -> None:
    text = (
        "Mahanni Hippoli, with the town's planning and building department, said the first "
        "reading was on August 20."
    )
    out, changes = repair_text(text, PEOPLE)
    assert "Hannah Hippely" in out
    assert "Mahanni Hippoli" not in out
    assert changes and "Hannah Hippely" in changes[0]


def test_a_caption_surname_is_repaired_from_the_roster() -> None:
    out, _ = repair_text("Mayor Pro Tem Cowish voted yes.", PEOPLE)
    assert out == "Mayor Pro Tem Jenn Kaaoush voted yes."


def test_a_heard_name_with_no_record_match_becomes_a_role() -> None:
    out, changes = repair_text("Council Member Quorbex announced the tour.", PEOPLE)
    assert "Quorbex" not in out
    assert "council member" in out.lower()
    assert changes and "no record match" in changes[0]


def test_the_check_fails_a_name_the_record_does_not_hold() -> None:
    article = Article(
        headline="Superior hears fire code",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "Mahanni Hippoli said the first reading was on August 20.",
                        ("c01",),
                    ),
                ),
            ),
        ),
    )
    report = check_article(RECAP, article, people=PEOPLE)
    assert not report.passed
    assert any(issue.code == "name_spelling" for issue in report.issues)


def test_a_quote_word_name_is_repaired_too() -> None:
    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="civicclerk-superiorco-42",
        claims=(
            Claim(
                id="c01",
                topic="Fire Code",
                text="The council heard the second reading.",
                timestamp="0:48:31",
                quote="I'm Mahanni Hippoli with the town's planning and building department",
            ),
        ),
    )
    words = quote_words(recap, Quote("c01", "A speaker"), PEOPLE)
    assert "Hannah Hippely" in words
    assert "Mahanni" not in words


def test_a_roll_call_names_the_record_mayor_pro_tem() -> None:
    text = "Council Member Foster, Mayor Lacis and Mayor Pro Tem voted yes."
    out, changes = fill_roll_call_names(text, PEOPLE)
    assert "Mayor Pro Tem Jenn Kaaoush voted yes" in out
    assert changes and "Kaaoush" in changes[0]


def test_the_mayor_pro_tem_is_named_from_the_roster_in_plain_prose() -> None:
    # The seat has one holder the record prints, so the name goes wherever the record says
    # "mayor pro tem", not only in a roll call.
    text = "The Mayor Pro Tem spoke about the budget."
    out, changes = fill_roll_call_names(text, PEOPLE)
    assert out == "The Mayor Pro Tem Jenn Kaaoush spoke about the budget."
    assert changes and "Kaaoush" in changes[0]


def test_a_misheard_mayor_pro_tem_is_set_to_the_record_name() -> None:
    text = "Second from mayor protem cowish. All those in favor."
    out, changes = fill_roll_call_names(text, PEOPLE)
    assert out == "Second from Mayor Pro Tem Jenn Kaaoush. All those in favor."
    assert changes


def test_a_merged_first_and_surname_is_the_record_name() -> None:
    # "Craig Craft" is the caption's run-together "Cracraft"; the roster prints Heather Cracraft.
    out, changes = repair_text("Second from Council Member Craig Craft.", PEOPLE)
    assert out == "Second from Council Member Heather Cracraft."
    assert changes


def test_repair_article_names_a_seat_only_voter() -> None:
    article = Article(
        headline="Superior hears fire code",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The council approved the contract; Mayor Pro Tem voted yes.",
                        ("c01",),
                    ),
                ),
            ),
        ),
    )
    fixed, _ = repair_article(article, PEOPLE)
    assert "Mayor Pro Tem Jenn Kaaoush voted yes" in fixed.paragraphs[0].text


def test_a_vote_line_prints_the_record_spelling() -> None:
    article = Article(
        headline="Superior hears fire code",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The council heard the second reading of the fire code ordinance.",
                        ("c01",),
                    ),
                ),
            ),
        ),
        votes=(Vote("Mayor Pro Tem Cowish moved to approve", "passed", None, "c01"),),
    )
    fixed, changes = repair_article(article, PEOPLE)
    assert fixed.votes[0].motion == "Mayor Pro Tem Jenn Kaaoush moved to approve"
    assert any("Jenn Kaaoush" in change for change in changes)


def test_a_quote_that_prints_a_caption_spelling_fails_the_check() -> None:
    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="civicclerk-superiorco-42",
        claims=(
            Claim(
                id="c01",
                topic="Agenda",
                text="The council approved the agenda.",
                timestamp="0:04:13",
                quote="Second from Council Member Craig Craft.",
            ),
        ),
    )
    article = Article(
        headline="Superior council approves agenda",
        sections=(Section(None, (Paragraph("The council approved the agenda.", ("c01",)),)),),
        quotes=(Quote("c01", "A speaker"),),
    )
    # The printed quote is repaired to the record's name, so the story passes.
    assert "Heather Cracraft" in quote_words(recap, article.quotes[0], PEOPLE)
    report = check_article(recap, article, people=PEOPLE)
    assert not any(issue.where.startswith("quote") for issue in report.issues), report.render()


def test_transcript_page_names_are_repaired_from_the_record() -> None:
    from civiccast_brief.episode.build import _name_fixes

    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="civicclerk-superiorco-42",
        claims=(),
    )
    fixes = _name_fixes(
        recap,
        PEOPLE,
        [
            "Second from Council Member Craig Craft.",
            "motion from council member foster and a second from mayor protem cowish",
        ],
    )
    pairs = {(fix["from"], fix["to"]) for fix in fixes}
    assert ("Craig Craft", "Heather Cracraft") in pairs
    assert (
        "mayor protem cowish",
        "Mayor Pro Tem Jenn Kaaoush",
    ) in pairs


def test_a_surname_only_packet_holder_does_not_repeat_the_seat_name() -> None:
    from civiccast_brief.article.names import People, Person, seat_fixes

    surname_only = Person(
        name="Kaaoush",
        first="",
        last="Kaaoush",
        role="Mayor Pro Tem",
        source="documents.json packet p. 55",
        evidence="Mayor Pro-tem Kaaoush",
    )
    full = Person(
        name="Jenn Kaaoush",
        first="Jenn",
        last="Kaaoush",
        role="Mayor Pro Tem",
        source="cities/superior/roster.json",
        evidence='"name": "Jenn Kaaoush"',
    )
    people = People(persons=(surname_only, full), text="")
    # The full name is already printed: the seat is not named twice.
    assert seat_fixes("the Mayor Pro Tem Jenn Kaaoush voted yes", people) == []
    out, _ = fill_roll_call_names("the Mayor Pro Tem Kaaoush voted yes", people)
    assert out == "the Mayor Pro Tem Jenn Kaaoush voted yes"


def test_a_resolution_label_is_not_a_person_name() -> None:
    out, changes = repair_text("The council approved Resolution R-H-87.", PEOPLE)
    assert out == "The council approved Resolution R-H-87."
    assert changes == []


def test_a_short_word_is_not_a_surname_by_sound_alone() -> None:
    # "Full-depth" was once "repaired" to a person because a crude sound key made "depth" and
    # "deputy" alike. A sound-only match needs a longer surname.
    from civiccast_brief.article.names import People, Person, repair_text

    person = Person(
        name="Ellie Bouhn",
        first="Ellie Bouhn",
        last="Deputy Attorney",
        role="Deputy Attorney",
        source="documents.json packet p. 23",
        evidence="Town Attorney Ellie Bouhn",
    )
    people = People(persons=(person,), text="town attorney ellie bouhn main street")
    out, changes = repair_text("Full-depth asphalt repairs continued on Main Street.", people)
    assert out == "Full-depth asphalt repairs continued on Main Street."
    assert changes == []


def test_a_street_name_is_not_a_person_with_no_record_match() -> None:
    out, changes = repair_text("Full-depth asphalt repairs continued on Honey Creek Lane.", PEOPLE)
    assert out == "Full-depth asphalt repairs continued on Honey Creek Lane."
    assert changes == []


def test_the_captions_other_mayor_pro_tem_spellings_are_named() -> None:
    out, _ = fill_roll_call_names("Merry proton cowish. Nair, proton, cowish.", PEOPLE)
    assert "cowish" not in out.lower()
    assert out.count("Mayor Pro Tem Jenn Kaaoush") == 2


def test_person_issues_catches_a_member_spelling_and_leaves_a_place_alone() -> None:
    from civiccast_brief.article.names import person_issues

    issues = person_issues("Council Member Craigcraft toured the Community Center.", PEOPLE)
    assert any("Craigcraft" in message for _, message in issues)
    assert not any("Community Center" in message for _, message in issues)


def test_a_sentence_start_word_is_not_rewritten_as_a_sound_alike_name() -> None:
    board = roster_from_dict(
        {
            "city": "Superior",
            "body": "Board of Education",
            "state": "CO",
            "members": [
                {"name": "Temu Otting", "first": "Temu", "last": "Otting", "role": "Director"}
            ],
        }
    )
    people = load_people([(board, "cities/superior/rosters/board-of-education.json")], {}, "")
    text = "Sitting also as the urban renewal authority, the council approved the plan."
    out, changes = repair_text(text, people)
    assert out == text
    assert not changes
    assert people.match("Sitting", "") is not None  # the sound-alike the guard stops


# Boulder, Nov 19 2026: the check printed "the Rob Kaplan land use map", "starting Jason. 1"
# and "A speaker voted unanimously" for the Planning Board. Places, plans, bodies and dates
# name no one, so they stay as written.
BOULDER = load_people(
    [
        (
            roster_from_dict(
                {
                    "city": "Boulder",
                    "body": "City Council",
                    "state": "CO",
                    "members": [
                        {
                            "name": "Rob Kaplan",
                            "first": "Rob",
                            "last": "Kaplan",
                            "role": "Council Member",
                        },
                        {
                            "name": "Jason Unger",
                            "first": "Jason",
                            "last": "Unger",
                            "role": "Member",
                        },
                        {"name": "Joel Smith", "first": "Joel", "last": "Smith", "role": "Member"},
                        {"name": "Jan Hill", "first": "Jan", "last": "Hill", "role": "Member"},
                    ],
                }
            ),
            "cities/boulder/roster.json",
        )
    ],
    {},
    "",
)


def test_places_plans_and_bodies_are_not_people() -> None:
    text = (
        "The Planning Board voted unanimously on Sept. 22. The second phase of the Transit "
        "Village Area Plan changes the Boulder Valley Comprehensive Plan land use map. The "
        "Federal Emergency Management Agency map takes effect near South Boulder Creek, with "
        "Residential Rural 2 zoning on Rural Lands and University Hill."
    )
    out, changes = repair_text(text, BOULDER)
    assert out == text
    assert changes == []


def test_month_abbreviation_is_not_a_first_name() -> None:
    text = "The rule takes effect starting Jan. 1, 2027."
    assert repair_text(text, BOULDER) == (text, [])


def test_a_record_person_named_like_a_place_is_still_checked() -> None:
    out, changes = repair_text("Later, Jann Hill asked a question.", BOULDER)
    assert "Jan Hill" in out
    assert changes


def test_a_different_surname_is_not_swapped_for_a_roster_member() -> None:
    out, changes = repair_text("Bob Smithers spoke during public comment.", BOULDER)
    assert "Joel Smith" not in out
    assert "Bob Smithers" not in out
    assert changes


def test_real_misspellings_still_take_the_record_spelling() -> None:
    out, _ = repair_text("Rob Kaplin and Jasn Unger spoke.", BOULDER)
    assert out == "Rob Kaplan and Jason Unger spoke."


# Longmont, Nov 19 2026: the weekly stories printed "Angela Brill" for "Senate Bill",
# "Sangay" for "Housing", "Wes Parker" for "Roosevelt Park" and "a speaker" for "Dream Lab",
# "Colorado Gives Day" and "Thistle Resident-Owned Communities". Bills, programs, events and
# bodies name no one, and an ordinary word is not a member's name by sound alone.
def _member(name: str, role: str = "Member") -> dict[str, str]:
    first, last = name.split(" ", 1)
    return {"name": name, "first": first, "last": last, "role": role}


LONGMONT = load_people(
    [
        (
            roster_from_dict(
                {
                    "city": "Longmont",
                    "body": "City Council",
                    "state": "CO",
                    "members": [
                        _member("Angela Brill"),
                        _member("Wes Parker"),
                        _member("Sangay Mingyur"),
                        _member("Matthew Popkin", "Council Member"),
                        _member("Doug Barnert", "Commissioner"),
                        _member("Holly Norton", "Commissioner"),
                    ],
                }
            ),
            "cities/longmont/roster.json",
        )
    ],
    {},
    "",
)


def test_bills_programs_events_and_bodies_keep_their_names() -> None:
    text = (
        "Senate Bill 24-174 requires the study. Thistle Resident-Owned Communities runs the "
        "park. The Dream Lab drew families, and Colorado Gives Day is Dec. 8. Museum Protectors "
        "gave $500. The Regional Transportation District Eco Pass goes to every employee. The "
        "council heard about the Cities Summit, the National League of Cities and True North."
    )
    out, changes = repair_text(text, LONGMONT)
    assert out == text
    assert changes == []


def test_an_ordinary_word_is_not_a_member_by_sound_alone() -> None:
    text = "Housing and Human Services met Nov. 12. Humanity of Longmont builds homes."
    assert repair_text(text, LONGMONT) == (text, [])


def test_a_place_after_a_preposition_is_left_alone() -> None:
    text = "The exhibit came from a city in Japan and a gallery from Nashville."
    assert repair_text(text, LONGMONT) == (text, [])


def test_an_unknown_speaker_still_loses_the_caption_name() -> None:
    out, changes = repair_text("Bob Quorbex said the rent doubled.", LONGMONT)
    assert "Quorbex" not in out
    assert changes and "no record match" in changes[0]


def test_a_titled_member_still_takes_the_record_spelling() -> None:
    out, _ = repair_text("Commissioner Barnet moved approval.", LONGMONT)
    assert out == "Commissioner Doug Barnert moved approval."


CALENDAR_PACKET = (
    "Mayor Council Calendar City Summit (Nashville, Tennessee)\n"
    "ATTEST:\n"
    "Mayor\n"
    "The City Clerk\n"
    "Board Member Wolford moved, seconded by Board Member Davis, to recommend approval.\n"
    "The Chief Financial Officer shall repay the loans.\n"
    "Presented By: Joanne Zeas, Human Resources, Miles Churchill, Public Safety\n"
    "Presented For: Action\n"
)


def test_packet_headings_are_not_people() -> None:
    pages = {"p1": DocPage("p1", "packet", "https://example.test/packet", 1, CALENDAR_PACKET)}
    people = load_people([(ROSTER, "roster.json")], pages, "documents.json")
    names = {person.name for person in people.persons}
    for heading in (
        "Council Calendar City",
        "The",
        "Board Member Davis",
        "Financial Officer",
        "Human Resources",
        "Public Safety",
        "ATTEST",
    ):
        assert heading not in names
    assert {"Joanne Zeas", "Miles Churchill"} <= names


def test_a_phrase_is_not_swapped_for_a_packet_heading() -> None:
    pages = {"p1": DocPage("p1", "packet", "https://example.test/packet", 1, CALENDAR_PACKET)}
    people = load_people([(ROSTER, "roster.json")], pages, "documents.json")
    assert fixes("[S2] Theo said Social Security pays it.", people) == []


def test_a_different_surname_is_not_swapped() -> None:
    roster = roster_from_dict(
        {
            "city": "Longmont",
            "body": "Board of Appeals",
            "state": "CO",
            "members": [
                {"name": "JOHN LENNON", "first": "JOHN", "last": "LENNON", "role": "Member"},
                {"name": "Jake Marsing", "first": "Jake", "last": "Marsing", "role": "Member"},
            ],
        }
    )
    people = load_people([(roster, "rosters/appeals.json")], {}, "")
    assert fixes("John Bolton spoke.", people) == []
    assert fixes("Jake Parsim spoke.", people) == [("Jake Parsim", "Jake Marsing")]


# Lafayette, Nov 19 2026: the site printed "Mayor Pro Tem Tim Barnes Executive", "Rachel
# Hanson" for the speaker Michael Watson, and "Jordan Walker" for Aurora Water.
LAFAYETTE_ROSTER = roster_from_dict(
    {
        "city": "Lafayette",
        "body": "City Council",
        "state": "CO",
        "members": [
            {"name": "Tim Barnes", "first": "Tim", "last": "Barnes", "role": "Mayor Pro Tem"},
            {"name": "Adam Gianola", "first": "Adam", "last": "Gianola", "role": "Councilor"},
        ],
    }
)
ARTS_BOARD = roster_from_dict(
    {
        "city": "Lafayette",
        "body": "Cultural Arts Board",
        "state": "CO",
        "members": [
            {"name": "Rachel Hanson", "first": "Rachel", "last": "Hanson", "role": "Member"},
            {"name": "Jordan Walker", "first": "Jordan", "last": "Walker", "role": "Member"},
        ],
    }
)
NORMS_MEMO = (
    "Council Agenda Memo \nPrepared by: \n- Councilor Adam Gianola  \n"
    "- Mayor Pro Tem Tim Barnes \n \nExecutive Summary \nThe Council Norms and Procedures\n"
    "Sustainability Director\nMelanie Asquith Principal Utilities Engineer\n"
)
LAFAYETTE = load_people(
    [(LAFAYETTE_ROSTER, "roster.json")],
    {"p562": DocPage("p562", "agenda", "https://example.test/agenda", 562, NORMS_MEMO)},
    "documents.json",
    [(ARTS_BOARD, "rosters/cultural-arts-board.json")],
)


def test_a_document_name_does_not_run_into_the_next_line() -> None:
    names = {person.name for person in LAFAYETTE.persons}
    assert "Tim Barnes Executive" not in names
    assert not any("Principal" in name for name in names)


def test_another_bodys_member_is_never_a_misheard_spelling() -> None:
    from civiccast_brief.article.names import seat_fixes

    text = "Michael Watson spoke for Aurora Water. Rachel Hanson spoke too."
    assert fixes(text, LAFAYETTE) == []
    assert seat_fixes(text, LAFAYETTE) == []
    out, _ = repair_text("Michael Watson spoke.", LAFAYETTE)
    assert "Rachel Hanson" not in out
    # The meeting's own body still corrects a misheard member.
    assert fixes("Adam Giannola spoke.", LAFAYETTE) == [("Adam Giannola", "Adam Gianola")]


def test_the_seat_fix_keeps_the_words_after_the_seat() -> None:
    from civiccast_brief.article.names import seat_fixes

    pairs = dict(
        seat_fixes(
            "Thanks, mayor pro tem, city manager. The pro tem or whoever's next. "
            "Mayor Pro Tem Barnes.",
            LAFAYETTE,
        )
    )
    assert pairs["mayor pro tem, city manager"] == "Mayor Pro Tem Tim Barnes, city manager"
    assert pairs["pro tem or whoever's"] == "Mayor Pro Tem Tim Barnes or whoever's"
    assert pairs["Mayor Pro Tem Barnes"] == "Mayor Pro Tem Tim Barnes"


def test_a_place_word_is_not_a_surname_by_sound() -> None:
    from civiccast_brief.article.names import People, Person

    walton = Person(
        name="Morgan Walton",
        first="Morgan",
        last="Walton",
        role="Director",
        source="documents.json agenda p. 15",
        evidence="Deputy Finance Director Morgan Walton",
    )
    people = People(persons=(walton,), text="deputy finance director morgan walton")
    assert fixes("It's where the Oldtown digital sign stood.", people) == []
    assert fixes("Director Waltin spoke.", people) == [
        ("Director Waltin", "Director Morgan Walton")
    ]


def test_a_misheard_holder_after_the_seat_is_replaced_whole() -> None:
    from civiccast_brief.article.names import seat_fixes

    pairs = dict(seat_fixes("Mayor Pro Tem Jen Kawish. Mayor Pro Tem the Cowish.", PEOPLE))
    assert pairs["Mayor Pro Tem Jen Kawish"] == "Mayor Pro Tem Jenn Kaaoush"
    assert pairs["Mayor Pro Tem the Cowish"] == "Mayor Pro Tem Jenn Kaaoush"


def test_a_commenter_is_not_a_packet_name_with_another_first_sound() -> None:
    from civiccast_brief.article.names import People, Person

    ooton = Person(
        name="Jenn Ooton",
        first="Jenn",
        last="Ooton",
        role="",
        source="documents.json packet p. 59",
        evidence="Presented by: Jenn Ooton",
    )
    people = People(persons=(ooton,), text="jenn ooton")
    assert fixes("Okay. Um next person, John Bolton.", people) == []


# Lafayette, Nov 2 and Nov 17, 2026: the captions wrote "Councelor Johnson" for Councilor
# Jensen, the minutes list every councilor and none is a Johnson, and Roy Johnson, a member of
# the IDEA board, spoke in public comment.
LAFAYETTE_COUNCIL = roster_from_dict(
    {
        "city": "Lafayette",
        "body": "City Council",
        "state": "CO",
        "members": [
            {"name": "Saul Tapia Vega", "first": "Saul", "last": "Tapia Vega", "role": "Mayor"},
            {"name": "Annmarie Jensen", "first": "Annmarie", "last": "Jensen", "role": "Councilor"},
            {
                "name": "Crystal Gallegos",
                "first": "Crystal",
                "last": "Gallegos",
                "role": "Councilor",
            },
        ],
    }
)
IDEA_BOARD = roster_from_dict(
    {
        "city": "Lafayette",
        "body": "IDEA Board",
        "state": "CO",
        "members": [{"name": "Roy Johnson", "first": "Roy", "last": "Johnson", "role": "Member"}],
    }
)
MINUTES = "Public Input\n3. Roy Johnson spoke in support of naming the pocket park.\n"
LAFAYETTE_NOV = load_people(
    [(LAFAYETTE_COUNCIL, "roster.json")],
    {"m3": DocPage("m3", "minutes", "https://example.test/minutes", 3, MINUTES)},
    "documents.json",
    [(IDEA_BOARD, "rosters/idea-board.json")],
)


def test_a_misheard_councilor_under_the_title_is_the_councilor_not_a_commenter() -> None:
    from civiccast_brief.article.names import apply_fixes

    pairs = fixes("Thank you, Mayor. Councelor Johnson.", LAFAYETTE_NOV)
    assert pairs == [("Councelor Johnson", "Councelor Annmarie Jensen")]
    # The fix is tied to the title: the commenter's own name stays as printed.
    assert fixes("Next we'll hear from Roy Johnson.", LAFAYETTE_NOV) == []
    assert apply_fixes("I'm still Roy Johnson, Sweetwater Circle.", pairs) == (
        "I'm still Roy Johnson, Sweetwater Circle."
    )


def test_a_councilor_title_is_never_the_mayor() -> None:
    # "Councilor Vegas" was called on during council feedback; the mayor is addressed as mayor.
    assert fixes("Councilor Vegas.", LAFAYETTE_NOV) == []


def test_a_roster_seat_beats_an_earlier_holder_in_the_packet() -> None:
    from civiccast_brief.article.names import seat_fixes

    # Louisville, Nov 2, 2026: a committee's history says "2024 - Mayor Pro Tem Dickinson";
    # the roster says Barbara Hamlington holds the seat now.
    roster = roster_from_dict(
        {
            "city": "Louisville",
            "body": "City Council",
            "state": "CO",
            "members": [
                {
                    "name": "Caleb Dickinson",
                    "first": "Caleb",
                    "last": "Dickinson",
                    "role": "Council Member, Ward I",
                },
                {
                    "name": "Barbara Hamlington",
                    "first": "Barbara",
                    "last": "Hamlington",
                    "role": "Mayor Pro Tem, Ward III",
                },
            ],
        }
    )
    history = (
        "Previous Committee Members:\n2024 - Mayor Pro Tem Dickinson and Council member Fahey\n"
    )
    people = load_people(
        [(roster, "roster.json")],
        {"p9": DocPage("p9", "packet", "https://example.test/packet", 9, history)},
        "documents.json",
    )
    pairs = dict(seat_fixes("Thank you, Mayor Pro Tem Hamlington. The pro tem seconded.", people))
    assert pairs["Mayor Pro Tem Hamlington"] == "Mayor Pro Tem Barbara Hamlington"
    assert "Dickinson" not in " ".join(pairs.values())


def test_another_bodys_member_the_documents_name_is_corrected() -> None:
    # Lyons, Nov 16, 2026: the agenda prints "Neal Evers to Planning & Community Development
    # Commission"; the captions wrote "Neil Evers".
    commission = roster_from_dict(
        {
            "city": "Lyons",
            "body": "Planning & Community Development Commission",
            "state": "CO",
            "members": [
                {"name": "Neal Evers", "first": "Neal", "last": "Evers", "role": "Vice Chair"}
            ],
        }
    )
    agenda = "Mayoral Appointments\nNeal Evers to Planning & Community Development Commission\n"
    people = load_people(
        [],
        {"a2": DocPage("a2", "agenda", "https://example.test/agenda", 2, agenda)},
        "documents.json",
        [(commission, "rosters/planning-community-development-commission.json")],
    )
    assert fixes("next on this agenda is Neil Evers who is a", people) == [
        ("Neil Evers", "Neal Evers")
    ]
    # Without the documents printing the name, another body's member stays exact-only.
    alone = load_people([], {}, "documents.json", [(commission, "rosters/pcdc.json")])
    assert fixes("next on this agenda is Neil Evers who is a", alone) == []


# The site applies a meeting's name pairs to the whole page, transcript included, so each pair
# must be safe wherever its printed form appears.
TOWN = load_people(
    [
        (
            roster_from_dict(
                {
                    "city": "Lyons",
                    "body": "Board of Trustees",
                    "state": "CO",
                    "members": [
                        {
                            "name": "Greg Lowell",
                            "first": "Greg",
                            "last": "Lowell",
                            "role": "Trustee",
                        },
                        {
                            "name": "Karin Hoskin",
                            "first": "Karin",
                            "last": "Hoskin",
                            "role": "Trustee",
                        },
                        {
                            "name": "Sam Browning",
                            "first": "Sam",
                            "last": "Browning",
                            "role": "Trustee",
                        },
                        {
                            "name": "Neal Shah",
                            "first": "Neal",
                            "last": "Shah",
                            "role": "Mayor Pro Tem",
                        },
                        {
                            "name": "Meosha Babbs (SVVSD)",
                            "first": "Meosha",
                            "last": "Babbs",
                            "role": "Trustee",
                        },
                    ],
                }
            ),
            "cities/lyons/roster.json",
        )
    ],
    {},
    "documents.json",
)


def test_a_fix_replaces_whole_words_only() -> None:
    from civiccast_brief.article.names import apply_fixes

    pairs = [("Shaw", "Shah")]
    assert apply_fixes("Trustee Shaw spoke about Shawnee Road.", pairs) == (
        "Trustee Shah spoke about Shawnee Road."
    )


def test_two_words_are_a_name_only_when_the_first_sounds_like_the_person() -> None:
    assert fixes("We hiked to Lake Powell last year.", TOWN) == []
    assert ("Greg Lowel", "Greg Lowell") in fixes("We thank Greg Lowel for it.", TOWN)


def test_a_lone_word_under_a_title_keeps_the_title() -> None:
    assert ("Trustee Lowel", "Trustee Greg Lowell") in fixes("Trustee Lowel, go ahead.", TOWN)


def _site_pairs(*lines: str) -> list[tuple[str, str]]:
    from civiccast_brief.episode.build import _name_fixes

    recap = Recap("Lyons", "Board of Trustees", "2026-11-16", "abc", ())
    return [(p["from"], p["to"]) for p in _name_fixes(recap, TOWN, list(lines))]


def test_a_lone_word_the_meeting_also_says_in_lower_case_is_left_as_printed() -> None:
    pairs = _site_pairs("Trustee Lowel moved it.", "Lowel seconded.", "lowel is a word here.")
    assert ("Trustee Lowel", "Trustee Greg Lowell") in pairs
    assert all(wrong != "Lowel" for wrong, _ in pairs)


def test_a_lone_word_with_no_title_needs_the_person_named_in_the_meeting() -> None:
    # "the Burning" is not Trustee Browning when nobody names Browning.
    assert all(wrong != "Burning" for wrong, _ in _site_pairs("We've also done the Burning."))
    pairs = _site_pairs("Trustee Browing moved it.", "We heard from Browing again.")
    assert ("Browing", "Browning") in pairs


def test_a_record_name_prints_without_its_packet_note() -> None:
    pairs = _site_pairs("Trustee Babs had the first.")
    assert ("Trustee Babs", "Trustee Meosha Babbs") in pairs


def test_a_bare_seat_title_gets_no_name_on_the_site() -> None:
    # Electing "a new mayor pro tem" is not the current holder; only a misheard name is fixed.
    pairs = _site_pairs("Council will elect a new mayor protem.", "Mayor Proton Shaw, go ahead.")
    assert all("elect" not in wrong and wrong.lower() != "mayor protem" for wrong, _ in pairs)
    assert ("Mayor Proton Shaw, go", "Mayor Pro Tem Neal Shah, go") in pairs


def test_two_ordinary_words_are_not_a_name_on_the_site() -> None:
    pairs = _site_pairs("The grant went to Sharing Housing.", "We like sharing; housing is short.")
    assert all(wrong != "Sharing Housing" for wrong, _ in pairs)
