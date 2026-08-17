import anthropic

from app.config import get_settings
from app.providers.base import BudgetExceeded, LLMResult, LLMUsage

# USD per million tokens, in micros. Update alongside the model.
PRICING_MICROS_PER_MTOK = {
    "claude-fable-5": (10_000_000, 50_000_000),
    "claude-opus-5": (5_000_000, 25_000_000),
    "claude-sonnet-5": (3_000_000, 15_000_000),
    "claude-haiku-4-5": (1_000_000, 5_000_000),
}

# Web search is billed per search, not per token: $10 per 1,000.
WEB_SEARCH_MICROS_PER_REQUEST = 10_000

# A server-tool turn stops with `pause_turn` when the search loop hits its
# internal iteration limit; resending continues it. The cap stops a model that
# keeps pausing from looping forever on our dime — reaching it is a real
# failure, not a state to paper over.
MAX_RESUMPTIONS = 6

# Blocks the search tool emits. Text before the last of these is the model
# talking about its research; the answer is what comes after.
SEARCH_BLOCK_TYPES = frozenset(
    {"server_tool_use", "web_search_tool_result", "web_fetch_tool_result"}
)


def narration_from(blocks) -> str:
    """The answer text, with pre-search chatter dropped.

    A searching model narrates its process — "I'll research this before
    writing" — in a text block ahead of the first query. Joining every text
    block put that line at the top of the script, where TTS then read it
    aloud. Only text after the final search is the answer.

    The assumption is that searching finishes before writing starts, which is
    what the prompt asks for. If a model interleaved long narration between
    searches, the earlier part would be dropped here rather than returned as a
    silently truncated script — the script stage's word-count floor is what
    catches that.
    """
    last_search = -1
    for i, block in enumerate(blocks):
        if getattr(block, "type", None) in SEARCH_BLOCK_TYPES:
            last_search = i

    after = [b for b in blocks[last_search + 1 :] if getattr(b, "type", None) == "text"]
    # No text after the last search means nothing was searched (the ordinary
    # no-tools path) or the turn ended on a tool result. Either way, every
    # text block is answer text.
    if not after:
        after = [b for b in blocks if getattr(b, "type", None) == "text"]
    return "".join(b.text for b in after)


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.llm_model
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key or None)

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        web_search: bool = False,
        budget_micros: int | None = None,
    ) -> LLMResult:
        settings = get_settings()

        # Server-side search: Anthropic runs the queries and feeds the results
        # back into the same call, so there is no tool loop for us to drive.
        # The `_20260209` variant filters results in a sandbox before they
        # reach the context window, which is what keeps a research-heavy script
        # from spending its whole token budget on search noise.
        tools = (
            [
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": settings.web_search_max_uses,
                }
            ]
            if web_search
            else []
        )

        messages: list[dict] = [{"role": "user", "content": prompt}]
        # Blocks rather than text: which text counts as the answer depends on
        # where the search blocks fall, and that ordering is lost once the text
        # is flattened.
        blocks: list = []
        input_tokens = output_tokens = searches = 0

        for _ in range(MAX_RESUMPTIONS):
            # Streaming so long scripts don't trip the SDK's HTTP timeout guard;
            # get_final_message() gives us the assembled response.
            #
            # `fallbacks="default"` re-runs a safety-declined request on
            # Anthropic's recommended substitute inside the same call, routed by
            # refusal category. Science topics brush against the bio and cyber
            # classifiers often enough that a hard failure mid-run isn't worth
            # the risk.
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=messages,
                **({"tools": tools} if tools else {}),
            ) as stream:
                message = stream.get_final_message()

            # Accumulate before any raise, so a failure still reports what the
            # attempt cost. Usage is per-response, not cumulative.
            input_tokens += message.usage.input_tokens
            output_tokens += message.usage.output_tokens
            server_use = getattr(message.usage, "server_tool_use", None)
            searches += getattr(server_use, "web_search_requests", 0) or 0

            spent = self.cost_micros(
                LLMUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    web_search_requests=searches,
                )
            )
            # Checked before deciding whether to resume. Each resumption resends
            # the whole conversation plus everything search has pulled in, so a
            # call that pauses repeatedly costs a multiple of one request — the
            # one way this stage could outrun a ceiling set from the outside.
            if budget_micros is not None and spent > budget_micros:
                raise BudgetExceeded(
                    f"Run budget exhausted inside a single call: spent "
                    f"${spent / 1_000_000:.2f} of the ${budget_micros / 1_000_000:.2f} "
                    f"remaining. Raise RUN_BUDGET_USD, shorten the script, or "
                    f"lower WEB_SEARCH_MAX_USES."
                )

            # A refusal here means the fallback declined too. Check before
            # reading content — on a refusal there may be nothing in it.
            if message.stop_reason == "refusal":
                detail = getattr(message.stop_details, "category", None) or "unspecified"
                raise RuntimeError(f"Model declined the request ({detail})")

            # A response cut off at the ceiling is never usable: JSON stops
            # mid-string, a script stops mid-sentence. Callers used to receive
            # the fragment and quietly treat it as a complete answer, so fail
            # here instead. Note the ceiling covers reasoning tokens as well as
            # visible output, which is what makes a seemingly generous limit
            # run out.
            if message.stop_reason == "max_tokens":
                raise RuntimeError(
                    f"Model hit the {max_tokens}-token ceiling and was cut off "
                    "mid-response. Raise max_tokens for this stage."
                )

            blocks.extend(message.content)

            # Paused mid-turn with searches still to run. Echo the turn back
            # verbatim and the server picks up where it stopped; do not add a
            # "continue" message, which the API would read as a new instruction.
            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue

            return LLMResult(
                text=narration_from(blocks).strip(),
                usage=LLMUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    web_search_requests=searches,
                ),
            )

        raise RuntimeError(
            f"Model was still paused mid-turn after {MAX_RESUMPTIONS} resumptions. "
            "Lower WEB_SEARCH_MAX_USES or set SCRIPT_WEB_SEARCH=false."
        )

    def cost_micros(self, usage: LLMUsage) -> int:
        in_rate, out_rate = PRICING_MICROS_PER_MTOK.get(self.model, (5_000_000, 25_000_000))
        return round(
            usage.input_tokens * in_rate / 1_000_000
            + usage.output_tokens * out_rate / 1_000_000
            + usage.web_search_requests * WEB_SEARCH_MICROS_PER_REQUEST
        )
