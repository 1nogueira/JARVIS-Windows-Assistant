from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from backend.tasks.models import ActionNode, ActionStatus, TERMINAL_STATES
from backend.tools.registry import ConfirmationRequired
from backend.tools.results import tool_result_detail, tool_result_outcome


NodeRunner = Callable[[ActionNode], Awaitable[Any]]
ConfirmationResolver = Callable[[str, bool], Awaitable[Any]]


@dataclass(slots=True)
class TaskGraph:
    request_id: str
    title: str
    actions: list[ActionNode]
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    async def execute(
        self,
        runner: NodeRunner,
        *,
        concurrent: bool = False,
    ) -> list[ActionNode]:
        if concurrent:
            await asyncio.gather(*(self._run_node(node, runner) for node in self.actions))
        else:
            for node in self.actions:
                await self._run_node(node, runner)
                if node.status == ActionStatus.NEEDS_CONFIRMATION:
                    break
        if all(node.status in TERMINAL_STATES for node in self.actions):
            self.completed_at = time.time()
        return self.actions

    async def _run_node(self, node: ActionNode, runner: NodeRunner) -> None:
        if node.status != ActionStatus.PENDING:
            return
        node.status = ActionStatus.RUNNING
        node.started_at = time.time()
        try:
            node.result = await runner(node)
        except asyncio.CancelledError:
            node.status = ActionStatus.CANCELLED
            node.detail = "Cancelada pelo usuário"
            node.completed_at = time.time()
            raise
        except TimeoutError as exc:
            node.status = ActionStatus.TIMED_OUT
            node.detail = str(exc)
            node.completed_at = time.time()
        except ConfirmationRequired as pending:
            node.status = ActionStatus.NEEDS_CONFIRMATION
            node.confirmation_id = pending.confirmation_id
            node.detail = pending.message
        except Exception as exc:
            node.status = ActionStatus.FAILED
            node.detail = str(exc)
            node.completed_at = time.time()
        else:
            _apply_result(node)
            node.completed_at = time.time()

    async def resume(
        self,
        action_id: str,
        approved: bool,
        resolver: ConfirmationResolver,
        runner: NodeRunner,
    ) -> list[ActionNode]:
        node = next((item for item in self.actions if item.id == action_id), None)
        if not node or node.status != ActionStatus.NEEDS_CONFIRMATION or not node.confirmation_id:
            raise ValueError("Ação aguardando confirmação não encontrada.")
        if approved:
            node.status = ActionStatus.RUNNING
            try:
                node.result = await resolver(node.confirmation_id, True)
            except asyncio.CancelledError:
                node.status = ActionStatus.CANCELLED
                node.detail = "Cancelada pelo usuário"
                node.completed_at = time.time()
                raise
            except Exception as exc:
                node.status = ActionStatus.FAILED
                node.detail = str(exc)
                node.completed_at = time.time()
            else:
                _apply_result(node)
                node.completed_at = time.time()
        else:
            await resolver(node.confirmation_id, False)
            node.status = ActionStatus.CANCELLED
            node.detail = "Não autorizada pelo usuário"
            node.completed_at = time.time()
        node.confirmation_id = None
        # A confirmation is a one-shot capability for this node only. The
        # approval must never advance unrelated pending nodes implicitly.
        if all(item.status in TERMINAL_STATES for item in self.actions):
            self.completed_at = time.time()
        return self.actions

    @property
    def summary(self) -> dict[str, int]:
        return {
            state.value: sum(node.status == state for node in self.actions)
            for state in ActionStatus
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "title": self.title,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "summary": self.summary,
            "actions": [node.public_dict() for node in self.actions],
        }


def _apply_result(node: ActionNode) -> None:
    outcome = tool_result_outcome(node.result)
    node.status = {
        "completed": ActionStatus.COMPLETED,
        "failed": ActionStatus.FAILED,
        "needs_input": ActionStatus.NEEDS_INPUT,
    }[outcome]
    node.detail = tool_result_detail(node.result)
