from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.memory.database import Database


@dataclass(slots=True)
class TelemetryRecord:
    request_id: str
    conversation_id: str
    agent: str
    model: str
    engine: str
    started_at: float
    completed_at: float
    ttft_ms: float | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    tokens_per_second: float | None = None
    tool_calls: int = 0
    tool_duration_ms: float = 0.0
    retries: int = 0
    cancelled: bool = False
    failed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return round((self.completed_at - self.started_at) * 1000, 3)

    def public_dict(self) -> dict[str, Any]:
        return {**asdict(self), "duration_ms": self.duration_ms}


class TelemetryStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    model TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL NOT NULL,
                    ttft_ms REAL,
                    prompt_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    tokens_per_second REAL,
                    tool_calls INTEGER NOT NULL,
                    tool_duration_ms REAL NOT NULL,
                    retries INTEGER NOT NULL,
                    cancelled INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_completed
                    ON telemetry(completed_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_request
                    ON telemetry(request_id);
                """
            )

    def record(self, record: TelemetryRecord) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO telemetry(
                    request_id, conversation_id, agent, model, engine, started_at,
                    completed_at, ttft_ms, prompt_tokens, output_tokens,
                    tokens_per_second, tool_calls, tool_duration_ms, retries,
                    cancelled, failed, metadata
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    completed_at=excluded.completed_at, ttft_ms=excluded.ttft_ms,
                    prompt_tokens=excluded.prompt_tokens, output_tokens=excluded.output_tokens,
                    tokens_per_second=excluded.tokens_per_second,
                    tool_calls=excluded.tool_calls, tool_duration_ms=excluded.tool_duration_ms,
                    retries=excluded.retries, cancelled=excluded.cancelled,
                    failed=excluded.failed, metadata=excluded.metadata
                """,
                (
                    record.request_id,
                    record.conversation_id,
                    record.agent,
                    record.model,
                    record.engine,
                    record.started_at,
                    record.completed_at,
                    record.ttft_ms,
                    record.prompt_tokens,
                    record.output_tokens,
                    record.tokens_per_second,
                    record.tool_calls,
                    record.tool_duration_ms,
                    record.retries,
                    int(record.cancelled),
                    int(record.failed),
                    json.dumps(record.metadata, ensure_ascii=False),
                ),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM telemetry ORDER BY completed_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def dashboard(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) requests,
                       AVG((completed_at-started_at)*1000) average_latency_ms,
                       AVG(ttft_ms) average_ttft_ms,
                       AVG(tokens_per_second) average_tokens_per_second,
                       SUM(tool_calls) tool_calls,
                       SUM(failed) failures,
                       SUM(cancelled) cancellations,
                       SUM(output_tokens) output_tokens
                FROM telemetry
                """
            ).fetchone()
            daily = connection.execute(
                """
                SELECT date(completed_at, 'unixepoch', 'localtime') day,
                       COUNT(*) requests, AVG(ttft_ms) ttft_ms,
                       AVG((completed_at-started_at)*1000) latency_ms,
                       SUM(tool_calls) tool_calls, SUM(failed) failures
                FROM telemetry GROUP BY day ORDER BY day DESC LIMIT 14
                """
            ).fetchall()
        return {
            "summary": dict(totals) if totals else {},
            "series": [dict(row) for row in reversed(daily)],
            "generated_at": time.time(),
        }


def _row_dict(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["cancelled"] = bool(value["cancelled"])
    value["failed"] = bool(value["failed"])
    value["duration_ms"] = round((value["completed_at"] - value["started_at"]) * 1000, 3)
    try:
        value["metadata"] = json.loads(value.get("metadata") or "{}")
    except json.JSONDecodeError:
        value["metadata"] = {}
    return value

