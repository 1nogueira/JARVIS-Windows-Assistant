from __future__ import annotations

import asyncio
import threading


class CancellationToken:
    """Thread-safe cooperative cancellation signal for blocking handlers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError

    def wait(self, seconds: float) -> None:
        if self._event.wait(max(0.0, seconds)):
            raise asyncio.CancelledError


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = asyncio.Lock()

    async def track(self, request_id: str, task: asyncio.Task[object]) -> None:
        async with self._lock:
            current = self._tasks.get(request_id)
            if current and not current.done():
                raise ValueError("Já existe uma tarefa ativa com esse request_id.")
            self._tasks[request_id] = task
            self._tokens[request_id] = CancellationToken()
        task.add_done_callback(lambda completed: asyncio.create_task(self.remove(request_id, completed)))

    async def remove(
        self, request_id: str, completed_task: asyncio.Task[object] | None = None
    ) -> None:
        async with self._lock:
            current = self._tasks.get(request_id)
            if completed_task is None or current is completed_task:
                self._tasks.pop(request_id, None)
                self._tokens.pop(request_id, None)

    async def token_for(self, request_id: str) -> CancellationToken:
        async with self._lock:
            return self._tokens.setdefault(request_id, CancellationToken())

    async def cancel(self, request_id: str | None = None) -> int:
        async with self._lock:
            if request_id is None:
                items = list(self._tasks.items())
            elif request_id in self._tasks:
                items = [(request_id, self._tasks[request_id])]
            else:
                items = []
            for key, _ in items:
                token = self._tokens.get(key)
                if token:
                    token.cancel()
        for _, task in items:
            task.cancel()
        return len(items)


task_manager = TaskManager()
