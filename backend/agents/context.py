from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.models import ActionTrace


@dataclass(slots=True)
class AgentRunContext:
    request_id: str
    conversation_id: str
    actions: list[ActionTrace] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0

    def add_sources(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        for item in result.get("results", []):
            if isinstance(item, dict) and item.get("url"):
                self.sources.append(
                    {
                        key: item.get(key)
                        for key in ("title", "url", "source", "published_date")
                        if item.get(key) is not None
                    }
                )

