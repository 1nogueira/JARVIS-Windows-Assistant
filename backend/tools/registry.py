from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.core.cancellation import task_manager
from backend.core.config import SettingsStore
from backend.core.events import EventBus
from backend.core.i18n import localized, tr
from backend.tools.labels import tool_category, tool_confirmation, tool_description
from backend.core.logging import JsonlAuditLog
from backend.security.confirmations import ConfirmationManager
from backend.security.permissions import PermissionLevel, PermissionPolicy
from backend.tools.capability_audit import contract_for
from backend.tools.results import explicit_failure, normalize_tool_result, tool_result_outcome


ToolHandler = Callable[..., Awaitable[Any] | Any]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    permission_level: PermissionLevel
    handler: ToolHandler
    category: str = "General"
    enabled: bool = True
    confirmation_text: str | None = None
    tags: list[str] = field(default_factory=list)
    timeout_seconds: float | None = None
    effectful: bool = False
    executor: str = "unclassified"
    verification_method: str = "unclassified"
    audit_covered: bool = False

    def ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": tool_description(self.name, self.description),
                "parameters": self.parameters,
            },
        }

    def public_dict(self, effective_level: PermissionLevel) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": tool_description(self.name, self.description),
            "parameters": self.parameters,
            "permission_level": effective_level.value,
            "category": tool_category(self.category),
            "enabled": self.enabled,
            "tags": self.tags,
            "effectful": self.effectful,
            "executor": self.executor,
            "verification_method": self.verification_method,
            "audit_covered": self.audit_covered,
        }


class ToolError(RuntimeError):
    pass


@dataclass(slots=True)
class ConfirmationRequired(Exception):
    confirmation_id: str
    message: str
    tool_name: str
    preview: dict[str, Any]


class ToolRegistry:
    def __init__(
        self,
        settings: SettingsStore,
        events: EventBus,
        confirmations: ConfirmationManager,
        audit_log: JsonlAuditLog,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.settings = settings
        self.events = events
        self.confirmations = confirmations
        self.audit_log = audit_log
        self.policy = PermissionPolicy(settings)

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        if tool.name in self._tools:
            raise ValueError(f"Ferramenta já registrada: {tool.name}")
        contract = contract_for(tool.name)
        if contract:
            tool.effectful = contract.effectful
            tool.executor = contract.executor
            tool.verification_method = contract.verification_method
            tool.audit_covered = True
        self._tools[tool.name] = tool
        return tool

    def tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        permission_level: PermissionLevel = PermissionLevel.SAFE,
        category: str = "General",
        confirmation_text: str | None = None,
        tags: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Callable[[ToolHandler], ToolHandler]:
        def decorator(handler: ToolHandler) -> ToolHandler:
            self.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    parameters=parameters,
                    permission_level=permission_level,
                    handler=handler,
                    category=category,
                    confirmation_text=confirmation_text,
                    tags=tags or [],
                    timeout_seconds=timeout_seconds,
                )
            )
            return handler

        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_public(self) -> list[dict[str, Any]]:
        return [
            tool.public_dict(self.policy.level_for(tool.name, tool.permission_level))
            for tool in self._tools.values()
        ]

    def capability_matrix(self) -> list[dict[str, Any]]:
        matrix: list[dict[str, Any]] = []
        for tool in self._tools.values():
            effective = self.policy.level_for(tool.name, tool.permission_level)
            matrix.append(
                {
                    "tool": tool.name,
                    "action": tool.description,
                    "risk": effective.value,
                    "confirmation_required": effective is not PermissionLevel.SAFE,
                    "executor": tool.executor,
                    "verification_method": tool.verification_method,
                    "effectful": tool.effectful,
                    "contract": tool.audit_covered,
                    "tested": False,
                    "result": "not_tested",
                }
            )
        return matrix

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.ollama_schema() for tool in self._tools.values() if tool.enabled]

    def schemas_for(self, names: set[str]) -> list[dict[str, Any]]:
        return [
            tool.ollama_schema()
            for tool in self._tools.values()
            if tool.enabled and tool.name in names
        ]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        tool = self._tools.get(name)
        if not tool:
            return False
        tool.enabled = enabled
        return True

    @localized
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str,
        approved: bool = False,
    ) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise ToolError(tr(f"Ferramenta desconhecida: {name}", f"Unknown tool: {name}"))
        if not tool.enabled:
            raise ToolError(tr(f"Ferramenta desabilitada: {name}", f"Tool disabled: {name}"))
        self._validate_arguments(tool.parameters, arguments)

        if not approved and self.policy.requires_confirmation(name, tool.permission_level):
            description = tool_confirmation(name, tool.confirmation_text)
            preview = self._confirmation_preview(tool, arguments)
            pending = await self.confirmations.create(
                name, arguments, description, preview, request_id
            )
            preview = pending.preview
            await self.events.publish(
                "tool.confirmation_required",
                {
                    "request_id": request_id,
                    "tool": name,
                    "confirmation_id": pending.id,
                    "preview": preview,
                },
            )
            raise ConfirmationRequired(pending.id, description, name, preview)

        started = time.perf_counter()
        await self.events.publish("tool.started", {"request_id": request_id, "tool": name})
        token = await task_manager.token_for(request_id)
        handler_task: asyncio.Task[Any] | None = None
        try:
            timeout = tool.timeout_seconds or float(
                self.settings.section("agent").get("tool_timeout_seconds", 45)
            )
            handler_arguments = dict(arguments)
            if "_cancellation_token" in inspect.signature(tool.handler).parameters:
                handler_arguments["_cancellation_token"] = token
            result = tool.handler(**handler_arguments)
            if inspect.isawaitable(result):
                handler_task = asyncio.create_task(result)
                done, _ = await asyncio.wait({handler_task}, timeout=timeout)
                if not done:
                    token.cancel()
                    await self._await_cooperative_stop(handler_task)
                    raise ToolError(
                        tr(f"A ferramenta {name} excedeu o limite de {timeout:g} segundos.", f"Tool {name} exceeded the {timeout:g}-second limit.")
                    )
                result = handler_task.result()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            result = normalize_tool_result(result, duration_ms=elapsed_ms)
            if tool_result_outcome(result) != "completed":
                error = str(result.get("detail") or result.get("error") or tr("Falha estruturada", "Structured failure"))
                self.audit_log.write(
                    "tool.failed", tool=name, error=error, elapsed_ms=elapsed_ms
                )
                await self.events.publish(
                    "tool.failed",
                    {
                        "request_id": request_id,
                        "tool": name,
                        "error": error,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return result
            self.audit_log.write(
                "tool.completed", tool=name, result="ok", elapsed_ms=elapsed_ms
            )
            await self.events.publish(
                "tool.completed",
                {"request_id": request_id, "tool": name, "elapsed_ms": elapsed_ms},
            )
            return result
        except asyncio.CancelledError:
            token.cancel()
            if handler_task and not handler_task.done():
                await self._await_cooperative_stop(handler_task)
            self.audit_log.write("tool.cancelled", tool=name)
            await self.events.publish("tool.cancelled", {"request_id": request_id, "tool": name})
            raise
        except ToolError as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            result = normalize_tool_result(
                explicit_failure(
                    "tool_execution_failed",
                    detail=str(exc),
                    verification="executor_failed_before_postcondition",
                ),
                duration_ms=elapsed_ms,
            )
            self.audit_log.write(
                "tool.failed", tool=name, error=str(exc), elapsed_ms=elapsed_ms
            )
            await self.events.publish(
                "tool.failed", {"request_id": request_id, "tool": name, "error": str(exc)}
            )
            return result
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            self.audit_log.write(
                "tool.failed", tool=name, error=str(exc), elapsed_ms=elapsed_ms
            )
            await self.events.publish(
                "tool.failed", {"request_id": request_id, "tool": name, "error": str(exc)}
            )
            return normalize_tool_result(
                explicit_failure(
                    "tool_execution_failed",
                    detail=str(exc),
                    verification="executor_exception_before_postcondition",
                ),
                duration_ms=elapsed_ms,
            )

    @staticmethod
    async def _await_cooperative_stop(task: asyncio.Task[Any]) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except TimeoutError:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, TimeoutError):
                pass
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except Exception:
            return

    @staticmethod
    def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        try:
            ToolRegistry._validate_schema_value(schema, arguments, "argumentos")
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @staticmethod
    def _validate_schema_value(schema: dict[str, Any], value: Any, path: str) -> None:
        if "oneOf" in schema:
            matches = 0
            for candidate in schema["oneOf"]:
                try:
                    ToolRegistry._validate_schema_value(candidate, value, path)
                    matches += 1
                except ValueError:
                    continue
            if matches != 1:
                raise ValueError(f"{path} não corresponde a uma alternativa válida.")
            return
        if "anyOf" in schema:
            for candidate in schema["anyOf"]:
                try:
                    ToolRegistry._validate_schema_value(candidate, value, path)
                    return
                except ValueError:
                    continue
            raise ValueError(f"{path} não corresponde a um formato válido.")

        expected = schema.get("type")
        valid_type = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "boolean": lambda item: isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if expected:
            expected_types = [expected] if isinstance(expected, str) else list(expected)
            if not any(valid_type.get(kind, lambda _: False)(value) for kind in expected_types):
                readable = " ou ".join(expected_types)
                raise ValueError(f"{path} precisa ser do tipo {readable}.")

        if "enum" in schema and value not in schema["enum"]:
            allowed = ", ".join(repr(item) for item in schema["enum"])
            raise ValueError(f"{path} precisa ser um dos valores: {allowed}.")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise ValueError(f"{path} precisa ser maior ou igual a {schema['minimum']}.")
            if "maximum" in schema and value > schema["maximum"]:
                raise ValueError(f"{path} precisa ser menor ou igual a {schema['maximum']}.")
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise ValueError(f"{path} é curto demais.")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise ValueError(f"{path} é longo demais.")
            if "pattern" in schema and re.fullmatch(str(schema["pattern"]), value) is None:
                raise ValueError(f"{path} possui formato inválido.")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise ValueError(f"{path} precisa ter ao menos {schema['minItems']} item(ns).")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise ValueError(f"{path} pode ter no máximo {schema['maxItems']} item(ns).")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    ToolRegistry._validate_schema_value(item_schema, item, f"{path}[{index}]")
        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(f"Argumentos obrigatórios ausentes em {path}: {', '.join(missing)}")
            properties = schema.get("properties")
            if properties is not None:
                unknown = set(value) - set(properties)
                if unknown and schema.get("additionalProperties", False) is not True:
                    raise ValueError(
                        f"Argumentos não reconhecidos em {path}: {', '.join(sorted(unknown))}"
                    )
                for name, item in value.items():
                    if name in properties:
                        ToolRegistry._validate_schema_value(
                            properties[name], item, f"{path}.{name}"
                        )

    @staticmethod
    def _confirmation_preview(
        tool: ToolDefinition, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        labels = {
            "path": tr("Caminho", "Path"),
            "source": tr("Origem", "Source"),
            "destination": tr("Destino", "Destination"),
            "contact": tr("Contato", "Contact"),
            "message": tr("Mensagem", "Message"),
            "send": tr("Enviar agora", "Send now"),
            "pid": "PID",
            "process_name": tr("Processo", "Process"),
            "package_id": tr("Pacote", "Package"),
            "target": tr("Destino/URL", "Target/URL"),
            "url": "URL",
            "selector": tr("Seletor", "Selector"),
            "query": tr("Consulta", "Query"),
            "text": tr("Texto", "Text"),
            "content": tr("Conteúdo", "Content"),
            "level": tr("Nível", "Level"),
            "muted": tr("Mudo", "Muted"),
            "page": tr("Página", "Page"),
        }
        details: list[dict[str, str]] = []
        if tool.name == "desktop_automation":
            actions = arguments.get("actions") or []
            kinds = [str(item.get("type", "ação")) for item in actions if isinstance(item, dict)]
            details.append({"label": tr("Etapas", "Steps"), "value": str(len(actions))})
            details.append({"label": tr("Sequência", "Sequence"), "value": ", ".join(kinds[:12])})
        else:
            for key, value in arguments.items():
                if key.casefold() in {
                    "password",
                    "secret",
                    "token",
                    "authorization",
                    "cookie",
                    "api_key",
                    "access_key",
                    "private_key",
                    "session",
                    "session_id",
                }:
                    continue
                rendered = ToolRegistry._safe_preview_value(value)
                details.append(
                    {
                        "label": labels.get(key, key.replace("_", " ").title()),
                        "value": rendered,
                    }
                )
        summary = details[0]["value"] if details else tool_description(tool.name, tool.description)
        return {
            "title": tool.name.replace("_", " ").title(),
            "summary": summary[:240],
            "details": details[:12],
        }

    @staticmethod
    def _safe_preview_value(value: Any) -> str:
        if isinstance(value, bool):
            return tr("Sim", "Yes") if value else tr("Não", "No")
        if isinstance(value, dict):
            sanitized = {
                key: (
                    "[REDACTED]"
                    if key.casefold()
                    in {
                        "password",
                        "secret",
                        "token",
                        "authorization",
                        "cookie",
                        "api_key",
                        "access_key",
                        "private_key",
                        "session",
                        "session_id",
                    }
                    else ToolRegistry._safe_preview_value(item)
                )
                for key, item in value.items()
            }
            text = str(sanitized)
        elif isinstance(value, list):
            text = str([ToolRegistry._safe_preview_value(item) for item in value])
        else:
            text = str(value)
        if (
            "-----BEGIN" in text
            or re.search(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", text, re.IGNORECASE)
            or re.search(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", text)
            or re.search(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}\b", text)
        ):
            return "[REDACTED]"
        return text[:240] + ("…" if len(text) > 240 else "")

    @localized
    async def execute_confirmation(self, confirmation_id: str, approved: bool) -> Any:
        if not approved:
            rejected = await self.confirmations.reject(confirmation_id)
            if not rejected:
                raise ToolError(tr("Confirmação inválida ou expirada.", "Invalid or expired confirmation."))
            return {"approved": False, "message": tr("Ação cancelada pelo usuário.", "Action cancelled by the user.")}
        action = await self.confirmations.consume(confirmation_id)
        if not action:
            raise ToolError(tr("Confirmação inválida ou expirada.", "Invalid or expired confirmation."))
        result = await self.execute(
            action.tool_name,
            action.arguments,
            request_id=action.request_id,
            approved=True,
        )
        return {"approved": True, "tool": action.tool_name, "result": result}
