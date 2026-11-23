"""Audio finishing: intro and outro stitching, loudness, MP3, chapters.json and WebVTT.

Loudness is set by one constant gain over the whole stitched program, followed by a lookahead
limiter (`alimiter`). The targets are -16 LUFS integrated and -1.5 dBTP true peak, measured on
the delivered MP3.

The gain does not come from loudnorm. loudnorm only applies a constant gain when its linear mode
is allowed, and that needs headroom below its peak ceiling; our voices peak at 0 dBFS, so every
real episode fell back to loudnorm's dynamic mode. Dynamic loudnorm varies its gain over time:
measured on three episodes it added 5.2 to 5.8 dB to the first five seconds and 2.4 to 2.9 dB
after 0:20, although the raw voices are not clipped in their opening. The start of each program
therefore came out louder than the rest. One constant gain cannot do that. `solve_limiter_gain`
finds it by measuring rather than arithmetic, because the limiter is nonlinear: it renders the
program at a gain, measures the limited WAV, and corrects the gain until the master sits at
MASTER_TARGET_LUFS.

The limiter shaves only the samples above its ceiling and is a no-op when the peaks are already
below it. Its delay compensation is on (`latency=true`). Without it the limiter outputs its
lookahead delay first: every real episode came out 219 samples (5.0 ms at 44.1 kHz) later than
its input, which would slide the audio against the chapter and transcript times. With it, the
output samples sit at the same positions as the input and the length is unchanged.

A 64 kbps LAME encode then lifts the decoded true peak above the master's. LAME runs at its
slowest search (MP3_COMPRESSION_LEVEL), which cuts that overshoot from about 3 dB to well under
1 dB at the same bitrate. The MP3 is measured with ebur128 after encoding; if its true peak is
over -1.5 dBTP the ceiling is lowered by the overshoot and the master re-rendered from the
stitched program (never from a previously limited master, so gain reduction does not compound).
The master is held at MASTER_TARGET_LUFS so the decoded MP3 lands near -16 LUFS; the peak ceiling
takes priority when both cannot be met. Neither the gain nor the limiter changes the length.

MP3 is mono at 44.1 kHz, LAME at 64 kbps. The output is one voice at a time, so a single channel
carries it, and 64 kbps is 8 KB per second: a 90-second episode is about 720 KB, where 128 kbps
would be about 1.4 MB. That is a choice from the arithmetic, not a listening test; 96 kbps is the
step to try if Scott hears artifacts.

Chapters use the Podcasting 2.0 JSON chapters format, version 1.2.0. A chapter starts at the first
spoken line after its [CHAPTER] mark. The first chapter always starts at 0, so it also covers an
intro and any lines before the first mark.

The transcript is WebVTT with a voice tag per cue, for example <v Pat>, using the line timings.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from civiccast_brief.audio.engines import RenderResult
from civiccast_brief.audio.errors import FinishError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg
from civiccast_brief.audio.lines import SPEAKERS, Line, Script

TARGET_LUFS = -16.0
TARGET_TRUE_PEAK_DBTP = -1.5
# LAME loses ~0.3-0.4 LU, so a master aimed a little hot lands the decoded MP3 near -16 LUFS.
MASTER_TARGET_LUFS = -15.7
LOUDNESS_TOLERANCE_LU = 0.5
# Peak must clear the target by this much, so codec noise cannot land exactly on the boundary.
MP3_PEAK_MARGIN_DB = 0.1
MAX_LIMIT_ATTEMPTS = 4
MAX_GAIN_STEPS = 6
# ebur128's absolute gate is -70 LUFS, so nothing audible measures at or below this.
SILENCE_LUFS = -70.0
LIMITER_ATTACK_MS = 5
LIMITER_RELEASE_MS = 50
SAMPLE_RATE = 44100
MP3_BITRATE = "64k"
# LAME's slowest search produces less quantization noise, so the decoded true peak overshoots
# the master by well under 1 dB instead of the ~3 dB it does at the default effort. Same bitrate.
MP3_COMPRESSION_LEVEL = 9
CHAPTERS_VERSION = "1.2.0"
SPEAKER_NAMES = {"S1": "Pat", "S2": "Alex"}

_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_LRA = re.compile(r"Loudness range:\s*LRA:\s*(-?[\d.]+)\s*LU")
_TRUE_PEAK = re.compile(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


@dataclass(frozen=True)
class Loudness:
    integrated_lufs: float
    true_peak_dbtp: float
    lra: float


@dataclass(frozen=True)
class ChapterMark:
    start: float
    title: str


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    speaker: str  # display name, for example Pat
    text: str


@dataclass(frozen=True)
class EpisodeFiles:
    mp3: Path
    chapters_json: Path
    transcript_vtt: Path
    master_wav: Path
    render_json: Path
    chapters: tuple[ChapterMark, ...]
    cues: tuple[Cue, ...]
    duration: float  # seconds, MP3 as ffprobe reads it
    loudness: Loudness  # measured on the MP3 with ebur128


# --- audio ----------------------------------------------------------------------------------


def stitch(voice: Path, intro: Path | None, outro: Path | None, out_wav: Path) -> None:
    """Join intro, voice and outro into one mono WAV at SAMPLE_RATE. Missing parts are skipped."""
    sources = [path for path in (intro, voice, outro) if path is not None]
    args: list[str] = []
    for source in sources:
        args += ["-i", str(source)]
    chains = [
        f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono[a{i}]"
        for i in range(len(sources))
    ]
    joined = "".join(f"[a{i}]" for i in range(len(sources)))
    chains.append(f"{joined}concat=n={len(sources)}:v=0:a=1[out]")
    run_ffmpeg(
        [
            *args,
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ],
        what="stitching intro, voice and outro",
    )


def limiter_filter(gain_db: float, ceiling_db: float) -> str:
    """Volume by gain_db, then a lookahead limiter whose sample peak never passes ceiling_db.

    `latency=true` turns on the filter's delay compensation, so the output is not shifted by the
    lookahead and the sample positions match the input.
    """
    limit = 10 ** (ceiling_db / 20)
    return (
        f"volume={gain_db:.3f}dB,"
        f"alimiter=limit={limit:.6f}:level=disabled"
        f":attack={LIMITER_ATTACK_MS}:release={LIMITER_RELEASE_MS}:latency=true"
    )


def apply_peak_limiter(program: Path, out_wav: Path, *, gain_db: float, ceiling_db: float) -> None:
    """Apply gain and a lookahead limiter. Writes 16-bit mono WAV.

    The length and the sample positions are unchanged, so timed chapters and transcripts still line
    up with the audio.
    """
    run_ffmpeg(
        [
            "-nostats",
            "-i",
            str(program),
            "-af",
            limiter_filter(gain_db, ceiling_db),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ],
        what="true-peak limiting",
    )


def solve_limiter_gain(
    program: Path, out_wav: Path, *, ceiling_db: float, target_lufs: float
) -> float:
    """Gain that makes the limited master measure target_lufs. Renders into out_wav as it goes.

    Limiting is nonlinear, so the gain is found by measuring, not by arithmetic.
    """
    source_lufs = measure_loudness(program).integrated_lufs
    if source_lufs <= SILENCE_LUFS:
        raise FinishError("the program is silent; there is nothing to normalise")
    gain_db = target_lufs - source_lufs
    for _ in range(MAX_GAIN_STEPS):
        apply_peak_limiter(program, out_wav, gain_db=gain_db, ceiling_db=ceiling_db)
        measured = measure_loudness(out_wav).integrated_lufs
        error = target_lufs - measured
        if abs(error) < 0.05:
            break
        gain_db += error
    return gain_db


def encode_mp3(master: Path, out_mp3: Path) -> None:
    run_ffmpeg(
        [
            "-i",
            str(master),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            MP3_BITRATE,
            "-compression_level",
            str(MP3_COMPRESSION_LEVEL),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            str(out_mp3),
        ],
        what="MP3 encode",
    )


def pick_attempt(attempts: list[tuple[Path, Path, Loudness]]) -> tuple[Path, Path, Loudness]:
    """The peak ceiling wins: prefer attempts at or under the target after the MP3 margin, then
    the one nearest -16 LUFS. With none under it, publish the lowest peak and warn: the voices are
    already paid for, and a 0.1 dB miss must not throw a finished episode away."""
    ceiling = TARGET_TRUE_PEAK_DBTP - MP3_PEAK_MARGIN_DB
    under_ceiling = [attempt for attempt in attempts if attempt[2].true_peak_dbtp <= ceiling]
    if under_ceiling:
        return min(under_ceiling, key=lambda a: abs(a[2].integrated_lufs - TARGET_LUFS))
    lowest = min(attempts, key=lambda a: a[2].true_peak_dbtp)
    print(
        f"warning: no attempt landed at or under {ceiling:.1f} dBTP; publishing the lowest"
        f" peak, {lowest[2].true_peak_dbtp:.1f} dBTP (target {TARGET_TRUE_PEAK_DBTP:.1f} dBTP)",
        file=sys.stderr,
    )
    return lowest


def master_for_mp3(program: Path, work: Path, out_mp3: Path) -> tuple[Path, Path, Loudness]:
    """Apply one gain and the limiter to a stitched program, then encode. Returns
    (master, mp3 files, loudness).

    The limiter ceiling is lowered by however far the decoded MP3's true peak overshoots, and
    each attempt is re-rendered from the stitched program so gain reduction never compounds.
    """
    master = work / "master.wav"
    ceiling = TARGET_TRUE_PEAK_DBTP
    attempts: list[tuple[Path, Path, Loudness]] = []
    for number in range(MAX_LIMIT_ATTEMPTS):
        attempt_master = work / f"master-{number + 1}.wav"
        attempt_mp3 = work / f"episode-{number + 1}.mp3"
        solve_limiter_gain(
            program, attempt_master, ceiling_db=ceiling, target_lufs=MASTER_TARGET_LUFS
        )
        encode_mp3(attempt_master, attempt_mp3)
        loudness = measure_loudness(attempt_mp3)
        attempts.append((attempt_master, attempt_mp3, loudness))
        peak_ok = loudness.true_peak_dbtp <= TARGET_TRUE_PEAK_DBTP - MP3_PEAK_MARGIN_DB
        loud_ok = abs(loudness.integrated_lufs - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU
        if peak_ok and loud_ok:
            break
        if not peak_ok:
            overshoot = loudness.true_peak_dbtp - (TARGET_TRUE_PEAK_DBTP - MP3_PEAK_MARGIN_DB)
            ceiling -= overshoot + 0.1
        else:
            # The peak is fine but the MP3 is too loud or quiet; more limiting only loses
            # loudness, so keep the closest attempt.
            break

    best_master, best_mp3, best_loudness = pick_attempt(attempts)
    shutil.copyfile(best_master, master)
    shutil.copyfile(best_mp3, out_mp3)
    for attempt_master, attempt_mp3, _ in attempts:
        if attempt_master != best_master:
            attempt_master.unlink(missing_ok=True)
        if attempt_mp3 != best_mp3:
            attempt_mp3.unlink(missing_ok=True)
    return master, out_mp3, best_loudness


def parse_ebur128_summary(text: str) -> Loudness:
    integrated = _INTEGRATED.search(text)
    lra = _LRA.search(text)
    peak = _TRUE_PEAK.search(text)
    if integrated is None or lra is None or peak is None:
        raise FinishError("could not read the ebur128 summary from ffmpeg")
    return Loudness(
        integrated_lufs=float(integrated.group(1)),
        true_peak_dbtp=float(peak.group(1)),
        lra=float(lra.group(1)),
    )


def measure_loudness(path: Path) -> Loudness:
    """Integrated loudness, true peak and loudness range, measured by ffmpeg's ebur128 filter."""
    proc = run_ffmpeg(
        [
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        what="ebur128 measurement",
    )
    return parse_ebur128_summary(proc.stderr)


# --- chapters and transcript ----------------------------------------------------------------


def chapter_marks(script: Script, line_starts: list[float]) -> list[ChapterMark]:
    """Chapter start times on the episode timeline. The first mark is always 0."""
    marks: list[ChapterMark] = []
    for position, chapter in enumerate(script.chapters):
        start = 0.0 if position == 0 else line_starts[chapter.line_index]
        # The title is shown, so it starts with a capital ("public comment" is "Public comment").
        title = chapter.title[:1].upper() + chapter.title[1:]
        marks.append(ChapterMark(round(start, 3), title))
    return marks


def chapters_document(marks: list[ChapterMark]) -> dict[str, object]:
    return {
        "version": CHAPTERS_VERSION,
        "chapters": [{"startTime": mark.start, "title": mark.title} for mark in marks],
    }


def write_chapters_json(path: Path, marks: list[ChapterMark]) -> None:
    text = json.dumps(chapters_document(marks), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def build_cues(
    lines: tuple[Line, ...],
    spans: list[tuple[float, float]],
    names: dict[str, str],
) -> list[Cue]:
    cues: list[Cue] = []
    for line, (start, end) in zip(lines, spans, strict=True):
        if line.speaker not in names:
            raise FinishError(f"no display name for speaker {line.speaker}")
        cues.append(Cue(start, end, names[line.speaker], line.text))
    return cues


def vtt_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def vtt_document(cues: list[Cue]) -> str:
    """WebVTT text. Cues must be in order and must not overlap."""
    blocks = ["WEBVTT"]
    previous_end = 0.0
    for cue in cues:
        if cue.end <= cue.start or cue.start < previous_end:
            raise FinishError(f"transcript cue at {cue.start:.3f} s is out of order")
        previous_end = cue.end
        blocks.append(
            f"{vtt_timestamp(cue.start)} --> {vtt_timestamp(cue.end)}\n"
            f"<v {_escape(cue.speaker)}>{_escape(cue.text)}</v>"
        )
    return "\n\n".join(blocks) + "\n"


def write_vtt(path: Path, cues: list[Cue]) -> None:
    path.write_text(vtt_document(cues), encoding="utf-8", newline="\n")


# --- the whole episode ----------------------------------------------------------------------


def finish_episode(
    render: RenderResult,
    script: Script,
    out_dir: Path,
    *,
    intro: Path | None = None,
    outro: Path | None = None,
    speaker_names: dict[str, str] | None = None,
) -> EpisodeFiles:
    """Stitch, normalise, encode and write chapters and transcript into out_dir."""
    if len(render.lines) != len(script.lines):
        raise FinishError(
            f"render has {len(render.lines)} line timings for {len(script.lines)} lines"
        )
    names = dict(SPEAKER_NAMES if speaker_names is None else speaker_names)
    missing = [speaker for speaker in SPEAKERS if speaker not in names]
    if missing:
        raise FinishError(f"no display names for {missing}")

    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    offset = probe_duration(intro) if intro is not None else 0.0
    program = work / "program.wav"
    stitch(render.wav, intro, outro, program)
    mp3 = out_dir / "episode.mp3"
    master, _, loudness = master_for_mp3(program, work, mp3)

    spans = [(line.start + offset, line.end + offset) for line in render.lines]
    marks = chapter_marks(script, [start for start, _ in spans])
    cues = build_cues(script.lines, spans, names)
    chapters_json = out_dir / "chapters.json"
    write_chapters_json(chapters_json, marks)
    transcript = out_dir / "transcript.vtt"
    write_vtt(transcript, cues)

    render_json = work / "render.json"
    render_record = {
        "engine": render.engine,
        "timing": render.timing,
        "voice_wav": str(render.wav),
        "voice_duration_s": render.duration,
        "intro_offset_s": offset,
        "lines": [
            {"number": line.number, "speaker": line.speaker, "start": start, "end": end}
            for line, (start, end) in zip(render.lines, spans, strict=True)
        ],
    }
    render_json.write_text(json.dumps(render_record, indent=2) + "\n", encoding="utf-8")

    return EpisodeFiles(
        mp3=mp3,
        chapters_json=chapters_json,
        transcript_vtt=transcript,
        master_wav=master,
        render_json=render_json,
        chapters=tuple(marks),
        cues=tuple(cues),
        duration=probe_duration(mp3),
        loudness=loudness,
    )
