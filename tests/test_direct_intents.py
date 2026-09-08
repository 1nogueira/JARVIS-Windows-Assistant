from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from backend.skills.deterministic import try_direct_intent
from backend.core.events import EventBus
from backend.core.logging import JsonlAuditLog
from backend.security.confirmations import ConfirmationManager
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success
from backend.tools.windows import open_app


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, name: str, arguments: dict[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "get_current_weather":
            if arguments.get("day_offset") == 1:
                return explicit_success({
                    "success": True,
                    "location": "Lisboa",
                    "state": "Lisboa",
                    "temperature_max_c": 22.2,
                    "temperature_min_c": 14.1,
                    "precipitation_probability_percent": 10,
                    "condition": "parcialmente nublado",
                    "source_url": "https://open-meteo.com/",
                })
            return explicit_success({
                "success": True,
                "location": "Lisboa",
                "state": "Lisboa",
                "temperature_c": 24.5,
                "feels_like_c": 25.0,
                "condition": "parcialmente nublado",
                "source_url": "https://open-meteo.com/",
            })
        if name == "diagnose_system_usage":
            return explicit_success({
                "success": True,
                "metrics": {"ram_percent": 81.0, "cpu_percent": 34.0},
                "top_memory": [
                    {"name": "chrome.exe", "memory_mb": 2300.0, "cpu_percent": 12.0}
                ],
                "top_cpu": [
                    {"name": "game.exe", "memory_mb": 900.0, "cpu_percent": 28.0}
                ],
                "observations": ["O uso de RAM está elevado."],
            })
        if name == "create_reminder":
            return explicit_success({"id": 1, **arguments})
        if name == "list_reminders":
            return explicit_success({
                "success": True,
                "items": [{
                    "id": 7,
                    "title": "Aula de inglês",
                    "due_at": "2026-08-20T21:50:00+00:00",
                    "recurrence": "weekly",
                    "minutes_before": 10,
                }],
            })
        if name == "update_reminder":
            return explicit_success({"title": "Aula de inglês", **arguments})
        return explicit_success({"opened": arguments.get("target") or "Epic Games Launcher"})


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("Abra o YouTube", "open_website"),
        ("Abra uma guia anônima do Google", "open_private_browser"),
        ("Abra a Epic Games", "open_app"),
        (r"Abra o VS Code no arquivo C:\Users\Example\Desktop\teste.py na linha 5", "open_in_vscode"),
        ("Quantos graus está em Lisboa?", "get_current_weather"),
        ("Jarvis abriu Netflix", "open_website"),
        ("Jarvis, esqueça completamente a ação anterior. Abra a Netflix no meu navegador", "open_website"),
        ("Tire um print da minha tela e salve para mim", "capture_screen"),
        ("Jarvis, eu quero que você tire uma captura de tela da minha tela agora", "capture_screen"),
    ],
)
async def test_direct_common_intents_are_unambiguous(
    message: str, expected_tool: str, settings_store
) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(message, registry, settings_store, "request")  # type: ignore[arg-type]
    assert result is not None
    assert result.tool == expected_tool
    assert "senhor" in result.answer.casefold()


async def test_weekly_reminder_uses_the_requested_weekday(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Lembre-me toda quinta-feira às 18h da aula de inglês, avisando 10 minutos antes.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "create_reminder"
    name, arguments = registry.calls[-1]
    assert name == "create_reminder"
    assert datetime.fromisoformat(arguments["due_at"]).weekday() == 3
    assert arguments["recurrence"] == "weekly"
    assert arguments["minutes_before"] == 10
    assert arguments["title"] == "aula de inglês"


async def test_relative_reminder_preserves_seconds_and_purpose(settings_store) -> None:
    registry = FakeRegistry()
    before = datetime.now().astimezone()
    result = await try_direct_intent(
        "Quero que você me avise daqui 10 segundos, um alarme para daqui 10 segundos "
        "você me avisar que eu preciso tomar meu remédio.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "create_reminder"
    name, arguments = registry.calls[-1]
    assert name == "create_reminder"
    due = datetime.fromisoformat(arguments["due_at"])
    assert timedelta(seconds=8) <= due - before <= timedelta(seconds=12)
    assert arguments["title"] == "tomar meu remédio"
    assert "10 segundos" in result.answer


async def test_short_relative_reminder_keeps_medication_purpose(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Quero que você me avise daqui 10 segundos que eu preciso tomar meu remédio",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "create_reminder"
    assert registry.calls[-1][1]["title"] == "tomar meu remédio"


async def test_discord_conversation_is_not_treated_as_application(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Jarvis, abrir conversa com o Usuário Exemplo no Discord.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "open_discord_conversation"
    assert registry.calls[-1] == ("open_discord_conversation", {"contact": "Usuário Exemplo"})


async def test_plain_discord_request_opens_the_installed_app(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Abra meu Discord",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "open_app"
    assert registry.calls == [("open_app", {"query": "Discord"})]


def test_discord_uses_registered_uri_without_slow_start_menu_scan(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr("backend.tools.windows.os.startfile", lambda target: opened.append(target))
    monkeypatch.setattr(
        "backend.tools.windows.discover_apps",
        lambda *args, **kwargs: pytest.fail("Start Menu scan should not run for Discord"),
    )

    result = open_app("meu Discord")

    assert opened == ["discord://"]
    assert result["opened"] == "Discord"


async def test_multiple_open_requests_execute_every_explicit_action(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Abra YouTube, Instagram, o Discord e a Netflix e coloque no Google também "
        "pesquise por Adam Sandler.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert [name for name, _ in registry.calls] == [
        "open_website",
        "open_website",
        "open_app",
        "open_website",
        "open_website",
    ]
    assert registry.calls[0][1] == {"target": "youtube"}
    assert registry.calls[1][1] == {"target": "instagram"}
    assert registry.calls[2][1] == {"query": "Discord"}
    assert registry.calls[3][1] == {"target": "netflix"}
    assert registry.calls[4][1]["target"].endswith("search?q=Adam+Sandler")
    assert len(result.actions) == 5
    assert all(action.status == "completed" for action in result.actions)


async def test_special_phrase_plays_theme_without_model(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Acorda, criança. Papai chegou.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "control_theme_music"
    assert registry.calls[-1] == ("control_theme_music", {"action": "play"})


async def test_explicit_music_control_is_deterministic(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Jarvis, recomece a música do começo.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "control_theme_music"
    assert registry.calls[-1] == ("control_theme_music", {"action": "restart"})


async def test_spoken_delete_filename_resolves_before_confirmation(
    monkeypatch, tmp_path, settings_store
) -> None:
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    target = desktop / "exemplo.txt"
    target.write_text("apagar", encoding="utf-8")
    monkeypatch.setattr("backend.tools.files.desktop_directories", lambda: [desktop])
    registry = FakeRegistry()

    result = await try_direct_intent(
        "Exclua o arquivo exemplo ponto txt da área de trabalho",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "move_to_recycle_bin"
    assert registry.calls[-1] == ("move_to_recycle_bin", {"path": str(target.resolve())})


async def test_existing_reminder_time_is_updated_not_recreated(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Altere o lembrete da aula de inglês para 18:00",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "update_reminder"
    assert [name for name, _ in registry.calls] == ["list_reminders", "update_reminder"]
    arguments = registry.calls[-1][1]
    assert arguments["reminder_id"] == 7
    local_due = datetime.fromisoformat(arguments["due_at"]).astimezone()
    assert (local_due.hour, local_due.minute) == (18, 0)


async def test_matching_city_uses_configured_context_and_tomorrow_forecast(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Quantos graus irá fazer amanhã em Lisboa?",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "get_current_weather"
    assert registry.calls[-1] == (
        "get_current_weather",
        {"location": "Lisboa, PT", "day_offset": 1},
    )
    assert "máxima de 22.2" in result.answer


async def test_explicit_weather_city_is_preserved_when_default_differs(settings_store) -> None:
    settings_store.update({"user": {"default_location": "São Paulo"}})
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Quantos graus está em São Pedro?",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert registry.calls[-1] == (
        "get_current_weather",
        {"location": "São Pedro", "day_offset": 0},
    )


async def test_high_ram_request_runs_real_diagnosis(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Investigue por que minha memória RAM está muito elevada",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "diagnose_system_usage"
    assert registry.calls[-1] == (
        "diagnose_system_usage",
        {"focus": "memory", "limit": 6},
    )
    assert "chrome.exe" in result.answer


async def test_whatsapp_message_uses_saved_mother_contact(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Fale para minha mãe que o Jarvis está respondendo",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "whatsapp_message"
    assert registry.calls[-1] == (
        "whatsapp_message",
        {"contact": "mãe", "message": "o Jarvis está respondendo", "send": True},
    )


async def test_created_text_defaults_to_logs_folder(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        'Crie um arquivo chamado "teste.txt" com conteúdo Olá senhor e abra para eu ver',
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "create_and_open_text_file"
    assert registry.calls[-1][1]["filename"] == "teste.txt"
    assert registry.calls[-1][1]["content"] == "Olá senhor"
    assert registry.calls[-1][1]["directory"] == "artifacts"


async def test_create_any_text_file_on_desktop_is_deterministic(settings_store) -> None:
    registry = FakeRegistry()
    result = await try_direct_intent(
        "Cria o arquivo txt na área de trabalho. Pode ser com qualquer nome, só cria.",
        registry,  # type: ignore[arg-type]
        settings_store,
        "request",
    )
    assert result is not None
    assert result.tool == "create_and_open_text_file"
    _, arguments = registry.calls[-1]
    assert arguments["filename"].startswith("nota-jarvis-")
    assert arguments["filename"].endswith(".txt")
    assert arguments["directory"] == "desktop"
    assert arguments["content"] == ""


async def test_promoted_open_direct_intent_returns_confirmation(
    tmp_path, settings_store
) -> None:
    settings_store.update({"permissions": {"open_website": "CONFIRM"}})
    registry = ToolRegistry(
        settings_store,
        EventBus(),
        ConfirmationManager(),
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )
    opened = []

    @registry.tool(
        name="open_website",
        description="abre site",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
        permission_level=PermissionLevel.SAFE,
    )
    def open_website(target):
        opened.append(target)
        return {"opened": target}

    pending = await try_direct_intent(
        "Abra o YouTube", registry, settings_store, "promoted-open"
    )
    assert pending is not None
    assert pending.status == "awaiting_confirmation"
    assert pending.confirmation_id
    assert opened == []
    await registry.execute_confirmation(pending.confirmation_id, True)
    assert opened == ["youtube"]
