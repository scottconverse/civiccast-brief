import json

import pytest

from civiccast_brief import models as M


class FakeRunner:
    def __init__(self, out, code=0, err=""):
        self.out, self.code, self.err = out, code, err
        self.calls = []

    def __call__(self, cmd, stdin, env):
        self.calls.append((cmd, stdin, dict(env)))
        return self.code, self.out, self.err


def ok_json(text="hello", model="claude-opus-5-5"):
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "duration_ms": 1200,
            "modelUsage": {model: {"inputTokens": 3}},
        }
    )


def test_defaults_without_a_file_are_claude_for_council_and_deepseek_elsewhere(tmp_path):
    settings = M.load_settings(tmp_path)
    for task in M.TASKS:
        assert settings.pick(task, "City Council") == M.CLAUDE
        assert settings.pick(task, "Planning and Zoning Commission") == M.DEEPSEEK


def test_body_override_beats_task_default_and_file_round_trips(tmp_path):
    settings = M.load_settings(tmp_path)
    settings.tasks["recap"] = M.TaskPicks(
        default="claude:claude-sonnet-5-5", bodies={"City Council": M.DEEPSEEK}
    )
    path = M.save_settings(tmp_path, settings)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == 1
    assert set(data["tasks"]) == set(M.TASKS)
    again = M.load_settings(tmp_path)
    assert again.pick("recap", "City Council") == M.DEEPSEEK
    assert again.pick("recap", "Library Board") == "claude:claude-sonnet-5-5"
    # Untouched tasks were written with the built-in picks.
    assert again.pick("script", "City Council") == M.CLAUDE


@pytest.mark.parametrize("pick", ["gpt:4", "claude:", "claude", "ollama:two words", ""])
def test_bad_picks_are_refused_with_the_format(tmp_path, pick):
    (tmp_path / "models.json").write_text(json.dumps({"tasks": {"recap": {"default": pick}}}))
    with pytest.raises(M.ModelSettingsError, match="provider:model|not a model pick"):
        M.load_settings(tmp_path)


def test_unknown_task_and_bad_json_are_refused(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({"tasks": {"poem": {}}}))
    with pytest.raises(M.ModelSettingsError, match="unknown task"):
        M.load_settings(tmp_path)
    (tmp_path / "models.json").write_text("{nope")
    with pytest.raises(M.ModelSettingsError, match="not readable JSON"):
        M.load_settings(tmp_path)
    with pytest.raises(M.ModelSettingsError, match="unknown task"):
        M.ModelSettings().pick("poem", "City Council")


def test_claude_runs_on_stdin_without_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-real")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-real")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "kept")
    runner = FakeRunner(ok_json("the recap"))
    complete = M.complete_for(tmp_path, "recap", "City Council", runner=runner)
    text, receipt = complete("PROMPT TEXT")
    assert text == "the recap"
    cmd, stdin, env = runner.calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "PROMPT TEXT" not in " ".join(cmd)
    assert stdin == "PROMPT TEXT"
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "kept"
    assert (receipt.provider, receipt.model, receipt.task, receipt.body) == (
        "claude",
        "claude-opus-5-5",
        "recap",
        "City Council",
    )
    assert receipt.seconds >= 0
    assert "used=claude-opus-5-5" in receipt.line()


@pytest.mark.parametrize(
    "out,code,match",
    [
        ("not json", 1, "without JSON"),
        (json.dumps({"is_error": True, "result": "Not logged in"}), 1, "Not logged in"),
        (json.dumps({"subtype": "success", "result": "  "}), 0, "empty answer"),
    ],
)
def test_claude_failures_raise(tmp_path, out, code, match):
    complete = M.complete_for(tmp_path, "judge", "City Council", runner=FakeRunner(out, code))
    with pytest.raises(M.ModelCallError, match=match):
        complete("p")


def test_other_bodies_go_to_ollama_deepseek(tmp_path):
    seen = {}

    class Inner:
        def line(self):
            return "[receipt] in=1 out=2"

    def chat(prompt, *, model):
        seen.update(prompt=prompt, model=model)
        return "deepseek text", Inner()

    complete = M.complete_for(tmp_path, "article", "Library Board", chat=chat)
    text, receipt = complete("hi")
    assert text == "deepseek text"
    assert seen == {"prompt": "hi", "model": "deepseek-v4.1-flash:1017-cloud"}
    assert receipt.provider == "ollama" and receipt.model == "deepseek-v4.1-flash:1017-cloud"
    assert "in=1 out=2" in receipt.detail
