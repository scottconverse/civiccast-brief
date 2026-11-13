import json
import shutil
from pathlib import Path

import pytest

from civiccast_brief.episode import build as B
from civiccast_brief.review.export import export_published
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.site.build import build_site

CITY = Path(__file__).parents[1] / "site" / "fixtures" / "episodes" / "city.json"


@pytest.fixture
def meeting(tmp_path):
    m = tmp_path / "meeting"
    m.mkdir()
    (m / "recap.json").write_text(
        json.dumps(
            {
                "city": "Longmont",
                "body": "City Council",
                "meeting_date": "October 20, 2026",
                "video_id": "VID00000001",
                "claims": [
                    {
                        "id": "c01",
                        "topic": "Consent agenda",
                        "text": "The consent agenda passed.",
                        "timestamp": "0:25:47",
                        "quote": "The consent agenda passes.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (m / "transcript.json").write_text(
        json.dumps(
            {
                "video_id": "VID00000001",
                "title": "City Council Regular Session - October 20, 2026",
                "segments": [
                    {
                        "start": 5,
                        "end": 6,
                        "text": "Call to order.",
                        "turn_start": True,
                        "speaker": "unknown",
                        "voice": "voice 01",
                    },
                    {
                        "start": 1547,
                        "end": 1549,
                        "text": "The consent agenda passes.",
                        "turn_start": True,
                        "speaker": "Mayor Peck",
                        "voice": "voice 02",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (m / "speakers.json").write_text(
        json.dumps(
            {"diarization": {"labels": {"SPEAKER_03": "voice 01", "SPEAKER_00": "voice 02"}}}
        ),
        encoding="utf-8",
    )
    (m / "script.json").write_text(
        json.dumps(
            [
                {"speaker": "S1", "text": "Welcome to CivicCast Brief.", "claim_ids": []},
                {"speaker": "CHAPTER", "text": "Consent agenda", "claim_ids": ["c01"]},
                {"speaker": "S2", "text": "The consent agenda passed.", "claim_ids": ["c01"]},
            ]
        ),
        encoding="utf-8",
    )
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "episode.mp3").write_bytes(b"\xff\xfb" + b"\0" * 998)
    (audio / "chapters.json").write_text(
        json.dumps(
            {"version": "1.2.0", "chapters": [{"startTime": 3.5, "title": "Consent agenda"}]}
        )
    )
    return m, audio


def _approved_export(tmp_path, meeting_dir):
    db = tmp_path / "review.db"
    item_id = B.stage(db, meeting_dir, meeting_dir / "script.json")
    with ReviewStore(db) as store:
        item = store.get_item(item_id)
        assert item.status == "review"
        assert item.speakers == {"voice 01": "unknown", "voice 02": "unknown"}
        store.update_content(
            item_id,
            speakers={"voice 01": "Clerk", "voice 02": "unknown"},
            recap=item.recap,
            script=item.script,
        )
        store.publish(item_id)
    out = tmp_path / "export"
    result = export_published(db, out)
    assert len(result.exported) == 1
    return result.exported[0]


def _assemble(item_dir, meeting_dir, audio):
    return B.assemble(
        item_dir=item_dir,
        meeting_dir=meeting_dir,
        script_json=meeting_dir / "script.json",
        audio_dir=audio,
        media_base_url="https://media.example.com/longmont/",
        city_slug="example",
        published="2026-10-21T09:00:00-06:00",
    )


def test_staged_and_approved_meeting_builds_into_the_site(tmp_path, meeting, monkeypatch):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 61.234)
    item_dir = _approved_export(tmp_path, meeting_dir)
    episode = _assemble(item_dir, meeting_dir, audio)

    assert episode["id"] == "2026-10-20-example-city-council"
    assert episode["audio"] == {
        "url": "https://media.example.com/longmont/2026-10-20-example-city-council.mp3",
        "duration_s": 61.23,
        "bytes": 1000,
        "type": "audio/mpeg",
    }
    assert episode["script"].splitlines()[1] == "[S2] The consent agenda passed."
    assert episode["chapters"] == [{"start": 3.5, "title": "Consent agenda"}]
    speakers = [line["speaker"] for line in episode["transcript"]]
    assert speakers == ["Clerk", "Mayor Peck"]  # producer named voice 01; roll-call name kept

    site_in = tmp_path / "site-in"
    site_in.mkdir()
    shutil.copy(CITY, site_in / "city.json")
    (site_in / f"{episode['id']}.json").write_text(json.dumps(episode), encoding="utf-8")
    report = build_site(site_in, tmp_path / "site", "https://example.test")
    assert report.episodes == 1


def test_assemble_refuses_a_script_changed_after_approval(tmp_path, meeting, monkeypatch):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 1.0)
    item_dir = _approved_export(tmp_path, meeting_dir)
    script = json.loads((meeting_dir / "script.json").read_text())
    script[2]["text"] = "The consent agenda failed."
    (meeting_dir / "script.json").write_text(json.dumps(script))
    with pytest.raises(B.EpisodeError, match="approved script differs"):
        _assemble(item_dir, meeting_dir, audio)


def test_assemble_refuses_an_unapproved_folder(tmp_path, meeting):
    meeting_dir, audio = meeting
    item = tmp_path / "item"
    item.mkdir()
    (item / "item.json").write_text(json.dumps({"status": "draft"}))
    with pytest.raises(B.EpisodeError, match="not a published export"):
        _assemble(item, meeting_dir, audio)


def test_roundup_stages_and_assembles_without_a_transcript(tmp_path, meeting, monkeypatch):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 10.0)
    recap = json.loads((meeting_dir / "recap.json").read_text())
    recap["video_id"] = "roundup"
    recap["meeting_date"] = "October 6, 2026 and October 20, 2026"
    # Seven bodies joined, as on 10 November 2026: past the site's 120-character board limit.
    recap["body"] = " and ".join(f"Longmont Advisory Board Number {n}" for n in range(7))
    (meeting_dir / "recap.json").write_text(json.dumps(recap))
    (meeting_dir / "transcript.json").unlink()
    (meeting_dir / "speakers.json").unlink()

    db = tmp_path / "review.db"
    item_id = B.stage(db, meeting_dir, meeting_dir / "script.json")
    with ReviewStore(db) as store:
        assert store.get_item(item_id).meeting_id == "roundup October 6, 2026 and October 20, 2026"
        store.publish(item_id)
    item_dir = export_published(db, tmp_path / "export").exported[0]
    episode = _assemble(item_dir, meeting_dir, audio)

    assert episode["id"] == "2026-10-20-example-weekly-roundup"
    # The dates are a range, not every date strung together.
    assert episode["headline"] == "Longmont weekly roundup, Oct. 6 – Oct. 20"
    assert episode["video_url"] is None and episode["transcript"] is None
    assert episode["board"] == "Weekly roundup"


def _roundup(tmp_path, meeting_dir):
    recap = json.loads((meeting_dir / "recap.json").read_text())
    recap["video_id"] = "roundup"
    recap["meeting_date"] = "October 6, 2026 and October 20, 2026"
    recap["claims"][0]["id"] = "m2-c01"
    recap["claims"][0]["text"] = "[October 20, 2026 meeting] The consent agenda passed."
    (meeting_dir / "recap.json").write_text(json.dumps(recap))
    (meeting_dir / "transcript.json").unlink()
    (meeting_dir / "speakers.json").unlink()
    db = tmp_path / "review.db"
    item_id = B.stage(db, meeting_dir, meeting_dir / "script.json")
    with ReviewStore(db) as store:
        store.publish(item_id)
    return export_published(db, tmp_path / "export").exported[0]


def test_roundup_uses_its_written_title_when_it_passes(tmp_path, meeting, monkeypatch):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 10.0)
    item_dir = _roundup(tmp_path, meeting_dir)
    (meeting_dir / "title.json").write_text(
        json.dumps({"title": "Longmont council passes its consent agenda", "claim_ids": ["m2-c01"]})
    )
    assert _assemble(item_dir, meeting_dir, audio)["headline"] == (
        "Longmont council passes its consent agenda"
    )


def test_roundup_refuses_a_written_title_with_a_fact_the_claims_lack(
    tmp_path, meeting, monkeypatch
):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 10.0)
    item_dir = _roundup(tmp_path, meeting_dir)
    (meeting_dir / "title.json").write_text(
        json.dumps({"title": "Council passes consent agenda 7-0", "claim_ids": ["m2-c01"]})
    )
    with pytest.raises(B.EpisodeError, match="number 7 is not in the cited claims"):
        _assemble(item_dir, meeting_dir, audio)


def _story(passed_text="The consent agenda passed."):
    return {
        "headline": "Consent agenda passes",
        "dek": "",
        "sections": [{"heading": None, "paragraphs": [{"text": passed_text, "cites": ["c01"]}]}],
        "votes": [
            {"motion": "Consent agenda", "result": "passed", "tally": None, "claim_id": "c01"}
        ],
        "quotes": [{"claim_id": "c01", "speaker": "Mayor Peck"}],
    }


def _published_article(tmp_path, meeting_dir, story):
    from civiccast_brief.article.__main__ import main as article_main

    (meeting_dir / "article.json").write_text(json.dumps(story), encoding="utf-8")
    db = tmp_path / "articles.db"
    code = article_main(
        [
            "stage",
            "--db",
            str(db),
            "--recap",
            str(meeting_dir / "recap.json"),
            "--article",
            str(meeting_dir / "article.json"),
        ]
    )
    assert code == 0
    with ReviewStore(db) as store:
        [item] = store.list_items()
        # The story passed its check, so it was published with no press.
        assert item.kind == "article" and item.status == "published"
    [folder] = export_published(db, tmp_path / "article-export").exported
    return db, folder


def test_a_published_article_is_added_to_the_episode_in_the_site_format(
    tmp_path, meeting, monkeypatch
):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 61.234)
    item_dir = _approved_export(tmp_path, meeting_dir)
    _, article_dir = _published_article(tmp_path, meeting_dir, _story())
    episode = B.assemble(
        item_dir=item_dir,
        meeting_dir=meeting_dir,
        script_json=meeting_dir / "script.json",
        audio_dir=audio,
        media_base_url="https://media.example.com/longmont/",
        city_slug="example",
        published="2026-10-21T09:00:00-06:00",
        article_dir=article_dir,
    )
    article = episode["article"]
    assert article["headline"] == "Consent agenda passes"
    assert article["sections"][0]["paragraphs"][0]["cites"] == [{"claim": 0, "start_s": 1547}]
    assert article["votes"] == [
        {"motion": "Consent agenda", "result": "passed", "tally": None, "start_s": 1547}
    ]
    # The quote is the claim's verbatim words; "Mayor Peck" is named in the captions there.
    assert article["quotes"] == [
        {"text": "The consent agenda passes.", "speaker": "Mayor Peck", "start_s": 1547}
    ]


def test_assemble_refuses_an_article_edited_on_the_desk_to_add_a_number(
    tmp_path, meeting, monkeypatch
):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 61.234)
    item_dir = _approved_export(tmp_path, meeting_dir)
    db, _ = _published_article(tmp_path, meeting_dir, _story())
    with ReviewStore(db) as store:
        [item] = store.list_items()
        store.update_content(item.id, article=_story("The consent agenda passed 7-0."))
        store.publish(item.id)
    [article_dir] = export_published(db, tmp_path / "article-export").exported
    with pytest.raises(B.EpisodeError, match="number 7 is not in the cited claims"):
        B.assemble(
            item_dir=item_dir,
            meeting_dir=meeting_dir,
            script_json=meeting_dir / "script.json",
            audio_dir=audio,
            media_base_url="https://media.example.com/longmont/",
            city_slug="example",
            published="2026-10-21T09:00:00-06:00",
            article_dir=article_dir,
        )


def _passing_script(meeting_dir):
    """A script that passes the deterministic check, with the recap claims it needs."""
    from civiccast_brief.script.writer import DISCLAIMER

    recap = json.loads((meeting_dir / "recap.json").read_text(encoding="utf-8"))
    recap["claims"].append(
        {
            "id": "c02",
            "topic": "Live stream",
            "text": "The meeting video is on the city's YouTube channel.",
            "timestamp": "0:00:05",
            "quote": "Call to order.",
        }
    )
    (meeting_dir / "recap.json").write_text(json.dumps(recap), encoding="utf-8")
    lines = [
        {"speaker": "S1", "text": "CivicCast Brief, Longmont, October twentieth.", "claim_ids": []},
        {"speaker": "S2", "text": DISCLAIMER, "claim_ids": []},
        {"speaker": "CHAPTER", "text": "Consent agenda", "claim_ids": ["c01"]},
        {"speaker": "S1", "text": "The consent agenda passed.", "claim_ids": ["c01"]},
        {
            "speaker": "S2",
            "text": "Watch the meeting video on the city's YouTube channel.",
            "claim_ids": ["c02"],
        },
    ]
    path = meeting_dir / "passing-script.json"
    path.write_text(json.dumps(lines), encoding="utf-8")
    return path


def test_stage_checks_the_script_and_publishes_it_at_once(tmp_path, meeting):
    meeting_dir, _ = meeting
    script = _passing_script(meeting_dir)
    db = tmp_path / "review.db"
    item_id = B.stage(db, meeting_dir, script)
    with ReviewStore(db) as store:
        item = store.get_item(item_id)
    assert item.checks_passed is True
    assert item.status == "published"
    assert item.body == "City Council"


def test_a_failing_script_is_held_in_review(tmp_path, meeting):
    meeting_dir, _ = meeting
    # The fixture's own script has no disclaimer and no outro, so it fails the check.
    db2 = tmp_path / "review2.db"
    failing = B.stage(db2, meeting_dir, meeting_dir / "script.json")
    with ReviewStore(db2) as store:
        item = store.get_item(failing)
    assert item.checks_passed is False
    assert item.status == "review"


def test_a_story_citing_a_document_page_keeps_its_page_in_the_site_format(
    tmp_path, meeting, monkeypatch
):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 61.234)
    url = "https://longmont.primegov.com/Public/CompiledDocument?meetingTemplateId=9&compileOutputType=1"
    (meeting_dir / "documents.json").write_text(
        json.dumps(
            {
                "format": 1,
                "documents": [
                    {
                        "kind": "minutes",
                        "url": url,
                        "sha256": "0" * 64,
                        "pages": [{"page": 3, "text": "Consent Agenda. Carried: 7 - 0"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    story = _story("The consent agenda passed 7-0.")
    story["sections"][0]["paragraphs"][0]["pages"] = ["d1p3"]
    item_dir = _approved_export(tmp_path, meeting_dir)
    _, article_dir = _published_article(tmp_path, meeting_dir, story)
    episode = B.assemble(
        item_dir=item_dir,
        meeting_dir=meeting_dir,
        script_json=meeting_dir / "script.json",
        audio_dir=audio,
        media_base_url="https://media.example.com/longmont/",
        city_slug="example",
        published="2026-10-21T09:00:00-06:00",
        article_dir=article_dir,
    )
    paragraph = episode["article"]["sections"][0]["paragraphs"][0]
    assert paragraph["pages"] == [{"kind": "minutes", "url": url, "page": 3}]


def test_a_published_article_becomes_a_stand_alone_story_file_with_no_episode(tmp_path, meeting):
    meeting_dir, _ = meeting  # no episode is staged, approved or assembled
    _, article_dir = _published_article(tmp_path, meeting_dir, _story())
    story = B.assemble_story(
        article_dir=article_dir,
        meeting_dir=meeting_dir,
        city_slug="example",
        published="2026-10-21T09:00:00-06:00",
    )
    # Same id the meeting's episode gets, so the site can merge the two later.
    assert story["id"] == "2026-10-20-example-city-council"
    assert story["article"]["headline"] == "Consent agenda passes"
    assert "audio" not in story and "script" not in story
    assert story["video_url"].startswith("https://www.youtube.com/")

    site_in = tmp_path / "site-in"
    (site_in / "stories").mkdir(parents=True)
    shutil.copy(CITY, site_in / "city.json")
    (site_in / "stories" / f"{story['id']}.json").write_text(json.dumps(story), encoding="utf-8")
    report = build_site(site_in, tmp_path / "site", "https://example.test")
    assert (report.episodes, report.stories) == (0, 1)
    assert (tmp_path / "site" / "stories" / story["id"] / "index.html").is_file()


def test_an_episode_id_has_only_what_the_site_accepts():
    """The Urban Renewal Authority's body name has parentheses and starts with the city's name;
    its episode id was "2026-11-10-longmont-longmont-urban-renewal-authority-(lura)", which the
    site build refused, so the whole weekly deploy failed."""
    from civiccast_brief.script.recap import Recap

    recap = Recap(
        city="Longmont",
        body="Longmont Urban Renewal Authority (LURA)",
        meeting_date="November 10, 2026",
        video_id="VID00000002",
        claims=(),
    )
    assert B.episode_slug(recap, "longmont") == "2026-11-10-longmont-urban-renewal-authority-lura"
    council = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="November 3, 2026",
        video_id="VID00000003",
        claims=(),
    )
    assert B.episode_slug(council, "longmont") == "2026-11-03-longmont-city-council"
