from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Any

from backend.core.config import SettingsStore
from backend.core.i18n import get_language, tr


class WhisperCppSTT:
    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        config = self.settings.section("voice")
        executable = self._executable(config)
        model = Path(str(config.get("stt_model", ""))).expanduser()
        return {
            "available": bool(executable and model.is_file()),
            "engine": "whisper.cpp",
            "executable": executable or "",
            "model_configured": model.is_file(),
        }

    def _executable(self, config: dict[str, Any]) -> str | None:
        configured = str(config.get("stt_executable", "")).strip()
        if configured and Path(configured).is_file():
            return configured
        return shutil.which("whisper-cli")

    async def transcribe(self, wav_data: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_sync, wav_data)

    def _transcribe_sync(self, wav_data: bytes) -> str:
        config = self.settings.section("voice")
        executable = self._executable(config)
        model = Path(str(config.get("stt_model", ""))).expanduser().resolve()
        language = str(config.get("stt_language", "pt")).strip() or "pt"
        threads = max(2, min(12, int(config.get("stt_threads", 8))))
        vocabulary = transcription_prompt(config)
        if not executable or not model.is_file():
            raise RuntimeError(tr("whisper.cpp ou seu modelo ainda não foram configurados.", "whisper.cpp or its model has not been configured yet.", language=get_language(self.settings)))
        with tempfile.TemporaryDirectory(prefix="jarvis-stt-") as directory:
            source = Path(directory) / "speech.wav"
            output = Path(directory) / "transcript"
            source.write_bytes(wav_data)
            completed = subprocess.run(
                [
                    executable, "-m", str(model), "-f", str(source),
                    "-l", language, "-t", str(threads), "--prompt", vocabulary,
                    "-otxt", "-of", str(output), "-nt",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            transcript = output.with_suffix(".txt")
            if completed.returncode != 0 or not transcript.exists():
                raise RuntimeError(tr(f"Falha na transcrição local: {completed.stderr[-300:]}", f"Local transcription failed: {completed.stderr[-300:]}", language=get_language(self.settings)))
            return clean_transcript(
                transcript.read_text(encoding="utf-8", errors="replace").strip()
            )


def transcription_prompt(config: dict[str, Any]) -> str:
    configured = str(config.get("stt_prompt") or "").strip()
    if configured:
        return configured
    language = str(config.get("stt_language", "pt")).casefold()
    vocabulary = "Jarvis, YouTube, Google, Epic Games."
    if language.startswith("en"):
        return vocabulary + " English."
    if language.startswith("pt"):
        return vocabulary + " Português brasileiro."
    return vocabulary


def clean_transcript(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(
        r"^(?:o\s+)?(?:lajar\s+viste|laja\s+arvis|jar\s+viste|ja\s+viste|jairvis|jervis|jarves)\b",
        "Jarvis",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b([\wÀ-ÿ-]+)\s+(?:ponto|dot)\s+"
        r"(txt|md|json|csv|log|pdf|docx?|xlsx?|png|jpe?g|zip|py|js|ts)\b",
        r"\1.\2",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned
