"""The folder a Windows PC worker fills: YouTube captions and episode audio.

A cloud server cannot fetch YouTube captions (YouTube asks a data-center address to sign in)
and has no GPU for the voice. With ``--worker DIR`` the weekly run asks for both here instead
of doing them, and the station's worker API (:mod:`civiccast_brief.station.worker`) lets a
Windows PC on a home connection read the requests and send the files back::

    DIR/wanted/captions/<video id>.json    a meeting waits for its captions
    DIR/wanted/audio/<key>.json            a published episode waits for its audio
    DIR/captions/<video id>/...            the caption file and yt-dlp's info file
    DIR/audio/<key>/episode.mp3, chapters.json, transcript.vtt

A folder is used only once it has its ``.done`` marker, which the worker API writes after the
last file of the job arrived. ``key`` names one script spoken by one engine, so an edited
script asks for new audio.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

CAPTIONS: Final = "captions"
AUDIO: Final = "audio"
KINDS: Final = (CAPTIONS, AUDIO)
AUDIO_FILES: Final = ("episode.mp3", "chapters.json", "transcript.vtt")
DONE: Final = ".done"
_KEY: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NAME: Final = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}$")


class WorkerError(ValueError):
    """A request to the worker folder that cannot be taken; the message says why."""


def audio_key(script: str, engine: str) -> str:
    """One key per script text and voice engine."""
    words = " ".join(script.split())
    return hashlib.sha256(f"{engine}\n{words}".encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


@dataclass(frozen=True)
class WorkerFolder:
    root: Path

    def _check(self, kind: str, key: str) -> None:
        if kind not in KINDS:
            raise WorkerError(f"the job kind must be one of {', '.join(KINDS)}")
        if not _KEY.match(key):
            raise WorkerError(f"not a job key: {key!r}")

    def _wanted(self, kind: str, key: str) -> Path:
        return self.root / "wanted" / kind / f"{key}.json"

    def folder(self, kind: str, key: str) -> Path:
        self._check(kind, key)
        return self.root / kind / key

    def ready(self, kind: str, key: str) -> Path | None:
        """The job's folder once all of it arrived, else None."""
        folder = self.folder(kind, key)
        return folder if (folder / DONE).is_file() else None

    # ----- the weekly run asks ---------------------------------------------------------------

    def want_captions(self, video_id: str) -> None:
        self._check(CAPTIONS, video_id)
        path = self._wanted(CAPTIONS, video_id)
        if not path.is_file():
            _write_json(path, {"video_id": video_id, "asked_at": _now()})

    def want_audio(self, key: str, script: str, engine: str) -> None:
        self._check(AUDIO, key)
        path = self._wanted(AUDIO, key)
        if not path.is_file():
            _write_json(path, {"key": key, "script": script, "engine": engine, "asked_at": _now()})

    # ----- the worker API ----------------------------------------------------------------------

    def jobs(self) -> dict[str, list[dict[str, Any]]]:
        """What the worker should do, oldest request first."""
        found: dict[str, list[dict[str, Any]]] = {CAPTIONS: [], AUDIO: []}
        for kind in KINDS:
            folder = self.root / "wanted" / kind
            if not folder.is_dir():
                continue
            entries = []
            for path in folder.glob("*.json"):
                try:
                    entries.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            found[kind] = sorted(entries, key=lambda e: str(e.get("asked_at", "")))
        return found

    def receive(self, kind: str, key: str, name: str, source: Path) -> None:
        """Take one uploaded file (already saved at ``source``) into the job's folder."""
        folder = self.folder(kind, key)
        if not _NAME.match(name) or name == DONE:
            raise WorkerError(f"not a file name the worker sends: {name!r}")
        if kind == AUDIO and name not in AUDIO_FILES:
            raise WorkerError(f"an audio job takes only {', '.join(AUDIO_FILES)}")
        if kind == CAPTIONS and not name.startswith(key):
            raise WorkerError(f"a caption file for {key} must start with {key}")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / DONE).unlink(missing_ok=True)
        os.replace(source, folder / name)

    def finish(self, kind: str, key: str) -> None:
        """Mark the job complete; refuse when a file it needs is missing."""
        folder = self.folder(kind, key)
        names = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
        if kind == AUDIO:
            missing = [n for n in AUDIO_FILES if n not in names]
            if missing:
                raise WorkerError(f"audio {key} is missing {', '.join(missing)}")
        elif not any(n.endswith((".srv3", ".vtt", "panel.json")) for n in names):
            raise WorkerError(f"no caption file (.srv3, .vtt or panel.json) for {key} arrived")
        (folder / DONE).write_text(_now(), encoding="utf-8")
        self._wanted(kind, key).unlink(missing_ok=True)

    # ----- the weekly run takes ----------------------------------------------------------------

    def copy_into(self, kind: str, key: str, out: Path) -> bool:
        """Copy a finished job's files into ``out``; False when it has not arrived yet."""
        folder = self.ready(kind, key)
        if folder is None:
            return False
        out.mkdir(parents=True, exist_ok=True)
        for path in folder.iterdir():
            if path.is_file() and path.name != DONE:
                shutil.copyfile(path, out / path.name)
        return True
