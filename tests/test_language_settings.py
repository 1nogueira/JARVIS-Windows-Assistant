from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import backend.api.app as api_module
from backend.core.i18n import form_of_address, get_language, language_context, tr
from backend.responses import build_speech_text, response_from_tool_result
from backend.memory.database import Database
from backend.memory.short_term import ConversationMemory
from backend.observability.telemetry import TelemetryStore
from backend.tasks import TaskStore
from backend.security.session import session_credentials
from backend.tools.labels import CONFIRMATIONS, DESCRIPTIONS
from backend.tools.registry import ConfirmationRequired
from backend.tools.results import explicit_failure, explicit_success


@pytest.fixture
def client(monkeypatch, settings_store, tmp_path):
    settings_store.update({"privacy": {"history_enabled": False}, "voice": {"wake_word_enabled": False}})
    monkeypatch.setattr(api_module, "settings", settings_store)
    monkeypatch.setattr(api_module.services.runtime, "settings", settings_store)
    monkeypatch.setattr(api_module.services.runtime.tools, "settings", settings_store)
    database = Database(tmp_path / "language-tests.db")
    database.initialize()
    monkeypatch.setattr(api_module.services.runtime, "tasks", TaskStore(database))
    monkeypatch.setattr(api_module.services.runtime, "telemetry", TelemetryStore(database))
    api_module.services.runtime.tasks.initialize()
    api_module.services.runtime.telemetry.initialize()
    monkeypatch.setattr(api_module.services.runtime, "conversations", ConversationMemory(database))
    async def forbidden_stream(*args, **kwargs):
        raise AssertionError("Unexpected model streaming in an endpoint test")
        yield
    monkeypatch.setattr(api_module.services.runtime.engine, "stream", forbidden_stream)
    return TestClient(api_module.app, headers={"Authorization": f"Bearer {session_credentials.token}"})


def test_language_setting_is_validated_persisted_and_updates_recognition(client, settings_store):
    response = client.put("/api/settings", json={"settings": {"user": {"language": "en-US"}}})
    assert response.status_code == 200
    assert response.json()["user"]["language"] == "en-US"
    assert response.json()["voice"]["stt_language"] == "en"
    settings_store.reload()
    assert settings_store.section("user")["language"] == "en-US"
    assert client.get("/api/settings").json()["user"]["language"] == "en-US"
    for invalid in ("de-DE", "en", None, 1):
        assert client.put("/api/settings", json={"settings": {"user": {"language": invalid}}}).status_code == 422
    assert settings_store.section("user")["language"] == "en-US"
    restored = client.put("/api/settings", json={"settings": {"user": {"language": "pt-BR"}}})
    assert restored.json()["voice"]["stt_language"] == "pt"


def test_explicit_recognition_language_is_preserved(client):
    response = client.put("/api/settings", json={"settings": {"user": {"language": "en-US"}, "voice": {"stt_language": "pt"}}})
    assert response.status_code == 200
    assert response.json()["voice"]["stt_language"] == "pt"


@pytest.mark.parametrize(("language", "message", "prefix"), [
    ("pt-BR", "abre a camera e o vscode", "Abri"),
    ("en-US", "open the camera and vscode", "Opened"),
])
def test_chat_endpoint_uses_selected_language_with_verified_mock_results(client, monkeypatch, language, message, prefix):
    calls = []

    async def execute(name, arguments, *, request_id):
        calls.append((name, arguments))
        return explicit_success({"opened": arguments.get("query") or "Camera"})

    async def forbidden_inference(*args, **kwargs):
        raise AssertionError("Deterministic command must not call the model")

    monkeypatch.setattr(api_module.services.runtime.tools, "execute", execute)
    monkeypatch.setattr(api_module.services.runtime.tool_orchestrator.engine, "generate", forbidden_inference)
    assert client.put("/api/settings", json={"settings": {"user": {"language": language}}}).status_code == 200
    response = client.post("/api/chat", json={"request_id": str(uuid4()), "message": message})
    assert response.status_code == 200
    result = response.json()
    assert result["route"] == "task"
    assert result["message"].startswith(prefix)
    assert calls == [("open_camera", {}), ("open_app", {"query": "Visual Studio Code"})]
    assert all(action["status"] == "completed" for action in result["actions"])


def test_tool_and_skill_metadata_follow_language(client):
    client.put("/api/settings", json={"settings": {"user": {"language": "en-US"}}})
    tools = {item["name"]: item for item in client.get("/api/tools").json()}
    assert set(tools) == set(DESCRIPTIONS)
    assert tools["get_system_metrics"]["description"].startswith("Read current")
    assert tools["get_system_metrics"]["category"] == "System"
    assert all(tool.name in CONFIRMATIONS for tool in api_module.services.tools.registry._tools.values() if tool.confirmation_text)
    skills = {item["name"]: item for item in client.get("/api/skills").json()}
    assert skills["windows_apps"]["title"] == "Windows applications"
    assert client.get("/api/data-sources").json()[0]["name"] == "Local memory"


async def test_english_confirmation_is_localized_without_executing(settings_store, monkeypatch):
    registry = api_module.services.tools.registry
    settings_store.update({"user": {"language": "en-US"}})
    monkeypatch.setattr(registry, "settings", settings_store)
    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute("write_text_file", {"path": "example.txt", "content": "hello"}, request_id=str(uuid4()))
    assert pending.value.message == "May I save this file?"
    assert {item["label"] for item in pending.value.preview["details"]} == {"Path", "Content"}
    await registry.confirmations.reject(pending.value.confirmation_id)


async def test_language_context_is_isolated_between_concurrent_requests():
    async def value(language):
        with language_context(language):
            await asyncio.sleep(0)
            return tr("Olá", "Hello")

    assert await asyncio.gather(value("pt-BR"), value("en-US")) == ["Olá", "Hello"]
    assert get_language() == "pt-BR"


def test_english_results_require_success_and_preserve_data(settings_store):
    settings_store.update({"user": {"language": "en-US"}})
    assert form_of_address(settings_store) == "sir"
    assert response_from_tool_result("open_app", explicit_success({"opened": "Aplicativo Exemplo"}), language="en-US") == "I opened and verified Aplicativo Exemplo, sir."
    for result in ({}, {"verified": True}, explicit_failure("launch_not_verified")):
        assert response_from_tool_result("open_app", result, language="en-US").startswith("I could not")
    assert "the code" in build_speech_text("```python\nprint('Olá')\n```", language="en-US")
    assert "print" not in build_speech_text("```python\nprint('Olá')\n```", language="en-US")
