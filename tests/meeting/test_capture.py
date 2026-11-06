import json
import subprocess
from pathlib import Path

import pytest

from civiccast_brief.meeting import capture as C


def test_video_id_must_look_like_a_youtube_id(tmp_path):
    with pytest.raises(C.CaptureError, match="not a YouTube video id"):
        C.capture_captions("bad id; rm -rf", tmp_path)


def test_bridge_payload_is_read_and_its_command_is_the_isolated_bridge(tmp_path, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        payload = {
            "video_id": "abc123XYZ_-",
            "caption_file": str(tmp_path / "abc.en.srv3"),
            "caption_reused": True,
            "info": {"title": "City Council Regular Session - 20 October 2026"},
            "segments": [{"start_ms": 0, "end_ms": 1000, "text": ">> hello"}],
        }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    result = C.capture_captions("abc123XYZ_-", tmp_path / "raw", python="python-test.exe")
    assert seen["argv"][:3] == ["python-test.exe", "-I", str(C.BRIDGE)]
    assert seen["argv"][3] == "abc123XYZ_-"
    assert result.caption_reused is True
    assert result.info["title"].startswith("City Council")
    assert result.segments[0]["text"] == ">> hello"


def test_bridge_error_becomes_a_capture_error(tmp_path, monkeypatch):
    def fake_run(argv, **kwargs):
        body = {"error": "yt-dlp failed", "bot_check": "sign in to confirm you're not a bot"}
        return subprocess.CompletedProcess(argv, 1, stdout=json.dumps(body).encode(), stderr=b"")

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    with pytest.raises(C.CaptureError, match="yt-dlp failed"):
        C.capture_captions("abc123XYZ_-", Path(tmp_path), python="python-test.exe")


def test_origin_reads_the_manual_subtitle_flag_from_the_info_file(tmp_path):
    (tmp_path / "abc.info.json").write_text(
        json.dumps({"subtitles": {}, "automatic_captions": {"en": []}}), encoding="utf-8"
    )
    assert C.caption_origin(tmp_path, "abc") == {"kind": "youtube_auto_captions"}
    (tmp_path / "abc.info.json").write_text(json.dumps({"subtitles": {"en": []}}), encoding="utf-8")
    assert C.caption_origin(tmp_path, "abc") == {"kind": "uploaded"}
    assert C.caption_origin(tmp_path, "missing")["kind"] == "unknown"
