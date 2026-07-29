"""Stage-level tests.

These run entirely offline against the fake providers, so the full pipeline
is exercised on every commit without an API key or a cent of spend.
"""

import pytest

from app.models import StageName
from app.pipeline import stages


def test_titles_returns_candidates(ctx):
    result = stages.stage_titles(ctx)
    assert len(result.output["titles"]) >= 1
    assert result.output["chosen"] == result.output["titles"][0]


def test_titles_hash_is_stable_for_same_topic(ctx):
    assert stages.titles_hash(ctx) == stages.titles_hash(ctx)


def test_titles_hash_changes_with_topic(ctx):
    first = stages.titles_hash(ctx)
    ctx.topic = "Something else entirely"
    assert stages.titles_hash(ctx) != first


def test_script_writes_artifact(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    result = stages.stage_script(ctx)

    assert result.output["word_count"] > 50
    assert len(result.artifacts) == 1
    assert result.artifacts[0].kind == "script"
    assert result.artifacts[0].s3_key in stub_storage


def test_tts_produces_playable_audio(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.prior[StageName.script.value] = stages.stage_script(ctx).output

    result = stages.stage_tts(ctx)
    audio = stub_storage[result.artifacts[0].s3_key]

    assert audio[:4] == b"RIFF"  # real WAV container, not an empty file
    assert result.tts_characters > 0


def test_package_carries_synthetic_disclosure(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.prior[StageName.script.value] = stages.stage_script(ctx).output
    ctx.prior[StageName.review.value] = stages.stage_review(ctx).output
    ctx.prior[StageName.tts.value] = stages.stage_tts(ctx).output

    result = stages.stage_package(ctx)
    assert "synthetic" in result.output["disclosure"].lower()
    assert result.output["title"]


def test_unknown_voice_is_rejected(ctx):
    from app.providers.registry import resolve_voice

    with pytest.raises(ValueError, match="Unknown voice"):
        resolve_voice("no-such-voice")
