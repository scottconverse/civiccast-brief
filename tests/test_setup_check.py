"""The This computer probes, each against a fake world."""

from __future__ import annotations

from pathlib import Path

from civiccast_brief import setup_check
from civiccast_brief.record.runtime import Installed, Pointer, RuntimeManager, write_pointer
from civiccast_brief.record.runtime import tools as runtime_tools


def which_having(*names: str):
    def which(name: str) -> str | None:
        return f"C:\\tools\\{name}.exe" if name in names else None

    return which


def test_ffmpeg_needs_both_ffmpeg_and_ffprobe():
    both = setup_check.probe_ffmpeg(which=which_having("ffmpeg", "ffprobe"))
    assert both.ok is True and both.need == "required" and both.fix == ""
    only_one = setup_check.probe_ffmpeg(which=which_having("ffmpeg"))
    assert only_one.ok is False and "ffprobe" in only_one.detail
    assert "ffmpeg" in only_one.fix
    assert setup_check.probe_ffmpeg(which=which_having()).fix


def a_manager_with_ytdlp(root: Path, *, with_python: bool) -> RuntimeManager:
    manager = RuntimeManager(root=root)
    venv = manager.tool_root / "2026.8.19"
    write_pointer(manager.tool_root, Pointer(active=Installed("2026.8.19", str(venv))))
    if with_python:
        python = runtime_tools.python_in(venv)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
    return manager


def test_ytdlp_reads_the_runtime_pointer(tmp_path):
    ready = setup_check.probe_ytdlp(
        manager=a_manager_with_ytdlp(tmp_path / "ready", with_python=True)
    )
    assert ready.ok is True and ready.fix == "" and "Installed" in ready.detail

    missing = setup_check.probe_ytdlp(
        manager=a_manager_with_ytdlp(tmp_path / "missing", with_python=False)
    )
    assert missing.ok is False
    assert "tools --test-video" in missing.fix
    assert "run setup" in missing.detail


def test_ytdlp_with_no_pointer_is_not_installed(tmp_path):
    check = setup_check.probe_ytdlp(manager=RuntimeManager(root=tmp_path))
    assert check.ok is False and "not installed" in check.detail


def test_chrome_checks_the_three_installer_folders():
    environ = {
        "ProgramFiles": r"C:\Program Files",
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
    }
    wanted = str(
        Path(r"C:\Users\me\AppData\Local") / "Google" / "Chrome" / "Application" / "chrome.exe"
    )
    found = setup_check.probe_chrome(environ=environ, is_file=lambda p: p == wanted)
    assert found.ok is True and found.fix == ""
    missing = setup_check.probe_chrome(environ=environ, is_file=lambda p: False)
    assert missing.ok is False and "google.com/chrome" in missing.fix


def tags(*names: str) -> dict:
    return {"models": [{"name": name} for name in names]}


def test_ollama_running_with_the_model():
    check = setup_check.probe_ollama(get_json=lambda url, timeout: tags("llama3", "x:1"))
    # The writer model is not in that answer, so this is the "model missing" fix.
    assert check.ok is False and check.fix == f"Run ollama pull {setup_check.WRITER_MODEL}."
    ok = setup_check.probe_ollama(get_json=lambda url, timeout: tags(setup_check.WRITER_MODEL))
    assert ok.ok is True and ok.fix == ""


def test_ollama_not_running_has_its_own_fix():
    def refuse(url: str, timeout: float) -> dict:
        raise OSError("connection refused")

    check = setup_check.probe_ollama(get_json=refuse)
    assert check.ok is False
    assert "ollama.com/download" in check.fix
    assert "pull" not in check.fix


def test_claude_is_looked_up_on_path():
    assert setup_check.probe_claude(which=which_having("claude")).ok is True
    missing = setup_check.probe_claude(which=which_having())
    assert missing.ok is False and "Install Claude Code" in missing.fix


def test_claude_signin_not_checked_makes_no_call():
    def runner(*args):
        raise AssertionError("no call may be made")

    check = setup_check.probe_claude_signin(enabled=False, runner=runner)
    assert check.ok is None and check.need == "required"
    assert check.detail == "Not checked yet."


def test_claude_signin_asks_haiku_once_through_the_models_runner():
    calls = []

    def runner(command, stdin, env):
        calls.append((command, stdin, env))
        return 0, '{"result": "OK"}', ""

    check = setup_check.probe_claude_signin(
        enabled=True, runner=runner, environ={"PATH": "C:\\bin", "ANTHROPIC_API_KEY": "secret"}
    )
    assert check.ok is True and check.fix == ""
    assert len(calls) == 1
    command, stdin, env = calls[0]
    assert command == setup_check.models.claude_command("claude-haiku-5-5")
    assert "claude-haiku-5-5" in command
    assert stdin == "Reply with the single word OK."
    assert "ANTHROPIC_API_KEY" not in env

    failed = setup_check.probe_claude_signin(
        enabled=True, runner=lambda *a: (1, "", "not signed in")
    )
    assert failed.ok is False and failed.fix


def test_gemini_keeps_only_a_yes_or_no_and_never_the_key():
    secret = "AIza-not-a-real-key-0123456789"
    checks = [
        setup_check.probe_gemini(environ={"GEMINI_API_KEY": secret}, read_saved=lambda t: None),
        setup_check.probe_gemini(environ={}, read_saved=lambda t: secret),
        setup_check.probe_gemini(environ={}, read_saved=lambda t: None),
    ]
    assert [c.ok for c in checks] == [True, True, False]
    for check in checks:
        fields = f"{check.key} {check.label} {check.need} {check.detail} {check.fix}"
        assert secret not in fields
    assert "GEMINI_API_KEY" in checks[2].fix


def test_lfm3_needs_the_executable_to_exist():
    ok = setup_check.probe_lfm3(which=which_having("llama-tts"), environ={}, is_file=lambda p: True)
    assert ok.ok is True and ok.need == "voice" and ok.fix == ""
    missing = setup_check.probe_lfm3(
        which=which_having(), environ={"LOCALAPPDATA": r"C:\Local"}, is_file=lambda p: False
    )
    assert missing.ok is False and missing.need == "voice"


def test_voice_ok_with_gemini_only_lfm3_only_or_neither():
    def row(key: str, ok: bool | None) -> setup_check.Check:
        return setup_check.Check(key, key, "voice", ok, "", "")

    gemini = setup_check.probe_gemini(environ={"GEMINI_API_KEY": "x"}, read_saved=lambda t: None)
    lfm3 = setup_check.probe_lfm3(which=which_having("llama-tts"), is_file=lambda p: True)
    neither = setup_check.probe_gemini(environ={}, read_saved=lambda t: None)
    assert setup_check.voice_ok([gemini, neither]) is True
    assert setup_check.voice_ok([neither, lfm3]) is True
    assert setup_check.voice_ok([neither, row("other", None)]) is False


def test_all_required_ok_counts_a_not_checked_row_as_not_ok():
    ready = setup_check.Check("a", "a", "required", True, "", "")
    missing = setup_check.Check("b", "b", "required", False, "", "fix")
    unchecked = setup_check.Check("c", "c", "required", None, "", "fix")
    optional = setup_check.Check("d", "d", "voice", False, "", "fix")
    assert setup_check.all_required_ok([ready, optional]) is True
    assert setup_check.all_required_ok([ready, missing]) is False
    assert setup_check.all_required_ok([ready, unchecked]) is False


def test_run_checks_returns_every_row_in_order_without_calling_a_model(tmp_path):
    def refuse(*args, **kwargs):
        raise OSError("nothing real runs here")

    checks = setup_check.run_checks(
        which=which_having(),
        get_json=refuse,
        read_saved=lambda target: None,
        runner=refuse,
        environ={},
        runtime_root=tmp_path,
    )
    assert [c.key for c in checks] == [
        "ffmpeg",
        "ytdlp",
        "chrome",
        "ollama",
        "claude",
        "claude_signin",
        "gemini",
        "lfm3",
    ]
    assert checks[5].ok is None  # sign-in was not asked for
    assert [c.need for c in checks].count("voice") == 2
