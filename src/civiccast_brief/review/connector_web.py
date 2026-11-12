"""Separate evidence/export and exact-plan producer actions on the local desk."""

from __future__ import annotations

import json
from datetime import date

from fastapi import Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from civiccast_brief.connectors import CivicSources, catalog
from civiccast_brief.sources import load_sources
from civiccast_brief.weekly.run import Settings, run_week


def install_routes(app, state, read_form, bad_token, *, factory=None):
    def service():
        if state.city_dir is None:
            raise ValueError("Start the desk with a city folder.")
        return factory(state.city_dir) if factory else CivicSources(state.city_dir)

    def page(*, result=None, error="", status=200):
        return state.page(
            "connectors.html",
            title="Connector evidence",
            status=status,
            connector_sources=[s for s in load_sources(state.city_dir) if s.kind == "connector"]
            if state.city_dir
            else [],
            providers=catalog(),
            result=json.dumps(result, indent=2, ensure_ascii=False) if result is not None else "",
            error=error,
        )

    @app.get("/connectors")
    def evidence():
        return page()

    @app.post("/connectors/{action}")
    async def action(action: str, request: Request):
        form = await read_form(request)
        if form is None:
            return bad_token()
        try:
            client = service()
        except ValueError as exc:
            return page(error=str(exc), status=400)
        try:
            if action == "search":
                return page(
                    result=(await run_in_threadpool(client.search, form["phrase"])).public_dict()
                )
            if action == "export":
                format = form.get("format", "zip")
                if format not in {"zip", "json", "jsonl", "csv-meetings", "csv-items", "ics"}:
                    raise ValueError("Pick a supported export format.")
                result, metadata, stream = await run_in_threadpool(
                    client.export,
                    format=format,
                    include_attachments=form.get("include_attachments") == "yes",
                )

                def chunks():
                    try:
                        while block := stream.read(1024 * 1024):
                            yield block
                    finally:
                        stream.close()
                        client.close()

                return StreamingResponse(
                    chunks(),
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="civic-evidence.{format}"',
                        "X-Connector-Status": result.status,
                        "X-Content-SHA256": metadata["sha256"],
                    },
                )
            if action == "commit":
                plan_hash = form["plan_hash"]
                result = await run_in_threadpool(
                    client.commit,
                    form["plan_id"],
                    plan_hash,
                    approved=form.get("approved_plan_hash") == plan_hash,
                )
                return page(result=result.public_dict())
            if action == "receipt":
                return page(
                    result=(await run_in_threadpool(client.receipt, form["plan_id"])).public_dict()
                )
            sources = [
                s
                for s in load_sources(state.city_dir)
                if s.kind == "connector" and s.id == form.get("source_id")
            ]
            if not sources:
                raise ValueError("Select a configured standalone source.")
            source = sources[0]
            if action == "plan":
                changes = json.loads(form["changes"])
                return page(
                    result=(await run_in_threadpool(client.plan, source, changes)).public_dict()
                )
            if action == "fetch":
                fields = json.loads(form.get("fetch_fields", "{}"))
                return page(
                    result=(
                        await run_in_threadpool(client.fetch_account, source, fields)
                    ).public_dict()
                )
            if action == "discover":
                first, last = date.fromisoformat(form["first"]), date.fromisoformat(form["last"])
                meetings, problems = await run_in_threadpool(client.found, source, first, last)
                report = await run_in_threadpool(
                    run_week,
                    Settings(state.city_dir, state.city_dir / ".connectors/runs", state.store.path),
                    meetings,
                    last,
                )
                return page(
                    result={
                        "source_problems": problems,
                        "workflow": report.lines,
                        "failed": report.failed,
                        "review_items": [
                            {"id": i.id, "title": i.title, "status": i.status}
                            for i in state.store.list_items()
                        ],
                    }
                )
            return PlainTextResponse("Unknown connector action.", status_code=404)
        except PermissionError as exc:
            return page(error=str(exc), status=403)
        except (ValueError, OSError, KeyError) as exc:
            return page(error=str(exc), status=400)
        finally:
            # Persistent SDK storage owns per-call connections, not a background service.
            if action != "export":
                client.close()
