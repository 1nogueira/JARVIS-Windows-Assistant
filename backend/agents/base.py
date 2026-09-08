from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentContext:
    request_id: str
    conversation_id: str = "default"
    history: list[dict[str, Any]] = field(default_factory=list)
    skills: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    display_text: str
    speech_text: str = ""
    agent: str = ""
    model: str = ""
    engine: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    agent_id: str
    accepts_tools: bool = False

    @abstractmethod
    async def run(self, message: str, context: AgentContext) -> AgentResult:
        raise NotImplementedError

    async def stream(
        self, message: str, context: AgentContext
    ) -> AsyncIterator[dict[str, Any]]:
        result = await self.run(message, context)
        yield {"type": "token", "content": result.display_text}
        yield {"type": "complete", "result": result}

