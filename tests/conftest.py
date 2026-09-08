from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.config import SettingsStore


@pytest.fixture
def settings_store(tmp_path: Path) -> SettingsStore:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "ollama": {"url": "http://127.0.0.1:11434", "chat_model": ""},
                "search": {"searxng_url": "http://127.0.0.1:8080", "timeout_seconds": 1},
                "agent": {"tool_timeout_seconds": 2, "max_steps": 3},
                "privacy": {"history_enabled": True, "keep_screenshots": False},
                "voice": {},
                "permissions": {},
                "user": {"default_location": "Lisboa, PT"},
                "storage": {"artifact_directory": str(tmp_path / "artifacts")},
            }
        ),
        encoding="utf-8",
    )
    return SettingsStore(path)
