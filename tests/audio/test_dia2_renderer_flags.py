"""The Dia2 renderer script and `dia2_commands` must agree on every command-line flag.

The renderer runs in the Dia2 venv (it imports dia2, numpy, whisper_timestamped), so it is
parsed with `ast` here and never imported.
"""

import ast
import re
from pathlib import Path

from civiccast_brief.audio import engines
from civiccast_brief.audio.engines import Dia2Config, dia2_commands

RENDERER = Path(engines.__file__).parent / "box" / "render_dia2_chunked.py"


def _passed_flags() -> set[str]:
    commands = dia2_commands(Dia2Config(), r"C:\run\script.txt", r"C:\run\voice.wav")
    return {flag for cmd in commands for flag in re.findall(r"(?<=\s)'?(--[a-z0-9-]+)", cmd)}


def _defined_flags(path: Path) -> set[str]:
    flags: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            flags.update(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith("--")
            )
    return flags


def test_renderer_script_is_in_the_repo() -> None:
    assert RENDERER.is_file(), f"missing {RENDERER}"


def test_every_flag_dia2_commands_passes_is_defined_by_the_renderer() -> None:
    passed = _passed_flags()
    assert passed >= {"--script", "--out", "--ref1", "--ref2", "--atempo"}, passed
    missing = passed - _defined_flags(RENDERER)
    assert not missing, f"renderer does not define {sorted(missing)}"
