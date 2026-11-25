"""The weekly run with stand-in steps: each one writes the files the real one writes."""

import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

from civiccast_brief.weekly.find import Agenda, Found
from civiccast_brief.weekly.run import STATE_NAME, Settings, Site, Steps, run_week

COUNCIL = "City Council"
PZ = "Planning and Zoning Commission"
LAST = date(2026, 11, 10)


def _opt(argv, name):
    return argv[argv.index(name) + 1]


def _positional(argv):
    return argv[argv.index("--") + 1]


class Fake:
    """Stand-in mains. ``fail`` maps a step name to how many times it fails first."""

    def __init__(self, fail=None, claims=1):
        self.calls = []
        self.claims = claims
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
        video = _positional(argv)
        claims = [{"id": f"c{n:02d}", "text": "A fact."} for n in range(1, self.claims + 1)]
        recap = {
            "body": "x",
            "meeting_date": "November 3, 2026",
            "video_id": video,
            "claims": claims,
        }
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
        slug = _opt(argv, "--slug") if "--slug" in argv else None
        if argv[0] == "assemble":
            meeting_id = json.loads((Path(_opt(argv, "--item")) / "item.json").read_text())[
                "meeting_id"
            ]
            name = slug or f"ep-{meeting_id}"
            Path(_opt(argv, "--out")).write_text(json.dumps({"id": name}), encoding="utf-8")
        if argv[0] == "story":
            meeting_id = json.loads((Path(_opt(argv, "--article")) / "item.json").read_text())[
                "meeting_id"
            ]
            name = slug or f"st-{meeting_id}"
            Path(_opt(argv, "--out")).write_text(json.dumps({"id": name}))
        return 1 if self._failing("episode") else 0

    def article(self, argv):
        self.calls.append(("article", list(argv)))
        if argv[0] in ("write", "skill", "from-stories"):
            out = Path(_opt(argv, "--out"))
            if not self._failing("article"):
                out.with_suffix(".json").write_text("{}", encoding="utf-8")
                if argv[0] != "write":
                    # The skill writes the evidence its story rests on beside the meeting.
                    Path(_opt(argv, "--recap")).with_name("story-recap.json").write_text(
                        "{}", encoding="utf-8"
                    )
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


def test_review_only_adds_the_flag_to_the_episode_and_article_stage_calls(tmp_path):
    """The desk's Test one meeting runs each stage for review; the weekly job does not."""
    fake = Fake()
    settings = replace(_settings(tmp_path), review_only=True)
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    stages = [argv for step, argv in fake.calls if argv[0] == "stage"]
    assert [step for step, argv in fake.calls if argv[0] == "stage"] == ["episode", "article"]
    assert all("--review-only" in argv for argv in stages)


def test_without_review_only_the_stage_calls_have_no_such_flag(tmp_path):
    fake = Fake()
    run_week(_settings(tmp_path), [COUNCIL_MEETING], LAST, fake.steps())
    stages = [argv for step, argv in fake.calls if argv[0] == "stage"]
    assert stages and all("--review-only" not in argv for argv in stages)


def test_one_meeting_runs_every_step_in_order_with_its_roster_and_agenda(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert report.failed == 0
    assert fake.names() == [
        ("meeting", "run"),
        ("script", "write"),
        ("episode", "stage"),
        ("article", "skill"),
        ("article", "stage"),
    ]
    run_argv = fake.calls[0][1]
    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    assert _positional(run_argv) == "oWZpzjzjwnE"
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


def test_a_regular_meeting_script_is_written_with_its_body_roster(tmp_path):
    """The meeting path passes its body, so a bare first name the claims carry can print in
    full. The roundup path has no single body and stays without a roster."""
    fake = Fake()
    settings = _settings(tmp_path)
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert report.failed == 0
    write_argv = next(argv for step, argv in fake.calls if step == "script" and argv[0] == "write")
    assert _opt(write_argv, "--roster") == str(settings.city / "roster.json")


def test_a_leading_dash_video_id_is_passed_after_the_option_terminator(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    meeting = Found(
        COUNCIL,
        date(2026, 11, 3),
        "City Council Regular Session",
        "p",
        "-kPuLsOsvhk",
    )

    report = run_week(settings, [meeting], LAST, fake.steps())

    assert report.failed == 0
    run_argv = next(argv for step, argv in fake.calls if step == "meeting")
    assert run_argv[-2:] == ["--", "-kPuLsOsvhk"]


def test_the_meeting_parser_accepts_a_leading_dash_video_id_after_double_dash():
    from civiccast_brief.meeting.cli import build_parser

    args = build_parser().parse_args(["run", "--out", "out", "--", "-abc123def45"])

    assert args.video_id == "-abc123def45"


def test_a_dateless_video_runs_on_the_date_found_for_its_meeting(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path, rosters=("Board of Trustees",))
    meeting = Found(
        "Board of Trustees",
        date(2026, 11, 2),
        "Board of Trustees Budget Workshop",
        "civicplus:www.townoflyons.com",
        "LLLLLLLLLLL",
    )

    report = run_week(settings, [meeting], LAST, fake.steps())

    assert report.failed == 0
    run_argv = fake.calls[0][1]
    assert _opt(run_argv, "--meeting-date") == "November 2, 2026"
    assert Path(_opt(run_argv, "--out")).name == ("2026-11-02-board-of-trustees-lllllllllll")


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
    assert [_positional(argv) for step, argv in rerun.calls if step == "meeting"] == ["oWZpzjzjwnE"]
    assert "2026-11-05 Planning and Zoning Commission: done in an earlier run" in report.lines


class NotStartedFake(Fake):
    """The first meeting run says the video has not started; the next one runs normally."""

    def __init__(self):
        super().__init__()
        self.not_started = 1

    def meeting(self, argv):
        if self.not_started:
            self.not_started -= 1
            self.calls.append(("meeting", list(argv)))
            return 3
        return super().meeting(argv)


def test_a_live_event_that_has_not_started_is_not_run_and_is_retried_later(tmp_path):
    fake = NotStartedFake()
    settings = _settings(tmp_path)
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert report.failed == 0
    assert (
        "2026-11-03 City Council Regular Session: not run: "
        "the video is a live event that has not started yet" in report.lines
    )
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert state["meetings"][COUNCIL_MEETING.key]["status"] == "not run"
    # Nothing after the meeting ran.
    assert fake.names() == [("meeting", "run")]

    # The next week picks the video up again now that it has aired.
    again = Fake()
    report = run_week(settings, [COUNCIL_MEETING], LAST, again.steps())
    assert report.failed == 0
    assert "2026-11-03 City Council Regular Session: done" in report.lines
    assert [step for step, _ in again.calls if step == "meeting"] == ["meeting"]


def test_a_failed_story_does_not_redo_the_steps_before_it(tmp_path):
    settings = _settings(tmp_path)
    fake = Fake(fail={"article": 1})
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    assert report.failed == 1
    assert "failed: article skill failed (no story was written)" in report.lines[0]

    rerun = Fake()
    run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())
    assert rerun.names() == [("article", "skill"), ("article", "stage")]


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
    tail = fake.names()[-6:]
    assert tail == [
        ("script", "roundup"),
        ("script", "write"),
        ("article", "title"),
        ("article", "from-stories"),
        ("article", "stage"),
        ("episode", "stage"),
    ]
    assert _opt(fake.calls[-1][1], "--meeting") == str(settings.work / "2026-11-10-roundup")
    # The site's city goes with the recaps, so a board it shares from another city counts.
    assert _opt(roundup, "--city") == str(settings.city)


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
    # Nothing is built again; the built episode and story only get the current name fixes.
    assert rerun.names() == [
        ("review", "export"),
        ("episode", "names"),
        ("episode", "names"),
        ("site", "build"),
    ]
    refreshed = [_opt(a, "--file") for step, a in rerun.calls if a[0] == "names"]
    assert refreshed == [
        str(site.episodes / "ep-oWZpzjzjwnE.json"),
        str(site.episodes / "stories" / "owzpzjzjwne.json"),
    ]


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
    assert [a[0] for a in articles] == ["skill", "skill", "stage"]
    assert all(_opt(a, "--documents") == str(written) for a in articles)
    # The stage check needs the roster, as the write check does: without it a seat is named
    # from the packet's surname alone and the printed name is repaired twice.
    assert all(_opt(a, "--city") == str(settings.city) for a in articles)
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
    # Not made twice; the built story only gets the current name fixes.
    assert again.names() == [("review", "export"), ("episode", "names"), ("site", "build")]


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


class _SameDayFake(Fake):
    """A run whose meetings all carry one body: Longmont's Board of Education, Nov 11 2026."""

    def meeting(self, argv):
        code = super().meeting(argv)
        out = Path(_opt(argv, "--out"))
        recap = json.loads((out / "recap.json").read_text(encoding="utf-8"))
        recap["body"] = "Board of Education"
        (out / "recap.json").write_text(json.dumps(recap), encoding="utf-8")
        return code


def test_two_meetings_of_one_body_on_one_day_get_their_own_ids(tmp_path):
    # Nov 11 2026: a Regular Meeting and a Study Session, two Vimeo videos of one board.
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    regular = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Regular Meeting", "p", "VID00000002"
    )
    study = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Study Session (Audio Only)", "p",
        "VID00000003",
    )  # fmt: skip
    fake = _SameDayFake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000002"},
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "VID00000002"},
        "0003-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000003"},
        "0004-article": {"kind": "article", "status": "published", "meeting_id": "VID00000003"},
    }
    report = run_week(
        settings, [regular, study], LAST, fake.steps(), published="2026-11-11T09:00:00-07:00"
    )

    assert report.failed == 0, report.lines
    assert not any("same meeting as" in line for line in report.lines), report.lines
    base = "2026-11-03-longmont-board-of-education"  # the fake's meeting date
    assembled = {_opt(a, "--item"): _opt(a, "--slug") for s, a in fake.calls if a[0] == "assemble"}
    assert assembled == {
        str(settings.work / "exported" / "0001-episode"): base,
        str(settings.work / "exported" / "0003-episode"): f"{base}-study-session",
    }
    assert sorted(p.name for p in site.episodes.glob("2026-*.json")) == [
        f"{base}-study-session.json",
        f"{base}.json",
    ]
    stories = {
        json.loads(p.read_text(encoding="utf-8"))["id"]
        for p in (site.episodes / "stories").glob("*.json")
    }
    assert stories == {base, f"{base}-study-session"}  # both stories, two ids, none dropped

    # A second run leaves both ids alone: neither is built again.
    again = _SameDayFake()
    again.exported = fake.exported
    second = run_week(settings, [regular, study], LAST, again.steps())
    assert second.failed == 0, second.lines
    assert not any(a[0] in ("assemble", "story") for _s, a in again.calls)
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert state["built"]["VID00000002"] == base
    assert state["built"]["VID00000003"] == f"{base}-study-session"


def test_the_meeting_already_on_the_site_keeps_the_plain_id(tmp_path):
    """The built map decides which of a day's meetings keeps the unsuffixed id."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    regular = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Regular Meeting", "p", "VID00000002"
    )
    study = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Study Session", "p", "VID00000003"
    )
    settings.work.mkdir(parents=True)
    (settings.work / STATE_NAME).write_text(
        json.dumps(
            {
                "format": 1,
                "meetings": {},
                "roundups": {},
                "built": {"VID00000003": "2026-11-03-longmont-board-of-education"},
            }
        ),
        encoding="utf-8",
    )
    fake = _SameDayFake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000002"},
        "0003-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000003"},
    }
    run_week(settings, [regular, study], LAST, fake.steps())
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    base = "2026-11-03-longmont-board-of-education"
    assert state["built"]["VID00000003"] == base  # already on the site: keeps the plain id
    assert state["built"]["VID00000002"] == f"{base}-regular-meeting"


def test_two_meetings_built_under_one_id_before_the_fix_are_split(tmp_path):
    """The failed Nov 23 2026 stage left both meetings' episodes named by the one shared id."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    regular = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Regular Meeting", "p", "VID00000002"
    )
    study = Found(
        COUNCIL, date(2026, 11, 11), "Board of Education Study Session", "p", "VID00000003"
    )
    base = "2026-11-03-longmont-board-of-education"
    settings.work.mkdir(parents=True)
    (settings.work / STATE_NAME).write_text(
        json.dumps(
            {
                "format": 1,
                "meetings": {},
                "roundups": {},
                "built": {"VID00000002": base, "VID00000003": base},
            }
        ),
        encoding="utf-8",
    )
    fake = _SameDayFake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000002"},
        "0003-episode": {"kind": "episode", "status": "published", "meeting_id": "VID00000003"},
    }
    report = run_week(settings, [regular, study], LAST, fake.steps())

    assert report.failed == 0, report.lines
    # Both are built again, so the shared file cannot keep the wrong meeting's episode.
    assembled = sorted(_opt(a, "--slug") for _s, a in fake.calls if a[0] == "assemble")
    assert assembled == [base, f"{base}-study-session"]
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert state["built"] == {"VID00000002": base, "VID00000003": f"{base}-study-session"}


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


def test_a_killed_copy_that_shares_an_episode_name_does_not_stay_on_the_site(tmp_path):
    """Live on 13 November 2026: two videos of the 5 November preservation commission built the
    same episode file; the second was killed on the desk and its episode stayed on the site,
    because the copy left published was not built again."""
    from civiccast_brief.weekly.run import State, _drop_unpublished_episodes

    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    site.episodes.mkdir()
    (site.episodes / "2026-11-05-hpc.json").write_text('{"video": "killed"}', encoding="utf-8")
    state = State(tmp_path / STATE_NAME)
    state.data["built"] = {"kqArjgcjLF0": "2026-11-05-hpc", "yK8bes8CYTM": "2026-11-05-hpc"}
    state.data["built_from"] = {"kqArjgcjLF0": "hash-kept", "yK8bes8CYTM": "hash-killed"}
    items = [
        (tmp_path, {"kind": "episode", "status": "published", "meeting_id": "kqArjgcjLF0"}),
    ]

    _drop_unpublished_episodes(site, state, items)

    assert state.data["built"] == {"kqArjgcjLF0": "2026-11-05-hpc"}
    # The kept copy is built again, so its episode replaces the killed one's in the file.
    assert "kqArjgcjLF0" not in state.data["built_from"]


def test_a_meeting_run_before_keeps_its_run_when_it_would_not_be_run_now(tmp_path):
    """Live on 13 November 2026: a preservation commission video already run was found again as
    the second video of its meeting; its entry became 'not run' and its published episode could
    not be built ("no run of it is in this work folder")."""
    from dataclasses import replace

    settings = _settings(tmp_path)
    run_week(settings, [COUNCIL_MEETING], LAST, Fake().steps())
    second = replace(COUNCIL_MEETING, reason="another video of this meeting runs (XXXXXXXXXXX)")
    report = run_week(settings, [second], LAST, Fake().steps())

    entry = json.loads((settings.work / STATE_NAME).read_text())["meetings"][COUNCIL_MEETING.key]
    assert entry["status"] == "done"
    assert f"{COUNCIL_MEETING.day} {COUNCIL_MEETING.title}: done in an earlier run" in report.lines


def test_publish_site_builds_the_site_from_the_desk_without_running_any_meeting(tmp_path):
    from civiccast_brief.weekly.run import publish_site

    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    # The weekly run made the story; nothing is published yet, so no site.
    run_week(settings, [COUNCIL_MEETING], LAST, Fake().steps())
    assert not list(site.episodes.glob("stories/*.json"))

    # The editor publishes it on the desk; the publish run builds the site and runs no meeting.
    fake = Fake()
    fake.exported = {
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }
    report = publish_site(settings, fake.steps(), published="2026-11-13T15:00:00-07:00")
    assert report.failed == 0, report.lines
    assert "oWZpzjzjwnE: built story owzpzjzjwne.json" in report.lines
    assert not any(step in ("meeting", "script", "audio", "article") for step, _ in fake.calls)
    assert fake.names()[0] == ("review", "export")
    assert fake.names()[-1] == ("site", "build")
    assert (site.episodes / "stories" / "owzpzjzjwne.json").is_file()


def test_the_site_build_reads_the_city_settings_from_the_episodes_folder(tmp_path):
    """The site build reads city.json from the episodes folder, so the run puts the city
    folder's copy there. Erie's 2026-11-16 run failed the site build without it."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    city = {"slug": "longmont", "site_title": "CivicCast Brief: Longmont"}
    (settings.city / "city.json").write_text(json.dumps(city), encoding="utf-8")
    fake = Fake()
    fake.exported = {
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }

    report = run_week(
        settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00"
    )

    assert report.failed == 0, report.lines
    assert json.loads((site.episodes / "city.json").read_text(encoding="utf-8")) == city


def test_the_real_site_build_succeeds_with_the_city_settings_the_run_put_there(
    tmp_path, monkeypatch
):
    """The real site CLI builds from the episodes folder the run filled, with no network."""
    from civiccast_brief.site import cli as site_cli
    from civiccast_brief.site import stills
    from civiccast_brief.weekly.run import publish_site

    monkeypatch.setattr(stills, "download", lambda video_id: None)
    fixtures = Path(__file__).parent.parent / "site" / "fixtures" / "episodes"
    city = json.loads((fixtures / "city.json").read_text(encoding="utf-8"))
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://example.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug=city["slug"],
    )
    settings = _settings(tmp_path, site=site)
    shutil.copytree(fixtures, site.episodes, ignore=shutil.ignore_patterns("city.json"))
    (settings.city / "city.json").write_text(json.dumps(city), encoding="utf-8")
    fake = Fake()
    steps = fake.steps()
    steps.site = site_cli.main

    report = publish_site(settings, steps, published="2026-11-10T09:00:00-07:00")

    assert report.failed == 0, report.lines
    assert (site.out / "index.html").is_file()


def test_the_roundup_gets_a_written_article_and_its_episode_is_built_with_it(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
    )
    settings = _settings(tmp_path, site=site, rosters=(COUNCIL, PZ))
    fake = Fake()
    run_week(settings, [PZ_MEETING, COUNCIL_MEETING], LAST, fake.steps())

    roundup_folder = settings.work / "2026-11-10-roundup"
    written = [
        argv
        for step, argv in fake.calls
        if step == "article"
        and argv[0] == "from-stories"
        and _opt(argv, "--out").startswith(str(roundup_folder))
    ]
    assert written and _opt(written[0], "--recap") == str(roundup_folder / "recap.json")
    staged = [
        argv
        for step, argv in fake.calls
        if step == "article"
        and argv[0] == "stage"
        and _opt(argv, "--db") == str(settings.db)
        and str(roundup_folder) in _opt(argv, "--article")
    ]
    assert staged

    roundup_id = "roundup November 3, 2026 and November 5, 2026"
    fake.exported = {
        "0001-article": {
            "kind": "article",
            "status": "published",
            "meeting_id": roundup_id,
        },
        "0002-episode": {"kind": "episode", "status": "published", "meeting_id": roundup_id},
    }
    report = run_week(
        settings,
        [PZ_MEETING, COUNCIL_MEETING],
        LAST,
        fake.steps(),
        published="2026-11-10T09:00:00-07:00",
    )
    assert report.failed == 0
    assemble = next(
        argv
        for step, argv in fake.calls
        if step == "episode"
        and argv[0] == "assemble"
        and _opt(argv, "--item") == str(settings.work / "exported" / "0002-episode")
    )
    assert _opt(assemble, "--article") == str(settings.work / "exported" / "0001-article")
    # The roundup has no stand-alone story file: its article renders on the roundup page.
    assert not any(
        step == "episode" and argv[0] == "story" and roundup_id in _opt(argv, "--article")
        for step, argv in fake.calls
    )


def test_a_recap_with_no_facts_makes_no_podcast_but_the_story_runs_and_the_recap_retries(
    tmp_path,
):
    # Boulder, Nov 15 2026: the recap failed in every window, and the podcast published was a
    # 24-second welcome and sign-off.
    settings = _settings(tmp_path)
    fake = Fake(claims=0)
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())
    assert report.failed == 1
    assert "the recap has no facts" in report.lines[0]
    assert ("script", "write") not in fake.names()
    assert ("episode", "stage") not in fake.names()
    assert ("article", "skill") in fake.names()

    rerun = Fake()
    report = run_week(settings, [COUNCIL_MEETING], LAST, rerun.steps())
    assert report.failed == 0
    names = rerun.names()
    assert names[0][0] == "meeting"
    assert ("script", "write") in names
    assert ("episode", "stage") in names
    assert ("article", "skill") not in names


def test_the_local_audio_step_gets_the_fallback_engine(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
        audio_engine="gemini",
        audio_fallback_engine="dia2",
    )
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0001-episode": {
            "kind": "episode",
            "status": "published",
            "meeting_id": "oWZpzjzjwnE",
            "content_hash": "a" * 64,
            "script": "[S1] hi\n",
        }
    }
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00")

    audio = next(argv for step, argv in fake.calls if step == "audio")
    assert _opt(audio, "--engine") == "gemini"
    assert _opt(audio, "--fallback-engine") == "dia2"


def test_a_documents_fetch_that_fails_is_not_marked_done_and_is_tried_again(tmp_path):
    """A portal failure must not be recorded as a finished step, and the recap must not be
    handed a documents.json that was never written."""
    from dataclasses import replace

    attempts = []

    def documents():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("portal answered 503")
        return {"format": 1, "documents": []}, ["packet: 1 page"]

    settings = _settings(tmp_path)
    meeting = replace(COUNCIL_MEETING, documents=documents)
    fake = Fake(fail={"article": 1})
    run_week(settings, [meeting], LAST, fake.steps())

    entry = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))["meetings"][
        COUNCIL_MEETING.key
    ]
    assert "documents" not in entry["done"]
    run_argv = next(argv for step, argv in fake.calls if step == "meeting")
    assert "--documents" not in run_argv

    rerun = Fake()
    report = run_week(settings, [meeting], LAST, rerun.steps())
    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    assert attempts == [1, 1]
    assert (folder / "documents.json").is_file()
    article = next(argv for step, argv in rerun.calls if step == "article" and argv[0] == "skill")
    assert _opt(article, "--documents") == str(folder / "documents.json")
    assert report.failed == 0, report.lines


def test_a_partial_audio_folder_is_rendered_again_and_a_missing_file_is_one_items_failure(tmp_path):
    """A render that stopped with chapters.json but no transcript.vtt must be redone, and a
    file missing during the copy is one item's logged failure, not the end of the run."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    run_week(settings, [COUNCIL_MEETING], LAST, Fake().steps())
    audio = settings.work / "2026-11-03-city-council-owzpzjzjwne" / "audio"
    audio.mkdir(parents=True)
    (audio / "episode.mp3").write_text("half", encoding="utf-8")
    (audio / "chapters.json").write_text("[]", encoding="utf-8")
    # no transcript.vtt

    fake = Fake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }

    def mp3_only(argv):
        fake.calls.append(("audio", list(argv)))
        out = Path(_opt(argv, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "episode.mp3").write_text("whole", encoding="utf-8")
        return 0

    fake.audio = mp3_only
    report = run_week(settings, [COUNCIL_MEETING], LAST, fake.steps())

    assert "audio" in [step for step, _ in fake.calls]  # the partial folder was rendered again
    assert report.failed == 1
    assert any(
        line.startswith("oWZpzjzjwnE:") and "transcript.vtt" in line for line in report.lines
    ), report.lines
    assert fake.calls[-1][0] == "site"  # the missing file did not stop the run


def test_a_truncated_episode_mp3_is_rendered_again_not_reused(tmp_path, monkeypatch):
    """The MP3 header still reports the whole length after a half copy, so presence alone must
    not make a truncated episode count as done."""
    from civiccast_brief.weekly import run as run_module

    monkeypatch.setattr(run_module, "probe_duration", lambda path: 480.0)
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    item = {
        "kind": "episode",
        "status": "published",
        "meeting_id": "oWZpzjzjwnE",
        "content_hash": "a" * 64,
        "script": "[S1] hi\n",
    }
    first = Fake()
    first.exported = {"0001-episode": item}
    run_week(
        settings, [COUNCIL_MEETING], LAST, first.steps(), published="2026-11-10T09:00:00-07:00"
    )
    assert [step for step, _ in first.names()].count("audio") == 1

    folder = settings.work / "2026-11-03-city-council-owzpzjzjwne"
    (folder / "audio" / "episode.mp3").write_bytes(b"x" * 1000)  # half a copy

    again = Fake()
    again.exported = {"0001-episode": {**item, "content_hash": "b" * 64}}
    run_week(
        settings, [COUNCIL_MEETING], LAST, again.steps(), published="2026-11-11T09:00:00-07:00"
    )

    assert [step for step, _ in again.names()].count("audio") == 1  # rendered, not reused


#: The two videos of Superior's Oct. 26 Town Council meeting, as the real state has them. The
#: episode file on the site is the second one's, and its story is the one the site must keep.
_TWO_VIDEOS = {
    "civicclerk-superiorco-43": "https://cpmedia.azureedge.net/superiorco/clip.mp4",
    "If5njrlpf7M": "https://www.youtube.com/watch?v=If5njrlpf7M",
}


def _two_video_fake(exported):
    """A stand-in whose episode and story files carry the video each was built from, as the
    real ones do, and whose two meetings build the same episode id."""
    fake = Fake()
    fake.exported = exported
    episode = fake.episode

    def same_id(argv):
        code = episode(argv)
        if argv[0] in ("assemble", "story"):
            meeting_id = json.loads(
                (
                    Path(_opt(argv, "--item" if argv[0] == "assemble" else "--article"))
                    / "item.json"
                ).read_text(encoding="utf-8")
            )["meeting_id"]
            Path(_opt(argv, "--out")).write_text(
                json.dumps(
                    {"id": "2026-10-26-superior-town-council", "video_url": _TWO_VIDEOS[meeting_id]}
                ),
                encoding="utf-8",
            )
        return code

    fake.episode = same_id
    return fake


def _two_video_meetings():
    first = Found(
        COUNCIL,
        date(2026, 10, 26),
        "Town Council",
        "civicclerk",
        "civicclerk-superiorco-43",
        media_url=_TWO_VIDEOS["civicclerk-superiorco-43"],
    )
    second = Found(COUNCIL, date(2026, 10, 26), "Town Council", "p", "If5njrlpf7M")
    return first, second


def test_the_site_keeps_the_story_of_the_video_the_episode_was_built_from(tmp_path):
    """Two videos of one meeting give two stories with one id, and both build the same episode
    name. The episode file on the site is the second video's, so its story must be the one kept
    (the first video's sorts first, which is how the real site ended up with the wrong one)."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    first, second = _two_video_meetings()
    # The desk item order has the CivicClerk video first, so the second (YouTube) episode is
    # written last over the same file name, as in the real saved output.
    fake = _two_video_fake(
        {
            "0001-episode": {
                "kind": "episode",
                "status": "published",
                "meeting_id": "civicclerk-superiorco-43",
            },
            "0002-article": {
                "kind": "article",
                "status": "published",
                "meeting_id": "civicclerk-superiorco-43",
            },
            "0003-episode": {
                "kind": "episode",
                "status": "published",
                "meeting_id": "If5njrlpf7M",
            },
            "0004-article": {
                "kind": "article",
                "status": "published",
                "meeting_id": "If5njrlpf7M",
            },
        }
    )
    report = run_week(settings, [first, second], LAST, fake.steps())

    built = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))["built"]
    assert built == {
        "civicclerk-superiorco-43": "2026-10-26-superior-town-council",
        "If5njrlpf7M": "2026-10-26-superior-town-council",
    }
    stories = sorted(p.name for p in (site.episodes / "stories").glob("*.json"))
    assert stories == ["if5njrlpf7m.json"]
    assert report.failed == 1  # the duplicate is still reported for the editor


def test_the_site_keeps_the_story_of_a_non_youtube_episode(tmp_path):
    """The episode's video_url can be a CivicClerk MP4. The story of that same video is kept,
    even when its file name sorts after the YouTube video's (so first-by-name would lose it)."""
    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    youtube = Found(COUNCIL, date(2026, 10, 26), "Town Council", "p", "If5njrlpf7M")
    mp4 = Found(
        COUNCIL,
        date(2026, 10, 26),
        "Town Council",
        "civicclerk",
        "zmcclip01",
        media_url="https://cpmedia.azureedge.net/superiorco/clip.mp4",
    )
    videos = {
        "If5njrlpf7M": "https://www.youtube.com/watch?v=If5njrlpf7M",
        "zmcclip01": "https://cpmedia.azureedge.net/superiorco/clip.mp4",
    }
    fake = Fake()
    fake.exported = {
        "0001-episode": {"kind": "episode", "status": "published", "meeting_id": "If5njrlpf7M"},
        "0002-article": {"kind": "article", "status": "published", "meeting_id": "If5njrlpf7M"},
        "0003-episode": {"kind": "episode", "status": "published", "meeting_id": "zmcclip01"},
        "0004-article": {"kind": "article", "status": "published", "meeting_id": "zmcclip01"},
    }
    episode = fake.episode

    def same_id(argv):
        code = episode(argv)
        if argv[0] in ("assemble", "story"):
            flag = "--item" if argv[0] == "assemble" else "--article"
            meeting_id = json.loads(
                (Path(_opt(argv, flag)) / "item.json").read_text(encoding="utf-8")
            )["meeting_id"]
            Path(_opt(argv, "--out")).write_text(
                json.dumps({"id": "2026-10-26-council", "video_url": videos[meeting_id]}),
                encoding="utf-8",
            )
        return code

    fake.episode = same_id
    run_week(settings, [youtube, mp4], LAST, fake.steps())

    stories = sorted(p.name for p in (site.episodes / "stories").glob("*.json"))
    assert stories == ["zmcclip01.json"]


def test_a_story_published_on_the_desk_reaches_the_site_when_its_meeting_failed(tmp_path):
    """The no-facts branch publishes the story and marks the meeting failed; that published
    story must still be built on the site."""
    from civiccast_brief.weekly.run import publish_site

    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    report = run_week(settings, [COUNCIL_MEETING], LAST, Fake(claims=0).steps())
    assert report.failed == 1
    entry = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))["meetings"][
        COUNCIL_MEETING.key
    ]
    assert entry["status"] == "failed"
    assert "article" in entry["done"]

    published = Fake()
    published.exported = {
        "0001-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }
    report = publish_site(settings, published.steps(), published="2026-11-13T15:00:00-07:00")

    assert report.failed == 0, report.lines
    assert (site.episodes / "stories" / "owzpzjzjwne.json").is_file()


def test_a_broken_transcript_json_does_not_stop_the_publish(tmp_path):
    """A failed meeting can leave a half-written transcript.json. Reading the run folders must
    skip that entry, not raise and end the publish before the site is built."""
    from civiccast_brief.weekly.run import publish_site

    site = _site(tmp_path)
    settings = _settings(tmp_path, site=site)
    run_week(settings, [COUNCIL_MEETING], LAST, Fake().steps())
    folder = next(settings.work.glob("2026-11-03-*"))
    (folder / "transcript.json").write_text('{"video_id": "oWZ', encoding="utf-8")  # truncated

    published = Fake()
    published.exported = {
        "0001-article": {"kind": "article", "status": "published", "meeting_id": "oWZpzjzjwnE"}
    }
    report = publish_site(settings, published.steps(), published="2026-11-13T15:00:00-07:00")

    assert report.failed == 0, report.lines
    assert any("no run of it is in this work folder" in line for line in report.lines)
    # The publish went on to the end: nothing published is left to build, so it says so.
    assert "site: not built: no episode is published yet; approve items on the desk" in report.lines


def test_no_fallback_engine_means_no_flag_on_the_audio_step(tmp_path):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
        audio_engine="gemini",
    )
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0001-episode": {
            "kind": "episode",
            "status": "published",
            "meeting_id": "oWZpzjzjwnE",
            "content_hash": "a" * 64,
            "script": "[S1] hi\n",
        }
    }
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00")

    audio = next(argv for step, argv in fake.calls if step == "audio")
    assert "--fallback-engine" not in audio


def test_limit_runs_only_the_newest_runnable_meeting_and_skips_the_roundup(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    cancelled = Found(
        PZ, date(2026, 11, 7), "Cancelled Meeting", "p", "CCCCCCCCCCC", reason="cancelled"
    )

    report = run_week(
        settings, [COUNCIL_MEETING, PZ_MEETING, cancelled], LAST, fake.steps(), limit=1
    )

    assert report.failed == 0
    assert [_positional(argv) for step, argv in fake.calls if step == "meeting"] == ["PZPZPZPZPZ1"]
    assert "roundup: skipped (--limit)" in report.lines
    assert not any(line.startswith("roundup: not made") for line in report.lines)
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert "youtube:oWZpzjzjwnE" not in state["meetings"]
    assert "youtube:CCCCCCCCCCC" not in state["meetings"]


def test_limit_counts_only_meetings_not_done_and_without_a_reason(tmp_path):
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))
    run_week(settings, [PZ_MEETING], LAST, Fake().steps())
    cancelled = Found(
        PZ, date(2026, 11, 7), "Cancelled Meeting", "p", "CCCCCCCCCCC", reason="cancelled"
    )

    fake = Fake()
    report = run_week(
        settings,
        [COUNCIL_MEETING, PZ_MEETING, cancelled],
        LAST,
        fake.steps(),
        limit=1,
    )

    assert [_positional(argv) for step, argv in fake.calls if step == "meeting"] == ["oWZpzjzjwnE"]
    assert "2026-11-05 Planning and Zoning Commission: done in an earlier run" not in report.lines
    assert "roundup: skipped (--limit)" in report.lines


def test_without_limit_every_meeting_and_the_roundup_run_as_before(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path, rosters=(COUNCIL, PZ))

    report = run_week(settings, [PZ_MEETING, COUNCIL_MEETING], LAST, fake.steps())

    assert [_positional(argv) for step, argv in fake.calls if step == "meeting"] == [
        "PZPZPZPZPZ1",
        "oWZpzjzjwnE",
    ]
    assert "roundup: skipped (--limit)" not in report.lines
    assert any(line.startswith("roundup:") and "skipped" not in line for line in report.lines)


def _published_episode(tmp_path, **site_kw):
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
        **site_kw,
    )
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0001-episode": {
            "kind": "episode",
            "status": "published",
            "meeting_id": "oWZpzjzjwnE",
            "content_hash": "a" * 64,
            "script": "[S1] hi\n",
        }
    }
    run_week(settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00")
    audio = next(argv for step, argv in fake.calls if step == "audio")
    return settings, audio


def test_the_voices_file_fills_in_the_engine_and_writes_the_audio_config(tmp_path):
    spoken = tmp_path / "voices.json"
    spoken.write_text(
        json.dumps(
            {
                "engine": "gemini",
                "fallback_engine": "lfm3",
                "audio": {"gemini": {"voice_s2": "Puck"}},
            }
        ),
        encoding="utf-8",
    )
    settings, audio = _published_episode(tmp_path, voices=spoken)

    assert _opt(audio, "--engine") == "gemini"
    assert _opt(audio, "--fallback-engine") == "lfm3"
    config = Path(_opt(audio, "--config"))
    assert config == settings.work / "audio-config.json"
    assert json.loads(config.read_text(encoding="utf-8")) == {"gemini": {"voice_s2": "Puck"}}


def test_the_audio_engine_flags_win_over_the_voices_file(tmp_path):
    spoken = tmp_path / "voices.json"
    spoken.write_text(
        json.dumps({"engine": "gemini", "fallback_engine": "lfm3", "audio": {}}),
        encoding="utf-8",
    )
    _, audio = _published_episode(
        tmp_path,
        voices=spoken,
        audio_engine="dia2",
        audio_fallback_engine="chatterbox",
    )

    assert _opt(audio, "--engine") == "dia2"
    assert _opt(audio, "--fallback-engine") == "chatterbox"
    assert "--config" in audio  # the voices file's audio part still applies


def test_no_voices_file_means_the_audio_argv_is_unchanged(tmp_path):
    _, audio = _published_episode(tmp_path, audio_engine="gemini")
    assert audio == [
        audio[0],
        "--out",
        _opt(audio, "--out"),
        "--engine",
        "gemini",
    ]
    assert "--config" not in audio and "--fallback-engine" not in audio
    assert not (tmp_path / "work" / "audio-config.json").exists()


def test_a_broken_voices_file_fails_the_site_steps_instead_of_using_defaults(tmp_path):
    spoken = tmp_path / "voices.json"
    spoken.write_text(json.dumps({"engine": "fake"}), encoding="utf-8")
    site = Site(
        episodes=tmp_path / "site-in",
        media=tmp_path / "media",
        out=tmp_path / "site",
        base_url="https://longmont.civiccastbrief.org",
        media_base_url="https://media.example.org",
        slug="longmont",
        voices=spoken,
    )
    settings = _settings(tmp_path, site=site)
    fake = Fake()
    fake.exported = {
        "0001-episode": {
            "kind": "episode",
            "status": "published",
            "meeting_id": "oWZpzjzjwnE",
            "content_hash": "a" * 64,
            "script": "[S1] hi\n",
        }
    }
    report = run_week(
        settings, [COUNCIL_MEETING], LAST, fake.steps(), published="2026-11-10T09:00:00-07:00"
    )
    assert report.failed
    assert any("voices:" in line for line in report.lines)
    assert "audio" not in [step for step, _ in fake.calls]
