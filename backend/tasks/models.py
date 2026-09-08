from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    NEEDS_CONFIRMATION = "awaiting_confirmation"  # compatibility alias
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATES = {
    ActionStatus.COMPLETED,
    ActionStatus.FAILED,
    ActionStatus.CANCELLED,
    ActionStatus.TIMED_OUT,
    ActionStatus.NEEDS_INPUT,
}


@dataclass(slots=True)
class ActionNode:
    label: str
    tool: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    status: ActionStatus = ActionStatus.PENDING
    detail: str = ""
    result: Any = None
    confirmation_id: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value.pop("result", None)
        return value
