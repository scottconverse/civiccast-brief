"""A CivicClerk minutes file is dated by the meeting its name prints, not by its event."""

from __future__ import annotations

from datetime import date
from typing import Any

from civiccast_brief.citybuild.platforms import clerk_docs


class _Page:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _Fetcher:
    def get(self, url: str, max_bytes: int | None = None) -> _Page:
        return _Page(
            {
                "value": [
                    {
                        "id": 43,
                        "eventName": "Town Council Meeting",
                        "categoryName": "Town Council",
                        "startDateTime": "2026-10-26T18:00:00Z",
                        "publishedFiles": [
                            {"type": "Agenda", "fileId": 4745, "name": "10.26.2026 Agenda"},
                            {"type": "Agenda Packet", "fileId": 4746, "name": "10.26.2026 Packet"},
                            {"type": "Minutes", "fileId": 4749, "name": "10.12.2026 Minutes"},
                        ],
                    }
                ]
            }
        )


def test_a_minutes_file_takes_the_day_its_name_prints() -> None:
    docs = clerk_docs("superiorco", _Fetcher(), date(2026, 11, 18))  # type: ignore[arg-type]
    by_kind = {doc.what: doc for doc in docs}
    assert by_kind["agenda"].day == date(2026, 10, 26)
    assert by_kind["packet"].day == date(2026, 10, 26)
    assert by_kind["minutes"].day == date(2026, 10, 12)
    assert "fileId=4749" in by_kind["minutes"].url
