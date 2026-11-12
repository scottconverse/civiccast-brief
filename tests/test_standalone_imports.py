"""Standalone-reader regressions, using synthetic inputs and no provider access.

These tests intentionally fail on the pre-integration baseline. Keep the existing
importer facade callable while replacing its executable dependency with the SDK.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date

import httpx
import pytest

from civiccast_brief import townlight_imports
from civiccast_brief.meeting import granicus
from civiccast_brief.record.adapters.primegov import PrimeGovAdapter

MISSING_PYTHON = r"C:\TownlightNotInstalled\python.exe"
PRIMEGOV = "https://synthetic.primegov.com"
GRANICUS = "synthetic.granicus.com"
PUBLISHER = """<table><tr>
<td headers="Name">City Council</td>
<td headers="Date">November 11, 2026</td>
<td headers="VideoLink"><a href="MediaPlayer.php?view_id=2&amp;clip_id=42">Video</a></td>
</tr></table>"""


@pytest.fixture
def no_townlight_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOWNLIGHT_IMPORT_PYTHON", MISSING_PYTHON)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Brief must not launch a Townlight Python executable")

    monkeypatch.setattr(subprocess, "run", forbidden)


def test_normalize_facade_runs_without_a_townlight_executable(
    no_townlight_process: None,
) -> None:
    capture = json.dumps(
        {
            "MeetingId": 42,
            "MeetingName": "City Council",
            "MeetingDate": "2026-11-11T19:00:00Z",
        }
    ).encode()
    result = townlight_imports.normalize(
        MISSING_PYTHON,
        "legistar",
        "https://synthetic.legistar.com",
        capture,
        input_format="meeting-export",
    )
    assert result["version"] == 1
    assert len(result["meetings"]) == 1
    meeting = result["meetings"][0]
    assert str(meeting["external_id"]) == "42"
    assert meeting["title"] == "City Council"
    assert meeting["scheduled_start"] == "2026-11-11T19:00:00Z"


def test_invalid_capture_fails_without_launching_a_townlight_executable(
    no_townlight_process: None,
) -> None:
    with pytest.raises(townlight_imports.TownlightImportError):
        townlight_imports.normalize(
            MISSING_PYTHON,
            "legistar",
            "https://synthetic.legistar.com",
            b"not JSON",
            input_format="meeting-export",
        )


def test_primegov_existing_caller_ignores_stale_townlight_executable_setting(
    no_townlight_process: None,
) -> None:
    payload = {
        "id": 42,
        "dateTime": "2026-11-11T19:00:00",
        "title": "City Council",
        "videoUrl": "https://www.youtube.com/watch?v=abcdefghijk",
        "documentList": [
            {
                "id": 9,
                "templateId": 90,
                "compileOutputType": 1,
                "templateName": "Agenda",
            }
        ],
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "synthetic.primegov.com"
        assert request.method == "GET"
        return httpx.Response(200, json=[payload])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        meetings = PrimeGovAdapter(PRIMEGOV, client).list_meetings(
            date(2026, 11, 11), date(2026, 11, 11)
        )
    assert requests
    assert [meeting.id for meeting in meetings] == [42]
    assert meetings[0].start.tzinfo is None
    assert meetings[0].video_url == payload["videoUrl"]
    document = meetings[0].documents[0]
    assert (document.id, document.template_id, document.meeting_id) == (9, 90, 42)


def test_granicus_existing_caller_ignores_stale_townlight_executable_setting(
    no_townlight_process: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def fetch(url: str) -> str:
        requests.append(url)
        assert url == f"https://{GRANICUS}/ViewPublisher.php?view_id=2"
        return PUBLISHER

    monkeypatch.setattr(granicus, "_fetch", fetch)
    clips = granicus.list_clips(GRANICUS, 2)
    assert len(requests) == 1
    assert [(clip.clip_id, clip.name, clip.date, clip.view_id) for clip in clips] == [
        (42, "City Council", "November 11, 2026", 2)
    ]
