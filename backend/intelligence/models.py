from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from backend.engines.base import InferenceEngine
from backend.intelligence.hardware import HardwareProfile, detect_hardware


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str
    engine: str
    size_bytes: int = 0
    parameter_billions: float | None = None
    context_length: int | None = None
    tool_calling: bool = False
    vision: bool = False
    embeddings: bool = False
    approximate_ram_gb: float | None = None
    approximate_vram_gb: float | None = None
    available: bool = True
    modified_at: str | None = None

    def public_dict(self, *, selected: bool = False, recommended: bool = False) -> dict[str, Any]:
        return {**asdict(self), "selected": selected, "recommended": recommended}


class ModelCatalog:
    """Runtime model catalog populated from engine discovery, never per inference."""

    def __init__(self, engine: InferenceEngine, hardware: HardwareProfile | None = None) -> None:
        self.engine = engine
        self.hardware = hardware or detect_hardware()
        self._models: dict[str, ModelInfo] = {}
        self.updated_at: str | None = None

    async def refresh(self, *, force: bool = False) -> list[ModelInfo]:
        discovered = await self.engine.list_models(force=force)
        self._models = {
            info.name: info
            for raw in discovered
            if (info := self._from_engine(raw)).name
        }
        self.updated_at = datetime.now(UTC).isoformat()
        return self.list()

    def list(self) -> list[ModelInfo]:
        return sorted(self._models.values(), key=lambda item: item.name.casefold())

    def get(self, name: str) -> ModelInfo | None:
        return self._models.get(name)

    def recommended_name(self) -> str | None:
        models = self.list()
        if not models:
            return None
        budget = self.hardware.available_vram_gb or self.hardware.ram_gb * 0.55
        fitting = [
            model for model in models
            if model.approximate_ram_gb is None or model.approximate_ram_gb <= budget
        ]
        candidates = fitting or models
        return max(
            candidates,
            key=lambda item: (item.parameter_billions or 0, -item.size_bytes),
        ).name

    def public_dict(self, selected: str = "") -> dict[str, Any]:
        recommended = self.recommended_name()
        return {
            "engine": self.engine.engine_id,
            "updated_at": self.updated_at,
            "hardware": self.hardware.public_dict(),
            "models": [
                item.public_dict(
                    selected=item.name == selected,
                    recommended=item.name == recommended,
                )
                for item in self.list()
            ],
            "selected": selected,
            "recommended": recommended,
        }

    def _from_engine(self, raw: dict[str, Any]) -> ModelInfo:
        name = str(raw.get("name") or raw.get("model") or "")
        size = int(raw.get("size") or 0)
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        parameters = _parameter_count(name, str(details.get("parameter_size") or ""))
        lower = name.casefold()
        return ModelInfo(
            name=name,
            engine=self.engine.engine_id,
            size_bytes=size,
            parameter_billions=parameters,
            tool_calling=any(term in lower for term in ("qwen", "llama3", "mistral", "granite")),
            vision=any(term in lower for term in ("vision", "-vl", "llava", "moondream")),
            embeddings=any(term in lower for term in ("embed", "nomic", "bge", "snowflake")),
            approximate_ram_gb=round(size / (1024**3) * 1.18, 1) if size else None,
            approximate_vram_gb=round(size / (1024**3) * 1.05, 1) if size else None,
            available=True,
            modified_at=str(raw.get("modified_at") or "") or None,
        )


def _parameter_count(name: str, declared: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", declared) or re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*[bB](?![a-z])", name
    )
    return float(match.group(1)) if match else None

