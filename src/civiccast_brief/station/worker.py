"""The PC worker: a Windows PC fetches YouTube captions and makes audio for a cloud station.

A cloud server cannot get YouTube captions (YouTube asks a data-center address to sign in) and
has no GPU for the voice. The weekly run there is given ``--worker DIR`` and writes what it
needs into that folder (see :mod:`civiccast_brief.weekly.worker`). The station serves the
folder to the PC under ``/worker``, behind a shared token::

    GET  /worker/jobs                        what the PC should do
    PUT  /worker/<kind>/<key>/<file name>    one file of a job (the request body)
    POST /worker/<kind>/<key>/done           the job's files all arrived

When captions arrive the station starts the weekly job again; when audio arrives it queues a
publish. The PC runs::

    python -m civiccast_brief.station worker --station https://desk.example.org

The token is ``CIVICCAST_WORKER_TOKEN`` or the Windows credential ``CivicCastBrief/worker-token``,
the same on both ends.
"""

from __future__ import annotations

import hmac
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from civiccast_brief.station.runner import Station
from civiccast_brief.weekly.worker import AUDIO, AUDIO_FILES, CAPTIONS, WorkerError, WorkerFolder

TOKEN_ENV: Final = "CIVICCAST_WORKER_TOKEN"
TOKEN_CREDENTIAL: Final = "CivicCastBrief/worker-token"
#: The largest file the station takes from the worker (an hour-long episode is about 60 MB).
MAX_UPLOAD: Final = 300 * 1024 * 1024
DEFAULT_EVERY: Final = 300


def worker_token() -> str | None:
    token = os.environ.get(TOKEN_ENV)
    if token:
        return token
    if sys.platform == "win32":
        from civiccast_brief.record.credentials import read_windows_credential

        return read_windows_credential(TOKEN_CREDENTIAL)
    return None


# ----- the station's side ------------------------------------------------------------------------


def make_worker_app(inbox: WorkerFolder, station: Station, token: str) -> FastAPI:
    """The worker API, mounted at /worker on the station."""
    if not token:
        raise ValueError("the worker API needs a token")
    expected = f"Bearer {token}".encode()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def refused(request: Request) -> JSONResponse | None:
        given = request.headers.get("authorization", "").encode()
        if hmac.compare_digest(given, expected):
            return None
        return JSONResponse({"error": "the worker token is missing or wrong"}, status_code=401)

    def bad(exc: Exception, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=status)

    @app.get("/jobs")
    def jobs(request: Request) -> Any:
        return refused(request) or inbox.jobs()

    @app.put("/{kind}/{key}/{name}")
    async def upload(kind: str, key: str, name: str, request: Request) -> Any:
        if (no := refused(request)) is not None:
            return no
        try:
            inbox.folder(kind, key)
        except WorkerError as exc:
            return bad(exc)
        incoming = inbox.root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=incoming, suffix=".part")
        size = 0
        try:
            with os.fdopen(handle, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        return bad(WorkerError(f"{name} is over {MAX_UPLOAD // 2**20} MB"), 413)
                    out.write(chunk)
            inbox.receive(kind, key, name, Path(temp))
        except WorkerError as exc:
            return bad(exc)
        finally:
            Path(temp).unlink(missing_ok=True)
        return {"received": name, "bytes": size}

    @app.post("/{kind}/{key}/done")
    def done(kind: str, key: str, request: Request) -> Any:
        if (no := refused(request)) is not None:
            return no
        if kind == CAPTIONS and station.status().running is not None:
            # The run going now may be past this meeting; the worker tries again later.
            return bad(WorkerError("the weekly job is running; send done again later"), 409)
        try:
            inbox.finish(kind, key)
        except WorkerError as exc:
            return bad(exc)
        if kind == CAPTIONS:
            station.run_now()
            return {"done": key, "next": "the weekly job started"}
        station.request_publish()
        return {"done": key, "next": "a publish is queued"}

    return app


# ----- the PC's side -----------------------------------------------------------------------------

#: Fetches one video's captions into a folder (meeting.capture.capture_captions).
Capture = Callable[[str, Path], object]
#: Runs the audio command with its arguments; returns the exit code (audio.cli.main).
Speak = Callable[[list[str]], int]


def _capture(video_id: str, folder: Path) -> object:
    from civiccast_brief.meeting.capture import capture_captions

    return capture_captions(video_id, folder)


def _speak(argv: list[str]) -> int:
    from civiccast_brief.audio.cli import main

    return main(argv)


class WorkerFailed(RuntimeError):
    """One job could not be done; the message says why."""


def _check(response: Any, what: str) -> Any:
    if response.status_code >= 400:
        try:
            reason = response.json().get("error", "")
        except ValueError:
            reason = response.text[:200]
        raise WorkerFailed(f"{what}: the station said {response.status_code} {reason}".rstrip())
    return response.json()


def _send(client: Any, kind: str, key: str, files: Sequence[Path]) -> None:
    for path in files:
        with path.open("rb") as body:
            _check(client.put(f"/worker/{kind}/{key}/{path.name}", content=body), path.name)
    _check(client.post(f"/worker/{kind}/{key}/done"), f"finishing {kind} {key}")


def do_captions(client: Any, job: dict[str, Any], work: Path, capture: Capture) -> None:
    video_id = str(job.get("video_id", ""))
    folder = work / CAPTIONS / video_id
    try:
        capture(video_id, folder)
    except Exception as exc:  # any capture failure is reported, then the next job
        raise WorkerFailed(f"captions for {video_id}: {exc}") from exc
    files = sorted(
        p
        for p in folder.iterdir()
        if p.is_file() and p.name.startswith(video_id) and not p.name.endswith((".part", ".tmp"))
    )
    _send(client, CAPTIONS, video_id, files)


def do_audio(client: Any, job: dict[str, Any], work: Path, speak: Speak) -> None:
    key = str(job.get("key", ""))
    folder = work / AUDIO / key
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "script.txt"
    script.write_text(str(job.get("script", "")), encoding="utf-8")
    engine = str(job.get("engine") or "")
    argv = [str(script), "--out", str(folder), *(["--engine", engine] if engine else [])]
    if speak(argv) != 0:
        raise WorkerFailed(f"audio {key}: the audio command failed (its output says why)")
    _send(client, AUDIO, key, [folder / name for name in AUDIO_FILES])


def work_once(
    client: Any,
    work: Path,
    say: Callable[[str], None],
    *,
    capture: Capture = _capture,
    speak: Speak = _speak,
) -> tuple[int, int]:
    """Do every job the station has now; returns (done, failed)."""
    jobs = _check(client.get("/worker/jobs"), "reading the jobs")
    done = failed = 0
    for kind in (CAPTIONS, AUDIO):
        for job in jobs.get(kind, []):
            name = job.get("video_id") or job.get("key")
            try:
                if kind == CAPTIONS:
                    do_captions(client, job, work, capture)
                else:
                    do_audio(client, job, work, speak)
            except (WorkerFailed, OSError) as exc:
                failed += 1
                say(f"failed: {exc}")
                continue
            done += 1
            say(f"sent {kind} {name}")
    return done, failed


def run_worker(
    station_url: str,
    token: str,
    work: Path,
    say: Callable[[str], None],
    *,
    every: int = DEFAULT_EVERY,
    once: bool = False,
    client: Any = None,
    capture: Capture = _capture,
    speak: Speak = _speak,
) -> int:
    """Poll the station and do its jobs; with ``once`` a single pass (exit 1 if any failed)."""
    if client is None:
        client = httpx.Client(
            base_url=station_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60.0, write=600.0),
        )
    work.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            done, failed = work_once(client, work, say, capture=capture, speak=speak)
            if done or failed:
                say(f"{done} sent, {failed} failed")
        except (WorkerFailed, OSError) as exc:
            failed = 1
            say(f"failed: {exc}")
        except httpx.HTTPError as exc:  # a network error waits for the next pass
            failed = 1
            say(f"failed: cannot reach the station: {exc}")
        if once:
            return 1 if failed else 0
        time.sleep(every)
