from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.core.events import EventBus
from backend.core.cancellation import task_manager
from backend.core.logging import JsonlAuditLog
from backend.security.confirmations import ConfirmationManager
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ConfirmationRequired, ToolError, ToolRegistry
from backend.tools.results import explicit_success


@pytest.fixture
def registry(settings_store, tmp_path: Path) -> ToolRegistry:
    return ToolRegistry(
        settings_store,
        EventBus(),
        ConfirmationManager(),
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )


@pytest.mark.asyncio
async def test_safe_tool_executes(registry):
    @registry.tool(
        name="sum", description="soma", parameters={"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a", "b"]}
    )
    def add(a, b):
        return explicit_success({"value": a + b}, verification="sum_recomputed")

    result = await registry.execute("sum", {"a": 2, "b": 4}, request_id="x")
    assert result["success"] is True
    assert result["verified"] is True
    assert result["error"] is None
    assert result["value"] == 6
    assert result["data"] == {"value": 6}
    assert isinstance(result["duration_ms"], float)


@pytest.mark.asyncio
async def test_tool_without_explicit_success_is_a_structured_failure(registry):
    @registry.tool(name="legacy", description="legacy", parameters={"type": "object", "properties": {}})
    def legacy():
        return {"value": 1}

    result = await registry.execute("legacy", {}, request_id="missing-success")

    assert result["success"] is False
    assert result["error"] == "invalid_tool_result"


@pytest.mark.asyncio
async def test_confirm_tool_waits_and_then_executes(registry):
    called = []

    @registry.tool(
        name="change", description="muda", parameters={"type": "object", "properties": {"value": {}}, "required": ["value"]},
        permission_level=PermissionLevel.CONFIRM,
    )
    def change(value):
        called.append(value)
        return explicit_success({"value": value}, verification="test_readback")

    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute("change", {"value": 7}, request_id="x")
    assert called == []
    result = await registry.execute_confirmation(pending.value.confirmation_id, True)
    assert result["approved"] is True
    assert called == [7]


@pytest.mark.asyncio
async def test_rejected_confirmation_never_calls_tool(registry):
    called = []

    @registry.tool(name="danger", description="x", parameters={"type": "object", "properties": {}}, permission_level=PermissionLevel.RESTRICTED)
    def danger():
        called.append(True)

    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute("danger", {}, request_id="x")
    result = await registry.execute_confirmation(pending.value.confirmation_id, False)
    assert result["approved"] is False
    assert called == []


@pytest.mark.asyncio
async def test_unknown_arguments_are_rejected(registry):
    @registry.tool(name="empty", description="x", parameters={"type": "object", "properties": {}})
    def empty():
        return True

    with pytest.raises(ToolError, match="não reconhecidos"):
        await registry.execute("empty", {"unexpected": 1}, request_id="x")


@pytest.mark.asyncio
async def test_disabled_tool_is_not_executed(registry):
    @registry.tool(name="off", description="x", parameters={"type": "object", "properties": {}})
    def off():
        return True

    registry.set_enabled("off", False)
    with pytest.raises(ToolError, match="desabilitada"):
        await registry.execute("off", {}, request_id="x")


def test_strict_schema_validation_rejects_wrong_types_and_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {
            "send": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["safe", "fast"]},
            "level": {"type": "integer", "minimum": 0, "maximum": 10},
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": 2,
            },
        },
        "required": ["send", "mode", "level", "items"],
    }
    for invalid in (
        {"send": "false", "mode": "safe", "level": 1, "items": [1]},
        {"send": False, "mode": "invalid", "level": 1, "items": [1]},
        {"send": False, "mode": "safe", "level": 11, "items": [1]},
        {"send": False, "mode": "safe", "level": 1, "items": []},
        {"send": False, "mode": "safe", "level": 1, "items": [1, 2, 3]},
        {"send": False, "mode": "safe", "level": 1, "items": ["1"]},
    ):
        with pytest.raises(ToolError):
            ToolRegistry._validate_arguments(schema, invalid)

    valid = {"send": False, "mode": "safe", "level": 1, "items": [1]}
    ToolRegistry._validate_arguments(schema, valid)
    assert valid["send"] is False


@pytest.mark.asyncio
async def test_confirmation_previews_validated_arguments_and_revalidates(registry):
    called = []

    @registry.tool(
        name="whatsapp_message",
        description="mensagem",
        parameters={
            "type": "object",
            "properties": {
                "contact": {"type": "string"},
                "message": {"type": "string"},
                "send": {"type": "boolean"},
            },
            "required": ["contact", "message", "send"],
        },
        permission_level=PermissionLevel.CONFIRM,
    )
    def send(contact, message, send):
        called.append((contact, message, send))

    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute(
            "whatsapp_message",
            {"contact": "mãe", "message": "Cheguei", "send": False},
            request_id="preview",
        )
    details = {item["label"]: item["value"] for item in pending.value.preview["details"]}
    assert details["Contato"] == "mãe"
    assert details["Mensagem"] == "Cheguei"
    assert details["Enviar agora"] == "Não"

    tool = registry.get("whatsapp_message")
    assert tool is not None
    tool.parameters["properties"]["send"]["enum"] = [True]
    with pytest.raises(ToolError):
        await registry.execute_confirmation(pending.value.confirmation_id, True)
    assert called == []


@pytest.mark.asyncio
async def test_confirmation_executes_exact_previewed_copy_and_redacts_nested_secrets(
    registry,
):
    called: list[tuple[str, dict[str, str]]] = []

    @registry.tool(
        name="delete_file",
        description="remove arquivo",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string"},
                        "token": {"type": "string"},
                    },
                },
            },
            "required": ["path", "metadata"],
        },
        permission_level=PermissionLevel.CONFIRM,
    )
    def delete_file(path, metadata):
        called.append((path, metadata))
        return explicit_success({"deleted": path}, verification="test_origin_absent")

    arguments = {
        "path": r"C:\dados\exato.txt",
        "metadata": {"note": "teste", "token": "synthetic-secret-value"},
    }
    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute("delete_file", arguments, request_id="preview-copy")

    details = {item["label"]: item["value"] for item in pending.value.preview["details"]}
    preview_text = str(pending.value.preview)
    assert details["Caminho"] == r"C:\dados\exato.txt"
    assert "synthetic-secret-value" not in preview_text
    assert "[REDACTED]" in preview_text

    arguments["path"] = r"C:\dados\alterado.txt"
    arguments["metadata"]["note"] = "alterado"
    await registry.execute_confirmation(pending.value.confirmation_id, True)
    assert called == [
        (
            r"C:\dados\exato.txt",
            {"note": "teste", "token": "synthetic-secret-value"},
        )
    ]


@pytest.mark.asyncio
async def test_timeout_stops_cooperative_thread_before_later_effects(registry):
    effects: list[int] = []

    @registry.tool(
        name="slow_sequence",
        description="sequência",
        parameters={"type": "object", "properties": {}},
        timeout_seconds=0.07,
    )
    async def slow_sequence(_cancellation_token=None):
        def worker():
            for index in range(20):
                _cancellation_token.raise_if_cancelled()
                effects.append(index)
                _cancellation_token.wait(0.025)
            return True

        return await asyncio.to_thread(worker)

    result = await registry.execute("slow_sequence", {}, request_id="timeout-sequence")
    assert result["success"] is False
    assert result["error"] == "tool_execution_failed"
    assert "excedeu" in result["detail"]
    stopped_at = len(effects)
    await asyncio.sleep(0.1)
    assert len(effects) == stopped_at
    assert stopped_at < 20


@pytest.mark.asyncio
async def test_stop_propagates_to_cooperative_thread(registry):
    effects: list[int] = []

    @registry.tool(
        name="stoppable_sequence",
        description="sequência",
        parameters={"type": "object", "properties": {}},
        timeout_seconds=5,
    )
    async def stoppable_sequence(_cancellation_token=None):
        def worker():
            for index in range(30):
                _cancellation_token.raise_if_cancelled()
                effects.append(index)
                _cancellation_token.wait(0.02)

        await asyncio.to_thread(worker)

    task = asyncio.create_task(
        registry.execute("stoppable_sequence", {}, request_id="stop-sequence")
    )
    await task_manager.track("stop-sequence", task)
    await asyncio.sleep(0.06)
    assert await task_manager.cancel("stop-sequence") == 1
    with pytest.raises(asyncio.CancelledError):
        await task
    stopped_at = len(effects)
    await asyncio.sleep(0.1)
    assert len(effects) == stopped_at
    assert stopped_at < 30
