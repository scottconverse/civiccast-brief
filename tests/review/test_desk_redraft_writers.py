"""Redraft on the real desk: the story and script writers, given the editor's note."""

from __future__ import annotations

import json

import pytest

from civiccast_brief.review import cli, desk
from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.writers import desk_writer
from civiccast_brief.script.recap import Claim, Recap, save_recap

RECAP = Recap(
    city="Longmont",
    body="City Council",
    meeting_date="October 20, 2026",
    video_id="uHFuCfd5MVQ",
    claims=(
        Claim(
            id="c01",
            topic="Annexation",
            text="The council approved the 8513 St. Vrain Road annexation 6-1.",
            timestamp="1:02:03",
            quote="the motion carries six to one",
        ),
        Claim(
            id="c02",
            topic="Public comment",
            text="A speaker asked the council to keep the Sunset pool open.",
            timestamp="0:10:00",
            quote="please keep Sunset pool open",
        ),
    ),
)

OLD_STORY = {
    "headline": "Speaker asks council to keep Sunset pool open",
    "dek": "",
    "sections": [
        {
            "heading": None,
            "paragraphs": [
                {
                    "text": "A speaker asked the council to keep the Sunset pool open.",
                    "cites": ["c02"],
                }
            ],
        }
    ],
    "votes": [],
    "quotes": [],
    "topics": [],
}


def story(*paragraphs: tuple[str, list[str]], headline: str) -> str:
    return json.dumps(
        {
            "headline": headline,
            "dek": "",
            "sections": [
                {"heading": None, "paragraphs": [{"text": t, "cites": c} for t, c in paragraphs]}
            ],
            "votes": [],
            "quotes": [],
            "topics": [],
        }
    )


OLD_REPORT = "CHECK RESULT: PASS Article: 1 sections, 1 paragraphs (the old draft's report)"

GOOD = ("The council approved the 8513 St. Vrain Road annexation 6-1.", ["c01"])
NEW_NUMBER = ("The annexation covers 40 acres.", ["c01"])


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work" / "2026-10-20-city-council-uhfucfd5mvq"
    folder.mkdir(parents=True)
    save_recap(RECAP, folder / "recap.json")
    return tmp_path / "work"


def stage_story(db, city):
    return desk.stage_item(
        db,
        kind="article",
        meeting_id=RECAP.video_id,
        title=OLD_STORY["headline"],
        meeting_date="2026-10-20",
        body="City Council",
        checks_passed=True,
        checks_note=OLD_REPORT,
        article=OLD_STORY,
    )


def stand_in(answers):
    prompts: list[tuple[str, str, str]] = []

    def complete_for(task, body):
        def complete(prompt):
            prompts.append((task, body, prompt))
            return answers[len(prompts) - 1]

        return complete

    return prompts, complete_for


def test_redraft_writes_the_story_again_with_the_editors_note(work, tmp_path) -> None:
    db, city = tmp_path / "desk.db", tmp_path / "city"
    city.mkdir()
    item = stage_story(db, city)
    prompts, complete_for = stand_in([story(GOOD, headline="Council approves annexation 6-1")])
    writer = desk_writer(work, complete_for=complete_for)

    desk.redraft(ReviewStore(db), item.id, "Lead with the annexation vote.", writer)

    task, body, prompt = prompts[0]
    assert (task, body) == ("article", "City Council")
    assert "Lead with the annexation vote." in prompt
    assert OLD_STORY["headline"] in prompt  # the writer sees the draft it is changing
    after = ReviewStore(db).get_item(item.id)
    assert after.article["headline"] == "Council approves annexation 6-1"
    assert after.checks_passed is True
    assert after.status == "review"
    # The desk shows the new draft's report, not the old one's.
    assert after.checks_note != OLD_REPORT and "CHECK RESULT: PASS" in after.checks_note


def test_a_redraft_that_adds_a_fact_is_rewritten_once_then_cut_to_what_the_claims_back(
    work, tmp_path
) -> None:
    db, city = tmp_path / "desk.db", tmp_path / "city"
    city.mkdir()
    item = stage_story(db, city)
    bad = story(GOOD, NEW_NUMBER, headline="Council approves annexation 6-1")
    prompts, complete_for = stand_in([bad, bad])

    desk.redraft(
        ReviewStore(db), item.id, "Add the size.", desk_writer(work, complete_for=complete_for)
    )

    assert len(prompts) == 2
    assert "40" in prompts[1][2] and "Add the size." in prompts[1][2]
    after = ReviewStore(db).get_item(item.id)
    texts = [p["text"] for s in after.article["sections"] for p in s["paragraphs"]]
    assert texts == [GOOD[0]]
    assert after.checks_passed is True


def test_an_episode_redraft_writes_the_script_again_with_the_note(work, tmp_path) -> None:
    db, city = tmp_path / "desk.db", tmp_path / "city"
    city.mkdir()
    item = desk.stage_item(
        db,
        kind="episode",
        meeting_id=RECAP.video_id,
        title="City Council",
        meeting_date="2026-10-20",
        body="City Council",
        checks_passed=True,
        speakers={},
        recap="R",
        script="[S1] The old opening.\n",
    )
    answer = "\n".join(
        json.dumps(line)
        for line in [
            {"speaker": "CHAPTER", "text": "Annexation", "claim_ids": ["c01"]},
            {"speaker": "S1", "text": GOOD[0], "claim_ids": ["c01"]},
        ]
    )
    prompts, complete_for = stand_in([answer, answer])

    desk.redraft(
        ReviewStore(db),
        item.id,
        "Start with the vote.",
        desk_writer(work, complete_for=complete_for),
    )

    assert prompts[0][0] == "script"
    assert "Start with the vote." in prompts[0][2] and "The old opening." in prompts[0][2]
    after = ReviewStore(db).get_item(item.id)
    assert after.script == f"[CHAPTER Annexation]\n[S1] {GOOD[0]}\n"
    assert after.checks_passed is False  # no intro or outro: the script check says so


def test_an_item_with_no_run_folder_is_named_in_the_error(tmp_path) -> None:
    db, city = tmp_path / "desk.db", tmp_path / "city"
    city.mkdir()
    (tmp_path / "work").mkdir()
    item = stage_story(db, city)
    writer = desk_writer(tmp_path / "work", complete_for=stand_in([])[1])
    with pytest.raises(ReviewError, match="uHFuCfd5MVQ"):
        desk.redraft(ReviewStore(db), item.id, "Shorter.", writer)


def test_serve_connects_the_writers_when_given_the_weekly_work_folder(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "serve", lambda app, port: seen.update(w=app.state.desk.writer))
    city, work = tmp_path / "city", tmp_path / "work"
    city.mkdir()
    work.mkdir()
    base = ["serve", "--db", str(tmp_path / "d.db"), "--city", str(city)]
    assert cli.main(base) == 0
    assert seen["w"] is None
    assert cli.main([*base, "--work", str(work)]) == 0
    assert seen["w"] is not None
    assert cli.main([*base, "--work", str(tmp_path / "none")]) == 1


def test_an_editors_own_save_drops_the_old_drafts_check_report(tmp_path) -> None:
    db, city = tmp_path / "desk.db", tmp_path / "city"
    city.mkdir()
    item = stage_story(db, city)
    edited = json.loads(story(GOOD, headline="Council approves annexation"))

    ReviewStore(db).update_content(item.id, article=edited)

    after = ReviewStore(db).get_item(item.id)
    assert after.checks_passed is None and after.checks_note == ""
