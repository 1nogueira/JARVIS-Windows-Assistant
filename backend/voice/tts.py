from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.core.config import ROOT_DIR, SettingsStore
from backend.core.events import EventBus


class PiperTTS:
    def __init__(self, settings: SettingsStore, events: EventBus) -> None:
        self.settings = settings
        self.events = events
        self.output_dir = ROOT_DIR / "data" / "speech"
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()
        self._playback_stop = threading.Event()

    def status(self) -> dict[str, Any]:
        config = self.settings.section("voice")
        executable = self._executable(config)
        model = Path(str(config.get("piper_model", ""))).expanduser()
        return {
            "available": bool(executable and model.is_file()),
            "engine": "Piper",
            "executable": executable or "",
            "model_configured": model.is_file(),
        }

    def _executable(self, config: dict[str, Any]) -> str | None:
        configured = str(config.get("piper_executable", "")).strip()
        if configured and Path(configured).is_file():
            return configured
        return shutil.which("piper")

    async def synthesize(self, text: str, request_id: str = "voice") -> Path:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._synthesize_sync, text)
            finally:
                self._process = None

    async def speak(self, text: str, request_id: str = "voice") -> Path:
        async with self._lock:
            self._playback_stop.clear()
            await self.events.publish("tts.started", {"request_id": request_id})
            try:
                output = await asyncio.to_thread(self._synthesize_sync, text)
                self._process = None
                if not self._playback_stop.is_set():
                    await asyncio.to_thread(self._play_sync, output)
                return output
            finally:
                self._process = None
                await self.events.publish("tts.completed", {"request_id": request_id})

    def _play_sync(self, path: Path) -> None:
        try:
            import winsound
        except ImportError as exc:
            raise RuntimeError("A reprodução de voz local requer Windows.") from exc
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / max(1, audio.getframerate())
        if self._playback_stop.is_set():
            return
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not self._playback_stop.wait(0.1):
            pass
        winsound.PlaySound(None, 0)

    def _synthesize_sync(self, text: str) -> Path:
        config = self.settings.section("voice")
        executable = self._executable(config)
        model = Path(str(config.get("piper_model", ""))).expanduser().resolve()
        if not executable or not model.is_file():
            raise RuntimeError("Piper ou seu modelo de voz ainda não foram configurados.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output = self.output_dir / f"speech-{uuid4()}.wav"
        speech_rate = max(0.5, min(2.0, float(config.get("speech_rate", 1.0))))
        self._process = subprocess.Popen(
            [
                executable,
                "--model",
                str(model),
                "--length_scale",
                str(round(1.0 / speech_rate, 3)),
                "--output_file",
                str(output),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            _, stderr = self._process.communicate(text.encode("utf-8"), timeout=120)
        except subprocess.TimeoutExpired as exc:
            self._process.terminate()
            self._process.communicate()
            output.unlink(missing_ok=True)
            raise RuntimeError("A síntese de voz ultrapassou 120 segundos.") from exc
        if self._process.returncode != 0 or not output.exists():
            raise RuntimeError(f"Falha no Piper: {stderr.decode(errors='replace')[-300:]}")
        return output

    async def stop(self) -> bool:
        stopped = False
        process = self._process
        self._playback_stop.set()
        if process and process.poll() is None:
            process.terminate()
            stopped = True
        try:
            import winsound

            winsound.PlaySound(None, 0)
            stopped = True
        except (ImportError, RuntimeError):
            pass
        return stopped
