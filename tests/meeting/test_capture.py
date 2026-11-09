import json

import pytest

from civiccast_brief.meeting import capture as C
from civiccast_brief.record import proc


def test_video_id_must_look_like_a_youtube_id(tmp_path):
    with pytest.raises(C.CaptureError, match="not a YouTube video id"):
        C.capture_captions("bad id; rm -rf", tmp_path)


SRV3 = (
    b'<?xml version="1.0" encoding="utf-8" ?><timedtext format="3"><body>'
    b'<p t="0" d="1000" w="1"><s ac="0">&gt;&gt; hello</s></p>'
    b"</body></timedtext>"
)


class FakeRunner:
    """Stands in for ``proc.run``: records the argv and writes what yt-dlp would write."""

    def __init__(self, work, returncode=0, stderr="", write=True):
        self.work, self.returncode, self.stderr, self.write = work, returncode, stderr, write
        self.calls = []

    def __call__(self, argv, *, timeout_s, env, cwd=None):
        self.calls.append(list(argv))
        if self.write:
            (self.work / "abc123XYZ_-.en.srv3").write_bytes(SRV3)
            info = {"id": "abc123XYZ_-", "title": "City Council Regular Session - 20 October 2026"}
            (self.work / "abc123XYZ_-.info.json").write_text(json.dumps(info), encoding="utf-8")
        return proc.ProcessResult(
            argv=tuple(argv), returncode=self.returncode, stdout=b"", stderr=self.stderr
        )


def test_captions_are_fetched_in_process_with_the_named_python(tmp_path):
    work = tmp_path / "raw"
    work.mkdir()
    runner = FakeRunner(work)
    result = C.capture_captions("abc123XYZ_-", work, python="python-test.exe", runner=runner)
    argv = runner.calls[0]
    assert argv[:3] == ["python-test.exe", "-m", "yt_dlp"]
    assert argv[-1] == "https://www.youtube.com/watch?v=abc123XYZ_-"
    assert "--skip-download" in argv
    assert result.caption_reused is False
    assert result.caption_file.name == "abc123XYZ_-.en.srv3"
    assert result.info["title"].startswith("City Council")
    assert result.segments == [{"start_ms": 0, "end_ms": 1000, "text": ">> hello"}]


def test_a_caption_file_already_there_is_reused_without_calling_youtube(tmp_path):
    (tmp_path / "abc123XYZ_-.en.srv3").write_bytes(SRV3)

    def no_call(*a, **k):
        pytest.fail("yt-dlp was called")

    result = C.capture_captions("abc123XYZ_-", tmp_path, runner=no_call)
    assert result.caption_reused is True
    assert result.segments[0]["text"] == ">> hello"


def test_a_failed_ytdlp_becomes_a_capture_error_with_the_bot_check(tmp_path):
    runner = FakeRunner(
        tmp_path, returncode=1, stderr="ERROR: Sign in to confirm you're not a bot", write=False
    )
    with pytest.raises(C.CaptureError, match="yt-dlp failed") as raised:
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner)
    assert "not a bot" in str(raised.value)


def test_ytdlp_that_writes_no_caption_file_is_a_plain_error(tmp_path):
    runner = FakeRunner(tmp_path, write=False)
    with pytest.raises(C.CaptureError, match="wrote no caption file"):
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner)


def test_a_missing_ytdlp_runtime_says_to_set_it_up(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICCAST_RECORD_RUNTIME_ROOT", str(tmp_path / "no-runtime"))
    with pytest.raises(C.CaptureError, match="yt-dlp is not set up yet"):
        C.ytdlp()


def test_origin_reads_the_manual_subtitle_flag_from_the_info_file(tmp_path):
    (tmp_path / "abc.info.json").write_text(
        json.dumps({"subtitles": {}, "automatic_captions": {"en": []}}), encoding="utf-8"
    )
    assert C.caption_origin(tmp_path, "abc") == {"kind": "youtube_auto_captions"}
    (tmp_path / "abc.info.json").write_text(json.dumps({"subtitles": {"en": []}}), encoding="utf-8")
    assert C.caption_origin(tmp_path, "abc") == {"kind": "uploaded"}
    assert C.caption_origin(tmp_path, "missing")["kind"] == "unknown"
