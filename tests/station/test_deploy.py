"""The deploy step: Cloudflare (R2 media, Pages site) and a plain folder for any web host."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from civiccast_brief.station import deploy
from civiccast_brief.station.app import main

SECRET = "cfat_not_a_real_token_0123456789"
TARGET = deploy.Cloudflare(
    project="civiccast-brief-longmont",
    bucket="civiccast-brief-media",
    prefix="longmont/",
    media_url="https://media.example.org/longmont/",
    account="acct123",
)


def _built(tmp_path: Path) -> tuple[Path, Path]:
    site, media = tmp_path / "site", tmp_path / "media"
    (site / "episodes").mkdir(parents=True)
    (site / "index.html").write_text("<h1>Longmont</h1>", encoding="utf-8")
    (site / "_headers").write_text("/*\n  X-Frame-Options: DENY\n", encoding="utf-8")
    (site / "episodes" / "one.html").write_text("one", encoding="utf-8")
    media.mkdir()
    for name, data in {
        "same.mp3": b"old audio",
        "weak.json": b"{}",
        "new.mp3": b"new audio",
        "changed.vtt": b"WEBVTT\n",
        "notes.txt": b"?",
    }.items():
        (media / name).write_bytes(data)
    return site, media


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class Wrangler:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.codes = codes or {}

    def __call__(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        verb = argv[1]
        return self.codes.get(verb, 0), f"wrangler {verb} with {env.get('CLOUDFLARE_API_TOKEN')}"


ETAGS = {
    "same.mp3": f'"{_md5(b"old audio")}"',
    "weak.json": f'W/"{_md5(b"{}")}"',
    "changed.vtt": '"0123"',
}


def _head(url: str) -> str | None:
    assert url.startswith("https://media.example.org/longmont/")  # one slash, not two
    return ETAGS.get(url.rsplit("/", 1)[1])


def test_cloudflare_uploads_only_new_or_changed_media_then_deploys_the_site(tmp_path):
    site, media = _built(tmp_path)
    tool, said = Wrangler(), []
    deploy.deploy_cloudflare(
        site, media, TARGET, said.append, command=tool, head=_head, token=SECRET, tool=["wr"]
    )
    puts = [argv for argv, _ in tool.calls if argv[1] == "r2"]
    assert [a[4] for a in puts] == [
        "civiccast-brief-media/longmont/changed.vtt",
        "civiccast-brief-media/longmont/new.mp3",
    ]
    assert puts[0][-3:] == ["--content-type", "text/vtt", "--remote"]
    assert puts[1][-3:] == ["--content-type", "audio/mpeg", "--remote"]
    pages, env = tool.calls[-1]
    assert pages == [
        "wr", "pages", "deploy", str(site), "--project-name", "civiccast-brief-longmont",
        "--branch", "main", "--commit-dirty=true",
    ]  # fmt: skip
    # Wrangler gets the token and the account; nothing the station prints shows the token.
    assert env == {"CLOUDFLARE_API_TOKEN": SECRET, "CLOUDFLARE_ACCOUNT_ID": "acct123"}
    log = "\n".join(said)
    assert SECRET not in log
    assert "wrangler pages with ****" in log
    assert "skipped notes.txt: no content type for .txt" in log
    assert "media: 2 file(s) uploaded" in log
    assert "site: deployed to Pages project civiccast-brief-longmont" in log


def test_a_failed_cloudflare_step_says_which_and_never_shows_the_token(tmp_path):
    site, media = _built(tmp_path)
    with pytest.raises(deploy.DeployError) as upload:
        deploy.deploy_cloudflare(
            site, media, TARGET, print, command=Wrangler({"r2": 1}), head=_head, token=SECRET,
            tool=["wr"],
        )  # fmt: skip
    assert "upload failed for longmont/changed.vtt" in str(upload.value)
    assert SECRET not in str(upload.value)
    with pytest.raises(deploy.DeployError, match="the Pages deploy failed"):
        deploy.deploy_cloudflare(
            site, media, TARGET, print, command=Wrangler({"pages": 1}), head=_head,
            token=SECRET, tool=["wr"],
        )  # fmt: skip


def test_nothing_published_yet_uploads_media_and_deploys_no_site(tmp_path):
    site, media = _built(tmp_path)
    (site / "index.html").unlink()
    tool, said = Wrangler(), []
    deploy.deploy_cloudflare(
        site, media, TARGET, said.append, command=tool, head=_head, token="", tool=["wr"]
    )
    assert all(argv[1] == "r2" for argv, _ in tool.calls)
    assert "site: not built yet (nothing is published); nothing deployed" in said


def test_a_folder_deploy_mirrors_the_build_and_keeps_the_host_s_own_files(tmp_path):
    site, media = _built(tmp_path)
    web, files = tmp_path / "public_html", tmp_path / "public_html" / "media"
    web.mkdir()
    (web / ".htaccess").write_text("Options -Indexes\n", encoding="utf-8")
    said: list[str] = []
    deploy.deploy_folder(site, media, web, files, said.append)
    assert (web / "episodes" / "one.html").read_text(encoding="utf-8") == "one"
    assert (web / "_headers").is_file()
    assert (files / "new.mp3").read_bytes() == b"new audio"
    assert said[-1] == f"site: 3 file(s) copied, 0 removed, into {web}"

    # The next deploy copies only what changed and removes only what it put there itself.
    (site / "episodes" / "one.html").unlink()
    (site / "index.html").write_text("<h1>Longmont, again</h1>", encoding="utf-8")
    said.clear()
    deploy.deploy_folder(site, media, web, files, said.append)
    assert said == [
        f"media: 0 file(s) copied, 0 removed, into {files}",
        f"site: 1 file(s) copied, 1 removed, into {web}",
    ]
    assert not (web / "episodes" / "one.html").exists()
    assert (web / ".htaccess").read_text(encoding="utf-8") == "Options -Indexes\n"
    assert (files / "new.mp3").is_file()  # the media folder inside the web root stays


def test_the_station_cli_deploys_to_a_folder(tmp_path, capsys):
    site, media = _built(tmp_path)
    out = tmp_path / "www"
    code = main(
        ["deploy", "folder", "--site", str(site), "--media", str(media), "--out", str(out),
         "--media-out", str(out / "media")]
    )  # fmt: skip
    assert code == 0
    assert (out / "index.html").is_file()
    assert "site: 3 file(s) copied" in capsys.readouterr().out
