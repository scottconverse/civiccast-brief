"""voices.json: the city's voice engine, its fallback and the two hosts' voices."""

from __future__ import annotations

import json

import pytest

from civiccast_brief.review import voices


def test_a_missing_file_gives_empty_settings_with_no_overrides(tmp_path):
    settings = voices.load(tmp_path)
    assert (settings.engine, settings.fallback_engine, settings.audio) == ("", "", {})


def test_save_and_load_round_trip(tmp_path):
    settings = voices.Voices(
        engine="gemini",
        fallback_engine="lfm3",
        audio={"gemini": {"voice_s1": "Bodi", "voice_s2": "Sola"}},
    )
    path = voices.save(tmp_path, settings)
    assert path == tmp_path / "voices.json"
    assert voices.load(tmp_path) == settings
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "engine": "gemini",
        "fallback_engine": "lfm3",
        "audio": {"gemini": {"voice_s1": "Bodi", "voice_s2": "Sola"}},
    }
    assert not (tmp_path / "voices.json.tmp").exists()


@pytest.mark.parametrize("engine", ["fake", "kokoro", "Gemini"])
def test_a_bad_engine_is_named_at_its_field(tmp_path, engine):
    with pytest.raises(voices.VoicesError) as bad:
        voices.save(tmp_path, voices.Voices(engine=engine))
    assert bad.value.field == "engine"
    assert not (tmp_path / "voices.json").exists()


def test_the_fallback_must_be_a_different_engine(tmp_path):
    with pytest.raises(voices.VoicesError) as bad:
        voices.save(tmp_path, voices.Voices(engine="gemini", fallback_engine="gemini"))
    assert bad.value.field == "fallback_engine"
    assert "different engine" in str(bad.value)


def test_an_empty_voice_is_an_error(tmp_path):
    with pytest.raises(voices.VoicesError) as bad:
        voices.save(tmp_path, voices.Voices(engine="gemini", audio={"gemini": {"voice_s1": " "}}))
    assert bad.value.field == "gemini-voice_s1"
    assert "Enter a voice" in str(bad.value)


def test_a_voice_cannot_hold_a_control_character(tmp_path):
    with pytest.raises(voices.VoicesError) as bad:
        voices.save(tmp_path, voices.Voices(engine="lfm3", audio={"lfm3": {"voice_s2": "ma\nle"}}))
    assert bad.value.field == "lfm3-voice_s2"


def test_a_voice_is_at_most_eighty_characters(tmp_path):
    with pytest.raises(voices.VoicesError) as bad:
        voices.save(tmp_path, voices.Voices(engine="lfm3", audio={"lfm3": {"voice_s1": "x" * 81}}))
    assert bad.value.field == "lfm3-voice_s1"


def test_an_unknown_audio_key_is_an_error(tmp_path):
    path = tmp_path / "voices.json"
    path.write_text(
        json.dumps({"engine": "gemini", "audio": {"gemini": {"voice_s3": "Nobody"}}}),
        encoding="utf-8",
    )
    with pytest.raises(voices.VoicesError) as bad:
        voices.load(tmp_path)
    assert "unknown keys" in str(bad.value)
    assert "'voice_s3'" in str(bad.value)


def test_a_file_that_is_not_json_is_an_error_naming_the_path(tmp_path):
    (tmp_path / "voices.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(voices.VoicesError) as bad:
        voices.load(tmp_path)
    assert "voices.json" in str(bad.value) and "not readable JSON" in str(bad.value)


def test_voice_value_falls_back_to_the_code_default(tmp_path):
    empty = voices.Voices()
    assert voices.voice_value(empty, "gemini", "voice_s1") == "Bodi"
    assert voices.default_voice("lfm3", "voice_s2") == "female"
    chosen = voices.Voices(engine="gemini", audio={"gemini": {"voice_s1": "Puck"}})
    assert voices.voice_value(chosen, "gemini", "voice_s1") == "Puck"
    assert voices.voice_value(chosen, "gemini", "voice_s2") == "Sola"


def test_from_form_writes_only_the_voices_changed_from_the_code_default():
    form = {
        "engine": "gemini",
        "fallback_engine": "lfm3",
        "gemini-voice_s1": "Bodi",  # the code default: left out
        "gemini-voice_s2": "Puck",  # changed: kept
    }
    chosen = voices.from_form(form, voices.Voices())
    assert (chosen.engine, chosen.fallback_engine) == ("gemini", "lfm3")
    assert chosen.audio == {"gemini": {"voice_s2": "Puck"}}


def test_from_form_keeps_another_engines_voices():
    current = voices.Voices(engine="gemini", audio={"lfm3": {"voice_s1": "ryan"}})
    form = {
        "engine": "gemini",
        "fallback_engine": "",
        "gemini-voice_s1": "Kore",
        "gemini-voice_s2": "Sola",
        "lfm3-voice_s1": "ryan",
        "lfm3-voice_s2": "female",
    }
    chosen = voices.from_form(form, current)
    assert chosen.audio == {"gemini": {"voice_s1": "Kore"}, "lfm3": {"voice_s1": "ryan"}}


def test_from_form_with_none_for_the_fallback_and_every_default_voice():
    chosen = voices.from_form(
        {
            "engine": "lfm3",
            "fallback_engine": "",
            "lfm3-voice_s1": "male",
            "lfm3-voice_s2": "female",
        },
        voices.Voices(),
    )
    assert chosen.fallback_engine == ""
    assert chosen.audio == {}


def test_write_audio_config_writes_only_the_audio_part(tmp_path):
    settings = voices.Voices(
        engine="gemini", fallback_engine="lfm3", audio={"gemini": {"voice_s1": "Puck"}}
    )
    out = tmp_path / "work" / "audio-config.json"
    assert voices.write_audio_config(out, settings) == out
    assert json.loads(out.read_text(encoding="utf-8")) == {"gemini": {"voice_s1": "Puck"}}


def test_saved_notice_names_the_engine_and_its_voices():
    settings = voices.Voices(engine="gemini", audio={"gemini": {"voice_s2": "Puck"}})
    assert (
        voices.saved_notice(settings) == "Saved. The next episode uses Gemini, with Bodi and Puck."
    )
