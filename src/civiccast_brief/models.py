"""Which model writes each task, set per city in ``models.json`` (the desk's Models screen edits it).

The file sits in the city folder::

    {
      "format": 1,
      "tasks": {
        "recap":   {"default": "ollama:deepseek-v4.1-flash:1017-cloud",
                    "bodies": {"City Council": "claude:claude-opus-5-5"}},
        "judge":   {...}, "article": {...}, "script": {...}, "roundup": {...}
      }
    }

A pick is ``provider:model``. Providers:

- ``ollama``: the local Ollama chat in ``script/llm.py`` (DeepSeek by default).
- ``claude``: ``claude -p`` on the owner's Claude subscription (OAuth). The child process gets no
  ``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN``, so it can never bill an API key. The prompt
  goes on stdin and ``--output-format json`` returns the text.

With no file, or for a task the file leaves out, the built-in picks apply: Claude for
"City Council", DeepSeek for every other body. For a task the file lists, the file is the whole
answer: a body in its "bodies" gets that pick, every other body gets its "default".

``complete_for(city_dir, task, body)`` returns a ``Complete``: prompt in, (text, receipt) out,
the shape ``meeting/pipeline.py`` takes. The receipt says provider, model and seconds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from civiccast_brief.script import llm

TASKS: Final = ("recap", "judge", "article", "script", "roundup")
PROVIDERS: Final = ("claude", "ollama")
SETTINGS_NAME: Final = "models.json"
FORMAT: Final = 1

DEEPSEEK: Final = f"ollama:{llm.DEFAULT_MODEL}"
CLAUDE: Final = "claude:claude-opus-5-5"
#: The built-in pick for a body when models.json says nothing about it.
BODY_DEFAULTS: Final = {"City Council": CLAUDE}
#: Picks the Models screen offers; a pick already in the file is offered too.
CHOICES: Final = (
    "claude:claude-opus-5-5",
    "claude:claude-sonnet-5-5",
    "claude:claude-haiku-5-5",
    DEEPSEEK,
)
CLAUDE_TIMEOUT_S: Final = 1800
#: Credentials that would make ``claude`` bill an API key instead of the subscription.
API_KEY_VARS: Final = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

Complete = Callable[[str], tuple[str, Any]]
#: Runs one command with stdin text; returns (exit code, stdout, stderr). Tests pass a stand-in.
Runner = Callable[[list[str], str, Mapping[str, str]], tuple[int, str, str]]
#: The Ollama chat; tests pass a stand-in.
Chat = Callable[..., tuple[str, Any]]


class ModelSettingsError(ValueError):
    """models.json is unreadable or holds a pick that is not ``provider:model``."""


class ModelCallError(RuntimeError):
    """The model ran but gave no usable answer."""


@dataclass(frozen=True)
class ModelReceipt:
    provider: str
    model: str
    seconds: float
    task: str = ""
    body: str = ""
    detail: str = ""

    def line(self) -> str:
        extra = f" {self.detail}" if self.detail else ""
        return (
            f"[receipt] task={self.task} body={self.body!r} provider={self.provider} "
            f"model={self.model} secs={self.seconds:.1f}{extra}"
        )


@dataclass
class TaskPicks:
    default: str = ""
    bodies: dict[str, str] = field(default_factory=dict)


def default_picks() -> TaskPicks:
    """DeepSeek for every body, Claude for the bodies in BODY_DEFAULTS."""
    return TaskPicks(default=DEEPSEEK, bodies=dict(BODY_DEFAULTS))


@dataclass
class ModelSettings:
    tasks: dict[str, TaskPicks] = field(default_factory=dict)

    def pick(self, task: str, body: str) -> str:
        """The pick for one task and body: the body's override, else the task's default."""
        if task not in TASKS:
            raise ModelSettingsError(f"unknown task {task!r}; tasks are {', '.join(TASKS)}")
        picks = self.tasks.get(task) or default_picks()
        return picks.bodies.get(" ".join(body.split()), picks.default or DEEPSEEK)

    def to_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "tasks": {
                task: {"default": p.default or DEEPSEEK, "bodies": dict(sorted(p.bodies.items()))}
                for task in TASKS
                for p in [self.tasks.get(task) or default_picks()]
            },
        }


def split_pick(pick: str) -> tuple[str, str]:
    """``"claude:claude-opus-5-5"`` -> ("claude", "claude-opus-5-5"). Refuses anything else."""
    provider, sep, model = str(pick).strip().partition(":")
    if not sep or provider not in PROVIDERS or not model.strip() or any(c.isspace() for c in model):
        raise ModelSettingsError(
            f"{pick!r} is not a model pick; write provider:model, for example {CLAUDE!r} or "
            f"{DEEPSEEK!r} (providers: {', '.join(PROVIDERS)})"
        )
    return provider, model


def parse_settings(data: Any) -> ModelSettings:
    if not isinstance(data, dict) or not isinstance(data.get("tasks", {}), dict):
        raise ModelSettingsError('models.json must be an object with a "tasks" object')
    settings = ModelSettings()
    for task, raw in data.get("tasks", {}).items():
        if task not in TASKS:
            raise ModelSettingsError(f"unknown task {task!r}; tasks are {', '.join(TASKS)}")
        if not isinstance(raw, dict):
            raise ModelSettingsError(f"task {task!r} must be an object")
        default = str(raw.get("default", DEEPSEEK))
        split_pick(default)
        bodies_raw = raw.get("bodies") or {}
        if not isinstance(bodies_raw, dict):
            raise ModelSettingsError(f'"bodies" of task {task!r} must be an object')
        bodies: dict[str, str] = {}
        for body, pick in bodies_raw.items():
            name = " ".join(str(body).split())
            if not name:
                raise ModelSettingsError(f"task {task!r} has a blank body name")
            split_pick(str(pick))
            bodies[name] = str(pick)
        settings.tasks[task] = TaskPicks(default=default, bodies=bodies)
    return settings


def load_settings(city_dir: str | Path) -> ModelSettings:
    path = Path(city_dir) / SETTINGS_NAME
    if not path.exists():
        return ModelSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelSettingsError(f"{path} is not readable JSON: {exc}") from exc
    return parse_settings(data)


def save_settings(city_dir: str | Path, settings: ModelSettings) -> Path:
    """Write models.json (through a temporary file, so a crash never leaves half a file)."""
    path = Path(city_dir) / SETTINGS_NAME
    text = json.dumps(settings.to_json(), indent=2, ensure_ascii=False) + "\n"
    parse_settings(json.loads(text))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for ``claude -p``: everything but API-key credentials."""
    env = dict(os.environ if base is None else base)
    for name in API_KEY_VARS:
        env.pop(name, None)
    return env


def claude_command(model: str) -> list[str]:
    # No tools, no MCP servers, no saved session: a plain completion.
    return [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]


def run_process(cmd: list[str], stdin: str, env: Mapping[str, str]) -> tuple[int, str, str]:
    """Run one command in an empty folder, so the model sees no project files."""
    # On Windows the npm install is claude.CMD, which subprocess finds only by its full path.
    program = shutil.which(cmd[0])
    if program is None:
        raise ModelCallError(f"{cmd[0]!r} is not on PATH; install Claude Code and sign in")
    with tempfile.TemporaryDirectory(prefix="civiccast-model-") as cwd:
        done = subprocess.run(
            [program, *cmd[1:]],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(env),
            cwd=cwd,
            timeout=CLAUDE_TIMEOUT_S,
            check=False,
        )
    return done.returncode, done.stdout, done.stderr


def claude_complete(
    model: str, *, runner: Runner = run_process, task: str = "", body: str = ""
) -> Complete:
    def complete(prompt: str) -> tuple[str, ModelReceipt]:
        started = time.monotonic()
        code, out, err = runner(claude_command(model), prompt, child_env())
        seconds = time.monotonic() - started
        try:
            data = json.loads(out)
        except ValueError as exc:
            tail = (err or out).strip()[-300:]
            raise ModelCallError(f"claude -p exited {code} without JSON: {tail}") from exc
        if code != 0 or data.get("is_error") or data.get("subtype") not in (None, "success"):
            reason = data.get("result") or data.get("subtype") or err.strip()[-300:]
            raise ModelCallError(f"claude -p failed (exit {code}): {reason}")
        text = data.get("result")
        if not isinstance(text, str) or not text.strip():
            raise ModelCallError("claude -p returned an empty answer")
        used = sorted((data.get("modelUsage") or {}).keys())
        detail = f"used={','.join(used)}" if used else ""
        if data.get("duration_ms") is not None:
            detail = f"{detail} duration_ms={data['duration_ms']}".strip()
        return text, ModelReceipt("claude", model, seconds, task, body, detail)

    return complete


def ollama_complete(
    model: str, *, chat: Chat | None = None, task: str = "", body: str = ""
) -> Complete:
    def complete(prompt: str) -> tuple[str, ModelReceipt]:
        started = time.monotonic()
        text, inner = (chat or llm.chat)(prompt, model=model)
        detail = inner.line().removeprefix("[receipt] ") if hasattr(inner, "line") else ""
        return text, ModelReceipt("ollama", model, time.monotonic() - started, task, body, detail)

    return complete


def complete_for(
    city_dir: str | Path,
    task: str,
    body: str,
    *,
    runner: Runner = run_process,
    chat: Chat | None = None,
) -> Complete:
    """The writer for one task and body, as models.json picks it."""
    provider, model = split_pick(load_settings(city_dir).pick(task, body))
    if provider == "claude":
        return claude_complete(model, runner=runner, task=task, body=body)
    return ollama_complete(model, chat=chat, task=task, body=body)
