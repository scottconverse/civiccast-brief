"""Command line for the audio package.

    python -m civiccast_brief.audio SCRIPT [--engine dia2|chatterbox|gemini|lfm3|fake] --out DIR
                                    [--fallback-engine NAME] [--intro FILE] [--outro FILE]
                                    [--config FILE]

The default engine is dia2 (Scott, 2026-11-12). With ``--fallback-engine``, a failure of the
primary engine (making it or rendering with it) is retried with that engine instead. Output in DIR:
episode.mp3, chapters.json, transcript.vtt, and work/ (the raw voice WAV, the program and master
WAVs, and render.json with the line timings).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.audio.engines import (
    DEFAULT_ENGINE,
    ENGINE_NAMES,
    AudioConfig,
    RenderResult,
    load_config,
    make_engine,
)
from civiccast_brief.audio.errors import AudioError
from civiccast_brief.audio.finish import finish_episode
from civiccast_brief.audio.lines import Script, read_script


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m civiccast_brief.audio",
        description=(
            "Render a two-host script to a podcast episode: MP3, chapters.json and a "
            "WebVTT transcript."
        ),
    )
    parser.add_argument(
        "script",
        type=Path,
        help="script text file: [S1] and [S2] lines, with [CHAPTER Title] marks",
    )
    parser.add_argument(
        "--engine",
        default=DEFAULT_ENGINE,
        choices=ENGINE_NAMES,
        help=f"voice engine (default: {DEFAULT_ENGINE})",
    )
    parser.add_argument(
        "--fallback-engine",
        choices=ENGINE_NAMES,
        help=(
            "engine to render with if the primary engine fails to make or render "
            "(default: none; the run fails instead)"
        ),
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="output directory (created if missing)"
    )
    parser.add_argument("--intro", type=Path, help="audio file to place before the voice")
    parser.add_argument("--outro", type=Path, help="audio file to place after the voice")
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "JSON file overriding the GPU box, engine paths, Gemini and LFM3 settings and "
            "voices (see engines.py)"
        ),
    )
    return parser


def _render(name: str, config: AudioConfig, script: Script, out_wav: Path) -> RenderResult:
    """Make the named engine and render the script with it. Raises AudioError on any failure."""
    return make_engine(name, config).render(list(script.lines), out_wav)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for label, path in (("intro", args.intro), ("outro", args.outro)):
        if path is not None and not path.is_file():
            print(f"error: {label} file not found: {path}", file=sys.stderr)
            return 1
    try:
        script = read_script(args.script)
        config = load_config(args.config)
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    fallback = args.fallback_engine
    if fallback == args.engine:
        fallback = None
    voice_wav = args.out / "work" / "voice.wav"
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        render = _render(args.engine, config, script, voice_wav)
    except AudioError as exc:
        if fallback is None:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"warning: {args.engine} engine failed ({exc}); rendering with {fallback}",
            file=sys.stderr,
        )
        # A half-written WAV must not be mistaken for a finished render.
        voice_wav.unlink(missing_ok=True)
        try:
            render = _render(fallback, config, script, voice_wav)
        except AudioError as fallback_exc:
            print(f"error: {exc}", file=sys.stderr)
            print(f"error: {fallback_exc}", file=sys.stderr)
            return 1

    try:
        files = finish_episode(render, script, args.out, intro=args.intro, outro=args.outro)
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    loud = files.loudness
    print(f"engine: {render.engine} (timing: {render.timing})")
    print(f"spoken lines: {len(script.lines)}, chapters: {len(files.chapters)}")
    print(
        f"episode: {files.mp3} ({files.duration:.2f} s, "
        f"{loud.integrated_lufs:.1f} LUFS integrated, {loud.true_peak_dbtp:.1f} dBTP)"
    )
    print(f"chapters: {files.chapters_json}")
    print(f"transcript: {files.transcript_vtt} ({len(files.cues)} cues)")
    return 0
