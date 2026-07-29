"""Provider interfaces.

Everything above this layer talks to these Protocols, never to a vendor SDK.
Swapping vendors means adding one file that satisfies the Protocol and one
line in the registry — no changes in the pipeline, API, or tests.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> LLMResult:
        """Return a single completion. Implementations must not retry — the
        pipeline owns retry policy so it stays uniform across vendors."""
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
