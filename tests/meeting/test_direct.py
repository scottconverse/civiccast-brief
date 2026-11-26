"""Direct-media capture: the source's captions, or this machine's speech program.

Every test uses fakes: no network, no GPU and no real transcription.
"""

import json
import subprocess
import wave
from pathlib import Path

import pytest

from civiccast_brief.meeting import direct


def _audio(monkeypatch):
    """A stand-in for fetch_audio that writes an empty WAV where the real one would go."""

    def fetch_audio(media_url, work_dir, meeting_id, *, ffmpeg="ffmpeg"):
        work_dir.mkdir(parents=True, exist_ok=True)
        wav = work_dir / f"{meeting_id}.wav"
        wav.write_bytes(b"RIFF")
        return wav

    monkeypatch.setattr(direct, "fetch_audio", fetch_audio)


def _runner(argv_seen: list[list[str]], segments=None):
    """A stand-in for the speech program: writes its JSON and prints the path."""
    written = segments if segments is not None else [{"start": 1.0, "end": 2.5, "text": "Hello"}]

    def run(argv, timeout_s):
        argv_seen.append(argv)
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "meeting.json"
        path.write_text(json.dumps({"segments": written}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=f"{path}\n".encode(), stderr=b"")

    return run


def test_a_meeting_without_captions_is_transcribed_from_its_audio(tmp_path, monkeypatch):
    _audio(monkeypatch)
    seen: list[list[str]] = []

    captured = direct.transcribe(
        "https://cdn.test/9.mp4", tmp_path / "raw", "civicclerk-erieco-9", runner=_runner(seen)
    )

    assert captured.transcribed is True
    assert captured.caption_reused is False
    assert captured.caption_file.name == "meeting.json"
    assert captured.items == [{"start_ms": 1000, "end_ms": 2500, "text": "Hello"}]
    assert "transcribe" in seen[0]
    speech = direct.speech_command()
    assert seen[0][: len(speech)] == speech


def test_the_timeout_comes_from_the_wav_length(tmp_path, monkeypatch):
    wav = tmp_path / "meeting-9.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 32000)  # two seconds at 16 kHz
    monkeypatch.setattr(direct, "fetch_audio", lambda *a, **k: wav)
    timeouts: list[float] = []

    def run(argv, timeout_s):
        timeouts.append(timeout_s)
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "meeting.json"
        path.write_text(
            json.dumps({"segments": [{"start": 1.0, "end": 2.5, "text": "Hello"}]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout=f"{path}\n".encode(), stderr=b"")

    direct.transcribe("https://cdn.test/9.mp4", tmp_path / "raw", "meeting-9", runner=run)

    assert timeouts == [direct.transcription_timeout_s(2.0)]


def test_a_saved_transcript_is_reused_without_running_the_speech_program(tmp_path, monkeypatch):
    _audio(monkeypatch)
    raw = tmp_path / "raw"
    direct.transcribe("https://cdn.test/9.mp4", raw, "meeting-9", runner=_runner([]))

    def refuse(argv, timeout_s):
        raise AssertionError("the speech program ran again")

    again = direct.transcribe("https://cdn.test/9.mp4", raw, "meeting-9", runner=refuse)

    assert again.caption_reused is True
    assert again.transcribed is True
    assert again.items[0]["text"] == "Hello"


def test_an_empty_transcription_is_a_failure_with_the_reason(tmp_path, monkeypatch):
    _audio(monkeypatch)

    with pytest.raises(direct.DirectError, match="transcribed .* to no lines"):
        direct.transcribe(
            "https://cdn.test/9.mp4", tmp_path / "raw", "meeting-9", runner=_runner([], segments=[])
        )


def test_a_failed_speech_program_is_a_failure_with_the_reason(tmp_path, monkeypatch):
    _audio(monkeypatch)

    def failed(argv, timeout_s):
        return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"model load failed\n")

    with pytest.raises(direct.DirectError, match="transcription failed.*model load failed"):
        direct.transcribe("https://cdn.test/9.mp4", tmp_path / "raw", "meeting-9", runner=failed)


def test_capture_reads_captions_when_listed_and_transcribes_otherwise(tmp_path, monkeypatch):
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello town\n"
    monkeypatch.setattr(direct, "_fetch", lambda url: vtt)

    listed = direct.capture("https://cdn.test/9.mp4", "https://cdn.test/9.vtt", tmp_path / "raw")
    assert listed.transcribed is False
    assert listed.items[0]["text"] == "Hello town"

    transcribed = direct.DirectCapture(Path("transcript.json"), False, [], transcribed=True)
    monkeypatch.setattr(direct, "transcribe", lambda *a, **k: transcribed)
    assert direct.capture("https://cdn.test/9.mp4", None, tmp_path / "other") is transcribed

    with pytest.raises(direct.DirectError, match=direct.NO_MEDIA):
        direct.capture("", None, tmp_path / "none")
