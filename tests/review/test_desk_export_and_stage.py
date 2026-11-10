"""The export gate, staging with auto-publish, and redraft."""

from __future__ import annotations

import json
import sqlite3

import pytest

from civiccast_brief.review import desk
from civiccast_brief.review.export import export_published
from civiccast_brief.review.store import ReviewError, ReviewStore


def stage(db, city, meeting_id="m1", body="Planning Commission", checks=True, **kw):
    fields = (
        {"speakers": {"voice 01": "unknown"}, "recap": "R", "script": "S"}
        if "article" not in kw
        else {}
    )
    return desk.stage_item(
        db,
        city,
        kind="article" if "article" in kw else "episode",
        meeting_id=meeting_id,
        title="T",
        meeting_date="2026-11-04",
        body=body,
        checks_passed=checks,
        **fields,
        **kw,
    )


def test_export_writes_only_published_items_and_removes_ones_pulled_back(tmp_path, article):
    db, out = tmp_path / "d.db", tmp_path / "out"
    a = stage(db, None, "m1")
    b = stage(db, None, "m2", article=article)
    stage(db, None, "m3")
    with ReviewStore(db) as store:
        store.publish(a.id)
        store.publish(b.id)
    first = export_published(db, out)
    assert sorted(p.name for p in first.exported) == ["0001-m1", "0002-m2"]
    assert (out / "0001-m1" / "script.txt").read_text(encoding="utf-8") == "S"
    assert (
        json.loads((out / "0002-m2" / "article.json").read_text(encoding="utf-8"))["headline"]
        == article["headline"]
    )
    meta = json.loads((out / "0002-m2" / "item.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "article" and meta["body"] == "Planning Commission"
    assert {s.meeting_id: s.reason for s in first.skipped} == {
        "m3": "status is review; only published items are exported"
    }

    with ReviewStore(db) as store:
        store.hold(a.id)
    second = export_published(db, out)
    assert [p.name for p in second.exported] == ["0002-m2"]
    assert not (out / "0001-m1").exists()


def test_export_refuses_text_changed_behind_the_desk(tmp_path):
    db = tmp_path / "d.db"
    item = stage(db, None)
    with ReviewStore(db) as store:
        store.publish(item.id)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE items SET script = 'tampered' WHERE id = ?", (item.id,))
    conn.commit()
    conn.close()
    result = export_published(db, tmp_path / "out")
    assert result.exported == ()
    assert "content changed after publishing" in result.skipped[0].reason


def test_export_never_creates_a_database(tmp_path):
    with pytest.raises(ReviewError, match="database not found"):
        export_published(tmp_path / "missing.db", tmp_path / "out")
    assert not (tmp_path / "missing.db").exists()


def test_stage_auto_publishes_only_for_bodies_turned_on(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    desk.save_auto_publish(city, {"Planning Commission": True, "City Council": False})
    db = tmp_path / "d.db"
    assert stage(db, city, "a").status == "published"
    assert stage(db, city, "b", checks=False).status == "review"
    assert stage(db, city, "c", body="City Council").status == "review"
    assert stage(db, city, "d", body="Library Board").status == "review"
    assert desk.load_auto_publish(city) == {"City Council": False, "Planning Commission": True}


def test_a_broken_desk_file_is_reported(tmp_path):
    (tmp_path / "desk.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ReviewError, match="not a readable desk settings file"):
        desk.load_auto_publish(tmp_path)


def test_redraft_sends_the_note_to_the_writer_and_returns_to_review(tmp_path, article):
    db = tmp_path / "d.db"
    item = stage(db, None)
    seen = []

    def writer(current, note):
        seen.append((current.id, note))
        return {"recap": "R, shorter.", "checks_passed": True}

    with ReviewStore(db) as store:
        store.publish(item.id)
        with pytest.raises(ReviewError, match="write a note"):
            desk.redraft(store, item.id, "  ", writer)
        desk.redraft(store, item.id, "Make it shorter.", writer)
        after = store.get_item(item.id)
        assert seen == [(item.id, "Make it shorter.")]
        assert (after.recap, after.status, after.checks_passed) == ("R, shorter.", "review", True)
        assert after.note == "Make it shorter."
        assert store.history(item.id)[0].action == "redrafted"
        with pytest.raises(ReviewError, match="same text"):
            desk.redraft(store, item.id, "Again.", lambda i, n: {"recap": "R, shorter."})
        with pytest.raises(ReviewError, match="fields an episode has not"):
            desk.redraft(store, item.id, "Again.", lambda i, n: {"article": article})
