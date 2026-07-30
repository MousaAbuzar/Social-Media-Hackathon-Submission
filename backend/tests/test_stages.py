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


def test_titles_does_not_choose_for_the_user(ctx):
    # Picking is a gated decision; the stage must not pre-empt it.
    assert "chosen" not in stages.stage_titles(ctx).output


def test_title_cleaning_strips_list_scaffolding():
    assert stages._clean_title("1. How Black Holes Bend Time") == "How Black Holes Bend Time"
    assert stages._clean_title("- How Black Holes Bend Time") == "How Black Holes Bend Time"
    assert stages._clean_title('"How Black Holes Bend Time"') == "How Black Holes Bend Time"
    assert stages._clean_title("3) How Black Holes Bend Time") == "How Black Holes Bend Time"


def test_titles_carry_a_rationale_for_each_candidate(ctx):
    output = stages.stage_titles(ctx).output
    assert len(output["candidates"]) == len(output["titles"])
    assert all(c["why"] for c in output["candidates"])


def test_titles_recommend_one_of_the_candidates(ctx):
    output = stages.stage_titles(ctx).output
    assert output["recommended"] in output["titles"]


def test_titles_fall_back_to_plain_lines_when_the_reply_is_not_json():
    # A model that ignores the JSON contract costs the rationales, not the run.
    candidates, recommended = stages._parse_candidates(
        "How Black Holes Bend Time\nWhat Falls Into a Black Hole", count=5
    )
    assert [c["title"] for c in candidates] == [
        "How Black Holes Bend Time",
        "What Falls Into a Black Hole",
    ]
    assert all(c["why"] == "" for c in candidates)
    assert recommended == "How Black Holes Bend Time"


def test_titles_ignore_a_recommendation_that_names_no_candidate():
    _, recommended = stages._parse_candidates(
        '{"candidates": [{"title": "How Black Holes Bend Time", "why": "x"}],'
        ' "recommended": "A Title That Was Dropped"}',
        count=5,
    )
    assert recommended == "How Black Holes Bend Time"


def test_titles_survive_a_markdown_fence():
    candidates, _ = stages._parse_candidates(
        '```json\n{"candidates": [{"title": "How Black Holes Bend Time", "why": "x"}]}\n```',
        count=5,
    )
    assert candidates == [{"title": "How Black Holes Bend Time", "why": "x"}]


def test_titles_hash_is_stable_for_same_topic(ctx):
    assert stages.titles_hash(ctx) == stages.titles_hash(ctx)


def test_titles_hash_changes_with_topic(ctx):
    first = stages.titles_hash(ctx)
    ctx.topic = "Something else entirely"
    assert stages.titles_hash(ctx) != first


def test_script_uses_the_chosen_title(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    assert stages.stage_script(ctx).output["title"] == ctx.chosen_title


def test_script_length_follows_the_requested_minutes(ctx, stub_storage):
    from app.config import get_settings

    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.target_minutes = 25
    output = stages.stage_script(ctx).output

    assert output["target_minutes"] == 25
    assert output["target_words"] == 25 * get_settings().words_per_minute
    # The fake reads its own target back out of the prompt, so a 25-minute ask
    # really does produce roughly 25 minutes of narration.
    assert output["estimated_minutes"] == pytest.approx(25, abs=2)


def test_a_longer_script_really_is_longer(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output

    ctx.target_minutes = 5
    short = stages.stage_script(ctx).output["word_count"]
    ctx.target_minutes = 30
    long = stages.stage_script(ctx).output["word_count"]

    assert long > short * 4


def test_script_hash_changes_with_requested_length(ctx):
    # Otherwise a retry at a new length would serve the cached old script.
    ctx.target_minutes = 5
    first = stages.script_hash(ctx)
    ctx.target_minutes = 25
    assert stages.script_hash(ctx) != first


def test_script_falls_back_to_the_default_length(ctx, stub_storage):
    from app.config import get_settings

    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.target_minutes = None
    assert (
        stages.stage_script(ctx).output["target_minutes"]
        == get_settings().default_script_minutes
    )


def test_script_refuses_to_run_without_a_title(ctx):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.chosen_title = None
    with pytest.raises(ValueError, match="without a chosen title"):
        stages.stage_script(ctx)


def test_tts_refuses_to_run_without_a_voice(ctx, stub_storage):
    ctx.prior[StageName.titles.value] = stages.stage_titles(ctx).output
    ctx.prior[StageName.script.value] = stages.stage_script(ctx).output
    ctx.voice_id = None
    with pytest.raises(ValueError, match="without a chosen voice"):
        stages.stage_tts(ctx)


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
