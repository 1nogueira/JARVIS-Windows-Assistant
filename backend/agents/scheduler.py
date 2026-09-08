from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.agents.managed import ManagedAgentStore
from backend.core.events import EventBus


AgentRunner = Callable[[dict[str, Any], str], Awaitable[str]]


class AgentScheduler:
    """Small local scheduler backed entirely by the managed-agent SQLite tables."""

    def __init__(
        self,
        store: ManagedAgentStore,
        events: EventBus,
        runner: AgentRunner,
        *,
        poll_seconds: float = 15.0,
    ) -> None:
        self.store = store
        self.events = events
        self.runner = runner
        self.poll_seconds = max(0.05, poll_seconds)
        self._loop_task: asyncio.Task[None] | None = None
        self._runs: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        await asyncio.to_thread(self.store.recover_interrupted)
        await self._prime_schedules()
        self._loop_task = asyncio.create_task(self._loop(), name="managed-agent-scheduler")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        active = list(self._runs.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._runs.clear()

    async def run_now(self, agent_id: str) -> str:
        agent = await asyncio.to_thread(self.store.get, agent_id)
        if not agent:
            raise ValueError("Agente não encontrado.")
        if agent_id in self._runs and not self._runs[agent_id].done():
            raise ValueError("O agente já está em execução.")
        request_id = str(uuid4())
        task = asyncio.create_task(self._execute(agent, request_id))
        self._runs[agent_id] = task
        task.add_done_callback(lambda _: self._runs.pop(agent_id, None))
        return request_id

    async def refresh(self, agent_id: str) -> dict[str, Any] | None:
        agent = await asyncio.to_thread(self.store.get, agent_id)
        if not agent:
            return None
        next_run = next_run_at(agent, datetime.now(UTC)) if agent["active"] else None
        return await asyncio.to_thread(
            self.store.update,
            agent_id,
            {"next_run_at": next_run.isoformat() if next_run else None},
        )

    async def _prime_schedules(self) -> None:
        now = datetime.now(UTC)
        for agent in await asyncio.to_thread(self.store.list):
            if not agent["active"] or agent["type"] == "manual" or agent["next_run_at"]:
                continue
            next_run = next_run_at(agent, now)
            await asyncio.to_thread(
                self.store.update,
                agent["id"],
                {"next_run_at": next_run.isoformat() if next_run else None},
            )

    async def _loop(self) -> None:
        while True:
            await self._prime_schedules()
            now = datetime.now(UTC)
            due = await asyncio.to_thread(self.store.due, now.isoformat())
            for agent in due:
                agent_id = str(agent["id"])
                if agent_id in self._runs and not self._runs[agent_id].done():
                    continue
                request_id = str(uuid4())
                task = asyncio.create_task(self._execute(agent, request_id))
                self._runs[agent_id] = task
                task.add_done_callback(lambda _, key=agent_id: self._runs.pop(key, None))
            await asyncio.sleep(self.poll_seconds)

    async def _execute(self, agent: dict[str, Any], request_id: str) -> None:
        run = await asyncio.to_thread(self.store.begin_run, agent["id"], request_id)
        await self.events.publish(
            "persistent_agent.started",
            {"request_id": request_id, "agent_id": agent["id"], "agent": agent["name"]},
        )
        status = "completed"
        summary = ""
        try:
            summary = await self.runner(agent, request_id)
        except asyncio.CancelledError:
            status = "cancelled"
            summary = "Execução cancelada durante o encerramento do JARVIS."
            raise
        except Exception as exc:
            status = "failed"
            summary = f"Falha: {str(exc)[:1_900]}"
        finally:
            next_run = next_run_at(agent, datetime.now(UTC))
            await asyncio.to_thread(
                self.store.finish_run,
                agent["id"],
                run["id"],
                status=status,
                summary=summary,
                next_run_at=next_run.isoformat() if next_run and agent["active"] else None,
            )
            await self.events.publish(
                "persistent_agent.completed" if status == "completed" else "persistent_agent.failed",
                {
                    "request_id": request_id,
                    "agent_id": agent["id"],
                    "agent": agent["name"],
                    "status": status,
                },
            )


def next_run_at(agent: dict[str, Any], after: datetime) -> datetime | None:
    """Return the next UTC execution time for supported local schedules."""
    kind = str(agent.get("type") or "manual")
    if kind == "manual":
        return None
    schedule = str(agent.get("schedule") or "").strip().casefold()
    if kind in {"interval", "monitor"}:
        seconds = _interval_seconds(schedule) or (300 if kind == "monitor" else 3600)
        return after + timedelta(seconds=seconds)
    local_after = after.astimezone()
    hour, minute = _clock(schedule)
    candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "daily":
        local_result = candidate if candidate > local_after else candidate + timedelta(days=1)
        return local_result.astimezone(UTC)
    if kind == "weekly":
        weekday = _weekday(schedule)
        days = (weekday - candidate.weekday()) % 7
        candidate += timedelta(days=days)
        local_result = candidate if candidate > local_after else candidate + timedelta(days=7)
        return local_result.astimezone(UTC)
    return None


def _clock(value: str) -> tuple[int, int]:
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", value)
    return (int(match.group(1)), int(match.group(2))) if match else (8, 0)


def _interval_seconds(value: str) -> int | None:
    match = re.search(r"(?:interval|every|cada)?\s*(\d+)\s*([smhd])?", value)
    if not match:
        return None
    amount = max(1, int(match.group(1)))
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(match.group(2) or "s", 1)
    return min(amount * factor, 31 * 86400)


def _weekday(value: str) -> int:
    names = {
        "monday": 0, "segunda": 0, "tuesday": 1, "terça": 1, "terca": 1,
        "wednesday": 2, "quarta": 2, "thursday": 3, "quinta": 3,
        "friday": 4, "sexta": 4, "saturday": 5, "sábado": 5, "sabado": 5,
        "sunday": 6, "domingo": 6,
    }
    return next((number for name, number in names.items() if name in value), 0)
