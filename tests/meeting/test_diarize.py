import json
import subprocess

import pytest

from civiccast_brief.meeting import diarize as D
from civiccast_brief.meeting import pipeline
from civiccast_brief.meeting import transcript as T
from civiccast_brief.meeting.speakers import speakers_report, split_turns
from civiccast_brief.meeting.transcript import Segment


def seg(start, end, text="x", turn=False):
    return Segment(start=start, end=end, text=text, turn_start=turn)


def test_each_line_gets_the_voice_that_overlaps_it_most():
    segments = [seg(0, 2), seg(2, 4), seg(4, 6)]
    spans = [D.Span(0, 2.5, "SPEAKER_07"), D.Span(2.5, 6, "SPEAKER_02")]
    counts = D.assign_voices(segments, spans)
    assert [s.voice for s in segments] == ["voice 01", "voice 02", "voice 02"]
    assert counts["voices"] == 2
    assert counts["labels"] == {"SPEAKER_07": "voice 01", "SPEAKER_02": "voice 02"}


def test_lines_no_voice_overlaps_get_none():
    segments = [seg(0, 1), seg(10, 11)]
    counts = D.assign_voices(segments, [D.Span(0, 1, "A")])
    assert [s.voice for s in segments] == ["voice 01", None]
    assert counts["segments_with_voice"] == 1
    assert counts["segments_without_voice"] == 1


def test_unsorted_and_overlapping_spans_still_pick_the_largest_overlap():
    segments = [seg(5, 9)]
    spans = [D.Span(6, 9, "B"), D.Span(0, 6, "A")]
    D.assign_voices(segments, spans)
    assert segments[0].voice == "voice 01"  # B overlaps 3 s, A 1 s
    assert D.assign_voices(segments, spans)["labels"] == {"B": "voice 01"}


def test_voice_round_trips_through_transcript_json():
    segments = [seg(0, 1)]
    segments[0].voice = "voice 03"
    data = T.to_json(segments, video_id="v", title="t", meeting_date="2026-10-20", source={})
    back = T.from_json(json.loads(json.dumps(data)))
    assert back[0].voice == "voice 03"


def test_a_voice_never_becomes_a_name():
    segments = [seg(0, 1, "hello", turn=True)]
    D.assign_voices(segments, [D.Span(0, 1, "A")])
    turns = split_turns(segments)
    report = speakers_report("v", segments, turns, {"calls": []}, {"status": "run"})
    assert report["turns"][0]["speaker"] == "unknown"
    assert report["turns"][0]["voice"] == "voice 01"
    assert "textflowkit diarization" in report["method"]


def test_bridge_error_is_raised_with_its_reason(tmp_path, monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout=b'{"error": "no model in cache"}\n')

    monkeypatch.setattr(D.subprocess, "run", fake_run)
    with pytest.raises(D.DiarizeError, match="no model in cache"):
        D.run_diarization(tmp_path / "a.wav", tmp_path / "out.json", python="py")


def test_existing_diarization_file_is_reused(tmp_path, monkeypatch):
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"turns": [{"start": 0, "end": 1, "speaker": "A"}]}))
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: pytest.fail("bridge was called"))
    assert D.spans_from(D.run_diarization(tmp_path / "a.wav", out)) == [D.Span(0, 1, "A")]


def test_pipeline_records_a_failed_diarization_and_goes_on(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise D.DiarizeError("audio fetch failed for v")

    segments = [seg(0, 1)]
    status = pipeline._diarize("v", tmp_path, segments, True, boom, None, lambda m: None)
    assert status == {"status": "failed", "reason": "audio fetch failed for v"}
    assert segments[0].voice is None


def test_pipeline_not_requested():
    status = pipeline._diarize("v", None, [], False, None, None, lambda m: None)
    assert status["status"] == "not run"
