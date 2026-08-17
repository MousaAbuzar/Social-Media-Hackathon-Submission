"""Deterministic LLM stand-in.

Lets the whole pipeline — and the whole test suite — run with no API key,
no network, and no cost. Selected automatically when no key is configured.
"""

import hashlib
import json
import re

from app.providers.base import LLMResult, LLMUsage

# The script prompt states its own target. Reading it back is what lets the
# fake honour a requested length, so the offline demo shows a 25-minute script
# actually coming out 25 minutes long.
_TARGET_WORDS = re.compile(r"approximately (\d+) words")


def _fake_script(seed: str, target_words: int) -> str:
    paragraph = (
        f"This is placeholder narration generated without calling a model ({seed}). "
        "It exists so the pipeline can be exercised end to end offline. "
    )
    per_paragraph = len(paragraph.split()) * 3
    paragraphs = max(1, round(target_words / per_paragraph))
    return "\n\n".join(paragraph * 3 for _ in range(paragraphs))


class FakeLLM:
    name = "fake"

    def __init__(self, model: str = "fake-1") -> None:
        self.model = model

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        web_search: bool = False,
        budget_micros: int | None = None,
    ) -> LLMResult:
        # `web_search` and `budget_micros` are accepted and ignored: offline is
        # the whole point of this provider. There is nothing to ground against
        # and nothing to spend, so no budget can be crossed.
        seed = hashlib.sha256((system + prompt).encode()).hexdigest()[:8]

        if "TITLES" in system:
            text = json.dumps(
                {
                    "candidates": [
                        {
                            "title": f"Placeholder Title {i} [{seed}]",
                            "why": (
                                f"Placeholder rationale {i}. Generated offline without "
                                "calling a model, so it shows the shape of a real "
                                "critique rather than the substance of one."
                            ),
                        }
                        for i in range(1, 9)
                    ],
                    "recommended": f"Placeholder Title 2 [{seed}]",
                    "recommended_why": (
                        "Placeholder comparison. Offline there is nothing to weigh "
                        "these against each other on, so this stands in for the case "
                        "a real model makes for its pick over the rest."
                    ),
                }
            )
        elif "REVIEW" in system:
            text = "OK"
        else:
            match = _TARGET_WORDS.search(system)
            text = _fake_script(seed, int(match.group(1)) if match else 1200)

        return LLMResult(
            text=text,
            usage=LLMUsage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4),
        )

    def cost_micros(self, usage: LLMUsage) -> int:
        return 0
