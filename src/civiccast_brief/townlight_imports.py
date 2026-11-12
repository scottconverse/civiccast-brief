"""Compatibility facade for the pinned, in-process standalone reader."""

from __future__ import annotations

import sys

from civic_data_connectors import Source, create_engine


class TownlightImportError(RuntimeError):
    """The standalone reader could not read a source response."""


def configured_python() -> str | None:
    """Keep old callers on the shared path; no executable is ever launched."""
    return sys.executable


def normalize(python: str, connector: str, url: str, content: bytes, *, input_format: str) -> dict:
    """The historical ``python`` argument is ignored for caller compatibility."""
    result = create_engine().normalize(Source(connector, url), content, input_format=input_format)
    if result.status != "completed":
        reason = "; ".join(error.message for error in result.errors)
        raise TownlightImportError(reason or f"The standalone reader returned {result.status}.")
    response = result.data.get("compatibility")
    if (
        not isinstance(response, dict)
        or response.get("version") != 1
        or not isinstance(response.get("meetings"), list)
    ):
        raise TownlightImportError("The standalone reader returned an unsupported response.")
    return response
