from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.agents.scheduler import AgentScheduler, next_run_at
from backend.core.events import EventBus
from backend.engines.base import EngineCapabilities, InferenceEngine, StreamChunk
from backend.engines.ollama import OllamaEngine
from backend.memory.database import Database
from backend.observability import ObservabilityRuntime, TelemetryRecord, TelemetryStore, TraceStore
from backend.responses import build_speech_text
from backend.routing import RequestRouter, RouteMode
from backend.runtime import AssistantRuntime
from backend.skills import SkillManifest, SkillRegistry
from backend.skills.deterministic import try_direct_intent
from backend.skills.windows_open import format_open_graph, task_graph_for_open
from backend.tasks import ActionNode, ActionStatus, TaskGraph
from backend.tools.registry import ConfirmationRequired
from backend.tools.results import explicit_success


class FakeEngine(InferenceEngine):
    engine_id = "fake"
    capabilities = EngineCapabilities(streaming=True)

    def __init__(self) -> None:
        self.generate_calls = 0
        self.stream_calls = 0

    async def generate(self, messages, *, model=None, tools=None):  # type: ignore[no-untyped-def]
        self.generate_calls += 1
        return {"message": {"content": "Canberra"}, "model": "fake-1"}

    async def stream(self, messages, *, model=None):  # type: ignore[no-untyped-def]
        self.stream_calls += 1
        yield StreamChunk(content="Can", model="fake-1")
        await asyncio.sleep(0)
        yield StreamChunk(content="berra", model="fake-1")
        yield StreamChunk(done=True, model="fake-1", prompt_tokens=12, output_tokens=2)

    async def list_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        return [{"name": "fake-1"}]

    async def health(self) -> dict[str, Any]:
        return {"online": True}


class FakeConversations:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    async def load(self, conversation_id: str) -> list[dict[str, str]]:
        return list(self.items)

    async def add(self, conversation_id: str, role: str, content: str, *, persist: bool = True) -> None:
        self.items.append({"role": role, "content": content})


class FakeTelemetry:
    def __init__(self) -> None:
        self.records: list[TelemetryRecord] = []

    def record(self, record: TelemetryRecord) -> None:
        self.records.append(record)


class NeverOrchestrator:
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A pergunta simples não pode chegar ao orquestrador de ferramentas")


def _runtime(settings_store):  # type: ignore[no-untyped-def]
    skills = SkillRegistry()
    skills.discover()
    engine = FakeEngine()
    telemetry = FakeTelemetry()
    runtime = AssistantRuntime(
        settings=settings_store,
        engine=engine,
        tool_orchestrator=NeverOrchestrator(),  # type: ignore[arg-type]
        conversations=FakeConversations(),  # type: ignore[arg-type]
        tools=object(),  # type: ignore[arg-type]
        events=EventBus(),
        telemetry=telemetry,  # type: ignore[arg-type]
        tasks=object(),  # type: ignore[arg-type]
        managed_agents=object(),  # type: ignore[arg-type]
        skills=skills,
    )
    return runtime, engine, telemetry


@pytest.mark.asyncio
async def test_simple_request_uses_fast_path_and_exactly_one_inference(settings_store) -> None:
    runtime, engine, telemetry = _runtime(settings_store)
    response = await runtime.run(
        "Qual é a capital da Austrália?", "fast", "request-fast"
    )
    assert response.route == "simple"
    assert response.message == "Canberra"
    assert response.metrics["inference_count"] == 1
    assert engine.stream_calls == 1
    assert engine.generate_calls == 0
    assert telemetry.records[0].agent == "simple"


@pytest.mark.asyncio
async def test_stream_yields_first_token_before_complete(settings_store) -> None:
    runtime, engine, _ = _runtime(settings_store)
    events = [
        event
        async for event in runtime.stream(
            "Explique algo estável.", "stream", "request-stream"
        )
    ]
    types = [event["type"] for event in events]
    assert types.index("token") < types.index("complete")
    assert "".join(event.get("content", "") for event in events) == "Canberra"
    assert events[-1]["response"]["metrics"]["ttft_ms"] is not None
    assert engine.stream_calls == 1


def test_router_and_lazy_skill_discovery() -> None:
    skills = SkillRegistry()
    summaries = skills.discover()
    assert len(summaries) >= 11
    assert not isinstance(skills.get("windows_apps"), SkillManifest)
    assert isinstance(skills.get("windows_apps", load_details=True), SkillManifest)
    router = RequestRouter(skills)
    assert router.route("Quanto é 2+2?").mode == RouteMode.SIMPLE
    assert router.route("Abra Discord.").mode == RouteMode.DIRECT_ACTION
    assert router.route("Abra Discord, Edge e VS Code.").mode == RouteMode.TASK
    assert router.route("Pesquise profundamente baterias de sódio.").mode == RouteMode.RESEARCH
    assert router.route("Faça um resumo toda manhã.").mode == RouteMode.SCHEDULED


def test_six_target_graph_keeps_every_target_and_reports_partial_success() -> None:
    graph = task_graph_for_open(
        "Abra YouTube, Visual Studio Code, Discord, Microsoft Edge, Epic Games e WhatsApp.",
        "multi-six",
    )
    assert graph is not None
    assert len(graph.actions) == 6
    assert [node.tool for node in graph.actions] == [
        "open_website", "open_app", "open_app", "open_app", "open_app", "open_app"
    ]
    for node in graph.actions:
        node.status = ActionStatus.COMPLETED
    graph.actions[4].status = ActionStatus.FAILED
    graph.actions[4].detail = "não instalado"
    answer = format_open_graph(graph)
    assert "Epic Games (não instalado)" in answer
    assert "abri tudo" not in answer.casefold()
    assert "WhatsApp" in answer


@pytest.mark.asyncio
async def test_desktop_followup_reuses_actual_filename(settings_store) -> None:
    class Registry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def execute(self, name: str, arguments: dict[str, Any], *, request_id: str):
            self.calls.append((name, arguments))
            return {
                "written": f"C:\\Users\\Example\\Desktop\\{arguments['filename']}",
                "opened": f"C:\\Users\\Example\\Desktop\\{arguments['filename']}",
            }

    registry = Registry()
    result = await try_direct_intent(
        "Eu falei no Desktop, não está aqui.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "desktop-followup",
        history=[{"role": "user", "content": "Crie example.txt contendo olá"}],
    )
    assert result is not None
    assert registry.calls[0][0] == "create_and_open_text_file"
    assert registry.calls[0][1]["filename"] == "example.txt"
    assert registry.calls[0][1]["directory"] == "desktop"
    assert "C:\\Users\\Example\\Desktop\\example.txt" in result.answer


@pytest.mark.asyncio
async def test_task_graph_pauses_and_resumes_after_confirmation() -> None:
    graph = TaskGraph(
        request_id="confirm-task",
        title="Confirmação no meio",
        actions=[
            ActionNode(label="Enviar mensagem", tool="send", arguments={}),
            ActionNode(label="Abrir relatório", tool="open", arguments={}),
        ],
    )
    calls: list[str] = []

    async def runner(node: ActionNode) -> dict[str, str]:
        calls.append(node.tool)
        if node.tool == "send":
            raise ConfirmationRequired("confirmation-1", "Autorizar envio?", "send", {})
        return explicit_success({"opened": "relatório"}, verification="test_window")

    await graph.execute(runner)
    assert graph.actions[0].status == ActionStatus.NEEDS_CONFIRMATION
    assert graph.actions[1].status == ActionStatus.PENDING

    async def resolve(confirmation_id: str, approved: bool) -> dict[str, str]:
        assert (confirmation_id, approved) == ("confirmation-1", True)
        return explicit_success({"sent": "ok"}, verification="test_send_readback")

    await graph.resume(graph.actions[0].id, True, resolve, runner)
    assert [node.status for node in graph.actions] == [
        ActionStatus.COMPLETED, ActionStatus.PENDING
    ]
    assert calls == ["send"]


def test_speech_renderer_never_reads_code_or_json() -> None:
    code = build_speech_text("Aqui está:\n```python\nprint('segredo')\n```")
    structured = build_speech_text('{"tool":"open_app","arguments":{"query":"Discord"}}')
    assert "print" not in code
    assert "código na tela" in code
    assert "open_app" not in structured
    assert "dados estruturados" in structured


@pytest.mark.asyncio
async def test_ollama_model_discovery_is_cached(settings_store) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"models": [{"name": "qwen:3b"}]})

    engine = OllamaEngine(settings_store, model_cache_ttl=60)
    engine._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handler)
    )
    try:
        await engine.list_models()
        await engine.list_models()
        assert requests == 1
        await engine.list_models(force=True)
        assert requests == 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_telemetry_and_traces_persist_without_private_content(tmp_path: Path) -> None:
    database = Database(tmp_path / "observability.db")
    database.initialize()
    telemetry = TelemetryStore(database)
    telemetry.initialize()
    traces = TraceStore(database)
    traces.initialize()
    telemetry.record(
        TelemetryRecord(
            request_id="observed", conversation_id="local", agent="simple",
            model="fake", engine="fake", started_at=time.time() - 0.1,
            completed_at=time.time(), ttft_ms=15, output_tokens=2,
            metadata={"route": "simple"},
        )
    )
    assert telemetry.dashboard()["summary"]["requests"] == 1

    bus = EventBus()
    runtime = ObservabilityRuntime(bus, traces)
    await runtime.start()
    await asyncio.sleep(0)
    await bus.publish(
        "request.routed",
        {
            "request_id": "observed", "route": "simple",
            "message": "conteúdo privado", "system_prompt": "não persistir",
        },
    )
    for _ in range(20):
        if traces.list(request_id="observed"):
            break
        await asyncio.sleep(0.01)
    await runtime.stop()
    event = traces.list(request_id="observed")[0]
    assert event["data"] == {"request_id": "observed", "route": "simple"}


@pytest.mark.asyncio
async def test_persistent_agent_survives_restart_and_records_run(tmp_path: Path) -> None:
    from backend.agents.managed import ManagedAgentStore

    database = Database(tmp_path / "agents.db")
    database.initialize()
    first = ManagedAgentStore(database)
    first.initialize()
    agent = first.create(
        {
            "name": "Brief local", "type": "interval", "instruction": "Resuma o sistema",
            "schedule": "interval 1s", "active": True,
            "next_run_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )
    interrupted = first.begin_run(agent["id"], "interrupted-request")
    assert interrupted["status"] == "running"

    restarted = ManagedAgentStore(database)
    restarted.initialize()
    assert restarted.recover_interrupted() == 1
    assert restarted.runs(agent["id"])[0]["status"] == "interrupted"
    restarted.update(
        agent["id"],
        {"next_run_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    executions: list[str] = []

    async def run_agent(item: dict[str, Any], request_id: str) -> str:
        executions.append(request_id)
        return "Concluído localmente."

    scheduler = AgentScheduler(restarted, EventBus(), run_agent, poll_seconds=0.01)
    await scheduler.start()
    for _ in range(50):
        if executions and restarted.runs(agent["id"])[0]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    await scheduler.stop()
    assert executions
    latest = restarted.runs(agent["id"])[0]
    assert latest["status"] == "completed"
    assert restarted.get(agent["id"])["next_run_at"] is not None  # type: ignore[index]
    assert next_run_at(restarted.get(agent["id"]) or {}, datetime.now(UTC)) is not None
