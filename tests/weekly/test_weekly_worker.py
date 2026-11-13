"""With --worker, a cloud run asks a Windows PC for YouTube captions and audio and waits for them."""

import json
from pathlib import Path

from test_weekly_run import COUNCIL_MEETING, LAST, Fake, _opt, _settings

from civiccast_brief.weekly.run import STATE_NAME, Settings, Site, run_week
from civiccast_brief.weekly.worker import AUDIO, AUDIO_FILES, CAPTIONS, WorkerFolder, audio_key


def _with_worker(settings: Settings, tmp_path: Path) -> Settings:
    return Settings(
        city=settings.city,
        work=settings.work,
        db=settings.db,
        site=settings.site,
        worker=tmp_path / "worker",
    )


def _deliver(inbox: WorkerFolder, kind: str, key: str, files: dict[str, str], tmp_path: Path):
    for name, text in files.items():
        upload = tmp_path / f"upload-{name}"
        upload.write_text(text, encoding="utf-8")
        inbox.receive(kind, key, name, upload)
    inbox.finish(kind, key)


def test_a_youtube_meeting_waits_for_its_captions_then_runs_with_them(tmp_path):
    settings = _with_worker(_settings(tmp_path), tmp_path)
    inbox = WorkerFolder(settings.worker)
    fake = Fake()
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert fake.calls == [], "nothing runs without the captions"
    assert report.failed == 0
    assert any("waiting for its captions from the PC worker" in line for line in report.lines)
    jobs = inbox.jobs()
    assert [j["video_id"] for j in jobs[CAPTIONS]] == ["oWZpzjzjwnE"]
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert state["meetings"]["youtube:oWZpzjzjwnE"]["status"] == "waiting"

    caption = {"oWZpzjzjwnE.en.srv3": "<timedtext/>", "oWZpzjzjwnE.info.json": "{}"}
    _deliver(inbox, CAPTIONS, "oWZpzjzjwnE", caption, tmp_path)
    assert inbox.jobs()[CAPTIONS] == []

    again = Fake()
    report = run_week(settings, [COUNCIL_MEETING], LAST, again.steps())
    assert report.failed == 0
    assert again.names()[0] == ("meeting", "run")
    raw = Path(_opt(again.calls[0][1], "--out")) / "raw"
    assert (raw / "oWZpzjzjwnE.en.srv3").read_text(encoding="utf-8") == "<timedtext/>"


def test_a_published_episode_waits_for_its_audio_then_is_built_with_it(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
        audio_engine="vibevoice",
    )
    plain = _settings(tmp_path, site=site)
    item = {"kind": "episode", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    script = "[S1] The council met.\n"
    # The first run is on the PC (no worker), so the meeting itself is already done.
    first = Fake()
    first.exported = {}
    run_week(plain, [COUNCIL_MEETING], LAST, first.steps())

    settings = _with_worker(plain, tmp_path)
    inbox = WorkerFolder(settings.worker)
    fake = Fake()
    fake.exported = {"0001-episode": {**item, "content_hash": "a" * 64, "script": script}}
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert "audio" not in [step for step, _ in fake.calls], "the cloud makes no audio"
    assert not any(argv[0] == "assemble" for step, argv in fake.calls if step == "episode")
    assert "oWZpzjzjwnE: waiting for its audio from the PC worker" in report.lines
    assert report.failed == 0
    [job] = inbox.jobs()[AUDIO]
    assert job["script"] == script and job["engine"] == "vibevoice"
    assert job["key"] == audio_key(script, "vibevoice")

    _deliver(inbox, AUDIO, job["key"], {n: f"pc {n}" for n in AUDIO_FILES}, tmp_path)
    again = Fake()
    again.exported = fake.exported
    report = run_week(settings, [COUNCIL_MEETING], LAST, again.steps())

    assert "oWZpzjzjwnE: built episode ep-oWZpzjzjwnE" in report.lines
    assert "audio" not in [step for step, _ in again.calls]
    assemble = next(a for s, a in again.calls if s == "episode" and a[0] == "assemble")
    audio = Path(_opt(assemble, "--audio"))
    assert (audio / "episode.mp3").read_text(encoding="utf-8") == "pc episode.mp3"


def test_the_worker_folder_refuses_files_a_job_does_not_take(tmp_path):
    inbox = WorkerFolder(tmp_path)
    upload = tmp_path / "x"
    for kind, key, name in (
        (AUDIO, "k1", "evil.exe"),
        (CAPTIONS, "abc", "other.srv3"),
        (CAPTIONS, "abc", "..\\abc.srv3"),
        (CAPTIONS, "../up", "x.srv3"),
        ("video", "abc", "abc.srv3"),
    ):
        upload.write_text("x", encoding="utf-8")
        try:
            inbox.receive(kind, key, name, upload)
        except ValueError:
            continue
        raise AssertionError(f"took {kind}/{key}/{name}")
    upload.write_text("x", encoding="utf-8")
    inbox.receive(AUDIO, "k1", "episode.mp3", upload)
    try:
        inbox.finish(AUDIO, "k1")
    except ValueError as exc:
        assert "chapters.json" in str(exc)
    else:
        raise AssertionError("finished without all the audio files")
    assert inbox.ready(AUDIO, "k1") is None
