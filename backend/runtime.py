from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from backend.agents.base import AgentContext, AgentResult
from backend.agents.managed import ManagedAgentStore
from backend.agents.research import ResearchAgent
from backend.agents.simple import SimpleAgent
from backend.agents.tool_orchestrator import ToolOrchestratorAgent
from backend.core.config import SettingsStore
from backend.core.events import EventBus
from backend.core.i18n import form_of_address, get_language, localized, tr
from backend.core.models import ActionTrace, AssistantState, ChatResponse
from backend.engines.base import InferenceEngine
from backend.memory.short_term import ConversationMemory
from backend.observability.telemetry import TelemetryRecord, TelemetryStore
from backend.responses import build_speech_text
from backend.routing import (
    RequestRouter,
    RouteDecision,
    RouteMode,
    has_external_action_intent,
)
from backend.skills import SkillRegistry
from backend.skills.deterministic import try_direct_intent
from backend.skills.windows_open import (
    execute_open_graph,
    format_open_graph,
    task_graph_for_open,
)
from backend.tasks import TaskStore
from backend.tools.registry import ToolRegistry


class AssistantRuntime:
    """Route requests to agents and verified tool execution."""

    def __init__(
        self,
        *,
        settings: SettingsStore,
        engine: InferenceEngine,
        tool_orchestrator: ToolOrchestratorAgent,
        conversations: ConversationMemory,
        tools: ToolRegistry,
        events: EventBus,
        telemetry: TelemetryStore,
        tasks: TaskStore,
        managed_agents: ManagedAgentStore,
        skills: SkillRegistry,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.tool_orchestrator = tool_orchestrator
        self.conversations = conversations
        self.tools = tools
        self.events = events
        self.telemetry = telemetry
        self.tasks = tasks
        self.managed_agents = managed_agents
        self.skills = skills
        self.router = RequestRouter(skills)
        self.simple = SimpleAgent(engine, settings, events)
        self.research = ResearchAgent(engine, tools, settings, events)

    @localized
    async def run(
        self,
        message: str,
        conversation_id: str,
        request_id: str,
    ) -> ChatResponse:
        decision = await self._begin_request(message, conversation_id, request_id)
        started = time.perf_counter()
        try:
            response = await self._run_decision(
                decision, message, conversation_id, request_id
            )
        except asyncio.CancelledError:
            await self.events.publish(
                "request.cancelled",
                {"request_id": request_id, "agent": decision.agent, "route": decision.mode.value},
            )
            await self._record_telemetry(
                request_id,
                conversation_id,
                decision,
                started,
                ChatResponse(
                    request_id=request_id,
                    message=tr("Solicitação cancelada, senhor.", "Request cancelled, sir."),
                    speech_text=tr("Solicitação cancelada, senhor.", "Request cancelled, sir."),
                    state=AssistantState.IDLE,
                ),
                cancelled=True,
            )
            raise
        except Exception:
            await self.events.publish(
                "request.failed",
                {"request_id": request_id, "agent": decision.agent, "route": decision.mode.value},
            )
            raise
        await self._complete_request(response, decision, conversation_id, started)
        return response

    @localized
    async def run_managed(
        self,
        agent: dict[str, Any],
        request_id: str,
    ) -> ChatResponse:
        """Execute a persisted definition without routing it back into scheduling."""
        message = _strip_schedule(str(agent.get("instruction") or ""))
        decision = self.router.route(message)
        if decision.mode == RouteMode.SIMPLE and has_external_action_intent(message):
            decision = RouteDecision(
                mode=RouteMode.DIRECT_ACTION,
                agent="direct",
                skills=decision.skills,
                confidence=1.0,
                reason="barreira defensiva: efeito externo não pode usar o agente simples",
                requires_tools=True,
            )
        if decision.mode == RouteMode.SCHEDULED:
            decision = RouteDecision(
                mode=RouteMode.SIMPLE,
                agent="simple",
                confidence=0.8,
                reason="execução de definição persistente já agendada",
            )
        conversation_id = f"agent:{agent['id']}" if agent.get("memory_enabled", True) else f"agent-run:{request_id}"
        started = time.perf_counter()
        await self.events.publish(
            "request.created",
            {"request_id": request_id, "conversation_id": conversation_id},
        )
        await self.events.publish(
            "request.routed",
            {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "route": decision.mode.value,
                "agent": decision.agent,
                "skills": list(decision.skills),
                "reason": decision.reason,
            },
        )
        response = await self._run_decision(
            decision, message, conversation_id, request_id
        )
        await self._complete_request(response, decision, conversation_id, started)
        return response

    @localized
    async def stream(
        self,
        message: str,
        conversation_id: str,
        request_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        decision = await self._begin_request(message, conversation_id, request_id)
        started = time.perf_counter()
        yield {"type": "request", "request_id": request_id}
        yield {"type": "route", "route": decision.public_dict()}
        if decision.mode != RouteMode.SIMPLE:
            response = await self._run_decision(
                decision, message, conversation_id, request_id
            )
            await self._complete_request(response, decision, conversation_id, started)
            yield {"type": "complete", "response": response.model_dump(mode="json")}
            return

        history, persist = await self._history_before_user(message, conversation_id)
        context = AgentContext(
            request_id=request_id,
            conversation_id=conversation_id,
            history=history,
        )
        final: AgentResult | None = None
        try:
            async for event in self.simple.stream(message, context):
                if event["type"] == "token":
                    yield {"type": "token", "content": event["content"]}
                elif event["type"] == "complete":
                    final = event["result"]
        except asyncio.CancelledError:
            await self.events.publish(
                "request.cancelled",
                {"request_id": request_id, "agent": decision.agent, "route": decision.mode.value},
            )
            raise
        if final is None:
            raise RuntimeError("O engine encerrou o stream sem resultado final.")
        if persist:
            await self.conversations.add(
                conversation_id, "assistant", final.display_text, persist=True
            )
        response = self._response_from_agent(request_id, decision, final)
        await self._complete_request(response, decision, conversation_id, started)
        yield {"type": "complete", "response": response.model_dump(mode="json")}

    async def _begin_request(
        self, message: str, conversation_id: str, request_id: str
    ) -> RouteDecision:
        await self.events.publish(
            "request.created",
            {"request_id": request_id, "conversation_id": conversation_id},
        )
        decision = self.router.route(message)
        await self.events.publish(
            "request.routed",
            {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "route": decision.mode.value,
                "agent": decision.agent,
                "skills": list(decision.skills),
                "reason": decision.reason,
            },
        )
        for skill in decision.skills:
            await self.events.publish(
                "skill.selected",
                {"request_id": request_id, "skill": skill, "agent": decision.agent},
            )
        return decision

    async def _run_decision(
        self,
        decision: RouteDecision,
        message: str,
        conversation_id: str,
        request_id: str,
    ) -> ChatResponse:
        if decision.mode == RouteMode.DIRECT_ACTION:
            direct_response = await self._run_deterministic_action(
                decision, message, conversation_id, request_id
            )
            if direct_response is not None:
                return direct_response

        if decision.mode == RouteMode.SIMPLE:
            history, persist = await self._history_before_user(message, conversation_id)
            result = await self.simple.run(
                message,
                AgentContext(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    history=history,
                ),
            )
            if persist:
                await self.conversations.add(
                    conversation_id, "assistant", result.display_text, persist=True
                )
            return self._response_from_agent(request_id, decision, result)

        if decision.mode == RouteMode.RESEARCH:
            history, persist = await self._history_before_user(message, conversation_id)
            result = await self.research.run(
                message,
                AgentContext(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    history=history,
                    skills=decision.skills,
                ),
            )
            if persist:
                await self.conversations.add(
                    conversation_id, "assistant", result.display_text, persist=True
                )
            return self._response_from_agent(request_id, decision, result)

        if decision.mode == RouteMode.TASK:
            graph = task_graph_for_open(message, request_id)
            if graph:
                await self.events.publish(
                    "task.created",
                    {
                        "request_id": request_id,
                        "agent": decision.agent,
                        "status": "pending",
                    },
                )
                await execute_open_graph(graph, self.tools, self.events)
                payload = await asyncio.to_thread(self.tasks.save, graph)
                form = form_of_address(self.settings)
                answer = format_open_graph(graph, form_of_address=form, language=get_language(self.settings))
                privacy = self.settings.section("privacy")
                if bool(privacy.get("history_enabled", True)):
                    await self.conversations.add(conversation_id, "user", message, persist=True)
                    await self.conversations.add(conversation_id, "assistant", answer, persist=True)
                return ChatResponse(
                    request_id=request_id,
                    message=answer,
                    speech_text=answer,
                    state=AssistantState.IDLE,
                    route=decision.mode.value,
                    agent=decision.agent,
                    engine="tools",
                    actions=[
                        ActionTrace(
                            tool=node.tool,
                            status=node.status.value,
                            detail=node.detail,
                            action_id=node.id,
                            label=node.label,
                        )
                        for node in graph.actions
                    ],
                    metrics={"task": payload["summary"]},
                )

        if decision.mode == RouteMode.SCHEDULED:
            managed = await asyncio.to_thread(
                self.managed_agents.create,
                {
                    "name": _agent_name(message),
                    "type": "daily" if "todo" in message.casefold() or "toda" in message.casefold() else "monitor",
                    "instruction": message,
                    "skills": list(decision.skills),
                    "schedule": _schedule_hint(message),
                    "active": True,
                },
            )
            answer = tr(
                f"Criei o agente persistente {managed['name']}, senhor. "
                "Revise o horário na página Agentes antes da primeira execução.",
                f"Created persistent agent {managed['name']}, sir. "
                "Review the schedule on the Agents page before its first run."
            )
            return ChatResponse(
                request_id=request_id,
                message=answer,
                speech_text=answer,
                route=decision.mode.value,
                agent=decision.agent,
                engine="scheduler",
                metrics={"managed_agent_id": managed["id"]},
            )

        # The tool executor enforces permissions and verifies results.
        response = await self.tool_orchestrator.run(
            message, conversation_id, request_id
        )
        response.route = decision.mode.value
        response.agent = decision.agent
        response.engine = "ollama/tools"
        response.speech_text = build_speech_text(
            response.message,
            form_of_address=form_of_address(self.settings),
        )
        return response

    async def _run_deterministic_action(
        self,
        decision: RouteDecision,
        message: str,
        conversation_id: str,
        request_id: str,
    ) -> ChatResponse | None:
        persist = bool(self.settings.section("privacy").get("history_enabled", True))
        history = await self.conversations.load(conversation_id) if persist else []
        direct = await try_direct_intent(
            message,
            self.tools,
            self.settings,
            request_id,
            history=history,
        )
        if direct is None:
            return None
        if persist:
            await self.conversations.add(conversation_id, "user", message, persist=True)
            await self.conversations.add(
                conversation_id, "assistant", direct.answer, persist=True
            )
        await self.events.publish(
            "assistant.response", {"request_id": request_id, "message": direct.answer}
        )
        preview = None
        if direct.confirmation_id:
            pending = await self.tools.confirmations.get(direct.confirmation_id)
            preview = pending.preview if pending else None
        actions = direct.actions or [
            ActionTrace(tool=direct.tool, status=direct.status, detail=direct.detail)
        ]
        return ChatResponse(
            request_id=request_id,
            message=direct.answer,
            speech_text=build_speech_text(
                direct.answer,
                form_of_address=form_of_address(self.settings),
            ),
            state=AssistantState.IDLE,
            route=decision.mode.value,
            agent="direct",
            engine="tools",
            actions=actions,
            sources=direct.sources,
            confirmation_id=direct.confirmation_id,
            confirmation_preview=preview,
            metrics={"inference_count": 0},
        )

    async def _history_before_user(
        self, message: str, conversation_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        persist = bool(self.settings.section("privacy").get("history_enabled", True))
        history = await self.conversations.load(conversation_id) if persist else []
        if persist:
            await self.conversations.add(conversation_id, "user", message, persist=True)
        return history, persist

    def _response_from_agent(
        self, request_id: str, decision: RouteDecision, result: AgentResult
    ) -> ChatResponse:
        return ChatResponse(
            request_id=request_id,
            message=result.display_text,
            speech_text=result.speech_text,
            state=AssistantState.IDLE,
            sources=list(result.metadata.get("sources") or []),
            route=decision.mode.value,
            agent=result.agent or decision.agent,
            model=result.model,
            engine=result.engine,
            metrics={
                "prompt_tokens": result.prompt_tokens,
                "output_tokens": result.output_tokens,
                **{
                    key: value
                    for key, value in result.metadata.items()
                    if key not in {"sources"}
                },
            },
        )

    async def _complete_request(
        self,
        response: ChatResponse,
        decision: RouteDecision,
        conversation_id: str,
        started: float,
    ) -> None:
        elapsed = time.perf_counter() - started
        response.metrics.setdefault("duration_ms", round(elapsed * 1000, 3))
        await self.events.publish(
            "response.completed",
            {
                "request_id": response.request_id,
                "conversation_id": conversation_id,
                "route": decision.mode.value,
                "agent": response.agent or decision.agent,
                "model": response.model,
                "engine": response.engine,
                "duration_ms": response.metrics["duration_ms"],
                "ttft_ms": response.metrics.get("ttft_ms"),
                "prompt_tokens": response.metrics.get("prompt_tokens", 0),
                "output_tokens": response.metrics.get("output_tokens", 0),
            },
        )
        await self._record_telemetry(
            response.request_id,
            conversation_id,
            decision,
            started,
            response,
            failed=response.state == AssistantState.ERROR,
        )

    async def _record_telemetry(
        self,
        request_id: str,
        conversation_id: str,
        decision: RouteDecision,
        started: float,
        response: ChatResponse,
        *,
        cancelled: bool = False,
        failed: bool = False,
    ) -> None:
        completed = time.perf_counter()
        output_tokens = int(response.metrics.get("output_tokens") or 0)
        duration_seconds = max(completed - started, 0.000_001)
        record = TelemetryRecord(
            request_id=request_id,
            conversation_id=conversation_id,
            agent=response.agent or decision.agent,
            model=response.model,
            engine=response.engine or self.engine.engine_id,
            started_at=time.time() - duration_seconds,
            completed_at=time.time(),
            ttft_ms=response.metrics.get("ttft_ms"),
            prompt_tokens=int(response.metrics.get("prompt_tokens") or 0),
            output_tokens=output_tokens,
            tokens_per_second=(
                float(response.metrics.get("tokens_per_second"))
                if response.metrics.get("tokens_per_second") is not None
                else (round(output_tokens / duration_seconds, 3) if output_tokens else None)
            ),
            tool_calls=len(response.actions),
            tool_duration_ms=0.0,
            cancelled=cancelled,
            failed=failed,
            metadata={"route": decision.mode.value, "skills": list(decision.skills)},
        )
        await asyncio.to_thread(self.telemetry.record, record)


def _agent_name(message: str) -> str:
    words = message.strip().rstrip(".!?").split()
    return " ".join(words[:7]).capitalize()[:120] or "Agente persistente"


def _schedule_hint(message: str) -> str:
    lower = message.casefold()
    if "toda manhã" in lower or "toda manha" in lower:
        return "daily 08:00"
    if "toda noite" in lower:
        return "daily 20:00"
    if "toda semana" in lower:
        return "weekly"
    return "review_required"


def _strip_schedule(message: str) -> str:
    value = message
    for pattern in (
        r"\b(?:todo dia|todos os dias|toda manhã|toda manha|toda tarde|toda noite)\b",
        r"\b(?:diariamente|semanalmente|toda semana)\b",
        r"\b(?:a cada (?:hora|dia|semana))\b",
        r"\b(?:monitore|monitorar)\b",
    ):
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    return " ".join(value.split()).strip(" ,.-") or message
