"""Deterministic LLM stand-in.

Lets the whole pipeline — and the whole test suite — run with no API key,
no network, and no cost. Selected automatically when no key is configured.
"""

import hashlib

from app.providers.base import LLMResult, LLMUsage


class FakeLLM:
    name = "fake"

    def __init__(self, model: str = "fake-1") -> None:
        self.model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> LLMResult:
        seed = hashlib.sha256((system + prompt).encode()).hexdigest()[:8]

        if "TITLES" in system:
            text = "\n".join(f"Placeholder Title {i} [{seed}]" for i in range(1, 9))
        elif "REVIEW" in system:
            text = "OK"
        else:
            paragraph = (
                f"This is placeholder narration generated without calling a model ({seed}). "
                "It exists so the pipeline can be exercised end to end offline. "
            )
            text = "\n\n".join(paragraph * 3 for _ in range(6))

        return LLMResult(
            text=text,
            usage=LLMUsage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4),
        )

    def cost_micros(self, usage: LLMUsage) -> int:
        return 0
