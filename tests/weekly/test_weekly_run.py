"""The weekly run with stand-in steps: each one writes the files the real one writes."""

import json
import shutil
from datetime import date
from pathlib import Path

from civiccast_brief.weekly.find import Agenda, Found
from civiccast_brief.weekly.run import STATE_NAME, Settings, Site, Steps, run_week

COUNCIL = "City Council"
PZ = "Planning and Zoning Commission"
LAST = date(2026, 11, 10)


def _opt(argv, name):
    return argv[argv.index(name) + 1]


class Fake:
    """Stand-in mains. ``fail`` maps a step name to how many times it fails first."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = dict(fail or {})
        self.exported = {}

    def _failing(self, name):
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            return True
        return False

    def steps(self):
        return Steps(
            meeting=self.meeting,
            script=self.script,
            episode=self.episode,
            article=self.article,
            review=self.review,
            audio=self.audio,
            site=self.site,
        )

    def meeting(self, argv):
        self.calls.append(("meeting", list(argv)))
        if self._failing("meeting"):
            return 1
        out = Path(_opt(argv, "--out"))
        video = argv[1]
        recap = {"body": "x", "meeting_date": "November 3, 2026", "video_id": video, "claims": []}
        (out / "recap.json").write_text(json.dumps(recap), encoding="utf-8")
        (out / "transcript.json").write_text(json.dumps({"video_id": video}), encoding="utf-8")
        return 0

    def script(self, argv):
        self.calls.append(("script", list(argv)))
        command = argv[0]
        if command in ("write", "repair"):
            out = Path(_opt(argv, "--out"))
            out.write_text("[S1] hi\n", encoding="utf-8")
            out.with_suffix(".json").write_text("[]", encoding="utf-8")
            return 1 if self._failing(command) else 0
        if command == "check":
            Path(_opt(argv, "--report")).write_text("line 3 ERROR outro", encoding="utf-8")
            return 1
        if command == "roundup":
            Path(_opt(argv, "--out")).write_text(
                json.dumps({"meeting_date": "November 3, 2026 and November 5, 2026"}),
                encoding="utf-8",
            )
            return 0
        raise AssertionError(argv)

    def episode(self, argv):
        self.calls.append(("episode", list(argv)))
        if argv[0] == "assemble":
            meeting_id = json.loads((Path(_opt(argv, "--item")) / "item.json").read_text())[
                "meeting_id"
            ]
            Path(_opt(argv, "--out")).write_text(
                json.dumps({"id": f"ep-{meeting_id}"}), encoding="utf-8"
            )
        if argv[0] == "story":
            meeting_id = json.loads((Path(_opt(argv, "--article")) / "item.json").read_text())[
                "meeting_id"
            ]
            Path(_opt(argv, "--out")).write_text(json.dumps({"id": f"st-{meeting_id}"}))
        return 1 if self._failing("episode") else 0

    def article(self, argv):
        self.calls.append(("article", list(argv)))
        if argv[0] == "write":
            out = Path(_opt(argv, "--out"))
            if not self._failing("article"):
                out.with_suffix(".json").write_text("{}", encoding="utf-8")
                return 0
            return 1
        return 0

    def review(self, argv):
        self.calls.append(("review", list(argv)))
        out = Path(_opt(argv, "--out"))
        out.mkdir(parents=True, exist_ok=True)  # the real export always writes manifest.json
        for name, meta in self.exported.items():
            (out / name).mkdir(parents=True, exist_ok=True)
            (out / name / "item.json").write_text(json.dumps(meta), encoding="utf-8")
            if "script" in meta:  # the real export writes the desk's script.txt
                (out / name / "script.txt").write_text(meta["script"], encoding="utf-8")
        return 0

    def audio(self, argv):
        self.calls.append(("audio", list(argv)))
        out = Path(_opt(argv, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        for name in ("episode.mp3", "chapters.json", "transcript.vtt"):
            (out / name).write_text(name, encoding="utf-8")
        return 0

    def site(self, argv):
        self.calls.append(("site", list(argv)))
        return 0

    def names(self):
        return [(step, argv[0]) for step, argv in self.calls]


def _city(tmp_path, rosters=(COUNCIL,)):
    city = tmp_path / "city"
    (city / "rosters").mkdir(parents=True)
    (city / "roster.json").write_text(json.dumps({"body": COUNCIL}), encoding="utf-8")
    for body in rosters:
        if body != COUNCIL:
            name = body.lower().replace(" ", "-")
            (city / "rosters" / f"{name}.json").write_text(json.dumps({"body": body}))
    return city


def _settings(tmp_path, site=None, **kw):
    return Settings(
        city=_city(tmp_path, **kw), work=tmp_path / "work", db=tmp_path / "desk.db", site=site
    )


AGENDA = Agenda("https://longmont.primegov.com/agenda/9", ("1. Call to order",))
COUNCIL_MEETING = Found(
    COUNCIL, date(2026, 11, 3), "City Council Regular Session", "p", "oWZpzjzjwnE", agenda=AGENDA
)
PZ_MEETING = Found(PZ, date(2026, 11, 5), "Planning and Zoning Commission", "p", "PZPZPZPZPZ1")


def test_one_meeting_runs_every_step_in_order_with_its_roster_and_agenda(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert report.failed == 0
    assert fake.names() == [
        ("meeting", "run"),
        ("script", "write"),
        ("episode", "stage"),
        ("article", "write"),
        ("article", "stage"),
    ]
    run_argv = fake.calls[0][1]
    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    assert run_argv[1] == "oWZpzjzjwnE"
    assert _opt(run_argv, "--out") == str(folder)
    assert _opt(run_argv, "--roster") == str(settings.city / "roster.json")
    agenda = Path(_opt(run_argv, "--agenda")).read_text(encoding="utf-8")
    assert agenda.startswith("Source: https://longmont.primegov.com/agenda/9\nRetrieved ")
    assert "AGENDA\n1. Call to order" in agenda
    stage = fake.calls[2][1]
    assert "--city" not in stage
    assert _opt(stage, "--script") == str(folder / "script.json")
    assert "roundup: not made (1 meeting(s) done this week; it needs 2)" in report.lines
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    entry = state["meetings"]["youtube:oWZpzjzjwnE"]
    assert entry["status"] == "done"
    assert entry["done"] == ["roster", "recap", "script", "episode", "article"]


def test_a_failing_script_is_written_again_with_the_report_then_repaired(tmp_path):
    fake = Fake(fail={"write": 2})
    settings = _settings(tmp_path)
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    scripts = [argv for step, argv in fake.calls if step == "script"]
    assert [argv[0] for argv in scripts] == ["write", "check", "write", "repair"]
    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    assert _opt(scripts[2], "--feedback") == str(folder / "script-report.txt")
    stage = next(argv for step, argv in fake.calls if step == "episode")
    assert _opt(stage, "--script") == str(folder / "script-final.json")


def test_a_failure_is_recorded_the_next_meeting_still_runs_and_a_rerun_resumes(tmp_path):
    fake = Fake(fail={"meeting": 1})
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    report = run_week(settings, [COUNCIL_MEETING, PZ_MEETING], LAST, fake.steps())

    assert report.failed == 1
    assert "2026-11-03 City Council Regular Session: failed: meeting run failed" in report.lines
    assert "2026-11-05 Planning and Zoning Commission: done" in report.lines
    pz_run = [argv for step, argv in fake.calls if step == "meeting"][1]
    assert _opt(pz_run, "--roster").endswith("planning-and-zoning-commission.json")
    assert "--agenda" not in pz_run

    rerun = Fake()
    report = run_week(settings, [COUNCIL_MEETING, PZ_MEETING], LAST, rerun.steps())
    assert report.failed == 0
    assert [argv[1] for step, argv in rerun.calls if step == "meeting"] == ["oWZpzjzjwnE"]
    assert "2026-11-05 Planning and Zoning Commission: done in an earlier run" in report.lines


def test_a_failed_story_does_not_redo_the_steps_before_it(tmp_path):
    settings = _settings(tmp_path)
    fake = Fake(fail={"article": 1})
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    assert report.failed == 1
    assert "failed: article write failed (no story was written)" in report.lines[0]

    rerun = Fake()
    run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())
    assert rerun.names() == [("article", "write"), ("article", "stage")]


def test_meetings_that_cannot_run_are_listed_and_run_nothing(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)  # no roster for the commission
    cancelled = Found(
        COUNCIL, date(2026, 11, 10), "CANCELLED", "p", reason="the meeting was cancelled"
    )
    report = run_week(settings, [cancelled, PZ_MEETING], LAST, fake.steps())

    assert "2026-11-10 CANCELLED: not run: the meeting was cancelled" in report.lines
    assert any(
        "failed: no roster for Planning and Zoning Commission "
        "(add rosters/planning-and-zoning-commission.json)" in line
        for line in report.lines
    )
    assert fake.calls == []
    assert report.failed == 1


def test_two_meetings_make_a_roundup_with_their_recaps_in_date_order(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    report = run_week(settings, [PZ_MEETING, COUNCIL_MEETING], LAST, fake.steps())

    assert "roundup: done (2 meetings)" in report.lines
    roundup = next(argv for step, argv in fake.calls if step == "script" and argv[0] == "roundup")
    recaps = [roundup[i + 1] for i, a in enumerate(roundup) if a == "--recap"]
    assert [Path(r).parent.name for r in recaps] == [
        "2026-11-03-city-council-owzpzjzjwne",
        "2026-11-05-planning-and-zoning-commission-pzpzpzpzpz1",
    ]
    tail = fake.names()[-4:]
    assert tail == [
        ("script", "roundup"),
        ("script", "write"),
        ("article", "title"),
        ("episode", "stage"),
    ]
    assert _opt(fake.calls[-1][1], "--meeting") == str(settings.work / "2026-11-10-roundup")


def test_publish_builds_each_published_episode_once_with_its_story(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "oWZpzjzjwnE"},
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"},
        "0003-episode": {"kind": "episode", "status": "published", "meeting_id": "OLDOLDOLD01"},
    }
    report = run_week(
        settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00"
    )

    assert report.failed == 0
    assert "OLDOLDOLD01: published, but no run of it is in this work folder" in report.lines
    assert "oWZpzjzjwnE: built episode ep-oWZpzjzjwnE" in report.lines
    assemble = next(
        argv for step, argv in fake.calls if step == "episode" and argv[0] == "assemble"
    )
    assert _opt(assemble, "--article") == str(settings.work / "exported" / "0002-article")
    assert _opt(assemble, "--published") == "2026-11-10T09:00:00-07:00"
    assert _opt(assemble, "--city") == "longmont"
    assert (site.episodes / "ep-oWZpzjzjwnE.json").is_file()
    assert sorted(p.name for p in site.media.iterdir()) == [
        "ep-oWZpzjzjwnE-chapters.json",
        "ep-oWZpzjzjwnE.mp3",
        "ep-oWZpzjzjwnE.vtt",
    ]
    assert fake.names()[-1] == ("site", "build")

    rerun = Fake()
    rerun.exported = fake.exported
    run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())
    assert rerun.names() == [("review", "export"), ("site", "build")]


def test_a_redrafted_episode_is_built_again_with_audio_from_the_desks_script(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    settings = _settings(tmp_path, site=site)
    item = {"kind": "episode", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    fake = Fake()
    fake.exported = {"0001-episode": {**item, "content_hash": "a" * 64, "script": "[S1] hi\n"}}
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    first_audio = next(argv for step, argv in fake.calls if step == "audio")

    # The editor redrafts the script on the desk and publishes it again.
    redrafted = "[CHAPTER Budget]\n[S1] The council passed the budget.\n[S2] Seven to zero.\n"
    again = Fake()
    again.exported = {"0001-episode": {**item, "content_hash": "b" * 64, "script": redrafted}}
    report = run_week(settings, [COUNCIL_MEETING], LAST, again.steps())

    assert "oWZpzjzjwnE: built episode ep-oWZpzjzjwnE" in report.lines
    audio = next(argv for step, argv in again.calls if step == "audio")
    assert Path(audio[0]).read_text(encoding="utf-8") == redrafted
    assert _opt(audio, "--out") != _opt(first_audio, "--out"), "not the old audio folder"
    assemble = next(
        argv for step, argv in again.calls if step == "episode" and argv[0] == "assemble"
    )
    assert _opt(assemble, "--audio") == _opt(audio, "--out")
    script = json.loads(Path(_opt(assemble, "--script")).read_text(encoding="utf-8"))
    assert [(line["speaker"], line["text"]) for line in script] == [
        ("CHAPTER", "Budget"),
        ("S1", "The council passed the budget."),
        ("S2", "Seven to zero."),
    ]


def test_the_meetings_documents_are_written_passed_to_the_story_and_reported(tmp_path):
    from dataclasses import replace

    data = {"format": 1, "documents": [{"kind": "packet", "url": "https://x.test/p", "pages": []}]}
    lines = ["packet: 38 pages", "minutes: draft minutes on pages 17-38 of the November 17 packet"]
    asked = []

    def documents():
        asked.append(1)
        return data, lines

    fake = Fake(fail={"article": 1})
    settings = _settings(tmp_path)
    meeting = replace(COUNCIL_MEETING, documents=documents)
    run_week(settings, [meeting], LAST, fake.steps())
    report = run_week(settings, [meeting], LAST, fake.steps())

    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    written = folder / "documents.json"
    assert json.loads(written.read_text(encoding="utf-8")) == data
    # Fetched once; the rerun after the failed story does not ask the portal again.
    assert asked == [1]
    articles = [argv for step, argv in fake.calls if step == "article"]
    assert [a[0] for a in articles] == ["write", "write", "stage"]
    assert all(_opt(a, "--documents") == str(written) for a in articles)
    assert report.lines[:3] == [
        "2026-11-03 City Council Regular Session: done",
        "2026-11-03 City Council Regular Session: packet: 38 pages",
        "2026-11-03 City Council Regular Session: " + lines[1],
    ]


def test_the_documents_come_before_the_recap_so_the_recap_can_read_names_from_them(tmp_path):
    from dataclasses import replace

    data = {"format": 1, "documents": [{"kind": "minutes", "url": "https://x.test/m", "pages": []}]}
    fake = Fake()
    seen = []
    meeting_main = fake.meeting

    def meeting(argv):
        documents = Path(_opt(argv, "--documents"))
        seen.append(json.loads(documents.read_text(encoding="utf-8")))
        return meeting_main(argv)

    fake.meeting = meeting
    settings = _settings(tmp_path)
    run_week(settings, [replace(COUNCIL_MEETING, documents=lambda: (data, []))], LAST, fake.steps())
    assert seen == [data]
    # A meeting with no portal documents runs without the flag.
    other = Fake()
    run_week(_settings(tmp_path / "b"), [COUNCIL_MEETING], LAST, other.steps())
    [argv] = [a for step, a in other.calls if step == "meeting"]
    assert "--documents" not in argv


def test_nothing_published_yet_waits_for_review_and_is_not_a_failure(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    site.episodes.mkdir()
    (site.episodes / "city.json").write_text("{}", encoding="utf-8")
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {}  # every item is still on the desk for review
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert report.failed == 0
    assert ("site", "build") not in fake.names()
    assert "site: not built: no episode is published yet; approve items on the desk" in report.lines


def test_a_documents_fetch_that_breaks_is_reported_and_the_meeting_still_runs(tmp_path):
    from dataclasses import replace

    calls = []

    def documents():
        calls.append(1)
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    fake = Fake()
    settings = _settings(tmp_path)
    meeting = replace(COUNCIL_MEETING, documents=documents)
    report = run_week(settings, [meeting], LAST, fake.steps())

    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    assert not (folder / "documents.json").exists()
    assert "2026-11-03 City Council Regular Session: done" in report.lines
    assert (
        "2026-11-03 City Council Regular Session: documents not read: "
        "Cannot send a request, as the client has been closed." in report.lines
    )
    assert ("meeting", "run") in fake.names()
    assert calls == [1]


STORY = {
    "headline": "h",
    "dek": "",
    "sections": [{"heading": None, "paragraphs": [{"text": "p", "cites": []}]}],
    "votes": [],
    "quotes": [],
    "topics": [],
}


class DeskFake(Fake):
    """Stages on a real desk, the way the real episode and article stages do: a second
    stage of the same item fails with "already exists"."""

    def __init__(self, db, crash_on=None):
        super().__init__()
        self.db, self.crash_on = db, crash_on

    def _stage(self, kind, argv):
        from civiccast_brief.review import desk
        from civiccast_brief.review.store import ReviewError

        if kind == self.crash_on:
            raise RuntimeError("the run stopped here")
        name = "--meeting" if kind == "episode" else "--recap"
        folder = Path(_opt(argv, name))
        folder = folder if folder.is_dir() else folder.parent
        recap = json.loads((folder / "recap.json").read_text(encoding="utf-8"))
        try:
            desk.stage_item(
                self.db, kind=kind, meeting_id=recap["video_id"], title="t",
                meeting_date="2026-11-03", body=COUNCIL, checks_passed=True,
                article=STORY if kind == "article" else None,
            )  # fmt: skip
        except ReviewError:
            return 1
        return 0

    def episode(self, argv):
        if argv[0] == "stage":
            self.calls.append(("episode", list(argv)))
            return self._stage("episode", argv)
        return super().episode(argv)

    def article(self, argv):
        if argv[0] == "stage":
            self.calls.append(("article", list(argv)))
            return self._stage("article", argv)
        return super().article(argv)


def test_a_run_that_stopped_after_staging_resumes_without_staging_again(tmp_path):
    settings = _settings(tmp_path)
    crashed = DeskFake(settings.db, crash_on="article")
    try:
        run_week(settings, [COUNCIL_MEETING], LAST, crashed.steps())
    except RuntimeError:
        pass  # the run stopped after the episode went on the desk, before progress was saved

    rerun = DeskFake(settings.db)
    report = run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())

    assert report.failed == 0, report.lines
    assert "2026-11-03 City Council Regular Session: done" in report.lines
    assert ("episode", "stage") not in rerun.names()  # the episode on the desk is kept
    assert ("article", "stage") in rerun.names()


def test_a_story_already_on_the_desk_is_not_written_again(tmp_path):
    settings = _settings(tmp_path)
    first = DeskFake(settings.db)
    run_week(settings, [COUNCIL_MEETING], LAST, first.steps())
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    for entry in state["meetings"].values():  # as if the run stopped before saving
        entry.update(status="", done=["recap", "script"])
    (settings.work / STATE_NAME).write_text(json.dumps(state), encoding="utf-8")

    rerun = DeskFake(settings.db)
    report = run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())

    assert report.failed == 0, report.lines
    assert rerun.names() == []


def _site(tmp_path):
    return Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )


def test_a_published_story_reaches_the_site_with_no_published_episode(tmp_path):
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0002-article": {
            "kind": "article",
            "status": "published",
            "meeting_id": "oWZpzjzjwnE",
            "content_hash": "a" * 64,
        }
    }
    report = run_week(
        settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00"
    )

    assert report.failed == 0
    assert "oWZpzjzjwnE: built story owzpzjzjwne.json" in report.lines
    story = next(a for step, a in fake.calls if step == "episode" and a[0] == "story")
    assert _opt(story, "--article") == str(settings.work / "exported" / "0002-article")
    assert _opt(story, "--city") == "longmont"
    assert (site.episodes / "stories" / "owzpzjzjwne.json").is_file()
    assert not any(a[0] == "assemble" for step, a in fake.calls if step == "episode")
    # The site is built (no "nothing published" stop) and fetches the meeting stills.
    build = fake.calls[-1]
    assert build[0] == "site" and "--stills" in build[1]

    again = Fake()
    again.exported = fake.exported
    run_week(settings, [COUNCIL_MEETING], LAST, again.steps())
    assert again.names() == [("review", "export"), ("site", "build")]  # not made twice


def test_a_story_no_longer_published_leaves_the_site(tmp_path):
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    story_file = site.episodes / "stories" / "owzpzjzjwne.json"
    assert story_file.is_file()

    # The real export removes the folder of an item that is no longer published.
    shutil.rmtree(settings.work / "exported")
    held = Fake()
    report = run_week(settings, [COUNCIL_MEETING], LAST, held.steps())
    assert not story_file.exists()
    assert "owzpzjzjwne: story is no longer published; removed from the site" in report.lines


def test_one_meeting_published_twice_reaches_the_site_once(tmp_path):
    # Longmont uploaded the Nov 3 session twice; both videos were published on the desk and
    # their stories got the same id, which the site build refuses.
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    twice = Found(COUNCIL, date(2026, 11, 3), "City Council Regular Session", "p", "Q3Cpd52hWa4")
    fake = Fake()
    fake.exported = {
        f"000{n}-article": {"kind": "article", "status": "published", "meeting_id": video}
        for n, video in ((2, "oWZpzjzjwnE"), (3, "Q3Cpd52hWa4"))
    }
    story = fake.episode

    def same_id(argv):
        code = story(argv)
        if argv[0] == "story":
            Path(_opt(argv, "--out")).write_text(json.dumps({"id": "2026-11-03-council"}))
        return code

    fake.episode = same_id
    report = run_week(settings, [COUNCIL_MEETING, twice], LAST, fake.steps())

    stories = sorted(p.name for p in (site.episodes / "stories").glob("*.json"))
    assert stories == ["owzpzjzjwne.json"]
    assert report.failed == 1
    assert any(
        line.startswith("q3cpd52hwa4: same meeting as owzpzjzjwne (story 2026-11-03-council)")
        and "Kill one copy on the desk" in line
        for line in report.lines
    ), report.lines
    assert fake.calls[-1][0] == "site"  # the site is still built


def test_a_meeting_posted_twice_is_in_the_roundup_once(tmp_path):
    """Council's November 3 meeting has two videos; the roundup took both."""
    twice = Found(COUNCIL, date(2026, 11, 3), "City Council Regular Session", "p", "Q3Cpd52hWa4")
    fake = Fake()
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    report = run_week(settings, [COUNCIL_MEETING, twice, PZ_MEETING], LAST, fake.steps())

    assert "roundup: done (2 meetings)" in report.lines
    roundup = next(argv for step, argv in fake.calls if step == "script" and argv[0] == "roundup")
    assert roundup.count("--recap") == 2


def test_next_weeks_roundup_leaves_out_what_this_weeks_covered(tmp_path):
    """The run looks back 14 days, so each week's roundup repeated the week before."""
    fake = Fake()
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    run_week(settings, [COUNCIL_MEETING, PZ_MEETING], LAST, fake.steps())
    later = Found(COUNCIL, date(2026, 11, 12), "City Council Regular Session", "p", "NEWNEWNEW01")
    fake.calls.clear()
    report = run_week(settings, [PZ_MEETING, later], date(2026, 11, 17), fake.steps())

    assert "roundup: not made (1 meeting(s) done this week; it needs 2)" in report.lines


def test_an_episode_killed_on_the_desk_leaves_the_site(tmp_path):
    """Killing a published episode took its story off the site but left the episode there."""
    import shutil

    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    settings = _settings(tmp_path, site=site)
    (site.episodes).mkdir(parents=True)
    (site.episodes / "older-episode.json").write_text("{}", encoding="utf-8")
    fake = Fake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    assert (site.episodes / "ep-oWZpzjzjwnE.json").is_file()

    shutil.rmtree(settings.work / "exported")  # the real export drops what is not published
    killed = Fake()
    killed.exported = {}
    report = run_week(settings, [COUNCIL_MEETING], LAST, killed.steps())

    assert "oWZpzjzjwnE: no longer published; episode ep-oWZpzjzjwnE removed" in report.lines
    assert sorted(p.name for p in site.episodes.glob("*.json")) == ["older-episode.json"]
    assert list(site.media.iterdir()) == []
