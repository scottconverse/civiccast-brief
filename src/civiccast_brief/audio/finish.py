"""Audio finishing: intro and outro stitching, loudness, MP3, chapters.json and WebVTT.

Loudness is measured twice with ffmpeg's loudnorm, which targets -16 LUFS integrated, -1.5 dBTP
true peak and 11 LU loudness range (set explicitly; loudnorm's own default range is 7 LU). The
second pass uses linear gain when the peak ceiling allows it and loudnorm's dynamic gain when it
does not. Neither mode changes the length, so the transcript and chapter times stay valid.

Known limit: loudnorm reaches the target only when the material's peak-to-loudness spread is small
enough. The 90-second Chatterbox render has about 21 LU of spread and ends at -17.0 LUFS with the
true-peak ceiling binding. A compressor before loudnorm reached -16.1 LUFS in a scratch test; that
is not in this chain because it changes the sound and needs Scott's listening decision.

MP3 is mono at 44.1 kHz, LAME at 64 kbps. The output is one voice at a time, so a single channel
carries it, and 64 kbps is 8 KB per second: a 90-second episode is about 720 KB, where 128 kbps
would be about 1.4 MB. That is a choice from the arithmetic, not a listening test; 96 kbps is the
step to try if Scott hears artifacts.

Chapters use the Podcasting 2.0 JSON chapters format, version 1.2.0. A chapter starts at the first
spoken line after its [CHAPTER] mark. The first chapter always starts at 0, so it also covers an
intro and any lines before the first mark.

The transcript is WebVTT with a voice tag per cue, for example <v Maya>, using the line timings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from civiccast_brief.audio.engines import RenderResult
from civiccast_brief.audio.errors import FinishError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg
from civiccast_brief.audio.lines import SPEAKERS, Line, Script

TARGET_LUFS = -16.0
TARGET_TRUE_PEAK_DBTP = -1.5
TARGET_LRA = 11.0
SAMPLE_RATE = 44100
MP3_BITRATE = "64k"
CHAPTERS_VERSION = "1.2.0"
SPEAKER_NAMES = {"S1": "Maya", "S2": "Theo"}

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
    speaker: str  # display name, for example Maya
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


def _loudnorm_measurements(stderr: str) -> dict[str, str]:
    start, end = stderr.rfind("{"), stderr.rfind("}")
    if start < 0 or end < start:
        raise FinishError("loudnorm printed no measurements")
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FinishError("loudnorm measurements were not valid JSON") from exc
    needed = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    missing = [key for key in needed if key not in data]
    if missing:
        raise FinishError(f"loudnorm measurements lack {missing}")
    return data


def normalize_loudness(program: Path, out_wav: Path) -> None:
    """Two-pass loudnorm to TARGET_LUFS and TARGET_TRUE_PEAK_DBTP. Writes 16-bit mono WAV."""
    measure_graph = (
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK_DBTP}:LRA={TARGET_LRA}:print_format=json"
    )
    first = run_ffmpeg(
        ["-nostats", "-i", str(program), "-af", measure_graph, "-f", "null", "-"],
        what="loudness measurement",
    )
    measured = _loudnorm_measurements(first.stderr)
    if measured["input_i"] in ("-inf", "inf"):
        raise FinishError("the program is silent; there is nothing to normalise")
    graph = (
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK_DBTP}:LRA={TARGET_LRA}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true:print_format=summary"
    )
    run_ffmpeg(
        [
            "-nostats",
            "-i",
            str(program),
            "-af",
            graph,
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ],
        what="loudness normalisation",
    )


def encode_mp3(master: Path, out_mp3: Path) -> None:
    run_ffmpeg(
        [
            "-i",
            str(master),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            MP3_BITRATE,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            str(out_mp3),
        ],
        what="MP3 encode",
    )


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
    master = work / "master.wav"
    stitch(render.wav, intro, outro, program)
    normalize_loudness(program, master)
    mp3 = out_dir / "episode.mp3"
    encode_mp3(master, mp3)

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
        loudness=measure_loudness(mp3),
    )
