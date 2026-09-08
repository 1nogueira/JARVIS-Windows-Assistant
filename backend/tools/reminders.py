from __future__ import annotations

import asyncio
from typing import Any

from backend.core.reminders import ReminderStore
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success


def register_reminder_tools(registry: ToolRegistry, reminders: ReminderStore) -> None:
    @registry.tool(
        name="create_reminder",
        description=(
            "Cria um lembrete local com aviso por voz. due_at deve ser ISO 8601 com data e hora; "
            "para toda quinta-feira, calcule a próxima quinta e use recurrence='weekly'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due_at": {"type": "string"},
                "recurrence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                "minutes_before": {"type": "integer", "minimum": 0, "maximum": 10080},
            },
            "required": ["title", "due_at"],
        },
        permission_level=PermissionLevel.SAFE,
        category="Lembretes",
    )
    async def create_reminder(
        title: str,
        due_at: str,
        recurrence: str = "none",
        minutes_before: int = 0,
    ) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(
                reminders.create, title, due_at, recurrence, minutes_before
            ),
            verification="inserted_reminder_row_read_back",
        )

    @registry.tool(
        name="list_reminders",
        description="Lista os próximos lembretes e compromissos ativos do usuário.",
        parameters={"type": "object", "properties": {}},
        permission_level=PermissionLevel.SAFE,
        category="Lembretes",
    )
    async def list_reminders() -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(reminders.list),
            verification="active_reminder_rows_read_from_SQLite",
        )

    @registry.tool(
        name="update_reminder",
        description=(
            "Altera um lembrete existente pelo id. O horário em due_at é o horário real do "
            "compromisso; minutes_before controla somente a antecedência do aviso."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer"},
                "title": {"type": "string"},
                "due_at": {"type": "string"},
                "recurrence": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly", "monthly"],
                },
                "minutes_before": {"type": "integer", "minimum": 0, "maximum": 10080},
            },
            "required": ["reminder_id"],
        },
        permission_level=PermissionLevel.SAFE,
        category="Lembretes",
    )
    async def update_reminder(
        reminder_id: int,
        title: str | None = None,
        due_at: str | None = None,
        recurrence: str | None = None,
        minutes_before: int | None = None,
    ) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(
                reminders.update,
                reminder_id,
                title,
                due_at,
                recurrence,
                minutes_before,
            ),
            verification="updated_reminder_row_read_back",
        )

    @registry.tool(
        name="delete_reminder",
        description="Exclui um lembrete local pelo identificador.",
        parameters={
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
        permission_level=PermissionLevel.CONFIRM,
        category="Lembretes",
        confirmation_text="Quer que eu exclua esse lembrete, senhor?",
    )
    async def delete_reminder(reminder_id: int) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(reminders.delete, reminder_id),
            verification="reminder_row_absence_verified",
        )
