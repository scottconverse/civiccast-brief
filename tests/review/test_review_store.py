"""The review store: status transitions, the content hash, and the reset after an edit."""

from __future__ import annotations

from pathlib import Path

import pytest

from civiccast_brief.review.store import (
    ReviewError,
    ReviewStore,
    compute_content_hash,
    normalize_speakers,
)

SPEAKERS = {"SPEAKER_00": "Mayor Smith", "SPEAKER_01": "unknown"}
RECAP = "The council voted five to two."
SCRIPT = "[S1] Five to two.\n[S2] Right."


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    return ReviewStore(tmp_path / "review.db")


def add(store: ReviewStore, *, meeting_id: str = "m-1", recap: str = RECAP):
    return store.add_item(
        meeting_id=meeting_id,
        title="Council, Nov 4",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap=recap,
        script=SCRIPT,
    )


def test_new_item_is_a_draft_with_no_approval(store: ReviewStore) -> None:
    item = add(store)

    assert item.status == "draft"
    assert item.approved_at is None
    assert item.approved_hash is None
    assert store.get_item(item.id) == item


def test_meeting_id_is_unique(store: ReviewStore) -> None:
    add(store)
    with pytest.raises(ReviewError, match="already exists"):
        add(store)


def test_blank_speaker_name_becomes_unknown_and_labels_are_trimmed() -> None:
    assert normalize_speakers({" SPEAKER_00 ": "  Mayor   Smith ", "SPEAKER_01": "  "}) == {
        "SPEAKER_00": "Mayor Smith",
        "SPEAKER_01": "unknown",
    }
    with pytest.raises(ReviewError, match="cannot be blank"):
        normalize_speakers({"  ": "x"})


def test_content_hash_ignores_key_order_and_changes_with_content() -> None:
    base = {
        "meeting_id": "m",
        "title": "t",
        "meeting_date": "2026-11-04",
        "recap": RECAP,
        "script": SCRIPT,
    }
    one = compute_content_hash(speakers={"A": "x", "B": "y"}, **base)
    two = compute_content_hash(speakers={"B": "y", "A": "x"}, **base)
    three = compute_content_hash(speakers={"A": "x", "B": "z"}, **base)

    assert one == two
    assert one != three


def test_crlf_from_a_browser_hashes_the_same_as_lf(store: ReviewStore) -> None:
    item = add(store, recap="line one\nline two")
    update = store.update_content(
        item.id, speakers=SPEAKERS, recap="line one\r\nline two", script=SCRIPT
    )

    assert update.changed is False
    assert store.get_item(item.id).recap == "line one\nline two"


def test_approve_then_approve_again_is_a_no_op(store: ReviewStore) -> None:
    item = add(store)

    assert store.approve(item.id) is True
    approved = store.get_item(item.id)
    assert approved.status == "approved"
    assert approved.approved_hash == item.content_hash
    assert approved.approved_at is not None

    assert store.approve(item.id) is False
    assert store.get_item(item.id) == approved


def test_reject_clears_any_approval(store: ReviewStore) -> None:
    item = add(store)
    store.approve(item.id)

    store.reject(item.id)

    rejected = store.get_item(item.id)
    assert rejected.status == "rejected"
    assert rejected.approved_at is None
    assert rejected.approved_hash is None


def test_rejected_item_can_be_approved_again(store: ReviewStore) -> None:
    item = add(store)
    store.reject(item.id)

    assert store.approve(item.id) is True
    assert store.get_item(item.id).status == "approved"


def test_saving_identical_content_keeps_the_approval(store: ReviewStore) -> None:
    item = add(store)
    store.approve(item.id)

    update = store.update_content(item.id, speakers=SPEAKERS, recap=RECAP, script=SCRIPT)

    assert update.changed is False
    assert update.previous_status == "approved"
    assert update.status == "approved"
    assert store.get_item(item.id).status == "approved"


def test_editing_the_recap_after_approval_resets_to_draft(store: ReviewStore) -> None:
    item = add(store)
    store.approve(item.id)

    update = store.update_content(item.id, speakers=SPEAKERS, recap="Changed.", script=SCRIPT)

    assert update.changed is True
    assert update.previous_status == "approved"
    after = store.get_item(item.id)
    assert after.status == "draft"
    assert after.approved_at is None
    assert after.approved_hash is None
    assert after.recap == "Changed."


def test_editing_the_script_after_approval_resets_to_draft(store: ReviewStore) -> None:
    item = add(store)
    store.approve(item.id)

    store.update_content(item.id, speakers=SPEAKERS, recap=RECAP, script=SCRIPT + "\n[S1] Ok.")

    assert store.get_item(item.id).status == "draft"


def test_editing_a_speaker_name_after_approval_resets_to_draft(store: ReviewStore) -> None:
    item = add(store)
    store.approve(item.id)

    store.update_content(
        item.id,
        speakers={"SPEAKER_00": "Mayor Jones", "SPEAKER_01": "unknown"},
        recap=RECAP,
        script=SCRIPT,
    )

    assert store.get_item(item.id).status == "draft"


def test_editing_a_rejected_item_moves_it_back_to_draft(store: ReviewStore) -> None:
    item = add(store)
    store.reject(item.id)

    update = store.update_content(item.id, speakers=SPEAKERS, recap="Reworked.", script=SCRIPT)

    assert update.previous_status == "rejected"
    assert store.get_item(item.id).status == "draft"


def test_audio_request_records_the_current_content(store: ReviewStore) -> None:
    item = add(store)

    first = store.record_audio_request(item.id)
    store.update_content(item.id, speakers=SPEAKERS, recap="Changed.", script=SCRIPT)
    second = store.record_audio_request(item.id)

    assert first.content_hash == item.content_hash
    assert second.content_hash != first.content_hash
    listed = store.list_audio_requests(item.id)
    assert [r.id for r in listed] == [second.id, first.id]


def test_unknown_item_id_is_refused(store: ReviewStore) -> None:
    assert store.get_item(999) is None
    with pytest.raises(ReviewError, match="no item"):
        store.approve(999)
    with pytest.raises(ReviewError, match="no item"):
        store.reject(999)
    with pytest.raises(ReviewError, match="no item"):
        store.record_audio_request(999)
    with pytest.raises(ReviewError, match="no item"):
        store.update_content(999, speakers={}, recap="", script="")
