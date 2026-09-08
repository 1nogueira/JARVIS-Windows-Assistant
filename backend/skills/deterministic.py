"""Execute unambiguous requests through the tool registry."""

from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from backend.core.artifacts import latest_artifact
from backend.core.config import SettingsStore
from backend.core.i18n import form_of_address, language_context, tr
from backend.core.models import ActionTrace
from backend.responses import response_from_tool_result
from backend.tools.files import resolve_named_file
from backend.tools.registry import ConfirmationRequired, ToolError, ToolRegistry
from backend.tools.results import tool_result_detail, tool_result_outcome
from backend.tools.weather import canonicalize_location
from backend.voice.wakeword import special_wake_phrase_matches
from backend.skills.windows_open import (
    classify_open_target,
    execute_open_graph,
    format_open_graph,
    parse_open_targets,
    task_graph_for_open,
)


@dataclass(slots=True)
class DirectIntentResult:
    answer: str
    tool: str
    status: str = "completed"
    sources: list[dict[str, Any]] = field(default_factory=list)
    confirmation_id: str | None = None
    detail: str = field(default_factory=lambda: tr("Concluído", "Completed"))
    actions: list[ActionTrace] = field(default_factory=list)


WEEKDAYS = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}

_address: ContextVar[str | None] = ContextVar("direct_intent_address", default=None)


def _title() -> str:
    return _address.get() or tr("senhor", "sir")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", normalized).strip(" .?!")


async def try_direct_intent(
    message: str,
    registry: ToolRegistry,
    settings: SettingsStore,
    request_id: str,
    history: list[dict[str, Any]] | None = None,
) -> DirectIntentResult | None:
    """Keep locale and permissions consistent for every direct-intent branch."""
    with language_context(settings):
        token = _address.set(form_of_address(settings))
        try:
            return await _try_direct_intent_with_confirmation(
                message, registry, settings, request_id, history
            )
        finally:
            _address.reset(token)


async def _try_direct_intent_with_confirmation(
    message: str,
    registry: ToolRegistry,
    settings: SettingsStore,
    request_id: str,
    history: list[dict[str, Any]] | None,
) -> DirectIntentResult | None:
    try:
        return await _try_direct_intent(
            message, registry, settings, request_id, history=history or []
        )
    except ConfirmationRequired as pending:
        return DirectIntentResult(
            answer=pending.message,
            tool=pending.tool_name,
            status="awaiting_confirmation",
            confirmation_id=pending.confirmation_id,
            detail=pending.message,
        )


async def _try_direct_intent(
    message: str,
    registry: ToolRegistry,
    settings: SettingsStore,
    request_id: str,
    *,
    history: list[dict[str, Any]],
) -> DirectIntentResult | None:
    """Execute only unambiguous, common intents; the agent handles everything else."""
    normalized = normalize_text(message)
    normalized = re.sub(r"^jarvis[, ]+", "", normalized)
    if re.search(r"\b(?:don't|do not|never)\b", normalized):
        return None

    power_action = _parse_power_action(normalized)
    if power_action:
        return await _execute_confirmable(
            registry, f"{power_action}_computer", {}, "", request_id
        )

    if _requests_empty_recycle_bin(normalized):
        recycle_drive = _parse_recycle_bin_drive(message)
        return await _execute_confirmable(
            registry,
            "empty_recycle_bin",
            {"drive": recycle_drive} if recycle_drive else {},
            "",
            request_id,
        )

    audio_action = _parse_windows_audio_action(normalized)
    if audio_action:
        tool, arguments = audio_action
        return await _execute_confirmable(
            registry, tool, arguments, "", request_id
        )

    close_target = _parse_close_process_target(normalized)
    if close_target:
        pid = _resolve_process_pid(close_target)
        if pid is None:
            return DirectIntentResult(
                answer=tr(f"Não encontrei {close_target} em execução, {_title()}.", f"I could not find {close_target} running, {_title()}."),
                tool="close_process",
                status="failed",
                detail="process_not_found",
            )
        return await _execute_confirmable(
            registry, "close_process", {"pid": pid}, "", request_id
        )

    special_phrase = str(
        settings.section("voice").get(
            "special_wake_phrase", "acorda criança o papai chegou"
        )
    )
    if special_wake_phrase_matches(message, special_phrase):
        try:
            music_result = await registry.execute(
                "control_theme_music", {"action": "play"}, request_id=request_id
            )
        except ToolError as exc:
            return DirectIntentResult(
                answer=tr(f"Não consegui tocar a música especial, {_title()}. {exc}", f"I could not play the theme music, {_title()}. {exc}"),
                tool="control_theme_music",
                status="failed",
                detail=str(exc),
            )
        if tool_result_outcome(music_result) != "completed":
            return DirectIntentResult(
                answer=response_from_tool_result("control_theme_music", music_result),
                tool="control_theme_music",
                status="failed",
                detail=tool_result_detail(music_result),
            )
        greeting = str(
            settings.section("voice").get(
                "special_wake_greeting",
                tr("Bem-vindo, senhor. Como estão as coisas? Quais projetos temos para hoje?", "Welcome, sir. How are things? What are we working on today?"),
            )
        )
        return DirectIntentResult(answer=greeting, tool="control_theme_music")

    open_graph = task_graph_for_open(message, request_id)
    if open_graph and not re.search(
        r"\b(?:pesquise|pesquisar|procure|procurar|busque|buscar|search|find)\b", normalized
    ):
        await execute_open_graph(open_graph, registry)
        form = form_of_address(settings)
        traces = [
            ActionTrace(
                tool=node.tool,
                status=node.status.value,
                detail=node.detail,
                action_id=node.id,
                label=node.label,
            )
            for node in open_graph.actions
        ]
        return DirectIntentResult(
            answer=format_open_graph(open_graph, form_of_address=form),
            tool="multiple_actions",
            status="completed" if all(node.status.value == "completed" for node in open_graph.actions) else "failed",
            detail=tr(f"{len(open_graph.actions)} ação(ões) processada(s)", f"{len(open_graph.actions)} action(s) processed"),
            actions=traces,
        )

    single_open_targets = parse_open_targets(message)
    if len(single_open_targets) == 1 and not re.search(
        r"\b(?:conversa|arquivo|pasta|linha|anonima|privada|inprivate|incognito|canal|ele|isso|relatorio|conversation|file|folder|line|private|channel|report)\b",
        normalized,
    ):
        target = single_open_targets[0]
        return await _execute_open(
            registry,
            target.tool,
            target.arguments,
            target.label,
            request_id,
        )

    open_requests = _parse_open_requests(message, normalized)
    if open_requests:
        return await _execute_open_requests(registry, open_requests, request_id)

    music_action = _parse_music_action(normalized)
    if music_action:
        return await _execute_music_control(registry, music_action, request_id)

    if any(word in normalized for word in ("temperatura", "quantos graus", "clima", "previsao do tempo", "temperature", "weather", "forecast")):
        default_location = str(settings.section("user").get("default_location", "")).strip()
        location_match = re.search(r"\b(?:em|para|in|for)\s+(.+?)[?.!]*$", message, flags=re.IGNORECASE)
        location = location_match.group(1).strip() if location_match else default_location
        location = canonicalize_location(location, default_location)
        day_offset = 1 if "amanha" in normalized or "tomorrow" in normalized else 0
        if location:
            return await _execute_weather(registry, location, request_id, day_offset)

    diagnostic_terms = ("ram", "memoria", "cpu", "processador", "disco", "gpu", "computador", "pc", "memory", "processor", "disk", "computer")
    diagnostic_requests = (
        "investigue", "investigar", "analise", "analisar", "diagnostique", "diagnosticar",
        "por que", "porque", "motivo", "muito alta", "muito alto", "elevada", "elevado",
        "travando", "lento", "investigate", "analyze", "diagnose", "why", "high", "slow",
    )
    if any(term in normalized for term in diagnostic_terms) and any(
        term in normalized for term in diagnostic_requests
    ):
        return await _execute_system_diagnosis(registry, normalized, request_id)

    reminder_update = _parse_reminder_update(normalized)
    if reminder_update:
        return await _execute_reminder_update(registry, reminder_update, request_id)

    reminder = _parse_relative_reminder(message, normalized)
    if reminder:
        return await _execute_reminder(registry, reminder, request_id)

    reminder = _parse_recurring_reminder(message, normalized)
    if reminder:
        return await _execute_reminder(registry, reminder, request_id)

    if any(
        term in normalized
        for term in (
            "tire um print",
            "tira um print",
            "captura da tela",
            "captura de tela",
            "capture a tela",
            "capturar a tela",
            "print da minha tela",
            "screenshot",
        )
    ):
        try:
            capture = await registry.execute("capture_screen", {}, request_id=request_id)
        except ToolError as exc:
            return DirectIntentResult(
                answer=tr(f"Não consegui capturar a tela, {_title()}. {exc}", f"I could not capture the screen, {_title()}. {exc}"),
                tool="capture_screen",
                status="failed",
                detail=str(exc),
            )
        if tool_result_outcome(capture) != "completed":
            return DirectIntentResult(
                answer=response_from_tool_result("capture_screen", capture),
                tool="capture_screen",
                status="failed",
                detail=tool_result_detail(capture),
            )
        path = str(capture.get("path", ""))
        return DirectIntentResult(
            answer=tr(f"Pronto, {_title()}. Salvei a captura em {path}.", f"Done, {_title()}. I saved the screenshot to {path}."),
            tool="capture_screen",
            detail=path,
        )

    whatsapp = _parse_whatsapp_message(message, normalized)
    if whatsapp:
        return await _execute_confirmable(
            registry,
            "whatsapp_message",
            whatsapp,
            tr(f"Vou preparar a mensagem para o WhatsApp, {_title()}.", f"I will prepare the WhatsApp message, {_title()}."),
            request_id,
        )

    discord_contact = _parse_discord_conversation(message)
    if discord_contact:
        return await _execute_open(
            registry,
            "open_discord_conversation",
            {"contact": discord_contact},
            tr(f"a conversa com {discord_contact} no Discord", f"the conversation with {discord_contact} on Discord"),
            request_id,
        )

    deletion = _parse_file_deletion(message, normalized, settings, history)
    if deletion:
        return await _execute_confirmable(
            registry,
            "move_to_recycle_bin",
            {"path": str(deletion)},
            "",
            request_id,
        )
    if _requests_file_recycle(normalized):
        return DirectIntentResult(
            answer=tr(f"Preciso do caminho ou nome exato do arquivo que deve ir para a Lixeira, {_title()}.", f"I need the exact file name or path to move it to the Recycle Bin, {_title()}."),
            tool="move_to_recycle_bin",
            status="needs_input",
            detail=tr("Arquivo não identificado", "File not identified"),
        )

    creation = _parse_desktop_correction(normalized, history)
    creation = creation or _parse_file_creation(message, normalized)
    if creation:
        return await _execute_confirmable(
            registry,
            "create_and_open_text_file",
            creation,
            tr(f"Criei e abri {creation['filename']}, {_title()}.", f"I created and opened {creation['filename']}, {_title()}."),
            request_id,
        )

    if re.search(
        r"\b(?:abra|abre|abrir)\s+(?:ele|isso|o arquivo|o relatorio|o relatório)\b",
        normalized,
    ):
        try:
            target = latest_artifact(settings)
        except FileNotFoundError as exc:
            return DirectIntentResult(answer=str(exc), tool="open_latest_artifact", status="failed")
        return await _execute_open(
            registry, "open_latest_artifact", {}, str(target), request_id
        )

    whatsapp_web_match = re.search(
        r"\b(?:abra|abre|abriu|acesse)\s+(?:o\s+)?whatsapp\s+web\b",
        normalized,
    )
    if whatsapp_web_match:
        return await _execute_open(
            registry, "open_website", {"target": "whatsapp"}, "o WhatsApp Web", request_id
        )

    whatsapp_native_match = re.search(
        r"\b(?:abra|abre|abriu|abrir|inicie|iniciar)\s+(?:o\s+|meu\s+)?whatsapp\b",
        normalized,
    )
    if whatsapp_native_match:
        return await _execute_open(
            registry, "open_app", {"query": "WhatsApp"}, "o WhatsApp", request_id
        )

    site_match = re.search(
        r"\b(?:abra|abre|abriu|acesse)\s+(?:o\s+|a\s+)?"
        r"(netflix|youtube|google|gmail|instagram|facebook|spotify)\b",
        normalized,
    )
    if site_match:
        site = site_match.group(1)
        return await _execute_open(
            registry, "open_website", {"target": site}, site, request_id
        )

    discord_match = re.search(
        r"\b(?:abra|abre|abriu|abrir|inicie|iniciar)\s+(?:o\s+|meu\s+)?discord\b",
        normalized,
    )
    if discord_match:
        return await _execute_open(
            registry, "open_app", {"query": "Discord"}, "o Discord", request_id
        )

    youtube_channel = re.fullmatch(
        r"(?:abra|abrir|acesse|acessar) (?:o )?canal (?:do|da|de) (.+?) (?:no|do) youtube",
        normalized,
    )
    if youtube_channel:
        channel = youtube_channel.group(1).strip()
        url = f"https://www.youtube.com/results?search_query={quote_plus(channel + ' canal')}"
        return await _execute_open(
            registry, "open_website", {"target": url}, f"o canal de {channel} no YouTube", request_id
        )

    vscode_match = re.match(
        r"^(?:jarvis[, ]+)?(?:abra|abrir|inicie|iniciar)\s+(?:o\s+)?"
        r"(?:vs\s*code|vscode|visual studio code)\s+(?:no\s+|na\s+|em\s+)?"
        r"(?:arquivo\s+|pasta\s+)?(.+)$",
        message.strip(),
        flags=re.IGNORECASE,
    )
    if vscode_match:
        target = vscode_match.group(1).strip(" \"'.")
        line_match = re.search(r"\s+(?:na\s+)?linha\s+(\d+)\s*$", target, flags=re.IGNORECASE)
        line = int(line_match.group(1)) if line_match else 1
        if line_match:
            target = target[:line_match.start()].strip(" \"'")
        return await _execute_open(
            registry,
            "open_in_vscode",
            {"path": target, "line": line, "column": 1},
            target,
            request_id,
        )

    private_match = re.fullmatch(
        r"(?:abra|abrir|inicie|iniciar) (?:uma |a )?(?:guia|aba|janela|modo) "
        r"(?:anonima|privada|inprivate|incognito)(?: (?:do|no|com o) (.+))?",
        normalized,
    )
    if private_match:
        target = (private_match.group(1) or "google").strip()
        return await _execute_open(registry, "open_private_browser", {"target": target}, target, request_id)

    open_match = re.fullmatch(
        r"(?:abra|abrir|acesse|acessar|inicie|iniciar|execute|executar) (?:o |a |um |uma )?(.+)",
        normalized,
    )
    if open_match and " e " not in open_match.group(1):
        target = open_match.group(1).strip()
        if re.match(r"^(?:[a-z]:\\|\\\\|/)", target):
            original_match = re.match(
                r"^(?:jarvis[, ]+)?(?:abra|abrir|acesse|acessar|inicie|iniciar|execute|executar)\s+"
                r"(?:o\s+|a\s+|um\s+|uma\s+)?(.+)$",
                message.strip(),
                flags=re.IGNORECASE,
            )
            original_target = (original_match.group(1) if original_match else target).strip(" \"'")
            tool = "open_folder" if Path(original_target).expanduser().is_dir() else "open_file"
            return await _execute_open(
                registry, tool, {"path": original_target}, original_target, request_id
            )
        classified = classify_open_target(target)
        if classified:
            return await _execute_open(
                registry,
                classified.tool,
                classified.arguments,
                classified.label,
                request_id,
            )

    return None


def _parse_open_requests(message: str, normalized: str) -> list[dict[str, Any]]:
    """Extract a safe batch of explicit site/app openings without asking the model."""
    if not re.search(r"\b(?:abra|abre|abrir|acesse|acessar|inicie|iniciar)\b", normalized):
        return []
    if re.search(r"\bnao\s+(?:abra|abre|abrir|acesse|acessar|inicie|iniciar)\b", normalized):
        return []

    requests: list[dict[str, Any]] = []
    site_labels = {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "netflix": "Netflix",
        "google": "Google",
        "gmail": "Gmail",
        "whatsapp web": "WhatsApp Web",
        "facebook": "Facebook",
        "spotify": "Spotify",
        "tiktok": "TikTok",
    }
    for site, label in site_labels.items():
        match = re.search(rf"\b{re.escape(site)}\b", normalized)
        if match:
            requests.append(
                {
                    "position": match.start(),
                    "tool": "open_website",
                    "arguments": {"target": site},
                    "label": label,
                }
            )

    native_whatsapp = re.search(r"\bwhatsapp\b(?!\s+web)", normalized)
    if native_whatsapp:
        requests.append(
            {
                "position": native_whatsapp.start(),
                "tool": "open_app",
                "arguments": {"query": "WhatsApp"},
                "label": "WhatsApp",
            }
        )

    discord = re.search(r"\bdiscord\b", normalized)
    if discord:
        requests.append(
            {
                "position": discord.start(),
                "tool": "open_app",
                "arguments": {"query": "Discord"},
                "label": "Discord",
            }
        )

    search_match = re.search(
        r"\b(?:pesquise|pesquisar|pesquisa|procure|procurar|busque|buscar)\s+(?:por\s+)?(.+?)[.!?]*$",
        message,
        flags=re.IGNORECASE,
    )
    if search_match:
        query = search_match.group(1).strip(" \"'.,;:!?")
        if query:
            search_request = {
                "position": search_match.start(),
                "tool": "open_website",
                "arguments": {
                    "target": f"https://www.google.com/search?q={quote_plus(query)}"
                },
                "label": f"a pesquisa por {query}",
            }
            google_index = next(
                (
                    index
                    for index, request in enumerate(requests)
                    if request["arguments"] == {"target": "google"}
                ),
                None,
            )
            if google_index is None:
                requests.append(search_request)
            else:
                search_request["position"] = requests[google_index]["position"]
                requests[google_index] = search_request

    requests.sort(key=lambda request: int(request["position"]))
    # A single ordinary target is handled by the more specific parsers below. A
    # Google search is kept here because opening the search results is the action.
    if len(requests) < 2 and not search_match:
        return []
    return requests


async def _execute_open_requests(
    registry: ToolRegistry,
    requests: list[dict[str, Any]],
    request_id: str,
) -> DirectIntentResult:
    traces: list[ActionTrace] = []
    opened: list[str] = []
    browser_opened: list[str] = []
    failed: list[str] = []
    for request in requests:
        tool = str(request["tool"])
        label = str(request["label"])
        try:
            result = await registry.execute(
                tool, dict(request["arguments"]), request_id=request_id
            )
        except ToolError as exc:
            failed.append(f"{label} ({exc})")
            traces.append(ActionTrace(tool=tool, status="failed", detail=str(exc)))
        else:
            outcome = tool_result_outcome(result)
            if outcome != "completed":
                detail = tool_result_detail(result)
                failed.append(f"{label} ({detail})")
                traces.append(ActionTrace(tool=tool, status=outcome, detail=detail))
                continue
            if tool in {"open_website", "open_private_browser"}:
                browser_opened.append(label)
            else:
                opened.append(label)
            traces.append(ActionTrace(tool=tool, status="completed", detail=label))

    parts: list[str] = []
    if opened:
        parts.append(tr(f"Abri {_natural_join(opened)}", f"I opened {_natural_join(opened)}"))
    if browser_opened:
        parts.append(
            tr(f"Enviei {_natural_join(browser_opened)} ao navegador e confirmei uma janela visível", f"I sent {_natural_join(browser_opened)} to the browser and verified a visible window")
        )
    if failed:
        parts.append(tr(f"Não consegui abrir {_natural_join(failed)}", f"I could not open {_natural_join(failed)}"))
    answer = ". ".join(parts).rstrip(".") + f", {_title()}."
    status = "completed" if not failed else "failed"
    return DirectIntentResult(
        answer=answer,
        tool=traces[0].tool if len(traces) == 1 else "multiple_actions",
        status=status,
        detail=tr(f"{len(opened) + len(browser_opened)} concluída(s), {len(failed)} falha(s)", f"{len(opened) + len(browser_opened)} completed, {len(failed)} failed"),
        actions=traces,
    )


def _natural_join(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + tr(f" e {items[-1]}", f" and {items[-1]}")


def _parse_whatsapp_message(message: str, normalized: str) -> dict[str, Any] | None:
    if "whatsapp" not in normalized and not any(
        phrase in normalized for phrase in ("para minha mae", "pra minha mae", "pro contato mae")
    ):
        return None
    contact_match = re.search(
        r"(?:contato\s+)?(?:da\s+minha\s+|minha\s+)?(mae|mãe)\b",
        message,
        flags=re.IGNORECASE,
    )
    quoted = re.search(r"mensagem\s+[\"'](.+?)[\"']", message, flags=re.IGNORECASE)
    after_contact = re.search(
        r"(?:para|pra|pro)\s+(?:o\s+contato\s+)?(?:da\s+minha\s+|minha\s+)?"
        r"(?:mae|mãe)\s+que\s+(.+?)[.!?]*$",
        message,
        flags=re.IGNORECASE,
    )
    if not contact_match or not (quoted or after_contact):
        return None
    text = (quoted.group(1) if quoted else after_contact.group(1)).strip(" \"'.")
    if not text:
        return None
    return {"contact": contact_match.group(1), "message": text, "send": True}


def _parse_discord_conversation(message: str) -> str | None:
    match = re.search(
        r"\b(?:abra|abre|abrir|inicie|iniciar)\s+(?:uma\s+|a\s+)?"
        r"conversa\s+com\s+(?:o\s+|a\s+)?(.+?)\s+(?:no|do)\s+discord\b",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip(" \"'.,") or None


def _parse_power_action(normalized: str) -> str | None:
    english = re.fullmatch(
        r"(?:please\s+)?(shut down|shutdown|restart|reboot|lock)\s+"
        r"(?:the\s+|my\s+)?(?:pc|computer|windows|session)(?:\s+please)?",
        normalized,
    )
    if english:
        return {"shut down": "shutdown", "shutdown": "shutdown", "reboot": "restart"}.get(
            english.group(1), english.group(1)
        )
    if re.search(r"\b(?:desliga|desligue|desligar)\b", normalized) and re.search(
        r"\b(?:pc|computador|maquina|windows)\b", normalized
    ):
        return "shutdown"
    if re.search(r"\b(?:reinicia|reinicie|reiniciar|reboot)\b", normalized) and (
        re.search(r"\b(?:pc|computador|maquina|windows)\b", normalized)
        or re.fullmatch(r"(?:pode\s+)?reiniciar(?:\s+(?:ai|por favor))?", normalized)
    ):
        return "restart"
    if re.search(r"\b(?:bloqueia|bloqueie|bloquear|trava|trave)\b", normalized) and re.search(
        r"\b(?:pc|computador|maquina|windows|sessao)\b", normalized
    ):
        return "lock"
    return None


def _requests_empty_recycle_bin(normalized: str) -> bool:
    if re.fullmatch(r"(?:please\s+)?empty\s+(?:the\s+)?recycle bin(?:\s+on\s+drive\s+[a-z]:)?(?:\s+please)?", normalized):
        return True
    return "lixeira" in normalized and bool(
        re.search(r"\b(?:esvazia|esvazie|esvaziar|limpa|limpe|limpar)\b", normalized)
    )


def _parse_recycle_bin_drive(message: str) -> str | None:
    match = re.search(
        r"\b(?:unidade|drive|disco)\s+([A-Za-z]):(?:\\)?(?=\s|$)",
        message,
        flags=re.IGNORECASE,
    )
    return f"{match.group(1).upper()}:" if match else None


def _parse_windows_audio_action(
    normalized: str,
) -> tuple[str, dict[str, Any]] | None:
    english = re.fullmatch(
        r"(?:please\s+)?(?:(set)\s+(?:the\s+)?volume\s+to\s+(\d{1,3})(?:%|\s+percent)?|"
        r"(increase|raise|lower|decrease)\s+(?:the\s+)?volume|"
        r"(mute|unmute)\s+(?:the\s+|my\s+)?(?:audio|sound|volume|computer|pc))(?:\s+please)?",
        normalized,
    )
    if english:
        if english.group(1):
            return "set_volume", {"level": max(0, min(100, int(english.group(2))))}
        if english.group(3):
            return "set_volume", {"delta": 10 if english.group(3) in {"increase", "raise"} else -10}
        return "set_mute", {"muted": english.group(4) == "mute"}
    if re.search(r"\b(?:muta|mute|mutar)\b", normalized) and re.search(
        r"\b(?:pc|computador|audio|som|volume)\b", normalized
    ):
        return "set_mute", {"muted": True}
    if re.search(r"\b(?:desmuta|desmute|desmutar)\b", normalized):
        return "set_mute", {"muted": False}
    if "volume" not in normalized and "som" not in normalized:
        return None
    absolute = re.search(r"\b(?:volume|som)\s+(?:em\s+)?(\d{1,3})\b", normalized)
    if absolute:
        return "set_volume", {"level": max(0, min(100, int(absolute.group(1))))}
    if re.search(r"\b(?:aumenta|aumente|suba|eleva|eleve)\b", normalized):
        return "set_volume", {"delta": 10}
    if re.search(r"\b(?:abaixa|abaixe|diminua|reduza|baixa|baixe)\b", normalized):
        return "set_volume", {"delta": -10}
    return None


def _parse_close_process_target(normalized: str) -> str | None:
    match = re.search(
        r"\b(?:fecha|feche|fechar|encerra|encerre)\s+(?:o\s+|a\s+)?"
        r"(discord|vscode|visual studio code|camera|epic games launcher|launcher)\b",
        normalized,
    )
    return match.group(1) if match else None


def _resolve_process_pid(target: str) -> int | None:
    aliases = {
        "discord": {"discord.exe"},
        "vscode": {"code.exe"},
        "visual studio code": {"code.exe"},
        "camera": {"windowscamera.exe", "camera.exe"},
        "epic games launcher": {"epicgameslauncher.exe"},
        "launcher": {"epicgameslauncher.exe"},
    }
    try:
        import psutil
    except ImportError:
        return None
    matches: list[int] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (process.info.get("name") or "").casefold() in aliases.get(target, set()):
                matches.append(int(process.info["pid"]))
        except (psutil.Error, TypeError, ValueError):
            continue
    return min(matches) if matches else None


def _requests_file_recycle(normalized: str) -> bool:
    destructive = bool(
        re.search(
            r"\b(?:delete|deletar|apague|apagar|exclua|excluir|joga|jogue|mova|mandar|mande)\b",
            normalized,
        )
    )
    return destructive and ("arquivo" in normalized or "lixeira" in normalized or bool(re.search(r"\.[a-z0-9]{1,12}\b", normalized)))


def _parse_music_action(normalized: str) -> str | None:
    english = re.fullmatch(r"(?:please\s+)?(play|pause|resume|restart|stop)\s+(?:the\s+)?(?:music|theme|theme music)(?:\s+please)?", normalized)
    if english:
        return english.group(1)
    if not any(term in normalized for term in ("musica", "tema", "the clash")):
        return None
    if re.search(r"\b(?:parar|pare|desligar|desligue|encerrar)\b", normalized):
        return "stop"
    if re.search(r"\b(?:pausar|pause)\b", normalized):
        return "pause"
    if re.search(r"\b(?:recomecar|recomece|reiniciar|reinicie|do comeco)\b", normalized):
        return "restart"
    if re.search(r"\b(?:continuar|continue|retomar|retome|despausar)\b", normalized):
        return "resume"
    if re.search(r"\b(?:tocar|toque|iniciar|comece)\b", normalized):
        return "play"
    return None


async def _execute_music_control(
    registry: ToolRegistry, action: str, request_id: str
) -> DirectIntentResult:
    try:
        result = await registry.execute(
            "control_theme_music", {"action": action}, request_id=request_id
        )
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui controlar a música, {_title()}. {exc}", f"I could not control the music, {_title()}. {exc}"),
            tool="control_theme_music",
            status="failed",
            detail=str(exc),
        )
    outcome = tool_result_outcome(result)
    if outcome != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("control_theme_music", result),
            tool="control_theme_music",
            status=outcome,
            detail=tool_result_detail(result),
        )
    answers = {
        "play": tr(f"A música começou, {_title()}.", f"The music started, {_title()}."),
        "pause": tr(f"Música pausada, {_title()}.", f"Music paused, {_title()}."),
        "resume": tr(f"Continuando a música, {_title()}.", f"Music resumed, {_title()}."),
        "restart": tr(f"Recomeçando a música, {_title()}.", f"Music restarted, {_title()}."),
        "stop": tr(f"Música parada, {_title()}.", f"Music stopped, {_title()}."),
    }
    return DirectIntentResult(
        answer=answers[action],
        tool="control_theme_music",
        detail=str(result),
    )


def _parse_file_deletion(
    message: str,
    normalized: str,
    settings: SettingsStore,
    history: list[dict[str, Any]],
) -> Path | None:
    if not _requests_file_recycle(normalized):
        return None
    explicit_path = re.search(
        r"\b([A-Za-z]:\\.+?\.[A-Za-z0-9]{1,12})"
        r"(?:\s+(?:para|pra|na|a)\s+(?:a\s+|minha\s+)?lixeira|\s*$)",
        message,
        flags=re.IGNORECASE,
    )
    if explicit_path:
        target = Path(explicit_path.group(1).strip(" \"'.")).expanduser()
        return target.resolve() if target.is_file() else None
    quoted = re.search(r"[\"']([^\"']+\.[A-Za-z0-9]{1,12})[\"']", message)
    unquoted = re.search(
        r"\barquivo\s+(?:chamado\s+)?"
        r"([\wÀ-ÿ _-]+?(?:\.|\s+ponto\s+)[A-Za-z0-9]{1,12})\b",
        message,
        flags=re.IGNORECASE,
    )
    after_verb = re.search(
        r"\b(?:delete|deletar|deletei?|apague|apagar|exclua|excluir|joga|jogue|mova|mande)\s+"
        r"(?:o\s+)?(?:arquivo\s+)?"
        r"([\wÀ-ÿ _-]+?(?:\.|\s+ponto\s+)[A-Za-z0-9]{1,12})\b",
        message,
        flags=re.IGNORECASE,
    )
    raw_name = (
        quoted.group(1)
        if quoted
        else unquoted.group(1)
        if unquoted
        else after_verb.group(1)
        if after_verb
        else ""
    ).strip(" .")
    if not raw_name:
        if re.search(r"\b(?:esse|este|o)\s+arquivo\b", normalized):
            for item in reversed(history):
                prior = str(item.get("content") or "")
                path_match = re.search(r"\b([A-Za-z]:\\[^\r\n\"']+?\.[A-Za-z0-9]{1,12})\b", prior)
                if path_match:
                    candidate = Path(path_match.group(1).strip()).expanduser()
                    if candidate.is_file():
                        return candidate.resolve()
                name_match = re.search(r"\b([\wÀ-ÿ _-]+\.[A-Za-z0-9]{1,12})\b", prior)
                if name_match:
                    resolved = resolve_named_file(name_match.group(1), settings)
                    if resolved:
                        return resolved
        return None
    return resolve_named_file(raw_name, settings)


def _parse_file_creation(message: str, normalized: str) -> dict[str, Any] | None:
    if not any(
        term in normalized
        for term in (
            "cria", "crie", "criar", "gere", "gerar", "faca", "faz um arquivo",
            "me mande um relatorio", "faca um relatorio",
        )
    ):
        return None
    if not any(term in normalized for term in ("arquivo", "txt", "relatorio", "documento")):
        return None
    quoted = re.search(r"[\"']([^\"']+\.(?:txt|md|json|csv|py|js|ts|html|css))[\"']", message, flags=re.IGNORECASE)
    named = re.search(
        r"(?:chamado|nome(?:ado)?)\s+([\wÀ-ÿ._ -]+\.(?:txt|md|json|csv|py|js|ts|html|css))",
        message,
        flags=re.IGNORECASE,
    )
    direct_name = re.search(
        r"\b(?:cria|crie|criar|gere|gerar|faça|faca)\s+"
        r"(?:(?:um|o)\s+)?(?:(?:arquivo|documento)\s+)?"
        r"([\wÀ-ÿ._-]+\.(?:txt|md|json|csv|py|js|ts|html|css))\b",
        message,
        flags=re.IGNORECASE,
    )
    if quoted or named or direct_name:
        filename = (
            quoted.group(1)
            if quoted
            else named.group(1)
            if named
            else direct_name.group(1)
        ).strip()
    else:
        extension_match = re.search(
            r"\b(?:arquivo|documento)\s+(?:ponto\s+)?(txt|md|json|csv|py|js|ts|html|css)\b",
            normalized,
        )
        extension = extension_match.group(1) if extension_match else "txt"
        filename = datetime.now().astimezone().strftime(f"nota-jarvis-%Y%m%d-%H%M%S.{extension}")
    content_match = re.search(
        r"(?:com\s+(?:o\s+)?conte[uú]do|escreva(?:\s+nele)?|contendo)\s*[:\-]?\s*(.+)$",
        message,
        flags=re.IGNORECASE,
    )
    if content_match:
        content = content_match.group(1).strip(" \"'")
        content = re.split(
            r"\s+e\s+(?:abra|abre|abrir|deixe\s+aberto)(?:\s+para\s+eu\s+ver)?[.!?]*$",
            content,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" \"'")
    else:
        content = ""
    open_requested = any(term in normalized for term in ("abra", "abrir", "aberto", "para eu ver"))
    return {
        "filename": filename,
        "content": content,
        "open_with": "default" if open_requested else "default",
        "directory": (
            "desktop"
            if any(term in normalized for term in ("area de trabalho", "desktop"))
            else "artifacts"
        ),
        "overwrite": False,
    }


def _parse_desktop_correction(
    normalized: str,
    history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    asks_desktop = any(term in normalized for term in ("desktop", "area de trabalho"))
    is_correction = any(
        term in normalized
        for term in (
            "eu falei", "eu disse", "nao esta aqui", "nao ficou", "local errado",
            "era para", "deveria estar", "corrija", "corrige",
        )
    )
    if not asks_desktop or not is_correction:
        return None
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        prior = str(item.get("content") or "")
        parsed = _parse_file_creation(prior, normalize_text(prior))
        if parsed:
            return {**parsed, "directory": "desktop", "overwrite": False}
    return None


async def _execute_confirmable(
    registry: ToolRegistry,
    tool: str,
    arguments: dict[str, Any],
    success_answer: str,
    request_id: str,
) -> DirectIntentResult:
    try:
        result = await registry.execute(tool, arguments, request_id=request_id)
    except ConfirmationRequired as pending:
        return DirectIntentResult(
            answer=pending.message,
            tool=tool,
            status="awaiting_confirmation",
            confirmation_id=pending.confirmation_id,
            detail=pending.message,
        )
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui concluir, {_title()}. {exc}", f"I could not complete the action, {_title()}. {exc}"),
            tool=tool,
            status="failed",
            detail=str(exc),
        )
    outcome = tool_result_outcome(result)
    detail = tool_result_detail(result)
    if outcome != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result(tool, result),
            tool=tool,
            status=outcome,
            detail=detail,
        )
    if tool == "create_and_open_text_file" and isinstance(result, dict):
        written = str(result.get("written") or arguments.get("filename") or tr("o arquivo", "the file"))
        return DirectIntentResult(
            answer=tr(f"Criei, conferi e abri {written}, {_title()}.", f"I created, verified and opened {written}, {_title()}."),
            tool=tool,
            detail=detail,
        )
    return DirectIntentResult(
        answer=success_answer or response_from_tool_result(tool, result),
        tool=tool,
        detail=detail,
    )


def _parse_reminder_update(normalized: str) -> dict[str, Any] | None:
    if "lembrete" not in normalized or not any(
        verb in normalized for verb in ("altere", "alterar", "mude", "mudar", "ajuste", "corrija")
    ):
        return None
    time_match = re.search(
        r"\b(?:para|pras?|as)\s+(\d{1,2})(?:[:h](\d{2})?)?\b", normalized
    )
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    title_match = re.search(
        r"\blembrete\s+(?:da|do|de)?\s*(.+?)\s+(?:para|pras?|as)\s+\d{1,2}",
        normalized,
    )
    return {
        "title_query": (title_match.group(1).strip() if title_match else ""),
        "hour": hour,
        "minute": minute,
    }


async def _execute_reminder_update(
    registry: ToolRegistry, update: dict[str, Any], request_id: str
) -> DirectIntentResult:
    try:
        reminders = await registry.execute("list_reminders", {}, request_id=request_id)
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui consultar os lembretes, {_title()}. {exc}", f"I could not retrieve the reminders, {_title()}. {exc}"),
            tool="update_reminder",
            status="failed",
        )
    if tool_result_outcome(reminders) != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("list_reminders", reminders),
            tool="list_reminders",
            status="failed",
            detail=tool_result_detail(reminders),
        )
    reminders = reminders.get("items", []) if isinstance(reminders, dict) else []
    if not reminders:
        return DirectIntentResult(
            answer=tr(f"Não encontrei um lembrete ativo para alterar, {_title()}.", f"I could not find an active reminder to update, {_title()}."),
            tool="update_reminder",
            status="failed",
        )
    query = normalize_text(str(update["title_query"]))
    matches = [
        item for item in reminders
        if not query or query in normalize_text(str(item.get("title", "")))
    ]
    if len(matches) != 1:
        return DirectIntentResult(
            answer=tr(f"Encontrei mais de um lembrete possível. Diga o nome completo, {_title()}.", f"I found multiple possible reminders. Please give the full name, {_title()}."),
            tool="update_reminder",
            status="failed",
        )
    selected = matches[0]
    due = datetime.fromisoformat(str(selected["due_at"]).replace("Z", "+00:00")).astimezone()
    due = due.replace(hour=int(update["hour"]), minute=int(update["minute"]), second=0, microsecond=0)
    arguments = {"reminder_id": int(selected["id"]), "due_at": due.isoformat()}
    try:
        result = await registry.execute("update_reminder", arguments, request_id=request_id)
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui alterar o lembrete, {_title()}. {exc}", f"I could not update the reminder, {_title()}. {exc}"),
            tool="update_reminder",
            status="failed",
        )
    if tool_result_outcome(result) != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("update_reminder", result),
            tool="update_reminder",
            status="failed",
            detail=tool_result_detail(result),
        )
    title = str(result.get("title") or selected.get("title") or tr("lembrete", "reminder"))
    return DirectIntentResult(
        answer=tr(
            f"Pronto, {_title()}. Alterei {title} para {int(update['hour']):02d}:{int(update['minute']):02d}.",
            f"Done, {_title()}. I changed {title} to {int(update['hour']):02d}:{int(update['minute']):02d}.",
        ),
        tool="update_reminder",
    )


def _parse_relative_reminder(message: str, normalized: str) -> dict[str, Any] | None:
    if not any(term in normalized for term in ("lembre", "lembrar", "avise", "avisar", "alarme")):
        return None
    relative = re.search(
        r"\bdaqui\s+(?:a\s+)?(\d+)\s*(segundos?|minutos?|horas?)\b",
        normalized,
    )
    if not relative:
        return None
    amount = int(relative.group(1))
    unit = relative.group(2)
    if amount < 1:
        return None
    if unit.startswith("segundo"):
        delta = timedelta(seconds=min(amount, 86_400))
        unit_label = "segundo" if amount == 1 else "segundos"
    elif unit.startswith("minuto"):
        delta = timedelta(minutes=min(amount, 10_080))
        unit_label = "minuto" if amount == 1 else "minutos"
    else:
        delta = timedelta(hours=min(amount, 168))
        unit_label = "hora" if amount == 1 else "horas"

    title = "lembrete"
    # The final connector identifies the reminder's purpose in nested requests.
    connectors = list(re.finditer(r"\bque\b", message, flags=re.IGNORECASE))
    if connectors:
        title = message[connectors[-1].end():].strip(" .,;:-")
    else:
        original_relative = re.search(
            r"\bdaqui\s+(?:a\s+)?\d+\s*(?:segundos?|minutos?|horas?)\b",
            message,
            flags=re.IGNORECASE,
        )
        tail = message[original_relative.end():] if original_relative else ""
        title = re.sub(
            r"^(?:,?\s*(?:para|de)\s+(?:eu\s+)?)",
            "",
            tail,
            flags=re.IGNORECASE,
        ).strip(" .,;:-") or "lembrete"
    title = re.sub(
        r"^(?:eu\s+)?(?:preciso|devo|tenho\s+que)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip(" .,;:-") or "lembrete"
    due = datetime.now().astimezone() + delta
    return {
        "title": title,
        "due_at": due.isoformat(),
        "recurrence": "none",
        "minutes_before": 0,
        "schedule_label": f"daqui a {amount} {unit_label}",
    }


def _parse_recurring_reminder(message: str, normalized: str) -> dict[str, Any] | None:
    if not any(term in normalized for term in ("lembre", "lembrar", "avise", "avisar")):
        return None
    time_match = re.search(r"\b(?:as\s+)?(\d{1,2})(?:[:h](\d{2})?)?\b", normalized)
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None

    recurrence = ""
    target_weekday: int | None = None
    weekday_label = ""
    for weekday, number in WEEKDAYS.items():
        if re.search(rf"\b(?:toda|todo)\s+(?:a\s+)?{weekday}(?:-feira)?\b", normalized):
            recurrence = "weekly"
            target_weekday = number
            weekday_label = f"{weekday}-feira" if weekday not in {"sabado", "domingo"} else weekday
            break
    if not recurrence and re.search(r"\b(?:todo dia|todos os dias|diariamente)\b", normalized):
        recurrence = "daily"
    if not recurrence:
        return None

    now = datetime.now().astimezone()
    if target_weekday is None:
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        schedule_label = f"todos os dias às {hour:02d}:{minute:02d}"
    else:
        days_ahead = (target_weekday - now.weekday()) % 7
        due = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if due <= now:
            due += timedelta(days=7)
        schedule_label = f"toda {weekday_label} às {hour:02d}:{minute:02d}"

    original_time = re.search(
        r"\b(?:às?|as?)\s+(\d{1,2})(?:[:h](\d{2})?)?\b", message, flags=re.IGNORECASE
    )
    tail = message[original_time.end():] if original_time else ""
    tail = re.sub(r"^[\s,]*(?:da|do|de|sobre)\s+", "", tail, flags=re.IGNORECASE)
    title = re.split(r",?\s+(?:avis\w*|com\s+aviso)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0]
    title = title.strip(" .,;:-") or "compromisso"
    before_match = re.search(r"\b(\d+)\s+minutos?\s+antes\b", normalized)
    minutes_before = int(before_match.group(1)) if before_match else 0
    if minutes_before > 10_080:
        return None
    return {
        "title": title,
        "due_at": due.isoformat(),
        "recurrence": recurrence,
        "minutes_before": minutes_before,
        "schedule_label": schedule_label,
    }


async def _execute_reminder(
    registry: ToolRegistry, reminder: dict[str, Any], request_id: str
) -> DirectIntentResult:
    arguments = {key: reminder[key] for key in ("title", "due_at", "recurrence", "minutes_before")}
    try:
        result = await registry.execute("create_reminder", arguments, request_id=request_id)
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui criar o lembrete, {_title()}. {exc}", f"I could not create the reminder, {_title()}. {exc}"),
            tool="create_reminder",
            status="failed",
        )
    if tool_result_outcome(result) != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("create_reminder", result),
            tool="create_reminder",
            status="failed",
            detail=tool_result_detail(result),
        )
    before = int(reminder["minutes_before"])
    advance = tr(f", com aviso {before} minutos antes", f", with an alert {before} minutes before") if before else ""
    return DirectIntentResult(
        answer=tr(
            f"Lembrete criado: {reminder['title']}, {reminder['schedule_label']}{advance}, {_title()}.",
            f"Reminder created: {reminder['title']}, {reminder['schedule_label']}{advance}, {_title()}.",
        ),
        tool="create_reminder",
    )


async def _execute_weather(
    registry: ToolRegistry, location: str, request_id: str, day_offset: int = 0
) -> DirectIntentResult:
    try:
        result = await registry.execute(
            "get_current_weather",
            {"location": location, "day_offset": day_offset},
            request_id=request_id,
        )
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui consultar o clima agora, {_title()}. {exc}", f"I could not retrieve the weather now, {_title()}. {exc}"),
            tool="get_current_weather",
            status="failed",
        )
    if tool_result_outcome(result) != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("get_current_weather", result),
            tool="get_current_weather",
            status="failed",
            detail=tool_result_detail(result),
        )
    place = ", ".join(part for part in (result.get("location"), result.get("state")) if part)
    if day_offset:
        maximum = result.get("temperature_max_c")
        minimum = result.get("temperature_min_c")
        rain = result.get("precipitation_probability_percent")
        condition = result.get("condition") or tr("condição não informada", "unspecified conditions")
        if isinstance(maximum, (int, float)) and isinstance(minimum, (int, float)):
            answer = tr(
                f"Amanhã em {place}, a previsão é de {condition}, mínima de {minimum:g} "
                f"e máxima de {maximum:g} graus",
                f"Tomorrow in {place}, the forecast is {condition}, with a low of {minimum:g} "
                f"and a high of {maximum:g} degrees Celsius",
            )
            if isinstance(rain, (int, float)):
                answer += tr(f", com até {rain:g}% de chance de chuva", f", with up to a {rain:g}% chance of rain")
            answer += f", {_title()}."
        else:
            answer = tr(f"Recebi a previsão de amanhã para {place}, mas faltaram as temperaturas, {_title()}.", f"I received tomorrow's forecast for {place}, but temperatures were missing, {_title()}.")
        return DirectIntentResult(
            answer=answer,
            tool="get_current_weather",
            sources=[{"title": "Open-Meteo", "url": result["source_url"], "source": "Open-Meteo"}],
        )
    temperature = result.get("temperature_c")
    feels = result.get("feels_like_c")
    condition = result.get("condition") or tr("condição não informada", "unspecified conditions")
    if isinstance(temperature, (int, float)):
        answer = tr(f"Agora em {place}, faz {temperature:g} graus, com {condition}.", f"It is currently {temperature:g} degrees Celsius in {place}, with {condition}.")
        if isinstance(feels, (int, float)):
            answer += tr(f" A sensação térmica é de {feels:g} graus, {_title()}.", f" It feels like {feels:g} degrees Celsius, {_title()}.")
        else:
            answer += f" {_title().capitalize()}."
    else:
        answer = tr(f"Recebi o clima de {place}, mas a temperatura não veio informada, {_title()}.", f"I received the weather for {place}, but no temperature was reported, {_title()}.")
    return DirectIntentResult(
        answer=answer,
        tool="get_current_weather",
        sources=[{"title": "Open-Meteo", "url": result["source_url"], "source": "Open-Meteo"}],
    )


async def _execute_system_diagnosis(
    registry: ToolRegistry, normalized: str, request_id: str
) -> DirectIntentResult:
    if any(term in normalized for term in ("ram", "memoria", "memory")):
        focus = "memory"
    elif any(term in normalized for term in ("cpu", "processador", "processor")):
        focus = "cpu"
    elif "disco" in normalized or "disk" in normalized:
        focus = "disk"
    elif "gpu" in normalized:
        focus = "gpu"
    else:
        focus = "auto"
    try:
        result = await registry.execute(
            "diagnose_system_usage", {"focus": focus, "limit": 6}, request_id=request_id
        )
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui diagnosticar o computador agora, {_title()}. {exc}", f"I could not diagnose the computer now, {_title()}. {exc}"),
            tool="diagnose_system_usage",
            status="failed",
            detail=str(exc),
        )
    if tool_result_outcome(result) != "completed":
        return DirectIntentResult(
            answer=response_from_tool_result("diagnose_system_usage", result),
            tool="diagnose_system_usage",
            status="failed",
            detail=tool_result_detail(result),
        )
    metrics = result.get("metrics") or {}
    ram = metrics.get("ram_percent")
    cpu = metrics.get("cpu_percent")
    answer = (
        tr(f"Diagnóstico atual, {_title()}: RAM em {ram:g}% e CPU em {cpu:g}%.", f"Current diagnosis, {_title()}: RAM at {ram:g}% and CPU at {cpu:g}%.")
        if isinstance(ram, (int, float)) and isinstance(cpu, (int, float))
        else tr(f"Diagnóstico atual concluído, {_title()}.", f"Current diagnosis completed, {_title()}.")
    )
    top_key = "top_cpu" if focus == "cpu" else "top_memory"
    top = result.get(top_key) or []
    if top:
        items = []
        for process in top[:5]:
            if focus == "cpu":
                items.append(tr(f"{process['name']} ({process['cpu_percent']:g}% de CPU)", f"{process['name']} ({process['cpu_percent']:g}% CPU)"))
            else:
                items.append(f"{process['name']} ({process['memory_mb']:g} MB)")
        answer += tr(" Maiores consumidores: ", " Top consumers: ") + ", ".join(items) + "."
    observations = result.get("observations") or []
    if observations:
        answer += " " + " ".join(str(item) for item in observations[:3])
    return DirectIntentResult(
        answer=answer,
        tool="diagnose_system_usage",
        detail=tr(f"Foco: {focus}", f"Focus: {focus}"),
    )


async def _execute_open(
    registry: ToolRegistry,
    tool: str,
    arguments: dict[str, Any],
    requested_target: str,
    request_id: str,
) -> DirectIntentResult:
    try:
        result = await registry.execute(tool, arguments, request_id=request_id)
    except ToolError as exc:
        return DirectIntentResult(
            answer=tr(f"Não consegui abrir {requested_target}, {_title()}. {exc}", f"I could not open {requested_target}, {_title()}. {exc}"),
            tool=tool,
            status="failed",
        )
    outcome = tool_result_outcome(result)
    if outcome != "completed":
        detail = tool_result_detail(result)
        candidates = result.get("candidates") if isinstance(result, dict) else None
        if outcome == "needs_input" and candidates:
            names = [str(item.get("name")) for item in candidates if isinstance(item, dict)]
            choice = _natural_join([name for name in names if name])
            answer = tr(f"Encontrei mais de uma opção para {requested_target}: {choice}. Qual devo abrir, {_title()}?", f"I found multiple options for {requested_target}: {choice}. Which should I open, {_title()}?")
        else:
            answer = tr(f"Não consegui abrir {requested_target}, {_title()}. {detail}", f"I could not open {requested_target}, {_title()}. {detail}")
        return DirectIntentResult(
            answer=answer,
            tool=tool,
            status=outcome,
            detail=detail,
        )
    if tool in {"open_website", "open_private_browser"}:
        return DirectIntentResult(
            answer=response_from_tool_result(tool, result),
            tool=tool,
            detail=tool_result_detail(result),
        )
    opened = str(result.get("opened") or requested_target)
    if tool == "open_private_browser":
        answer = tr(f"Abri {opened} em uma janela anônima, {_title()}.", f"I opened {opened} in a private window, {_title()}.")
    elif tool == "open_website":
        answer = tr(f"Abri {opened} no seu navegador, {_title()}.", f"I opened {opened} in your browser, {_title()}.")
    else:
        answer = tr(f"Abri {opened}, {_title()}.", f"I opened {opened}, {_title()}.")
    return DirectIntentResult(
        answer=answer,
        tool=tool,
        detail=tool_result_detail(result),
    )
