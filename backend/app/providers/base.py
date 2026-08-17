"""Provider interfaces.

Everything above this layer talks to these Protocols, never to a vendor SDK.
Swapping vendors means adding one file that satisfies the Protocol and one
line in the registry — no changes in the pipeline, API, or tests.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class BudgetExceeded(RuntimeError):
    """A call stopped because it would have spent past its ceiling.

    Typed so callers can tell "this stage broke" from "we ran out of money".
    A stage may choose to carry on without its own result; none may carry on
    after this, because the spend already happened and was never recorded.
    """


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Server-side web searches performed during the call. Billed per search
    # rather than per token, so it cannot be folded into the counts above.
    web_search_requests: int = 0


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        web_search: bool = False,
        budget_micros: int | None = None,
    ) -> LLMResult:
        """Return a single completion. Implementations must not retry — the
        pipeline owns retry policy so it stays uniform across vendors.

        `web_search` asks the provider to ground the answer in live sources. It
        is a hint, not a contract: a provider without a search tool is free to
        ignore it and answer from what it already knows.

        `budget_micros` is the most this one call may spend. A single request is
        already bounded by `max_tokens`, but a call that internally resumes —
        a server-side tool loop, say — can spend a multiple of that, so the
        ceiling has to be enforced inside the call rather than around it.
        Implementations must raise rather than return a partial result once
        they cross it. `None` means no limit.
        """
        ...

    def cost_micros(self, usage: LLMUsage) -> int:
        """Cost of this usage in millionths of a US dollar."""
        ...


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    provider: str
    description: str
    # Vendor-side identifier. Kept separate from `id` so our stable public id
    # survives a provider swap.
    external_id: str = ""


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    content_type: str
    characters: int


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def voices(self) -> list[Voice]: ...

    def synthesize(self, *, text: str, voice: Voice) -> SynthesisResult: ...

    def cost_micros(self, characters: int) -> int: ...
