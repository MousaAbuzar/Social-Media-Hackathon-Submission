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


def test_titles_system_asks_for_a_comparative_case_for_the_pick():
    # The hover on the best pick is only as good as this instruction: drop the
    # "compare" framing and the model just restates that title's own rationale.
    rendered = prompts.TITLES_SYSTEM.format(count=5)
    assert '"recommended_why"' in rendered
    assert "Compare — do not restate its own" in rendered


@pytest.mark.parametrize(
    "rule",
    [
        "Aim for 45-60 characters",
        "Front-load the subject",
        # The curiosity gap and its self-check are what separate a title that
        # gets clicked from one that is merely correct. Losing either in an
        # edit is the failure this test exists to catch.
        "THE CLICK",
        "THE GAP",
        "can the viewer walk away",
    ],
)
def test_titles_system_keeps_its_best_practice_rules(rule):
    assert rule in prompts.TITLES_SYSTEM


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


@pytest.mark.parametrize(
    "phrase",
    [
        # Search happens before writing, not as a fact-check afterwards. Lose
        # this and the model writes from memory and then verifies itself.
        "Use it before you write",
        # The whole point of searching is to find how he actually explains this
        # specific topic, rather than applying a generic impression of his voice.
        "What Neil deGrasse Tyson has actually said about this topic in public",
        # Research that leaks into the narration gets read aloud.
        "The research must not show",
    ],
)
def test_script_system_keeps_its_research_instructions(phrase):
    assert phrase in prompts.SCRIPT_SYSTEM


def test_script_system_forbids_narrating_the_research():
    # Process chatter ("I'll research this before writing") is stripped from
    # the response too, but belt and braces: the adapter's strip assumes
    # searching finishes before writing starts, and this is the instruction
    # that makes that true.
    assert "Do all of your searching before you write a single word" in prompts.SCRIPT_SYSTEM
    assert "is not a line of the script" in prompts.SCRIPT_SYSTEM


def test_script_research_does_not_license_quoting_him():
    # Searching his material makes verbatim reproduction easy to fall into,
    # which would walk straight past the impersonation guardrails below.
    assert "do not quote him" in prompts.SCRIPT_SYSTEM


@pytest.mark.parametrize(
    "phrase",
    [
        # The whole point: the script kept coming out dense because nothing in
        # the prompt argued against density.
        "Density is not depth",
        "One new idea per sentence",
        # The consolidation beat. Without an explicit "not filler" framing this
        # reads as padding and collides with the retention rules.
        "It is how an idea survives being heard once",
        "One number at a time",
    ],
)
def test_script_system_keeps_its_clarity_rules(phrase):
    assert phrase in prompts.SCRIPT_SYSTEM


@pytest.mark.parametrize(
    "phrase",
    [
        # Short sentences and fragments are the single most recognisable thing
        # about the reference style; without this the model writes essay prose.
        "Sentences run short",
        "Fragments are fine when they carry a beat",
        "Start sentences with And, But, Now, So, Because",
        # Never leave a number unanchored, and give big ones a second framing.
        "Never leave a figure",
        # The direct-address beats that make it sound like a person.
        "Think about that for a moment",
        # Explicit turn markers, but only in front of an actual turn.
        "But here's where it gets interesting",
        "is worse than no marker",
        # The escalating ladder that carries the whole reference script.
        "Build as a ladder",
    ],
)
def test_script_system_keeps_the_house_delivery_style(phrase):
    assert phrase in prompts.SCRIPT_SYSTEM


def test_delivery_style_does_not_invite_copying_another_creator():
    """The style was distilled from a specific channel's script.

    Rules travel; phrasings must not. Publishing another creator's lines under
    your own narration is the failure mode this guards.
    """
    assert "never reproduce a phrasing" in prompts.SCRIPT_SYSTEM


def test_hand_offs_are_allowed_even_though_structure_talk_is_not():
    """These two rules pull against each other and both have to survive.

    The reference style says "Now let's zoom out" constantly, which an absolute
    "never announce structure" rule would forbid. The distinction is what
    follows the phrase, so the ban is scoped to talking about the video.
    """
    assert "conversational hand-off" in prompts.SCRIPT_SYSTEM
    assert '"let\'s dive in"' in prompts.SCRIPT_SYSTEM
    # The old absolute phrasing would silently veto the house style.
    assert "Never announce structure." not in prompts.SCRIPT_SYSTEM


def test_the_question_open_is_a_permitted_cold_open():
    # The reference opens on a personal question, not on an image or a number.
    # The earlier rule allowed only the latter.
    assert "or the question itself" in prompts.SCRIPT_SYSTEM
    assert "Open cold" in prompts.SCRIPT_SYSTEM


def test_script_length_rule_does_not_forbid_restatement():
    """The length requirement used to say "never repeat yourself".

    Combined with the retention rule below, that left cramming in more facts as
    the only way to fill the time — which is what made the script heavy. If
    either instruction comes back absolute, density comes back with it.
    """
    assert "Never repeat yourself" not in prompts.SCRIPT_SYSTEM
    assert "Cut every sentence that only restates the previous one" not in prompts.SCRIPT_SYSTEM
    assert "second pass they need" in prompts.SCRIPT_SYSTEM


def test_script_system_does_not_ship_everything_it_researched():
    # Research pulls in far more specifics than a script can carry by ear.
    assert "select ruthlessly" in prompts.SCRIPT_SYSTEM


def test_review_does_not_flag_the_house_style_it_was_asked_for():
    # The old wording failed every script for "writing in the voice of a real
    # person" — the exact thing the script prompt now requires.
    assert "do not flag it" in prompts.REVIEW_SYSTEM.lower()
    assert "quotation attributed to a real person" in prompts.REVIEW_SYSTEM


def test_review_still_catches_fabricated_attribution():
    assert "claiming to BE a real named person" in prompts.REVIEW_SYSTEM
