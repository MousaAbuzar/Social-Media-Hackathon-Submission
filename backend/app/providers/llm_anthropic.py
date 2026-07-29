import anthropic

from app.config import get_settings
from app.providers.base import LLMResult, LLMUsage

# USD per million tokens, in micros. Update alongside the model.
PRICING_MICROS_PER_MTOK = {
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
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise RuntimeError("Model declined the request")

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
