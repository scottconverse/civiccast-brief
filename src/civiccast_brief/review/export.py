"""Export published desk items to a folder. This is the gate: nothing else is written.

An item is written only when all three hold:
  - its status is "published";
  - it has a publish record (published_at and published_hash);
  - the hash of its current content equals the hash recorded when it was published.

Every other item is skipped, and the reason is logged and returned. A skipped item never
reaches the folder: if an item was exported earlier and is now held, killed or edited, its
folder from the earlier export is removed.

The output folder belongs to the export. It holds one folder per exported item and a
manifest.json that lists them. An episode folder has recap.txt, script.txt and item.json; an
article folder has article.json and item.json. Use a folder for this export only.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from civiccast_brief.review.store import ReviewError, ReviewItem, ReviewStore

logger = logging.getLogger(__name__)

MANIFEST_NAME: Final = "manifest.json"
MANIFEST_FORMAT: Final = 2
_FOLDER_PATTERN: Final = re.compile(r"^\d+-[A-Za-z0-9-]*$")


@dataclass(frozen=True)
class SkippedItem:
    item_id: int
    meeting_id: str
    reason: str


@dataclass(frozen=True)
class ExportResult:
    exported: tuple[Path, ...]
    skipped: tuple[SkippedItem, ...]


def gate_reason(item: ReviewItem) -> str | None:
    """Return why the item may not be exported, or None if it may."""
    if item.status != "published":
        return f"status is {item.status}; only published items are exported"
    if item.published_at is None or item.published_hash is None:
        return "publish record is missing; publish the item again"
    if item.published_hash != item.content_hash:
        return "content changed after publishing; publish the item again"
    return None


def folder_name(item: ReviewItem) -> str:
    """Folder for one item: the id plus a slug of the meeting id. Only letters, digits, hyphens."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", item.meeting_id).strip("-")[:60] or "meeting"
    return f"{item.id:04d}-{slug}"


def export_published(db: str | Path, out_dir: str | Path) -> ExportResult:
    """Write every published item that passes the gate into out_dir; skip the rest."""
    db_path = Path(db)
    if not db_path.is_file():
        raise ReviewError(f"database not found: {db_path}")
    out = Path(out_dir)

    with ReviewStore(db_path) as store:
        items = store.list_items()

    skipped: list[SkippedItem] = []
    ready: dict[str, ReviewItem] = {}
    for item in items:
        reason = gate_reason(item)
        if reason is not None:
            skipped.append(SkippedItem(item.id, item.meeting_id, reason))
            logger.info("export skipped item %d (%s): %s", item.id, item.meeting_id, reason)
            continue
        ready[folder_name(item)] = item

    out.mkdir(parents=True, exist_ok=True)
    for stale in _previous_folders(out):
        if stale not in ready:
            _remove_folder(out, stale)
    exported = tuple(_write_item(out, name, item) for name, item in ready.items())
    _write_manifest(out, ready)
    return ExportResult(exported=exported, skipped=tuple(skipped))


def _previous_folders(out: Path) -> list[str]:
    manifest = out / MANIFEST_NAME
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        names = [entry["folder"] for entry in data["items"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Without the manifest the stale folders cannot be found, so refuse rather than guess.
        raise ReviewError(
            f"export manifest is unreadable ({manifest}); move the folder aside and export again"
        ) from exc
    return [name for name in names if isinstance(name, str) and _FOLDER_PATTERN.match(name)]


def _remove_folder(out: Path, name: str) -> None:
    path = out / name
    if path.is_dir() and path.resolve().parent == out.resolve():
        shutil.rmtree(path)


def _write_item(out: Path, name: str, item: ReviewItem) -> Path:
    folder = out / name
    folder.mkdir(exist_ok=True)
    meta: dict[str, object] = {
        "status": item.status,
        "kind": item.kind,
        "meeting_id": item.meeting_id,
        "body": item.body,
        "title": item.title,
        "meeting_date": item.meeting_date,
    }
    if item.kind == "article":
        _write_json(folder / "article.json", item.article)
    else:
        (folder / "recap.txt").write_text(item.recap, encoding="utf-8", newline="\n")
        (folder / "script.txt").write_text(item.script, encoding="utf-8", newline="\n")
        meta["speakers"] = item.speakers
    meta |= {
        "published_at": item.published_at,
        "published_by": item.published_by,
        "content_hash": item.content_hash,
    }
    _write_json(folder / "item.json", meta)
    return folder


def _write_json(path: Path, data: object) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_manifest(out: Path, ready: dict[str, ReviewItem]) -> None:
    data = {
        "format": MANIFEST_FORMAT,
        "items": [
            {
                "folder": name,
                "item_id": item.id,
                "kind": item.kind,
                "meeting_id": item.meeting_id,
                "published_at": item.published_at,
                "content_hash": item.content_hash,
            }
            for name, item in ready.items()
        ],
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


#: The earlier name, kept for code that imports it.
export_approved = export_published
