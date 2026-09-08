from __future__ import annotations

import json
from typing import Any

from backend.memory.database import Database


SAFE_EVENT_FIELDS = {
    "request_id", "conversation_id", "route", "agent", "engine", "model", "skill",
    "skills", "tool", "status", "step", "elapsed_ms", "confirmation_id", "error",
    "reason", "mode", "prompt_tokens", "output_tokens", "ttft_ms", "duration_ms",
    "action_id", "label", "agent_id",
}


class TraceStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trace_request
                    ON trace_events(request_id, id);
                CREATE INDEX IF NOT EXISTS idx_trace_time
                    ON trace_events(id DESC);
                """
            )

    def append(self, event: dict[str, Any]) -> None:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        safe = {key: _safe_value(value) for key, value in data.items() if key in SAFE_EVENT_FIELDS}
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO trace_events(event_id, request_id, timestamp, event, data) VALUES(?, ?, ?, ?, ?)",
                (
                    str(event.get("id") or ""),
                    request_id,
                    str(event.get("timestamp") or ""),
                    str(event.get("event") or "unknown"),
                    json.dumps(safe, ensure_ascii=False),
                ),
            )

    def list(self, *, request_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        capped = max(1, min(limit, 1000))
        with self.database.connect() as connection:
            if request_id:
                rows = connection.execute(
                    "SELECT * FROM trace_events WHERE request_id=? ORDER BY id LIMIT ?",
                    (request_id, capped),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM trace_events ORDER BY id DESC LIMIT ?", (capped,)
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            result.append(item)
        return result


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:20]]
    return str(value)[:500]
