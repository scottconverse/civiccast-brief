import json
import time

import pytest

from civiccast_brief.script import llm
from civiccast_brief.script.cli import main
from civiccast_brief.script.recap import Claim, Recap, save_recap
from civiccast_brief.script.writer import DISCLAIMER, ScriptLine, save_script


def ndjson(*chunks: dict) -> list[bytes]:
    return [(json.dumps(chunk) + "\n").encode("utf-8") for chunk in chunks]


def test_read_stream_joins_chunks_and_reads_receipt():
    lines = ndjson(
        {"message": {"content": "[S1] Hel"}, "done": False},
        {"message": {"content": "lo."}, "done": False},
        {"done": True, "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 7},
    )
    text, receipt = llm.read_stream(lines, started=time.monotonic())
    assert text == "[S1] Hello."
    assert (receipt.prompt_tokens, receipt.output_tokens, receipt.done_reason) == (12, 7, "stop")


def test_read_stream_reports_error_chunk():
    with pytest.raises(llm.ModelError, match="boom"):
        llm.read_stream(ndjson({"error": "boom"}), started=time.monotonic())


def test_read_stream_flags_cut_off_answer():
    lines = ndjson(
        {"message": {"content": "[S1] unfinished"}},
        {"done": True, "done_reason": "length", "eval_count": 32000},
    )
    with pytest.raises(llm.ModelCutOff):
        llm.read_stream(lines, started=time.monotonic())


def test_read_stream_rejects_empty_answer():
    with pytest.raises(llm.ModelError, match="empty"):
        llm.read_stream(ndjson({"done": True, "done_reason": "stop"}), started=time.monotonic())


def _fixture_recap(tmp_path):
    recap = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="October 20, 2026",
        video_id="rsagM9xn28Y",
        claims=(
            Claim(
                "c01",
                "Meeting",
                "The Longmont City Council met on October 20, 2026.",
                "0:00:00",
                "Longmont City Council Regular Session - October 20, 2026",
            ),
            Claim(
                "c02",
                "Live stream",
                "The meeting video is on the city's YouTube channel.",
                "0:05:20",
                "viewed at the city's YouTube channel",
            ),
        ),
    )
    path = tmp_path / "recap.json"
    save_recap(recap, path)
    return path


def test_cli_check_fails_without_the_youtube_outro_and_writes_report(tmp_path, capsys):
    recap_path = _fixture_recap(tmp_path)
    lines = [
        ScriptLine(
            "S1", "CivicCast Brief, Longmont, October twentieth, twenty twenty-six.", ("c01",)
        ),
        ScriptLine("S2", DISCLAIMER),
        ScriptLine("S1", "The meeting video is on the city's YouTube channel.", ("c02",)),
        ScriptLine("S2", "That's a big number.", ()),
    ]
    script_path = tmp_path / "script.json"
    save_script(lines, tmp_path / "script.txt", script_path)
    report_path = tmp_path / "report.txt"
    code = main(
        [
            "check",
            "--recap",
            str(recap_path),
            "--script",
            str(script_path),
            "--report",
            str(report_path),
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "CHECK RESULT: FAIL" in out
    assert "outro" in out
    assert report_path.read_text(encoding="utf-8").startswith("CHECK RESULT: FAIL")


def test_cli_check_exit_zero_when_clean(tmp_path, capsys):
    recap_path = _fixture_recap(tmp_path)
    lines = [
        ScriptLine(
            "S1", "CivicCast Brief, Longmont, October twentieth, twenty twenty-six.", ("c01",)
        ),
        ScriptLine("S2", DISCLAIMER),
        ScriptLine("S1", "The meeting video is on the city's YouTube channel.", ("c02",)),
        ScriptLine("S2", "Watch the video on the city's YouTube channel.", ("c02",)),
    ]
    script_path = tmp_path / "script.json"
    save_script(lines, tmp_path / "script.txt", script_path)
    code = main(["check", "--recap", str(recap_path), "--script", str(script_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "CHECK RESULT: PASS" in out


def test_cli_rejects_missing_command():
    with pytest.raises(SystemExit):
        main([])


def test_cli_write_drops_a_chapter_mark_with_no_line_in_it(tmp_path, monkeypatch, capsys):
    from civiccast_brief.audio.lines import read_script
    from civiccast_brief.script import cli

    recap_path = _fixture_recap(tmp_path)
    answer = "\n".join(
        json.dumps(line)
        for line in [
            {"speaker": "S1", "text": "CivicCast Brief, Longmont.", "claim_ids": ["c01"]},
            {"speaker": "S2", "text": DISCLAIMER, "claim_ids": []},
            {"speaker": "CHAPTER", "text": "October 20 meeting", "claim_ids": ["c01"]},
            {"speaker": "CHAPTER", "text": "Live stream", "claim_ids": ["c02"]},
            {"speaker": "S1", "text": "The meeting video is online.", "claim_ids": ["c02"]},
            {
                "speaker": "S2",
                "text": "Watch it on the city's YouTube channel.",
                "claim_ids": ["c02"],
            },
        ]
    )
    monkeypatch.setattr(cli, "_writer", lambda args, recap: lambda prompt: answer)
    out = tmp_path / "script.txt"
    main(["write", "--recap", str(recap_path), "--out", str(out)])

    assert "dropped empty chapter: October 20 meeting" in capsys.readouterr().out
    assert [c.title for c in read_script(out).chapters] == ["Live stream"]
