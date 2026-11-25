"""Where each city's website goes: nothing, a folder on this PC, or Cloudflare.

``site.json`` sits next to the desk's ``desk.db`` (the city's work folder), not in the city
folder, because it belongs to the desk's job::

    {
      "target": "none"
    }

    {
      "target": "folder",
      "base_url": "https://longmont.example.org",
      "media_base_url": "https://longmont.example.org/media",
      "out": "C:/web/longmont",
      "media_out": "C:/web/longmont/media"
    }

    {
      "target": "cloudflare",
      "base_url": "https://longmont.civiccastbrief.org",
      "media_base_url": "https://media.civiccastbrief.org/longmont",
      "project": "civiccast-brief-longmont",
      "bucket": "civiccast-brief-media",
      "prefix": "longmont/",
      "account": ""
    }

The setup wizard's Website step fills ``{slug}`` in one set of patterns for every city and
writes each city's own resolved ``site.json``. The desk builds its Station with the publish
steps this module makes: the site is rebuilt from what is published, then put where readers
get it. A missing file means no website; a broken one means no website and a warning, as
``weekly.json`` is handled.

Paths inside this module, exactly: the city's work folder is ``db.parent`` (the hub's
``DIR/<slug>/``, which holds ``desk.db``, ``city/`` and ``runs/weekly/``; a single-city desk's
``--db`` folder). The site builds into ``<db.parent>/site/episodes``, ``site/media`` and
``site/out``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

SETTINGS_NAME: Final = "site.json"
TARGETS: Final = ("none", "folder", "cloudflare")
PROJECT_RE: Final = re.compile(r"^[a-z0-9-]{1,58}$")
BUCKET_RE: Final = re.compile(r"^[a-z0-9-]{3,63}$")
#: The Windows Credential Manager target the deploy step reads (``station/deploy.py``).
TOKEN_CREDENTIAL: Final = "CivicCastBrief/cloudflare-deploy"


@dataclass(frozen=True)
class SiteSettings:
    """One city's website choice and the values that choice needs."""

    target: str = "none"
    base_url: str = ""
    media_base_url: str = ""
    out: str = ""
    media_out: str = ""
    project: str = ""
    bucket: str = ""
    prefix: str = ""
    account: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT: Final = SiteSettings()


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    return "" if value is None else str(value).strip()


def load(path: str | Path) -> tuple[SiteSettings, str]:
    """``site.json`` and, when it is broken, a warning; a missing file means no website."""
    path = Path(path)
    if not path.exists():
        return DEFAULT, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return DEFAULT, f"{path} could not be read: {exc}. Saving the form replaces it."
    if not isinstance(data, dict):
        return DEFAULT, f"{path} must hold a JSON object. Saving the form replaces it."
    target = _text(data, "target") or "none"
    if target not in TARGETS:
        return DEFAULT, (
            f"{path} has the unknown website choice {target!r}. Saving the form replaces it."
        )
    settings = SiteSettings(
        target=target,
        base_url=_text(data, "base_url"),
        media_base_url=_text(data, "media_base_url"),
        out=_text(data, "out"),
        media_out=_text(data, "media_out"),
        project=_text(data, "project"),
        bucket=_text(data, "bucket"),
        prefix=_text(data, "prefix"),
        account=_text(data, "account"),
    )
    return settings, ""


def save(path: str | Path, settings: SiteSettings) -> Path:
    """Write ``site.json`` through a temporary file, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings.to_json(), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def _http_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate(settings: SiteSettings) -> list[tuple[str, str]]:
    """Every setting that will not work, as (field, plain message); empty when it is usable."""
    errors: list[tuple[str, str]] = []
    if settings.target not in TARGETS:
        return [("target", "Choose None, a folder on this PC, or Cloudflare.")]
    if settings.target == "none":
        return errors
    if not _http_url(settings.base_url):
        errors.append(("base_url", "The site address must start with http:// or https://."))
    if not _http_url(settings.media_base_url):
        errors.append(("media_base_url", "The media address must start with http:// or https://."))
    if settings.target == "folder":
        if not settings.out.strip():
            errors.append(("out", "Write the web root folder, like C:\\web\\longmont."))
        elif not Path(settings.out).is_absolute():
            errors.append(("out", "The web root must be a full path, like C:\\web\\longmont."))
        if not settings.media_out.strip():
            errors.append(("media_out", "Write the media folder, like C:\\web\\longmont\\media."))
        elif not Path(settings.media_out).is_absolute():
            errors.append(
                (
                    "media_out",
                    "The media folder must be a full path, like C:\\web\\longmont\\media.",
                )
            )
    else:
        if not PROJECT_RE.match(settings.project):
            errors.append(
                (
                    "project",
                    "The Pages project needs 1 to 58 lowercase letters, digits or hyphens.",
                )
            )
        if not BUCKET_RE.match(settings.bucket):
            errors.append(
                (
                    "bucket",
                    "The R2 bucket needs 3 to 63 lowercase letters, digits or hyphens.",
                )
            )
        if not settings.prefix.strip():
            errors.append(("prefix", "Write the key prefix in the bucket, like longmont/."))
    return errors


def resolve(values: Mapping[str, Any], slug: str) -> SiteSettings:
    """One city's settings from the wizard's patterns, with ``{slug}`` filled in."""

    def filled(key: str) -> str:
        return _text(values, key).replace("{slug}", slug)

    return SiteSettings(
        target=_text(values, "target") or "none",
        base_url=filled("base_url"),
        media_base_url=filled("media_base_url"),
        out=filled("out"),
        media_out=filled("media_out"),
        project=filled("project"),
        bucket=filled("bucket"),
        prefix=filled("prefix"),
        account=filled("account"),
    )


def city_slug(city_dir: str | Path) -> str:
    """The city's slug: ``city.json``'s when it has one, else its folder's name."""
    city_dir = Path(city_dir)
    try:
        data = json.loads((city_dir / "city.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        slug = str(data.get("slug") or "").strip()
        if slug:
            return slug
    return city_dir.parent.name if city_dir.name == "city" else city_dir.name


def build_folders(db: str | Path) -> tuple[Path, Path, Path]:
    """The site build's episodes, media and out folders, all under the work folder."""
    site = Path(db).parent / "site"
    return site / "episodes", site / "media", site / "out"


def publish_steps(
    settings: SiteSettings,
    city_dir: str | Path,
    db: str | Path,
    weekly_work: str | Path,
    voices: str | Path | None = None,
) -> list[list[str]]:
    """The commands that build the site from the desk and put it where readers get it.

    Empty for None. The first step is the desk's own site build; the second is the matching
    deploy. The weekly run copies ``city.json`` into the episodes folder itself, so nothing
    else has to be there.
    """
    if settings.target == "none":
        return []
    episodes, media, out = build_folders(db)
    build = [
        sys.executable,
        "-m",
        "civiccast_brief.weekly",
        "publish",
        "--city",
        str(city_dir),
        "--work",
        str(weekly_work),
        "--db",
        str(db),
        "--episodes",
        str(episodes),
        "--media",
        str(media),
        "--site-out",
        str(out),
        "--base-url",
        settings.base_url,
        "--media-base-url",
        settings.media_base_url,
    ]
    voice_file = Path(voices) if voices is not None else Path(city_dir) / "voices.json"
    if voice_file.is_file():
        build += ["--voices", str(voice_file)]
    deploy = [
        sys.executable,
        "-m",
        "civiccast_brief.station",
        "deploy",
        settings.target,
        "--site",
        str(out),
        "--media",
        str(media),
    ]
    if settings.target == "folder":
        deploy += ["--out", settings.out, "--media-out", settings.media_out]
    else:
        deploy += [
            "--project",
            settings.project,
            "--bucket",
            settings.bucket,
            "--prefix",
            settings.prefix,
            "--media-url",
            settings.media_base_url,
        ]
        if settings.account:
            deploy += ["--account", settings.account]
    return [build, deploy]


# ----- the wizard's Check Cloudflare ---------------------------------------------------------


def credential_exists() -> bool:
    """True when the deploy token is in Windows Credential Manager. The value is never kept."""
    try:
        from civiccast_brief.record.credentials import read_windows_credential

        return bool(read_windows_credential(TOKEN_CREDENTIAL))
    except Exception:  # noqa: BLE001 - a missing store or platform just means "not found"
        return False


def npx_present() -> bool:
    """True when npx is on PATH, which the Cloudflare deploy runs wrangler through."""
    return shutil.which("npx") is not None
