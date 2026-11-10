"""The desk store: states and actions, publishing locked to exact text, articles, migration."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from civiccast_brief.review.export import export_published
from civiccast_brief.review.store import ReviewError, ReviewStore, compute_content_hash


def episode(store: ReviewStore, meeting_id: str = "m1", **kw):
    return store.add_item(
        meeting_id=meeting_id,
        title="Council",
        meeting_date="2026-11-04",
        speakers={"voice 01": "unknown"},
        recap="Recap.",
        script="[S1] Script.",
        **kw,
    )


def test_new_items_wait_in_review_and_publish_locks_the_text(tmp_path):
    with ReviewStore(tmp_path / "d.db") as store:
        item = episode(store)
        assert item.status == "review" and item.kind == "episode"
        assert store.publish(item.id) is True
        assert store.publish(item.id) is False  # same text, nothing to do
        published = store.get_item(item.id)
        assert published.status == "published"
        assert published.published_hash == published.content_hash
        update = store.update_content(item.id, recap="Recap, edited.")
        assert update.changed and update.previous_status == "published"
        edited = store.get_item(item.id)
        assert edited.status == "review" and edited.published_at is None
        assert [h.action for h in store.history(item.id)] == ["edited", "published", "staged"]


def test_hold_and_kill_unpublish_and_a_killed_item_needs_an_edit(tmp_path):
    with ReviewStore(tmp_path / "d.db") as store:
        item = episode(store)
        store.publish(item.id)
        assert store.hold(item.id, "wait for the minutes") is True
        assert store.hold(item.id) is False
        held = store.get_item(item.id)
        assert held.status == "held" and held.published_hash is None
        assert held.note == "wait for the minutes"
        assert store.kill(item.id) is True
        with pytest.raises(ReviewError, match="killed item cannot be published"):
            store.publish(item.id)
        store.update_content(item.id, script="[S1] New script.")
        assert store.get_item(item.id).status == "review"
        assert store.publish(item.id) is True


def test_auto_publish_needs_passed_checks(tmp_path):
    with ReviewStore(tmp_path / "d.db") as store:
        passed = episode(store, "a", checks_passed=True, auto_publish=True)
        failed = episode(store, "b", checks_passed=False, auto_publish=True)
        not_on = episode(store, "c", checks_passed=True, auto_publish=False)
    assert passed.status == "published"
    assert passed.published_by == "auto-publish (checks passed)"
    assert failed.status == "review" and not_on.status == "review"


def test_an_article_is_checked_edited_and_hashed_with_its_kind(tmp_path, article):
    with ReviewStore(tmp_path / "d.db") as store:
        item = store.add_item(
            kind="article",
            meeting_id="m1",
            title="Council",
            meeting_date="2026-11-04",
            article=article,
            body="City Council",
        )
        same_meeting_episode = episode(store, "m1")  # one item per kind and meeting
        assert item.headline == "Council passes the budget"
        assert item.content_hash != same_meeting_episode.content_hash
        with pytest.raises(ReviewError, match="already exists"):
            store.add_item(
                kind="article", meeting_id="m1", title="x", meeting_date="d", article=article
            )
        with pytest.raises(ReviewError, match="no text"):
            store.add_item(
                kind="article",
                meeting_id="m2",
                title="x",
                meeting_date="d",
                article={**article, "sections": [{"paragraphs": [{"text": " "}]}]},
            )
        with pytest.raises(ReviewError, match="no speakers, recap or script"):
            store.update_content(item.id, recap="x")
        new = json.loads(json.dumps(article))
        new["sections"][0]["paragraphs"][0]["text"] = "It passed 6 to 1."
        assert store.update_content(item.id, article=new).changed
        assert store.get_item(item.id).article["sections"][0]["paragraphs"][0]["cites"] == ["c1"]


def test_a_stale_redraft_is_refused(tmp_path):
    with ReviewStore(tmp_path / "d.db") as store:
        item = episode(store)
        store.update_content(item.id, recap="Someone else edited this.")
        with pytest.raises(ReviewError, match="changed while this ran"):
            store.update_content(item.id, recap="Redraft.", expected_hash=item.content_hash)


OLD_SCHEMA = """
CREATE TABLE items (
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
CREATE TABLE audio_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    requested_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
"""


def old_database(path: Path) -> None:
    """A database as the producer screen left it: one approved, one draft, one rejected."""
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for n, status in enumerate(("approved", "draft", "rejected"), start=1):
        fields = {
            "meeting_id": f"m{n}",
            "title": "T",
            "meeting_date": f"2026-10-0{n}",
            "speakers": {"voice 01": "Clerk"},
            "recap": "R",
            "script": "S",
        }
        digest = compute_content_hash(**fields) if status == "approved" else None
        conn.execute(
            "INSERT INTO items (meeting_id, title, meeting_date, speakers, recap, script, status,"
            " approved_at, approved_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["meeting_id"],
                "T",
                fields["meeting_date"],
                json.dumps(fields["speakers"]),
                "R",
                "S",
                status,
                "2026-10-05T10:00:00+00:00" if digest else None,
                digest,
                "2026-10-05T10:00:00+00:00",
            ),
        )
    conn.execute(
        "INSERT INTO audio_requests (item_id, requested_at, content_hash)"
        " VALUES (1, '2026-10-05T11:00:00+00:00', 'abc')"
    )
    conn.commit()
    conn.close()


def test_an_old_review_database_is_migrated_without_losing_items(tmp_path):
    db = tmp_path / "review.db"
    old_database(db)
    original = tmp_path / "original.db"
    shutil.copy(db, original)
    with ReviewStore(db) as store:
        assert store.migrated == 3
        items = {i.meeting_id: i for i in store.list_items()}
        assert {k: v.status for k, v in items.items()} == {
            "m1": "published",
            "m2": "review",
            "m3": "killed",
        }
        approved = items["m1"]
        assert approved.published_hash == approved.content_hash  # the old approval still holds
        assert approved.published_at == "2026-10-05T10:00:00+00:00"
        assert len(store.list_audio_requests(approved.id)) == 1
        assert store.history(approved.id)[0].action == "migrated"
    with ReviewStore(db) as again:
        assert again.migrated == 0  # once only
    result = export_published(db, tmp_path / "out")
    assert len(result.exported) == 1 and len(result.skipped) == 2
    meta = json.loads((result.exported[0] / "item.json").read_text(encoding="utf-8"))
    assert meta["status"] == "published" and meta["published_at"] == "2026-10-05T10:00:00+00:00"
    assert original.read_bytes() != db.read_bytes()  # the copy kept, the target changed


def test_a_newer_schema_is_refused(tmp_path):
    db = tmp_path / "d.db"
    ReviewStore(db).close()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(ReviewError, match="newer desk"):
        ReviewStore(db)
