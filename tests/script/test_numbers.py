import pytest

from civiccast_brief.script.numbers import number_atoms, spoken_to_digits


@pytest.mark.parametrize(
    ("spoken", "digits"),
    [
        ("twenty twenty-six dash sixty-nine", "2026-69"),
        ("Ordinance twenty twenty-six dash sixty-nine", "Ordinance 2026-69"),
        ("two hundred thousand dollars", "200000 dollars"),
        ("six to one", "6-1"),
        ("eighty-five thirteen", "8513"),
        ("eighty-five thirteen Saint Vrain Road", "8513 Saint Vrain Road"),
        ("one thousand nine hundred seventy-six", "1976"),
        ("one hundred and five", "105"),
        ("two hundred six South Main", "206 South Main"),
        ("about seven hundred spaces", "about 700 spaces"),
        ("March first, twenty twenty-seven", "March 1, 2027"),
        ("October twentieth", "October 20"),
        ("October twenty-first", "October 21"),
        ("four point five million dollars", "4.5 million dollars"),
        ("twenty-five council members", "25 council members"),
        ("twenty two people", "22 people"),
        ("seven o'clock", "7 o'clock"),
        ("Ordinance sixty-nine six to one", "Ordinance 69 6-1"),
        ("eight five one three Saint Vrain Road", "8513 Saint Vrain Road"),
        ("U S two eighty-seven frontage", "U S 287 frontage"),
        ("Resolution twenty twenty-six dash zero two", "Resolution 2026-02"),
        ("five forty-five Collier Street", "545 Collier Street"),
    ],
)
def test_spoken_numbers_become_digits(spoken, digits):
    assert spoken_to_digits(spoken) == digits


@pytest.mark.parametrize(
    "text",
    [
        "one of the members",
        "no one came",
        "the first reading",
        "Ninth Avenue",
        "R-T-D",
    ],
)
def test_words_that_are_not_numbers_are_left_alone(text):
    assert spoken_to_digits(text) == text
    assert number_atoms(text) == frozenset()


def test_atoms_match_spoken_and_digit_forms():
    assert number_atoms("twenty twenty-six dash sixty-nine") == {"2026", "69"}
    assert number_atoms("Ordinance 2026-69") == {"2026", "69"}
    assert number_atoms("six to one") == {"6", "1"}
    assert number_atoms("council voted 6-1") == {"6", "1"}


def test_atoms_drop_thousands_commas():
    assert number_atoms("$200,000") == {"200000"}
    assert number_atoms("two hundred thousand dollars") == {"200000"}


def test_atoms_keep_decimals_and_dates():
    assert number_atoms("four point five million dollars") == {"4.5"}
    assert number_atoms("March 1, 2027") == {"1", "2027"}
