"""The export gate: only approved items whose content is unchanged since approval get exported."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from civiccast_brief.review.export import export_approved
from civiccast_brief.review.store import ReviewError, ReviewStore

MEETING = "2026-11-04-longmont-council"
SPEAKERS = {"SPEAKER_00": "Mayor Smith", "SPEAKER_01": "unknown"}
RECAP = "The council voted five to two to approve Ordinance 2026-69."
SCRIPT = "[S1] The council voted five to two.\n[S2] Right, on Ordinance 2026-69."


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    return ReviewStore(tmp_path / "review.db")


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "export"


def add_item(store: ReviewStore, meeting_id: str = MEETING, recap: str = RECAP):
    return store.add_item(
        meeting_id=meeting_id,
        title="Longmont City Council, November 4",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap=recap,
        script=SCRIPT,
    )


def folders(out: Path) -> list[str]:
    if not out.is_dir():
        return []
    return sorted(p.name for p in out.iterdir() if p.is_dir())


def test_draft_item_is_refused(store: ReviewStore, out: Path) -> None:
    item = add_item(store)

    result = export_approved(store.path, out)

    assert result.exported == ()
    assert [s.item_id for s in result.skipped] == [item.id]
    assert "draft" in result.skipped[0].reason
    assert folders(out) == []


def test_rejected_item_is_refused(store: ReviewStore, out: Path) -> None:
    item = add_item(store)
    store.reject(item.id)

    result = export_approved(store.path, out)

    assert result.exported == ()
    assert "rejected" in result.skipped[0].reason
    assert folders(out) == []


def test_approved_item_is_exported(store: ReviewStore, out: Path) -> None:
    item = add_item(store)
    store.approve(item.id)

    result = export_approved(store.path, out)

    assert result.skipped == ()
    assert len(result.exported) == 1
    folder = result.exported[0]
    assert folder.parent == out
    assert (folder / "recap.txt").read_text(encoding="utf-8") == RECAP
    assert (folder / "script.txt").read_text(encoding="utf-8") == SCRIPT
    meta = json.loads((folder / "item.json").read_text(encoding="utf-8"))
    assert meta["meeting_id"] == MEETING
    assert meta["speakers"] == SPEAKERS
    assert meta["content_hash"] == item.content_hash


def test_approved_then_edited_is_refused_until_approved_again(
    store: ReviewStore, out: Path
) -> None:
    item = add_item(store)
    store.approve(item.id)
    store.update_content(item.id, speakers=SPEAKERS, recap=RECAP + " Edited.", script=SCRIPT)

    refused = export_approved(store.path, out)
    assert refused.exported == ()
    assert "draft" in refused.skipped[0].reason

    store.approve(item.id)
    allowed = export_approved(store.path, out)
    assert allowed.skipped == ()
    assert len(allowed.exported) == 1
    recap_text = (allowed.exported[0] / "recap.txt").read_text(encoding="utf-8")
    assert recap_text == RECAP + " Edited."


def test_approved_status_with_changed_content_is_refused(store: ReviewStore, out: Path) -> None:
    # Bypass the store: change the recap but leave status approved and the old hash in place.
    item = add_item(store)
    store.approve(item.id)
    conn = sqlite3.connect(store.path)
    try:
        conn.execute(
            "UPDATE items SET recap = ? WHERE id = ?", ("Changed behind the gate.", item.id)
        )
        conn.commit()
        status = conn.execute("SELECT status FROM items WHERE id = ?", (item.id,)).fetchone()
    finally:
        conn.close()
    assert status == ("approved",)

    result = export_approved(store.path, out)

    assert result.exported == ()
    assert "changed after approval" in result.skipped[0].reason
    assert folders(out) == []


def test_rejecting_after_export_removes_the_old_folder(store: ReviewStore, out: Path) -> None:
    item = add_item(store)
    store.approve(item.id)
    first = export_approved(store.path, out)
    assert len(folders(out)) == 1

    store.reject(item.id)
    second = export_approved(store.path, out)

    assert second.exported == ()
    assert folders(out) == []
    assert not first.exported[0].exists()


def test_editing_after_export_removes_the_old_folder(store: ReviewStore, out: Path) -> None:
    item = add_item(store)
    store.approve(item.id)
    export_approved(store.path, out)
    assert len(folders(out)) == 1

    store.update_content(item.id, speakers=SPEAKERS, recap="A new recap.", script=SCRIPT)
    result = export_approved(store.path, out)

    assert result.exported == ()
    assert folders(out) == []


def test_only_approved_items_are_written(store: ReviewStore, out: Path) -> None:
    approved = add_item(store, meeting_id="approved-meeting")
    add_item(store, meeting_id="draft-meeting")
    rejected = add_item(store, meeting_id="rejected-meeting")
    store.approve(approved.id)
    store.approve(rejected.id)
    store.reject(rejected.id)

    result = export_approved(store.path, out)

    assert len(result.exported) == 1
    assert result.exported[0].name.endswith("approved-meeting")
    assert len(folders(out)) == 1
    assert sorted(s.meeting_id for s in result.skipped) == ["draft-meeting", "rejected-meeting"]


def test_meeting_id_cannot_write_outside_the_export_folder(
    store: ReviewStore, tmp_path: Path, out: Path
) -> None:
    item = add_item(store, meeting_id="../../escape")
    store.approve(item.id)

    result = export_approved(store.path, out)

    assert len(result.exported) == 1
    assert result.exported[0].parent == out
    assert not (tmp_path / "escape").exists()
    assert not (out.parent / "escape").exists()


def test_missing_database_is_an_error(tmp_path: Path, out: Path) -> None:
    with pytest.raises(ReviewError, match="database not found"):
        export_approved(tmp_path / "missing.db", out)
    assert not (tmp_path / "missing.db").exists()
