from __future__ import annotations

from typing import Any

from backend.core.config import SettingsStore
from backend.memory.long_term import LongTermMemory
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_failure, explicit_success


def register_memory_tools(
    registry: ToolRegistry, memory: LongTermMemory, settings: SettingsStore
) -> None:
    def require_memory() -> None:
        if not settings.section("privacy").get("memory_enabled", True):
            raise RuntimeError("A memória está desativada nas configurações de privacidade.")

    @registry.tool(
        name="remember",
        description=(
            "Guarda uma informação útil na memória local somente quando o usuário pedir explicitamente "
            "para lembrar. Nunca use para senhas, tokens ou segredos."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["key", "value"],
        },
        permission_level=PermissionLevel.CONFIRM,
        category="Memória",
        confirmation_text="Quer que eu guarde essa informação na memória local?",
    )
    async def remember(key: str, value: str, category: str = "general") -> dict[str, Any]:
        require_memory()
        return explicit_success(await memory.remember(key, value, category, explicit=True))

    @registry.tool(
        name="search_memory",
        description="Pesquisa preferências e fatos previamente guardados na memória local.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        },
        permission_level=PermissionLevel.SAFE,
        category="Memória",
    )
    async def search_memory(query: str = "", limit: int = 20) -> dict[str, Any]:
        require_memory()
        return explicit_success(await memory.search(query, limit))

    @registry.tool(
        name="forget_memory",
        description="Exclui uma memória específica pelo identificador.",
        parameters={"type": "object", "properties": {"memory_id": {"type": "integer"}}, "required": ["memory_id"]},
        permission_level=PermissionLevel.CONFIRM,
        category="Memória",
        confirmation_text="Quer que eu apague essa memória?",
    )
    async def forget_memory(memory_id: int) -> dict[str, bool]:
        require_memory()
        deleted = await memory.forget(memory_id)
        if not deleted:
            return explicit_failure(
                "memory_not_found",
                detail="A memória indicada não existe.",
                data={"memory_id": memory_id},
                verification="sqlite_delete_affected_zero_rows",
            )
        return explicit_success(
            {"deleted": memory_id}, verification="sqlite_row_deleted"
        )
