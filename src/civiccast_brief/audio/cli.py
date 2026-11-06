"""Command line for the audio package.

    python -m civiccast_brief.audio SCRIPT --engine chatterbox|dia2|fake --out DIR
                                    [--intro FILE] [--outro FILE] [--config FILE]

There is no default engine: Scott has not yet picked between the two. Output in DIR:
episode.mp3, chapters.json, transcript.vtt, and work/ (the raw voice WAV, the program and master
WAVs, and render.json with the line timings).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.audio.engines import ENGINE_NAMES, load_config, make_engine
from civiccast_brief.audio.errors import AudioError
from civiccast_brief.audio.finish import finish_episode
from civiccast_brief.audio.lines import read_script


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
        required=True,
        choices=ENGINE_NAMES,
        help="voice engine; required, there is no default",
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="output directory (created if missing)"
    )
    parser.add_argument("--intro", type=Path, help="audio file to place before the voice")
    parser.add_argument("--outro", type=Path, help="audio file to place after the voice")
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON file overriding the GPU box, engine paths and voices (see engines.py)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for label, path in (("intro", args.intro), ("outro", args.outro)):
        if path is not None and not path.is_file():
            print(f"error: {label} file not found: {path}", file=sys.stderr)
            return 1
    try:
        script = read_script(args.script)
        config = load_config(args.config)
        engine = make_engine(args.engine, config)
        out = args.out
        out.mkdir(parents=True, exist_ok=True)
        render = engine.render(list(script.lines), out / "work" / "voice.wav")
        files = finish_episode(render, script, out, intro=args.intro, outro=args.outro)
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
