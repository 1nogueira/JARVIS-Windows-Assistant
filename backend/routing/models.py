from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RouteMode(StrEnum):
    SIMPLE = "simple"
    DIRECT_ACTION = "direct_action"
    TASK = "task"
    RESEARCH = "research"
    SCHEDULED = "scheduled"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    mode: RouteMode
    agent: str
    skills: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    reason: str = ""
    requires_tools: bool = False

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["mode"] = self.mode.value
        return value

