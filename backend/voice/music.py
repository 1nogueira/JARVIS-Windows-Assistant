from __future__ import annotations

import ctypes
import platform
import threading
from pathlib import Path
from typing import Any

from backend.core.config import SettingsStore
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_failure, explicit_success


class MusicService:
    """Small Windows-native MP3 player used by the special wake phrase."""

    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings
        self.alias = "jarvis_theme"
        self._lock = threading.RLock()
        self._playing = False

    def status(self) -> dict[str, Any]:
        path = self._configured_path()
        mode = "closed"
        if platform.system() == "Windows":
            with self._lock:
                mode = self._mode()
                self._playing = mode == "playing"
        return {
            "available": platform.system() == "Windows" and path.is_file(),
            "path": str(path),
            "playing": mode == "playing",
            "paused": mode == "paused",
            "mode": mode,
            "engine": "Windows MCI",
        }

    async def play(self) -> dict[str, Any]:
        # MCI commands must stay on the thread that opened the device.
        return self._play_sync()

    async def stop(self) -> dict[str, Any]:
        return self._stop_sync()

    async def pause(self) -> dict[str, Any]:
        return self._pause_sync()

    async def resume(self) -> dict[str, Any]:
        return self._resume_sync()

    async def restart(self) -> dict[str, Any]:
        return self._restart_sync()

    def _configured_path(self) -> Path:
        value = str(self.settings.section("voice").get("special_wake_music", "")).strip()
        return Path(value).expanduser()

    def _play_sync(self) -> dict[str, Any]:
        path = self._configured_path()
        if platform.system() != "Windows":
            raise RuntimeError("A música especial usa o reprodutor nativo do Windows.")
        if not path.is_file():
            raise RuntimeError(f"Música especial não encontrada: {path}")
        if path.suffix.casefold() not in {".mp3", ".wav", ".wma"}:
            raise RuntimeError("O arquivo da música especial precisa ser MP3, WAV ou WMA.")
        volume = int(self.settings.section("voice").get("special_wake_music_volume", 32))
        mci_volume = max(0, min(volume, 100)) * 10
        with self._lock:
            self._send(f"close {self.alias}", ignore_error=True)
            safe_path = str(path.resolve()).replace('"', "")
            self._send(f'open "{safe_path}" type mpegvideo alias {self.alias}')
            try:
                self._send(f"setaudio {self.alias} volume to {mci_volume}")
                self._send(f"play {self.alias} from 0")
            except RuntimeError:
                self._send(f"close {self.alias}", ignore_error=True)
                raise
            self._playing = True
        return {"playing": True, "path": str(path.resolve()), "volume_percent": volume}

    def _stop_sync(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            self._playing = False
            return {"playing": False}
        with self._lock:
            self._send(f"stop {self.alias}", ignore_error=True)
            self._send(f"close {self.alias}", ignore_error=True)
            self._playing = False
        return {"playing": False}

    def _pause_sync(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            return {"playing": False, "paused": False}
        with self._lock:
            mode = self._mode()
            if mode == "playing":
                self._send(f"pause {self.alias}")
                self._playing = False
                return {"playing": False, "paused": True, "changed": True}
            return {
                "playing": False,
                "paused": mode == "paused",
                "changed": False,
            }

    def _resume_sync(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            return {"playing": False, "paused": False}
        with self._lock:
            mode = self._mode()
            if mode == "paused":
                self._send(f"resume {self.alias}")
                self._playing = True
                return {"playing": True, "paused": False, "changed": True}
            return {
                "playing": mode == "playing",
                "paused": False,
                "changed": False,
            }

    def _restart_sync(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            return {"playing": False, "paused": False}
        with self._lock:
            mode = self._mode()
            if mode in {"playing", "paused", "stopped"}:
                self._send(f"play {self.alias} from 0")
                self._playing = True
                return {"playing": True, "paused": False, "changed": True}
        return self._play_sync()

    def _mode(self) -> str:
        mode = self._send(f"status {self.alias} mode", ignore_error=True).casefold()
        if mode not in {"playing", "paused", "stopped", "open", "seeking"}:
            return "closed"
        return mode

    @staticmethod
    def _send(command: str, *, ignore_error: bool = False) -> str:
        output = ctypes.create_unicode_buffer(512)
        winmm = ctypes.windll.winmm
        result = winmm.mciSendStringW(command, output, len(output), 0)
        if result and not ignore_error:
            error = ctypes.create_unicode_buffer(512)
            winmm.mciGetErrorStringW(result, error, len(error))
            raise RuntimeError(error.value or f"Falha do reprodutor do Windows ({result}).")
        return output.value


def register_music_tools(registry: ToolRegistry, music: MusicService) -> None:
    @registry.tool(
        name="control_theme_music",
        description="Toca, pausa, continua, reinicia ou para a música especial do JARVIS.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "resume", "restart", "stop"],
                }
            },
            "required": ["action"],
        },
        category="Áudio",
    )
    async def control_theme_music(action: str) -> dict[str, Any]:
        handlers = {
            "play": music.play,
            "pause": music.pause,
            "resume": music.resume,
            "restart": music.restart,
            "stop": music.stop,
        }
        result = await handlers[action]()
        status = music.status()
        expected = {
            "play": status["playing"],
            "pause": status["paused"],
            "resume": status["playing"],
            "restart": status["playing"],
            "stop": status["mode"] == "closed",
        }[action]
        if not expected:
            return explicit_failure(
                "music_state_not_applied",
                detail=f"O player terminou no estado {status['mode']}, incompatível com {action}.",
                data={"action": action, **result, "status": status},
                verification="MCI_mode_readback_mismatch",
            )
        return explicit_success(
            {"action": action, **result, "status": status},
            verification=f"MCI_mode_read_back:{status['mode']}",
        )
