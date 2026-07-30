"""Prompt contract tests.

Prompts are the product here, and they are edited by hand more often than the
code around them. These lock down the parts that are load-bearing: the
placeholders the stages fill in, and the guardrails that keep the output
publishable.
"""

import pytest

from app.pipeline import prompts


def test_script_system_renders_with_the_values_the_stage_supplies():
    # A stray unescaped brace in the prompt would raise here, not in prod.
    rendered = prompts.SCRIPT_SYSTEM.format(words=3750, minutes=25, wpm=150)
    assert "approximately 3750 words" in rendered
    assert "25 minutes" in rendered


def test_titles_system_renders_with_its_count():
    rendered = prompts.TITLES_SYSTEM.format(count=5)
    assert "exactly 5 candidates" in rendered
    # The JSON skeleton must survive .format() with its braces intact.
    assert '{"candidates":' in rendered.replace(" ", "")


def test_fake_llm_can_read_the_word_target_back_out():
    """The offline fake sizes its script by parsing the prompt.

    Reword the length line and the fake silently falls back to a default,
    which is exactly the kind of break a passing test suite would hide.
    """
    from app.providers.llm_fake import _TARGET_WORDS

    rendered = prompts.SCRIPT_SYSTEM.format(words=3750, minutes=25, wpm=150)
    match = _TARGET_WORDS.search(rendered)
    assert match and match.group(1) == "3750"


@pytest.mark.parametrize(
    "phrase",
    [
        "Copy the manner, not the man",
        "Do not claim to be Neil deGrasse Tyson",
        "Never invent a quotation",
    ],
)
def test_script_system_keeps_its_impersonation_guardrails(phrase):
    # Style is the point; passing the video off as a real person is not.
    assert phrase in prompts.SCRIPT_SYSTEM


def test_review_does_not_flag_the_house_style_it_was_asked_for():
    # The old wording failed every script for "writing in the voice of a real
    # person" — the exact thing the script prompt now requires.
    assert "do not flag it" in prompts.REVIEW_SYSTEM.lower()
    assert "quotation attributed to a real person" in prompts.REVIEW_SYSTEM


def test_review_still_catches_fabricated_attribution():
    assert "claiming to BE a real named person" in prompts.REVIEW_SYSTEM
