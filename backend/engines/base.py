from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    streaming: bool = True
    tool_calling: bool = False
    vision: bool = False
    embeddings: bool = False
    local: bool = True


@dataclass(slots=True)
class StreamChunk:
    content: str = ""
    done: bool = False
    model: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_duration_ns: int = 0
    eval_duration_ns: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class InferenceEngine(ABC):
    engine_id: str
    capabilities = EngineCapabilities()

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk()

    @abstractmethod
    async def list_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        return None

