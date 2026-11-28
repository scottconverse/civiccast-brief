import tomllib
from pathlib import Path

import civiccast_brief
import civiccast_brief.record

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def project_version() -> str:
    with PYPROJECT.open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def test_version() -> None:
    assert civiccast_brief.__version__ == project_version()


def test_record_version() -> None:
    assert civiccast_brief.record.__version__ == project_version()
