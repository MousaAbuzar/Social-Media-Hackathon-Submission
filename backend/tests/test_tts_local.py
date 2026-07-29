"""Self-hosted TTS adapter tests.

The point of these is that the request we build is right — mode, filenames,
chunking — since a wrong payload silently produces the wrong voice rather than
an error. No server is contacted; the HTTP call is stubbed.
"""

import json

import httpx
import pytest

from app.providers import tts_local
from app.providers.base import TTSProvider
from app.providers.tts_local import LocalTTS

VOICES = [
    {
        "id": "narrator_cloned",
        "label": "My Narrator",
        "mode": "clone",
        "external_id": "my_narrator.wav",
        "description": "Cloned.",
    },
    {
        "id": "narrator_builtin",
        "label": "Built-in",
        "mode": "predefined",
        "external_id": "Abigail.wav",
    },
]


@pytest.fixture
def voices_file(tmp_path, monkeypatch):
    path = tmp_path / "voices.local.json"
    path.write_text(json.dumps(VOICES), encoding="utf-8")
    monkeypatch.setattr(tts_local, "VOICES_FILE", path)
    return path


@pytest.fixture
def captured(monkeypatch):
    """Capture the outgoing request instead of making it."""
    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs["json"]
        return httpx.Response(
            200,
            content=b"RIFFfake-audio",
            headers={"content-type": "audio/wav"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tts_local.httpx, "post", fake_post)
    return sent


def test_satisfies_the_provider_protocol():
    assert isinstance(LocalTTS(base_url="http://localhost:8004"), TTSProvider)


def test_voices_load_from_file(voices_file):
    voices = LocalTTS(base_url="http://localhost:8004").voices()
    assert [v.id for v in voices] == ["narrator_cloned", "narrator_builtin"]
    assert all(v.provider == "local" for v in voices)


def test_missing_voices_file_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_local, "VOICES_FILE", tmp_path / "absent.json")
    assert LocalTTS(base_url="http://localhost:8004").voices() == []


def test_clone_voice_sends_reference_audio(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    voice = next(v for v in tts.voices() if v.id == "narrator_cloned")

    result = tts.synthesize(text="Hello there.", voice=voice)

    assert captured["url"] == "http://localhost:8004/tts"
    assert captured["json"]["voice_mode"] == "clone"
    assert captured["json"]["reference_audio_filename"] == "my_narrator.wav"
    assert "predefined_voice_id" not in captured["json"]
    assert result.audio == b"RIFFfake-audio"
    assert result.characters == len("Hello there.")


def test_predefined_voice_sends_voice_id(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    voice = next(v for v in tts.voices() if v.id == "narrator_builtin")

    tts.synthesize(text="Hello there.", voice=voice)

    assert captured["json"]["voice_mode"] == "predefined"
    assert captured["json"]["predefined_voice_id"] == "Abigail.wav"
    assert "reference_audio_filename" not in captured["json"]


def test_long_scripts_are_chunked_server_side(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    voice = tts.voices()[0]

    tts.synthesize(text="word " * 1200, voice=voice)

    assert captured["json"]["split_text"] is True
    assert captured["json"]["chunk_size"] > 0


def test_unknown_mode_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "voices.local.json"
    path.write_text(
        json.dumps([{"id": "x", "label": "X", "mode": "wat", "external_id": "a.wav"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(tts_local, "VOICES_FILE", path)

    tts = LocalTTS(base_url="http://localhost:8004")
    with pytest.raises(ValueError, match="wat"):
        tts.synthesize(text="hi", voice=tts.voices()[0])


def test_self_hosted_synthesis_is_free():
    assert LocalTTS(base_url="http://localhost:8004").cost_micros(100_000) == 0


def test_unreachable_server_explains_itself(voices_file, monkeypatch):
    def refuse(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tts_local.httpx, "post", refuse)
    tts = LocalTTS(base_url="http://localhost:8004")

    with pytest.raises(RuntimeError, match="docs/local-tts.md"):
        tts.synthesize(text="hi", voice=tts.voices()[0])
