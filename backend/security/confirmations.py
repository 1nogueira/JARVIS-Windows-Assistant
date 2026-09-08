from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class PendingAction:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    description: str
    preview: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    request_id: str
    fingerprint: str


class ConfirmationManager:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        description: str,
        preview: dict[str, Any],
        request_id: str,
    ) -> PendingAction:
        now = datetime.now(UTC)
        action_id = str(uuid4())
        immutable_arguments = deepcopy(arguments)
        immutable_preview = deepcopy(preview)
        immutable_preview.update(
            {"action_id": action_id, "tool": tool_name, "one_shot": True}
        )
        action = PendingAction(
            id=action_id,
            tool_name=tool_name,
            arguments=immutable_arguments,
            description=description,
            preview=immutable_preview,
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            request_id=request_id,
            fingerprint=_action_fingerprint(tool_name, immutable_arguments, request_id),
        )
        async with self._lock:
            self._prune_locked(now)
            self._pending[action.id] = action
        return action

    async def consume(self, confirmation_id: str) -> PendingAction | None:
        async with self._lock:
            action = self._pending.pop(confirmation_id, None)
        if (
            action
            and action.expires_at > datetime.now(UTC)
            and action.fingerprint
            == _action_fingerprint(action.tool_name, action.arguments, action.request_id)
        ):
            return action
        return None

    async def get(self, confirmation_id: str) -> PendingAction | None:
        now = datetime.now(UTC)
        async with self._lock:
            self._prune_locked(now)
            action = self._pending.get(confirmation_id)
            return deepcopy(action) if action else None

    async def reject(self, confirmation_id: str) -> bool:
        now = datetime.now(UTC)
        async with self._lock:
            action = self._pending.pop(confirmation_id, None)
        return bool(action and action.expires_at > now)

    def _prune_locked(self, now: datetime) -> None:
        expired = [key for key, action in self._pending.items() if action.expires_at <= now]
        for key in expired:
            del self._pending[key]


confirmation_manager = ConfirmationManager()


def _action_fingerprint(
    tool_name: str, arguments: dict[str, Any], request_id: str
) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "arguments": arguments, "request_id": request_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
