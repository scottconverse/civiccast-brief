"""The PC worker: the station's /worker API and the Windows PC's client, end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_station_publish import WED_NOON, Clock, Steps, station

from civiccast_brief.review.store import ReviewStore
from civiccast_brief.station import app as station_app
from civiccast_brief.station import worker as pc
from civiccast_brief.station.app import load_config, make_station_app
from civiccast_brief.weekly.worker import AUDIO, AUDIO_FILES, CAPTIONS, WorkerFolder

TOKEN = "worker-test-token"


@pytest.fixture
def setup(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    inbox = WorkerFolder(tmp_path / "worker")
    with ReviewStore(tmp_path / "desk.db") as store:
        s = station(tmp_path, clock, steps)
        app = make_station_app(store, s, worker=inbox, worker_token=TOKEN)
        client = TestClient(
            app, base_url="https://127.0.0.1", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        yield client, inbox, s, steps


def test_the_worker_api_needs_the_token(setup):
    client, inbox, _, _ = setup
    inbox.want_captions("oWZpzjzjwnE")
    for headers in ({"Authorization": ""}, {"Authorization": "Bearer wrong"}):
        assert client.get("/worker/jobs", headers=headers).status_code == 401
        put = client.put(
            "/worker/captions/oWZpzjzjwnE/oWZpzjzjwnE.vtt", content=b"x", headers=headers
        )
        assert put.status_code == 401
        assert client.post("/worker/captions/oWZpzjzjwnE/done", headers=headers).status_code == 401
    assert inbox.ready(CAPTIONS, "oWZpzjzjwnE") is None
    assert client.get("/worker/jobs").json()[CAPTIONS][0]["video_id"] == "oWZpzjzjwnE"


def test_arrived_captions_start_the_job_and_arrived_audio_queues_a_publish(setup):
    client, inbox, s, steps = setup
    inbox.want_captions("oWZpzjzjwnE")
    inbox.want_audio("k1", "[S1] hi\n", "")

    bad = client.put("/worker/captions/oWZpzjzjwnE/other.vtt", content=b"x")
    assert bad.status_code == 400 and "must start with oWZpzjzjwnE" in bad.json()["error"]
    early = client.post("/worker/captions/oWZpzjzjwnE/done")
    assert early.status_code == 400 and "no caption file" in early.json()["error"]

    put = client.put("/worker/captions/oWZpzjzjwnE/oWZpzjzjwnE.en.srv3", content=b"<timedtext/>")
    assert put.status_code == 200 and put.json()["bytes"] == 12
    assert client.post("/worker/captions/oWZpzjzjwnE/done").status_code == 200
    s.join_workers()
    assert steps.ran[:2] == ["find", "make"], "the weekly job ran again"
    assert inbox.ready(CAPTIONS, "oWZpzjzjwnE") is not None

    assert s.status().publish_at is None
    missing = client.post("/worker/audio/k1/done")
    assert missing.status_code == 400 and "episode.mp3" in missing.json()["error"]
    for name in AUDIO_FILES:
        assert client.put(f"/worker/audio/k1/{name}", content=name.encode()).status_code == 200
    assert client.post("/worker/audio/k1/done").status_code == 200
    assert s.status().publish_at is not None
    assert client.get("/worker/jobs").json() == {CAPTIONS: [], AUDIO: []}


def test_captions_that_arrive_during_a_run_are_sent_again_later(setup):
    client, inbox, s, steps = setup
    inbox.want_captions("oWZpzjzjwnE")
    client.put("/worker/captions/oWZpzjzjwnE/oWZpzjzjwnE.vtt", content=b"WEBVTT")
    answers = []
    steps.during["find"] = lambda: answers.append(client.post("/worker/captions/oWZpzjzjwnE/done"))
    s.run_once("manual")
    assert answers[0].status_code == 409
    assert inbox.jobs()[CAPTIONS], "still wanted, so the worker sends done again"
    assert client.post("/worker/captions/oWZpzjzjwnE/done").status_code == 200


def test_the_pc_worker_fetches_captions_makes_audio_and_sends_both(setup, tmp_path):
    client, inbox, s, _ = setup
    inbox.want_captions("oWZpzjzjwnE")
    inbox.want_audio("k1", "[S1] The council met.\n", "vibevoice")
    captured, spoken, said = [], [], []

    def capture(video_id: str, folder: Path) -> None:
        captured.append(video_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{video_id}.en.srv3").write_text("<timedtext/>", encoding="utf-8")
        (folder / f"{video_id}.info.json").write_text("{}", encoding="utf-8")

    def speak(argv: list[str]) -> int:
        spoken.append(argv)
        out = Path(argv[argv.index("--out") + 1])
        for name in AUDIO_FILES:
            (out / name).write_text(f"made {name}", encoding="utf-8")
        return 0

    code = pc.run_worker(
        "https://127.0.0.1", TOKEN, tmp_path / "pc", said.append,
        once=True, client=client, capture=capture, speak=speak,
    )  # fmt: skip
    s.join_workers()

    assert code == 0, said
    assert captured == ["oWZpzjzjwnE"]
    [argv] = spoken
    assert Path(argv[0]).read_text(encoding="utf-8") == "[S1] The council met.\n"
    assert argv[-2:] == ["--engine", "vibevoice"]
    got = inbox.ready(CAPTIONS, "oWZpzjzjwnE")
    assert got is not None and (got / "oWZpzjzjwnE.info.json").is_file()
    audio = inbox.ready(AUDIO, "k1")
    assert audio is not None and (audio / "episode.mp3").read_text() == "made episode.mp3"
    assert s.status().publish_at is not None
    assert "sent captions oWZpzjzjwnE" in said and "sent audio k1" in said


def test_a_failed_audio_job_is_reported_and_the_worker_says_so(setup, tmp_path):
    client, inbox, _, _ = setup
    inbox.want_audio("k1", "[S1] hi\n", "")
    said: list[str] = []
    code = pc.run_worker(
        "https://127.0.0.1", TOKEN, tmp_path / "pc", said.append,
        once=True, client=client, speak=lambda argv: 1,
    )  # fmt: skip
    assert code == 1
    assert any(line.startswith("failed: audio k1: the audio command failed") for line in said)
    assert inbox.jobs()[AUDIO], "still wanted"


def test_station_json_takes_a_worker_folder_and_needs_the_token(tmp_path, monkeypatch, capsys):
    config = {
        "db": "desk.db",
        "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
        "job": [["echo", "hi"]],
        "worker": "worker",
    }
    path = tmp_path / "station.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert load_config(path).worker == (tmp_path / "worker").resolve()

    monkeypatch.setattr(pc, "worker_token", lambda: None)
    assert station_app.main(["serve", "--config", str(path)]) == 1
    assert "needs the worker token" in capsys.readouterr().err
    assert station_app.main(["worker", "--station", "https://desk.example.org", "--once"]) == 1
    assert "worker token" in capsys.readouterr().err
    assert station_app.main(["worker", "--station", "http://desk.example.org", "--once"]) == 1
    assert "https://" in capsys.readouterr().err
