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
    assert number_atoms("four point five million dollars") == {"4500000"}
    assert number_atoms("$4.5 million") == {"4500000"}
    assert number_atoms("March 1, 2027") == {"1", "2027"}


@pytest.mark.parametrize(
    ("claim", "spoken"),
    [
        # From the October 27, 2026 study session: both lines were true and both were cut.
        ("about $6 million", "about six million dollars"),
        ("between $30 million and $50 million", "between thirty million and fifty million"),
        ("$4.5 million", "four point five million dollars"),
        ("$2 billion", "two billion dollars"),
        ("$200,000", "two hundred thousand dollars"),
    ],
)
def test_a_spoken_amount_matches_the_same_amount_in_digits(claim, spoken):
    assert number_atoms(spoken) == number_atoms(claim)


def test_two_amounts_joined_by_and_stay_two_amounts():
    assert number_atoms("thirty million and fifty million") == {"30000000", "50000000"}
    assert number_atoms("one hundred and five") == {"105"}  # one number, said with "and"


def test_one_speaker_is_a_person_not_a_number():
    # The script prompt tells the writer to say "one speaker" for "a speaker".
    assert number_atoms("One speaker said the pool should stay open.") == frozenset()
    assert number_atoms("one speaker asked") == frozenset()
