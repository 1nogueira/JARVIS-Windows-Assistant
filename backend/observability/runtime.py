from __future__ import annotations

import asyncio

from backend.core.events import EventBus
from backend.observability.traces import TraceStore


class ObservabilityRuntime:
    """Consumes operational events into a privacy-safe trace store."""

    def __init__(self, events: EventBus, traces: TraceStore) -> None:
        self.events = events
        self.traces = traces
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._consume(), name="jarvis-trace-consumer")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _consume(self) -> None:
        async for event in self.events.subscribe():
            await asyncio.to_thread(self.traces.append, event)

