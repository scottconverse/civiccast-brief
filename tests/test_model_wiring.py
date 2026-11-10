"""The recap, judge and script writers take the city's model picks (models.json)."""

import json

import pytest

from civiccast_brief import models
from civiccast_brief.meeting import cli as meeting_cli
from civiccast_brief.script import cli as script_cli
from civiccast_brief.script import llm

MEMBER = {"name": "Joan Peck", "first": "Joan", "last": "Peck", "role": "Mayor"}


def _city(tmp_path, body):
    city = tmp_path / "city"
    city.mkdir()
    (city / "city.json").write_text(json.dumps({"slug": "longmont", "name": "Longmont"}))
    roster = {"city": "Longmont", "body": body, "state": "Colorado", "members": [MEMBER]}
    (city / "roster.json").write_text(json.dumps(roster))
    return city


@pytest.fixture
def providers(monkeypatch):
    """Record which provider and model each writer was made for, and answer for it."""
    made = []

    def fake(provider):
        def make(model, *, task="", body="", **_):
            made.append((provider, model, task, body))
            return lambda prompt: (
                f"{provider} answer",
                models.ModelReceipt(provider, model, 0.0, task, body, ""),
            )

        return make

    monkeypatch.setattr(models, "claude_complete", fake("claude"))
    monkeypatch.setattr(models, "ollama_complete", fake("ollama"))
    return made


@pytest.fixture
def pipeline_run(monkeypatch):
    seen = {}

    def run(video_id, out, **kwargs):
        seen.update(kwargs)
        return {"video_id": video_id}

    monkeypatch.setattr(meeting_cli, "run", run)
    return seen


def test_council_recap_and_judge_use_claude_by_default(tmp_path, providers, pipeline_run):
    city = _city(tmp_path, "City Council")
    code = meeting_cli.main(
        ["run", "oWZpzjzjwnE", "--out", str(tmp_path / "o"), "--city", str(city)]
    )

    assert code == 0
    assert providers == [
        ("claude", "claude-opus-5-5", "recap", "City Council"),
        ("claude", "claude-opus-5-5", "judge", "City Council"),
    ]
    assert pipeline_run["model"] == "recap=claude:claude-opus-5-5 judge=claude:claude-opus-5-5"
    text, _ = pipeline_run["complete"]("prompt")
    assert text == "claude answer"
    assert pipeline_run["judge_complete"]("prompt")[0] == "claude answer"


def test_a_board_uses_deepseek_and_models_json_can_change_a_task(tmp_path, providers, pipeline_run):
    city = _city(tmp_path, "Planning and Zoning Commission")
    (city / "models.json").write_text(
        json.dumps(
            {
                "format": 1,
                "tasks": {"judge": {"default": "claude:claude-sonnet-5-5", "bodies": {}}},
            }
        )
    )
    assert (
        meeting_cli.main(["run", "PZPZPZPZPZ1", "--out", str(tmp_path / "o"), "--city", str(city)])
        == 0
    )
    assert providers == [
        ("ollama", llm.DEFAULT_MODEL, "recap", "Planning and Zoning Commission"),
        ("claude", "claude-sonnet-5-5", "judge", "Planning and Zoning Commission"),
    ]


def test_model_flag_or_no_city_keeps_the_single_ollama_model(tmp_path, providers, pipeline_run):
    city = _city(tmp_path, "City Council")
    out = str(tmp_path / "o")
    assert (
        meeting_cli.main(["run", "v", "--out", out, "--city", str(city), "--model", "qwen3:8b"])
        == 0
    )
    assert pipeline_run["model"] == "qwen3:8b"
    assert pipeline_run["complete"] is None and pipeline_run["judge_complete"] is None
    assert meeting_cli.main(["run", "v", "--out", out]) == 0
    assert pipeline_run["model"] == llm.DEFAULT_MODEL
    assert providers == []


def _recap(tmp_path, body, video_id="oWZpzjzjwnE"):
    path = tmp_path / "recap.json"
    path.write_text(
        json.dumps(
            {
                "city": "Longmont",
                "body": body,
                "meeting_date": "November 3, 2026",
                "video_id": video_id,
                "claims": [],
            }
        )
    )
    return script_cli.load_recap(path)


def _args(city=None, model=None):
    return script_cli.argparse.Namespace(city=city, model=model)


def test_script_writer_takes_the_pick_for_script_or_roundup(tmp_path, providers):
    city = _city(tmp_path, "City Council")
    script_cli._writer(_args(city), _recap(tmp_path, "City Council"))("p")
    script_cli._writer(_args(city), _recap(tmp_path, "City Council", script_cli.ROUNDUP_ID))("p")
    script_cli._writer(_args(city), _recap(tmp_path, "Historic Preservation Commission"))("p")
    assert providers == [
        ("claude", "claude-opus-5-5", "script", "City Council"),
        ("claude", "claude-opus-5-5", "roundup", "City Council"),
        ("ollama", llm.DEFAULT_MODEL, "script", "Historic Preservation Commission"),
    ]


def test_script_writer_model_flag_overrides_the_pick(tmp_path, providers, monkeypatch):
    calls = []
    monkeypatch.setattr(
        script_cli.llm, "chat", lambda prompt, model: calls.append(model) or ("text", llm_receipt())
    )
    city = _city(tmp_path, "City Council")
    assert (
        script_cli._writer(_args(city, "qwen3:8b"), _recap(tmp_path, "City Council"))("p") == "text"
    )
    assert calls == ["qwen3:8b"]
    assert providers == []


def llm_receipt():
    class Receipt:
        def line(self):
            return "[receipt] test"

    return Receipt()
