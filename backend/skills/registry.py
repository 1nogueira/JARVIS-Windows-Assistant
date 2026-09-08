from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from threading import RLock
from typing import Any

from backend.skills.models import SkillManifest, SkillSummary


class SkillRegistry:
    """Discovers inert manifests; instruction text is loaded only on selection."""

    def __init__(self, roots: list[Path] | None = None, *, max_composition_depth: int = 3) -> None:
        self.roots = roots or [Path(__file__).resolve().parent / "builtin"]
        self.max_composition_depth = max_composition_depth
        self._summaries: dict[str, SkillSummary] = {}
        self._manifest_paths: dict[str, Path] = {}
        self._lock = RLock()

    def discover(self) -> list[SkillSummary]:
        discovered: dict[str, SkillSummary] = {}
        paths: dict[str, Path] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for manifest_path in sorted(root.glob("*/manifest.json")):
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary = self._summary(data, manifest_path)
                if summary.name in discovered:
                    raise ValueError(f"Skill duplicada: {summary.name}")
                discovered[summary.name] = summary
                paths[summary.name] = manifest_path
        self._validate_dependencies(discovered)
        with self._lock:
            self._summaries = discovered
            self._manifest_paths = paths
        return self.list()

    def list(self) -> list[SkillSummary]:
        with self._lock:
            return sorted(self._summaries.values(), key=lambda item: (item.category, item.title))

    def get(self, name: str, *, load_details: bool = False) -> SkillSummary | SkillManifest | None:
        with self._lock:
            summary = self._summaries.get(name)
            path = self._manifest_paths.get(name)
        if not summary or not load_details or not path:
            return summary
        data = json.loads(path.read_text(encoding="utf-8"))
        instructions_path = path.parent / str(data.get("instructions_file", "instructions.md"))
        instructions = (
            instructions_path.read_text(encoding="utf-8")
            if instructions_path.is_file()
            else str(data.get("instructions") or "")
        )
        return SkillManifest(summary=summary, instructions=instructions, path=str(path.parent))

    def match(self, text: str, *, limit: int = 4) -> list[SkillSummary]:
        normalized = _normalize(text)
        scored: list[tuple[int, SkillSummary]] = []
        for skill in self.list():
            score = sum(2 if " " in key else 1 for key in skill.keywords if key in normalized)
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [item for _, item in scored[:limit]]

    def compose(self, name: str, *, depth: int = 0, seen: set[str] | None = None) -> list[SkillManifest]:
        if depth > self.max_composition_depth:
            raise ValueError("Profundidade máxima de composição de skills excedida.")
        active = set(seen or set())
        if name in active:
            raise ValueError(f"Ciclo de composição de skills: {name}")
        active.add(name)
        manifest = self.get(name, load_details=True)
        if not isinstance(manifest, SkillManifest):
            raise KeyError(f"Skill não encontrada: {name}")
        result = [manifest]
        for child in manifest.summary.subskills:
            result.extend(self.compose(child, depth=depth + 1, seen=active))
        return result

    @staticmethod
    def _summary(data: dict[str, Any], path: Path) -> SkillSummary:
        name = str(data.get("name") or path.parent.name).strip().casefold()
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in name):
            raise ValueError(f"Nome de skill inválido em {path}")
        return SkillSummary(
            name=name,
            title=str(data.get("title") or name.replace("_", " ").title()),
            description=str(data.get("description") or ""),
            tools=tuple(str(item) for item in data.get("tools", [])),
            capabilities=tuple(str(item) for item in data.get("capabilities", [])),
            keywords=tuple(_normalize(str(item)) for item in data.get("keywords", [])),
            category=str(data.get("category") or "General"),
            version=str(data.get("version") or "1.0.0"),
            deterministic=bool(data.get("deterministic", False)),
            subskills=tuple(str(item) for item in data.get("subskills", [])),
            metadata=dict(data.get("metadata") or {}),
        )

    def _validate_dependencies(self, skills: dict[str, SkillSummary]) -> None:
        for skill in skills.values():
            missing = [name for name in skill.subskills if name not in skills]
            if missing:
                raise ValueError(f"Skill {skill.name} referencia subskills ausentes: {missing}")


def _normalize(value: str) -> str:
    return (
        unicodedata.normalize("NFKD", value.casefold())
        .encode("ascii", "ignore")
        .decode()
    )

