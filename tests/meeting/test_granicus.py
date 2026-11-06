import json
from pathlib import Path

import pytest

from civiccast_brief.meeting import granicus as G
from civiccast_brief.meeting import pipeline
from civiccast_brief.meeting.roster import ROSTERS_DIR

FIX = Path(__file__).parent / "fixtures" / "granicus"
OAKLAND = ROSTERS_DIR / "oakland.json"


def test_publisher_rows_from_two_city_templates():
    oakland = G.parse_publisher(
        (FIX / "oakland.html").read_text(encoding="utf-8"), "oakland.granicus.com"
    )
    assert [(c.clip_id, c.date) for c in oakland] == [
        (7866, "November 4, 2026"),
        (7806, "October 6, 2026"),
    ]
    assert oakland[1].name.startswith("Concurrent Meeting of the Oakland Redevelopment")
    assert oakland[1].view_id == 2 and oakland[1].mp3_url is None

    berkeley = G.parse_publisher(
        (FIX / "berkeley.html").read_text(encoding="utf-8"), "berkeley.granicus.com"
    )
    assert [(c.clip_id, c.name, c.date) for c in berkeley] == [(5724, "ZAB", "October 8, 2026")]
    assert berkeley[0].mp3_url.startswith("https://archive-video.granicus.com/berkeley/")


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("Oct\xa0 8,\xa02026 - 07:00 PM", "October 8, 2026"),
        ("September 22, 2026", "September 22, 2026"),
        ("soon", None),
    ],
)
def test_long_date(text, date):
    assert G.long_date(text) == date


def test_vtt_cues_become_caption_items():
    items = G.parse_vtt((FIX / "oakland-7806.vtt").read_text(encoding="utf-8"))
    assert items[0] == {"start_ms": 167790, "end_ms": 167746, "text": "That"}
    assert items[1]["text"] == "afternoon, everybody. We're"
    assert all(item["text"] for item in items)


def test_an_empty_caption_file_stops_with_the_reason(tmp_path, monkeypatch):
    clip = G.Clip(
        host="berkeley.granicus.com", view_id=2, clip_id=5724, name="ZAB", date="October 8, 2026"
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(G, "_fetch", lambda url: "WEBVTT\n\n0\n")
    with pytest.raises(G.GranicusError, match="has no caption lines"):
        G.capture("berkeley.granicus.com", 2, 5724, tmp_path)


def test_capture_is_saved_and_reused_without_the_network(tmp_path, monkeypatch):
    clip = G.Clip(
        host="oakland.granicus.com", view_id=2, clip_id=7806, name="Council", date="October 6, 2026"
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(
        G, "_fetch", lambda url: (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    )
    first = G.capture("oakland.granicus.com", 2, 7806, tmp_path)
    assert not first.caption_reused and first.meeting_id == "granicus-oakland-7806"

    def no_network(*a):
        raise AssertionError("network used")

    monkeypatch.setattr(G, "_fetch", no_network)
    monkeypatch.setattr(G, "find_clip", no_network)
    second = G.capture("oakland.granicus.com", 2, 7806, tmp_path)
    assert second.caption_reused and second.clip == clip and second.items == first.items
    assert second.video_url == "https://oakland.granicus.com/MediaPlayer.php?view_id=2&clip_id=7806"


def test_pipeline_runs_a_granicus_clip(tmp_path, monkeypatch):
    clip = G.Clip(
        host="oakland.granicus.com",
        view_id=2,
        clip_id=7806,
        name="City Council",
        date="October 6, 2026",
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(
        G, "_fetch", lambda url: (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    )

    def complete(prompt):
        return "[]", {}

    summary = pipeline.run(
        "7806",
        tmp_path,
        granicus_host="oakland.granicus.com",
        granicus_view_id=2,
        roster_path=OAKLAND,
        complete=complete,
        log=lambda m: None,
    )
    assert summary["video_id"] == "granicus-oakland-7806"
    assert summary["meeting_date"] == "October 6, 2026"
    assert summary["caption_kind"] == "granicus_captions"
    transcript = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"]["platform"] == "granicus"
    assert transcript["source"]["video_url"].endswith("clip_id=7806")


def test_pipeline_refuses_a_granicus_run_without_a_view_id(tmp_path):
    with pytest.raises(pipeline.PipelineError, match="view id"):
        pipeline.run(
            "7806",
            tmp_path,
            granicus_host="oakland.granicus.com",
            roster_path=OAKLAND,
            log=lambda m: None,
        )


def test_granicus_recap_points_the_outro_to_the_city_website(tmp_path, monkeypatch):
    from civiccast_brief.script.checker import check_script
    from civiccast_brief.script.recap import load_recap
    from civiccast_brief.script.writer import DISCLAIMER, ScriptLine, build_prompt

    clip = G.Clip(
        host="oakland.granicus.com", view_id=2, clip_id=7806, name="Council", date="October 6, 2026"
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(
        G, "_fetch", lambda url: (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    )
    pipeline.run(
        "7806",
        tmp_path,
        granicus_host="oakland.granicus.com",
        granicus_view_id=2,
        roster_path=OAKLAND,
        complete=lambda prompt: ("[]", {}),
        log=lambda m: None,
    )
    recap = load_recap(tmp_path / "recap.json")
    assert recap.video_place == "website"
    assert "on the city's website" in build_prompt(recap)

    def outro(text):
        lines = [
            ScriptLine("S1", "Welcome to CivicCast Brief."),
            ScriptLine("S2", DISCLAIMER),
            ScriptLine("S2", text),
        ]
        return [i.message for i in check_script(recap, lines).issues if i.code == "outro"]

    assert outro("Watch the full meeting on the city's website.") == []
    assert outro("Watch it on the city's YouTube channel.") != []
