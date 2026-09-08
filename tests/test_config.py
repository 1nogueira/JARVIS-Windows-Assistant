from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.core.models import SettingsUpdate

def test_deep_update_preserves_siblings(settings_store):
    settings_store.update({"ollama": {"chat_model": "qwen3:4b"}})
    config = settings_store.all()
    assert config["ollama"]["url"] == "http://127.0.0.1:11434"
    assert config["ollama"]["chat_model"] == "qwen3:4b"


def test_update_persists_to_disk(settings_store):
    settings_store.update({"privacy": {"history_enabled": False}})
    settings_store.reload()
    assert settings_store.section("privacy")["history_enabled"] is False


def test_return_values_are_copies(settings_store):
    value = settings_store.all()
    value["ollama"]["url"] = "changed"
    assert settings_store.section("ollama")["url"] != "changed"


@pytest.mark.parametrize(
    "settings",
    [
        {"ollama": {"context_size": None}},
        {"ollama": {"temperature": "quente"}},
        {"voice": {"wake_word_enabled": "false"}},
        {"voice": {"sensitivity": 1.5}},
        {"agent": {"max_steps": 0}},
        {"interface": {"animations": 1}},
    ],
)
def test_invalid_settings_are_rejected_before_persistence(settings_store, settings):
    before = settings_store.path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate({"settings": settings})
    assert settings_store.path.read_text(encoding="utf-8") == before


def test_current_valid_settings_shape_is_accepted(settings_store):
    payload = SettingsUpdate.model_validate(
        {
            "settings": {
                "ollama": {
                    "url": "http://127.0.0.1:11434",
                    "context_size": 8192,
                    "temperature": 0.2,
                },
                "voice": {"wake_word_enabled": False, "sensitivity": 0.45},
                "agent": {"max_steps": 8, "tool_timeout_seconds": 45},
                "privacy": {"history_enabled": True, "structured_logs": False},
            }
        }
    )
    changes = payload.settings.model_dump(exclude_none=True, exclude_unset=True)
    updated = settings_store.update(changes)
    assert updated["agent"]["max_steps"] == 8
