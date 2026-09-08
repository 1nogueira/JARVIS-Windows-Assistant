from __future__ import annotations

import asyncio
import ctypes
import os
import time
from uuid import uuid4
from typing import Any

from backend.core.cancellation import CancellationToken
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_failure, explicit_success


def _pyautogui() -> Any:
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("PyAutoGUI não está instalado.") from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.12
    return pyautogui


def foreground_title() -> str:
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def _paste_unicode(
    gui: Any, text: str, cancellation_token: CancellationToken | None = None
) -> None:
    import pyperclip

    previous = pyperclip.paste()
    try:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        pyperclip.copy(text)
        gui.hotkey("ctrl", "v")
        if cancellation_token:
            cancellation_token.wait(0.25)
        else:
            time.sleep(0.25)
    finally:
        pyperclip.copy(previous)


def run_actions(
    actions: list[dict[str, Any]], cancellation_token: CancellationToken | None = None
) -> dict[str, Any]:
    if not 1 <= len(actions) <= 25:
        raise ValueError("A automação precisa ter entre 1 e 25 ações.")
    gui = _pyautogui()
    completed = 0
    for action in actions:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        kind = str(action.get("type", "")).casefold()
        if kind == "wait":
            seconds = max(0.0, min(10.0, float(action.get("seconds", 1))))
            if cancellation_token:
                cancellation_token.wait(seconds)
            else:
                time.sleep(seconds)
        elif kind in {"click", "double_click"}:
            x, y = int(action["x"]), int(action["y"])
            button = str(action.get("button", "left"))
            if kind == "double_click":
                gui.doubleClick(x=x, y=y, button=button, interval=0.12)
            else:
                gui.click(x=x, y=y, button=button)
        elif kind == "type":
            interval = max(0.0, min(0.2, float(action.get("interval", 0.02))))
            for character in str(action.get("text", "")):
                if cancellation_token:
                    cancellation_token.raise_if_cancelled()
                gui.write(character)
                if interval:
                    if cancellation_token:
                        cancellation_token.wait(interval)
                    else:
                        time.sleep(interval)
        elif kind == "press":
            for _ in range(max(1, min(10, int(action.get("presses", 1))))):
                if cancellation_token:
                    cancellation_token.raise_if_cancelled()
                gui.press(str(action["key"]))
        elif kind == "hotkey":
            keys = action.get("keys")
            if not isinstance(keys, list) or not 2 <= len(keys) <= 4:
                raise ValueError("Atalho inválido.")
            gui.hotkey(*(str(key) for key in keys))
        elif kind == "scroll":
            gui.scroll(int(action.get("amount", 0)))
        else:
            raise ValueError(f"Ação de interface não reconhecida: {kind}")
        completed += 1
    final_window = foreground_title()
    cursor = gui.position()
    return {
        "completed_actions": completed,
        "foreground_window": final_window,
        "cursor": {"x": int(cursor.x), "y": int(cursor.y)},
        "verification": "input_sequence_count_and_final_desktop_state_sampled",
    }


def _focused_text_readback(gui: Any) -> tuple[bool, str]:
    import pyperclip

    previous = pyperclip.paste()
    sentinel = f"jarvis-clipboard-sentinel-{uuid4()}"
    try:
        pyperclip.copy(sentinel)
        gui.hotkey("ctrl", "a")
        gui.hotkey("ctrl", "c")
        time.sleep(0.2)
        value = pyperclip.paste()
        return value != sentinel, value
    finally:
        pyperclip.copy(previous)


def whatsapp_message(
    contact: str,
    message: str,
    send: bool,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, Any]:
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    gui = _pyautogui()
    os.startfile("whatsapp:")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        if "whatsapp" in foreground_title().casefold():
            break
        if cancellation_token:
            cancellation_token.wait(0.4)
        else:
            time.sleep(0.4)
    else:
        raise RuntimeError("O WhatsApp não abriu ou não está conectado.")
    gui.hotkey("ctrl", "f")
    _paste_unicode(gui, contact, cancellation_token)
    cancellation_token.wait(1.2) if cancellation_token else time.sleep(1.2)
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    gui.press("enter")
    cancellation_token.wait(0.8) if cancellation_token else time.sleep(0.8)
    selected_title = foreground_title()
    if "whatsapp" not in selected_title.casefold():
        raise RuntimeError("O WhatsApp perdeu o foco; nenhuma mensagem foi digitada.")
    normalized_contact = contact.casefold().strip()
    if normalized_contact not in selected_title.casefold():
        return explicit_failure(
            "whatsapp_contact_not_verified",
            detail=(
                "O WhatsApp ficou em primeiro plano, mas o contato selecionado não pôde "
                "ser confirmado. Nenhuma mensagem foi digitada."
            ),
            data={"contact": contact, "sent": False, "window_title": selected_title},
            verification="foreground_app_without_exact_contact_readback",
        )
    _paste_unicode(gui, message, cancellation_token)
    if send:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        gui.press("enter")
        cancellation_token.wait(0.6) if cancellation_token else time.sleep(0.6)
        has_value, value = _focused_text_readback(gui)
        if has_value and value == message:
            return explicit_failure(
                "whatsapp_send_not_accepted",
                detail="O texto permaneceu no campo após Enter; o envio não foi confirmado.",
                data={"contact": contact, "characters": len(message), "sent": False},
                verification="message_text_remained_in_input",
            )
        return explicit_success(
            {"contact": contact, "characters": len(message), "sent": True},
            verification="WhatsApp_input_cleared_after_send_key",
        )
    has_value, value = _focused_text_readback(gui)
    if not has_value or value != message:
        return explicit_failure(
            "whatsapp_draft_not_verified",
            data={"contact": contact, "characters": len(message), "sent": False},
            verification="draft_input_readback_mismatch",
        )
    return explicit_success(
        {"contact": contact, "characters": len(message), "sent": False},
        verification="WhatsApp_draft_text_read_back",
    )


def discord_conversation(
    contact: str, cancellation_token: CancellationToken | None = None
) -> dict[str, Any]:
    clean_contact = contact.strip()
    if not clean_contact or len(clean_contact) > 100:
        raise ValueError("Informe o nome do contato do Discord.")
    gui = _pyautogui()
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    os.startfile("discord://")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        if "discord" in foreground_title().casefold():
            break
        cancellation_token.wait(0.4) if cancellation_token else time.sleep(0.4)
    else:
        raise RuntimeError("O Discord não abriu ou não ficou disponível.")
    gui.hotkey("ctrl", "k")
    cancellation_token.wait(0.35) if cancellation_token else time.sleep(0.35)
    _paste_unicode(gui, clean_contact, cancellation_token)
    cancellation_token.wait(1.2) if cancellation_token else time.sleep(1.2)
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    gui.press("enter")
    cancellation_token.wait(0.8) if cancellation_token else time.sleep(0.8)
    selected_title = foreground_title()
    if "discord" not in selected_title.casefold():
        raise RuntimeError("O Discord perdeu o foco antes de abrir a conversa.")
    if clean_contact.casefold() not in selected_title.casefold():
        return explicit_failure(
            "discord_contact_not_verified",
            detail=(
                "O Discord ficou em primeiro plano, mas a conversa selecionada não pôde "
                "ser confirmada pelo título da janela."
            ),
            data={"contact": clean_contact, "window_title": selected_title},
            verification="foreground_app_without_exact_contact_readback",
        )
    return explicit_success(
        {
            "opened": f"conversa com {clean_contact} no Discord",
            "contact": clean_contact,
            "window_title": selected_title,
        },
        verification="Discord_contact_present_in_foreground_window_title",
    )


def register_desktop_tools(registry: ToolRegistry) -> None:
    action_schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["wait"]},
                    "seconds": {"type": "number", "minimum": 0, "maximum": 10},
                },
                "required": ["type"],
            },
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["click", "double_click"]},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "middle", "right"]},
                },
                "required": ["type", "x", "y"],
            },
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["type"]},
                    "text": {"type": "string", "maxLength": 10_000},
                    "interval": {"type": "number", "minimum": 0, "maximum": 0.2},
                },
                "required": ["type", "text"],
            },
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["press"]},
                    "key": {"type": "string"},
                    "presses": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["type", "key"],
            },
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["hotkey"]},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                    },
                },
                "required": ["type", "keys"],
            },
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["scroll"]},
                    "amount": {"type": "integer"},
                },
                "required": ["type", "amount"],
            },
        ]
    }
    @registry.tool(
        name="get_screen_size",
        description="Obtém as dimensões da área de trabalho para planejar uma interação visual.",
        parameters={"type": "object", "properties": {}},
        category="Automação",
    )
    async def get_screen_size() -> dict[str, int]:
        gui = await asyncio.to_thread(_pyautogui)
        size = await asyncio.to_thread(gui.size)
        if size.width <= 0 or size.height <= 0:
            return explicit_failure(
                "invalid_screen_size", verification="non_positive_desktop_dimensions"
            )
        return explicit_success(
            {"width": size.width, "height": size.height},
            verification="positive_desktop_dimensions_read_back",
        )

    @registry.tool(
        name="open_discord_conversation",
        description=(
            "Abre o Discord e usa a busca rápida para entrar na conversa de um contato pelo nome. "
            "Não envia nenhuma mensagem."
        ),
        parameters={
            "type": "object",
            "properties": {"contact": {"type": "string"}},
            "required": ["contact"],
        },
        category="Comunicação",
    )
    async def open_discord_conversation(
        contact: str, _cancellation_token: CancellationToken | None = None
    ) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(discord_conversation, contact, _cancellation_token)
        )

    @registry.tool(
        name="desktop_automation",
        description=(
            "Executa uma sequência confirmada de cliques, digitação, teclas, atalhos, rolagem e espera "
            "na interface visível. Capture e analise a tela antes de escolher coordenadas."
        ),
        parameters={
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": action_schema,
                    "minItems": 1,
                    "maxItems": 25,
                }
            },
            "required": ["actions"],
        },
        permission_level=PermissionLevel.RESTRICTED,
        category="Automação",
        confirmation_text="Quer que eu execute essa sequência na sua tela, senhor?",
    )
    async def desktop_automation(
        actions: list[dict[str, Any]],
        _cancellation_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(run_actions, actions, _cancellation_token)
        )

    @registry.tool(
        name="whatsapp_message",
        description=(
            "Localiza um contato pelo nome no aplicativo WhatsApp, digita uma mensagem e opcionalmente "
            "a envia. Requer WhatsApp instalado e conectado."
        ),
        parameters={
            "type": "object",
            "properties": {
                "contact": {"type": "string"},
                "message": {"type": "string"},
                "send": {"type": "boolean"},
            },
            "required": ["contact", "message"],
        },
        permission_level=PermissionLevel.RESTRICTED,
        category="Comunicação",
        confirmation_text="Quer que eu digite essa mensagem no WhatsApp e a envie se solicitado, senhor?",
    )
    async def whatsapp_message_tool(
        contact: str,
        message: str,
        send: bool = False,
        _cancellation_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        return explicit_success(
            await asyncio.to_thread(
                whatsapp_message, contact, message, send, _cancellation_token
            )
        )
