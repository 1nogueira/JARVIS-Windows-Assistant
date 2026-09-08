from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from backend.tasks import ActionNode, ActionStatus, TaskGraph
from backend.core.events import EventBus
from backend.tools.browser import SITE_ALIASES
from backend.tools.registry import ToolRegistry
from backend.tools.results import tool_result_outcome


@dataclass(frozen=True, slots=True)
class OpenTarget:
    label: str
    tool: str
    arguments: dict[str, Any]


def parse_open_targets(message: str) -> list[OpenTarget]:
    """Parse explicit open commands, treating browser/app phrases as modifiers."""
    normalized = _normalize(message)
    match = re.search(
        r"\b(?:abra|abre|abrir|inicie|iniciar|execute|executar|open|launch|start)\b\s+(.+)$",
        normalized,
    )
    if not match:
        return []
    raw_match = re.search(
        r"\b(?:abra|abre|abrir|inicie|iniciar|execute|executar|open|launch|start)\b\s+(.+)$",
        message,
        flags=re.IGNORECASE,
    )
    body = raw_match.group(1) if raw_match else match.group(1)
    body = re.split(r"[.!?](?:\s|$)", body, maxsplit=1)[0]
    global_browser = bool(
        re.search(
            r"\b(?:no navegador|no browser|na web|pelo navegador|no meu navegador|no seu navegador|pelo meu navegador|pelo seu navegador)\s*$",
            _normalize(body),
        )
    )
    if global_browser:
        body = re.sub(
            r"\s+\b(?:no navegador|no browser|na web|pelo navegador|no meu navegador|no seu navegador|pelo meu navegador|pelo seu navegador)\s*$",
            "",
            body,
            flags=re.IGNORECASE,
        )
    rough_parts = re.split(
        r"\s*,\s*|\s+(?:e|and)\s+(?=(?:o |a |meu |minha |um |uma |the |my |a |an )?[A-Za-zÀ-ÿ])",
        body,
        flags=re.IGNORECASE,
    )
    parts: list[str] = []
    for part in rough_parts:
        cleaned = _clean_target(part)
        if not cleaned:
            continue
        expanded = _explode_known_targets(cleaned)
        parts.extend(expanded or [cleaned])
    if not parts:
        return []
    targets = [_classify_target(part, force_browser=global_browser) for part in parts]
    return [target for target in targets if target is not None]


def task_graph_for_open(message: str, request_id: str) -> TaskGraph | None:
    if re.search(
        r"\b(?:pesquise|pesquisar|procure|procurar|busque|buscar|search|find)\b",
        _normalize(message),
    ):
        return None
    targets = parse_open_targets(message)
    if len(targets) < 2:
        return None
    return TaskGraph(
        request_id=request_id,
        title="Abrir aplicativos e sites",
        actions=[
            ActionNode(label=item.label, tool=item.tool, arguments=item.arguments)
            for item in targets
        ],
    )


async def execute_open_graph(
    graph: TaskGraph,
    registry: ToolRegistry,
    events: EventBus | None = None,
) -> TaskGraph:
    async def runner(node: ActionNode) -> Any:
        if events:
            await events.publish(
                "tool.started",
                {
                    "request_id": graph.request_id,
                    "action_id": node.id,
                    "tool": node.tool,
                    "label": node.label,
                },
            )
        try:
            result = await registry.execute(
                node.tool, node.arguments, request_id=graph.request_id
            )
        except Exception:
            if events:
                await events.publish(
                    "tool.failed",
                    {
                        "request_id": graph.request_id,
                        "action_id": node.id,
                        "tool": node.tool,
                        "label": node.label,
                    },
                )
            raise
        if events:
            outcome = tool_result_outcome(result)
            await events.publish(
                "tool.completed" if outcome == "completed" else "tool.failed",
                {
                    "request_id": graph.request_id,
                    "action_id": node.id,
                    "tool": node.tool,
                    "label": node.label,
                    "detail": result.get("detail") if isinstance(result, dict) else None,
                },
            )
        return result

    await graph.execute(runner, concurrent=True)
    return graph


def format_open_graph(graph: TaskGraph, *, form_of_address: str = "senhor", language: str | None = None) -> str:
    english = language == "en-US"
    completed = [node for node in graph.actions if node.status == ActionStatus.COMPLETED]
    needs_input = [node for node in graph.actions if node.status == ActionStatus.NEEDS_INPUT]
    failed = [
        node for node in graph.actions
        if node.status not in {ActionStatus.COMPLETED, ActionStatus.NEEDS_INPUT}
    ]
    parts: list[str] = []
    if completed:
        parts.append(f"{'Opened' if english else 'Abri'} {_natural_join([node.label for node in completed], english=english)}")
    if failed:
        descriptions = [f"{node.label} ({node.detail or node.status.value})" for node in failed]
        parts.append(f"{'Could not open' if english else 'Não consegui abrir'} {_natural_join(descriptions, english=english)}")
    if needs_input:
        descriptions = [f"{node.label} ({node.detail})" for node in needs_input]
        parts.append(f"{'Please choose' if english else 'Preciso que escolha'} {_natural_join(descriptions, english=english)}")
    if not parts:
        parts.append("No opening was confirmed" if english else "Nenhuma abertura foi confirmada")
    title = "sir" if english and form_of_address == "senhor" else form_of_address
    return ". ".join(parts).rstrip(".") + f", {title}."


def classify_open_target(value: str, *, force_browser: bool = False) -> OpenTarget | None:
    return _classify_target(value, force_browser=force_browser)


def _classify_target(value: str, *, force_browser: bool = False) -> OpenTarget | None:
    normalized = _normalize(value)
    if normalized in {
        "no navegador", "no browser", "na web", "pelo navegador", "no meu navegador",
        "no seu navegador", "pelo meu navegador", "pelo seu navegador", "como aplicativo",
        "o aplicativo", "aplicativo",
    }:
        return None
    local_browser = bool(
        re.search(
            r"\b(?:no navegador|no browser|na web|pelo navegador|no meu navegador|no seu navegador|pelo meu navegador|pelo seu navegador)\b",
            normalized,
        )
    )
    force_app = bool(
        re.search(
            r"\b(?:aplicativo|app)(?:\s+(?:do|da|de))?\b|\bcomo aplicativo\b",
            normalized,
        )
    )
    normalized = re.sub(
        r"\b(?:no navegador|no browser|na web|pelo navegador|no meu navegador|no seu navegador|pelo meu navegador|pelo seu navegador|como aplicativo)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"\b(?:o\s+)?(?:aplicativo|app)(?:\s+(?:do|da|de))?\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    if normalized in {"camera", "camera do windows", "microsoft windows camera"}:
        return OpenTarget("Camera", "open_camera", {})
    if normalized in {"whatsapp web", "web whatsapp"}:
        return OpenTarget("WhatsApp Web", "open_website", {"target": "whatsapp"})
    if normalized == "whatsapp" and not (force_browser or local_browser):
        return OpenTarget("WhatsApp", "open_app", {"query": "WhatsApp"})
    site_key = normalized
    if site_key in SITE_ALIASES and (not force_app or force_browser or local_browser):
        labels = {
            "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
            "whatsapp": "WhatsApp Web", "google": "Google", "gmail": "Gmail",
            "netflix": "Netflix", "facebook": "Facebook", "spotify": "Spotify",
        }
        return OpenTarget(labels.get(site_key, value), "open_website", {"target": site_key})
    if re.fullmatch(r"https?://\S+|[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?", normalized):
        return OpenTarget(value, "open_website", {"target": value})
    aliases = {
        "visual studio code": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "vscode": "Visual Studio Code",
        "microsoft edge": "Microsoft Edge",
        "edge": "Microsoft Edge",
        "epic": "Epic Games Launcher",
        "epic games": "Epic Games Launcher",
        "discord": "Discord",
        "whatsapp": "WhatsApp",
        "tiktok": "TikTok",
        "launcher": "launcher",
    }
    query = aliases.get(normalized, normalized if force_app else value)
    label = {
        "visual studio code": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "vscode": "Visual Studio Code",
        "discord": "Discord",
        "tiktok": "TikTok",
    }.get(normalized, value)
    return OpenTarget(label, "open_app", {"query": query})


def _clean_target(value: str) -> str:
    value = value.strip(" \t\r\n\"'.,;:!?")
    value = re.sub(
        r"\s+(?:a[ií]|pra mim|para mim|por favor|por gentileza|faz favor|please|for me|thanks)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^(?:o|a|os|as|um|uma|meu|minha|the|my|an)\s+", "", value, flags=re.IGNORECASE)
    return value.strip()


def _explode_known_targets(value: str) -> list[str]:
    """Split omitted-comma lists such as ``youtube instagram e tiktok``."""
    remaining = _normalize(value)
    known = sorted(
        {
            *SITE_ALIASES,
            "camera", "camera do windows", "microsoft windows camera",
            "visual studio code", "vs code", "vscode", "discord", "whatsapp web",
            "microsoft edge", "edge", "epic games launcher", "epic games", "launcher",
        },
        key=lambda item: (len(item.split()), len(item)),
        reverse=True,
    )
    found: list[str] = []
    while remaining:
        remaining = re.sub(r"^(?:o|a|os|as|um|uma|meu|minha|the|my|an)\s+", "", remaining)
        match = next(
            (
                item for item in known
                if remaining == item or remaining.startswith(item + " ")
            ),
            None,
        )
        if not match:
            return []
        found.append(match)
        remaining = remaining[len(match):].strip()
    return found if len(found) > 1 else []


def _normalize(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode(),
    ).strip(" .?!")


def _natural_join(items: list[str], *, english: bool = False) -> str:
    if len(items) < 2:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + (f" and {items[-1]}" if english else f" e {items[-1]}")
