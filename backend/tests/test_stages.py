"""Stage-level tests.

These run entirely offline against the fake providers, so the full pipeline
is exercised on every commit without an API key or a cent of spend.
"""

import json

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


def test_titles_explain_why_the_recommendation_beats_the_others(ctx):
    # This is what the hover on the best pick shows.
    assert stages.stage_titles(ctx).output["recommended_why"]


def test_titles_fall_back_to_plain_lines_when_the_reply_is_not_json():
    # A model that ignores the JSON contract costs the rationales, not the run.
    candidates, recommended, recommended_why = stages._parse_candidates(
        "How Black Holes Bend Time\nWhat Falls Into a Black Hole", count=5
    )
    assert [c["title"] for c in candidates] == [
        "How Black Holes Bend Time",
        "What Falls Into a Black Hole",
    ]
    assert all(c["why"] == "" for c in candidates)
    assert recommended == "How Black Holes Bend Time"
    assert recommended_why == ""


def test_titles_ignore_a_recommendation_that_names_no_candidate():
    _, recommended, recommended_why = stages._parse_candidates(
        '{"candidates": [{"title": "How Black Holes Bend Time", "why": "x"}],'
        ' "recommended": "A Title That Was Dropped",'
        ' "recommended_why": "It beats the rest on search volume."}',
        count=5,
    )
    assert recommended == "How Black Holes Bend Time"
    # The comparison argued for the dropped title, so it must not be shown
    # against the one that replaced it.
    assert recommended_why == ""


def test_titles_keep_the_comparison_when_the_recommendation_stands():
    _, recommended, recommended_why = stages._parse_candidates(
        '{"candidates": [{"title": "How Black Holes Bend Time", "why": "x"}],'
        ' "recommended": "How Black Holes Bend Time",'
        ' "recommended_why": "It beats the rest on search volume."}',
        count=5,
    )
    assert recommended == "How Black Holes Bend Time"
    assert recommended_why == "It beats the rest on search volume."


def test_truncated_json_does_not_become_one_giant_title():
    """The regression: a reply cut off at max_tokens fell through to the
    line-based fallback, and the user saw 2000 characters of partial JSON
    presented as a single title."""
    truncated = (
        '{"candidates": [{"title": "How Rome Went From a Village to an Empire", '
        '"why": "It names the span and withholds the mechanism, which is the'
    )
    candidates, recommended, _ = stages._parse_candidates(truncated, count=5)
    assert candidates == []
    assert recommended is None


def test_an_overlong_title_is_dropped_rather_than_shown():
    candidates, _, _ = stages._parse_candidates(
        json.dumps(
            {
                "candidates": [
                    {"title": "x" * 400, "why": "runaway"},
                    {"title": "How Black Holes Bend Time", "why": "fine"},
                ]
            }
        ),
        count=5,
    )
    assert [c["title"] for c in candidates] == ["How Black Holes Bend Time"]


def test_titles_survive_a_markdown_fence():
    candidates, _, _ = stages._parse_candidates(
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


# --- Review is advisory, and must behave like it ------------------------


def _script_ctx(ctx, words: int = 4500):
    """A ctx whose prior script is `words` long, without paying to write one."""
    ctx.prior[StageName.script.value] = {"script": "word " * words, "word_count": words}
    return ctx


def test_review_token_ceiling_scales_with_the_script(ctx):
    """The 30-minute-script bug.

    A flat 2,000 covered the findings but not the thinking, so a long script
    was cut off mid-response and failed a stage that had already been paid for.
    """
    short = stages.review_max_tokens("word " * 1200)  # ~8 minutes
    long = stages.review_max_tokens("word " * 4500)  # ~30 minutes
    longest = stages.review_max_tokens("word " * 20000)

    assert short >= 8_000, "even a short script needs room to think"
    assert long > 2_000, "the ceiling that broke a 30-minute script"
    assert longest > long, "a longer script gets more room"
    assert longest <= 32_000, "but not unbounded"


def test_review_failure_does_not_fail_the_run(ctx, monkeypatch):
    """An advisory gate that can kill the run is not advisory.

    The script is written and paid for by this point. Losing it to a failed
    opinion about it is the worse outcome, so the stage steps aside instead.
    """
    _script_ctx(ctx)

    def boom(**kwargs):
        raise RuntimeError("Model hit the 2000-token ceiling and was cut off")

    monkeypatch.setattr(stages.get_llm(), "complete", boom)

    result = stages.stage_review(ctx)

    assert result.output["ran"] is False
    assert result.output["passed"] is False
    # Silence must not read as approval — the finding has to say it did not run.
    assert "could not run" in result.output["findings"]
    assert "not checked" in result.output["findings"]
    assert result.cost_micros == 0


def test_review_still_stops_the_run_when_the_budget_is_gone(ctx, monkeypatch):
    """The one failure worth stopping for.

    Carrying on would spend more on synthesis, and the spend that triggered
    this was never recorded — so the budget guard is already flying blind.
    """
    from app.providers.base import BudgetExceeded

    _script_ctx(ctx)

    def broke(**kwargs):
        raise BudgetExceeded("Run budget exhausted inside a single call")

    monkeypatch.setattr(stages.get_llm(), "complete", broke)

    with pytest.raises(BudgetExceeded):
        stages.stage_review(ctx)


def test_review_marks_a_successful_check_as_having_run(ctx):
    _script_ctx(ctx)
    output = stages.stage_review(ctx).output
    assert output["ran"] is True


def test_unknown_voice_is_rejected(ctx):
    from app.providers.registry import resolve_voice

    with pytest.raises(ValueError, match="Unknown voice"):
        resolve_voice("no-such-voice")
