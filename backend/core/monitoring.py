from __future__ import annotations

import asyncio
import time

from backend.core.config import SettingsStore
from backend.core.events import EventBus
from backend.tools.system import system_metrics


class SystemMonitor:
    """Opt-in, low-frequency proactive alerts with per-alert cooldowns."""

    def __init__(self, settings: SettingsStore, events: EventBus) -> None:
        self.settings = settings
        self.events = events
        self._task: asyncio.Task[None] | None = None
        self._last_alert: dict[str, float] = {}

    async def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="jarvis-system-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            config = self.settings.section("proactive")
            interval = max(30, int(config.get("interval_seconds", 60)))
            if config.get("enabled", False):
                try:
                    metrics = await asyncio.to_thread(system_metrics)
                    await self._check("ram", metrics["ram_percent"], float(config.get("ram_warning_percent", 90)), "Uso de memória elevado")
                    await self._check("disk", metrics["disk_percent"], float(config.get("disk_warning_percent", 90)), "Armazenamento quase cheio")
                except RuntimeError:
                    pass
            await asyncio.sleep(interval)

    async def _check(self, kind: str, value: float, threshold: float, message: str) -> None:
        now = time.monotonic()
        if value >= threshold and now - self._last_alert.get(kind, 0) >= 1800:
            self._last_alert[kind] = now
            await self.events.publish(
                "proactive.alert", {"kind": kind, "value": value, "threshold": threshold, "message": message}
            )
