import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("TTS_PROVIDER", "fake")


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
        voice_id="narrator_default",
        prior={},
    )
