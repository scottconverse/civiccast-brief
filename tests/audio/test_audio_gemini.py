"""Gemini TTS: request shape, WAV writing, splitting, quota fallback and errors. No network."""

import base64
import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from civiccast_brief.audio.engines import (
    DEFAULT_ENGINE,
    GEMINI_API_KEY_ENV,
    AudioConfig,
    GeminiAudio,
    GeminiConfig,
    GeminiTTS,
    gemini_api_key,
    gemini_requests,
    gemini_sample_rate,
    gemini_spans,
    make_engine,
    parse_gemini_audio,
)
from civiccast_brief.audio.errors import ConfigError, EngineError
from civiccast_brief.audio.lines import Line

KEY = "not-a-real-key-123"
RATE = 24000
LINES = [
    Line(1, "S1", "This is CivicCast Brief, for the November twentieth session."),
    Line(2, "S2", "The public comment period ran for about ten minutes."),
    Line(3, "S1", "Council approved the minutes."),
]


def _pcm(seconds: float, rate: int = RATE) -> bytes:
    return b"\x00\x00" * round(seconds * rate)


def _wav(seconds: float, rate: int = RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(_pcm(seconds, rate))
    return buffer.getvalue()


def _audio_item(data: bytes, mime_type: str) -> dict:
    return {
        "type": "audio",
        "mime_type": mime_type,
        "data": base64.b64encode(data).decode("ascii"),
    }


def _reply(seconds: float | None = 3.0, rate: int = RATE, status: str = "completed") -> dict:
    content = [] if seconds is None else [_audio_item(_wav(seconds, rate), "audio/wav")]
    return {
        "id": "v1_test",
        "status": status,
        "steps": [{"type": "model_output", "content": content}],
        "object": "interaction",
        "model": "gemini-3.8-flash-tts",
    }


class Transport:
    """An httpx.MockTransport handler that replays canned responses and records requests."""

    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, payload = self.responses.pop(0)
        return httpx.Response(status, json=payload)

    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


def _engine(
    transport: Transport,
    cfg: GeminiConfig | None = None,
    sleeps: list[float] | None = None,
) -> GeminiTTS:
    return GeminiTTS(
        cfg or GeminiConfig(),
        transport=transport.transport,
        api_key=KEY,
        sleep=(sleeps if sleeps is not None else []).append,
    )


# --- request shape ---------------------------------------------------------------------------


def test_request_uses_the_interactions_url_with_the_key_in_a_header(tmp_path: Path) -> None:
    transport = Transport([(200, _reply())])
    _engine(transport).render(list(LINES), tmp_path / "voice.wav")

    (request,) = transport.requests
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert KEY not in str(request.url)
    assert request.headers["x-goog-api-key"] == KEY
    assert transport.bodies()[0]["model"] == "gemini-3.8-flash-tts"


def test_request_has_one_content_item_per_line_with_its_speaker_and_the_voices(
    tmp_path: Path,
) -> None:
    transport = Transport([(200, _reply())])
    _engine(transport).render(list(LINES), tmp_path / "voice.wav")

    body = transport.bodies()[0]
    assert body["response_format"] == {"type": "audio"}

    speakers = body["generation_config"]["speech_config"]["speakers"]
    assert [s["speaker"] for s in speakers] == ["Speaker 1", "Speaker 2"]
    assert [s["voice"] for s in speakers] == ["Bodi", "Sola"]

    (user_input,) = body["input"]
    assert user_input["type"] == "user_input"
    items = user_input["content"]
    assert [item["type"] for item in items] == ["text", "text", "text"]
    assert [item["text"] for item in items] == [line.text for line in LINES]
    assert [[annotation["speaker"] for annotation in item["annotations"]] for item in items] == [
        ["Speaker 1"],
        ["Speaker 2"],
        ["Speaker 1"],
    ]
    assert all(
        annotation["type"] == "speech_metadata"
        for item in items
        for annotation in item["annotations"]
    )
    # No instruction sentence: it would be spoken.
    assert all("Read this two-host" not in item["text"] for item in items)


def test_voice_names_in_the_request_come_from_the_config(tmp_path: Path) -> None:
    transport = Transport([(200, _reply())])
    cfg = GeminiConfig(voice_s1="Fola", voice_s2="Rami")
    _engine(transport, cfg).render(list(LINES), tmp_path / "voice.wav")
    speakers = transport.bodies()[0]["generation_config"]["speech_config"]["speakers"]
    assert [s["voice"] for s in speakers] == ["Fola", "Rami"]


# --- the WAV and its timings -----------------------------------------------------------------


def test_wav_response_becomes_a_24k_mono_wav_with_one_timing_per_line(tmp_path: Path) -> None:
    transport = Transport([(200, _reply(seconds=3.0))])
    out = tmp_path / "out" / "voice.wav"
    result = _engine(transport).render(list(LINES), out)

    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == RATE
        assert wav.getnframes() == RATE * 3

    assert result.engine == "gemini:gemini-3.8-flash-tts"
    assert result.timing == "approximate"
    assert [line.number for line in result.lines] == [1, 2, 3]
    assert [line.speaker for line in result.lines] == ["S1", "S2", "S1"]
    assert result.lines[0].start == 0.0
    assert result.lines[-1].end == pytest.approx(result.duration, abs=0.01)
    for earlier, later in zip(result.lines, result.lines[1:]):
        assert earlier.end <= later.start
    assert result.duration == pytest.approx(3.0, abs=0.001)


def test_l16_raw_pcm_response_still_works(tmp_path: Path) -> None:
    payload = {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [_audio_item(_pcm(2.0, 48000), "audio/l16;codec=pcm;rate=48000")],
            }
        ],
    }
    transport = Transport([(200, payload)])
    out = tmp_path / "voice.wav"
    result = _engine(transport).render(list(LINES), out)

    with wave.open(str(out), "rb") as wav:
        assert wav.getframerate() == 48000
        assert wav.getnframes() == 48000 * 2
    assert result.duration == pytest.approx(2.0, abs=0.001)


def test_the_last_model_output_audio_item_is_used() -> None:
    payload = {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [_audio_item(_wav(1.0), "audio/wav")],
            },
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "ignored"}],
            },
            {
                "type": "model_output",
                "content": [_audio_item(_wav(2.0), "audio/wav")],
            },
        ],
    }
    clip = parse_gemini_audio(payload, 200)
    assert clip.sample_rate == RATE
    assert len(clip.pcm) == RATE * 2 * 2


def test_sample_rate_is_read_from_the_mime_type() -> None:
    assert gemini_sample_rate("audio/L16;codec=pcm;rate=24000") == 24000
    assert gemini_sample_rate("audio/L16;codec=pcm;rate=48000") == 48000
    assert gemini_sample_rate(None) == RATE


def test_parse_gemini_audio_reports_missing_audio_with_status_and_status_field() -> None:
    with pytest.raises(EngineError, match=r"HTTP 200, status 'incomplete'"):
        parse_gemini_audio(_reply(seconds=None, status="incomplete"), 200)


# --- splitting -------------------------------------------------------------------------------


def test_a_long_script_is_split_into_several_requests_and_one_wav(tmp_path: Path) -> None:
    lines = [Line(i, "S1" if i % 2 else "S2", "word " * 5) for i in range(1, 6)]  # 5 words each
    transport = Transport([(200, _reply(seconds=2.0)) for _ in lines])
    cfg = GeminiConfig(max_words_per_request=10)
    out = tmp_path / "voice.wav"
    result = _engine(transport, cfg).render(lines, out)

    assert len(transport.requests) == 3  # two 5-word lines per request; the last line alone
    with wave.open(str(out), "rb") as wav:
        frames = wav.getnframes()
    # 3 requests of 2 s each plus two 0.35 s gaps.
    assert frames == round((3 * 2.0 + 2 * 0.35) * RATE)
    assert result.duration == pytest.approx(3 * 2.0 + 2 * 0.35, abs=0.001)
    assert [line.number for line in result.lines] == [1, 2, 3, 4, 5]


def test_gemini_requests_keep_whole_lines_in_order_and_a_long_line_alone() -> None:
    lines = [
        Line(1, "S1", "one two three four five six"),  # 6
        Line(2, "S2", "seven eight nine ten eleven twelve"),  # 6
        Line(3, "S1", "thirteen"),  # 1
        Line(4, "S2", "a very long line with many more words than the limit"),  # 11
    ]
    groups = gemini_requests(lines, 10)
    assert [[line.number for line in group] for group in groups] == [[1], [2, 3], [4]]


def test_gemini_spans_split_each_request_by_word_count_and_add_the_gap() -> None:
    group = [Line(1, "S1", "one two three"), Line(2, "S2", "four")]
    spans = gemini_spans([group], [4.0], 0.35)
    assert spans[0] == (0.0, pytest.approx(3.0))
    assert spans[1] == (pytest.approx(3.0), pytest.approx(4.0))


# --- quota fallback --------------------------------------------------------------------------


def _retry_429(delay: str) -> dict:
    return {
        "error": {
            "message": "rate limited",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}],
        }
    }


def _per_day_429() -> dict:
    return {
        "error": {
            "message": "quota",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                    ],
                }
            ],
        }
    }


def test_a_per_minute_429_waits_out_retry_info_and_keeps_the_same_model(tmp_path: Path) -> None:
    transport = Transport([(429, _retry_429("20s")), (200, _reply())])
    sleeps: list[float] = []
    result = _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")

    assert sleeps == [pytest.approx(21.0)]
    assert len(transport.requests) == 2
    assert [b["model"] for b in transport.bodies()] == ["gemini-3.8-flash-tts"] * 2
    assert result.engine == "gemini:gemini-3.8-flash-tts"


def test_a_429_with_no_details_waits_the_default_twice_then_succeeds(tmp_path: Path) -> None:
    transport = Transport(
        [
            (429, {"error": {"message": "quota"}}),
            (429, {"error": {"message": "quota"}}),
            (200, _reply()),
        ]
    )
    sleeps: list[float] = []
    result = _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")

    assert sleeps == [pytest.approx(61.0), pytest.approx(61.0)]
    assert len(transport.requests) == 3
    assert [b["model"] for b in transport.bodies()] == ["gemini-3.8-flash-tts"] * 3
    assert result.engine == "gemini:gemini-3.8-flash-tts"


def test_a_per_day_quota_does_not_wait_and_uses_the_fallback(tmp_path: Path) -> None:
    transport = Transport([(429, _per_day_429()), (200, _reply())])
    sleeps: list[float] = []
    result = _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")

    assert sleeps == []
    assert len(transport.requests) == 2
    assert transport.bodies()[1]["model"] == "gemini-3.8-flash-lite-tts"
    assert result.engine == "gemini:gemini-3.8-flash-tts+gemini-3.8-flash-lite-tts"


def test_a_retry_delay_over_the_max_does_not_wait_and_uses_the_fallback(tmp_path: Path) -> None:
    transport = Transport([(429, _retry_429("300s")), (200, _reply())])
    sleeps: list[float] = []
    result = _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")

    assert sleeps == []
    assert transport.bodies()[1]["model"] == "gemini-3.8-flash-lite-tts"
    assert result.engine == "gemini:gemini-3.8-flash-tts+gemini-3.8-flash-lite-tts"


def test_429_on_the_main_model_retries_then_uses_the_fallback(tmp_path: Path) -> None:
    transport = Transport([(429, {"error": {"message": "quota"}})] * 3 + [(200, _reply())])
    sleeps: list[float] = []
    result = _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")

    assert sleeps == [pytest.approx(61.0)] * 2
    assert len(transport.requests) == 4
    assert [b["model"] for b in transport.bodies()] == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
    ]
    assert result.engine == "gemini:gemini-3.8-flash-tts+gemini-3.8-flash-lite-tts"


def test_429_on_both_models_is_an_engine_error_that_names_quota_and_not_the_key(
    tmp_path: Path,
) -> None:
    transport = Transport([(429, {"error": {"message": "quota"}})] * 6)
    sleeps: list[float] = []
    with pytest.raises(EngineError) as caught:
        _engine(transport, sleeps=sleeps).render(list(LINES), tmp_path / "voice.wav")
    assert "out of quota" in str(caught.value)
    assert KEY not in str(caught.value)
    assert len(transport.requests) == 6


def test_429_with_no_fallback_model_is_an_engine_error(tmp_path: Path) -> None:
    transport = Transport([(429, {"error": {"message": "quota"}})] * 3)
    cfg = GeminiConfig(fallback_model="")
    sleeps: list[float] = []
    with pytest.raises(EngineError, match="out of quota"):
        _engine(transport, cfg, sleeps).render(list(LINES), tmp_path / "voice.wav")
    assert sleeps == [pytest.approx(61.0)] * 2
    assert len(transport.requests) == 3


# --- other errors ----------------------------------------------------------------------------


def test_a_400_carries_the_api_error_message_and_never_the_key(tmp_path: Path) -> None:
    transport = Transport([(400, {"error": {"message": "voice 'Bodi' is not supported"}})])
    with pytest.raises(EngineError) as caught:
        _engine(transport).render(list(LINES), tmp_path / "voice.wav")
    assert "HTTP 400" in str(caught.value)
    assert "voice 'Bodi' is not supported" in str(caught.value)
    assert KEY not in str(caught.value)


def test_a_400_with_a_top_level_message_is_reported(tmp_path: Path) -> None:
    transport = Transport([(400, {"message": "bad request"})])
    with pytest.raises(EngineError, match="HTTP 400: bad request"):
        _engine(transport).render(list(LINES), tmp_path / "voice.wav")


# --- the key ---------------------------------------------------------------------------------


def test_no_key_in_env_or_the_store_is_a_config_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        "civiccast_brief.audio.engines.read_windows_credential", lambda target: None
    )
    with pytest.raises(ConfigError, match="no Gemini API key"):
        GeminiTTS(GeminiConfig()).render(list(LINES), tmp_path / "voice.wav")


def test_the_env_var_wins_over_the_store(monkeypatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "from-env")
    monkeypatch.setattr(
        "civiccast_brief.audio.engines.read_windows_credential", lambda target: "from-store"
    )
    assert gemini_api_key() == "from-env"


def test_the_store_is_used_when_the_env_var_is_unset(monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    seen = []

    def reader(target: str) -> str:
        seen.append(target)
        return "from-store"

    monkeypatch.setattr("civiccast_brief.audio.engines.read_windows_credential", reader)
    assert gemini_api_key() == "from-store"
    assert seen == ["CivicCastBrief/gemini-api"]


# --- wiring ----------------------------------------------------------------------------------


def test_make_engine_builds_gemini_and_dia2_is_still_the_default() -> None:
    assert isinstance(make_engine("gemini", AudioConfig()), GeminiTTS)
    assert DEFAULT_ENGINE == "dia2"
    assert GeminiAudio(b"\x00\x00", RATE).sample_rate == RATE
