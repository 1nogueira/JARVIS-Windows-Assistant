from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.artifacts import artifact_directory
from backend.core.config import SettingsStore


class ScreenshotService:
    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings

    @property
    def directory(self) -> Path:
        return artifact_directory(self.settings)

    async def capture_screen(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._capture_screen_sync)

    async def capture_window(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._capture_window_sync)

    def _capture_screen_sync(self) -> dict[str, Any]:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("Pillow não está instalado. Execute setup.ps1.") from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"screen-{stamp}.png"
        image = ImageGrab.grab(all_screens=True)
        image.save(path, format="PNG", optimize=True)
        if not path.is_file() or path.stat().st_size <= 0 or image.width <= 0 or image.height <= 0:
            raise RuntimeError("A captura não produziu um PNG verificável.")
        return {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "size_bytes": path.stat().st_size,
            "verification": "png_exists_nonempty_with_positive_dimensions",
        }

    def _capture_window_sync(self) -> dict[str, Any]:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("Pillow não está instalado. Execute setup.ps1.") from exc
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            raise RuntimeError("Nenhuma janela ativa foi encontrada.")
        rectangle = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            raise RuntimeError("Não foi possível obter as dimensões da janela ativa.")
        bbox = (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"window-{stamp}.png"
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        image.save(path, format="PNG", optimize=True)
        title_length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(handle, title_buffer, title_length + 1)
        if not path.is_file() or path.stat().st_size <= 0 or image.width <= 0 or image.height <= 0:
            raise RuntimeError("A captura da janela não produziu um PNG verificável.")
        return {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "title": title_buffer.value,
            "size_bytes": path.stat().st_size,
            "verification": "png_exists_nonempty_with_positive_dimensions",
        }

    async def delete(self, path: str | Path) -> None:
        target = Path(path).resolve()
        if self.directory.resolve() not in target.parents:
            return
        await asyncio.to_thread(target.unlink, missing_ok=True)
