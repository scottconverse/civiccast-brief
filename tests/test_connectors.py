"""Actual SDK/provider/storage/weekly/desk integration over synthetic HTTP data."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from civic_data_connectors import create_engine
from civic_data_connectors.engine import canonical_hash
from civic_data_connectors.interchange import read_bundle
from civic_data_connectors.providers import Response
from civic_data_connectors.storage import SQLiteStorage
from fastapi.testclient import TestClient

from civiccast_brief.connectors import CivicSources, catalog
from civiccast_brief.episode.build import stage
from civiccast_brief.meeting import pipeline
from civiccast_brief.meeting.roster import ROSTERS_DIR
from civiccast_brief.review.export import export_published
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.sources import SourceError, load_sources, make_source, save_sources
from civiccast_brief.weekly.find import Listers, find_meetings
from civiccast_brief.weekly.run import Settings, State, run_week

FIRST = LAST = date(2026, 11, 11)
BASE = "https://pub-synthetic.escribemeetings.com"
CORPUS = Path(__file__).parent / "fixtures/connector-baseline"
CASES = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))


class SyntheticProvider:
    def __init__(self, *, captions=False):
        self.calls = []
        self.fail = False
        self.captioned = captions
        self.title = "City Council"

    def request(self, url, **options):
        self.calls.append((url, options))
        assert url.startswith(BASE + "/")
        if options.get("method") == "POST":
            assert json.loads(options["body"]) == {
                "calendarStartDate": "2026-11-11",
                "calendarEndDate": "2026-11-12",
            }
            links = [{"Type": "Agenda", "Format": "HTML", "Url": "/agenda.html"}]
            if self.captioned:
                # Saved response attachment, not an invented eScribe caption endpoint.
                links.append(
                    {"Type": "Document", "Title": "Supplied captions", "Url": "/captions.vtt"}
                )
            return Response(
                url,
                json.dumps(
                    {
                        "d": [
                            {
                                "ID": "meeting-42",
                                "MeetingName": self.title,
                                "StartDate": "2026/11/11 19:00:00",
                                "Url": "/Meeting.aspx?Id=meeting-42",
                                "MeetingDocumentLink": links,
                            }
                        ]
                    }
                ).encode(),
                "application/json",
            )
        if self.fail:
            raise ValueError("synthetic attachment unavailable")
        if url.endswith("/agenda.html"):
            return Response(
                url,
                b"<h1>City Council</h1><p>Harbor sidewalk allocation.</p>"
                b"<script>never index this script</script>",
                "text/html",
            )
        if url.endswith("/captions.vtt"):
            return Response(
                url,
                b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n"
                b">> The Harbor sidewalk allocation is on the agenda.\n\n",
                "text/vtt",
            )
        raise AssertionError(url)


def setup(city, *, captions=False):
    city.mkdir(exist_ok=True)
    network = SyntheticProvider(captions=captions)
    engine = create_engine(
        storage=SQLiteStorage(city / ".connectors/state.db"),
        provider_transport=network,
        write_adapters={},
    )
    client = CivicSources(city, engine=engine)
    config = make_source(
        "connector", {"provider": "escribe", "url": BASE, "bodies": "City Council"}
    )
    save_sources(city, [config])
    return client, config, network


def test_namespace_options_roundtrip_and_account_fields(tmp_path, monkeypatch):
    client, _, _ = setup(tmp_path)
    source = make_source(
        "connector",
        {
            "provider": "escribe",
            "url": BASE,
            "bodies": "City Council",
            "namespace": "//pub-synthetic.escribemeetings.com",
            "options": {"download_origins": [BASE]},
        },
    )
    old_id = source.id
    save_sources(tmp_path, [source])
    restored = load_sources(tmp_path)[0]
    assert restored.id == old_id and restored.namespace == source.namespace
    assert client.source(restored).options == source.options
    seen = []
    from civic_data_connectors.models import OperationResult

    monkeypatch.setattr(
        client.engine,
        "execute",
        lambda operation, request, **kwargs: (
            seen.append((operation, request)) or OperationResult("unsupported")
        ),
    )
    client.fetch_account(restored, {"api_resource": "meeting", "external_id": "42"})
    assert seen[0][0] == "fetch" and seen[0][1]["source"]["namespace"] == source.namespace
    assert seen[0][1]["external_id"] == "42"
    with pytest.raises(ValueError):
        client.fetch_account(restored, {"credentials": {"token": "not-allowed"}})


def test_all_31_source_choices_and_all_34_format_options(tmp_path):
    providers = catalog()
    assert len(providers) == 31
    assert sum(len(p["formats"]) for p in providers) == 34
    with ReviewStore(tmp_path / "review.db") as store:
        app = make_desk_app(store, city_dir=tmp_path)
        client = TestClient(app, base_url="http://127.0.0.1")
        page = client.get("/sources")
        assert page.status_code == 200
        for provider in providers:
            assert f'value="{provider["provider"]}"' in page.text
            for input_format in provider["additional_formats"]:
                assert f'value="{input_format}"' in page.text
        assert 'name="capture"' in page.text
    with pytest.raises(SourceError, match="documented saved format"):
        make_source(
            "connector",
            {
                "provider": "wistia",
                "url": "https://api.wistia.com/modern/medias",
                "input_format": "legistar-events-json",
            },
        )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["connector"] + "/" + c["input_format"])
def test_all_34_formats_through_brief_adapter(tmp_path, case):
    client = CivicSources(tmp_path)
    capture = (CORPUS / case["file"]).read_bytes()
    assert hashlib.sha256(capture).hexdigest() == case["sha256"]
    result = client.normalize(case["connector"], case["source_url"], capture, case["input_format"])
    # The calendar fixture exceeds the default 1,000-record rich-result bound.
    # Its full legacy projection is still checked below; truncation must remain visible.
    if case["connector"] == "first-party":
        assert result.status == "partial"
        assert result.coverage["truncated"] is True
        assert result.coverage["input_records"] > result.coverage["returned_records"]
    else:
        assert result.status == "completed", result.public_dict()
    assert canonical_hash(result.data["compatibility"]) == case["original_output_sha256"]
    assert base64.b64decode(result.data["capture"]["content_base64"]) == capture


def test_native_discovery_real_weekly_review_search_export_and_reopen(tmp_path):
    city = tmp_path / "city"
    client, config, network = setup(city)
    db = city / "review.db"
    with ReviewStore(db) as store:
        app = make_desk_app(store, city_dir=city, connector_factory=lambda path: client)
        desk = TestClient(app, base_url="http://127.0.0.1")
        assert desk.post("/connectors/discover", data={}).status_code == 403
        response = desk.post(
            "/connectors/discover",
            data={
                "token": app.state.desk.token,
                "source_id": config.id,
                "first": FIRST.isoformat(),
                "last": LAST.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        assert "evidence saved for review" in response.text
        item = store.list_items()[0]
        assert item.status == "review" and item.checks_passed is False
        assert item.article["sections"][0]["paragraphs"][0]["cites"] == [
            BASE + "/agenda.html#page=1"
        ]
        assert "No usable supplied timed captions" in json.dumps(item.article)
        assert "never index this script" not in json.dumps(item.article)
        exported = desk.post(
            "/connectors/export",
            data={"token": app.state.desk.token, "format": "zip", "include_attachments": "yes"},
        )
        assert exported.status_code == 200
        bundle = read_bundle(exported.content, format="zip")
        assert bundle["records"][0]["identity"]["external_id"] == "meeting-42"
        assert bundle["extensions"]["brief_evidence"][0]["attachment_ids"]
        assert not export_published(db, city / "published").exported
        assert all(options.get("method", "GET") in {"GET", "POST"} for _, options in network.calls)
        assert not client.store.list("write_plans", scope=client.context.scope)
    reopened = CivicSources(city)
    assert "Harbor sidewalk allocation" in json.dumps(
        reopened.search("Harbor sidewalk allocation").public_dict()
    )
    assert reopened.store.list("brief_evidence", scope=reopened.context.scope)
    with ReviewStore(db) as store:
        assert len(store.list_items()) == 1
        assert store.list_items()[0].status == "review"


def test_failure_retry_saved_limited_capture_and_no_duplicate_review(tmp_path):
    client, config, network = setup(tmp_path)
    settings = Settings(tmp_path, tmp_path / "work", tmp_path / "review.db")
    listers = Listers(connector=client.found)
    network.fail = True
    meetings, errors = find_meetings([config], FIRST, LAST, listers)
    assert not errors and len(meetings) == 1
    failed = run_week(settings, meetings, LAST)
    assert failed.failed == 1
    state = State(settings.work / "weekly-state.json")
    assert state.meeting(meetings[0].key)["status"] == "failed"
    assert "recap" not in state.meeting(meetings[0].key)["done"]
    network.fail = False
    meetings, _ = find_meetings([config], FIRST, LAST, listers)
    assert run_week(settings, meetings, LAST).failed == 0
    assert run_week(settings, meetings, LAST).failed == 0
    with ReviewStore(settings.db) as store:
        assert len(store.list_items()) == 1
    assert not list(settings.work.rglob("transcript.json"))
    (tmp_path / "saved.ics").write_text(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:limited\nSUMMARY:City Council\n"
        "DTSTART:20261111T190000\nEND:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    saved = make_source(
        "connector",
        {
            "provider": "first-party",
            "url": "https://synthetic.example/calendar.ics",
            "capture": "saved.ics",
            "input_format": "public-meeting-ics",
            "bodies": "City Council",
        },
    )
    limited, problems = client.found(saved, FIRST, LAST)
    assert len(limited) == 1 and problems  # Saved capture is limited, never a complete catalog.
    assert run_week(settings, limited, LAST).failed == 0
    assert not list(settings.work.rglob("transcript.json"))
    assert not list(settings.work.rglob("script.json"))


def test_supplied_caption_capture_uses_real_pipeline_and_existing_producer_stage(tmp_path):
    client, config, _ = setup(tmp_path, captions=True)
    meetings, problems = client.found(config, FIRST, LAST)
    assert not problems
    folder = tmp_path / "captured"
    evidence = meetings[0].connector_prepare(folder)
    assert evidence["supplied_capture"]
    result = pipeline.run(
        "connector-" + meetings[0].connector_key,
        folder,
        supplied_capture=Path(evidence["supplied_capture"]),
        roster_path=ROSTERS_DIR / "oakland.json",
        complete=lambda prompt: ("[]", {}),
        log=lambda message: None,
    )
    assert result["caption_kind"] == "provider_supplied_captions"
    transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"]["capture_sha256"] == evidence["record"]["capture_sha256"]
    assert transcript["segments"][0]["start"] == 1
    (folder / "script.json").write_text(
        json.dumps([{"speaker": "S1", "text": "Source review required.", "claim_ids": []}]),
        encoding="utf-8",
    )
    db = tmp_path / "review.db"
    item_id = stage(db, folder, folder / "script.json")
    with ReviewStore(db) as store:
        assert store.get_item(item_id).status == "review"
    assert not export_published(db, tmp_path / "published").exported


def test_saved_capture_escape_and_unconfigured_write_rejected(tmp_path):
    client, config, _ = setup(tmp_path)
    with pytest.raises(SourceError):
        make_source(
            "connector",
            {"provider": "escribe", "url": BASE, "capture": "../outside.json", "bodies": "Council"},
        )
    with pytest.raises(PermissionError):
        client.plan(config, [])
    with pytest.raises(PermissionError):
        client.commit("unknown", "0" * 64, approved=False)


def test_same_external_id_isolated_by_city_and_producer_edits_preserved(tmp_path):
    a, config, _ = setup(tmp_path / "a")
    b, _, _ = setup(tmp_path / "b")
    assert a.context.scope != b.context.scope
    assert not b.store.list("records", scope=a.context.scope)
    found, _ = a.found(config, FIRST, LAST)
    evidence = found[0].connector_prepare(tmp_path / "a/work")
    db = tmp_path / "a/review.db"
    item_id = a.stage_evidence(db, evidence, "City Council")
    with ReviewStore(db) as store:
        article = store.get_item(item_id).article
        article["headline"] = "Producer-owned headline"
        store.update_content(item_id, article=article)
    a.stage_evidence(db, evidence, "City Council")
    a.stage_evidence(db, evidence, "City Council")
    with ReviewStore(db) as store:
        assert store.get_item(item_id).article["headline"] == "Producer-owned headline"


def test_supplied_recording_bytes_survive_review_reopen_and_export(tmp_path):
    origin = "https://webapi.legistar.com/v1/synthetic/events"
    recording_url = "https://media.synthetic.example/council.mp4"
    recording = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    capture = json.dumps(
        [
            {
                "EventId": 42,
                "EventBodyName": "City Council",
                "EventDate": "2026-11-11",
                "EventVideoPath": recording_url,
            }
        ]
    ).encode()
    (tmp_path / "saved.json").write_bytes(capture)

    class Network:
        def request(self, url, **options):
            assert url == recording_url
            return Response(url, recording, "video/mp4")

    client = CivicSources(
        tmp_path,
        engine=create_engine(
            storage=SQLiteStorage(tmp_path / ".connectors/state.db"),
            provider_transport=Network(),
            write_adapters={},
        ),
    )
    config = make_source(
        "connector",
        {
            "provider": "legistar",
            "url": origin,
            "capture": "saved.json",
            "input_format": "legistar-events-json",
            "bodies": "City Council",
            "options": {"download_origins": ["https://media.synthetic.example"]},
        },
    )
    save_sources(tmp_path, [config])
    meetings, limitations = client.found(config, FIRST, LAST)
    assert len(meetings) == 1 and limitations  # Saved discovery remains explicitly limited.
    folder = tmp_path / "capture"
    evidence = meetings[0].connector_prepare(folder)
    assert evidence["media"][0]["available"] is True
    assert evidence["media"][0]["sha256"] == hashlib.sha256(recording).hexdigest()
    assert "No usable supplied timed captions" in " ".join(evidence["limitations"])
    item_id = client.stage_evidence(tmp_path / "review.db", evidence, "City Council")
    with ReviewStore(tmp_path / "review.db") as store:
        assert store.get_item(item_id).status == "review"
    reopened = CivicSources(tmp_path)
    stored = reopened.store.list("brief_evidence", scope=reopened.context.scope)
    assert stored[0].value["media"] == evidence["media"]
    _result, _metadata, stream = reopened.export(include_attachments=True)
    with stream, zipfile.ZipFile(io.BytesIO(stream.read())) as archive:
        files = [archive.read(name) for name in archive.namelist()]
    assert recording in files and capture in files
    assert not (folder / "transcript.json").exists()
    assert not export_published(tmp_path / "review.db", tmp_path / "published").exported
