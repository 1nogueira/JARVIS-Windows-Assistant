from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from backend.memory.database import Database


class LongTermMemory:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def remember(
        self, key: str, value: str, category: str = "general", explicit: bool = True
    ) -> dict[str, Any]:
        if _looks_sensitive(key, value):
            raise ValueError("Não armazeno senhas, tokens ou segredos na memória.")
        return await asyncio.to_thread(self._remember_sync, key, value, category, explicit)

    def _remember_sync(
        self, key: str, value: str, category: str, explicit: bool
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO memories(key, value, category, explicit) VALUES (?, ?, ?, ?)",
                (key.strip(), value.strip(), category.strip() or "general", int(explicit)),
            )
            memory_id = cursor.lastrowid
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            return dict(row)

    async def search(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._search_sync, query, max(1, min(limit, 100)))

    def _search_sync(self, query: str, limit: int) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if query.strip():
                pattern = f"%{query.strip()}%"
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
                    ORDER BY updated_at DESC, id DESC LIMIT ?
                    """,
                    (pattern, pattern, pattern, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memories ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    async def update(self, memory_id: int, key: str, value: str, category: str) -> bool:
        if _looks_sensitive(key, value):
            raise ValueError("Não armazeno senhas, tokens ou segredos na memória.")
        return await asyncio.to_thread(self._update_sync, memory_id, key, value, category)

    def _update_sync(self, memory_id: int, key: str, value: str, category: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memories SET key = ?, value = ?, category = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (key.strip(), value.strip(), category.strip() or "general", memory_id),
            )
            return cursor.rowcount > 0

    async def forget(self, memory_id: int) -> bool:
        return await asyncio.to_thread(self._forget_sync, memory_id)

    def _forget_sync(self, memory_id: int) -> bool:
        with self.database.connect() as connection:
            deleted = connection.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            ).rowcount > 0
            remaining = connection.execute(
                "SELECT 1 FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            return deleted and remaining is None

    async def clear(self) -> int:
        return await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> int:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM memories")
            return cursor.rowcount


def _looks_sensitive(key: str, value: str) -> bool:
    normalized_key = _normalize(key)
    combined = f"{key}\n{value}"
    normalized = _normalize(combined)
    field_pattern = re.compile(
        r"\b(?:senha|password|passwd|passphrase|token|segredo|secret|credential|credencial|"
        r"authorization|cookie|api[ _-]?key|access[ _-]?key|client[ _-]?secret|"
        r"private[ _-]?key|chave[ _-]?privada|session[ _-]?(?:id|key|token)|pat)\b"
    )
    if field_pattern.search(normalized_key):
        return True
    patterns = (
        r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----",
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bAIza[0-9A-Za-z_-]{30,}\b",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b",
        r"https?://[^\s/:]+:[^\s/@]+@[^\s]+",
    )
    if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in patterns):
        return True
    return bool(field_pattern.search(normalized) and re.search(r"[:=]", combined))


def _normalize(value: str) -> str:
    return (
        unicodedata.normalize("NFKD", value.casefold())
        .encode("ascii", "ignore")
        .decode()
    )
