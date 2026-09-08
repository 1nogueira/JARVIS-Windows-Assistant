from __future__ import annotations

import ast
import json
import re
from typing import Any


class ToolCallParseError(ValueError):
    pass


def looks_like_tool_call_protocol(content: str) -> bool:
    """Identify JSON tool envelopes regardless of the current allow-list."""
    stripped = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", stripped, flags=re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return False

    def is_call(item: Any) -> bool:
        if isinstance(item, list):
            return bool(item) and all(is_call(entry) for entry in item)
        if not isinstance(item, dict):
            return False
        if isinstance(item.get("tool_calls"), list):
            return is_call(item["tool_calls"])
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name") or function.get("tool")
        arguments = function.get("arguments", function.get("parameters"))
        if not isinstance(name, str) or not name:
            return False
        explicit_protocol = any(
            key in item or key in function
            for key in ("tool", "tool_calls", "function", "arguments", "parameters")
        )
        action_name = bool(
            re.fullmatch(
                r"(?:open|close|set|write|read|create|delete|move|rename|copy|"
                r"empty|shutdown|restart|lock|install|control|capture|browser|"
                r"remember|forget|update|search|diagnose|get|find|list)_[a-z0-9_]+",
                name,
                flags=re.IGNORECASE,
            )
        )
        return (explicit_protocol or action_name) and (
            arguments is None or isinstance(arguments, (dict, str))
        )

    return is_call(value)


def contains_tool_call_protocol(content: str) -> bool:
    """Detect a tool envelope even when surrounded by prose or split fences."""

    if looks_like_tool_call_protocol(content):
        return True
    for fenced in re.finditer(
        r"```(?:json)?\s*([\s\S]*?)```", content, flags=re.IGNORECASE
    ):
        if looks_like_tool_call_protocol(fenced.group(1)):
            return True
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        serialized = json.dumps(value, ensure_ascii=False)
        if looks_like_tool_call_protocol(serialized):
            return True
    return False


def normalize_tool_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize Ollama/OpenAI-shaped native tool calls without evaluating text as code."""
    function = call.get("function", call)
    name = function.get("name")
    arguments = function.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise ToolCallParseError("Tool call sem nome.")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolCallParseError("Argumentos da ferramenta não são JSON válido.") from exc
    if not isinstance(arguments, dict):
        raise ToolCallParseError("Argumentos da ferramenta precisam ser um objeto.")
    return name, arguments


def extract_text_tool_calls(
    content: str,
    tool_parameters: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], str]:
    """Recover allow-listed tool calls emitted as text without evaluating code.

    Some smaller local models occasionally serialize a tool call inside a JSON
    code fence instead of using Ollama's native ``tool_calls`` field. Treat that
    serialization as an internal call, never as chat text.
    """
    calls: list[dict[str, Any]] = []
    without_json_fences = re.sub(
        r"```(?:json)?\s*([\s\S]*?)```",
        lambda match: _consume_json_tool_block(
            match.group(0), match.group(1), tool_parameters, calls
        ),
        content,
        flags=re.IGNORECASE,
    )

    stripped = without_json_fences.strip()
    if stripped.startswith(("{", "[")):
        parsed = _parse_json_tool_value(stripped, tool_parameters)
        if parsed:
            calls.extend(parsed)
            without_json_fences = ""

    retained: list[str] = []
    for original_line in without_json_fences.splitlines():
        candidate = original_line.strip().strip("`").lstrip("-*• ").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*", candidate):
            retained.append(original_line)
            continue
        try:
            expression = ast.parse(candidate, mode="eval").body
        except SyntaxError:
            retained.append(original_line)
            continue
        if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
            retained.append(original_line)
            continue
        name = expression.func.id.casefold()
        canonical = next((item for item in tool_parameters if item.casefold() == name), None)
        if not canonical:
            retained.append(original_line)
            continue
        try:
            positional = [_safe_literal(item) for item in expression.args]
            keywords = {
                item.arg: _safe_literal(item.value)
                for item in expression.keywords
                if item.arg is not None
            }
        except (TypeError, ValueError):
            retained.append(original_line)
            continue
        names = tool_parameters[canonical]
        if len(positional) > len(names):
            retained.append(original_line)
            continue
        arguments = {names[index]: value for index, value in enumerate(positional)}
        arguments.update(keywords)
        calls.append({"function": {"name": canonical, "arguments": arguments}})
    cleaned = "\n".join(retained).strip()
    return calls, cleaned


def _consume_json_tool_block(
    original: str,
    body: str,
    tool_parameters: dict[str, list[str]],
    calls: list[dict[str, Any]],
) -> str:
    parsed = _parse_json_tool_value(body.strip(), tool_parameters)
    if not parsed:
        return original
    calls.extend(parsed)
    return ""


def _parse_json_tool_value(
    raw: str,
    tool_parameters: dict[str, list[str]],
) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return _normalize_json_tool_value(value, tool_parameters)


def _normalize_json_tool_value(
    value: Any,
    tool_parameters: dict[str, list[str]],
) -> list[dict[str, Any]]:
    if isinstance(value, list):
        calls: list[dict[str, Any]] = []
        for item in value:
            calls.extend(_normalize_json_tool_value(item, tool_parameters))
        return calls
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("tool_calls"), list):
        return _normalize_json_tool_value(value["tool_calls"], tool_parameters)

    function = value.get("function") if isinstance(value.get("function"), dict) else value
    name = function.get("name") or function.get("tool")
    if not isinstance(name, str):
        return []
    canonical = next(
        (candidate for candidate in tool_parameters if candidate.casefold() == name.casefold()),
        None,
    )
    if not canonical:
        return []
    arguments = function.get("arguments", function.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return []
    if not isinstance(arguments, dict):
        return []
    return [{"function": {"name": canonical, "arguments": arguments}}]


def _safe_literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id.casefold() in {"true", "false", "null", "none"}:
        return {"true": True, "false": False, "null": None, "none": None}[node.id.casefold()]
    if isinstance(node, ast.List):
        return [_safe_literal(item) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return [_safe_literal(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        return {_safe_literal(key): _safe_literal(value) for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _safe_literal(node.operand)
        if not isinstance(value, (int, float)):
            raise ValueError("Literal numérico inválido.")
        return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("Somente argumentos literais são aceitos.")
