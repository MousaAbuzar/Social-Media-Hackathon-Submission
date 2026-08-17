import os

import pytest

# Assigned, not setdefault: Compose loads .env into the container environment,
# so a real key and a real TTS provider are already present here. setdefault
# left them in place, and the suite quietly billed the live API and reached for
# a local TTS server — which is how a billing error surfaced as 22 test
# failures. Tests pin the fakes unconditionally.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TTS_PROVIDER"] = "fake"


@pytest.fixture(scope="session", autouse=True)
def _fakes_are_actually_selected():
    """Fail the whole session if the fakes did not take.

    The env assignment above is the fix; this is the alarm that tells us it
    stopped working. Without it, a change to settings loading or provider
    selection turns "tests bill the live API" back into a silent condition —
    which is exactly how it went unnoticed before.
    """
    from app.config import get_settings
    from app.providers.llm_fake import FakeLLM
    from app.providers.registry import get_llm, get_tts
    from app.providers.tts_fake import FakeTTS

    # Caches are keyed on settings read at import time; drop them so this
    # check and every test see providers built from the env set above.
    get_settings.cache_clear()
    get_llm.cache_clear()
    get_tts.cache_clear()

    assert isinstance(get_llm(), FakeLLM), (
        "Tests resolved a real LLM provider. They would bill the live API — "
        "refusing to run."
    )
    assert isinstance(get_tts(), FakeTTS), (
        "Tests resolved a real TTS provider. They would call out to a real "
        "synthesis server — refusing to run."
    )


@pytest.fixture(autouse=True)
def no_live_api_calls(monkeypatch):
    """Make reaching the network a loud failure rather than a quiet charge.

    Provider selection is the first line of defence; this is the second. A test
    that constructs an Anthropic client — directly, or through a code path that
    ignores the registry — fails here instead of spending money.
    """

    def refuse(*args, **kwargs):
        raise AssertionError(
            "A test tried to construct a real Anthropic client. Tests run "
            "against FakeLLM; if this stage needs a live model, it needs a "
            "stub instead."
        )

    monkeypatch.setattr("app.providers.llm_anthropic.anthropic.Anthropic", refuse)


@pytest.fixture(autouse=True)
def stub_storage(monkeypatch):
    """Keep object storage out of unit tests.

    Stages call put_object directly; swapping it here lets the pipeline run
    with no MinIO, no S3, and no network.
    """
    written: dict[str, bytes] = {}

    def fake_put(key: str, data: bytes, content_type: str) -> int:
        written[key] = data
        return len(data)

    monkeypatch.setattr("app.pipeline.stages.put_object", fake_put)
    return written


@pytest.fixture
def ctx():
    from app.pipeline.stages import StageContext

    return StageContext(
        run_id="00000000-0000-0000-0000-000000000001",
        topic="Why do black holes have an event horizon?",
        chosen_title="Why Black Holes Have a Point of No Return",
        voice_id="narrator_default",
        prior={},
    )
