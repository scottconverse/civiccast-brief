"""The editor desk's calls for the weekly run, and its per-city settings.

The weekly run calls three things:

- ``stage_item(db, ...)``: put a new episode or article on the desk. Every item that passed its
  checks is published at once, for every body, with no press. An item that failed its checks is
  held in review and never published by itself.
- ``redraft(store, item_id, note, writer)``: the desk's Redraft button. The writer is injected;
  it gets the item and the editor's note and returns new content.
- ``export_published(db, out)`` (in ``review.export``): write every published item, and only
  those, to a folder.

"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from civiccast_brief.review.store import ContentUpdate, DeskItem, ReviewError, ReviewStore

#: Gets the item and the editor's note; returns the new content. For an episode, any of
#: "recap", "script", "speakers"; for an article, "article". It may add "checks_passed".
Writer = Callable[[DeskItem, str], Mapping[str, Any]]


def stage_item(
    db: str | Path,
    *,
    kind: str,
    meeting_id: str,
    title: str,
    meeting_date: str,
    body: str,
    checks_passed: bool,
    checks_note: str = "",
    speakers: Mapping[str, str] | None = None,
    recap: str = "",
    script: str = "",
    article: Mapping[str, Any] | None = None,
) -> DeskItem:
    """Add one item to the desk and publish it at once when its checks passed."""
    with ReviewStore(db) as store:
        return store.add_item(
            kind=kind,
            meeting_id=meeting_id,
            title=title,
            meeting_date=meeting_date,
            body=body,
            speakers=speakers,
            recap=recap,
            script=script,
            article=article,
            checks_passed=checks_passed,
            checks_note=checks_note,
            auto_publish=True,
        )


def redraft(store: ReviewStore, item_id: int, note: str, writer: Writer) -> ContentUpdate:
    """Ask the writer for a new draft with the editor's note, and put it back in review."""
    note = str(note).strip()
    if not note:
        raise ReviewError("write a note for the writer: what should change?")
    item = store.get_item(item_id)
    if item is None:
        raise ReviewError(f"no item with id {item_id}")
    result = dict(writer(item, note))
    allowed = (
        {"article", "checks_passed", "checks_note"}
        if item.kind == "article"
        else {
            "recap",
            "script",
            "speakers",
            "checks_passed",
            "checks_note",
        }
    )
    unknown = set(result) - allowed
    if unknown:
        raise ReviewError(f"the writer returned fields an {item.kind} has not: {sorted(unknown)}")
    checks = result.pop("checks_passed", None)
    checks_note = str(result.pop("checks_note", "") or "")
    update = store.update_content(
        item_id,
        action="redrafted",
        note=note,
        checks_passed=None if checks is None else bool(checks),
        checks_note=checks_note,
        expected_hash=item.content_hash,
        **result,
    )
    if not update.changed:
        raise ReviewError("the writer returned the same text; nothing changed")
    return update
