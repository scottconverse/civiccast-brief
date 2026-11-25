"""Voice engines behind one adapter interface.

An engine takes the spoken lines of a script and writes one WAV. It also reports where each line
starts and ends in that WAV, and how exact those times are. Chapter marks never reach an engine.

Timing, per engine:

- ``exact`` (FakeEngine): the engine writes each line itself, so the times are known.
- ``measured`` (ChatterboxRemote): the remote renderer prints the audio length of every line and
  puts a fixed 0.35 s gap between lines. Start times are the running sum of lengths and gaps, then
  the timeline is stretched to the length of the file that came back. The gaps and the total are
  exact. A line's end includes the quiet tail the TTS model leaves at the end of that line. On the
  90-second e2e render, silence detection put the audible end 0.13 to 0.34 s before each line's
  end, and the audible start of the next line 0.02 to 0.11 s after the next start. The offset did
  not grow across the render. Captions can therefore run a few tenths of a second past the speech.
- ``approximate`` (Dia2Remote): the renderer reports the exact length of each two-line chunk but
  not where one line ends inside a chunk. That boundary is estimated from the word counts of the
  two lines, then the timeline is stretched to the file length. Chunk edges use the renderer's
  measured chunk lengths and carry the same quiet-tail offset as ``measured``. A boundary inside a
  chunk is an estimate and was not checked against the audio.
- ``approximate`` (GeminiTTS): the API reports only the audio of a whole interaction. A request
  carries whole script lines up to ``GeminiConfig.max_words_per_request`` words, and the request
  audio lengths are exact. Inside a request, the boundary between two lines is estimated from the
  word counts of those lines, then the whole timeline is stretched to the final file length.
  Requests are joined in order with ``GeminiConfig.gap_s`` of silence.
- ``measured`` (LfmLocal): llama-tts writes each line to its own 24 kHz mono WAV, so every line
  length is exact. The line WAVs are joined in order with ``LfmConfig.gap_s`` of silence, and the
  gaps and the total are exact. A line's end includes the quiet tail the model leaves, as with
  ChatterboxRemote.

The remote engines drive the bake-off renderers on the GPU box over ssh and scp. GeminiTTS is the
one hosted engine: it calls Google's Gemini TTS Interactions API over HTTPS; nothing runs on the
box. LfmLocal is the one engine that runs on this PC: it calls llama-tts (llama.cpp) directly.
Remote commands are PowerShell (the box's default shell) sent with -EncodedCommand, so nothing has
to survive cmd.exe quoting. Every engine runs as plain Windows Python on the box; none needs Linux
or WSL. The outer PowerShell reports exit code 1 whenever the inner command failed, so the exit
code is not trusted. Each remote command prints CIVICCAST_RC=<n>, and that line decides.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import typing
import uuid
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import httpx

from civiccast_brief.audio.errors import ConfigError, EngineError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg
from civiccast_brief.audio.lines import Line
from civiccast_brief.record.credentials import read_windows_credential
from civiccast_brief.speech.windows_process import hidden_process_kwargs

Timing = Literal["exact", "measured", "approximate"]

SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20")
RC_MARKER = "CIVICCAST_RC="
ENGINE_NAMES = ("dia2", "chatterbox", "gemini", "lfm3", "fake")
# Scott, 2026-11-12: Dia2, on Windows. Step-Audio-EditX (a Linux program) is not an option at all.
DEFAULT_ENGINE = "dia2"

_CHATTER_LINE = re.compile(r"^line\s+(\d+)\s+(S[12])\s+audio=\s*([0-9.]+)s", re.MULTILINE)
_RC_LINE = re.compile(r"^CIVICCAST_RC=(-?\d+)\s*$", re.MULTILINE)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_API_KEY_TARGET = "CivicCastBrief/gemini-api"
GEMINI_DEFAULT_RATE = 24000
_GEMINI_RATE = re.compile(r"rate=(\d+)")
_GEMINI_RETRY_DELAY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)s$")
_GEMINI_SPEAKER_NAMES = {"S1": "Speaker 1", "S2": "Speaker 2"}


# --- results ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineTiming:
    number: int  # source line number in the script
    speaker: str
    start: float  # seconds from the start of the rendered WAV
    end: float


@dataclass(frozen=True)
class RenderResult:
    wav: Path
    engine: str
    timing: Timing
    lines: tuple[LineTiming, ...]
    duration: float  # seconds, measured from the WAV with ffprobe


class VoiceEngine(Protocol):
    name: str

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult: ...


# --- configuration ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteConfig:
    """The GPU box. Paths in the engine configs are Windows paths on that box."""

    host: str = "nvideablackwell"
    scratch_root: str = r"C:\dev\civiccast-brief-bakeoff\out\phase3"
    ssh: str = "ssh"
    scp: str = "scp"
    timeout_s: float = 3600.0


@dataclass(frozen=True)
class ChatterboxConfig:
    python: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\venv\Scripts\python.exe"
    renderer: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\render_turbo2.py"
    workdir: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox"
    hf_home: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\hf-home"
    # Voice per speaker: "builtin" or a WAV path relative to workdir. S1 is Pat, S2 is Alex.
    voice_s1: str = "builtin"
    voice_s2: str = r"refs\carter_ref.wav"
    # Must equal GAP_S in render_turbo2.py, which is not a parameter there.
    gap_s: float = 0.35


@dataclass(frozen=True)
class Dia2Config:
    python: str = r"C:\dev\civiccast-brief-bakeoff\dia2\.venv\Scripts\python.exe"
    renderer: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\render_dia2_chunked.py"
    workdir: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune"
    hf_home: str = ""  # empty leaves the environment alone
    ref_s1: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\ref_s1.wav"
    ref_s2: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\ref_s2.wav"
    # Settings picked in the bake-off (see docs/voice-bakeoff.md, round 2, variant C).
    lines_per_chunk: int = 2
    cfg: float = 3.0
    temp: float = 0.6
    gap_s: float = 0.3
    atempo: float = 0.85


@dataclass(frozen=True)
class GeminiConfig:
    """Google Gemini TTS over its REST API. Scott picked voices Bodi (S1) and Sola (S2)."""

    model: str = "gemini-3.8-flash-tts"
    fallback_model: str = "gemini-3.8-flash-lite-tts"  # empty string = no fallback
    voice_s1: str = "Bodi"
    voice_s2: str = "Sola"
    max_words_per_request: int = 1300
    timeout_s: float = 300.0
    gap_s: float = 0.35  # silence between requests' audio when a script is split
    # Per-minute limits: wait and retry the same model before falling back. A per-day quota
    # (or a wait longer than max_retry_wait_s) goes straight to the fallback.
    retry_wait_s: float = 60.0  # wait when the 429 body has no usable RetryInfo
    max_retry_wait_s: float = 90.0  # longer waits mean fall back instead
    retries_per_model: int = 2


LFM3_MODEL = r"C:\dev\civiccast-brief-models\lfm3\LFM3-Audio-2B-Q8_0.gguf"
LFM3_MMPROJ = r"C:\dev\civiccast-brief-models\lfm3\mmproj-LFM3-Audio-2B-Q8_0.gguf"
# Where the WinGet package puts llama-tts.exe, relative to %LOCALAPPDATA%.
LFM3_WINGET_EXE = (
    r"Microsoft\WinGet\Packages\ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe\llama-tts.exe"
)


@dataclass(frozen=True)
class LfmConfig:
    """LFM3 Audio through llama.cpp's llama-tts on this PC. Scott approved male S1, female S2."""

    llama_tts: str = ""  # empty = look on PATH, then the WinGet package path
    model: str = LFM3_MODEL
    mmproj: str = LFM3_MMPROJ
    voice_s1: str = "male"
    voice_s2: str = "female"
    gap_s: float = 0.35
    timeout_s: float = 300.0  # per line


@dataclass(frozen=True)
class AudioConfig:
    box: RemoteConfig = field(default_factory=RemoteConfig)
    chatterbox: ChatterboxConfig = field(default_factory=ChatterboxConfig)
    dia2: Dia2Config = field(default_factory=Dia2Config)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    lfm3: LfmConfig = field(default_factory=LfmConfig)


def load_config(path: Path | None) -> AudioConfig:
    """Defaults, or a JSON file whose keys override them. Unknown keys are an error."""
    if path is None:
        return AudioConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    return _build(AudioConfig, data, "config")


def _build(cls: type, data: object, where: str) -> typing.Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a JSON object")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for name, value in data.items():
        hint = hints[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = _build(hint, value, f"{where}.{name}")
        else:
            kwargs[name] = _coerce(hint, value, f"{where}.{name}")
    return cls(**kwargs)


def _coerce(hint: type, value: object, where: str) -> object:
    """Accept a JSON value for a str, int or float field. Anything else is an error."""
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    ok = (
        (hint is str and isinstance(value, str))
        or (hint is int and isinstance(value, int) and not isinstance(value, bool))
        or (hint is float and is_number)
    )
    if not ok:
        raise ConfigError(f"{where}: expected {hint.__name__}, got {value!r}")
    return hint(value)


def make_engine(name: str, config: AudioConfig) -> VoiceEngine:
    if name == "chatterbox":
        return ChatterboxRemote(config.box, config.chatterbox)
    if name == "dia2":
        return Dia2Remote(config.box, config.dia2)
    if name == "gemini":
        return GeminiTTS(config.gemini)
    if name == "lfm3":
        return LfmLocal(config.lfm3)
    if name == "fake":
        return FakeEngine()
    raise ConfigError(f"unknown engine {name!r}; choose from {', '.join(ENGINE_NAMES)}")


# --- PowerShell and transport helpers --------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess]


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def win_join(*parts: str) -> str:
    return "\\".join(part.rstrip("\\") for part in parts)


def _invoke(argv: list[str]) -> str:
    return "& " + " ".join(ps_quote(arg) for arg in argv)


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _scp_path(remote: str) -> str:
    return remote.replace("\\", "/")


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


class Box:
    """Runs PowerShell on the GPU box and copies files to and from it."""

    def __init__(self, cfg: RemoteConfig, runner: Runner = subprocess.run) -> None:
        self.cfg = cfg
        self._runner = runner

    def run(self, commands: list[str]) -> str:
        """Run PowerShell lines on the box. Returns stdout, or raises EngineError."""
        script = "\n".join(
            [
                "$ProgressPreference = 'SilentlyContinue'",
                *commands,
                "$rc = $LASTEXITCODE",
                "if ($null -eq $rc) { $rc = 0 }",
                f"Write-Output ('{RC_MARKER}' + $rc)",
            ]
        )
        argv = [
            self.cfg.ssh,
            *SSH_OPTIONS,
            self.cfg.host,
            "powershell -NoProfile -NonInteractive -EncodedCommand " + encode_powershell(script),
        ]
        proc = self._call(argv, f"command on {self.cfg.host}")
        if proc.returncode == 255:
            raise EngineError(f"ssh to {self.cfg.host} failed:\n{_tail(proc.stderr)}")
        match = _RC_LINE.search(proc.stdout)
        if match is None:
            raise EngineError(
                f"remote command on {self.cfg.host} gave no exit marker; output tail:\n"
                f"{_tail(proc.stdout)}\n{_tail(proc.stderr)}"
            )
        code = int(match.group(1))
        if code != 0:
            raise EngineError(
                f"remote command exited {code}; output tail:\n{_tail(proc.stdout)}\n"
                f"{_tail(proc.stderr)}"
            )
        return proc.stdout

    def mkdir(self, remote_dir: str) -> None:
        self.run(
            [
                f"New-Item -ItemType Directory -Force -ErrorAction Stop -Path {ps_quote(remote_dir)} | Out-Null"
            ]
        )

    def new_run_dir(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return win_join(self.cfg.scratch_root, f"{stamp}-{uuid.uuid4().hex[:6]}")

    def upload(self, local: Path, remote: str) -> None:
        argv = [self.cfg.scp, *SSH_OPTIONS, str(local), f"{self.cfg.host}:{_scp_path(remote)}"]
        self._copy(argv, f"upload {local.name}")

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.cfg.scp, *SSH_OPTIONS, f"{self.cfg.host}:{_scp_path(remote)}", str(local)]
        self._copy(argv, f"download {remote}")

    def _copy(self, argv: list[str], what: str) -> None:
        proc = self._call(argv, what)
        if proc.returncode != 0:
            raise EngineError(f"scp {what} failed (exit {proc.returncode}):\n{_tail(proc.stderr)}")

    def _call(self, argv: list[str], what: str) -> subprocess.CompletedProcess:
        try:
            return self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.cfg.timeout_s,
                **hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"{what} ran past {self.cfg.timeout_s:.0f} s") from exc
        except FileNotFoundError as exc:
            raise EngineError(f"{argv[0]} is not on PATH") from exc


def write_renderer_script(lines: list[Line], path: Path) -> None:
    """Write the spoken lines as `[S1] text` lines. Chapter marks are never written."""
    text = "".join(f"[{line.speaker}] {line.text}\n" for line in lines)
    path.write_text(text, encoding="utf-8", newline="\n")


# --- timing helpers --------------------------------------------------------------------------


def spans_from_durations(durations: list[float], gap_s: float) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for duration in durations:
        spans.append((cursor, cursor + duration))
        cursor += duration + gap_s
    return spans


def fit_to_length(spans: list[tuple[float, float]], actual: float) -> list[tuple[float, float]]:
    """Stretch a timeline so it ends where the real file ends. Removes small estimate errors."""
    raw_total = spans[-1][1]
    if raw_total <= 0 or actual <= 0:
        raise EngineError("cannot fit timings to an empty render")
    scale = actual / raw_total
    return [(start * scale, end * scale) for start, end in spans]


def parse_chatterbox_durations(stdout: str, lines: list[Line]) -> list[float]:
    """Per-line audio lengths printed by render_turbo2.py, checked against the lines sent."""
    found = _CHATTER_LINE.findall(stdout)
    if len(found) != len(lines):
        raise EngineError(
            f"renderer reported {len(found)} lines, expected {len(lines)}; output tail:\n"
            f"{_tail(stdout)}"
        )
    durations: list[float] = []
    for index, (number, speaker, seconds) in enumerate(found):
        if int(number) != index + 1 or speaker != lines[index].speaker:
            raise EngineError(f"renderer line {number} ({speaker}) does not match the script")
        durations.append(float(seconds))
    return durations


def parse_dia2_chunks(data: object, line_count: int) -> list[tuple[int, int, float]]:
    """(first_line, line_count, audio_s) per chunk from the renderer's JSON summary."""
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list) or not chunks:
        raise EngineError("Dia2 summary has no chunks")
    result: list[tuple[int, int, float]] = []
    expected_first = 1
    for chunk in chunks:
        first, count, audio = int(chunk["first_line"]), int(chunk["lines"]), float(chunk["audio_s"])
        if first != expected_first or count < 1:
            raise EngineError(f"Dia2 chunks are not contiguous at line {expected_first}")
        result.append((first, count, audio))
        expected_first += count
    if expected_first - 1 != line_count:
        raise EngineError(
            f"Dia2 summary covers {expected_first - 1} lines, script has {line_count}"
        )
    return result


def dia2_spans(
    chunks: list[tuple[int, int, float]], lines: list[Line], gap_s: float
) -> list[tuple[float, float]]:
    """Chunk edges from the renderer. Each chunk's length is split between its lines by words."""
    spans: list[tuple[float, float]] = [(0.0, 0.0)] * len(lines)
    cursor = 0.0
    for first, count, audio in chunks:
        group = lines[first - 1 : first - 1 + count]
        weights = [max(1, len(line.text.split())) for line in group]
        total = sum(weights)
        at = cursor
        for offset, weight in enumerate(weights):
            length = audio * weight / total
            spans[first - 1 + offset] = (at, at + length)
            at += length
        cursor += audio + gap_s
    return spans


def _result(
    wav: Path,
    engine: str,
    timing: Timing,
    lines: list[Line],
    spans: list[tuple[float, float]],
    duration: float,
) -> RenderResult:
    timings = tuple(
        LineTiming(line.number, line.speaker, round(start, 3), round(end, 3))
        for line, (start, end) in zip(lines, spans, strict=True)
    )
    return RenderResult(wav=wav, engine=engine, timing=timing, lines=timings, duration=duration)


# --- remote command builders -----------------------------------------------------------------


def chatterbox_commands(cfg: ChatterboxConfig, remote_script: str, remote_wav: str) -> list[str]:
    commands = []
    if cfg.hf_home:
        commands.append(f"$env:HF_HOME = {ps_quote(cfg.hf_home)}")
    commands.append(f"Set-Location -LiteralPath {ps_quote(cfg.workdir)}")
    commands.append(
        _invoke([cfg.python, cfg.renderer, cfg.voice_s1, cfg.voice_s2, remote_script, remote_wav])
    )
    return commands


def dia2_commands(cfg: Dia2Config, remote_script: str, remote_wav: str) -> list[str]:
    commands = []
    if cfg.hf_home:
        commands.append(f"$env:HF_HOME = {ps_quote(cfg.hf_home)}")
    commands.append(f"Set-Location -LiteralPath {ps_quote(cfg.workdir)}")
    commands.append(
        _invoke(
            [
                cfg.python,
                cfg.renderer,
                "--script",
                remote_script,
                "--out",
                remote_wav,
                "--ref1",
                cfg.ref_s1,
                "--ref2",
                cfg.ref_s2,
                "--lines-per-chunk",
                str(cfg.lines_per_chunk),
                "--cfg",
                str(cfg.cfg),
                "--temp",
                str(cfg.temp),
                "--gap",
                str(cfg.gap_s),
                "--atempo",
                str(cfg.atempo),
            ]
        )
    )
    return commands


# --- engines ---------------------------------------------------------------------------------


class ChatterboxRemote:
    """Chatterbox Turbo on the GPU box, via the bake-off renderer. Timing is measured."""

    name = "chatterbox"

    def __init__(
        self, box: RemoteConfig, cfg: ChatterboxConfig, runner: Runner = subprocess.run
    ) -> None:
        self.cfg = cfg
        self.box = Box(box, runner)

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        run_dir = self.box.new_run_dir()
        remote_script = win_join(run_dir, "script.txt")
        remote_wav = win_join(run_dir, "voice.wav")
        with tempfile.TemporaryDirectory(prefix="civiccast-chatterbox-") as tmp:
            local_script = Path(tmp) / "script.txt"
            write_renderer_script(lines, local_script)
            self.box.mkdir(run_dir)
            self.box.upload(local_script, remote_script)
        stdout = self.box.run(chatterbox_commands(self.cfg, remote_script, remote_wav))
        durations = parse_chatterbox_durations(stdout, lines)
        self.box.download(remote_wav, out_wav)
        duration = probe_duration(out_wav)
        spans = fit_to_length(spans_from_durations(durations, self.cfg.gap_s), duration)
        return _result(out_wav, self.name, "measured", lines, spans, duration)


class Dia2Remote:
    """Dia2 (chunked, reference-prefixed) on the GPU box, via the bake-off renderer.

    Timing is approximate inside each chunk. See the module docstring.
    """

    name = "dia2"

    def __init__(self, box: RemoteConfig, cfg: Dia2Config, runner: Runner = subprocess.run) -> None:
        self.cfg = cfg
        self.box = Box(box, runner)

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        run_dir = self.box.new_run_dir()
        remote_script = win_join(run_dir, "script.txt")
        stem = win_join(run_dir, "voice")
        remote_wav = stem + ".wav"
        with tempfile.TemporaryDirectory(prefix="civiccast-dia2-") as tmp:
            local_script = Path(tmp) / "script.txt"
            local_json = Path(tmp) / "voice.json"
            write_renderer_script(lines, local_script)
            self.box.mkdir(run_dir)
            self.box.upload(local_script, remote_script)
            self.box.run(dia2_commands(self.cfg, remote_script, remote_wav))
            # The renderer writes the tempo-adjusted copy and a JSON summary next to --out.
            if self.cfg.atempo != 1.0:
                final_remote = f"{stem}-atempo{self.cfg.atempo:.2f}.wav"
            else:
                final_remote = remote_wav
            self.box.download(f"{stem}.json", local_json)
            summary = json.loads(local_json.read_text(encoding="utf-8"))
            self.box.download(final_remote, out_wav)
        chunks = parse_dia2_chunks(summary, len(lines))
        duration = probe_duration(out_wav)
        spans = fit_to_length(dia2_spans(chunks, lines, self.cfg.gap_s), duration)
        return _result(out_wav, self.name, "approximate", lines, spans, duration)


# --- LFM3 Audio (local llama.cpp TTS) --------------------------------------------------------


def lfm3_executable(
    cfg: LfmConfig,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> str:
    """The llama-tts.exe to run: the configured path, then PATH, then the WinGet package path."""
    if cfg.llama_tts:
        return cfg.llama_tts
    found = which("llama-tts")
    if found:
        return found
    env = os.environ if environ is None else environ
    local = (env.get("LOCALAPPDATA") or "").strip()
    if local:
        return str(Path(local) / LFM3_WINGET_EXE)
    return LFM3_WINGET_EXE


class LfmLocal:
    """LFM3 Audio through llama-tts on this PC. Timing is measured, per line.

    Each line is rendered to its own WAV, then the lines are joined in order with `gap_s` of
    silence. A non-zero exit, a run past `timeout_s`, or no audio raises EngineError naming the
    line. See the module docstring.
    """

    name = "lfm3"

    def __init__(self, cfg: LfmConfig, runner: Runner = subprocess.run) -> None:
        self.cfg = cfg
        self._runner = runner

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        exe = lfm3_executable(self.cfg)
        for label, path in (
            ("llama-tts", exe),
            ("LFM3 model", self.cfg.model),
            ("LFM3 mmproj", self.cfg.mmproj),
        ):
            if not Path(path).is_file():
                raise ConfigError(f"{label} not found: {path}")

        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="civiccast-lfm3-", dir=out_wav.parent) as tmp:
            clips: list[Path] = []
            for index, line in enumerate(lines):
                clip = Path(tmp) / f"line-{index + 1:03d}.wav"
                self._render_line(exe, line, clip)
                clips.append(clip)
            pcm, rate, durations = self._join_clips(clips, lines)
        duration = write_pcm_wav(pcm, rate, out_wav)
        spans = fit_to_length(spans_from_durations(durations, self.cfg.gap_s), duration)
        return _result(out_wav, self.name, "measured", lines, spans, duration)

    def _render_line(self, exe: str, line: Line, clip: Path) -> None:
        voice = self.cfg.voice_s1 if line.speaker == "S1" else self.cfg.voice_s2
        argv = [
            exe,
            "-m",
            self.cfg.model,
            "-mm",
            self.cfg.mmproj,
            "-p",
            line.text,
            "--tts-voice",
            voice,
            "-o",
            str(clip),
        ]
        try:
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.cfg.timeout_s,
                **hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(
                f"line {line.number}: llama-tts ran past {self.cfg.timeout_s:.0f} s"
            ) from exc
        except FileNotFoundError as exc:
            raise EngineError(f"line {line.number}: {exe} is not on PATH") from exc
        if proc.returncode != 0:
            raise EngineError(
                f"line {line.number}: llama-tts exited {proc.returncode}:\n{_tail(proc.stderr)}"
            )
        if not clip.is_file() or clip.stat().st_size == 0:
            raise EngineError(f"line {line.number}: llama-tts wrote no audio to {clip.name}")

    def _join_clips(self, clips: list[Path], lines: list[Line]) -> tuple[bytes, int, list[float]]:
        """Read the line WAVs and join their PCM with `gap_s` of silence.

        Every clip must be mono 16-bit and share the first clip's rate. The durations come from
        each WAV's frame count. A clip that cannot be read raises EngineError naming the line.
        """
        rate: int | None = None
        chunks: list[bytes] = []
        durations: list[float] = []
        for clip, line in zip(clips, lines, strict=True):
            try:
                with wave.open(str(clip), "rb") as wav:
                    channels = wav.getnchannels()
                    width = wav.getsampwidth()
                    clip_rate = wav.getframerate()
                    frames = wav.getnframes()
                    data = wav.readframes(frames)
            except (wave.Error, EOFError, OSError) as exc:
                raise EngineError(f"line {line.number}: cannot read {clip.name}: {exc}") from exc
            if channels != 1 or width != 2:
                raise EngineError(
                    f"line {line.number}: {clip.name} is {channels} channel(s) at "
                    f"{width * 8} bits; only mono 16-bit is supported"
                )
            if len(data) != frames * 2:
                raise EngineError(
                    f"line {line.number}: {clip.name} is truncated "
                    f"({len(data)} of {frames * 2} bytes)"
                )
            if rate is None:
                rate = clip_rate
            elif clip_rate != rate:
                raise EngineError(
                    f"line {line.number}: {clip.name} is {clip_rate} Hz, expected {rate} Hz"
                )
            durations.append(frames / clip_rate)
            chunks.append(data)
        if rate is None:
            raise EngineError("no spoken lines to render")
        gap = b"\x00" * (round(self.cfg.gap_s * rate) * 2)
        return gap.join(chunks), rate, durations


# --- Gemini TTS (hosted) ---------------------------------------------------------------------


def gemini_api_key(environ: Mapping[str, str] | None = None) -> str:
    """The Gemini API key: the environment variable, then Credential Manager, or ConfigError.

    The key is only ever returned to the caller. It goes in the ``x-goog-api-key`` header, never in
    a URL, a log line or an error message.
    """
    env = os.environ if environ is None else environ
    from_env = (env.get(GEMINI_API_KEY_ENV) or "").strip()
    if from_env:
        return from_env
    saved = (read_windows_credential(GEMINI_API_KEY_TARGET) or "").strip()
    if saved:
        return saved
    raise ConfigError(
        f"no Gemini API key: set {GEMINI_API_KEY_ENV} or save Credential Manager target "
        f"{GEMINI_API_KEY_TARGET}"
    )


def gemini_requests(lines: list[Line], max_words: int) -> list[list[Line]]:
    """Whole lines in order, each request at most `max_words` words. A longer line is its own."""
    groups: list[list[Line]] = []
    current: list[Line] = []
    words = 0
    for line in lines:
        count = len(line.text.split())
        if current and words + count > max_words:
            groups.append(current)
            current = []
            words = 0
        current.append(line)
        words += count
    if current:
        groups.append(current)
    return groups


def gemini_request_body(cfg: GeminiConfig, lines: list[Line], model: str) -> dict[str, object]:
    """The Interactions body: one text item per line with its speaker, plus the two voices.

    Only the script lines are sent. There is no instruction sentence, because the model would
    speak it.
    """
    return {
        "model": model,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": line.text,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "speaker": _GEMINI_SPEAKER_NAMES[line.speaker],
                            }
                        ],
                    }
                    for line in lines
                ],
            }
        ],
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": {
                "speakers": [
                    {"speaker": "Speaker 1", "voice": cfg.voice_s1},
                    {"speaker": "Speaker 2", "voice": cfg.voice_s2},
                ]
            }
        },
    }


@dataclass(frozen=True)
class GeminiAudio:
    """Raw 16-bit little-endian mono PCM from one response, and its sample rate."""

    pcm: bytes
    sample_rate: int


def gemini_sample_rate(mime_type: object) -> int:
    """Sample rate from `audio/L16;codec=pcm;rate=24000`; the API's 24000 when it is absent."""
    if isinstance(mime_type, str):
        match = _GEMINI_RATE.search(mime_type)
        if match is not None:
            return int(match.group(1))
    return GEMINI_DEFAULT_RATE


def gemini_error_message(data: object) -> str:
    """The API's `error.message`, then a top-level `message`, then an empty string."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        message = data.get("message")
        if isinstance(message, str):
            return message
    return ""


def _gemini_details(data: object, type_suffix: str) -> list[dict]:
    """`error.details` entries whose `@type` ends with `type_suffix`."""
    error = data.get("error") if isinstance(data, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return []
    return [
        detail
        for detail in details
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith(type_suffix)
    ]


def gemini_retry_delay(data: object) -> float | None:
    """Seconds from a `RetryInfo` detail's `retryDelay` (e.g. "27s"), or None."""
    for detail in _gemini_details(data, "google.rpc.RetryInfo"):
        delay = detail.get("retryDelay")
        if not isinstance(delay, str):
            continue
        match = _GEMINI_RETRY_DELAY.match(delay.strip())
        if match is not None:
            return float(match.group(1))
    return None


def gemini_per_day_quota(data: object) -> bool:
    """True if any `QuotaFailure` violation names a per-day quota (nothing to wait out)."""
    for detail in _gemini_details(data, "google.rpc.QuotaFailure"):
        violations = detail.get("violations")
        for violation in violations if isinstance(violations, list) else []:
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _gemini_audio_item(data: object) -> dict[str, object] | None:
    """The last audio item of the last model_output step, or None."""
    steps = data.get("steps") if isinstance(data, dict) else None
    found: dict[str, object] | None = None
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "audio":
                continue
            encoded = item.get("data")
            if isinstance(encoded, str) and encoded:
                found = item
    return found


def _gemini_wav_audio(audio: bytes, status: int) -> GeminiAudio:
    """PCM and rate from a complete WAV file. Only mono 16-bit is accepted."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            pcm = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise EngineError(f"Gemini WAV audio could not be read (HTTP {status})") from exc
    if channels != 1 or width != 2:
        raise EngineError(
            f"Gemini WAV audio is {channels} channel(s) at {width * 8} bits (HTTP {status}); "
            "only mono 16-bit is supported"
        )
    return GeminiAudio(pcm, rate)


def parse_gemini_audio(data: object, status: int) -> GeminiAudio:
    """The last audio item of an interaction, as PCM. No audio item is an EngineError."""
    item = _gemini_audio_item(data)
    if item is None:
        state = data.get("status") if isinstance(data, dict) else None
        raise EngineError(f"Gemini returned no audio (HTTP {status}, status {state!r})")
    encoded = typing.cast(str, item["data"])
    mime_type = item.get("mime_type")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise EngineError(f"Gemini audio is not valid base64 (HTTP {status})") from exc
    if audio.startswith(b"RIFF") or (
        isinstance(mime_type, str) and mime_type.startswith("audio/wav")
    ):
        return _gemini_wav_audio(audio, status)
    if len(audio) % 2:
        raise EngineError(f"Gemini audio is not whole 16-bit samples (HTTP {status})")
    return GeminiAudio(audio, gemini_sample_rate(mime_type))


def write_pcm_wav(pcm: bytes, sample_rate: int, path: Path) -> float:
    """Write 16-bit little-endian mono PCM as a WAV. Returns the exact length in seconds."""
    if len(pcm) % 2:
        raise EngineError("cannot write a WAV from an odd number of PCM bytes")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return len(pcm) / (2 * sample_rate)


def gemini_spans(
    groups: list[list[Line]], durations: list[float], gap_s: float
) -> list[tuple[float, float]]:
    """Request edges from the exact request lengths. Inside a request, split by word count."""
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for group, audio in zip(groups, durations, strict=True):
        weights = [max(1, len(line.text.split())) for line in group]
        total = sum(weights)
        at = cursor
        for weight in weights:
            length = audio * weight / total
            spans.append((at, at + length))
            at += length
        cursor += audio + gap_s
    return spans


class GeminiTTS:
    """Gemini TTS over its REST API. Timing is approximate inside each request.

    One request carries whole script lines up to `max_words_per_request` words. The request WAVs
    are joined in order with `gap_s` of silence. A per-minute 429 waits the server's `RetryInfo`
    delay and retries the same model, then falls back; a per-day quota or a long wait falls back at
    once. See the module docstring.
    """

    name = "gemini"

    def __init__(
        self,
        cfg: GeminiConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self._transport = transport
        self._api_key = api_key
        self._sleep = sleep

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        key = self._api_key or gemini_api_key()
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        groups = gemini_requests(lines, self.cfg.max_words_per_request)
        clips: list[GeminiAudio] = []
        used: set[str] = set()
        with httpx.Client(timeout=self.cfg.timeout_s, transport=self._transport) as client:
            for group in groups:
                clip, models = self._request(client, group, key)
                clips.append(clip)
                used.update(models)

        rate = clips[0].sample_rate
        for clip in clips:
            if clip.sample_rate != rate:
                raise EngineError(
                    f"Gemini returned mixed sample rates ({rate} and {clip.sample_rate} Hz)"
                )
        gap_pcm = b"\x00" * (round(self.cfg.gap_s * rate) * 2)
        pcm = gap_pcm.join(clip.pcm for clip in clips)
        duration = write_pcm_wav(pcm, rate, out_wav)
        durations = [len(clip.pcm) / (2 * rate) for clip in clips]
        spans = fit_to_length(gemini_spans(groups, durations, self.cfg.gap_s), duration)
        name = f"gemini:{self.cfg.model}"
        if self.cfg.fallback_model and self.cfg.fallback_model in used:
            name += f"+{self.cfg.fallback_model}"
        return _result(out_wav, name, "approximate", lines, spans, duration)

    def _request(
        self, client: httpx.Client, group: list[Line], key: str
    ) -> tuple[GeminiAudio, list[str]]:
        body = gemini_request_body(self.cfg, group, self.cfg.model)
        response = self._post_retrying(client, self.cfg.model, body, key)
        models = [self.cfg.model]
        if response.status_code == 429:
            if not self.cfg.fallback_model:
                raise EngineError(
                    f"Gemini model {self.cfg.model} is out of quota (HTTP 429) "
                    "and no fallback model is set"
                )
            fallback_body = gemini_request_body(self.cfg, group, self.cfg.fallback_model)
            response = self._post_retrying(client, self.cfg.fallback_model, fallback_body, key)
            models.append(self.cfg.fallback_model)
            if response.status_code == 429:
                raise EngineError(
                    f"Gemini is out of quota: both {self.cfg.model} and "
                    f"{self.cfg.fallback_model} answered HTTP 429"
                )
        data = self._json(response)
        if response.status_code != 200:
            message = gemini_error_message(data) or "no error message"
            raise EngineError(f"Gemini answered HTTP {response.status_code}: {message}")
        return parse_gemini_audio(data, response.status_code), models

    def _post_retrying(
        self, client: httpx.Client, model: str, body: dict[str, object], key: str
    ) -> httpx.Response:
        """POST once, then wait out per-minute 429s on the same model up to the retry limit.

        A per-day quota or a wait longer than `max_retry_wait_s` returns the 429 as-is so the
        caller can fall back at once. Any other status is returned as-is.
        """
        response = self._post(client, model, body, key)
        retries = 0
        while response.status_code == 429 and retries < self.cfg.retries_per_model:
            data = self._json_or_empty(response)
            if gemini_per_day_quota(data):
                return response
            wait = gemini_retry_delay(data)
            if wait is None:
                wait = self.cfg.retry_wait_s
            if wait > self.cfg.max_retry_wait_s:
                return response
            seconds = wait + 1.0
            print(f"gemini: rate limited on {model}, waiting {seconds:.0f} s", file=sys.stderr)
            self._sleep(seconds)
            retries += 1
            response = self._post(client, model, body, key)
        return response

    def _post(
        self, client: httpx.Client, model: str, body: dict[str, object], key: str
    ) -> httpx.Response:
        try:
            return client.post(GEMINI_API_URL, json=body, headers={"x-goog-api-key": key})
        except httpx.HTTPError as exc:
            raise EngineError(
                f"cannot reach Gemini for model {model} ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise EngineError(f"Gemini answered HTTP {response.status_code} with no JSON") from exc

    @staticmethod
    def _json_or_empty(response: httpx.Response) -> object:
        """Parse a 429 body for its quota details; a body that is not JSON is treated as empty."""
        try:
            return response.json()
        except ValueError:
            return {}


def fake_duration(text: str) -> float:
    """Predictable tone length for FakeEngine: 0.3 s per word, at least 0.5 s."""
    return max(0.5, round(0.3 * len(text.split()), 3))


class FakeEngine:
    """Tones for speech and silence for gaps, written with ffmpeg. Tests only; no speech.

    Speaker S1 is a 440 Hz tone and S2 is 220 Hz. Timing is exact.
    """

    name = "fake"

    def __init__(self, gap_s: float = 0.35, sample_rate: int = 24000) -> None:
        self.gap_s = gap_s
        self.sample_rate = sample_rate

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        sr = self.sample_rate
        durations = [fake_duration(line.text) for line in lines]
        graph: list[str] = []
        labels: list[str] = []
        for index, (line, duration) in enumerate(zip(lines, durations, strict=True)):
            freq = 440 if line.speaker == "S1" else 220
            graph.append(
                f"sine=frequency={freq}:sample_rate={sr}:duration={duration:.3f}[s{index}]"
            )
            labels.append(f"[s{index}]")
            if index < len(lines) - 1:
                graph.append(
                    f"anullsrc=sample_rate={sr}:channel_layout=mono:duration={self.gap_s:.3f}"
                    f"[g{index}]"
                )
                labels.append(f"[g{index}]")
        graph.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]")
        run_ffmpeg(
            [
                "-filter_complex",
                ";".join(graph),
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ],
            what="fake engine",
        )
        spans = spans_from_durations(durations, self.gap_s)
        return _result(out_wav, self.name, "exact", lines, spans, probe_duration(out_wav))
