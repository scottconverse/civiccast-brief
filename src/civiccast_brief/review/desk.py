"""The editor desk's calls for the weekly run, and its per-city settings.

The weekly run calls three things:

- ``stage_item(db, city_dir, ...)``: put a new episode or article on the desk for review. When
  ``desk.json`` turns auto-publish on for the item's body and the item passed its checks, it is
  published at once, with no press.
- ``redraft(store, item_id, note, writer)``: the desk's Redraft button. The writer is injected;
  it gets the item and the editor's note and returns new content.
- ``export_published(db, out)`` (in ``review.export``): write every published item, and only
  those, to a folder.

``desk.json`` in the city folder::

    {"format": 1, "auto_publish": {"City Council": false, "Planning and Zoning Commission": true}}

A body that is not listed is not auto-published.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from civiccast_brief.review.store import ContentUpdate, DeskItem, ReviewError, ReviewStore

SETTINGS_NAME: Final = "desk.json"
FORMAT: Final = 1

#: Gets the item and the editor's note; returns the new content. For an episode, any of
#: "recap", "script", "speakers"; for an article, "article". It may add "checks_passed".
Writer = Callable[[DeskItem, str], Mapping[str, Any]]


def load_auto_publish(city_dir: str | Path) -> dict[str, bool]:
    path = Path(city_dir) / SETTINGS_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("auto_publish", {})
        if not isinstance(raw, dict):
            raise TypeError('"auto_publish" is not an object')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ReviewError(f"{path} is not a readable desk settings file: {exc}") from exc
    return {" ".join(str(k).split()): v is True for k, v in raw.items() if str(k).strip()}


def save_auto_publish(city_dir: str | Path, settings: Mapping[str, bool]) -> Path:
    path = Path(city_dir) / SETTINGS_NAME
    data = {
        "format": FORMAT,
        "auto_publish": {k: bool(v) for k, v in sorted(settings.items())},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(tmp, path)
    return path


def stage_item(
    db: str | Path,
    city_dir: str | Path | None,
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
    """Add one item to the desk; auto-publish it if desk.json says so and its checks passed."""
    auto = False
    if city_dir is not None:
        auto = load_auto_publish(city_dir).get(" ".join(body.split()), False)
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
            auto_publish=auto,
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
        {"article", "checks_passed"}
        if item.kind == "article"
        else {
            "recap",
            "script",
            "speakers",
            "checks_passed",
        }
    )
    unknown = set(result) - allowed
    if unknown:
        raise ReviewError(f"the writer returned fields an {item.kind} has not: {sorted(unknown)}")
    checks = result.pop("checks_passed", None)
    update = store.update_content(
        item_id,
        action="redrafted",
        note=note,
        checks_passed=None if checks is None else bool(checks),
        expected_hash=item.content_hash,
        **result,
    )
    if not update.changed:
        raise ReviewError("the writer returned the same text; nothing changed")
    return update
