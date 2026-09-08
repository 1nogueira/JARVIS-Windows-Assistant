from __future__ import annotations

from backend.core.logging import JsonlAuditLog


def test_structured_log_toggle_applies_at_runtime(tmp_path) -> None:
    enabled = False
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path, enabled=lambda: enabled)

    log.write("disabled", value=1)
    assert not path.exists()

    enabled = True
    log.write("enabled", password="synthetic-secret")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert '"action": "enabled"' in content
    assert "synthetic-secret" not in content
    assert "[REDACTED]" in content

    enabled = False
    before = path.read_text(encoding="utf-8")
    log.write("disabled-again")
    assert path.read_text(encoding="utf-8") == before
