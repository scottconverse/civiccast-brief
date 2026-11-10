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


class PublisherTrackRunner(FakeRunner):
    """The first request writes YouTube's en-orig file and a sidecar that lists an uploaded "en"."""

    def __call__(self, argv, *, timeout_s, env, cwd=None):
        self.calls.append(list(argv))
        track = argv[argv.index("--sub-langs") + 1]
        (self.work / f"abc123XYZ_-.{track}.srv3").write_bytes(SRV3)
        info = {"id": "abc123XYZ_-", "title": "t", "subtitles": {"en": [{"ext": "srv3"}]}}
        (self.work / "abc123XYZ_-.info.json").write_text(json.dumps(info), encoding="utf-8")
        return proc.ProcessResult(argv=tuple(argv), returncode=0, stdout=b"", stderr="")


def test_a_publisher_track_is_fetched_by_a_second_request(tmp_path):
    """Spec 8.3: en-orig first; the town's own "en" track by a second request, and it is used."""
    work = tmp_path / "raw"
    work.mkdir()
    runner = PublisherTrackRunner(work)
    result = C.capture_captions("abc123XYZ_-", work, python="python-test.exe", runner=runner)
    first, second = runner.calls
    assert first[first.index("--sub-langs") + 1] == "en-orig"
    assert second[second.index("--sub-langs") + 1] == "en"
    assert "--write-auto-subs" not in second
    assert result.caption_file.name == "abc123XYZ_-.en.srv3"


def test_youtube_captions_only_make_one_request(tmp_path):
    work = tmp_path / "raw"
    work.mkdir()
    runner = FakeRunner(work)
    C.capture_captions("abc123XYZ_-", work, python="python-test.exe", runner=runner)
    assert len(runner.calls) == 1


class PathsRunner:
    """Writes the caption where yt-dlp does: a relative ``-o`` name is joined onto ``--paths``."""

    def __call__(self, argv, *, timeout_s, env, cwd=None):
        from pathlib import Path

        out = Path(argv[argv.index("-o") + 1])
        if not out.is_absolute():
            out = Path(argv[argv.index("--paths") + 1]) / out
        folder = out.parent
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "abc123XYZ_-.en-orig.srv3").write_bytes(SRV3)
        info = {"id": "abc123XYZ_-", "title": "City Council Regular Session"}
        (folder / "abc123XYZ_-.info.json").write_text(json.dumps(info), encoding="utf-8")
        return proc.ProcessResult(argv=tuple(argv), returncode=0, stdout=b"", stderr="")


def test_a_relative_work_folder_finds_the_captions_yt_dlp_wrote(tmp_path, monkeypatch):
    # The weekly run passes folders like runs/weekly/<meeting>/raw. yt-dlp put the files in
    # raw/runs/weekly/<meeting>/raw and the capture said there were no captions.
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    result = C.capture_captions(
        "abc123XYZ_-", Path("runs/m/raw"), python="py", runner=PathsRunner()
    )
    assert result.caption_file == tmp_path / "runs" / "m" / "raw" / "abc123XYZ_-.en-orig.srv3"
    assert result.segments == [{"start_ms": 0, "end_ms": 1000, "text": ">> hello"}]
