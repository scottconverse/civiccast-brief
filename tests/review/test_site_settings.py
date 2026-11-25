"""The Website settings: site.json, its checks, and the publish commands it makes."""

from __future__ import annotations

import json
import sys

from civiccast_brief.review import site_settings
from civiccast_brief.review.site_settings import SiteSettings


def folder_settings(**changes) -> SiteSettings:
    values = {
        "target": "folder",
        "base_url": "https://longmont.example.org",
        "media_base_url": "https://longmont.example.org/media",
        "out": "C:/web/longmont",
        "media_out": "C:/web/longmont/media",
    }
    values.update(changes)
    return SiteSettings(**values)


def cloudflare_settings(**changes) -> SiteSettings:
    values = {
        "target": "cloudflare",
        "base_url": "https://longmont.civiccastbrief.org",
        "media_base_url": "https://media.civiccastbrief.org/longmont",
        "project": "civiccast-brief-longmont",
        "bucket": "civiccast-brief-media",
        "prefix": "longmont/",
    }
    values.update(changes)
    return SiteSettings(**values)


def paths(tmp_path):
    db = tmp_path / "alpha" / "desk.db"
    city_dir = tmp_path / "alpha" / "city"
    weekly_work = tmp_path / "alpha" / "runs" / "weekly"
    return city_dir, db, weekly_work


# ----- site.json -----------------------------------------------------------------------------


def test_a_missing_file_means_no_website(tmp_path):
    settings, warning = site_settings.load(tmp_path / "site.json")
    assert settings.target == "none"
    assert warning == ""


def test_a_broken_file_means_no_website_and_a_warning(tmp_path):
    path = tmp_path / "site.json"
    path.write_text("{not json", encoding="utf-8")
    settings, warning = site_settings.load(path)
    assert settings.target == "none"
    assert str(path) in warning and "could not be read" in warning


def test_an_unknown_choice_means_no_website_and_a_warning(tmp_path):
    path = tmp_path / "site.json"
    path.write_text(json.dumps({"target": "ftp"}), encoding="utf-8")
    settings, warning = site_settings.load(path)
    assert settings.target == "none"
    assert "unknown website choice" in warning


def test_save_writes_through_a_temporary_file_and_load_reads_it_back(tmp_path):
    path = tmp_path / "site.json"
    saved = site_settings.save(path, cloudflare_settings(account="abc123"))
    assert saved == path
    assert not (tmp_path / "site.json.tmp").exists()
    settings, warning = site_settings.load(path)
    assert warning == ""
    assert settings == cloudflare_settings(account="abc123")


# ----- validate ------------------------------------------------------------------------------


def test_none_is_always_valid_and_needs_nothing():
    assert site_settings.validate(SiteSettings(target="none")) == []


def test_a_good_folder_passes():
    assert site_settings.validate(folder_settings()) == []


def test_a_folder_needs_http_addresses_and_full_paths():
    errors = dict(
        site_settings.validate(
            folder_settings(
                base_url="longmont.example.org", media_base_url="", out="web", media_out=""
            )
        )
    )
    assert set(errors) == {"base_url", "media_base_url", "out", "media_out"}
    assert "http:// or https://" in errors["base_url"]
    assert "full path" in errors["out"]


def test_cloudflare_checks_the_project_bucket_and_prefix():
    assert site_settings.validate(cloudflare_settings()) == []
    errors = dict(
        site_settings.validate(
            cloudflare_settings(project="CivicCast_Brief", bucket="ab", prefix="")
        )
    )
    assert set(errors) == {"project", "bucket", "prefix"}
    assert "58" in errors["project"]
    assert "63" in errors["bucket"]


def test_an_unknown_target_is_refused():
    errors = dict(site_settings.validate(SiteSettings(target="ftp")))
    assert "target" in errors


def test_resolve_fills_the_slug_everywhere():
    values = {
        "target": "cloudflare",
        "base_url": "https://{slug}.civiccastbrief.org",
        "media_base_url": "https://media.civiccastbrief.org/{slug}",
        "project": "civiccast-brief-{slug}",
        "bucket": "media",
        "prefix": "{slug}/",
        "account": "",
    }
    settings = site_settings.resolve(values, "longmont")
    assert settings.base_url == "https://longmont.civiccastbrief.org"
    assert settings.media_base_url == "https://media.civiccastbrief.org/longmont"
    assert settings.project == "civiccast-brief-longmont"
    assert settings.prefix == "longmont/"


# ----- publish_steps -------------------------------------------------------------------------


def test_none_makes_no_publish_steps(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    assert site_settings.publish_steps(SiteSettings(), city_dir, db, weekly_work) == []


def test_folder_makes_the_build_and_the_folder_deploy_without_voices(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    steps = site_settings.publish_steps(folder_settings(), city_dir, db, weekly_work)
    site = db.parent / "site"
    assert steps == [
        [
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
            str(site / "episodes"),
            "--media",
            str(site / "media"),
            "--site-out",
            str(site / "out"),
            "--base-url",
            "https://longmont.example.org",
            "--media-base-url",
            "https://longmont.example.org/media",
        ],
        [
            sys.executable,
            "-m",
            "civiccast_brief.station",
            "deploy",
            "folder",
            "--site",
            str(site / "out"),
            "--media",
            str(site / "media"),
            "--out",
            "C:/web/longmont",
            "--media-out",
            "C:/web/longmont/media",
        ],
    ]


def test_folder_adds_the_city_voices_when_it_has_them(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    city_dir.mkdir(parents=True)
    (city_dir / "voices.json").write_text("{}", encoding="utf-8")
    steps = site_settings.publish_steps(folder_settings(), city_dir, db, weekly_work)
    assert steps[0][-2:] == ["--voices", str(city_dir / "voices.json")]


def test_cloudflare_makes_the_build_and_the_cloudflare_deploy(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    steps = site_settings.publish_steps(cloudflare_settings(), city_dir, db, weekly_work)
    site = db.parent / "site"
    assert steps[1] == [
        sys.executable,
        "-m",
        "civiccast_brief.station",
        "deploy",
        "cloudflare",
        "--site",
        str(site / "out"),
        "--media",
        str(site / "media"),
        "--project",
        "civiccast-brief-longmont",
        "--bucket",
        "civiccast-brief-media",
        "--prefix",
        "longmont/",
        "--media-url",
        "https://media.civiccastbrief.org/longmont",
    ]


def test_cloudflare_adds_the_account_when_it_is_given(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    steps = site_settings.publish_steps(
        cloudflare_settings(account="abc123"), city_dir, db, weekly_work
    )
    assert steps[1][-2:] == ["--account", "abc123"]


def test_an_explicit_voices_file_is_used_when_present(tmp_path):
    city_dir, db, weekly_work = paths(tmp_path)
    voices = tmp_path / "elsewhere.json"
    voices.write_text("{}", encoding="utf-8")
    steps = site_settings.publish_steps(folder_settings(), city_dir, db, weekly_work, voices=voices)
    assert steps[0][-2:] == ["--voices", str(voices)]


def test_city_slug_reads_city_json_then_the_folder_name(tmp_path):
    city_dir = tmp_path / "longmont-folder" / "city"
    city_dir.mkdir(parents=True)
    assert site_settings.city_slug(city_dir) == "longmont-folder"
    (city_dir / "city.json").write_text(json.dumps({"slug": "longmont"}), encoding="utf-8")
    assert site_settings.city_slug(city_dir) == "longmont"
