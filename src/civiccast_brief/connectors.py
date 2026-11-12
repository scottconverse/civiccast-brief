"""City-owned standalone connector state and producer evidence handoff.

Provider requests, identities, bounds and writes belong to the SDK. Brief owns
this mapping to its existing documents, supplied-caption pipeline and review desk.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from civic_data_connectors import Bounds, OperationContext, Query, Source, create_engine
from civic_data_connectors.engine import canonical_hash, source_identity_key
from civic_data_connectors.interchange import (
    cleanup_artifacts,
    open_artifact,
    stage_attachment,
)
from civic_data_connectors.storage import SQLiteStorage

from civiccast_brief.sources import Source as CitySource

CLIENT = "civiccast-brief"


def catalog() -> list[dict]:
    return create_engine().execute("providers", {}).data["providers"]


def scoped_file(city: Path, relative: str) -> Path:
    """Resolve saved captures under this city, including Windows junction checks."""
    path = (city / relative).resolve()
    if not relative or not path.is_relative_to(city.resolve()):
        raise ValueError("The capture must be inside this city folder.")
    return path


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "br", "h1", "h2"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _texts(attachment: dict) -> list[dict]:
    """Use existing PDF extraction; scanned files stay explicitly unreadable."""
    if "pages" in attachment:
        return [
            {"page": p.get("page", n), "text": p.get("text", "")}
            for n, p in enumerate(attachment["pages"], 1)
        ]
    if "text" in attachment:
        return [{"page": 1, "text": attachment["text"]}]
    content = base64.b64decode(attachment.get("content_base64", ""), validate=True)
    media_type = attachment.get("content_type", "").split(";", 1)[0].lower()
    if content.startswith(b"%PDF"):
        from civiccast_brief.weekly.documents import pdf_pages

        return [{"page": n, "text": text} for n, text in enumerate(pdf_pages(content), 1)]
    if media_type in {"text/plain", "text/vtt", "text/html", "application/xhtml+xml"}:
        text = content.decode("utf-8-sig")
        if "html" in media_type:
            parser = _HTMLText()
            parser.feed(text)
            text = "".join(parser.parts)
        return [{"page": 1, "text": text.strip()}]
    return []


class CivicSources:
    """No service startup; SQLite connections are operation-owned and closed by SDK."""

    def __init__(self, city: str | Path, *, engine=None, credentials=None, writes=False):
        self.city = Path(city).resolve()
        self.store = engine.storage if engine else SQLiteStorage(self.city / ".connectors/state.db")
        self.engine = engine or create_engine(storage=self.store)
        self.context = OperationContext(
            principal="producer",
            client_id=CLIENT,
            tenant=hashlib.sha256(str(self.city).casefold().encode()).hexdigest(),
            credentials=dict(credentials or {}),
        )
        self.writes = writes

    def close(self) -> None:
        cleanup_artifacts(self.engine)

    @staticmethod
    def source(config: CitySource, body: str | None = None) -> Source:
        return Source(
            config.provider,
            config.url,
            namespace=config.namespace or None,
            body=body,
            input_format=config.input_format or None,
            options=config.options,
        )

    def normalize(self, provider: str, url: str, content: bytes, input_format: str):
        return self.engine.normalize(
            Source(provider, url), content, input_format=input_format, context=self.context
        )

    def discover(self, config: CitySource, first: date, last: date):
        content = None
        if config.capture:
            with scoped_file(self.city, config.capture).open("rb") as stream:
                content = stream.read(Bounds().parser_bytes + 1)
        result = self.engine.execute(
            "discover",
            {
                "source": self.source(config).public_dict(),
                "bounds": asdict(Bounds(date_from=first.isoformat(), date_to=last.isoformat())),
            },
            content=content,
            context=self.context,
        )
        return self._capture_result(config, result)

    def fetch_account(self, config: CitySource, fields: dict):
        if not isinstance(fields, dict) or set(fields) - {
            "api_resource",
            "external_id",
            "offset",
            "filters",
        }:
            raise ValueError(
                "Use only documented account fetch fields; credentials belong in operator configuration."
            )
        result = self.engine.execute(
            "fetch", {"source": self.source(config).public_dict(), **fields}, context=self.context
        )
        return self._capture_result(config, result)

    def _capture_result(self, config: CitySource, result):
        saved = self.engine.execute(
            "import_bundle",
            {"result": result.public_dict(), "persist": True},
            context=self.context,
        )
        self.store.put(
            "brief_discovery",
            config.id,
            {"result": result.public_dict(), "import": saved.public_dict()},
            scope=self.context.scope,
        )
        if result.data and saved.status in {"failed", "cancelled", "unsupported"}:
            return saved
        return result

    def found(self, config: CitySource, first: date, last: date):
        from civiccast_brief.weekly.find import Found, match_body, youtube_id

        result = self.discover(config, first, last)
        problems = []
        if result.status != "completed":
            problems.append(
                f"{config.id}: {result.status}; "
                + "; ".join(e.message for e in result.errors)
                + f" coverage={json.dumps(result.coverage, ensure_ascii=False)}"
            )
        found = []
        for record in (result.data or {}).get("records", []):
            normalized = record["normalized"]
            title = str(normalized.get("title") or "")
            body = match_body(title, config.bodies)
            if not body:
                continue
            day = (
                normalized.get("meeting_date") or str(normalized.get("scheduled_start") or "")[:10]
            )
            if record["identity"]["record_type"] not in {"meeting", "event"} or not day:
                problems.append(f"{config.id}: recording/undated evidence retained, not a meeting")
                continue
            key = source_identity_key(record["identity"])
            videos = normalized.get("recordings") or []
            video = next((youtube_id(v.get("url")) for v in videos if youtube_id(v.get("url"))), "")
            found.append(
                Found(
                    body,
                    date.fromisoformat(day),
                    title,
                    config.id,
                    video,
                    connector_key=key,
                    connector_revision=record["normalized_sha256"],
                    connector_capture=record["capture_sha256"],
                    connector_prepare=lambda folder, r=record: self.prepare(config, r, folder),
                )
            )
        return found, problems

    def prepare(self, config: CitySource, record: dict, folder: Path) -> dict:
        """Download only SDK-declared attachments; keep independent failures retryable."""
        from civiccast_brief.meeting.granicus import parse_vtt

        folder.mkdir(parents=True, exist_ok=True)
        key = source_identity_key(record["identity"])
        record = deepcopy(record)
        documents, segments, media, limitations, failures, attachment_ids = [], [], [], [], [], []
        text_segments = []
        source = self.source(config)
        for kind in ("documents", "recordings"):
            for index, declared in enumerate(record["normalized"].get(kind) or []):
                result = self.engine.fetch_attachment(
                    source, record, index=index, kind=kind, context=self.context
                )
                attachment = (result.data or {}).get("attachment")
                if not attachment:
                    failures.append({"kind": kind, "index": index, "result": result.public_dict()})
                    continue
                raw = base64.b64decode(attachment.get("content_base64", ""), validate=True)
                identifier = canonical_hash([key, kind, index, attachment["sha256"]])
                if (
                    raw
                    and self.store.get("attachments", identifier, scope=self.context.scope) is None
                ):
                    stage_attachment(
                        self.engine,
                        identifier,
                        io.BytesIO(raw),
                        filename=f"{kind}-{index}.bin",
                        citation_url=attachment["citation_url"],
                        namespace=record["identity"]["namespace"],
                        context=self.context,
                    )
                if raw:
                    attachment_ids.append(identifier)
                try:
                    pages = _texts(attachment)
                except (ValueError, OSError, UnicodeError) as exc:
                    pages = []
                    limitations.append(
                        f"{kind} {index}: text could not be read ({type(exc).__name__})"
                    )
                entry = {
                    "kind": str(declared.get("kind") or "document").lower(),
                    "url": attachment["citation_url"],
                    "sha256": attachment["sha256"],
                    "pages": pages,
                    "text_availability": "available"
                    if any(p["text"].strip() for p in pages)
                    else "unavailable (no readable supplied text; no OCR/ASR performed)",
                }
                documents.append(entry)
                text_segments.extend(
                    {
                        "field": f"documents.{index}.text",
                        "text": p["text"],
                        "citation_url": entry["url"],
                        "page": p["page"],
                        "provenance": {
                            "method": "brief_supplied_text_extraction",
                            "attachment_sha256": entry["sha256"],
                            "capture_sha256": record["capture_sha256"],
                        },
                        "completeness": "complete",
                    }
                    for p in pages
                    if p["text"].strip()
                )
                if not any(p["text"].strip() for p in pages):
                    limitations.append(f"{kind} {index}: {entry['text_availability']}")
                if raw.lstrip().startswith(b"WEBVTT"):
                    segments.extend(parse_vtt(raw.decode("utf-8-sig")))
                if kind == "recordings":
                    media.append(
                        {
                            **declared,
                            "sha256": attachment["sha256"],
                            "content_type": attachment.get("content_type"),
                            "available": True,
                        }
                    )
        if not segments:
            limitations.append(
                "No usable supplied timed captions; transcription/recap not completed."
            )
        if not record["normalized"].get("recordings"):
            limitations.append("No recording supplied by this source.")
        record["text"] = [
            s
            for s in record.get("text", [])
            if (s.get("provenance") or {}).get("method") != "brief_supplied_text_extraction"
        ] + text_segments
        stored = self.store.get("records", key, scope=self.context.scope)
        self.store.put(
            "records",
            key,
            record,
            scope=self.context.scope,
            expected_version=stored.version if stored else None,
            create_only=stored is None,
        )
        data = {
            "format": 1,
            "record": record,
            "documents": documents,
            "media": media,
            "limitations": limitations,
            "failures": failures,
            "attachment_ids": attachment_ids,
            "status": "partial" if failures else "completed",
        }
        (folder / "documents.json").write_text(
            json.dumps({"format": 1, "documents": documents}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if segments:
            capture = {
                "format": 1,
                "video_id": f"connector-{key}",
                "title": record["normalized"]["title"],
                "meeting_date": record["normalized"].get("meeting_date")
                or record["normalized"]["scheduled_start"][:10],
                "items": segments,
                "source": {
                    "platform": config.provider,
                    "video_url": record["source_url"],
                    "capture_sha256": record["capture_sha256"],
                    "caption_kind": "provider_supplied_captions",
                    "record_identity": record["identity"],
                },
            }
            path = folder / "supplied-capture.json"
            path.write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")
            data["supplied_capture"] = str(path)
        (folder / "connector-evidence.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.store.put("brief_evidence", key, data, scope=self.context.scope)
        return data

    def stage_evidence(self, db: Path, evidence: dict, body: str) -> int:
        """Source excerpts are not generated reporting and never auto-publish."""
        from civiccast_brief.review.store import ReviewStore

        record = evidence["record"]
        key = source_identity_key(record["identity"])
        meeting_id = f"connector:{key}:evidence"
        paragraphs = [
            {"text": p["text"], "cites": [f"{d['url']}#page={p['page']}"]}
            for d in evidence["documents"]
            for p in d["pages"]
            if p["text"].strip()
        ]
        if not paragraphs:
            paragraphs = [
                {
                    "text": "Source metadata only: " + record["normalized"]["title"],
                    "cites": record["citation_urls"] or [record["source_url"]],
                }
            ]
        article = {
            "headline": "Source evidence: " + record["normalized"]["title"],
            "dek": "Supplied source excerpts, not a written recap. Producer review required.",
            "sections": [{"heading": "Source excerpts", "paragraphs": paragraphs}],
            "source_identity": record["identity"],
            "capture_sha256": record["capture_sha256"],
            "limitations": evidence["limitations"],
            "failures": evidence["failures"],
        }
        with ReviewStore(db) as store:
            managed = True
            old = next(
                (
                    i
                    for i in store.list_items()
                    if i.kind == "article" and i.meeting_id == meeting_id
                ),
                None,
            )
            if old:
                previous = self.store.get("brief_review", key, scope=self.context.scope)
                # Never overwrite producer edits; changed source remains in evidence.
                if previous and old.content_hash == previous.value.get("content_hash"):
                    store.update_content(
                        old.id,
                        article=article,
                        checks_passed=False,
                        checks_note="Source evidence only; no writing/transcription success claimed.",
                        expected_hash=old.content_hash,
                    )
                else:
                    managed = False
                item = store.get_item(old.id)
            else:
                item = store.add_item(
                    kind="article",
                    meeting_id=meeting_id,
                    title=article["headline"],
                    meeting_date=record["normalized"].get("meeting_date")
                    or record["normalized"]["scheduled_start"][:10],
                    body=body,
                    article=article,
                    checks_passed=False,
                    checks_note="Source evidence only; no writing/transcription success claimed.",
                )
            if managed:
                self.store.put(
                    "brief_review",
                    key,
                    {"item_id": item.id, "content_hash": item.content_hash},
                    scope=self.context.scope,
                )
            return item.id

    def search(self, phrase: str):
        return self.engine.search(Query(phrases=(phrase,)), context=self.context)

    def export(self, *, format="zip", include_attachments=False):
        observations = self.store.list("brief_discovery", scope=self.context.scope)
        evidence = self.store.list("brief_evidence", scope=self.context.scope)
        records = [row.value for row in self.store.list("records", scope=self.context.scope)]
        # Export the enriched consumer evidence alongside original shared records.
        bundle = {
            "schema_version": "2.0.0",
            "records": records,
            "capture": {},
            "extensions": {
                "brief_evidence": [row.value for row in evidence],
                "discovery": [row.value for row in observations],
            },
        }
        failures = [
            r.value["result"] for r in observations if r.value["result"]["status"] != "completed"
        ]
        identifiers = (
            list(dict.fromkeys(i for row in evidence for i in row.value["attachment_ids"]))
            if include_attachments
            else []
        )
        context = replace(self.context, authorization=frozenset({"export_attachments"}))
        result = self.engine.execute(
            "export",
            {
                "bundle": bundle,
                "format": format,
                "delivery": "storage",
                "attachment_ids": identifiers,
                "coverage": {"failures": failures},
            },
            context=context,
        )
        if not result.data or "artifact_id" not in result.data:
            raise ValueError("; ".join(e.message for e in result.errors) or result.status)
        metadata, stream = open_artifact(self.engine, result.data["artifact_id"], context=context)
        return result, metadata, stream

    def plan(self, config: CitySource, changes: list[dict]):
        if not self.writes:
            raise PermissionError("Provider writes are not explicitly configured.")
        context = replace(self.context, authorization=frozenset({"write:plan"}))
        return self.engine.plan_write(self.source(config), {"changes": changes}, context=context)

    def commit(self, plan_id: str, plan_hash: str, *, approved: bool):
        if not self.writes or not approved:
            raise PermissionError("The producer must approve this exact provider plan.")
        context = replace(self.context, authorization=frozenset({"write:commit"}))
        return self.engine.commit_write(
            {"plan_id": plan_id, "plan_hash": plan_hash}, context=context
        )

    def receipt(self, plan_id: str):
        if not self.writes:
            raise PermissionError("Provider writes are not explicitly configured.")
        context = replace(self.context, authorization=frozenset({"write:receipt"}))
        return self.engine.receipt({"plan_id": plan_id}, context=context)


def main(argv=None) -> int:
    """Local source operations, separate from the episode/publication CLI."""
    import argparse
    import os
    import shutil
    import sys

    from civiccast_brief.sources import load_sources
    from civiccast_brief.weekly.run import Settings, run_week

    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.connectors")
    parser.add_argument(
        "operation", choices=("discover", "search", "export", "plan", "commit", "receipt")
    )
    parser.add_argument("--city", type=Path, required=True)
    parser.add_argument("--db", type=Path, help="producer review database for discover")
    parser.add_argument("--source", help="configured standalone source ID")
    parser.add_argument("--first", type=date.fromisoformat)
    parser.add_argument("--last", type=date.fromisoformat)
    parser.add_argument("--phrase", default="")
    parser.add_argument(
        "--format",
        default="zip",
        choices=("zip", "json", "jsonl", "csv-meetings", "csv-items", "ics"),
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--include-attachments", action="store_true")
    parser.add_argument("--enable-writes", action="store_true")
    parser.add_argument(
        "--credentials-env", help="environment variable name, never a credential value"
    )
    parser.add_argument("--changes", help="city-scoped relative JSON changes file")
    parser.add_argument("--plan-id")
    parser.add_argument("--plan-hash")
    parser.add_argument("--approve-plan-hash")
    args = parser.parse_args(argv)
    client = None
    try:
        credentials = (
            json.loads(os.environ.get(args.credentials_env, "{}")) if args.credentials_env else {}
        )
        if not isinstance(credentials, dict):
            raise TypeError("Invalid private connector credential configuration.")
        client = CivicSources(args.city, credentials=credentials, writes=args.enable_writes)
        if args.operation == "export":
            if args.out is None:
                raise ValueError("Export needs --out; existing files are never overwritten.")
            result, _, stream = client.export(
                format=args.format, include_attachments=args.include_attachments
            )
            with stream, args.out.open("xb") as output:
                shutil.copyfileobj(stream, output)
        elif args.operation == "search":
            result = client.search(args.phrase)
        elif args.operation == "receipt":
            result = client.receipt(args.plan_id)
        elif args.operation == "commit":
            result = client.commit(
                args.plan_id,
                args.plan_hash,
                approved=bool(args.plan_hash) and args.approve_plan_hash == args.plan_hash,
            )
        else:
            sources = [
                s for s in load_sources(args.city) if s.kind == "connector" and s.id == args.source
            ]
            if not sources:
                raise ValueError("Select a configured source with --source.")
            if args.operation == "plan":
                if not args.changes:
                    raise ValueError("Planning needs a city-scoped --changes file.")
                changes = json.loads(
                    scoped_file(args.city, args.changes).read_text(encoding="utf-8")
                )
                result = client.plan(sources[0], changes)
            else:
                if not args.first or not args.last or not args.db:
                    raise ValueError("Discovery needs --first, --last and --db.")
                meetings, problems = client.found(sources[0], args.first, args.last)
                report = run_week(
                    Settings(args.city, args.city / ".connectors/runs", args.db),
                    meetings,
                    args.last,
                )
                print(json.dumps({"source_problems": problems, "workflow": report.lines}))
                return 1 if problems or report.failed else 0
        print(json.dumps(result.public_dict(), ensure_ascii=False))
        return 0 if result.status == "completed" else 1
    except (TypeError, ValueError, OSError, PermissionError) as exc:
        print(f"connector operation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
