"""Generic HTTP TTS adapter.

Most hosted TTS vendors expose the same shape: POST some JSON containing the
text and a voice identifier to a per-voice or per-request URL, get audio bytes
back. This adapter covers that shape so adding a vendor is configuration
rather than code.

Voices come from `voices.json` next to this file; see `voices.example.json`.
Nothing here is vendor-specific — set TTS_BASE_URL and TTS_API_KEY, and list
the voices your account is entitled to use.
"""

import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.providers.base import SynthesisResult, Voice

VOICES_FILE = Path(__file__).parent / "voices.json"

# Typical hosted-TTS pricing, ~$15 per million characters.
COST_MICROS_PER_CHARACTER = 15


def _load_voices() -> list[Voice]:
    if not VOICES_FILE.exists():
        return []
    raw = json.loads(VOICES_FILE.read_text(encoding="utf-8"))
    return [
        Voice(
            id=item["id"],
            label=item["label"],
            provider="http",
            description=item.get("description", ""),
            external_id=item["external_id"],
        )
        for item in raw
    ]


class HttpTTS:
    name = "http"

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.tts_base_url).rstrip("/")
        self.api_key = api_key or settings.tts_api_key
        if not self.base_url:
            raise ValueError("TTS_BASE_URL is required when TTS_PROVIDER=http")

    def voices(self) -> list[Voice]:
        return _load_voices()

    def synthesize(self, *, text: str, voice: Voice) -> SynthesisResult:
        response = httpx.post(
            f"{self.base_url}/{voice.external_id}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "audio/mpeg",
            },
            json={"text": text},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        response.raise_for_status()
        return SynthesisResult(
            audio=response.content,
            content_type=response.headers.get("content-type", "audio/mpeg"),
            characters=len(text),
        )

    def cost_micros(self, characters: int) -> int:
        return characters * COST_MICROS_PER_CHARACTER
