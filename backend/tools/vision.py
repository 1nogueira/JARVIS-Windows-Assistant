from __future__ import annotations

from typing import Any

from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success
from backend.vision.analyzer import VisionAnalyzer
from backend.vision.screenshot import ScreenshotService


def register_vision_tools(
    registry: ToolRegistry, screenshots: ScreenshotService, analyzer: VisionAnalyzer
) -> None:
    @registry.tool(
        name="capture_screen",
        description="Tira uma captura da tela inteira somente quando solicitada ou necessária à tarefa.",
        parameters={"type": "object", "properties": {}},
        permission_level=PermissionLevel.SAFE,
        category="Visão",
    )
    async def capture_screen() -> dict[str, Any]:
        return explicit_success(await screenshots.capture_screen())

    @registry.tool(
        name="capture_window",
        description="Captura somente a janela que está em primeiro plano.",
        parameters={"type": "object", "properties": {}},
        permission_level=PermissionLevel.SAFE,
        category="Visão",
    )
    async def capture_window() -> dict[str, Any]:
        return explicit_success(await screenshots.capture_window())

    @registry.tool(
        name="analyze_screen",
        description="Captura e analisa a tela atual com o modelo multimodal local configurado.",
        parameters={
            "type": "object",
            "properties": {"question": {"type": "string"}, "path": {"type": "string"}},
            "required": ["question"],
        },
        permission_level=PermissionLevel.SAFE,
        category="Visão",
    )
    async def analyze_screen(question: str, path: str | None = None) -> dict[str, Any]:
        return explicit_success(await analyzer.analyze(question, path))
