import anthropic

from app.config import get_settings
from app.providers.base import LLMResult, LLMUsage

# USD per million tokens, in micros. Update alongside the model.
PRICING_MICROS_PER_MTOK = {
    "claude-fable-5": (10_000_000, 50_000_000),
    "claude-opus-5": (5_000_000, 25_000_000),
    "claude-sonnet-5": (3_000_000, 15_000_000),
    "claude-haiku-4-5": (1_000_000, 5_000_000),
}


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.llm_model
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key or None)

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> LLMResult:
        # Streaming so long scripts don't trip the SDK's HTTP timeout guard;
        # get_final_message() gives us the assembled response.
        #
        # `fallbacks="default"` re-runs a safety-declined request on Anthropic's
        # recommended substitute inside the same call, routed by refusal
        # category. Science topics brush against the bio and cyber classifiers
        # often enough that a hard failure mid-run isn't worth the risk.
        with self._client.beta.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()

        # A refusal here means the fallback declined too. Check before reading
        # content — on a refusal there may be nothing in it.
        if message.stop_reason == "refusal":
            detail = getattr(message.stop_details, "category", None) or "unspecified"
            raise RuntimeError(f"Model declined the request ({detail})")

        text = "".join(block.text for block in message.content if block.type == "text")
        return LLMResult(
            text=text.strip(),
            usage=LLMUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
        )

    def cost_micros(self, usage: LLMUsage) -> int:
        in_rate, out_rate = PRICING_MICROS_PER_MTOK.get(self.model, (5_000_000, 25_000_000))
        return round(
            usage.input_tokens * in_rate / 1_000_000 + usage.output_tokens * out_rate / 1_000_000
        )
