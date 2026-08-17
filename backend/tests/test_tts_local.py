"""Self-hosted TTS adapter tests.

The point of these is that the request we build is right — mode, filenames,
chunking — since a wrong payload silently produces the wrong voice rather than
an error. No server is contacted; the HTTP call is stubbed.
"""

import io
import json
import wave

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


def make_wav(frames: int = 100) -> bytes:
    """A real, minimal WAV. The adapter now joins parts with the `wave`
    module, so a stub that isn't parseable audio no longer stands in."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * frames)
    return buffer.getvalue()


def wav_frames(data: bytes) -> int:
    with wave.open(io.BytesIO(data), "rb") as r:
        return r.getnframes()


@pytest.fixture
def captured(monkeypatch):
    """Capture outgoing requests instead of making them.

    `json` is the last request sent, which is what the single-request tests
    care about; `all` is every request, for the chunked ones.
    """
    sent: dict = {"all": []}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs["json"]
        sent["all"].append(kwargs["json"])
        return httpx.Response(
            200,
            content=make_wav(),
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
    assert result.audio[:4] == b"RIFF"
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


# --- Long scripts go out as several requests ----------------------------
#
# A 30-minute script was one HTTP request held open for over an hour. Anything
# that interrupted it — a restart, a crash, a dropped connection — lost the
# whole synthesis and the retry started from nothing.


def test_a_long_script_is_sent_as_several_requests(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    voice = tts.voices()[0]
    script = "This is a sentence about space. " * 900  # ~29k chars, a 30-min script

    result = tts.synthesize(text=script, voice=voice)

    assert len(captured["all"]) > 1, "a 30-minute script must not be one request"
    limit = tts.settings.tts_local_request_chars
    assert all(len(r["text"]) <= limit for r in captured["all"])
    # Every piece has to reach the server — a dropped one is a silent gap in
    # the narration, which is worse than an error.
    assert "".join(r["text"] for r in captured["all"]).replace(" ", "") == script.replace(" ", "")
    assert result.characters == len(script)


def test_the_pieces_are_joined_into_one_playable_file(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    voice = tts.voices()[0]

    result = tts.synthesize(text="This is a sentence about space. " * 900, voice=voice)

    assert result.audio[:4] == b"RIFF"
    # Joined, not just the last piece: total length is the sum of the parts.
    assert wav_frames(result.audio) == 100 * len(captured["all"])


def test_a_short_script_still_goes_out_as_one_request(voices_file, captured):
    tts = LocalTTS(base_url="http://localhost:8004")
    tts.synthesize(text="Short and sweet.", voice=tts.voices()[0])
    assert len(captured["all"]) == 1


def test_splitting_keeps_sentences_whole():
    pieces = tts_local.split_for_requests("One. Two. Three. Four. Five.", limit=12)
    assert len(pieces) > 1
    # A cut mid-sentence would change how the narrator reads the line.
    assert all(p.endswith(".") for p in pieces)


def test_a_sentence_longer_than_the_limit_still_ships():
    monster = "word " * 500
    pieces = tts_local.split_for_requests(monster, limit=100)
    # Better oversized than dropped — the server sub-chunks it anyway.
    assert "".join(pieces).replace(" ", "") == monster.replace(" ", "")


def test_a_dropped_connection_is_retried_not_fatal(voices_file, monkeypatch):
    """The failure that lost your 30-minute script.

    One transient blip used to fail the stage outright, throwing away every
    piece already synthesized.
    """
    monkeypatch.setattr(tts_local.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def flaky_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(
            200,
            content=make_wav(),
            headers={"content-type": "audio/wav"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tts_local.httpx, "post", flaky_post)
    tts = LocalTTS(base_url="http://localhost:8004")

    result = tts.synthesize(text="Hello there.", voice=tts.voices()[0])

    assert calls["n"] == 2
    assert result.audio[:4] == b"RIFF"


def test_a_server_that_never_comes_back_reports_which_piece_failed(voices_file, monkeypatch):
    monkeypatch.setattr(tts_local.time, "sleep", lambda _: None)

    def dead_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tts_local.httpx, "post", dead_post)
    tts = LocalTTS(base_url="http://localhost:8004")

    with pytest.raises(RuntimeError, match="piece 1 of 1"):
        tts.synthesize(text="Hello there.", voice=tts.voices()[0])


def test_a_bad_request_is_not_retried(voices_file, monkeypatch):
    """A 4xx is our bug — a wrong voice name, a malformed payload. Retrying
    burns minutes of GPU time repeating the same mistake."""
    calls = {"n": 0}

    def bad_request(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(422, request=httpx.Request("POST", url))

    monkeypatch.setattr(tts_local.httpx, "post", bad_request)
    tts = LocalTTS(base_url="http://localhost:8004")

    with pytest.raises(httpx.HTTPStatusError):
        tts.synthesize(text="Hello there.", voice=tts.voices()[0])
    assert calls["n"] == 1


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


def test_generation_knobs_are_sent_on_every_request(voices_file, captured):
    """The house style, pinned.

    These four decide what the narration sounds like, and the server keeps its
    own editable defaults for each. Sending them explicitly is the only thing
    stopping a change in the server's UI from quietly restyling our runs, so
    the values are asserted rather than trusted.
    """
    LocalTTS().synthesize(text="Hello.", voice=LocalTTS().voices()[0])

    payload = captured["json"]
    assert payload["temperature"] == 0.6
    assert payload["exaggeration"] == 0.85
    assert payload["cfg_weight"] == 0.5
    assert payload["speed_factor"] == 1.0
