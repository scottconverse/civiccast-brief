"""Names print from the record, never from the speech-to-text."""

from __future__ import annotations

from civiccast_brief.article.names import (
    fill_roll_call_names,
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
