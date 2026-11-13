"""Cablecast capture: the show list, the caption segments joined, the pipeline branch.

The fixtures in ``fixtures/cablecast`` are real answers from reflect-cityofloveland-co.cablecast.tv
(Loveland, CO), fetched 2026-11-12 and trimmed: the show rows keep their id, title, eventDate and
vods; the caption playlist lists five of its 1,569 segments; five real segments are kept.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from civiccast_brief.meeting import cablecast as C
from civiccast_brief.meeting import pipeline
from civiccast_brief.meeting.roster import ROSTERS_DIR

FIX = Path(__file__).parent / "fixtures" / "cablecast"
HOST = "reflect-cityofloveland-co.cablecast.tv"
API = f"https://{HOST}/cablecastapi/v1"
STORE = f"https://{HOST}/store-3/746-cc-v2/"
STORE_732 = f"https://{HOST}/store-3/732-Planning-Commission-August-24-2026-v2/"
OAKLAND = ROSTERS_DIR / "oakland.json"


def _text(name):
    return (FIX / name).read_text(encoding="utf-8")


def _answers():
    """Every address the capture asks for, answered from the saved real files."""
    answers = {
        f"{API}/shows/746": _text("show-746.json"),
        f"{API}/vods/530": _text("vod-530.json"),
        f"{API}/shows/732": _text("show-732.json"),
        f"{API}/vods/521": _text("vod-732.json"),
        STORE + "vod.m3u8": _text("vod.m3u8"),
        STORE + "captions.en.m3u8": _text("captions.en.m3u8"),
        STORE_732 + "vod.m3u8": _text("vod-732.m3u8"),
    }
    for number in ("00000", "00499", "00500", "00501", "01568"):
        suffix = "13.167" if number == "01568" else "10.000"
        answers[f"{STORE}subtitles/captions.en.{number}.vtt?duration={suffix}"] = _text(
            f"captions.en.{number}.vtt"
        )
    return answers


@pytest.fixture
def web(monkeypatch):
    answers = _answers()
    asked = []

    def fetch(url):
        asked.append(url)
        if url not in answers:
            raise C.CablecastError(f"could not fetch {url}: HTTP Error 404: Not Found")
        return answers[url]

    monkeypatch.setattr(C, "_fetch", fetch)
    return asked


def test_the_list_keeps_shows_with_a_video_once_each_and_dates_them_by_title(monkeypatch):
    shows = C.parse_shows(json.loads(_text("shows.json")), HOST)
    # Skipped: 745 "cc", 743 and 742, 736 and 664 (no vods); 726 repeats 732's title and day.
    assert [(s.show_id, s.vod_id, s.date) for s in shows] == [
        (750, 533, "November 10, 2026"),
        (749, 534, "November 10, 2026"),
        (748, 532, "November 9, 2026"),
        (746, 530, "November 4, 2026"),
        (744, 529, "October 27, 2026"),
        (740, 527, "October 20, 2026"),
        (737, 525, "October 6, 2026"),
        # eventDate says September 16; the title names the meeting's own day.
        (732, 521, "August 24, 2026"),
        # eventDate says January 28; the meeting was January 27.
        (665, 474, "January 27, 2026"),
    ]
    assert shows[3].name == "City Council Meeting November 4, 2026"
    assert shows[5].name == "City Council - Oct 20 2026"
    # The list is one request for the newest shows of every body, with no search term.
    asked = []
    monkeypatch.setattr(C, "_fetch", lambda url: asked.append(url) or _text("shows.json"))
    assert [s.show_id for s in C.list_shows(HOST)] == [s.show_id for s in shows]
    assert asked == [f"{API}/shows?pageSize=200"]


def test_a_show_without_a_day_is_not_listed():
    row = {"id": 1, "title": "Untitled", "eventDate": "soon", "vods": [9]}
    assert C.parse_shows({"shows": [row]}, HOST) == []


def test_the_vod_playlist_names_the_caption_playlist_and_its_segments():
    assert C.subtitles_uri(_text("vod.m3u8")) == "captions.en.m3u8"
    assert C.subtitles_uri(_text("vod-732.m3u8")) is None
    assert C.segment_uris(_text("captions.en.m3u8")) == [
        "subtitles/captions.en.00000.vtt?duration=10.000",
        "subtitles/captions.en.00499.vtt?duration=10.000",
        "subtitles/captions.en.00500.vtt?duration=10.000",
        "subtitles/captions.en.00501.vtt?duration=10.000",
        "subtitles/captions.en.01568.vtt?duration=13.167",
    ]


def test_segments_join_with_absolute_times_and_a_repeated_cue_is_kept_once():
    parts = [_text(f"captions.en.{n}.vtt") for n in ("00000", "00499", "00500", "00501")]
    items = C.join_segments(parts)
    assert len(items) == 9
    assert items[0] == {
        "start_ms": (1 * 3600 + 23 * 60 + 13) * 1000 + 100,
        "end_ms": (1 * 3600 + 23 * 60 + 15) * 1000 + 601,
        "text": "that an item on the consent agenda be moved to the regular",
    }
    assert items[-1]["end_ms"] == (1 * 3600 + 23 * 60 + 44) * 1000 + 367
    starts = [i["start_ms"] for i in items]
    assert starts == sorted(starts)
    # A segment seen twice (the edge of two segments) adds nothing and moves nothing.
    again = C.join_segments([parts[1], parts[2], parts[2], parts[3], parts[1]])
    assert again == items


def test_capture_joins_the_segments_and_saves_the_show(tmp_path, web, monkeypatch):
    cap = C.capture(HOST, 746, tmp_path)
    assert cap.show == C.Show(
        HOST,
        746,
        530,
        "City Council Meeting November 4, 2026",
        "November 4, 2026",
        STORE + "vod.mp4",
        15694,
    )
    assert cap.meeting_id == "cablecast-reflect-cityofloveland-co-746"
    assert cap.video_url == f"https://{HOST}/internetchannel/show/746"
    assert len(cap.items) == 12
    assert cap.items[-1]["start_ms"] == (4 * 3600 + 21 * 60 + 29) * 1000 + 968
    assert not cap.caption_reused
    assert (tmp_path / "746.captions.vtt").read_text(encoding="utf-8").startswith("WEBVTT\n\n")

    def no_network(url):
        raise AssertionError("network used")

    monkeypatch.setattr(C, "_fetch", no_network)
    again = C.capture(HOST, 746, tmp_path)
    assert again.caption_reused and again.show == cap.show and again.items == cap.items


def test_a_vod_with_no_caption_playlist_stops_with_the_reason(tmp_path, web):
    with pytest.raises(C.CablecastError, match="has no caption lines.*does not transcribe audio"):
        C.capture(HOST, 732, tmp_path)


def test_a_missing_vod_playlist_stops_with_the_reason(tmp_path, monkeypatch, web):
    real = C._fetch

    def fetch(url):
        if url.endswith("vod.m3u8"):
            raise C.CablecastError(f"could not fetch {url}: HTTP Error 404: Not Found")
        return real(url)

    monkeypatch.setattr(C, "_fetch", fetch)
    with pytest.raises(C.CablecastError, match="no vod.m3u8 playlist"):
        C.capture(HOST, 746, tmp_path)


def test_caption_segments_with_no_lines_stop_with_the_reason(tmp_path, monkeypatch, web):
    real = C._fetch
    empty = _text("captions.en.00000.vtt")
    monkeypatch.setattr(C, "_fetch", lambda url: empty if "/subtitles/" in url else real(url))
    with pytest.raises(C.CablecastError, match="has no caption lines"):
        C.capture(HOST, 746, tmp_path)


def test_a_segment_on_another_host_is_not_fetched(tmp_path, monkeypatch, web):
    real = C._fetch
    playlist = "#EXTM3U\n#EXTINF:10.000,\nhttps://elsewhere.example.com/captions.vtt\n"
    monkeypatch.setattr(
        C, "_fetch", lambda url: playlist if url.endswith("captions.en.m3u8") else real(url)
    )
    with pytest.raises(C.CablecastError, match="is not on"):
        C.capture(HOST, 746, tmp_path)


def test_audio_is_cut_from_the_vod_mp4_by_ffmpeg(tmp_path, web, monkeypatch):
    cap = C.capture(HOST, 746, tmp_path / "raw")
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv
        Path(argv[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(C.subprocess, "run", run)
    wav = C.fetch_audio(cap, tmp_path / "audio")
    argv = seen["argv"]
    assert wav.name == "cablecast-reflect-cityofloveland-co-746.wav"
    assert argv[argv.index("-i") + 1] == STORE + "vod.mp4"
    assert re.search(r"-ac 1 -ar 16000", " ".join(argv))


def _complete(prompt):
    return "[]", {}


def test_pipeline_runs_a_cablecast_show(tmp_path, web):
    summary = pipeline.run(
        "746",
        tmp_path,
        cablecast_host=HOST,
        roster_path=OAKLAND,
        complete=_complete,
        log=lambda m: None,
    )
    assert summary["video_id"] == "cablecast-reflect-cityofloveland-co-746"
    assert summary["meeting_date"] == "November 4, 2026"
    assert summary["caption_kind"] == "cablecast_captions"
    transcript = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"]["platform"] == "cablecast"
    assert transcript["source"]["show_id"] == 746
    assert transcript["source"]["video_url"] == f"https://{HOST}/internetchannel/show/746"
    recap = json.loads((tmp_path / "recap.json").read_text(encoding="utf-8"))
    assert recap["video_place"] == "website"


def test_pipeline_reports_a_show_with_no_captions_as_a_pipeline_error(tmp_path, web):
    with pytest.raises(pipeline.PipelineError, match="has no caption lines"):
        pipeline.run(
            "732",
            tmp_path,
            cablecast_host=HOST,
            roster_path=OAKLAND,
            complete=_complete,
            log=lambda m: None,
        )
