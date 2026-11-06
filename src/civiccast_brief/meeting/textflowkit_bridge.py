"""Voice diarization through textflowkit, run in textflowkit's own venv.

This file is run by textflowkit's interpreter, isolated, and is never imported by civiccast_brief:

    <textflowkit-venv>\\Scripts\\python.exe -I textflowkit_bridge.py <audio.wav> <out.json>

It reads the audio with textflowkit's own reader (``PyannoteDiarizer._load_waveform``) and runs
the pyannote pipeline textflowkit uses (``pyannote/speaker-diarization-3.1``). The model is loaded
from the local Hugging Face cache with ``HF_HUB_OFFLINE=1``: nothing is downloaded and no token is
used. If the model is not in the cache, the run fails and says so.

Writes ``{"model", "device", "seconds", "turns": [{"start", "end", "speaker"}]}`` to out.json and
prints one JSON line: ``{"ok": true, "turns": N}`` or ``{"error": ...}`` (exit 1).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"

MODEL = os.environ.get("TEXTFLOWKIT_PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")


def _fail(message: str) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False))
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return _fail("usage: textflowkit_bridge.py <audio.wav> <out.json>")
    audio, out = Path(argv[1]), Path(argv[2])
    if not audio.exists():
        return _fail(f"no audio file: {audio}")
    try:
        import torch
        from pyannote.audio import Pipeline
        from textflowkit.core.diarize import PyannoteDiarizer, SpeakerTurn
        from textflowkit.core.privacy import disable_dependency_telemetry
    except ImportError as exc:
        return _fail(f"textflowkit with the diarize extra is not installed here: {exc}")

    disable_dependency_telemetry()
    started = time.monotonic()
    try:
        pipeline = Pipeline.from_pretrained(MODEL)
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not hidden
        return _fail(f"could not load {MODEL} from the local cache: {exc}")
    if pipeline is None:
        return _fail(f"could not load {MODEL} from the local cache")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(device))
    try:
        result = pipeline(PyannoteDiarizer._load_waveform(audio))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"diarization failed: {exc}")
    annotation = getattr(result, "speaker_diarization", result)
    turns = [
        SpeakerTurn(start=float(t.start), end=float(t.end), speaker=str(label))
        for t, _, label in annotation.itertracks(yield_label=True)
    ]
    out.write_text(
        json.dumps(
            {
                "model": MODEL,
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
