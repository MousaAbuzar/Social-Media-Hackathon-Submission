"""Self-hosted TTS adapter (Chatterbox).

Talks to a Chatterbox TTS server running on your own machine. No vendor
account, no API key, no per-character cost — `cost_micros` is genuinely zero,
because the only thing a run spends is your own electricity.

We use the server's flexible `/tts` endpoint rather than its OpenAI-compatible
`/v1/audio/speech` route: cloning needs `voice_mode=clone`, and full-length
scripts need the server's own chunking, neither of which the OpenAI shape
exposes.

Voices are declared in `voices.local.json` next to this file; see
`voices.local.example.json`. A voice's `external_id` is a filename the server
already has — a sample in its `reference_audio/` folder for `clone` mode, or a
file in its `voices/` folder for `predefined` mode. The sample *is* the voice.

See `docs/local-tts.md` for getting the server running.
"""

import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.providers.base import SynthesisResult, Voice

VOICES_FILE = Path(__file__).parent / "voices.local.json"

# Synthesis is roughly real time on a laptop GPU and far slower on CPU, so a
# 1200-word script can legitimately run for many minutes. The ceiling is high
# on purpose — a timeout here fails the stage and wastes the work already done.
SYNTHESIS_TIMEOUT_SECONDS = 1800.0

VALID_MODES = ("clone", "predefined")


def _load_raw() -> list[dict]:
    if not VOICES_FILE.exists():
        return []
    return json.loads(VOICES_FILE.read_text(encoding="utf-8"))


def _voice_modes() -> dict[str, str]:
    """Map our voice id -> server voice mode.

    `Voice` is a vendor-neutral record with no room for a server-specific
    knob, so the mode is looked up alongside it rather than smuggled into a
    field that other providers would have to ignore.
    """
    modes = {}
    for item in _load_raw():
        mode = item.get("mode", "clone")
        if mode not in VALID_MODES:
            raise ValueError(
                f"Voice {item['id']!r} has mode {mode!r}; expected one of {VALID_MODES}"
            )
        modes[item["id"]] = mode
    return modes


class LocalTTS:
    name = "local"

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.tts_local_url).rstrip("/")
        self.settings = settings

    def voices(self) -> list[Voice]:
        # Read on every call so dropping a new sample into reference_audio/ and
        # adding a line to voices.local.json takes effect without a restart.
        return [
            Voice(
                id=item["id"],
                label=item["label"],
                provider=self.name,
                description=item.get("description", ""),
                external_id=item["external_id"],
            )
            for item in _load_raw()
        ]

    def synthesize(self, *, text: str, voice: Voice) -> SynthesisResult:
        mode = _voice_modes().get(voice.id, "clone")

        payload: dict[str, object] = {
            "text": text,
            "voice_mode": mode,
            "output_format": "wav",
            # Let the server chunk. It splits on sentence boundaries and
            # stitches the audio back together, which is what makes
            # audiobook-length input work at all.
            "split_text": True,
            "chunk_size": self.settings.tts_local_chunk_size,
            "temperature": self.settings.tts_local_temperature,
            "exaggeration": self.settings.tts_local_exaggeration,
            "cfg_weight": self.settings.tts_local_cfg_weight,
        }
        if mode == "clone":
            payload["reference_audio_filename"] = voice.external_id
        else:
            payload["predefined_voice_id"] = voice.external_id

        try:
            response = httpx.post(
                f"{self.base_url}/tts",
                json=payload,
                timeout=httpx.Timeout(SYNTHESIS_TIMEOUT_SECONDS, connect=10.0),
            )
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"No TTS server at {self.base_url}. Start Chatterbox (see "
                f"docs/local-tts.md) or set TTS_PROVIDER=fake to run offline."
            ) from exc

        response.raise_for_status()
        return SynthesisResult(
            audio=response.content,
            content_type=response.headers.get("content-type", "audio/wav"),
            characters=len(text),
        )

    def cost_micros(self, characters: int) -> int:
        # Self-hosted. There is no per-character price to report.
        return 0
