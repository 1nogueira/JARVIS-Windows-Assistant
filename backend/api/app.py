from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backend.agents.managed import ManagedAgentStore
from backend.agents.scheduler import AgentScheduler
from backend.agents.tool_orchestrator import ToolOrchestratorAgent
from backend.core.cancellation import task_manager
from backend.core.config import settings
from backend.core.events import event_bus
from backend.core.i18n import language_context, tr
from backend.core.logging import audit_log
from backend.core.monitoring import SystemMonitor
from backend.core.reminders import ReminderMonitor
from backend.engines.ollama import OllamaEngine
from backend.core.models import (
    ChatRequest,
    ChatResponse,
    ConfirmationRequest,
    MemoryCreate,
    SettingsUpdate,
    VoiceTranscriptionResponse,
)
from backend.memory.database import database
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ConversationMemory
from backend.intelligence.models import ModelCatalog
from backend.observability import ObservabilityRuntime, TelemetryStore, TraceStore
from backend.runtime import AssistantRuntime
from backend.skills import SkillManifest, SkillRegistry
from backend.tasks import TaskStore
from backend.security.confirmations import confirmation_manager
from backend.security.session import (
    bearer_token,
    session_credentials,
    websocket_session_token,
)
from backend.tools.factory import ToolServices, create_tool_services
from backend.tools.registry import ToolError
from backend.tools.system import running_processes, system_metrics
from backend.voice.audio_manager import AudioManager
from backend.voice.music import MusicService, register_music_tools
from backend.voice.stt import WhisperCppSTT
from backend.voice.tts import PiperTTS
from backend.voice.wakeword import WakeWordService
from backend.windows import get_app_resolver


class ToolToggle(BaseModel):
    enabled: bool


class MemoryUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="general", max_length=80)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    request_id: str = "voice"
    play_local: bool = False


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_at: str = Field(min_length=10, max_length=80)
    recurrence: str = "none"
    minutes_before: int = Field(default=0, ge=0, le=10_080)


class ReminderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_at: str | None = Field(default=None, min_length=10, max_length=80)
    recurrence: str | None = None
    minutes_before: int | None = Field(default=None, ge=0, le=10_080)


class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class ManagedAgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = "manual"
    instruction: str = Field(min_length=1, max_length=20_000)
    model: str = Field(default="", max_length=200)
    skills: list[str] = Field(default_factory=list, max_length=40)
    tools: list[str] = Field(default_factory=list, max_length=80)
    max_turns: int = Field(default=8, ge=1, le=32)
    memory_enabled: bool = True
    schedule: str = Field(default="", max_length=200)
    active: bool = True


class ManagedAgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    type: str | None = None
    instruction: str | None = Field(default=None, min_length=1, max_length=20_000)
    model: str | None = Field(default=None, max_length=200)
    skills: list[str] | None = Field(default=None, max_length=40)
    tools: list[str] | None = Field(default=None, max_length=80)
    max_turns: int | None = Field(default=None, ge=1, le=32)
    memory_enabled: bool | None = None
    schedule: str | None = Field(default=None, max_length=200)
    active: bool | None = None


@dataclass(slots=True)
class Services:
    ollama: OllamaEngine
    memory: LongTermMemory
    conversations: ConversationMemory
    tools: ToolServices
    orchestrator: ToolOrchestratorAgent
    audio: AudioManager
    stt: WhisperCppSTT
    tts: PiperTTS
    music: MusicService
    wakeword: WakeWordService
    skills: SkillRegistry
    models: ModelCatalog
    telemetry: TelemetryStore
    traces: TraceStore
    observability: ObservabilityRuntime
    tasks: TaskStore
    managed_agents: ManagedAgentStore
    runtime: AssistantRuntime
    scheduler: AgentScheduler


def build_services() -> Services:
    memory = LongTermMemory(database)
    conversations = ConversationMemory(
        database, max_messages=int(settings.section("agent").get("history_messages", 16))
    )
    music = MusicService(settings)
    tools = create_tool_services(settings, event_bus, confirmation_manager, audit_log, memory)
    register_music_tools(tools.registry, music)
    ollama = OllamaEngine(settings)
    skills = SkillRegistry()
    skills.discover()
    telemetry = TelemetryStore(database)
    traces = TraceStore(database)
    tasks = TaskStore(database)
    managed_agents = ManagedAgentStore(database)
    orchestrator = ToolOrchestratorAgent(
        settings, ollama, tools.registry, conversations, event_bus
    )
    runtime = AssistantRuntime(
        settings=settings,
        engine=ollama,
        tool_orchestrator=orchestrator,
        conversations=conversations,
        tools=tools.registry,
        events=event_bus,
        telemetry=telemetry,
        tasks=tasks,
        managed_agents=managed_agents,
        skills=skills,
    )
    async def run_managed(agent: dict[str, Any], request_id: str) -> str:
        response = await runtime.run_managed(agent, request_id)
        return response.display_text

    scheduler = AgentScheduler(managed_agents, event_bus, run_managed)
    return Services(
        ollama=ollama,
        memory=memory,
        conversations=conversations,
        tools=tools,
        orchestrator=orchestrator,
        audio=AudioManager(),
        stt=WhisperCppSTT(settings),
        tts=PiperTTS(settings, event_bus),
        music=music,
        wakeword=WakeWordService(settings, event_bus),
        skills=skills,
        models=ModelCatalog(ollama),
        telemetry=telemetry,
        traces=traces,
        observability=ObservabilityRuntime(event_bus, traces),
        tasks=tasks,
        managed_agents=managed_agents,
        runtime=runtime,
        scheduler=scheduler,
    )


services = build_services()
# Keep direct ASGI clients (and the packaged launcher health probe) usable
# before the lifespan hook is entered.
database.initialize()
services.telemetry.initialize()
services.traces.initialize()
services.tasks.initialize()
services.managed_agents.initialize()
system_monitor = SystemMonitor(settings, event_bus)
reminder_monitor = ReminderMonitor(
    settings,
    event_bus,
    services.tools.reminders,
    services.tts,
    services.wakeword,
    audit_log,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(database.initialize)
    await asyncio.to_thread(services.telemetry.initialize)
    await asyncio.to_thread(services.traces.initialize)
    await asyncio.to_thread(services.tasks.initialize)
    await asyncio.to_thread(services.managed_agents.initialize)
    await services.observability.start()
    await services.scheduler.start()
    audit_log.write("backend.started", pid=os.getpid())
    if settings.section("voice").get("wake_word_enabled", False):
        try:
            await services.wakeword.start()
        except RuntimeError as exc:
            audit_log.write("wakeword.unavailable", error=str(exc))
    await system_monitor.start()
    await reminder_monitor.start()
    warmup_task = asyncio.create_task(services.ollama.warmup())
    app_index_warmup_task = asyncio.create_task(
        asyncio.to_thread(get_app_resolver().warm)
    )
    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
        if not app_index_warmup_task.done():
            app_index_warmup_task.cancel()
        await services.scheduler.stop()
        await reminder_monitor.stop()
        await system_monitor.stop()
        await services.wakeword.stop()
        await services.music.stop()
        await services.tools.browser.close()
        await services.ollama.close()
        await services.observability.stop()
        audit_log.write("backend.stopped")


app = FastAPI(
    title="JARVIS Local API",
    version="0.3.6",
    description="API local do assistente JARVIS. Não exponha esta porta à internet.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def response_language(request: Request, call_next: Any) -> Any:
    with language_context(settings):
        return await call_next(request)


@app.middleware("http")
async def require_session_credential(request: Request, call_next: Any) -> Any:
    if request.method == "OPTIONS" or request.url.path == "/api/status":
        return await call_next(request)
    candidate = bearer_token(
        request.headers.get("authorization"), request.headers.get("x-jarvis-session")
    )
    if not session_credentials.verify(candidate):
        return JSONResponse(status_code=401, content={"detail": tr("Sessão local inválida.", "Invalid local session.")})
    return await call_next(request)


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    ollama = await services.ollama.health()
    ollama["selected"] = str(settings.section("ollama").get("chat_model", ""))
    return {
        "name": "JARVIS",
        "version": app.version,
        "online": True,
        "ollama": ollama,
        "voice": {
            "stt": services.stt.status(),
            "tts": services.tts.status(),
            "theme": services.music.status(),
            "wakeword": services.wakeword.status(),
        },
        "search": {
            "configured": True,
            "searxng_configured": bool(settings.section("search").get("searxng_url")),
            "fallback": "DuckDuckGo/Bing",
        },
    }


@app.get("/api/identity")
async def api_identity() -> dict[str, Any]:
    return {
        "name": "JARVIS",
        "version": app.version,
        "instance_id": session_credentials.instance_id,
        "pid": os.getpid(),
    }


@app.get("/api/models")
async def api_models() -> dict[str, Any]:
    health = await services.ollama.health()
    if health["online"]:
        await services.models.refresh()
    selected = str(settings.section("ollama").get("chat_model", ""))
    catalog = services.models.public_dict(selected)
    return {**health, **catalog, "selected_settings": settings.section("ollama")}


@app.put("/api/models/selected")
async def api_model_select(payload: ModelSelection) -> dict[str, Any]:
    models = await services.ollama.list_models()
    available = {str(item.get("name") or "") for item in models}
    if payload.model not in available:
        raise HTTPException(status_code=404, detail="Modelo não encontrado no Ollama.")
    settings.update({"ollama": {"chat_model": payload.model}})
    return {"selected": payload.model, "restart_required": False}


@app.get("/api/engines")
async def api_engines() -> list[dict[str, Any]]:
    return [await services.ollama.health()]


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(payload: ChatRequest) -> ChatResponse:
    request_id = str(payload.request_id)
    task: asyncio.Task[object] = asyncio.create_task(
        services.runtime.run(payload.message, payload.conversation_id, request_id)
    )
    try:
        await task_manager.track(request_id, task)
    except ValueError as exc:
        task.cancel()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        result = await task
        assert isinstance(result, ChatResponse)
        return result
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=499, detail="Tarefa cancelada.") from exc


@app.post("/api/chat/stream")
async def api_chat_stream(payload: ChatRequest) -> StreamingResponse:
    request_id = str(payload.request_id)
    # The runtime already enforces finite model output. Keeping this hand-off
    # queue non-blocking guarantees that cancellation can always publish its
    # terminal event, even when the HTTP consumer disconnects mid-stream.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in services.runtime.stream(
                payload.message, payload.conversation_id, request_id
            ):
                queue.put_nowait(event)
        except asyncio.CancelledError:
            queue.put_nowait({"type": "cancelled", "request_id": request_id})
            raise
        except Exception as exc:
            queue.put_nowait({"type": "error", "detail": str(exc)[:500]})
        finally:
            queue.put_nowait(None)

    producer: asyncio.Task[object] = asyncio.create_task(produce())
    try:
        await task_manager.track(request_id, producer)
    except ValueError as exc:
        producer.cancel()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def event_stream():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_name = str(item.get("type") or "message")
                data = json.dumps(item, ensure_ascii=False, default=str)
                yield f"event: {event_name}\ndata: {data}\n\n"
        finally:
            if not producer.done():
                producer.cancel()
                await task_manager.cancel(request_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.delete("/api/conversations/{conversation_id}")
async def api_conversation_clear(conversation_id: str) -> dict[str, Any]:
    if not conversation_id or len(conversation_id) > 120:
        raise HTTPException(status_code=400, detail=tr("Conversa inválida.", "Invalid conversation."))
    await services.conversations.clear(conversation_id)
    return {"cleared": conversation_id}


@app.post("/api/cancel")
async def api_cancel(request_id: str | None = None) -> dict[str, Any]:
    stopped_tts = await services.tts.stop()
    count = await task_manager.cancel(request_id)
    return {"cancelled_tasks": count, "stopped_speech": stopped_tts}


@app.post("/api/confirm")
async def api_confirm(payload: ConfirmationRequest) -> dict[str, Any]:
    request_id = await services.orchestrator.confirmation_request_id(payload.confirmation_id)
    try:
        if not request_id:
            return await services.orchestrator.confirm(payload.confirmation_id, payload.approved)
        task: asyncio.Task[object] = asyncio.create_task(
            services.orchestrator.confirm(payload.confirmation_id, payload.approved)
        )
        try:
            await task_manager.track(request_id, task)
        except ValueError:
            task.cancel()
            raise
        result = await task
        if isinstance(result, ChatResponse):
            return result.model_dump(mode="json")
        assert isinstance(result, dict)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/memory")
async def api_memory(query: str = "", limit: int = Query(50, ge=1, le=100)) -> list[dict[str, Any]]:
    _require_memory_enabled()
    return await services.memory.search(query, limit)


@app.post("/api/memory", status_code=201)
async def api_memory_create(payload: MemoryCreate) -> dict[str, Any]:
    _require_memory_enabled()
    try:
        return await services.memory.remember(payload.key, payload.value, payload.category, payload.explicit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/memory/{memory_id}")
async def api_memory_update(memory_id: int, payload: MemoryUpdate) -> dict[str, bool]:
    _require_memory_enabled()
    try:
        updated = await services.memory.update(memory_id, payload.key, payload.value, payload.category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail=tr("Memória não encontrada.", "Memory not found."))
    return {"updated": True}


@app.delete("/api/memory/{memory_id}")
async def api_memory_delete(memory_id: int) -> dict[str, bool]:
    _require_memory_enabled()
    deleted = await services.memory.forget(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=tr("Memória não encontrada.", "Memory not found."))
    return {"deleted": True}


@app.delete("/api/memory")
async def api_memory_clear(confirm: bool = False) -> dict[str, int]:
    _require_memory_enabled()
    if not confirm:
        raise HTTPException(status_code=400, detail=tr("Confirmação explícita necessária.", "Explicit confirmation required."))
    return {"deleted": await services.memory.clear()}


@app.get("/api/reminders")
async def api_reminders() -> list[dict[str, Any]]:
    return await asyncio.to_thread(services.tools.reminders.list)


@app.post("/api/reminders", status_code=201)
async def api_reminder_create(payload: ReminderCreate) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            services.tools.reminders.create,
            payload.title,
            payload.due_at,
            payload.recurrence,
            payload.minutes_before,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/reminders/{reminder_id}")
async def api_reminder_delete(reminder_id: int) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(services.tools.reminders.delete, reminder_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/reminders/{reminder_id}")
async def api_reminder_update(
    reminder_id: int, payload: ReminderUpdate
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            services.tools.reminders.update,
            reminder_id,
            payload.title,
            payload.due_at,
            payload.recurrence,
            payload.minutes_before,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tools")
async def api_tools() -> list[dict[str, Any]]:
    return services.tools.registry.list_public()


@app.patch("/api/tools/{tool_name}")
async def api_tool_toggle(tool_name: str, payload: ToolToggle) -> dict[str, Any]:
    if not services.tools.registry.set_enabled(tool_name, payload.enabled):
        raise HTTPException(status_code=404, detail=tr("Ferramenta não encontrada.", "Tool not found."))
    return {"name": tool_name, "enabled": payload.enabled}


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    return settings.all()


@app.put("/api/settings")
async def api_settings_update(payload: SettingsUpdate) -> dict[str, Any]:
    changes = payload.settings.model_dump(exclude_none=True, exclude_unset=True)
    language = changes.get("user", {}).get("language")
    if language and "stt_language" not in changes.get("voice", {}):
        changes.setdefault("voice", {})["stt_language"] = "en" if language == "en-US" else "pt"
    previous = settings.all()
    updated = settings.update(changes)
    previous_voice = previous.get("voice", {})
    updated_voice = updated.get("voice", {})
    listener_fields = {
        "wake_word_enabled",
        "wake_word",
        "sensitivity",
        "wake_vad_threshold",
        "microphone",
    }
    listener_changed = any(
        previous_voice.get(field) != updated_voice.get(field) for field in listener_fields
    )
    if listener_changed:
        try:
            if bool(updated_voice.get("wake_word_enabled", False)):
                await services.wakeword.restart()
            else:
                await services.wakeword.stop()
        except RuntimeError as exc:
            audit_log.write("wakeword.unavailable", error=str(exc))
    return updated


@app.get("/api/system")
async def api_system() -> dict[str, Any]:
    try:
        metrics, processes = await asyncio.gather(
            asyncio.to_thread(system_metrics), asyncio.to_thread(running_processes, 12, "memory")
        )
        return {"metrics": metrics, "processes": processes}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/logs")
async def api_logs(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(audit_log.recent, limit)


@app.get("/api/skills")
async def api_skills() -> list[dict[str, Any]]:
    return [skill.public_dict() for skill in services.skills.list()]


@app.get("/api/skills/{skill_name}")
async def api_skill_detail(skill_name: str) -> dict[str, Any]:
    skill = services.skills.get(skill_name, load_details=True)
    if not isinstance(skill, SkillManifest):
        raise HTTPException(status_code=404, detail=tr("Skill não encontrada.", "Skill not found."))
    return skill.public_dict()


@app.get("/api/tasks")
async def api_tasks(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(services.tasks.list, limit)


@app.get("/api/tasks/{request_id}")
async def api_task(request_id: str) -> dict[str, Any]:
    result = await asyncio.to_thread(services.tasks.get_by_request, request_id)
    if not result:
        raise HTTPException(status_code=404, detail=tr("Task não encontrada.", "Task not found."))
    return result


@app.get("/api/telemetry")
async def api_telemetry(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(services.telemetry.recent, limit)


@app.get("/api/telemetry/dashboard")
async def api_telemetry_dashboard() -> dict[str, Any]:
    return await asyncio.to_thread(services.telemetry.dashboard)


@app.get("/api/traces")
async def api_traces(
    request_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        services.traces.list, request_id=request_id, limit=limit
    )


@app.get("/api/agents")
async def api_agents() -> list[dict[str, Any]]:
    return await asyncio.to_thread(services.managed_agents.list)


@app.post("/api/agents", status_code=201)
async def api_agent_create(payload: ManagedAgentCreate) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            services.managed_agents.create,
            payload.model_dump(mode="python"),
        )
        return await services.scheduler.refresh(result["id"]) or result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/agents/{agent_id}")
async def api_agent_update(agent_id: str, payload: ManagedAgentUpdate) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True, mode="python")
    try:
        result = await asyncio.to_thread(
            services.managed_agents.update, agent_id, changes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail=tr("Agente não encontrado.", "Agent not found."))
    return await services.scheduler.refresh(agent_id) or result


@app.delete("/api/agents/{agent_id}")
async def api_agent_delete(agent_id: str) -> dict[str, bool]:
    deleted = await asyncio.to_thread(services.managed_agents.delete, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=tr("Agente não encontrado.", "Agent not found."))
    return {"deleted": True}


@app.get("/api/agents/{agent_id}/runs")
async def api_agent_runs(
    agent_id: str, limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    if not await asyncio.to_thread(services.managed_agents.get, agent_id):
        raise HTTPException(status_code=404, detail=tr("Agente não encontrado.", "Agent not found."))
    return await asyncio.to_thread(services.managed_agents.runs, agent_id, limit)


@app.post("/api/agents/{agent_id}/run", status_code=202)
async def api_agent_run(agent_id: str) -> dict[str, str]:
    try:
        request_id = await services.scheduler.run_now(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"agent_id": agent_id, "request_id": request_id, "status": "running"}


@app.get("/api/data-sources")
async def api_data_sources() -> list[dict[str, Any]]:
    privacy = settings.section("privacy")
    storage = settings.section("storage")
    return [
        {
            "id": "memory",
            "name": tr("Memória local", "Local memory"),
            "type": "sqlite",
            "status": "connected" if privacy.get("memory_enabled", True) else "disabled",
            "local": True,
            "description": tr("Preferências explícitas e histórico local no SQLite.", "Explicit preferences and local history in SQLite."),
        },
        {
            "id": "filesystem",
            "name": tr("Sistema de arquivos", "File system"),
            "type": "filesystem",
            "status": "available",
            "local": True,
            "description": tr("Arquivos e pastas escolhidos pelo usuário.", "Files and folders selected by the user."),
        },
        {
            "id": "artifacts",
            "name": tr("Arquivos do JARVIS", "JARVIS files"),
            "type": "folder",
            "status": "available",
            "local": True,
            "path": str(storage.get("artifact_directory") or ""),
            "description": tr("Capturas e documentos criados pelo assistente.", "Screenshots and documents created by the assistant."),
        },
        {
            "id": "screenshots",
            "name": tr("Capturas de tela", "Screenshots"),
            "type": "vision",
            "status": "available" if privacy.get("keep_screenshots", False) else "ephemeral",
            "local": True,
            "description": tr("Visão local; conteúdo externo sempre tratado como não confiável.", "Local vision; external content is always treated as untrusted."),
        },
    ]


@app.post("/api/voice/transcribe", response_model=VoiceTranscriptionResponse)
async def api_transcribe(request: Request) -> VoiceTranscriptionResponse:
    data = await request.body()
    if not data or len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=tr("Áudio vazio ou acima de 30 MB.", "Audio is empty or exceeds 30 MB."))
    try:
        wav = await services.audio.normalize_to_wav(data, request.headers.get("content-type", "audio/wav"))
        await event_bus.publish("speech.started")
        text = await services.stt.transcribe(wav)
        await event_bus.publish("speech.transcribed", {"text": text})
        return VoiceTranscriptionResponse(text=text, engine="whisper.cpp")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/voice/speak")
async def api_speak(payload: SpeakRequest) -> Any:
    try:
        if payload.play_local:
            if not settings.section("voice").get("enabled", True):
                return {"played": False}
            wake_status = services.wakeword.status()
            resume_wakeword = bool(
                wake_status.get("running") and not wake_status.get("paused")
            )
            if resume_wakeword:
                await services.wakeword.pause()
            try:
                path = await services.tts.speak(payload.text, payload.request_id)
                path.unlink(missing_ok=True)
            finally:
                if resume_wakeword:
                    await services.wakeword.resume()
            return {"played": True}
        path = await services.tts.synthesize(payload.text, payload.request_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    background = BackgroundTask(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="audio/wav", filename="jarvis.wav", background=background)


@app.post("/api/voice/theme/play")
async def api_theme_play() -> dict[str, Any]:
    try:
        return await services.music.play()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/voice/theme/stop")
async def api_theme_stop() -> dict[str, Any]:
    return await services.music.stop()


@app.post("/api/voice/theme/pause")
async def api_theme_pause() -> dict[str, Any]:
    return await services.music.pause()


@app.post("/api/voice/theme/resume")
async def api_theme_resume() -> dict[str, Any]:
    return await services.music.resume()


@app.post("/api/voice/theme/restart")
async def api_theme_restart() -> dict[str, Any]:
    return await services.music.restart()


@app.post("/api/voice/wakeword/start")
async def api_wakeword_start() -> dict[str, Any]:
    try:
        return await services.wakeword.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/voice/wakeword/stop")
async def api_wakeword_stop() -> dict[str, Any]:
    return await services.wakeword.stop()


@app.post("/api/voice/wakeword/pause")
async def api_wakeword_pause() -> dict[str, Any]:
    return await services.wakeword.pause()


@app.post("/api/voice/wakeword/resume")
async def api_wakeword_resume() -> dict[str, Any]:
    return await services.wakeword.resume()


@app.post("/api/voice/microphone/mute")
async def api_microphone_mute() -> dict[str, Any]:
    return await services.wakeword.mute_commands()


@app.post("/api/voice/microphone/unmute")
async def api_microphone_unmute() -> dict[str, Any]:
    return await services.wakeword.unmute_commands()


@app.get("/api/onboarding/checks")
async def api_onboarding_checks() -> dict[str, Any]:
    ollama = await services.ollama.health()
    return {
        "backend": {"ok": True, "detail": tr("Python e API ativos", "Python and API running")},
        "ollama": {"ok": ollama["online"], "detail": tr("Conectado", "Connected") if ollama["online"] else tr("Não detectado", "Not detected")},
        "model": {"ok": bool(ollama["models"]), "detail": tr(f"{len(ollama['models'])} modelo(s)", f"{len(ollama['models'])} model(s)")},
        "microphone": {"ok": services.wakeword.status()["available"], "detail": tr("Driver local disponível", "Local driver available")},
        "stt": services.stt.status(),
        "tts": services.tts.status(),
        "search": {"ok": True, "detail": tr("SearXNG com contingência DuckDuckGo/Bing", "SearXNG with DuckDuckGo/Bing fallback")},
        "playwright": {"ok": _module_available("playwright"), "detail": tr("Automação de navegador", "Browser automation")},
        "ffmpeg": {"ok": bool(services.audio.find_ffmpeg()), "detail": tr("Conversão de áudio", "Audio conversion")},
    }


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    token = websocket_session_token(websocket.headers.get("sec-websocket-protocol"))
    if not session_credentials.origin_allowed(origin) or not session_credentials.verify(token):
        await websocket.close(code=1008, reason=tr("Sessão local ou origem inválida.", "Invalid local session or origin."))
        return
    await websocket.accept(subprotocol="jarvis-events")
    try:
        async for event in event_bus.subscribe():
            await websocket.send_json(event)
    except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
        return


def _module_available(name: str) -> bool:
    try:
        return __import__(name) is not None
    except ImportError:
        return False


def _require_memory_enabled() -> None:
    if not settings.section("privacy").get("memory_enabled", True):
        raise HTTPException(status_code=403, detail=tr("A memória está desativada nas configurações de privacidade.", "Memory is disabled in the privacy settings."))
