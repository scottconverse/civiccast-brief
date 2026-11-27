import json
import subprocess
from pathlib import Path

import pytest

from civiccast_brief.meeting import direct, pipeline
from civiccast_brief.meeting import granicus as G
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


def test_an_empty_caption_file_is_transcribed_from_the_clips_audio(tmp_path, monkeypatch):
    clip = G.Clip(
        host="berkeley.granicus.com",
        view_id=2,
        clip_id=5724,
        name="ZAB",
        date="October 8, 2026",
        mp3_url="https://archive-video.granicus.com/berkeley/zab.mp3",
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(G, "_fetch", lambda url: "WEBVTT\n\n0\n")
    asked: list[tuple[str, Path, str]] = []

    def transcribe(media_url, raw_dir, meeting_id, **kwargs):
        asked.append((media_url, raw_dir, meeting_id))
        return direct.DirectCapture(
            Path("transcript.json"),
            False,
            [{"start_ms": 0, "end_ms": 1000, "text": "Hello"}],
            transcribed=True,
        )

    monkeypatch.setattr(G.direct, "transcribe", transcribe)
    cap = G.capture("berkeley.granicus.com", 2, 5724, tmp_path)
    assert asked == [
        ("https://archive-video.granicus.com/berkeley/zab.mp3", tmp_path, "granicus-berkeley-5724")
    ]
    assert cap.transcribed is True
    assert cap.items == [{"start_ms": 0, "end_ms": 1000, "text": "Hello"}]
    assert not cap.caption_reused


def test_capture_is_saved_and_reused_without_the_network(tmp_path, monkeypatch):
    clip = G.Clip(
        host="oakland.granicus.com", view_id=2, clip_id=7806, name="Council", date="October 6, 2026"
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(
        G, "_fetch", lambda url: (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    )

    def no_transcribe(*a, **k):
        raise AssertionError("a clip with captions was transcribed")

    monkeypatch.setattr(G.direct, "transcribe", no_transcribe)
    first = G.capture("oakland.granicus.com", 2, 7806, tmp_path)
    assert not first.caption_reused and first.meeting_id == "granicus-oakland-7806"
    assert first.transcribed is False

    def no_network(*a):
        raise AssertionError("network used")

    monkeypatch.setattr(G, "_fetch", no_network)
    monkeypatch.setattr(G, "find_clip", no_network)
    second = G.capture("oakland.granicus.com", 2, 7806, tmp_path)
    assert second.caption_reused and second.clip == clip and second.items == first.items
    assert second.video_url == "https://oakland.granicus.com/MediaPlayer.php?view_id=2&clip_id=7806"


def test_an_empty_saved_caption_file_is_fetched_again(tmp_path, monkeypatch):
    clip = G.Clip(
        host="goldenco.granicus.com",
        view_id=2,
        clip_id=6736,
        name="Council",
        date="November 4, 2026",
    )
    vtt = (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    asked: list[str] = []

    def fetch(url):
        asked.append(url)
        return vtt

    monkeypatch.setattr(G, "_fetch", fetch)
    # A meeting whose captions were not posted yet saved an empty file: it must be read again.
    (tmp_path / "clip.json").write_text(
        json.dumps(
            {
                "host": clip.host,
                "view_id": 2,
                "clip_id": 6736,
                "name": "Council",
                "date": "November 4, 2026",
                "mp3_url": None,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "6736.captions.vtt").write_text("WEBVTT\n", encoding="utf-8")

    cap = G.capture("goldenco.granicus.com", 2, 6736, tmp_path)

    assert not cap.caption_reused
    assert asked == ["https://goldenco.granicus.com/videos/6736/captions.vtt"]
    assert cap.items


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


def test_pipeline_records_a_transcribed_granicus_clip(tmp_path, monkeypatch):
    clip = G.Clip(
        host="oakland.granicus.com",
        view_id=2,
        clip_id=7806,
        name="City Council",
        date="October 6, 2026",
        mp3_url="https://archive-video.granicus.com/oakland/7806.mp3",
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(G, "_fetch", lambda url: "WEBVTT\n\n0\n")
    monkeypatch.setattr(
        G.direct,
        "transcribe",
        lambda *a, **k: direct.DirectCapture(
            Path("transcript.json"),
            False,
            [{"start_ms": 0, "end_ms": 4000, "text": "The meeting is called to order."}],
            transcribed=True,
        ),
    )

    summary = pipeline.run(
        "7806",
        tmp_path,
        granicus_host="oakland.granicus.com",
        granicus_view_id=2,
        roster_path=OAKLAND,
        complete=lambda prompt: ("[]", {}),
        log=lambda m: None,
    )
    assert summary["caption_kind"] == "local_speech_to_text"
    transcript = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"]["caption_kind"] == "local_speech_to_text"
    assert transcript["source"]["capture"] == pipeline.TRANSCRIBED_CAPTURE


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
    from civiccast_brief.script.writer import ScriptLine, build_prompt

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
            ScriptLine("S2", text),
        ]
        return [i.message for i in check_script(recap, lines).issues if i.code == "outro"]

    assert outro("Watch the full meeting on the city's website.") == []
    assert outro("Watch it on the city's YouTube channel.") != []


def test_pipeline_judges_with_the_judge_writer_when_one_is_given(tmp_path, monkeypatch):
    clip = G.Clip(
        host="oakland.granicus.com", view_id=2, clip_id=7806, name="Council", date="October 6, 2026"
    )
    monkeypatch.setattr(G, "find_clip", lambda *a: clip)
    monkeypatch.setattr(
        G, "_fetch", lambda url: (FIX / "oakland-7806.vtt").read_text(encoding="utf-8")
    )
    used = []

    def judged(claims, report, *, out, complete, log, terms=()):
        used.append(complete)
        return claims

    monkeypatch.setattr(pipeline, "_judged", judged)

    def writer(prompt):
        return "[]", {}

    def judge(prompt):
        return "[]", {}

    common = {
        "granicus_host": "oakland.granicus.com",
        "granicus_view_id": 2,
        "roster_path": OAKLAND,
        "log": lambda m: None,
    }
    pipeline.run("7806", tmp_path / "a", complete=writer, judge_complete=judge, **common)
    pipeline.run("7806", tmp_path / "b", complete=writer, **common)
    assert used == [judge, writer]


def test_the_audio_fetch_names_the_program_to_the_host(tmp_path, monkeypatch):
    clip = G.Clip(
        host="goldenco.granicus.com",
        view_id=2,
        clip_id=6736,
        name="Council",
        date="November 24, 2026",
        mp3_url="https://x/a.mp3",
    )
    cap = G.GranicusCapture(
        clip=clip, caption_file=Path("clip.vtt"), caption_reused=False, items=[]
    )
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv
        Path(argv[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(G.subprocess, "run", run)
    G.fetch_audio(cap, tmp_path / "audio")
    argv = seen["argv"]
    assert argv[argv.index("-user_agent") + 1] == direct.FETCH_USER_AGENT
    assert argv.index("-user_agent") < argv.index("-i")


def test_the_published_agenda_is_read_as_item_lines():
    page = (FIX / "golden-agenda.html").read_text(encoding="utf-8", errors="replace")
    lines = G.parse_agenda(page)
    assert lines[0] == "1) Pledge of Allegiance"
    assert "a) Colorado Gives Day – December 8, 2026" in lines
    assert "10) Hunger Free Golden Update" in lines
    assert "12) Adjournment" in lines
    # A Document link outside the item rows is not an agenda item.
    assert not any("Small Business Saturday 2026" in line for line in lines)


def test_agenda_url_is_the_viewer_page():
    assert G.agenda_url("goldenco.granicus.com", 2, 6736) == (
        "https://goldenco.granicus.com/AgendaViewer.php?view_id=2&clip_id=6736"
    )
