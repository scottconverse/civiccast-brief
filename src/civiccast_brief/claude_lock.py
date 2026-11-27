"""One Claude run at a time on this PC.

Each city's weekly run is its own process, and every run that writes a story calls
``claude -p`` on the same subscription. Two runs in the same hours therefore share one
5-hour limit and one of them fails with "You've hit your 5-hour limit" (Boulder and
Louisville, Nov 26). ``claude_turn`` takes a lock on a file beside the other CivicCast
Brief state for the length of one ``claude -p`` call, so a second run waits its turn.

The lock is a byte-locked file, so the OS frees it if the process dies; the child's
``timeout`` still ends a hung run and frees the lock the usual way.

Only the batch weekly run takes turns: it sets ``CIVICCAST_CLAUDE_TURNS`` to ``"1"``
before it starts. Every other caller (a desk button, the sign-in check) runs at once.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

DEFAULT_POLL_S = 5.0
#: Set to ``"1"`` by the batch weekly run; only then does a call take a turn.
TURNS_ENV = "CIVICCAST_CLAUDE_TURNS"
#: Said once, when the turn has to wait for another run.
WAITING_LINE = "claude: waiting for another Claude run on this PC to finish"


def lock_path() -> Path:
    """``$CIVICCAST_CLAUDE_LOCK``, else the per-user state folder's ``claude.lock``."""
    configured = os.environ.get("CIVICCAST_CLAUDE_LOCK", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CivicCastBrief" / "claude.lock"
    return Path.home() / ".cache" / "civiccast-brief" / "claude.lock"


def _try_lock(fd: int) -> bool:
    """Take the lock without waiting; False when another run holds it."""
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def claude_turn(
    *, say: Callable[[str], None] = print, poll_s: float = DEFAULT_POLL_S
) -> Iterator[None]:
    """Hold the PC's Claude lock for one ``claude -p`` call, waiting for another run.

    Takes a turn only when ``CIVICCAST_CLAUDE_TURNS`` is ``"1"`` (the batch weekly run);
    every other caller runs at once and touches no file.
    """
    if os.environ.get(TURNS_ENV) != "1":
        yield
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - the handle is the lock; closed below
    try:
        fd = handle.fileno()
        if os.name == "nt" and os.fstat(fd).st_size == 0:
            # Windows locks a byte in the file; an empty file has none at position 0.
            handle.write(b"\0")
            handle.flush()
        waited_from: float | None = None
        while not _try_lock(fd):
            if waited_from is None:
                say(WAITING_LINE)
                waited_from = time.monotonic()
            time.sleep(poll_s)
        if waited_from is not None:
            say(f"claude: waited {time.monotonic() - waited_from:.0f} s")
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        handle.close()
