from __future__ import annotations

import asyncio
import calendar
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.core.config import SettingsStore
from backend.core.events import EventBus
from backend.core.logging import JsonlAuditLog
from backend.memory.database import Database
from backend.voice.tts import PiperTTS
from backend.voice.wakeword import WakeWordService


RECURRENCES = {"none", "daily", "weekly", "monthly"}


def parse_due_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Data inválida. Use o formato ISO 8601, por exemplo 2026-08-20T18:00:00-03:00.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(UTC)


def advance_due(value: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return value + timedelta(days=1)
    if recurrence == "weekly":
        return value + timedelta(days=7)
    if recurrence == "monthly":
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)
    return value


class ReminderStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        title: str,
        due_at: str,
        recurrence: str = "none",
        minutes_before: int = 0,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("O lembrete precisa de um título.")
        if recurrence not in RECURRENCES:
            raise ValueError("Recorrência inválida.")
        if not 0 <= minutes_before <= 10_080:
            raise ValueError("A antecedência deve ficar entre 0 e 10080 minutos.")
        due = parse_due_at(due_at)
        now = datetime.now(UTC)
        if recurrence == "none" and due <= now:
            raise ValueError("O horário do lembrete precisa estar no futuro.")
        while recurrence != "none" and due <= now:
            due = advance_due(due, recurrence)
        alert_at = due - timedelta(minutes=minutes_before)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders(title, due_at, alert_at, recurrence, minutes_before, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (clean_title, due.isoformat(), alert_at.isoformat(), recurrence, minutes_before),
            )
            reminder_id = int(cursor.lastrowid)
        return self.get(reminder_id)

    def get(self, reminder_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        if not row:
            raise ValueError("Lembrete não encontrado.")
        return dict(row)

    def list(self, active_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM reminders"
        parameters: list[Any] = []
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY alert_at ASC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def delete(self, reminder_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            if not cursor.rowcount:
                raise ValueError("Lembrete não encontrado.")
            remaining = connection.execute(
                "SELECT 1 FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        if remaining:
            raise RuntimeError("O lembrete continuou no banco após a exclusão.")
        return {"deleted": reminder_id}

    def update(
        self,
        reminder_id: int,
        title: str | None = None,
        due_at: str | None = None,
        recurrence: str | None = None,
        minutes_before: int | None = None,
    ) -> dict[str, Any]:
        current = self.get(reminder_id)
        next_title = (title if title is not None else str(current["title"])).strip()
        next_recurrence = recurrence if recurrence is not None else str(current["recurrence"])
        next_minutes = (
            minutes_before if minutes_before is not None else int(current["minutes_before"])
        )
        if not next_title:
            raise ValueError("O lembrete precisa de um título.")
        if next_recurrence not in RECURRENCES:
            raise ValueError("Recorrência inválida.")
        if not 0 <= next_minutes <= 10_080:
            raise ValueError("A antecedência deve ficar entre 0 e 10080 minutos.")
        due = parse_due_at(due_at or str(current["due_at"]))
        now = datetime.now(UTC)
        if next_recurrence == "none" and due <= now:
            raise ValueError("O horário do lembrete precisa estar no futuro.")
        while next_recurrence != "none" and due <= now:
            due = advance_due(due, next_recurrence)
        alert_at = due - timedelta(minutes=next_minutes)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET title = ?, due_at = ?, alert_at = ?, recurrence = ?, minutes_before = ?, active = 1
                WHERE id = ?
                """,
                (
                    next_title,
                    due.isoformat(),
                    alert_at.isoformat(),
                    next_recurrence,
                    next_minutes,
                    reminder_id,
                ),
            )
        return self.get(reminder_id)

    def claim_due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE active = 1 AND alert_at <= ?
                ORDER BY alert_at ASC LIMIT 20
                """,
                (moment.isoformat(),),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                due = parse_due_at(item["due_at"])
                recurrence = str(item["recurrence"])
                if recurrence == "none":
                    connection.execute("UPDATE reminders SET active = 0 WHERE id = ?", (item["id"],))
                else:
                    next_due = advance_due(due, recurrence)
                    while next_due <= moment:
                        next_due = advance_due(next_due, recurrence)
                    next_alert = next_due - timedelta(minutes=int(item["minutes_before"]))
                    connection.execute(
                        "UPDATE reminders SET due_at = ?, alert_at = ? WHERE id = ?",
                        (next_due.isoformat(), next_alert.isoformat(), item["id"]),
                    )
                claimed.append(item)
        return claimed


class ReminderMonitor:
    def __init__(
        self,
        settings: SettingsStore,
        events: EventBus,
        reminders: ReminderStore,
        tts: PiperTTS,
        wakeword: WakeWordService,
        audit_log: JsonlAuditLog,
    ) -> None:
        self.settings = settings
        self.events = events
        self.reminders = reminders
        self.tts = tts
        self.wakeword = wakeword
        self.audit_log = audit_log
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="jarvis-reminder-monitor")

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
            due = await asyncio.to_thread(self.reminders.claim_due)
            for reminder in due:
                message = reminder_message(reminder)
                await self.events.publish("reminder.due", {**reminder, "message": message})
                if self.settings.section("voice").get("enabled", True):
                    wake_status = self.wakeword.status()
                    resume_wakeword = bool(
                        wake_status.get("running") and not wake_status.get("paused")
                    )
                    try:
                        if resume_wakeword:
                            await self.wakeword.pause()
                        path = await self.tts.speak(message, f"reminder-{reminder['id']}")
                        path.unlink(missing_ok=True)
                        self.audit_log.write(
                            "reminder.announced", reminder_id=reminder["id"], message=message
                        )
                    except Exception as exc:  # Keep monitoring if audio fails.
                        self.audit_log.write(
                            "reminder.voice_failed", reminder_id=reminder["id"], error=str(exc)
                        )
                    finally:
                        if resume_wakeword:
                            await self.wakeword.resume()
            await asyncio.sleep(5)


def reminder_message(
    reminder: dict[str, Any], now: datetime | None = None
) -> str:
    title = str(reminder.get("title", "compromisso"))
    due_value = reminder.get("due_at")
    if due_value:
        due_at = parse_due_at(str(due_value))
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        remaining_seconds = (due_at - moment).total_seconds()
        if remaining_seconds > 30:
            minutes = max(1, round(remaining_seconds / 60))
            unit = "minuto" if minutes == 1 else "minutos"
            return f"Senhor, você tem {title} daqui a {minutes} {unit}."
        if remaining_seconds < -30:
            late_minutes = max(1, round(abs(remaining_seconds) / 60))
            unit = "minuto" if late_minutes == 1 else "minutos"
            return (
                f"Senhor, lembrete atrasado: {title} estava marcado para "
                f"há {late_minutes} {unit}."
            )
    normalized = title.casefold()
    if "remédio" in normalized or "remedio" in normalized:
        return "Senhor, está na hora de tomar seu remédio. Não se esqueça de beber água."
    if re.match(r"^(?:tomar|beber|fazer|ligar|desligar|buscar|comprar|enviar|telefonar)\b", normalized):
        return f"Senhor, você precisa {title} agora."
    return f"Senhor, lembrete: {title} agora."
