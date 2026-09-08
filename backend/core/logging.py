from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from backend.core.config import ROOT_DIR, settings


SENSITIVE_KEYS = {"password", "token", "secret", "cookie", "authorization", "api_key"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class JsonlAuditLog:
    def __init__(
        self,
        path: Path | None = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.path = path or ROOT_DIR / "logs" / "jarvis.jsonl"
        self._enabled = enabled or (lambda: True)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, **fields: Any) -> None:
        if not self._enabled():
            return
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            **_redact(fields),
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logging.getLogger(__name__).exception("Falha ao gravar audit log")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        result: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result


audit_log = JsonlAuditLog(
    enabled=lambda: bool(settings.section("privacy").get("structured_logs", True))
)
