from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.agents.base import AgentContext
from backend.agents.simple import RoutingSafetyError, SimpleAgent
from backend.api.app import app, services
from backend.core.events import EventBus
from backend.engines.base import EngineCapabilities, InferenceEngine, StreamChunk
from backend.security.session import session_credentials
from backend.skills import SkillRegistry
from backend.skills.windows_open import parse_open_targets
from backend.tasks import ActionNode, ActionStatus, TaskGraph
from backend.windows.app_resolver import AppCandidate, WindowsAppResolver


def _parsed(message: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(item.label, item.tool, item.arguments) for item in parse_open_targets(message)]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "abra youtube e instagram no navegador",
            [
                ("YouTube", "open_website", {"target": "youtube"}),
                ("Instagram", "open_website", {"target": "instagram"}),
            ],
        ),
        (
            "abra youtube instagram e tiktok no navegador",
            [
                ("YouTube", "open_website", {"target": "youtube"}),
                ("Instagram", "open_website", {"target": "instagram"}),
                ("TikTok", "open_website", {"target": "tiktok"}),
            ],
        ),
        ("abra a camera", [("Camera", "open_camera", {})]),
        (
            "abra a camera, o visual studio code e o discord",
            [
                ("Camera", "open_camera", {}),
                ("Visual Studio Code", "open_app", {"query": "Visual Studio Code"}),
                ("Discord", "open_app", {"query": "Discord"}),
            ],
        ),
        (
            "abra youtube, instagram no navegador, a camera e o visual studio code",
            [
                ("YouTube", "open_website", {"target": "youtube"}),
                ("Instagram", "open_website", {"target": "instagram"}),
                ("Camera", "open_camera", {}),
                ("Visual Studio Code", "open_app", {"query": "Visual Studio Code"}),
            ],
        ),
        ("abra o aplicativo do tiktok", [("TikTok", "open_app", {"query": "TikTok"})]),
        ("abra o tiktok no navegador", [("TikTok", "open_website", {"target": "tiktok"})]),
    ],
)
def test_open_parser_understands_contextual_modifiers(
    message: str, expected: list[tuple[str, str, dict[str, Any]]]
) -> None:
    parsed = _parsed(message)
    assert parsed == expected
    assert all(item[0].casefold() != "no navegador" for item in parsed)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("abre o vscode ai", [("Visual Studio Code", "open_app", {"query": "Visual Studio Code"})]),
        ("abre a camera pra mim", [("Camera", "open_camera", {})]),
        ("abre o discord", [("Discord", "open_app", {"query": "Discord"})]),
        ("abre o launcher", [("launcher", "open_app", {"query": "launcher"})]),
        (
            "abre a camera e o vscode",
            [
                ("Camera", "open_camera", {}),
                ("Visual Studio Code", "open_app", {"query": "Visual Studio Code"}),
            ],
        ),
    ],
)
def test_natural_windows_open_phrases(
    message: str, expected: list[tuple[str, str, dict[str, Any]]]
) -> None:
    assert _parsed(message) == expected


def test_resolver_prefers_real_vscode_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executable = tmp_path / "Code.exe"
    executable.write_bytes(b"")
    resolver = WindowsAppResolver()
    candidate = AppCandidate(
        "Visual Studio Code", "exe", str(executable), "known_path", ("code.exe",), ("vscode", "vs code")
    )
    monkeypatch.setattr(resolver, "_known_candidates", lambda: [candidate])

    resolved = resolver.resolve("abra o vscode")

    assert resolved.status == "found"
    assert resolved.candidate == candidate
    assert resolved.candidate.target.endswith("Code.exe")


def test_launcher_never_falls_back_to_launcher_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = WindowsAppResolver()
    candidates = [
        AppCandidate("Epic Games Launcher", "exe", r"C:\Epic\EpicGamesLauncher.exe", "known_path"),
        AppCandidate("Minecraft Launcher", "start_app", "Microsoft.Minecraft", "start_app"),
    ]
    monkeypatch.setattr(resolver, "_known_candidates", lambda: candidates)
    monkeypatch.setattr(resolver, "_registry_candidates", lambda: [])
    monkeypatch.setattr(resolver, "_start_app_candidates", lambda: [])
    monkeypatch.setattr(resolver, "_shortcut_candidates", lambda: [])
    monkeypatch.setattr(resolver, "_path_candidates", lambda: [])

    resolved = resolver.resolve("launcher")

    assert resolved.status == "ambiguous"
    assert {item.name for item in resolved.candidates} == {
        "Epic Games Launcher",
        "Minecraft Launcher",
    }
    result = resolver.launch("launcher")
    assert result["success"] is False
    assert result["error"] == "ambiguous_application"
    assert all(item["target"].casefold() != "launcher.exe" for item in result["candidates"])


@pytest.mark.asyncio
async def test_task_graph_rejects_structured_false_success() -> None:
    graph = TaskGraph(
        request_id="structured-failure",
        title="Abrir",
        actions=[ActionNode(label="VS Code", tool="open_app", arguments={})],
    )

    async def runner(_: ActionNode) -> dict[str, Any]:
        return {"success": False, "error": "launch_not_verified", "detail": "sem janela"}

    await graph.execute(runner)

    assert graph.actions[0].status == ActionStatus.FAILED
    assert graph.actions[0].detail == "sem janela"


class JsonToolEngine(InferenceEngine):
    engine_id = "json-tool"
    capabilities = EngineCapabilities(streaming=True)

    def __init__(self) -> None:
        self.stream_calls = 0

    async def generate(self, messages, *, model=None, tools=None):  # type: ignore[no-untyped-def]
        raise AssertionError("generate não deve ser usado")

    async def stream(self, messages, *, model=None):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        yield StreamChunk(content='{"name":"open_app",', model="fake")
        yield StreamChunk(content='"arguments":{"query":"Visual Studio Code"}}', model="fake")
        yield StreamChunk(done=True, model="fake")

    async def list_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {"online": True}


@pytest.mark.asyncio
async def test_simple_agent_never_streams_tool_json(settings_store) -> None:
    engine = JsonToolEngine()
    agent = SimpleAgent(engine, settings_store, EventBus())
    events = [
        event
        async for event in agent.stream(
            "Conte uma curiosidade.",
            AgentContext(request_id="protocol", conversation_id="protocol", history=[]),
        )
    ]
    visible = "".join(str(event.get("content", "")) for event in events)
    assert "open_app" not in visible
    assert "arguments" not in visible
    assert "Bloqueei uma chamada" in visible


@pytest.mark.asyncio
async def test_simple_agent_defensively_rejects_external_action(settings_store) -> None:
    engine = JsonToolEngine()
    agent = SimpleAgent(engine, settings_store, EventBus())
    with pytest.raises(RoutingSafetyError):
        _ = [
            event
            async for event in agent.stream(
                "abra a camera",
                AgentContext(request_id="routing", conversation_id="routing", history=[]),
            )
        ]
    assert engine.stream_calls == 0


def test_real_chat_endpoint_routes_compound_windows_command_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, arguments: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        calls.append((name, arguments))
        opened = arguments.get("query") or name.removeprefix("open_")
        return {
            "success": True,
            "verified": True,
            "opened": opened,
            "verification": "test_window",
        }

    monkeypatch.setattr(services.runtime.tools, "execute", execute)
    async def forbidden_inference(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Comando Windows determinístico não pode chamar o LLM")

    monkeypatch.setattr(
        services.runtime.tool_orchestrator.engine, "generate", forbidden_inference
    )
    started = time.perf_counter()
    response = TestClient(app).post(
        "/api/chat",
        headers={"Authorization": f"Bearer {session_credentials.token}"},
        json={
            "request_id": str(uuid4()),
            "conversation_id": f"e2e-{uuid4()}",
            "message": "abra a camera, o visual studio code e o discord",
        },
    )
    routing_and_execution_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "task"
    assert payload["agent"] == "orchestrator"
    assert calls == [
        ("open_camera", {}),
        ("open_app", {"query": "Visual Studio Code"}),
        ("open_app", {"query": "Discord"}),
    ]
    assert [item["status"] for item in payload["actions"]] == ["completed"] * 3
    assert "open_app" not in payload["message"]
    assert "arguments" not in payload["message"]
    assert routing_and_execution_ms < 1000


@pytest.mark.parametrize(
    ("message", "expected_route", "expected_calls"),
    [
        ("abre o vscode ai", "direct_action", [("open_app", {"query": "Visual Studio Code"})]),
        ("abre a camera pra mim", "direct_action", [("open_camera", {})]),
        ("abre o discord", "direct_action", [("open_app", {"query": "Discord"})]),
        ("abre o launcher", "direct_action", [("open_app", {"query": "launcher"})]),
        (
            "abre a camera e o vscode",
            "task",
            [("open_camera", {}), ("open_app", {"query": "Visual Studio Code"})],
        ),
    ],
)
def test_real_endpoint_handles_natural_windows_phrases_without_orchestrator_or_llm(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_route: str,
    expected_calls: list[tuple[str, dict[str, Any]]],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, arguments: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        calls.append((name, arguments))
        return {
            "success": True,
            "verified": True,
            "opened": arguments.get("query") or "Camera",
            "verification": "test_window",
        }

    async def forbidden_inference(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Comando Windows determinístico não pode chamar o LLM")

    monkeypatch.setattr(services.runtime.tools, "execute", execute)
    monkeypatch.setattr(
        services.runtime.tool_orchestrator.engine, "generate", forbidden_inference
    )
    assert not hasattr(services.runtime, "legacy_orchestrator")

    response = TestClient(app).post(
        "/api/chat",
        headers={"Authorization": f"Bearer {session_credentials.token}"},
        json={
            "request_id": str(uuid4()),
            "conversation_id": f"natural-e2e-{uuid4()}",
            "message": message,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == expected_route
    assert calls == expected_calls
    assert all(action["status"] == "completed" for action in payload["actions"])
    if expected_route == "direct_action":
        assert payload["agent"] == "direct"
        assert payload["engine"] == "tools"
        assert payload["metrics"]["inference_count"] == 0


def test_router_and_parser_latency_is_below_fifty_ms() -> None:
    skills = SkillRegistry()
    skills.discover()
    from backend.routing import RequestRouter

    router = RequestRouter(skills)
    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        decision = router.route("abra a camera, o visual studio code e o discord")
        targets = parse_open_targets("abra a camera, o visual studio code e o discord")
        samples.append((time.perf_counter() - started) * 1000)
        assert decision.mode.value == "task"
        assert len(targets) == 3
    assert max(samples) < 50
