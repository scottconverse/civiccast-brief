"""What this PC needs for CivicCast Brief, checked in one place.

The installer checks Chrome and Ollama (``install.ps1``) and nothing else. The desk's This
computer page (:mod:`civiccast_brief.review.web`) asks the probes here instead, so a new
install can see on one page what is still missing and what to do about it.

Every probe takes its outside world as a callable (``which``, an HTTP getter, the credential
reader, the model runner, the environment), so tests use fakes and nothing real runs. No probe
stores, logs or returns a secret: the Gemini row keeps only a yes or no.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import httpx

from civiccast_brief import models
from civiccast_brief.audio import engines
from civiccast_brief.record.config import Settings
from civiccast_brief.record.credentials import read_credential
from civiccast_brief.record.runtime import RuntimeManager
from civiccast_brief.script.llm import DEFAULT_MODEL as WRITER_MODEL

Need = Literal["required", "voice", "optional"]

OLLAMA_TAGS_URL: Final = "http://127.0.0.1:11434/api/tags"
OLLAMA_TIMEOUT_S: Final = 5.0
SIGNIN_MODEL: Final = "claude-haiku-5-5"
SIGNIN_PROMPT: Final = "Reply with the single word OK."

FIX_YTDLP: Final = (
    "Run python -m civiccast_brief tools --test-video <a public meeting video URL> "
    "to install yt-dlp."
)
FIX_CHROME: Final = "Install Chrome from https://www.google.com/chrome/."
FIX_CLAUDE: Final = "Install Claude Code and sign in with claude."
FIX_LFM3_EXE: Final = "Install llama.cpp with WinGet, which puts llama-tts on this PC."

#: The HTTP getter's shape: the URL and the timeout, giving the parsed JSON body.
GetJson = Callable[[str, float], Any]
Exists = Callable[[str], bool]


@dataclass(frozen=True)
class Check:
    """One thing this PC needs: what was found, and what to do when it is missing."""

    key: str
    label: str
    need: Need
    #: True found, False missing, None not checked yet (the Claude sign-in row).
    ok: bool | None
    #: What was found, in plain words.
    detail: str
    #: One plain sentence saying what to do; "" when ok.
    fix: str


def _get_json(url: str, timeout: float) -> Any:
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _is_file(path: str) -> bool:
    return Path(path).is_file()


def probe_ffmpeg(*, which: Callable[[str], str | None] = shutil.which) -> Check:
    """ffmpeg and ffprobe, which make the audio and measure it."""
    if which("ffmpeg") is not None and which("ffprobe") is not None:
        # The same two programs civiccast_brief.audio.ffmpeg looks for.
        return Check("ffmpeg", "ffmpeg", "required", True, "ffmpeg and ffprobe are on PATH.", "")
    missing = [name for name in ("ffmpeg", "ffprobe") if which(name) is None]
    return Check(
        "ffmpeg",
        "ffmpeg",
        "required",
        False,
        f"Not on PATH: {', '.join(missing)}.",
        "Install ffmpeg, which makes and measures the audio.",
    )


def probe_ytdlp(
    *, runtime_root: Path | None = None, manager: RuntimeManager | None = None
) -> Check:
    """The yt-dlp runtime the tools command installs, read from its pointer."""
    if manager is None:
        root = runtime_root if runtime_root is not None else Settings.from_env().runtime_root
        manager = RuntimeManager(root=root)
    try:
        python = manager.interpreter()
    except RuntimeError as exc:
        return Check("ytdlp", "yt-dlp", "required", False, str(exc), FIX_YTDLP)
    return Check("ytdlp", "yt-dlp", "required", True, f"Installed ({python}).", "")


def probe_chrome(*, environ: Mapping[str, str] | None = None, is_file: Exists = _is_file) -> Check:
    """Chrome in the three folders install.ps1 checks."""
    env = _env(environ)
    relative = Path("Google") / "Chrome" / "Application" / "chrome.exe"
    folders = (
        env.get("ProgramFiles", ""),
        env.get("ProgramFiles(x86)", ""),
        env.get("LOCALAPPDATA", ""),
    )
    for folder in folders:
        if not folder:
            continue
        candidate = Path(folder) / relative
        if is_file(str(candidate)):
            return Check("chrome", "Chrome", "required", True, f"Found at {candidate}.", "")
    return Check(
        "chrome",
        "Chrome",
        "required",
        False,
        "Chrome was not found in the three usual folders.",
        FIX_CHROME,
    )


def _model_names(tags: Any) -> list[str]:
    """The model names in an Ollama /api/tags answer, without trusting its shape."""
    if not isinstance(tags, Mapping):
        return []
    listed = tags.get("models")
    if not isinstance(listed, list):
        return []
    names: list[str] = []
    for model in listed:
        if isinstance(model, Mapping):
            name = model.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def probe_ollama(*, get_json: GetJson = _get_json, model: str = WRITER_MODEL) -> Check:
    """Ollama is running and holds the writer model."""
    try:
        tags = get_json(OLLAMA_TAGS_URL, OLLAMA_TIMEOUT_S)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        return Check(
            "ollama",
            "Ollama",
            "required",
            False,
            f"Ollama did not answer at {OLLAMA_TAGS_URL} ({exc}).",
            "Start Ollama, or install it from https://ollama.com/download.",
        )
    if model in _model_names(tags):
        return Check("ollama", "Ollama", "required", True, f"Running, with {model}.", "")
    return Check(
        "ollama",
        "Ollama",
        "required",
        False,
        f"Running, but it does not list {model}.",
        f"Run ollama pull {model}.",
    )


def probe_claude(*, which: Callable[[str], str | None] = shutil.which) -> Check:
    """The claude command, which the Models page can send work to."""
    found = which("claude")
    if found:
        return Check("claude", "Claude Code", "required", True, f"Found at {found}.", "")
    return Check("claude", "Claude Code", "required", False, "claude is not on PATH.", FIX_CLAUDE)


def probe_claude_signin(
    *,
    enabled: bool,
    runner: models.Runner = models.run_process,
    environ: Mapping[str, str] | None = None,
) -> Check:
    """Ask Claude for a one-word reply, through the same runner the Models code uses.

    With ``enabled`` false no call is made and the answer is None, which the page shows as
    "Not checked yet".
    """
    if not enabled:
        return Check(
            "claude_signin",
            "Claude sign-in",
            "required",
            None,
            "Not checked yet.",
            "Press Check Claude sign-in to try a one-word reply.",
        )
    command = models.claude_command(SIGNIN_MODEL)
    env = models.child_env(environ)
    try:
        code, _out, _err = runner(command, SIGNIN_PROMPT, env)
    except (OSError, ValueError, models.ModelCallError) as exc:
        return Check(
            "claude_signin",
            "Claude sign-in",
            "required",
            False,
            f"claude could not be run: {exc}",
            FIX_CLAUDE,
        )
    if code == 0:
        return Check("claude_signin", "Claude sign-in", "required", True, "Claude answered.", "")
    return Check(
        "claude_signin",
        "Claude sign-in",
        "required",
        False,
        f"claude -p exited {code}.",
        "Run claude in a terminal and sign in.",
    )


def probe_gemini(
    *,
    environ: Mapping[str, str] | None = None,
    read_saved: Callable[[str], str | None] = read_credential,
) -> Check:
    """A Gemini key is set in the environment or saved in the credential store.

    Only a yes or no leaves this function; the key itself is never kept or shown.
    """
    label = "Gemini voice"
    if _env(environ).get(engines.GEMINI_API_KEY_ENV, "").strip():
        return Check("gemini", label, "voice", True, "GEMINI_API_KEY is set.", "")
    try:
        saved = bool(read_saved(engines.GEMINI_API_KEY_TARGET))
    except (OSError, RuntimeError):
        saved = False
    if saved:
        return Check(
            "gemini", label, "voice", True, "A saved key was found in Credential Manager.", ""
        )
    return Check(
        "gemini",
        label,
        "voice",
        False,
        "No Gemini key was found.",
        f"Set {engines.GEMINI_API_KEY_ENV}, or save a key for {engines.GEMINI_API_KEY_TARGET} "
        "in Credential Manager.",
    )


def probe_lfm3(
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    is_file: Exists = _is_file,
) -> Check:
    """llama-tts and its two model files, the voice engine that runs on this PC (need "voice").

    LfmLocal.render refuses to run unless all three exist, so the row is only ok when all three
    do. The paths come from the code defaults: the weekly run builds LfmConfig from the city's
    voices.json only inside the audio command (via ``--config``), so there is no simple existing
    way to read a city's configured paths here, and the page is not tied to one city folder.
    """
    cfg = engines.LfmConfig()
    executable = engines.lfm3_executable(cfg, which=which, environ=_env(environ))
    missing = [
        (label, path)
        for label, path in (
            ("llama-tts", executable),
            ("LFM3 model", cfg.model),
            ("LFM3 mmproj", cfg.mmproj),
        )
        if not is_file(path)
    ]
    if not missing:
        return Check(
            "lfm3",
            "LFM3 voice",
            "voice",
            True,
            f"Found at {executable}, with its model files.",
            "",
        )
    detail = "; ".join(f"{label} not found: {path}" for label, path in missing)
    exe_missing = any(label == "llama-tts" for label, _path in missing)
    return Check("lfm3", "LFM3 voice", "voice", False, detail, _lfm3_fix(exe_missing, cfg))


def _lfm3_fix(exe_missing: bool, cfg: engines.LfmConfig) -> str:
    """The exe's fix when it is missing, otherwise where to put the two model files."""
    if exe_missing:
        return FIX_LFM3_EXE
    return f"Put the LFM3 model files at {cfg.model} and {cfg.mmproj}."


def run_checks(
    *,
    claude_signin: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    get_json: GetJson = _get_json,
    read_saved: Callable[[str], str | None] = read_credential,
    runner: models.Runner = models.run_process,
    environ: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
) -> list[Check]:
    """Every check, in the order the page shows them."""
    return [
        probe_ffmpeg(which=which),
        probe_ytdlp(runtime_root=runtime_root),
        probe_chrome(environ=environ),
        probe_ollama(get_json=get_json),
        probe_claude(which=which),
        probe_claude_signin(enabled=claude_signin, runner=runner, environ=environ),
        probe_gemini(environ=environ, read_saved=read_saved),
        probe_lfm3(which=which, environ=environ),
    ]


def voice_ok(checks: list[Check]) -> bool:
    """True when at least one voice engine is ready."""
    return any(check.need == "voice" and check.ok is True for check in checks)


def all_required_ok(checks: list[Check]) -> bool:
    """True when every required row is ok (a not-checked row is not ok)."""
    return all(check.ok is True for check in checks if check.need == "required")
