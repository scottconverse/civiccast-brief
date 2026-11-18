"""Record terms: a resolution number or item name is printed as the meeting documents print it."""

from __future__ import annotations

from dataclasses import dataclass

from civiccast_brief.article.terms import load_record, repair_text, term_issues

DOCUMENTS = """\
Resolution Number R-91 Series 2026
A Resolution approving the purchase of three Noblewins Swings as public art.
Noblewins Bench Swing Purchase-R-91
3 wind turbine swing benches
Resolution Number R-82 Series 2026
A Resolution of the Town Council of the Town of Superior adopting a budget for the Town of
Superior for 2027 and appropriating sums of money to the various funds.
"""


@dataclass
class Page:
    text: str


def record():
    return load_record({"d1p1": Page(DOCUMENTS)})


def test_the_record_reads_resolution_numbers_and_item_names() -> None:
    found = record()
    assert found is not None
    assert [number.printed for number in found.numbers] == ["R-91", "R-82"]
    assert found.verbatim("Noblewins Swings")


def test_a_misheard_number_takes_the_record_number() -> None:
    new, changes = repair_text(
        "The council approved Resolution R-991, Series 2026, for three Nouvel wind swings.",
        record(),
    )
    assert "Resolution R-91, Series 2026" in new
    assert "R-991" not in new
    assert "Nouvel" not in new and "Noblewins" in new
    assert any("R-991" in change for change in changes)


def test_a_garbled_number_matches_the_item_it_describes() -> None:
    new, _ = repair_text(
        "A motion was made to approve Resolution Sura dash 2 Series 2026, adopting a budget "
        "and appropriating sums for 2027.",
        record(),
    )
    assert "Resolution R-82, Series 2026" in new
    assert "Sura" not in new


def test_a_garbled_number_with_no_record_match_becomes_a_generic_resolution() -> None:
    new, _ = repair_text("The council approved Resolution Sura dash 9.", record())
    assert new == "The council approved a resolution."
    assert "Sura" not in new


def test_an_unrepaired_number_or_item_fails_the_check() -> None:
    issues = term_issues(
        "The council approved Resolution R-991 for three Nouvel wind swings.", record()
    )
    messages = [message for _, message in issues]
    assert any("R-991" in message and "R-91" in message for message in messages)
    assert any("Nouvel" in message for message in messages)


def test_the_record_form_passes_the_check() -> None:
    assert (
        term_issues("The council approved Resolution R-91 for three Noblewins swings.", record())
        == []
    )


def test_no_documents_means_no_term_check() -> None:
    text = "The council approved Resolution R-991 for three Nouvel wind swings."
    assert repair_text(text, None) == (text, [])
    assert term_issues(text, None) == []


def test_an_ordinary_capitalised_word_is_not_stripped() -> None:
    text = "Announcements were read at the meeting."
    new, changes = repair_text(text, record())
    assert new == text and changes == []


def test_a_garbled_number_with_the_record_prefix_matches_it() -> None:
    record = load_record(
        {
            "d1p1": Page(
                "Resolution SURA-2 Series 2026\nA Resolution adopting a budget and "
                "appropriating sums of money for the Superior Urban Renewal Authority."
            )
        }
    )
    new, _ = repair_text(
        "The council approved Resolution Sura dash 2 Series 2026, adopting a budget "
        "and appropriating sums for 2027.",
        record,
    )
    assert "Resolution SURA-2, Series 2026" in new


def test_an_ordinary_verb_is_not_taken_for_a_name() -> None:
    text = "Eliminating the vendor fee increased revenue by about $500,000."
    new, changes = repair_text(text, record())
    assert new == text and changes == []


def test_a_bare_garbled_name_takes_the_record_name() -> None:
    new, changes = repair_text("The art purchase covers three Nouvel.", record())
    assert "Nouvel" not in new and "Noblewins" in new
    assert changes


def test_a_weekday_or_a_word_inside_a_longer_name_is_not_a_garble() -> None:
    record = load_record(
        {"d1p1": Page("The Honors Committee met for six Weeks. Nouvel Catering served lunch.")}
    )
    for text in (
        "The State of Superior event was set for Wednesday, Oct. 21.",
        "The council extended a tolling agreement with the Rock Creek Homeowners Association.",
    ):
        assert repair_text(text, record) == (text, [])
        assert term_issues(text, record) == []
