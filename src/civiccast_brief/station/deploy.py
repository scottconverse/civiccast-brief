"""Put the built site and its media where readers get them.

    python -m civiccast_brief.station deploy cloudflare --site live/site --media live/media
        --project civiccast-brief-longmont --bucket civiccast-brief-media --prefix longmont/
        --media-url https://media.civiccastbrief.org/longmont [--account ID]

    python -m civiccast_brief.station deploy folder --site live/site --media live/media
        --out /home/u1/public_html --media-out /home/u1/public_html/media

``cloudflare`` uploads each media file R2 does not hold yet, or holds with other bytes (the
public address answers with the file's MD5 as its ETag), then deploys the site to Pages with
wrangler, which keeps the ``_headers`` file. Wrangler signs in with ``CLOUDFLARE_API_TOKEN``
or, on Windows, the token saved in Credential Manager as ``CivicCastBrief/cloudflare-deploy``.
The token is never printed.

``folder`` copies the site and the media into folders a web server serves (a Hostinger web
root, an nginx folder). It removes only files it put there itself and that are gone from the
build, so the host's own files (``.htaccess``) stay.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: The wrangler the repo's worker pins (worker/signup/package.json).
WRANGLER_VERSION: Final = "4.158.0"
TOKEN_CREDENTIAL: Final = "CivicCastBrief/cloudflare-deploy"
CONTENT_TYPES: Final = {".mp3": "audio/mpeg", ".json": "application/json", ".vtt": "text/vtt"}
#: What the folder target wrote, so a later deploy removes only its own files.
MANIFEST: Final = ".civiccast-deployed.json"

#: Runs a command with extra environment; returns the exit code and what it printed.
Command = Callable[[Sequence[str], dict[str, str]], tuple[int, str]]
#: The ETag the public media address answers with, or None.
Head = Callable[[str], str | None]


class DeployError(RuntimeError):
    """A deploy step failed; the message says which and why."""


def run_command(argv: Sequence[str], env: dict[str, str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(argv),
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        return 127, f"could not start {argv[0]}: {exc}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def head_etag(url: str) -> str | None:
    import httpx

    try:
        reply = httpx.head(url, follow_redirects=True, timeout=30)
    except httpx.HTTPError:
        return None
    if reply.status_code != 200:
        return None
    return reply.headers.get("etag")


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def same_bytes(etag: str | None, digest: str) -> bool:
    # A compressed answer (JSON, VTT) marks the same ETag weak: W/"<md5>".
    if not etag:
        return False
    return etag.removeprefix("W/").strip('"').lower() == digest


def wrangler() -> list[str]:
    """The repo's own wrangler when it is installed, else the pinned one through npx."""
    repo = Path(__file__).resolve().parents[3]
    local = repo / "worker" / "signup" / "node_modules" / ".bin"
    for name in ("wrangler.cmd", "wrangler"):
        if (local / name).is_file():
            return [str(local / name)]
    npx = shutil.which("npx") or "npx"
    return [npx, "--yes", f"wrangler@{WRANGLER_VERSION}"]


def cloudflare_token() -> str | None:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    if sys.platform == "win32":
        from civiccast_brief.record.credentials import read_windows_credential

        return read_windows_credential(TOKEN_CREDENTIAL)
    return None


@dataclass(frozen=True)
class Cloudflare:
    project: str
    bucket: str
    prefix: str
    media_url: str
    account: str | None = None


def media_files(media: Path) -> Iterator[Path]:
    if media.is_dir():
        yield from sorted(p for p in media.iterdir() if p.is_file())


def deploy_cloudflare(
    site: Path,
    media: Path,
    target: Cloudflare,
    say: Callable[[str], None],
    *,
    command: Command = run_command,
    head: Head = head_etag,
    token: str | None = None,
    tool: Sequence[str] | None = None,
) -> None:
    env: dict[str, str] = {}
    token = token if token is not None else cloudflare_token()
    if token:
        env["CLOUDFLARE_API_TOKEN"] = token
        say("using the saved Cloudflare deploy token")
    account = target.account or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if account:
        env["CLOUDFLARE_ACCOUNT_ID"] = account
    tool = list(tool) if tool is not None else wrangler()
    uploaded = 0
    for path in media_files(media):
        key = target.prefix + path.name
        if same_bytes(head(f"{target.media_url.rstrip('/')}/{path.name}"), md5(path)):
            continue
        kind = CONTENT_TYPES.get(path.suffix.lower())
        if kind is None:
            say(f"skipped {path.name}: no content type for {path.suffix}")
            continue
        argv = [*tool, "r2", "object", "put", f"{target.bucket}/{key}", "--file", str(path)]
        code, out = command([*argv, "--content-type", kind, "--remote"], env)
        if code != 0:
            raise DeployError(f"upload failed for {key}: {_clean(out, token)}")
        say(f"uploaded {key}")
        uploaded += 1
    say(f"media: {uploaded} file(s) uploaded")
    if not (site / "index.html").is_file():
        say("site: not built yet (nothing is published); nothing deployed")
        return
    argv = [*tool, "pages", "deploy", str(site), "--project-name", target.project]
    code, out = command([*argv, "--branch", "main", "--commit-dirty=true"], env)
    say(_clean(out, token).strip())
    if code != 0:
        raise DeployError(f"the Pages deploy failed (exit code {code})")
    say(f"site: deployed to Pages project {target.project}")


def _clean(text: str, token: str | None) -> str:
    return text.replace(token, "****") if token else text


def sync_folder(source: Path, out: Path, say: Callable[[str], None], what: str) -> None:
    """Make ``out`` hold ``source``'s files; remove only files an earlier sync wrote."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / MANIFEST
    try:
        before = set(json.loads(manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        before = set()
    now: set[str] = set()
    copied = 0
    for path in sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else []:
        name = path.relative_to(source).as_posix()
        if name == MANIFEST:
            continue
        now.add(name)
        target = out / name
        same_size = target.is_file() and target.stat().st_size == path.stat().st_size
        if same_size and md5(target) == md5(path):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied += 1
    removed = 0
    for name in sorted(before - now):
        gone = out / name
        if gone.is_file():
            gone.unlink()
            removed += 1
    manifest.write_text(json.dumps(sorted(now), indent=1), encoding="utf-8")
    say(f"{what}: {copied} file(s) copied, {removed} removed, into {out}")


def deploy_folder(
    site: Path, media: Path, out: Path, media_out: Path, say: Callable[[str], None]
) -> None:
    sync_folder(media, media_out, say, "media")
    if not (site / "index.html").is_file():
        say("site: not built yet (nothing is published); nothing deployed")
        return
    sync_folder(site, out, say, "site")
