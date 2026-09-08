from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[2]
if getattr(sys, "frozen", False):
    BUNDLED_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
    ROOT_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "JARVIS"
else:
    BUNDLED_ROOT = SOURCE_ROOT
    ROOT_DIR = SOURCE_ROOT

BUNDLED_SETTINGS_PATH = BUNDLED_ROOT / "config" / "settings.example.json"
DEFAULT_SETTINGS_PATH = ROOT_DIR / "config" / "settings.json"
if not DEFAULT_SETTINGS_PATH.exists() and BUNDLED_SETTINGS_PATH.exists():
    DEFAULT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLED_SETTINGS_PATH, DEFAULT_SETTINGS_PATH)


class SettingsStore:
    """Thread-safe JSON settings with conservative environment overrides."""

    def __init__(self, path: Path = DEFAULT_SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = {}
        self.reload()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                raise FileNotFoundError(f"Arquivo de configuração ausente: {self.path}")
            current = json.loads(self.path.read_text(encoding="utf-8-sig"))
            defaults = self._bundled_defaults()
            self._settings = _deep_merge(defaults, current)
            self._backfill_model_names(defaults)
            self._apply_runtime_voice_paths()
            self._apply_environment()
            return deepcopy(self._settings)

    @staticmethod
    def _bundled_defaults() -> dict[str, Any]:
        if not BUNDLED_SETTINGS_PATH.exists():
            return {}
        return json.loads(BUNDLED_SETTINGS_PATH.read_text(encoding="utf-8-sig"))

    def _backfill_model_names(self, defaults: dict[str, Any]) -> None:
        configured = self._settings.setdefault("ollama", {})
        default_models = defaults.get("ollama", {})
        for key in ("chat_model", "vision_model", "embedding_model"):
            if not str(configured.get(key, "")).strip() and default_models.get(key):
                configured[key] = default_models[key]

    def _apply_runtime_voice_paths(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        voice = self._settings.setdefault("voice", {})
        bundled_paths = {
            "stt_executable": BUNDLED_ROOT / "data" / "voice" / "whisper" / "Release" / "whisper-cli.exe",
            "stt_model": BUNDLED_ROOT / "data" / "voice" / "whisper" / "ggml-small.bin",
            "piper_executable": BUNDLED_ROOT / "data" / "voice" / "piper" / "piper" / "piper.exe",
            "piper_model": BUNDLED_ROOT / "data" / "voice" / "piper" / "pt_BR-faber-medium.onnx",
        }
        for key, path in bundled_paths.items():
            configured = Path(str(voice.get(key) or ""))
            if path.is_file() and not configured.is_file():
                voice[key] = str(path)

    def _apply_environment(self) -> None:
        mapping = {
            "JARVIS_OLLAMA_URL": ("ollama", "url"),
            "JARVIS_SEARXNG_URL": ("search", "searxng_url"),
        }
        for env_name, path in mapping.items():
            value = os.getenv(env_name)
            if value:
                self._settings.setdefault(path[0], {})[path[1]] = value

    def all(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings.get(name, {}))

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._settings = _deep_merge(self._settings, changes)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp.replace(self.path)
            return deepcopy(self._settings)


def _deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


settings = SettingsStore()
