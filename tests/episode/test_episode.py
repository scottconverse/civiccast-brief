import json
import shutil
from pathlib import Path

import pytest

from civiccast_brief.episode import build as B
from civiccast_brief.review.export import export_approved
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
        assert item.status == "draft"
        assert item.speakers == {"voice 01": "unknown", "voice 02": "unknown"}
        store.update_content(
            item_id,
            speakers={"voice 01": "Clerk", "voice 02": "unknown"},
            recap=item.recap,
            script=item.script,
        )
        store.approve(item_id)
    out = tmp_path / "export"
    result = export_approved(db, out)
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
    with pytest.raises(B.EpisodeError, match="not an approved export"):
        _assemble(item, meeting_dir, audio)


def test_roundup_stages_and_assembles_without_a_transcript(tmp_path, meeting, monkeypatch):
    meeting_dir, audio = meeting
    monkeypatch.setattr(B, "probe_duration", lambda path: 10.0)
    recap = json.loads((meeting_dir / "recap.json").read_text())
    recap["video_id"] = "roundup"
    recap["meeting_date"] = "October 6, 2026 and October 20, 2026"
    (meeting_dir / "recap.json").write_text(json.dumps(recap))
    (meeting_dir / "transcript.json").unlink()
    (meeting_dir / "speakers.json").unlink()

    db = tmp_path / "review.db"
    item_id = B.stage(db, meeting_dir, meeting_dir / "script.json")
    with ReviewStore(db) as store:
        assert store.get_item(item_id).meeting_id == "roundup October 6, 2026 and October 20, 2026"
        store.approve(item_id)
    item_dir = export_approved(db, tmp_path / "export").exported[0]
    episode = _assemble(item_dir, meeting_dir, audio)

    assert episode["id"] == "2026-10-20-example-weekly-roundup"
    assert episode["headline"] == "Longmont weekly roundup: October 6, 2026 and October 20, 2026"
    assert episode["video_url"] is None and episode["transcript"] is None
