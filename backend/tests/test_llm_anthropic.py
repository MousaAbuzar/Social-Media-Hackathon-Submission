"""Response-parsing tests for the Anthropic adapter.

These cover the seam between a searching model's response and the narration we
hand to TTS. Everything here is pure block-shuffling — no client is built, so
nothing can reach the API.
"""

from dataclasses import dataclass

import pytest

from app.providers.llm_anthropic import narration_from


@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class ToolUse:
    type: str = "server_tool_use"


@dataclass
class ToolResult:
    type: str = "web_search_tool_result"


def search(*, query: str = "neutron star spin rate") -> list:
    """One search round trip, as it appears in the response content."""
    return [ToolUse(), ToolResult()]


def test_preamble_before_a_search_is_not_narration():
    """The bug this exists for.

    A searching model says what it is about to do before it does it. That line
    was landing at the top of the script, and TTS read it aloud.
    """
    blocks = [
        Text("I'll research this before writing."),
        *search(),
        Text("Something the size of a city, spinning 700 times a second."),
    ]
    assert narration_from(blocks) == (
        "Something the size of a city, spinning 700 times a second."
    )


def test_chatter_between_searches_is_dropped_too():
    blocks = [
        Text("Let me look this up."),
        *search(),
        Text("Now let me check the current figure."),
        *search(),
        Text("Something the size of a city."),
    ]
    assert narration_from(blocks) == "Something the size of a city."


def test_narration_spanning_a_resumption_is_kept_whole():
    """A paused turn splits the script across two responses.

    Both halves come after the last search, so both are narration — dropping
    the first would hand back a script missing its opening.
    """
    blocks = [
        Text("Chatter."),
        *search(),
        Text("First half of the script. "),
        Text("Second half, after the turn resumed."),
    ]
    assert narration_from(blocks) == (
        "First half of the script. Second half, after the turn resumed."
    )


def test_response_with_no_search_keeps_every_text_block():
    # The titles and review stages never search; their whole response is the
    # answer, and a last-search index of -1 must not eat the first block.
    blocks = [Text('{"candidates": '), Text('[{"title": "x"}]}')]
    assert narration_from(blocks) == '{"candidates": [{"title": "x"}]}'


def test_turn_ending_on_a_tool_result_falls_back_rather_than_returning_nothing():
    """No text after the final search means the split found nothing.

    Returning "" there would surface as an empty script; the earlier text is a
    better answer than none, and the stage's word-count floor still guards it.
    """
    blocks = [Text("Partial narration."), *search()]
    assert narration_from(blocks) == "Partial narration."


def test_web_fetch_counts_as_a_search():
    # web_fetch rides alongside web_search on the same tool version. Text
    # before a fetch is the same process chatter.
    blocks = [
        Text("Let me read that paper."),
        ToolUse(),
        ToolResult(type="web_fetch_tool_result"),
        Text("The real narration."),
    ]
    assert narration_from(blocks) == "The real narration."


def test_thinking_blocks_are_never_narration():
    # Thinking arrives as its own block type and must not reach the script,
    # whether or not the turn searched.
    thinking = type("Thinking", (), {"type": "thinking", "thinking": "hmm"})()
    assert narration_from([thinking, Text("Script.")]) == "Script."
    assert narration_from([thinking, *search(), Text("Script.")]) == "Script."


@pytest.mark.parametrize("blocks", [[], [ToolUse(), ToolResult()]])
def test_a_response_with_no_text_at_all_yields_empty(blocks):
    # Not an error here — the script stage raises on an implausibly short
    # script, which is where that judgement belongs.
    assert narration_from(blocks) == ""
