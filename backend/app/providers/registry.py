"""Provider selection.

The only module that knows which concrete provider is in play. Everything
else depends on the Protocols in `base.py`.
"""

from functools import lru_cache

from app.config import get_settings
from app.providers.base import LLMProvider, TTSProvider, Voice
from app.providers.llm_anthropic import AnthropicLLM
from app.providers.llm_fake import FakeLLM
from app.providers.tts_fake import FakeTTS
from app.providers.tts_http import HttpTTS
from app.providers.tts_local import LocalTTS


@lru_cache
def get_llm(model: str | None = None) -> LLMProvider:
    """The LLM for a stage. `model` overrides the default for stages that want
    a stronger (and pricier) model than the rest of the pipeline."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return FakeLLM()
    return AnthropicLLM(model=model)


@lru_cache
def get_tts() -> TTSProvider:
    settings = get_settings()
    match settings.tts_provider:
        case "local":
            return LocalTTS()
        case "http":
            return HttpTTS()
        case "fake" | "":
            return FakeTTS()
        case unknown:
            raise ValueError(f"Unknown TTS_PROVIDER: {unknown!r}")


def resolve_voice(voice_id: str) -> Voice:
    tts = get_tts()
    for voice in tts.voices():
        if voice.id == voice_id:
            return voice
    available = ", ".join(v.id for v in tts.voices()) or "(none configured)"
    raise ValueError(f"Unknown voice {voice_id!r}. Available: {available}")
