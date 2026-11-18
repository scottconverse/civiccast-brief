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


def test_a_seat_in_plain_prose_is_not_given_a_name() -> None:
    text = "The Mayor Pro Tem spoke about the budget."
    out, changes = fill_roll_call_names(text, PEOPLE)
    assert out == text
    assert changes == []


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
