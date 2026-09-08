from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.memory.database import Database


class ManagedAgentStore:
    """Persistent definitions and run history for manual and scheduled agents."""

    TYPES = {"manual", "daily", "weekly", "interval", "monitor"}

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS managed_agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    skills TEXT NOT NULL DEFAULT '[]',
                    tools TEXT NOT NULL DEFAULT '[]',
                    max_turns INTEGER NOT NULL DEFAULT 8,
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    schedule TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'idle',
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES managed_agents(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    trace_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_agent
                    ON agent_runs(agent_id, started_at DESC);
                """
            )

    def create(self, value: dict[str, Any]) -> dict[str, Any]:
        agent_type = str(value.get("type") or "manual")
        if agent_type not in self.TYPES:
            raise ValueError("Tipo de agente inválido.")
        now = datetime.now(UTC).isoformat()
        item = {
            "id": str(uuid4()),
            "name": str(value.get("name") or "Novo agente").strip()[:120],
            "type": agent_type,
            "instruction": str(value.get("instruction") or "").strip()[:20_000],
            "model": str(value.get("model") or "")[:200],
            "skills": [str(item) for item in value.get("skills", [])][:40],
            "tools": [str(item) for item in value.get("tools", [])][:80],
            "max_turns": max(1, min(int(value.get("max_turns") or 8), 32)),
            "memory_enabled": bool(value.get("memory_enabled", True)),
            "schedule": str(value.get("schedule") or "")[:200],
            "active": bool(value.get("active", True)),
            "state": "idle",
            "last_run_at": None,
            "next_run_at": value.get("next_run_at"),
            "created_at": now,
            "updated_at": now,
        }
        if not item["instruction"]:
            raise ValueError("A instrução do agente é obrigatória.")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_agents VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _agent_values(item),
            )
        return item

    def recover_interrupted(self) -> int:
        """Make runs interrupted by a process restart visible and runnable again."""
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            runs = connection.execute(
                """
                UPDATE agent_runs
                SET completed_at=?, status='interrupted',
                    summary='Execução interrompida pelo reinício do JARVIS.'
                WHERE status='running'
                """,
                (now,),
            )
            connection.execute(
                "UPDATE managed_agents SET state='idle', updated_at=? WHERE state='running'",
                (now,),
            )
        return int(runs.rowcount)

    def due(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_agents
                WHERE active=1 AND type!='manual' AND state!='running'
                    AND next_run_at IS NOT NULL AND next_run_at<=?
                ORDER BY next_run_at ASC LIMIT ?
                """,
                (now_iso, max(1, min(limit, 100))),
            ).fetchall()
        return [_agent_row(row) for row in rows]

    def begin_run(self, agent_id: str, request_id: str) -> dict[str, Any]:
        agent = self.get(agent_id)
        if not agent:
            raise ValueError("Agente não encontrado.")
        if agent["state"] == "running":
            raise ValueError("O agente já está em execução.")
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs(
                    id, agent_id, started_at, completed_at, status, summary,
                    trace_request_id
                ) VALUES(?, ?, ?, NULL, 'running', '', ?)
                """,
                (run_id, agent_id, now, request_id),
            )
            connection.execute(
                """
                UPDATE managed_agents SET state='running', last_run_at=?, updated_at=?
                WHERE id=?
                """,
                (now, now, agent_id),
            )
        return {"id": run_id, "agent_id": agent_id, "started_at": now, "status": "running"}

    def finish_run(
        self,
        agent_id: str,
        run_id: str,
        *,
        status: str,
        summary: str,
        next_run_at: str | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        state = "idle" if status in {"completed", "cancelled", "interrupted"} else "error"
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE agent_runs SET completed_at=?, status=?, summary=? WHERE id=?
                """,
                (now, status[:40], summary[:2_000], run_id),
            )
            connection.execute(
                """
                UPDATE managed_agents SET state=?, next_run_at=?, updated_at=? WHERE id=?
                """,
                (state, next_run_at, now, agent_id),
            )

    def list(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_agents ORDER BY active DESC, updated_at DESC"
            ).fetchall()
        return [_agent_row(row) for row in rows]

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_agents WHERE id=?", (agent_id,)
            ).fetchone()
        return _agent_row(row) if row else None

    def update(self, agent_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get(agent_id)
        if not current:
            return None
        allowed = {
            "name", "type", "instruction", "model", "skills", "tools", "max_turns",
            "memory_enabled", "schedule", "active", "state", "last_run_at", "next_run_at",
        }
        current.update({key: value for key, value in changes.items() if key in allowed})
        current["updated_at"] = datetime.now(UTC).isoformat()
        if current["type"] not in self.TYPES:
            raise ValueError("Tipo de agente inválido.")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE managed_agents SET name=?, type=?, instruction=?, model=?, skills=?, tools=?,
                    max_turns=?, memory_enabled=?, schedule=?, active=?, state=?, last_run_at=?,
                    next_run_at=?, updated_at=? WHERE id=?
                """,
                (
                    str(current["name"])[:120], current["type"], str(current["instruction"])[:20_000],
                    str(current["model"])[:200], json.dumps(current["skills"]), json.dumps(current["tools"]),
                    max(1, min(int(current["max_turns"]), 32)), int(bool(current["memory_enabled"])),
                    str(current["schedule"])[:200], int(bool(current["active"])), current["state"],
                    current["last_run_at"], current["next_run_at"], current["updated_at"], agent_id,
                ),
            )
        return self.get(agent_id)

    def delete(self, agent_id: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM managed_agents WHERE id=?", (agent_id,))
        return bool(cursor.rowcount)

    def runs(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY started_at DESC LIMIT ?",
                (agent_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]


def _agent_values(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["id"], item["name"], item["type"], item["instruction"], item["model"],
        json.dumps(item["skills"]), json.dumps(item["tools"]), item["max_turns"],
        int(item["memory_enabled"]), item["schedule"], int(item["active"]), item["state"],
        item["last_run_at"], item["next_run_at"], item["created_at"], item["updated_at"],
    )


def _agent_row(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["skills"] = json.loads(value["skills"])
    value["tools"] = json.loads(value["tools"])
    value["memory_enabled"] = bool(value["memory_enabled"])
    value["active"] = bool(value["active"])
    return value
