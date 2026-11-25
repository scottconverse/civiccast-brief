import json
from datetime import UTC, datetime

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


def test_a_live_event_that_has_not_started_is_not_a_failure(tmp_path):
    runner = FakeRunner(
        tmp_path,
        returncode=1,
        stderr="ERROR: [youtube] AhbV6EgmQJE: This live event will begin in a few moments",
        write=False,
    )

    class NoPanel:
        def transcript_panel(self, url):
            pytest.fail("the transcript panel was read for a stream that has not started")

    with pytest.raises(C.LiveNotStarted) as raised:
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner, browser=NoPanel())
    assert str(raised.value) == C.LIVE_NOT_STARTED_REASON
    assert "live event that has not started yet" in str(raised.value)


def test_a_premiere_that_has_not_started_is_not_a_failure(tmp_path):
    runner = FakeRunner(tmp_path, returncode=1, stderr="ERROR: Premieres in 2 hours", write=False)
    with pytest.raises(C.LiveNotStarted):
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner)


def test_a_real_caption_failure_is_still_a_capture_error(tmp_path):
    runner = FakeRunner(
        tmp_path,
        returncode=1,
        stderr="ERROR: unable to download video data: HTTP Error 403",
        write=False,
    )
    with pytest.raises(C.CaptureError) as raised:
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner)
    assert not isinstance(raised.value, C.LiveNotStarted)


# ----- A video posted too recently for YouTube's captions to exist ----------------------------


class NoCaptionsRunner:
    """yt-dlp succeeds but writes no caption file; only the info file lands, as in a fresh post."""

    def __init__(self, work, info):
        self.work, self.info = work, info

    def __call__(self, argv, *, timeout_s, env, cwd=None):
        (self.work / "abc123XYZ_-.info.json").write_text(json.dumps(self.info), encoding="utf-8")
        return proc.ProcessResult(argv=tuple(argv), returncode=0, stdout=b"", stderr="")


class NoPanel:
    def transcript_panel(self, url):
        raise C.panel.PanelError("the page has no Show transcript button")


def test_a_two_day_old_video_with_no_captions_is_not_yet(tmp_path):
    runner = NoCaptionsRunner(tmp_path, {"id": "abc123XYZ_-", "upload_date": "20261123"})
    with pytest.raises(C.CaptionsNotYet, match="not posted this video's captions yet") as raised:
        C.capture_captions(
            "abc123XYZ_-",
            tmp_path,
            python="py",
            runner=runner,
            browser=NoPanel(),
            now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        )
    assert "posted 2026-11-23" in str(raised.value)


def test_a_ten_day_old_video_with_no_captions_is_a_plain_error(tmp_path):
    runner = NoCaptionsRunner(tmp_path, {"id": "abc123XYZ_-", "upload_date": "20261115"})
    with pytest.raises(C.CaptureError) as raised:
        C.capture_captions(
            "abc123XYZ_-",
            tmp_path,
            python="py",
            runner=runner,
            browser=NoPanel(),
            now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        )
    assert not isinstance(raised.value, C.CaptionsNotYet)
    assert "no Show transcript button" in str(raised.value)


def test_the_release_time_wins_over_the_upload_date(tmp_path):
    """A stream released two days ago but uploaded ten days ago is still "not yet"."""
    runner = NoCaptionsRunner(
        tmp_path,
        {
            "id": "abc123XYZ_-",
            "upload_date": "20261115",
            "release_timestamp": int(datetime(2026, 11, 23, 8, tzinfo=UTC).timestamp()),
        },
    )
    with pytest.raises(C.CaptionsNotYet, match="posted 2026-11-23"):
        C.capture_captions(
            "abc123XYZ_-",
            tmp_path,
            python="py",
            runner=runner,
            browser=NoPanel(),
            now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        )


def test_no_info_file_means_the_missing_captions_stay_a_plain_error(tmp_path):
    with pytest.raises(C.CaptureError) as raised:
        C.capture_captions(
            "abc123XYZ_-",
            tmp_path,
            python="py",
            runner=FakeRunner(tmp_path, write=False),
            browser=NoPanel(),
            now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        )
    assert not isinstance(raised.value, C.CaptionsNotYet)


# ----- The meeting command's exit code -------------------------------------------------------


def test_the_meeting_command_exits_3_for_a_stream_that_has_not_started(tmp_path, monkeypatch):
    from civiccast_brief.meeting import cli

    def not_started(*a, **k):
        raise C.LiveNotStarted(C.LIVE_NOT_STARTED_REASON)

    monkeypatch.setattr(cli, "run", not_started)
    code = cli.main(["run", "abc123XYZ_-", "--out", str(tmp_path)])
    assert code == cli.LIVE_NOT_STARTED_EXIT == 3


def test_the_meeting_command_exits_4_when_the_captions_are_not_posted_yet(
    tmp_path, monkeypatch, capsys
):
    from civiccast_brief.meeting import cli

    def not_yet(*a, **k):
        raise C.CaptionsNotYet(f"{C.CAPTIONS_NOT_YET_REASON} (posted 2026-11-23)")

    monkeypatch.setattr(cli, "run", not_yet)
    code = cli.main(["run", "abc123XYZ_-", "--out", str(tmp_path)])
    assert code == cli.CAPTIONS_NOT_YET_EXIT == 4
    assert "not run: YouTube has not posted this video's captions yet" in capsys.readouterr().err


def test_the_meeting_command_still_exits_1_for_a_caption_failure(tmp_path, monkeypatch):
    from civiccast_brief.meeting import cli

    def failed(*a, **k):
        raise C.CaptureError("caption fetch failed for abc: yt-dlp failed")

    monkeypatch.setattr(cli, "run", failed)
    code = cli.main(["run", "abc123XYZ_-", "--out", str(tmp_path)])
    assert code == 1


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


# ----- The "Show transcript" panel (spec 8.4.2) -------------------------------------------------


class PanelBrowser:
    """The watch page's transcript panel, as Chrome reads it (Longmont P&Z, 21 Oct 2026)."""

    def __init__(self):
        self.asked = []

    def transcript_panel(self, url):
        self.asked.append(url)
        rows = [
            ["0:09", "pleasure of calling the meeting to order"],
            ["0:01", "Hello guys, good evening folks."],
            ["1:02:03", "  the public hearing\nis open  "],
            ["2:28:34", "And we are in adjournment."],
        ]
        return {"rows": rows, "length": 8926}


def test_when_ytdlp_gets_no_captions_the_transcript_panel_is_read(tmp_path):
    runner = FakeRunner(
        tmp_path, returncode=1, stderr="ERROR: Sign in to confirm you're not a bot", write=False
    )
    chrome = PanelBrowser()
    result = C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner, browser=chrome)
    assert chrome.asked == ["https://www.youtube.com/watch?v=abc123XYZ_-"]
    assert result.segments == [
        {"start_ms": 1000, "end_ms": 9000, "text": "Hello guys, good evening folks."},
        {"start_ms": 9000, "end_ms": 3723000, "text": "pleasure of calling the meeting to order"},
        {"start_ms": 3723000, "end_ms": 8914000, "text": "the public hearing is open"},
        {"start_ms": 8914000, "end_ms": 8926000, "text": "And we are in adjournment."},
    ]
    assert C.caption_origin(tmp_path, "abc123XYZ_-") == {"kind": "youtube_transcript_panel"}

    # The next run reads the kept panel file and asks neither yt-dlp nor Chrome.
    def no_call(*a, **k):
        pytest.fail("yt-dlp was called")

    again = C.capture_captions("abc123XYZ_-", tmp_path, runner=no_call, browser=None)
    assert again.caption_reused is True and again.segments == result.segments


def test_when_the_panel_fails_too_both_reasons_are_given(tmp_path):
    runner = FakeRunner(tmp_path, write=False)
    with pytest.raises(C.CaptureError) as raised:
        C.capture_captions("abc123XYZ_-", tmp_path, python="py", runner=runner)
    assert "wrote no caption file" in str(raised.value)
    assert "Show transcript panel: no Chrome in tests" in str(raised.value)
