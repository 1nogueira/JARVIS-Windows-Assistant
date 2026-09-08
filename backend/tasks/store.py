from __future__ import annotations

import json
from typing import Any

from backend.memory.database import Database
from backend.tasks.graph import TaskGraph


class TaskStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_graphs (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_task_graphs_request
                    ON task_graphs(request_id, created_at DESC);
                """
            )

    def save(self, graph: TaskGraph) -> dict[str, Any]:
        payload = graph.public_dict()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_graphs(id, request_id, title, payload, created_at, completed_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, completed_at=excluded.completed_at
                """,
                (
                    graph.id,
                    graph.request_id,
                    graph.title,
                    json.dumps(payload, ensure_ascii=False),
                    graph.created_at,
                    graph.completed_at,
                ),
            )
        return payload

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM task_graphs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM task_graphs WHERE request_id=? ORDER BY created_at DESC LIMIT 1",
                (request_id,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

