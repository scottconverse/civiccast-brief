"""The city's voices: which engine speaks the episode, its fallback, and the two hosts.

``voices.json`` sits in the city folder, next to ``city.json``::

    {
      "engine": "gemini",
      "fallback_engine": "lfm3",
      "audio": {"gemini": {"voice_s1": "Bodi", "voice_s2": "Sola"}}
    }

``audio`` holds exactly the keys ``civiccast_brief.audio.engines.load_config`` accepts, so the
file can be handed to the audio command as its ``--config`` once the ``audio`` part is written
on its own. The Voices page edits this file; ``weekly run --voices`` uses it.

Only the voices the user changed from the code default are written, so a later change to a code
default still reaches every city that never overrode it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from civiccast_brief.audio.engines import (
    ENGINE_NAMES,
    AudioConfig,
    ChatterboxConfig,
    Dia2Config,
    GeminiConfig,
    LfmConfig,
    _build,
)
from civiccast_brief.audio.errors import ConfigError

SETTINGS_NAME: Final = "voices.json"
#: The engines the desk offers. The desk never picks the test engine.
OFFERED: Final = tuple(name for name in ENGINE_NAMES if name != "fake")
#: What the live cities use, and what the page starts on when the file says nothing.
DEFAULT_ENGINE: Final = "gemini"
DEFAULT_FALLBACK: Final = "lfm3"
LABELS: Final = {
    "gemini": "Gemini",
    "lfm3": "LFM3",
    "dia2": "Dia2",
    "chatterbox": "Chatterbox",
}
#: What each engine is, in one plain line (docs/set-up-a-city.md and docs/voice-bakeoff.md).
SUMMARIES: Final = {
    "gemini": "Google's voices; needs a Google API key.",
    "lfm3": "Runs on this PC.",
    "dia2": "Needs the GPU PC.",
    "chatterbox": "Needs the GPU PC.",
}
#: Each engine's config class, for its code defaults.
CONFIGS: Final = {
    "gemini": GeminiConfig,
    "lfm3": LfmConfig,
    "dia2": Dia2Config,
    "chatterbox": ChatterboxConfig,
}
#: The voice fields of each engine's config, in host order. Dia2 clones from WAV files.
VOICE_FIELDS: Final = {
    "gemini": ("voice_s1", "voice_s2"),
    "lfm3": ("voice_s1", "voice_s2"),
    "dia2": ("ref_s1", "ref_s2"),
    "chatterbox": ("voice_s1", "voice_s2"),
}
MAX_VOICE_LEN: Final = 80


class VoicesError(ValueError):
    """voices.json is unreadable, or one of its settings is not usable."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Voices:
    """The voice settings, as voices.json keeps them; everything empty means "use the defaults"."""

    engine: str = ""
    fallback_engine: str = ""
    audio: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "fallback_engine": self.fallback_engine,
            "audio": dict(self.audio),
        }

    @property
    def chosen_engine(self) -> str:
        """The engine the desk names, or the live cities' choice when the file says nothing."""
        return self.engine or DEFAULT_ENGINE


def load(city_dir: str | Path) -> Voices:
    """voices.json from the city folder; empty settings when there is no file."""
    return load_file(Path(city_dir) / SETTINGS_NAME)


def load_file(path: str | Path) -> Voices:
    """voices.json at ``path``, checked; empty settings when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return Voices()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VoicesError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VoicesError(f"{path} must hold a JSON object")
    unknown = sorted(set(data) - {"engine", "fallback_engine", "audio"})
    if unknown:
        raise VoicesError(f"{path}: unknown keys {unknown}")
    audio = data.get("audio", {})
    if not isinstance(audio, dict):
        raise VoicesError('"audio" must be a JSON object', "audio")
    voices = Voices(
        engine=str(data.get("engine", "") or "").strip(),
        fallback_engine=str(data.get("fallback_engine", "") or "").strip(),
        audio=audio,
    )
    validate(voices)
    return voices


def validate(voices: Voices) -> None:
    """Raise VoicesError naming the field that is wrong."""
    for name, field_name in (("engine", "engine"), ("fallback_engine", "fallback_engine")):
        value = getattr(voices, name)
        if value and value not in OFFERED:
            raise VoicesError(
                f"Choose a voice engine from {', '.join(OFFERED)}; {value!r} is not one.",
                field_name,
            )
    if voices.engine and voices.engine == voices.fallback_engine:
        raise VoicesError(
            "If it fails, use must be a different engine, or None.", "fallback_engine"
        )
    try:
        _build(AudioConfig, dict(voices.audio), "audio")
    except ConfigError as exc:
        raise VoicesError(f"Not a voice setting the audio command reads: {exc}", "audio") from None
    for engine, keys in VOICE_FIELDS.items():
        for key in keys:
            value = voice_from_file(voices, engine, key)
            if value is None:
                continue
            field_name = field_id(engine, key)
            if not value.strip():
                raise VoicesError("Enter a voice name.", field_name)
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                raise VoicesError("A voice name cannot hold control characters.", field_name)
            if len(value) > MAX_VOICE_LEN:
                raise VoicesError(
                    f"A voice name is at most {MAX_VOICE_LEN} characters.", field_name
                )


def save(city_dir: str | Path, voices: Voices) -> Path:
    """Write voices.json through a temporary file, so a crash never leaves half a file."""
    validate(voices)
    path = Path(city_dir) / SETTINGS_NAME
    text = json.dumps(voices.to_json(), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def write_audio_config(path: str | Path, voices: Voices) -> Path:
    """Write just the ``audio`` part, the file the audio command takes as ``--config``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(voices.audio), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def field_id(engine: str, key: str) -> str:
    """The form field of one engine's voice, as the page names it."""
    return f"{engine}-{key}"


def default_voice(engine: str, key: str) -> str:
    """The voice the code uses when the file says nothing about it."""
    return str(getattr(CONFIGS[engine](), key))


def voice_from_file(voices: Voices, engine: str, key: str) -> str | None:
    """The voice voices.json sets for that engine and host, or None."""
    block = voices.audio.get(engine)
    if not isinstance(block, Mapping):
        return None
    value = block.get(key)
    return value if isinstance(value, str) else None


def voice_value(voices: Voices, engine: str, key: str) -> str:
    """The voice in use: the file's when it sets one, else the code default."""
    value = voice_from_file(voices, engine, key)
    return value if value is not None else default_voice(engine, key)


def from_form(form: Mapping[str, str], current: Voices) -> Voices:
    """The settings the Voices form asks for, starting from the ones on disk.

    A voice equal to the code default is left out of the file, so later code defaults still
    apply; an engine's block that keeps every default is left out too. Every engine's block is
    read, so a change made without JavaScript (all blocks visible) is kept as well.
    """
    engine = str(form.get("engine", "") or "").strip()
    fallback = str(form.get("fallback_engine", "") or "").strip()
    audio: dict[str, Any] = {
        name: dict(block)
        for name, block in (current.audio or {}).items()
        if isinstance(block, Mapping)
    }
    for name in OFFERED:
        block = audio.setdefault(name, {})
        for key in VOICE_FIELDS[name]:
            given = form.get(field_id(name, key))
            if given is None:
                continue  # an engine's block that was not on the form
            typed = str(given).strip()
            if not typed:
                block[key] = typed  # validate() names it: an empty voice is an error
            elif typed == default_voice(name, key):
                block.pop(key, None)
            else:
                block[key] = typed
        if not block:
            audio.pop(name, None)
    chosen = Voices(engine=engine, fallback_engine=fallback, audio=audio)
    validate(chosen)
    return chosen


def saved_notice(voices: Voices) -> str:
    """What the page says after a save: the engine and voices the next episode will use."""
    engine = voices.engine or DEFAULT_ENGINE
    first = voice_value(voices, engine, VOICE_FIELDS[engine][0])
    second = voice_value(voices, engine, VOICE_FIELDS[engine][1])
    return f"Saved. The next episode uses {LABELS[engine]}, with {first} and {second}."
