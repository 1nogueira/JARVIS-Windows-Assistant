from __future__ import annotations

import asyncio
import io
import re
import threading
import time
import unicodedata
import wave
from collections import deque
from difflib import SequenceMatcher
from typing import Any

from backend.core.config import SettingsStore
from backend.core.events import EventBus


class WakeWordService:
    """Lightweight openWakeWord listener, loaded only when enabled by the user."""

    def __init__(self, settings: SettingsStore, events: EventBus) -> None:
        self.settings = settings
        self.events = events
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._running = False
        self._command_muted = False
        self._last_error = ""

    def status(self) -> dict[str, Any]:
        try:
            import openwakeword  # noqa: F401
            import sounddevice  # noqa: F401

            available = True
        except ImportError:
            available = False
        return {
            "available": available,
            "running": self._running,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "paused": self._paused.is_set(),
            "engine": "openWakeWord",
            "wake_word": self.settings.section("voice").get("wake_word", "jarvis"),
            "keyword_fallback": True,
            "microphone_muted": self._command_muted,
            "last_error": self._last_error,
        }

    async def start(self) -> dict[str, Any]:
        if not self.settings.section("voice").get("wake_word_enabled", False):
            return self.status()
        if self._running and self._thread and self._thread.is_alive():
            self._paused.clear()
            return self.status()
        if not self.status()["available"]:
            raise RuntimeError("openWakeWord ou sounddevice não está instalado.")
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._paused.clear()
        self._last_error = ""
        self._running = True
        self._thread = threading.Thread(target=self._listen, args=(loop,), daemon=True, name="jarvis-wakeword")
        self._thread.start()
        return self.status()

    async def restart(self) -> dict[str, Any]:
        await self.stop()
        if not self.settings.section("voice").get("wake_word_enabled", False):
            return self.status()
        return await self.start()

    async def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 2)
        self._running = False
        self._paused.clear()
        return self.status()

    async def pause(self) -> dict[str, Any]:
        self._paused.set()
        await self.events.publish("wakeword.paused")
        return self.status()

    async def resume(self) -> dict[str, Any]:
        if not self._running:
            if not self.settings.section("voice").get("wake_word_enabled", False):
                return self.status()
            return await self.start()
        self._paused.clear()
        await self.events.publish("wakeword.resumed")
        return self.status()

    async def mute_commands(self) -> dict[str, Any]:
        self._command_muted = True
        self._paused.clear()
        await self.events.publish("microphone.muted")
        return self.status()

    async def unmute_commands(self) -> dict[str, Any]:
        self._command_muted = False
        self._paused.clear()
        await self.events.publish("microphone.unmuted")
        return self.status()

    def _listen(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model

            config = self.settings.section("voice")
            threshold = float(config.get("sensitivity", 0.55))
            maximum_vad_threshold = max(80, int(config.get("wake_vad_threshold", 550)))
            requested = str(config.get("wake_word", "jarvis")).casefold()
            device = config.get("microphone") or None
            model = Model()
            pre_roll: deque[bytes] = deque(maxlen=8)
            utterance: list[bytes] = []
            speech_frames = 0
            silence_frames = 0
            speech_active = False
            noise_floor = 50.0
            calibration_frames = 0
            while not self._stop.is_set():
                if self._paused.is_set():
                    time.sleep(0.1)
                    continue
                try:
                    with sd.RawInputStream(
                        samplerate=16000,
                        channels=1,
                        dtype="int16",
                        blocksize=1280,
                        device=device,
                    ) as stream:
                        self._last_error = ""
                        while not self._stop.is_set() and not self._paused.is_set():
                            data, overflowed = stream.read(1280)
                            if overflowed:
                                continue
                            samples = np.frombuffer(data, dtype=np.int16)
                            predictions = model.predict(samples)
                            score = max(
                                (
                                    float(value)
                                    for key, value in predictions.items()
                                    if requested in key.casefold()
                                ),
                                default=0.0,
                            )
                            if score >= threshold and not self._command_muted:
                                self._paused.set()
                                asyncio.run_coroutine_threadsafe(
                                    self.events.publish(
                                        "wakeword.detected",
                                        {"word": requested, "score": score, "engine": "openWakeWord"},
                                    ),
                                    loop,
                                )
                                break

                            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                            pre_roll.append(bytes(data))
                            if not speech_active and calibration_frames < 10:
                                noise_floor = noise_floor * 0.8 + rms * 0.2
                                calibration_frames += 1
                                continue
                            start_threshold = min(
                                float(maximum_vad_threshold),
                                max(70.0, noise_floor * 2.4 + 25.0),
                            )
                            silence_threshold = min(
                                float(maximum_vad_threshold),
                                max(55.0, noise_floor * 1.8 + 18.0),
                            )
                            if not speech_active:
                                if rms < start_threshold:
                                    noise_floor = noise_floor * 0.96 + rms * 0.04
                                    speech_frames = 0
                                else:
                                    speech_frames += 1
                                if speech_frames >= 2:
                                    speech_active = True
                                    utterance = list(pre_roll)
                                    silence_frames = 0
                            else:
                                utterance.append(bytes(data))
                                silence_frames = silence_frames + 1 if rms < silence_threshold else 0
                                if len(utterance) > 80:
                                    utterance = []
                                    speech_active = False
                                    speech_frames = 0
                                    silence_frames = 0
                                elif silence_frames >= 8:
                                    intent, transcript = self._transcribe_wake_intent(
                                        utterance, requested
                                    )
                                    utterance = []
                                    speech_active = False
                                    speech_frames = 0
                                    silence_frames = 0
                                    if intent:
                                        self._paused.set()
                                        if intent == "unmute":
                                            self._command_muted = False
                                            event_name = "wakeword.microphone_unmuted"
                                        elif intent == "special":
                                            event_name = "wakeword.special"
                                        else:
                                            event_name = "wakeword.detected"
                                        event_data = {
                                            "word": requested,
                                            "score": 1.0,
                                            "engine": "whisper.cpp",
                                            "transcript": transcript,
                                        }
                                        if intent == "special":
                                            event_data["greeting"] = str(
                                                config.get(
                                                    "special_wake_greeting",
                                                    "Bem-vindo, senhor. Quais projetos temos para hoje?",
                                                )
                                            )
                                        asyncio.run_coroutine_threadsafe(
                                            self.events.publish(event_name, event_data), loop
                                        )
                                        break
                                    # Reopen after transcription to discard buffered audio.
                                    break
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    self._last_error = str(exc)
                    asyncio.run_coroutine_threadsafe(
                        self.events.publish("wakeword.error", {"error": self._last_error}),
                        loop,
                    )
                    time.sleep(1.0)
        except Exception as exc:
            self._last_error = str(exc)
            asyncio.run_coroutine_threadsafe(
                self.events.publish("wakeword.error", {"error": self._last_error}),
                loop,
            )
        finally:
            self._running = False

    def _transcribe_wake_intent(
        self, frames: list[bytes], requested: str
    ) -> tuple[str | None, str]:
        if not 5 <= len(frames) <= 80:
            return None, ""
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"".join(frames))
            wav_data = buffer.getvalue()
        try:
            from backend.voice.stt import WhisperCppSTT

            transcript = WhisperCppSTT(self.settings)._transcribe_sync(wav_data)
        except (OSError, RuntimeError):
            return None, ""
        special = str(
            self.settings.section("voice").get(
                "special_wake_phrase", "acorda criança o papai chegou"
            )
        )
        if self._command_muted:
            if microphone_unmute_phrase_matches(transcript):
                return "unmute", transcript
            return None, transcript
        if special_wake_phrase_matches(transcript, special):
            return "special", transcript
        if wake_word_matches(transcript, requested):
            return "standard", transcript
        return None, transcript

    def _transcribe_wake_word(self, frames: list[bytes], requested: str) -> bool:
        intent, _ = self._transcribe_wake_intent(frames, requested)
        return intent is not None


def wake_word_matches(transcript: str, requested: str = "jarvis") -> bool:
    normalized = unicodedata.normalize("NFKD", transcript.casefold()).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9 ]", " ", normalized)
    compact = re.sub(r"\s+", "", normalized)
    target = re.sub(r"[^a-z0-9]", "", requested.casefold())
    variants = {target, "jairvis", "jarviste", "jarvis", "jervis", "jarves"}
    return any(variant and variant in compact for variant in variants)


def special_wake_phrase_matches(
    transcript: str, requested: str = "acorda criança o papai chegou"
) -> bool:
    def normalize(value: str) -> str:
        plain = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9 ]", " ", plain)

    heard = re.sub(r"\s+", " ", normalize(transcript)).strip()
    target = re.sub(r"\s+", " ", normalize(requested)).strip()
    if not heard or not target:
        return False
    if target in heard:
        return True
    anchors = (
        any(word in heard.split() for word in ("acorda", "corda")),
        any(word in heard.split() for word in ("crianca", "criança")),
        "papai" in heard.split(),
        any(word in heard.split() for word in ("chegou", "chego")),
    )
    return sum(anchors) >= 3 and SequenceMatcher(None, heard, target).ratio() >= 0.68


def microphone_unmute_phrase_matches(transcript: str) -> bool:
    normalized = unicodedata.normalize("NFKD", transcript.casefold()).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9 ]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    has_jarvis = wake_word_matches(normalized, "jarvis")
    has_enable = bool(re.search(r"\b(?:ligar|ligue|ativar|ative|desmutar|desmute)\b", normalized))
    has_microphone = bool(re.search(r"\bmicrofone\b", normalized))
    return has_jarvis and has_enable and has_microphone
