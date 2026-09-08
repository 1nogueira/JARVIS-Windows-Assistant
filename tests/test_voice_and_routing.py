from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.tool_orchestrator import (
    address_user,
    clean_model_content,
    fresh_search_query,
    requires_fresh_web_search,
    select_tool_names,
)
from backend.voice.audio_manager import AudioManager
from backend.voice.stt import clean_transcript
from backend.tools.weather import canonicalize_location
from backend.voice.wakeword import (
    WakeWordService,
    microphone_unmute_phrase_matches,
    special_wake_phrase_matches,
    wake_word_matches,
)
from backend.core.events import EventBus


def test_casual_message_does_not_send_all_tool_schemas() -> None:
    assert select_tool_names("Boa noite, como você está?") == set()


def test_tool_routing_selects_only_relevant_groups() -> None:
    windows = select_tool_names("Abra a calculadora e ajuste o volume")
    assert "open_app" in windows
    assert "set_volume" in windows
    assert "web_search" not in windows

    youtube = select_tool_names("Abra o YouTube")
    assert "open_website" in youtube
    assert "open_app" not in youtube

    weather = select_tool_names("Quantos graus está em Lisboa?")
    assert weather == {"get_current_weather"}

    system = select_tool_names("Quanto de CPU e RAM o computador está usando?")
    assert system == {"get_system_metrics", "diagnose_system_usage"}

    processes = select_tool_names("Qual programa está usando mais memória?")
    assert "get_running_processes" in processes


def test_model_reasoning_is_removed_from_visible_answer() -> None:
    assert clean_model_content("raciocinio interno </think> resposta final") == "resposta final"
    assert clean_model_content("<think>segredo</think>resposta limpa") == "resposta limpa"


def test_user_is_addressed_as_senhor_without_duplication() -> None:
    assert address_user("Resposta pronta.") == "Senhor, resposta pronta."
    assert address_user("Pois não, senhor.") == "Pois não, senhor."


def test_common_jarvis_wakeword_misrecognition_is_corrected() -> None:
    assert clean_transcript("O lajar viste, abra o YouTube") == "Jarvis, abra o YouTube"
    assert clean_transcript("Jairvis.") == "Jarvis."
    assert wake_word_matches("Jairvis.")
    assert not wake_word_matches("Abra a Netflix")


def test_transcript_cleanup_preserves_spoken_city_names() -> None:
    assert clean_transcript("Quantos  graus em Lisboa?") == "Quantos graus em Lisboa?"
    assert clean_transcript("E em Porto?") == "E em Porto?"


@pytest.mark.parametrize("location", ["Lisboa", "LÍSBOA", " lisboa, pt! "])
def test_location_normalization_uses_configured_city_context(location: str) -> None:
    assert canonicalize_location(location, "Lisboa, PT") == "Lisboa, PT"


@pytest.mark.parametrize(
    ("location", "default_location"),
    [
        ("Porto", "Lisboa, PT"),
        ("São Pedro", "São Paulo"),
        ("London", "Londonderry"),
        ("Paris, Texas", "Paris, France"),
        ("Lisboa", ""),
    ],
)
def test_configured_city_does_not_override_another_location(
    location: str, default_location: str
) -> None:
    assert canonicalize_location(location, default_location) == location


def test_spoken_file_extension_is_normalized() -> None:
    assert clean_transcript("Exclua o arquivo exemplo ponto txt") == "Exclua o arquivo exemplo.txt"


def test_special_wake_phrase_accepts_small_transcription_variations() -> None:
    assert special_wake_phrase_matches("Acorda criança, o papai chegou.")
    assert special_wake_phrase_matches("Acorda crianca papai chegou")
    assert special_wake_phrase_matches("Acorda criança que o papai chego")
    assert not special_wake_phrase_matches("O papai foi ao mercado")


def test_muted_listener_requires_complete_unmute_phrase() -> None:
    assert microphone_unmute_phrase_matches("Jarvis, ligar microfone")
    assert not microphone_unmute_phrase_matches("ligar microfone")
    assert not microphone_unmute_phrase_matches("Jarvis")


def test_recent_sports_questions_force_official_web_search() -> None:
    question = "Qual foi a última vez que o Corinthians foi campeão?"
    assert requires_fresh_web_search(question)
    query = fresh_search_query(question)
    assert "site:cbf.com.br" in query
    assert "2026" in query


def test_ffmpeg_is_found_in_winget_package(monkeypatch, tmp_path: Path) -> None:
    executable = (
        tmp_path
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-9.0-full_build"
        / "bin"
        / "ffmpeg.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test")
    monkeypatch.delenv("JARVIS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("backend.voice.audio_manager.shutil.which", lambda _: None)

    assert AudioManager.find_ffmpeg() == str(executable)


async def test_muting_commands_does_not_start_disabled_listener(settings_store) -> None:
    settings_store.update({"voice": {"wake_word_enabled": False}})
    service = WakeWordService(settings_store, EventBus())
    muted = await service.mute_commands()
    assert muted["microphone_muted"] is True
    assert muted["running"] is False
    unmuted = await service.unmute_commands()
    assert unmuted["microphone_muted"] is False
    assert unmuted["running"] is False


async def test_wakeword_restart_applies_latest_runtime_configuration(
    monkeypatch, settings_store
) -> None:
    service = WakeWordService(settings_store, EventBus())
    observed = []

    async def fake_stop():
        observed.append(("stop", None))
        return service.status()

    async def fake_start():
        observed.append(("start", settings_store.section("voice")["wake_word"]))
        return service.status()

    monkeypatch.setattr(service, "stop", fake_stop)
    monkeypatch.setattr(service, "start", fake_start)
    settings_store.update(
        {"voice": {"wake_word_enabled": True, "wake_word": "computador"}}
    )
    await service.restart()
    assert observed == [("stop", None), ("start", "computador")]
