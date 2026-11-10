"""SQLite store for the editor desk.

One item is one piece of work for the site: an ``episode`` (meeting metadata, speaker labels
mapped to names, the recap and the two-host script) or an ``article`` (a JSON object: headline,
dek, sections of cited paragraphs, votes, quotes, topics).

States, as in TownReporter:

- ``review``: waiting for the editor. Every new, edited or redrafted item is here.
- ``held``: set aside; not published until the editor publishes it.
- ``killed``: will not run. Editing or redrafting it brings it back to review.
- ``published``: the editor (or auto-publish) published this exact content.

Publishing is for exact content. It records a SHA-256 hash of everything the export writes.
Any change to the content after that moves the item back to review and clears the publish
record, so the item must be published again before the export writes it.

Databases from the earlier producer screen (statuses draft, approved and rejected) are migrated
the first time the store opens them: draft becomes review, approved becomes published (keeping
its approval time and hash), rejected becomes killed.
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
from typing import Any, Final, Self

KINDS: Final = ("episode", "article")
STATUSES: Final = ("review", "held", "killed", "published")
UNKNOWN_SPEAKER: Final = "unknown"
SCHEMA_VERSION: Final = 2
#: Old producer-screen status -> desk status.
MIGRATED_STATUS: Final = {"draft": "review", "approved": "published", "rejected": "killed"}

ITEMS_TABLE: Final = """
CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'episode' CHECK (kind IN ('episode', 'article')),
    meeting_id TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    speakers TEXT NOT NULL DEFAULT '{{}}',
    recap TEXT NOT NULL DEFAULT '',
    script TEXT NOT NULL DEFAULT '',
    article TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'review'
        CHECK (status IN ('review', 'held', 'killed', 'published')),
    checks_passed INTEGER CHECK (checks_passed IN (0, 1)),
    checks_note TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    published_hash TEXT,
    published_by TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, meeting_id)
);
"""
OTHER_TABLES: Final = """
CREATE TABLE IF NOT EXISTS audio_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    requested_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


class ReviewError(Exception):
    """A desk operation was refused, or the database it needs is missing."""


def normalize_speakers(speakers: Mapping[str, str]) -> dict[str, str]:
    """Trim labels and names. A blank name becomes "unknown"; a blank label is an error."""
    clean: dict[str, str] = {}
    for label, name in speakers.items():
        key = " ".join(str(label).split())
        if not key:
            raise ReviewError("a speaker label cannot be blank")
        clean[key] = " ".join(str(name).split()) or UNKNOWN_SPEAKER
    return clean


def _text(value: str) -> str:
    """Store text with Unix line endings. Browsers submit textareas with CRLF."""
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def normalize_article(article: Any) -> dict[str, Any]:
    """Check an article's shape and return a clean copy. Raises ReviewError naming the part.

    headline (text, required), dek (text), sections (list of {heading?, paragraphs: list of
    {text, cites: list of text}}), votes (list), quotes (list), topics (list of text).
    """
    if not isinstance(article, Mapping):
        raise ReviewError("an article must be a JSON object")
    headline = " ".join(str(article.get("headline") or "").split())
    if not headline:
        raise ReviewError("the article needs a headline")
    sections_in = article.get("sections") or []
    if not isinstance(sections_in, list) or not sections_in:
        raise ReviewError("the article needs at least one section")
    sections: list[dict[str, Any]] = []
    for s_no, section in enumerate(sections_in, start=1):
        if not isinstance(section, Mapping):
            raise ReviewError(f"section {s_no} must be an object")
        paragraphs_in = section.get("paragraphs") or []
        if not isinstance(paragraphs_in, list):
            raise ReviewError(f"section {s_no}: paragraphs must be a list")
        paragraphs: list[dict[str, Any]] = []
        for p_no, paragraph in enumerate(paragraphs_in, start=1):
            if not isinstance(paragraph, Mapping):
                raise ReviewError(f"section {s_no} paragraph {p_no} must be an object")
            text = _text(str(paragraph.get("text") or "")).strip()
            if not text:
                raise ReviewError(f"section {s_no} paragraph {p_no} has no text")
            cites = paragraph.get("cites") or []
            if not isinstance(cites, list):
                raise ReviewError(f"section {s_no} paragraph {p_no}: cites must be a list")
            paragraphs.append({**paragraph, "text": text, "cites": [str(c) for c in cites]})
        if not paragraphs:
            raise ReviewError(f"section {s_no} has no paragraphs")
        clean = {**section, "paragraphs": paragraphs}
        if "heading" in section:
            clean["heading"] = " ".join(str(section.get("heading") or "").split())
        sections.append(clean)
    for name in ("votes", "quotes", "topics"):
        if not isinstance(article.get(name) or [], list):
            raise ReviewError(f"the article's {name} must be a list")
    return {
        **article,
        "headline": headline,
        "dek": " ".join(str(article.get("dek") or "").split()),
        "sections": sections,
        "votes": list(article.get("votes") or []),
        "quotes": list(article.get("quotes") or []),
        "topics": [str(t) for t in article.get("topics") or []],
    }


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_content_hash(
    *,
    meeting_id: str,
    title: str,
    meeting_date: str,
    speakers: Mapping[str, str] | None = None,
    recap: str = "",
    script: str = "",
    kind: str = "episode",
    article: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 over the canonical JSON of everything the export writes.

    An episode's hash is the same as the earlier producer screen's, so migrated approvals still
    match their content.
    """
    if kind == "article":
        payload: dict[str, Any] = {
            "kind": "article",
            "meeting_id": meeting_id,
            "title": title,
            "meeting_date": meeting_date,
            "article": dict(article or {}),
        }
    else:
        payload = {
            "meeting_id": meeting_id,
            "title": title,
            "meeting_date": meeting_date,
            "speakers": dict(sorted((speakers or {}).items())),
            "recap": recap,
            "script": script,
        }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeskItem:
    id: int
    kind: str
    meeting_id: str
    body: str
    title: str
    meeting_date: str
    speakers: dict[str, str]
    recap: str
    script: str
    article: dict[str, Any] | None
    status: str
    checks_passed: bool | None
    checks_note: str
    note: str
    published_at: str | None
    published_hash: str | None
    published_by: str | None
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
            kind=self.kind,
            article=self.article,
        )

    @property
    def headline(self) -> str:
        return (self.article or {}).get("headline", "") if self.kind == "article" else self.title


#: The earlier name, kept for code that imports it.
ReviewItem = DeskItem


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


@dataclass(frozen=True)
class HistoryEntry:
    at: str
    action: str
    status: str
    note: str


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_to_item(row: sqlite3.Row) -> DeskItem:
    checks = row["checks_passed"]
    return DeskItem(
        id=row["id"],
        kind=row["kind"],
        meeting_id=row["meeting_id"],
        body=row["body"],
        title=row["title"],
        meeting_date=row["meeting_date"],
        speakers=json.loads(row["speakers"] or "{}"),
        recap=row["recap"],
        script=row["script"],
        article=json.loads(row["article"]) if row["article"] else None,
        status=row["status"],
        checks_passed=None if checks is None else bool(checks),
        checks_note=row["checks_note"],
        note=row["note"],
        published_at=row["published_at"],
        published_hash=row["published_hash"],
        published_by=row["published_by"],
        updated_at=row["updated_at"],
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_v1(conn: sqlite3.Connection) -> int:
    """Rebuild a producer-screen items table as a desk table. Returns the items moved."""
    conn.execute(ITEMS_TABLE.format(name="items_v2"))
    rows = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
    for row in rows:
        old = row["status"]
        status = MIGRATED_STATUS.get(old)
        if status is None:
            raise ReviewError(f"item {row['id']} has an unknown status {old!r}; not migrated")
        published = status == "published"
        conn.execute(
            "INSERT INTO items_v2 (id, kind, meeting_id, body, title, meeting_date, speakers,"
            " recap, script, status, published_at, published_hash, published_by, updated_at)"
            " VALUES (?, 'episode', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["meeting_id"],
                row["title"],
                row["meeting_date"],
                row["speakers"],
                row["recap"],
                row["script"],
                status,
                row["approved_at"] if published else None,
                row["approved_hash"] if published else None,
                "producer screen (approved before the desk)" if published else None,
                row["updated_at"],
            ),
        )
    conn.execute("DROP TABLE items")
    conn.execute("ALTER TABLE items_v2 RENAME TO items")
    for table_sql in OTHER_TABLES.split(";"):
        if table_sql.strip():
            conn.execute(table_sql)
    now = _now()
    for row in rows:
        conn.execute(
            "INSERT INTO history (item_id, at, action, status, note) VALUES (?, ?, ?, ?, ?)",
            (
                row["id"],
                now,
                "migrated",
                MIGRATED_STATUS[row["status"]],
                f"was {row['status']} on the producer screen",
            ),
        )
    return len(rows)


class ReviewStore:
    """Desk items, their history and audio requests in one SQLite file.

    One connection is kept for the life of the store (close it, or use it as a context manager).
    Opening a WAL-mode database per call would cost about 0.3 s per save on Windows; one
    connection costs about 0.06 s. A lock serialises calls, so the desk's server threads and
    the caller can share the store.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.migrated = 0
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, timeout=10, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        try:
            # WAL avoids creating and deleting a rollback journal on every save. With WAL,
            # synchronous=NORMAL only risks losing the last commits after a power cut; each save
            # and its status change commit together, so the database never holds a publish
            # record whose hash does not match its content.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._open_schema()
        except BaseException:
            self._conn.close()
            raise

    def _open_schema(self) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            has_items = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'items'"
                ).fetchone()
            )
            if version > SCHEMA_VERSION:
                raise ReviewError(
                    f"{self.path} was made by a newer desk (schema {version}); update the program"
                )
            if has_items and "approved_hash" in _columns(conn, "items"):
                self.migrated = _migrate_v1(conn)
            else:
                conn.execute(ITEMS_TABLE.format(name="items"))
                for table_sql in OTHER_TABLES.split(";"):
                    if table_sql.strip():
                        conn.execute(table_sql)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

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
    def _load(conn: sqlite3.Connection, item_id: int) -> DeskItem:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ReviewError(f"no item with id {item_id}")
        return _row_to_item(row)

    @staticmethod
    def _log(conn: sqlite3.Connection, item_id: int, action: str, status: str, note: str = ""):
        conn.execute(
            "INSERT INTO history (item_id, at, action, status, note) VALUES (?, ?, ?, ?, ?)",
            (item_id, _now(), action, status, note),
        )

    # ----- adding ---------------------------------------------------------------------------

    def add_item(
        self,
        *,
        meeting_id: str,
        title: str,
        meeting_date: str,
        speakers: Mapping[str, str] | None = None,
        recap: str = "",
        script: str = "",
        kind: str = "episode",
        article: Mapping[str, Any] | None = None,
        body: str = "",
        checks_passed: bool | None = None,
        checks_note: str = "",
        auto_publish: bool = False,
    ) -> DeskItem:
        """Add a new item for review. One item per kind and meeting_id.

        With ``auto_publish``, an item whose checks passed is published at once, with no press.
        """
        meeting_id = " ".join(str(meeting_id).split())
        if not meeting_id:
            raise ReviewError("meeting_id cannot be blank")
        if kind not in KINDS:
            raise ReviewError(f"kind must be one of {', '.join(KINDS)}")
        if kind == "article":
            article_json = _canonical(normalize_article(article))
            if speakers or recap or script:
                raise ReviewError("an article has no speakers, recap or script")
        else:
            if article is not None:
                raise ReviewError("an episode has no article")
            article_json = ""
        now = _now()
        try:
            with self._connect(write=True) as conn:
                cursor = conn.execute(
                    "INSERT INTO items (kind, meeting_id, body, title, meeting_date, speakers,"
                    " recap, script, article, status, checks_passed, checks_note, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'review', ?, ?, ?)",
                    (
                        kind,
                        meeting_id,
                        " ".join(str(body).split()),
                        str(title),
                        str(meeting_date),
                        json.dumps(normalize_speakers(speakers or {}), ensure_ascii=False),
                        _text(recap),
                        _text(script),
                        article_json,
                        None if checks_passed is None else int(bool(checks_passed)),
                        str(checks_note),
                        now,
                    ),
                )
                new_id = int(cursor.lastrowid or 0)
                self._log(conn, new_id, "staged", "review", str(checks_note))
                if auto_publish and checks_passed:
                    item = self._load(conn, new_id)
                    self._publish(conn, item, by="auto-publish (checks passed)")
        except sqlite3.IntegrityError as exc:
            raise ReviewError(f"a {kind} for meeting {meeting_id!r} already exists") from exc
        item = self.get_item(new_id)
        assert item is not None
        return item

    # ----- reading --------------------------------------------------------------------------

    def get_item(self, item_id: int) -> DeskItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else _row_to_item(row)

    def list_items(self, status: str | None = None) -> list[DeskItem]:
        query = "SELECT * FROM items"
        args: tuple[Any, ...] = ()
        if status is not None:
            if status not in STATUSES:
                raise ReviewError(f"status must be one of {', '.join(STATUSES)}")
            query += " WHERE status = ?"
            args = (status,)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY meeting_date DESC, id DESC", args).fetchall()
        return [_row_to_item(row) for row in rows]

    def history(self, item_id: int) -> list[HistoryEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM history WHERE item_id = ? ORDER BY id DESC", (item_id,)
            ).fetchall()
        return [HistoryEntry(r["at"], r["action"], r["status"], r["note"]) for r in rows]

    # ----- editing --------------------------------------------------------------------------

    def update_content(
        self,
        item_id: int,
        *,
        speakers: Mapping[str, str] | None = None,
        recap: str | None = None,
        script: str | None = None,
        article: Mapping[str, Any] | None = None,
        action: str = "edited",
        note: str = "",
        checks_passed: bool | None = None,
        expected_hash: str | None = None,
    ) -> ContentUpdate:
        """Save new content. Any change sends the item back to review and clears publishing.

        Fields left as None keep their value. ``expected_hash`` refuses the save if the item
        changed since the caller read it (a redraft takes a while).
        """
        with self._connect(write=True) as conn:
            old = self._load(conn, item_id)
            if expected_hash is not None and expected_hash != old.content_hash:
                raise ReviewError("the item changed while this ran; open it again and retry")
            if old.kind == "article":
                if speakers is not None or recap is not None or script is not None:
                    raise ReviewError("an article has no speakers, recap or script")
                new_article = normalize_article(article) if article is not None else old.article
                new_values: dict[str, Any] = {"article": new_article}
            else:
                if article is not None:
                    raise ReviewError("an episode has no article")
                new_values = {
                    "speakers": normalize_speakers(speakers)
                    if speakers is not None
                    else old.speakers,
                    "recap": _text(recap) if recap is not None else old.recap,
                    "script": _text(script) if script is not None else old.script,
                }
            new_hash = compute_content_hash(
                meeting_id=old.meeting_id,
                title=old.title,
                meeting_date=old.meeting_date,
                kind=old.kind,
                **new_values,
            )
            if new_hash == old.content_hash:
                return ContentUpdate(changed=False, previous_status=old.status, status=old.status)
            conn.execute(
                "UPDATE items SET speakers = ?, recap = ?, script = ?, article = ?,"
                " status = 'review', published_at = NULL, published_hash = NULL,"
                " published_by = NULL, note = ?, checks_passed = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(new_values.get("speakers", {}), ensure_ascii=False),
                    new_values.get("recap", ""),
                    new_values.get("script", ""),
                    _canonical(new_values["article"]) if old.kind == "article" else "",
                    str(note) or old.note,
                    None if checks_passed is None else int(bool(checks_passed)),
                    _now(),
                    item_id,
                ),
            )
            self._log(conn, item_id, action, "review", str(note))
        return ContentUpdate(changed=True, previous_status=old.status, status="review")

    # ----- state changes --------------------------------------------------------------------

    def _publish(self, conn: sqlite3.Connection, item: DeskItem, *, by: str) -> None:
        now = _now()
        conn.execute(
            "UPDATE items SET status = 'published', published_at = ?, published_hash = ?,"
            " published_by = ?, updated_at = ? WHERE id = ?",
            (now, item.content_hash, by, now, item.id),
        )
        self._log(conn, item.id, "published", "published", by)

    def publish(self, item_id: int, *, by: str = "editor") -> bool:
        """Publish the current content. False if this exact content is already published."""
        with self._connect(write=True) as conn:
            item = self._load(conn, item_id)
            if item.status == "killed":
                raise ReviewError("a killed item cannot be published; edit or redraft it first")
            if item.status == "published" and item.published_hash == item.content_hash:
                return False
            self._publish(conn, item, by=by)
        return True

    def _set_status(self, item_id: int, status: str, action: str, note: str) -> bool:
        with self._connect(write=True) as conn:
            item = self._load(conn, item_id)
            if item.status == status:
                return False
            conn.execute(
                "UPDATE items SET status = ?, published_at = NULL, published_hash = NULL,"
                " published_by = NULL, note = CASE WHEN ? = '' THEN note ELSE ? END,"
                " updated_at = ? WHERE id = ?",
                (status, note, note, _now(), item_id),
            )
            self._log(conn, item_id, action, status, note)
        return True

    def hold(self, item_id: int, note: str = "") -> bool:
        """Set the item aside. A published item is unpublished. False if already held."""
        return self._set_status(item_id, "held", "held", str(note))

    def kill(self, item_id: int, note: str = "") -> bool:
        """The item will not run. A published item is unpublished. False if already killed."""
        return self._set_status(item_id, "killed", "killed", str(note))

    # ----- audio ----------------------------------------------------------------------------

    def record_audio_request(self, item_id: int) -> AudioRequest:
        """Record a request to regenerate audio for the item's current content."""
        with self._connect(write=True) as conn:
            item = self._load(conn, item_id)
            if item.kind != "episode":
                raise ReviewError("only an episode has audio")
            requested_at = _now()
            cursor = conn.execute(
                "INSERT INTO audio_requests (item_id, requested_at, content_hash) VALUES (?, ?, ?)",
                (item_id, requested_at, item.content_hash),
            )
            request_id = int(cursor.lastrowid or 0)
        return AudioRequest(request_id, item_id, requested_at, item.content_hash)

    def list_audio_requests(self, item_id: int) -> list[AudioRequest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audio_requests WHERE item_id = ? ORDER BY id DESC", (item_id,)
            ).fetchall()
        return [
            AudioRequest(row["id"], row["item_id"], row["requested_at"], row["content_hash"])
            for row in rows
        ]
