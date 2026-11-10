"""The speech program: ``python -m civiccast_brief.speech``.

It runs as a child process so a long transcription or a model load has its own time limit
and cannot take CivicCast Brief down with it. Three commands:

    --version
        Prints one line: ``civiccast-speech <VERSION>``.
    transcribe AUDIO --output-dir DIR [--language en] [--model whistle] [--formats json]
        Turns any audio file ffmpeg can read into 16 kHz mono WAV, transcribes it with
        Whistle, writes ``DIR/<name>.json`` (textflowkit's transcript JSON) and prints
        that path. ``--formats`` accepts only ``json``.
    diarize AUDIO.wav OUT.json
        Splits the audio into speaker turns with pyannote, from the local model cache
        only (no network, no token), and writes the turns to OUT.json. The last line
        printed is JSON: ``{"ok": true, "turns": N}`` or ``{"error": "..."}``.

Exit code 0 means it worked; anything else means it did not, with the reason on stderr
(or, for ``diarize``, in the last JSON line).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

from civiccast_brief.speech import VERSION

PROGRAM = "civiccast-speech"

#: The pyannote pipeline CivicCast Brief has always used for telling voices apart.
DIARIZE_MODEL_ENV = "CIVICCAST_SPEECH_PYANNOTE_MODEL"
DEFAULT_DIARIZE_MODEL = "pyannote/speaker-diarization-3.1"


def _is_whistle_wav(path: Path) -> bool:
    """True when the file is already the 16 kHz mono 16-bit WAV Whistle reads."""
    try:
        with wave.open(str(path), "rb") as wav:
            return (
                wav.getframerate() == 16000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2
            )
    except (OSError, EOFError, wave.Error):
        return False


def _as_whistle_wav(source: Path, scratch: Path) -> Path:
    """The audio as 16 kHz mono 16-bit WAV, converting with ffmpeg when needed."""
    if _is_whistle_wav(source):
        return source
    from civiccast_brief.audio.ffmpeg import run_ffmpeg

    out = scratch / "audio-16k.wav"
    run_ffmpeg(
        ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
        what=f"converting {source.name} to 16 kHz WAV",
    )
    return out


def transcribe(audio: Path, output_dir: Path, *, language: str | None, model: str) -> Path:
    """Transcribe ``audio`` and return the path of the JSON transcript written."""
    from civiccast_brief.speech.whistle import WhistleEngine

    engine = WhistleEngine(model=model)
    engine.validate_language(language)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="civiccast-speech-") as scratch:
        wav = _as_whistle_wav(audio, Path(scratch))
        transcript = engine.transcribe(wav, language=language)
    transcript.source = str(audio)
    out = output_dir / f"{audio.stem}.json"
    transcript.save_json(out)
    return out.resolve()


def _diarize_fail(message: str) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False))
    return 1


def diarize(audio: Path, out: Path) -> int:
    """Write the speaker turns of ``audio`` to ``out``. Loads the model from the cache only."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    model = os.environ.get(DIARIZE_MODEL_ENV, DEFAULT_DIARIZE_MODEL)
    if not audio.exists():
        return _diarize_fail(f"no audio file: {audio}")
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        return _diarize_fail(
            "telling voices apart needs the diarize extra; run `uv sync --extra diarize` "
            f"in the CivicCast Brief folder ({exc})"
        )
    from civiccast_brief.speech.diarize import PyannoteDiarizer, SpeakerTurn
    from civiccast_brief.speech.privacy import disable_dependency_telemetry

    disable_dependency_telemetry()
    started = time.monotonic()
    try:
        pipeline = Pipeline.from_pretrained(model)
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not hidden
        return _diarize_fail(f"could not load {model} from the local cache: {exc}")
    if pipeline is None:
        return _diarize_fail(f"could not load {model} from the local cache")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(device))
    try:
        result = pipeline(PyannoteDiarizer._load_waveform(audio))
    except Exception as exc:  # noqa: BLE001
        return _diarize_fail(f"diarization failed: {exc}")
    annotation = getattr(result, "speaker_diarization", result)
    turns = [
        SpeakerTurn(start=float(t.start), end=float(t.end), speaker=str(label))
        for t, _, label in annotation.itertracks(yield_label=True)
    ]
    out.write_text(
        json.dumps(
            {
                "model": model,
                "device": device,
                "seconds": round(time.monotonic() - started, 1),
                "turns": [
                    {"start": round(t.start, 3), "end": round(t.end, 3), "speaker": t.speaker}
                    for t in turns
                ],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "turns": len(turns)}))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM, description="Speech on this machine.")
    parser.add_argument("--version", action="store_true", help="print the version and stop")
    sub = parser.add_subparsers(dest="command")
    t = sub.add_parser("transcribe", help="transcribe one audio file to JSON")
    t.add_argument("audio")
    t.add_argument("--output-dir", required=True)
    t.add_argument("--language", default="en")
    t.add_argument("--model", default="whistle")
    t.add_argument("--formats", default="json")
    d = sub.add_parser("diarize", help="split one WAV file into speaker turns")
    d.add_argument("audio")
    d.add_argument("out")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"{PROGRAM} {VERSION}")
        return 0
    if args.command == "transcribe":
        if args.formats.strip().lower() != "json":
            print(f"error: only json output is written, not {args.formats!r}", file=sys.stderr)
            return 2
        audio = Path(args.audio)
        if not audio.is_file():
            print(f"error: no audio file: {audio}", file=sys.stderr)
            return 1
        try:
            path = transcribe(
                audio, Path(args.output_dir), language=args.language or None, model=args.model
            )
        except Exception as exc:  # noqa: BLE001 - the reason goes to the caller
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(path)
        return 0
    if args.command == "diarize":
        return diarize(Path(args.audio), Path(args.out))
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
