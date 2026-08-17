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

import io
import json
import logging
import re
import time
import wave
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

# Per-request attempts. Synthesis is minutes of GPU work, so a lost request is
# expensive to redo — but far cheaper than losing the whole script.
REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 6.0)

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_for_requests(text: str, limit: int) -> list[str]:
    """Break a script into pieces small enough to synthesize in one request.

    The server chunks internally too, but that happens *inside* one HTTP call:
    a 27,000-character script is a single request holding open for well over an
    hour. Anything that interrupts it — a server restart, a crash, a dropped
    connection — loses the entire synthesis, and the retry starts from nothing.

    Splitting on sentence boundaries keeps each request to minutes and makes a
    failure cost one piece instead of all of them. Prosody is unaffected: the
    server was already splitting on the same boundaries.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    sentences: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            sentences.extend(s for s in _SENTENCE_END.split(paragraph) if s)

    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def concat_wav(parts: list[bytes]) -> bytes:
    """Join WAVs into one. Every part comes from the same server and voice, so
    the formats match; a mismatch would mean the server changed underneath us
    mid-script, and `wave` raising is the right response to that."""
    if len(parts) == 1:
        return parts[0]

    buffer = io.BytesIO()
    writer: wave.Wave_write | None = None
    try:
        for part in parts:
            with wave.open(io.BytesIO(part), "rb") as reader:
                if writer is None:
                    writer = wave.open(buffer, "wb")
                    writer.setparams(reader.getparams())
                writer.writeframes(reader.readframes(reader.getnframes()))
    finally:
        if writer is not None:
            writer.close()
    return buffer.getvalue()


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
            # Sent explicitly rather than left to the server's own default,
            # which is editable in its UI and would otherwise silently change
            # what our runs sound like.
            "speed_factor": self.settings.tts_local_speed_factor,
        }
        if mode == "clone":
            payload["reference_audio_filename"] = voice.external_id
        else:
            payload["predefined_voice_id"] = voice.external_id

        pieces = split_for_requests(text, self.settings.tts_local_request_chars)
        if not pieces:
            raise RuntimeError("Nothing to synthesize: the script is empty")

        audio_parts: list[bytes] = []
        content_type = "audio/wav"
        for index, piece in enumerate(pieces, start=1):
            log.info(
                "tts request %d/%d (%d chars) -> %s", index, len(pieces), len(piece),
                self.base_url,
            )
            response = self._post_with_retry({**payload, "text": piece}, index, len(pieces))
            audio_parts.append(response.content)
            content_type = response.headers.get("content-type", content_type)

        return SynthesisResult(
            audio=concat_wav(audio_parts),
            content_type=content_type,
            characters=len(text),
        )

    def _post_with_retry(self, payload: dict, index: int, total: int) -> httpx.Response:
        """One piece, retried on the failures that are worth retrying.

        A dropped connection or a restarting server is transient — the piece
        costs minutes of GPU time, so it is worth another go before failing a
        stage that has an expensive finished script behind it.
        """
        last: Exception | None = None
        for attempt in range(REQUEST_ATTEMPTS):
            if attempt:
                delay = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                log.warning(
                    "tts piece %d/%d failed (%s); retrying in %.0fs",
                    index, total, last, delay,
                )
                time.sleep(delay)
            try:
                response = httpx.post(
                    f"{self.base_url}/tts",
                    json=payload,
                    timeout=httpx.Timeout(SYNTHESIS_TIMEOUT_SECONDS, connect=10.0),
                )
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                # A 4xx is our bug — a bad voice name, a malformed payload —
                # and retrying just repeats it.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                last = exc

        raise RuntimeError(
            f"TTS failed on piece {index} of {total} after {REQUEST_ATTEMPTS} attempts "
            f"({type(last).__name__}: {last}). The server at {self.base_url} is not "
            f"responding — check it is still running (docs/local-tts.md), or set "
            f"TTS_PROVIDER=fake to run offline."
        ) from last

    def cost_micros(self, characters: int) -> int:
        # Self-hosted. There is no per-character price to report.
        return 0
