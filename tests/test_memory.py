from __future__ import annotations

from pathlib import Path

import pytest

from backend.memory.database import Database
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ConversationMemory


@pytest.fixture
def local_database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "memory.db")
    value.initialize()
    return value


@pytest.mark.asyncio
async def test_remember_search_update_forget(local_database):
    memory = LongTermMemory(local_database)
    item = await memory.remember("editor favorito", "VS Code", "preferencias")
    results = await memory.search("editor")
    assert results[0]["value"] == "VS Code"
    assert await memory.update(item["id"], "editor favorito", "Zed", "preferencias")
    assert (await memory.search("Zed"))[0]["id"] == item["id"]
    assert await memory.forget(item["id"])
    assert await memory.search("editor") == []


@pytest.mark.asyncio
async def test_sensitive_memory_is_blocked(local_database):
    memory = LongTermMemory(local_database)
    with pytest.raises(ValueError, match="senhas"):
        await memory.remember("minha senha", "123456")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("acesso", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.synthetic_signature"),
        ("chave", "-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----"),
        ("github", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"),
        ("aws", "AKIAABCDEFGHIJKLMNOP"),
        ("authorization", "Bearer synthetic_value_1234567890"),
    ],
)
async def test_common_synthetic_secret_formats_are_blocked(local_database, key, value):
    memory = LongTermMemory(local_database)
    with pytest.raises(ValueError):
        await memory.remember(key, value)


@pytest.mark.asyncio
async def test_normal_preferences_are_not_false_positives(local_database):
    memory = LongTermMemory(local_database)
    item = await memory.remember(
        "preferência de sessão de estudo", "Gosto de estudar por 45 minutos"
    )
    assert item["value"] == "Gosto de estudar por 45 minutos"


@pytest.mark.asyncio
async def test_short_term_history_is_bounded(local_database):
    memory = ConversationMemory(local_database, max_messages=4)
    for index in range(12):
        await memory.add("chat", "user", f"mensagem {index}", persist=True)
    history = await memory.load("chat")
    assert len(history) <= 5
    assert history[-1]["content"] == "mensagem 11"


@pytest.mark.asyncio
async def test_clear_all_memories(local_database):
    memory = LongTermMemory(local_database)
    await memory.remember("a", "um")
    await memory.remember("b", "dois")
    assert await memory.clear() == 2
    assert await memory.search() == []
