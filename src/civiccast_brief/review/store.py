"""SQLite store for producer review items.

One item is one meeting episode: meeting metadata, speaker labels mapped to names, the recap,
the two-host script, and a status (draft, approved or rejected).

Approval is for exact content. Approving records a SHA-256 hash of everything the export
writes. Any change to the speakers, recap or script after that moves the item back to draft
and clears the approval, so the item must be approved again before it can be exported.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Self

STATUSES: Final = ("draft", "approved", "rejected")
UNKNOWN_SPEAKER: Final = "unknown"

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    speakers TEXT NOT NULL,
    recap TEXT NOT NULL,
    script TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    approved_at TEXT,
    approved_hash TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    requested_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
"""


class ReviewError(Exception):
    """A review operation was refused, or the database it needs is missing."""


def normalize_speakers(speakers: Mapping[str, str]) -> dict[str, str]:
    """Trim labels and names. A blank name becomes "unknown"; a blank label is an error."""
    clean: dict[str, str] = {}
    for label, name in speakers.items():
        key = " ".join(str(label).split())
        if not key:
            raise ReviewError("a speaker label cannot be blank")
        clean[key] = " ".join(str(name).split()) or UNKNOWN_SPEAKER
    return clean


def compute_content_hash(
    *,
    meeting_id: str,
    title: str,
    meeting_date: str,
    speakers: Mapping[str, str],
    recap: str,
    script: str,
) -> str:
    """SHA-256 over the canonical JSON of everything the export writes."""
    payload = {
        "meeting_id": meeting_id,
        "title": title,
        "meeting_date": meeting_date,
        "speakers": dict(sorted(speakers.items())),
        "recap": recap,
        "script": script,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewItem:
    id: int
    meeting_id: str
    title: str
    meeting_date: str
    speakers: dict[str, str]
    recap: str
    script: str
    status: str
    approved_at: str | None
    approved_hash: str | None
    updated_at: str

    @property
    def content_hash(self) -> str:
        return compute_content_hash(
            meeting_id=self.meeting_id,
            title=self.title,
            meeting_date=self.meeting_date,
            speakers=self.speakers,
            recap=self.recap,
            script=self.script,
        )


@dataclass(frozen=True)
class ContentUpdate:
    changed: bool
    previous_status: str
    status: str


@dataclass(frozen=True)
class AudioRequest:
    id: int
    item_id: int
    requested_at: str
    content_hash: str


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: str) -> str:
    """Store text with Unix line endings. Browsers submit textareas with CRLF."""
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def _row_to_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=row["id"],
        meeting_id=row["meeting_id"],
        title=row["title"],
        meeting_date=row["meeting_date"],
        speakers=json.loads(row["speakers"]),
        recap=row["recap"],
        script=row["script"],
        status=row["status"],
        approved_at=row["approved_at"],
        approved_hash=row["approved_hash"],
        updated_at=row["updated_at"],
    )


class ReviewStore:
    """Items and audio requests in one SQLite file.

    One connection is kept for the life of the store (close it, or use it as a context manager).
    Opening a WAL-mode database per call would cost about 0.3 s per save on Windows; one
    connection costs about 0.06 s. A lock serialises calls, so the screen's server thread and
    the caller can share the store.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, timeout=10, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        try:
            # WAL avoids creating and deleting a rollback journal on every save. With WAL,
            # synchronous=NORMAL only risks losing the last commits after a power cut; each save
            # and its status change commit together, so the database never holds an approval
            # whose hash does not match its content.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.executescript(f"BEGIN; {SCHEMA} COMMIT;")
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn
            if not write:
                yield conn
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    @staticmethod
    def _load(conn: sqlite3.Connection, item_id: int) -> ReviewItem:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ReviewError(f"no item with id {item_id}")
        return _row_to_item(row)

    def add_item(
        self,
        *,
        meeting_id: str,
        title: str,
        meeting_date: str,
        speakers: Mapping[str, str],
        recap: str,
        script: str,
    ) -> ReviewItem:
        """Add a new item in draft status. One item per meeting_id."""
        meeting_id = " ".join(str(meeting_id).split())
        if not meeting_id:
            raise ReviewError("meeting_id cannot be blank")
        try:
            with self._connect(write=True) as conn:
                cursor = conn.execute(
                    "INSERT INTO items (meeting_id, title, meeting_date, speakers, recap, script,"
                    " status, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                    (
                        meeting_id,
                        str(title),
                        str(meeting_date),
                        json.dumps(normalize_speakers(speakers), ensure_ascii=False),
                        _text(recap),
                        _text(script),
                        _now(),
                    ),
                )
                new_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ReviewError(f"an item for meeting {meeting_id!r} already exists") from exc
        item = self.get_item(new_id)
        assert item is not None
        return item

    def get_item(self, item_id: int) -> ReviewItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else _row_to_item(row)

    def list_items(self) -> list[ReviewItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM items ORDER BY meeting_date DESC, id DESC"
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def update_content(
        self,
        item_id: int,
        *,
        speakers: Mapping[str, str],
        recap: str,
        script: str,
    ) -> ContentUpdate:
        """Save speakers, recap and script. Any change sends the item back to draft."""
        with self._connect(write=True) as conn:
            old = self._load(conn, item_id)
            new_speakers = normalize_speakers(speakers)
            new_recap = _text(recap)
            new_script = _text(script)
            new_hash = compute_content_hash(
                meeting_id=old.meeting_id,
                title=old.title,
                meeting_date=old.meeting_date,
                speakers=new_speakers,
                recap=new_recap,
                script=new_script,
            )
            if new_hash == old.content_hash:
                return ContentUpdate(changed=False, previous_status=old.status, status=old.status)
            conn.execute(
                "UPDATE items SET speakers = ?, recap = ?, script = ?, status = 'draft',"
                " approved_at = NULL, approved_hash = NULL, updated_at = ? WHERE id = ?",
                (
                    json.dumps(new_speakers, ensure_ascii=False),
                    new_recap,
                    new_script,
                    _now(),
                    item_id,
                ),
            )
        return ContentUpdate(changed=True, previous_status=old.status, status="draft")

    def approve(self, item_id: int) -> bool:
        """Approve the current content. Returns False if this exact content is already approved."""
        with self._connect(write=True) as conn:
            item = self._load(conn, item_id)
            if item.status == "approved" and item.approved_hash == item.content_hash:
                return False
            now = _now()
            conn.execute(
                "UPDATE items SET status = 'approved', approved_at = ?, approved_hash = ?,"
                " updated_at = ? WHERE id = ?",
                (now, item.content_hash, now, item_id),
            )
        return True

    def reject(self, item_id: int) -> None:
        with self._connect(write=True) as conn:
            self._load(conn, item_id)
            conn.execute(
                "UPDATE items SET status = 'rejected', approved_at = NULL, approved_hash = NULL,"
                " updated_at = ? WHERE id = ?",
                (_now(), item_id),
            )

    def record_audio_request(self, item_id: int) -> AudioRequest:
        """Record a request to regenerate audio for the item's current content."""
        with self._connect(write=True) as conn:
            item = self._load(conn, item_id)
            requested_at = _now()
            cursor = conn.execute(
                "INSERT INTO audio_requests (item_id, requested_at, content_hash) VALUES (?, ?, ?)",
                (item_id, requested_at, item.content_hash),
            )
            request_id = cursor.lastrowid
        return AudioRequest(
            id=request_id,
            item_id=item_id,
            requested_at=requested_at,
            content_hash=item.content_hash,
        )

    def list_audio_requests(self, item_id: int) -> list[AudioRequest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audio_requests WHERE item_id = ? ORDER BY id DESC", (item_id,)
            ).fetchall()
        return [
            AudioRequest(
                id=row["id"],
                item_id=row["item_id"],
                requested_at=row["requested_at"],
                content_hash=row["content_hash"],
            )
            for row in rows
        ]
