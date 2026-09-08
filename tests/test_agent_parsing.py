from __future__ import annotations

import pytest

from backend.agents.planner import ToolCallParseError, extract_text_tool_calls, normalize_tool_call
from backend.agents.prompts import SYSTEM_PROMPT
from backend.agents.tool_orchestrator import ToolOrchestratorAgent
from backend.core.events import EventBus
from backend.core.logging import JsonlAuditLog
from backend.security.confirmations import ConfirmationManager
from backend.tools.registry import ToolRegistry


def test_native_tool_call_dict_arguments():
    name, arguments = normalize_tool_call({"function": {"name": "web_search", "arguments": {"query": "hoje"}}})
    assert name == "web_search"
    assert arguments == {"query": "hoje"}


def test_string_arguments_are_json_only():
    _, arguments = normalize_tool_call({"name": "x", "arguments": '{"value": 2}'})
    assert arguments["value"] == 2


def test_code_like_arguments_are_never_evaluated():
    with pytest.raises(ToolCallParseError):
        normalize_tool_call({"name": "x", "arguments": "__import__('os').system('whoami')"})


def test_plain_text_tool_calls_are_recovered_without_eval() -> None:
    calls, remaining = extract_text_tool_calls(
        'Abrindo, senhor.\nopen_website("netflix")',
        {"open_website": ["target"]},
    )
    assert calls == [
        {"function": {"name": "open_website", "arguments": {"target": "netflix"}}}
    ]
    assert remaining == "Abrindo, senhor."


def test_json_fenced_tool_call_is_executed_instead_of_displayed() -> None:
    calls, remaining = extract_text_tool_calls(
        'Criei o arquivo.\n```json\n'
        '{"name":"create_and_open_text_file","arguments":{"filename":"nota.txt"}}'
        '\n```\nPronto, senhor.',
        {"create_and_open_text_file": ["filename", "content"]},
    )
    assert calls == [
        {
            "function": {
                "name": "create_and_open_text_file",
                "arguments": {"filename": "nota.txt"},
            }
        }
    ]
    assert "```" not in remaining
    assert "Pronto, senhor." in remaining


def test_untrusted_python_expression_is_not_recovered() -> None:
    calls, remaining = extract_text_tool_calls(
        "open_website(__import__('os').system('whoami'))",
        {"open_website": ["target"]},
    )
    assert calls == []
    assert "__import__" in remaining


def test_prompt_marks_external_content_as_untrusted():
    assert "UNTRUSTED EXTERNAL DATA" in SYSTEM_PROMPT
    assert "PowerShell" in SYSTEM_PROMPT


async def test_brain_executes_json_fallback_before_reporting_success(
    monkeypatch, tmp_path, settings_store
) -> None:
    class FakeOllama:
        def __init__(self) -> None:
            self.turn = 0

        async def generate(self, messages, *, model=None, tools=None):
            self.turn += 1
            if self.turn == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '```json\n{"name":"create_and_open_text_file",'
                            '"arguments":{"filename":"nota.txt"}}\n```'
                        ),
                    }
                }
            return {"message": {"role": "assistant", "content": "Pronto, senhor."}}

    class FakeConversations:
        def __init__(self) -> None:
            self.items = []

        async def load(self, conversation_id):
            return []

        async def add(self, conversation_id, role, content, persist=True):
            self.items.append((role, content))

        async def clear(self, conversation_id):
            return None

    registry = ToolRegistry(
        settings_store,
        EventBus(),
        ConfirmationManager(),
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )
    executed = []

    @registry.tool(
        name="create_and_open_text_file",
        description="cria",
        parameters={
            "type": "object",
            "properties": {"filename": {"type": "string"}},
            "required": ["filename"],
        },
    )
    def create_and_open_text_file(filename):
        executed.append(filename)
        return {
            "success": True,
            "verified": True,
            "error": None,
            "data": {"written": filename, "opened": filename},
            "duration_ms": 0,
            "verification": "synthetic_test_readback",
        }

    async def no_direct_intent(*args, **kwargs):
        return None

    monkeypatch.setattr("backend.agents.tool_orchestrator.try_direct_intent", no_direct_intent)
    orchestrator = ToolOrchestratorAgent(
        settings_store,
        FakeOllama(),  # type: ignore[arg-type]
        registry,
        FakeConversations(),  # type: ignore[arg-type]
        EventBus(),
    )

    response = await orchestrator.run(
        "Materialize um arquivo txt chamado nota.txt", request_id="json-fallback"
    )

    assert executed == ["nota.txt"]
    assert response.actions[0].status == "completed"
    assert "```" not in response.message
