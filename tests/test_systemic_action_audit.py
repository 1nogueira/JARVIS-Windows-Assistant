from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.agents.planner import contains_tool_call_protocol
from backend.agents.simple import sanitize_display_text
from backend.api.app import services
from backend.core.events import EventBus
from backend.core.logging import JsonlAuditLog
from backend.responses import response_from_tool_result
from backend.security.action_scope import validate_effect_scope
from backend.security.confirmations import ConfirmationManager
from backend.security.permissions import PermissionLevel
from backend.tools.capability_audit import CAPABILITY_CONTRACTS
from backend.tools.browser import BrowserController
from backend.tools.registry import ConfirmationRequired, ToolError, ToolRegistry
from backend.tools.results import explicit_success, normalize_tool_result
from backend.tools.windows import fixed_power_action


def test_capability_inventory_is_exhaustive_and_classified() -> None:
    public = services.tools.registry.list_public()
    registered = {item["name"] for item in public}

    assert len(registered) == 64
    assert registered == set(CAPABILITY_CONTRACTS)
    assert all(item["audit_covered"] for item in public)
    assert all(item["executor"] != "unclassified" for item in public)
    assert all(item["verification_method"] != "unclassified" for item in public)
    assert {"delete_file", "move_to_recycle_bin", "empty_recycle_bin"} <= registered


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "ok",
        {"success": True},
        {"success": True, "verified": False, "error": None},
        {"verified": True, "error": None},
    ],
)
def test_incomplete_or_unverified_tool_results_never_become_success(raw) -> None:
    result = normalize_tool_result(raw)
    assert result["success"] is False
    assert result["verified"] is False
    assert {"success", "verified", "error", "data", "duration_ms"} <= set(result)


def test_effect_scope_rejects_unrequested_target_and_unrelated_power_action() -> None:
    assert validate_effect_scope(
        "abre o discord", "open_app", {"query": "Discord"}
    )[0]
    assert not validate_effect_scope(
        "abre o discord", "open_app", {"query": "Visual Studio Code"}
    )[0]
    assert not validate_effect_scope(
        "abre o discord", "shutdown_computer", {}
    )[0]
    assert validate_effect_scope(
        "esvazie a lixeira da unidade D:", "empty_recycle_bin", {"drive": "D:"}
    )[0]


def test_tool_protocol_is_blocked_when_name_only_or_prefixed_by_prose() -> None:
    name_only = '{"name":"shutdown_computer"}'
    prefixed = f"Claro, senhor. Aqui vai:\n```json\n{name_only}\n```"

    assert contains_tool_call_protocol(name_only)
    assert contains_tool_call_protocol(prefixed)
    visible = sanitize_display_text(prefixed)
    assert "shutdown_computer" not in visible
    assert "Bloqueei uma chamada" in visible


@pytest.mark.asyncio
async def test_confirmation_fingerprint_rejects_internal_argument_tampering(
    settings_store, tmp_path: Path
) -> None:
    confirmations = ConfirmationManager()
    registry = ToolRegistry(
        settings_store,
        EventBus(),
        confirmations,
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )
    calls: list[str] = []

    @registry.tool(
        name="tamper_test",
        description="teste",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
        permission_level=PermissionLevel.CONFIRM,
    )
    def tamper_test(target: str):
        calls.append(target)
        return explicit_success({"target": target}, verification="test_readback")

    with pytest.raises(ConfirmationRequired) as pending:
        await registry.execute(
            "tamper_test", {"target": "original"}, request_id="tamper"
        )
    confirmations._pending[pending.value.confirmation_id].arguments["target"] = "changed"

    with pytest.raises(ToolError, match="inválida|expirada"):
        await registry.execute_confirmation(pending.value.confirmation_id, True)
    assert calls == []


def test_safe_power_smoke_never_invokes_a_windows_power_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("backend.tools.windows._require_windows", lambda: None)
    monkeypatch.setattr("backend.tools.windows.subprocess.run", run)
    monkeypatch.setenv("JARVIS_SAFE_POWER_SMOKE", "1")

    result = fixed_power_action("shutdown")

    assert result["success"] is False and result["verified"] is False
    assert result["error"] == "automated_power_smoke_disabled"
    assert result["data"]["executed"] is False
    assert calls == []


def test_failure_response_never_claims_external_success() -> None:
    failed = {
        "success": False,
        "verified": False,
        "error": "launch_not_verified",
        "data": {},
        "duration_ms": 1,
        "detail": "nenhuma janela apareceu",
    }
    response = response_from_tool_result("open_app", failed)
    assert response.startswith("Não consegui")
    assert "Abri" not in response


def test_production_backend_has_no_legacy_orchestrator_dependency() -> None:
    root = Path(__file__).resolve().parents[1] / "backend"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert "legacy_orchestrator" not in sources


@pytest.mark.asyncio
async def test_browser_search_falls_back_when_local_searxng_is_offline(
    settings_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_store.update({"search": {"searxng_url": "http://127.0.0.1:1"}})
    browser = BrowserController(settings_store)
    calls: list[str] = []

    async def navigate(url: str):
        calls.append(url)
        if url.startswith("http://127.0.0.1:1"):
            raise RuntimeError("offline")
        return explicit_success({"url": url}, verification="test_url_readback")

    monkeypatch.setattr(browser, "navigate", navigate)
    result = await browser.search("jarvis smoke")

    assert result["success"] is True
    assert calls[0].startswith("http://127.0.0.1:1/")
    assert calls[1].startswith("https://www.google.com/search?")
