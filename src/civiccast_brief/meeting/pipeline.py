"""Run the Phase 4 stages for one YouTube meeting and write each stage's file.

out/raw/                caption file and info from the TownRecord bridge
out/transcript.json     normalized caption lines, with speaker labels
out/speakers.json       turns, roll-call decisions and the diarization status
out/audio/              the meeting audio (only with diarize=True)
out/diarization.json    textflowkit's voice spans (only with diarize=True)
out/draft.json          the model's draft items per window (with receipts)
out/draft-answers/      the model's raw answer per window
out/judge.json          the claim judge's verdict on every claim that passed the code checks
out/recap.json          the verified recap (Phase 2 recap format)
out/verify-report.json  every dropped draft item and its reason (judge drops included)
out/summary.json        the numbers for the run
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from civiccast_brief.meeting import diarize as diarize_mod
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.meeting.agenda import load_agenda
from civiccast_brief.meeting.capture import caption_origin, capture_captions
from civiccast_brief.meeting.judge import judge as judge_claims
from civiccast_brief.meeting.recap import (
    DEFAULT_WINDOW_S,
    WindowDraft,
    build_prompt,
    draft_window,
    windows,
)
from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.meeting.speakers import (
    apply_turns,
    label_roll_call,
    speakers_report,
    split_turns,
)
from civiccast_brief.meeting.verify import TranscriptIndex, verify
from civiccast_brief.script import llm
from civiccast_brief.script.recap import Claim, Recap, save_recap

Complete = Callable[[str], tuple[str, Any]]


class PipelineError(RuntimeError):
    """A stage cannot continue (for example, no meeting date can be read)."""


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(
    video_id: str,
    out_dir: Path,
    *,
    townrecord_python: str | None = None,
    roster_path: Path | None = None,
    agenda_path: Path | None = None,
    window_s: float = DEFAULT_WINDOW_S,
    model: str = llm.DEFAULT_MODEL,
    reuse_draft: bool = False,
    diarize: bool = False,
    textflowkit_python: str | None = None,
    complete: Complete | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw"

    log(f"[{video_id}] capture: TownRecord caption command")
    capture = capture_captions(video_id, raw_dir, python=townrecord_python)
    title = str(capture.info.get("title") or "")
    meeting_date = transcript_mod.meeting_date_from_title(title)
    if meeting_date is None:
        raise PipelineError(f"no meeting date in the title {title!r}")
    segments = transcript_mod.segments_from_capture(capture.segments)
    origin = caption_origin(raw_dir, video_id)
    source = {
        "platform": "youtube",
        "video_id": video_id,
        "caption_kind": origin["kind"],
        "caption_file": capture.caption_file.name,
        "caption_download_reused": capture.caption_reused,
        "capture": (
            "TownRecord caption command (townrecord.capture.command) run in TownRecord's venv "
            "by townrecord_bridge.py; srv3 parsed by townrecord.captions.parse"
        ),
    }

    roster = load_roster(roster_path)
    turns = split_turns(segments)
    roll_call = label_roll_call(turns, roster)
    apply_turns(segments, turns)
    diarization = _diarize(
        video_id, out, segments, diarize, townrecord_python, textflowkit_python, log
    )
    speakers = speakers_report(video_id, segments, turns, roll_call, diarization)
    speakers["roster"] = {
        "file": str(roster_path) if roster_path else "default",
        "city": roster.city,
        "body": roster.body,
        "sources": list(roster.sources),
    }
    _write(
        out / "transcript.json",
        transcript_mod.to_json(
            segments, video_id=video_id, title=title, meeting_date=meeting_date, source=source
        ),
    )
    _write(out / "speakers.json", speakers)
    log(
        f"[{video_id}] speakers: {speakers['counts']['named_segments']} of "
        f"{speakers['counts']['segments']} caption lines named"
    )

    agenda = load_agenda(agenda_path)
    vocabulary = agenda.vocabulary if agenda else frozenset()
    index = TranscriptIndex(segments)
    cut = windows(segments, window_s)
    drafts = _drafts(
        out=out,
        cut=cut,
        segments=segments,
        turns=turns,
        roster=roster,
        agenda_text=agenda.excerpt() if agenda else "",
        title=title,
        meeting_date=meeting_date,
        model=model,
        reuse=reuse_draft,
        complete=complete,
        log=log,
        video_id=video_id,
    )

    title_of: list[str | None] = [None] * len(segments)
    for turn in turns:
        for position in range(turn.first, turn.last + 1):
            title_of[position] = turn.title

    items: list[dict[str, Any]] = []
    for draft in drafts:
        items.extend(draft["items"])
    claims, verify_report = verify(
        items,
        index,
        segments,
        roster,
        vocabulary,
        speaker_at=lambda position: segments[position].speaker,
        title_at=lambda position: title_of[position],
    )
    checked_count = len(claims)
    claims = _judged(
        claims,
        verify_report,
        out=out,
        complete=complete or (lambda prompt: llm.chat(prompt, model=model)),
        log=lambda line: log(f"[{video_id}] {line}"),
    )
    recap = Recap(
        city=roster.city,
        body=roster.body,
        meeting_date=meeting_date,
        video_id=video_id,
        claims=tuple(claims),
    )
    save_recap(recap, out / "recap.json")
    verify_report["agenda_source"] = agenda.source if agenda else None
    verify_report["agenda_terms"] = len(vocabulary)
    verify_report["windows"] = [
        {k: d[k] for k in ("window", "start", "end", "items", "error")} for d in drafts
    ]
    for entry in verify_report["windows"]:
        entry["items"] = len(entry["items"])
    _write(out / "verify-report.json", verify_report)

    summary = {
        "video_id": video_id,
        "title": title,
        "meeting_date": meeting_date,
        "caption_kind": origin["kind"],
        "diarization": speakers["diarization"]["status"],
        "voices": speakers["diarization"].get("voices"),
        "segments": speakers["counts"]["segments"],
        "named_segments": speakers["counts"]["named_segments"],
        "named_percent": speakers["counts"]["named_percent"],
        "title_only_segments": speakers["counts"]["title_only_segments"],
        "unknown_segments": speakers["counts"]["unknown_segments"],
        "turns": speakers["counts"]["turns"],
        "windows": len(cut),
        "windows_with_error": sum(1 for d in drafts if d["error"]),
        "claims_before_verification": len(items),
        "claims_after_code_checks": checked_count,
        "claims_after_verification": len(claims),
        "claims_dropped_by_judge": checked_count - len(claims),
        "agenda_source": agenda.source if agenda else None,
        "model": model,
    }
    _write(out / "summary.json", summary)
    log(f"[{video_id}] recap: {len(items)} draft items, {len(claims)} kept after verification")
    return summary


def _diarize(
    video_id: str,
    out: Path,
    segments: list[transcript_mod.Segment],
    requested: bool,
    townrecord_python: str | None,
    textflowkit_python: str | None,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Put voice labels on the caption lines. A failed run is recorded, not hidden, and the
    meeting goes on without voices: names never depend on diarization."""
    if not requested:
        return diarize_mod.not_run("diarization was not requested for this run (use --diarize)")
    try:
        log(f"[{video_id}] diarize: audio")
        audio = diarize_mod.fetch_audio(video_id, out / "audio", python=townrecord_python)
        log(f"[{video_id}] diarize: textflowkit")
        data = diarize_mod.run_diarization(
            audio, out / "diarization.json", python=textflowkit_python
        )
    except diarize_mod.DiarizeError as exc:
        log(f"[{video_id}] diarize FAILED: {exc}")
        return {"status": "failed", "reason": str(exc)}
    counts = diarize_mod.assign_voices(segments, diarize_mod.spans_from(data))
    log(f"[{video_id}] diarize: {counts['voices']} voices")
    return {
        "status": "run",
        "model": data.get("model"),
        "device": data.get("device"),
        "seconds": data.get("seconds"),
        "file": "diarization.json",
        **counts,
    }


def _judged(
    claims: list[Claim],
    verify_report: dict[str, Any],
    *,
    out: Path,
    complete: Complete,
    log: Callable[[str], None],
) -> list[Claim]:
    """Run the claim judge, write judge.json, add its drops to the report, renumber the rest."""
    judgments, calls = judge_claims(claims, complete, log=log)
    _write(
        out / "judge.json",
        {"claims": [j.to_json() for j in judgments], "calls": calls},
    )
    kept: list[Claim] = []
    for judgment in judgments:
        if judgment.kept:
            kept.append(judgment.claim)
            continue
        reason = f"the judge found the quote does not carry the claim ({judgment.verdict})"
        verify_report["dropped_items"].append(
            {
                "draft_index": None,
                "claim_id_before_judge": judgment.claim.id,
                "text": judgment.claim.text,
                "reason": f"{reason}: {judgment.reason}",
            }
        )
        verify_report["drop_reasons"][reason] = verify_report["drop_reasons"].get(reason, 0) + 1
    verify_report["dropped"] = len(verify_report["dropped_items"])
    verify_report["kept"] = len(kept)
    verify_report["judge"] = {
        "checked": len(judgments),
        "kept": len(kept),
        "verdicts": {
            verdict: sum(1 for j in judgments if j.verdict == verdict)
            for verdict in sorted({j.verdict for j in judgments})
        },
        "failed_calls": sum(1 for c in calls if "error" in c),
    }
    return [replace(claim, id=f"c{number:02d}") for number, claim in enumerate(kept, start=1)]


def _drafts(
    *,
    out: Path,
    cut: list,
    segments: list,
    turns: list,
    roster: Any,
    agenda_text: str,
    title: str,
    meeting_date: str,
    model: str,
    reuse: bool,
    complete: Complete | None,
    log: Callable[[str], None],
    video_id: str,
) -> list[dict[str, Any]]:
    """Model drafts for every window, from draft.json when reuse is asked for and it exists."""
    draft_file = out / "draft.json"
    if reuse and draft_file.exists():
        log(f"[{video_id}] recap: reusing {draft_file.name}")
        return list(json.loads(draft_file.read_text(encoding="utf-8"))["windows"])

    call: Complete = complete or (lambda prompt: llm.chat(prompt, model=model))
    answers = out / "draft-answers"
    answers.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for window in cut:
        prompt = build_prompt(
            title=title,
            meeting_date=meeting_date,
            part=window.index,
            parts=len(cut),
            window=window,
            segments=segments,
            turns=turns,
            roster=roster,
            agenda_text=agenda_text,
        )
        log(
            f"[{video_id}] recap part {window.index}/{len(cut)} "
            f"({transcript_mod.hms(window.start)} to {transcript_mod.hms(window.end)})"
        )
        try:
            draft: WindowDraft = draft_window(call, prompt, window.index)
        except (llm.ModelError, OSError) as exc:
            draft = WindowDraft(window.index, [], "", "", f"model call failed: {exc}")
        (answers / f"window-{window.index:02d}.txt").write_text(draft.answer, encoding="utf-8")
        results.append(
            {
                "window": window.index,
                "start": transcript_mod.hms(window.start),
                "end": transcript_mod.hms(window.end),
                "items": draft.items,
                "error": draft.error,
                "receipt": draft.receipt,
            }
        )
        _write(draft_file, {"model": model, "windows": results})
    return results
