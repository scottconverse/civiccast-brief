"""Client for the optional Townlight meeting import command."""

from __future__ import annotations

import json
import os
import subprocess


class TownlightImportError(RuntimeError):
    """The configured shared importer could not read a source response."""


def configured_python() -> str | None:
    return os.environ.get("TOWNLIGHT_IMPORT_PYTHON") or None


def normalize(python: str, connector: str, url: str, content: bytes, *, input_format: str) -> dict:
    try:
        result = subprocess.run(
            [
                python,
                "-m",
                "townlight_runtime.meeting_imports",
                connector,
                url,
                "-",
                "--format",
                input_format,
            ],
            input=content,
            capture_output=True,
            check=False,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TownlightImportError("Could not run the configured Townlight importer.") from error
    if result.returncode:
        reason = result.stderr.decode("utf-8", errors="replace").strip()
        raise TownlightImportError(reason or "The Townlight importer exited without a result.")
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise TownlightImportError("The Townlight importer returned invalid JSON.") from error
    if (
        not isinstance(response, dict)
        or response.get("version") != 1
        or not isinstance(response.get("meetings"), list)
    ):
        raise TownlightImportError("The Townlight importer returned an unsupported response.")
    return response
