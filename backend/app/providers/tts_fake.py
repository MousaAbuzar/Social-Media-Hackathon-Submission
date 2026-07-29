"""Offline TTS stand-in.

Emits a real, playable WAV of silence whose duration tracks the script length
(~150 spoken words per minute), so downstream stages get plausible byte counts
and durations without any vendor call.
"""

import io
import wave

from app.providers.base import SynthesisResult, Voice

SAMPLE_RATE = 22_050
WORDS_PER_MINUTE = 150

_VOICES = [
    Voice(
        id="narrator_default",
        label="Narrator (offline placeholder)",
        provider="fake",
        description="Silent placeholder audio. No vendor call, no cost.",
        external_id="fake-narrator",
    ),
]


class FakeTTS:
    name = "fake"

    def voices(self) -> list[Voice]:
        return list(_VOICES)

    def synthesize(self, *, text: str, voice: Voice) -> SynthesisResult:
        words = max(len(text.split()), 1)
        seconds = max(words / WORDS_PER_MINUTE * 60, 1.0)
        frames = int(seconds * SAMPLE_RATE)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(b"\x00\x00" * frames)

        return SynthesisResult(
            audio=buffer.getvalue(),
            content_type="audio/wav",
            characters=len(text),
        )

    def cost_micros(self, characters: int) -> int:
        return 0
