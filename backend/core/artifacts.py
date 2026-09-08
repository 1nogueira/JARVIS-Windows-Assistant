from __future__ import annotations

import os
from pathlib import Path

from backend.core.config import ROOT_DIR, SettingsStore


PLACEHOLDER_PARTS = {
    "caminho\\do\\arquivo",
    "caminho\\da\\area\\de\\trabalho",
    "caminho\\da\\área\\de\\trabalho",
}


def artifact_directory(settings: SettingsStore) -> Path:
    configured = str(settings.section("storage").get("artifact_directory", "")).strip()
    target = (
        Path(os.path.expandvars(configured)).expanduser()
        if configured
        else ROOT_DIR / "data" / "artifacts"
    )
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_artifact_path(settings: SettingsStore, value: str) -> Path:
    raw = os.path.expandvars(value.strip().strip('"\''))
    lowered = raw.casefold().replace("/", "\\")
    if not raw or any(part in lowered for part in PLACEHOLDER_PARTS):
        raise ValueError("Informe um nome de arquivo real; caminhos de exemplo não são aceitos.")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    root = artifact_directory(settings)
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise ValueError("O caminho relativo precisa permanecer na pasta de arquivos do JARVIS.")
    return target


def latest_artifact(settings: SettingsStore) -> Path:
    root = artifact_directory(settings)
    files = [item for item in root.rglob("*") if item.is_file()]
    if not files:
        raise FileNotFoundError(f"Nenhum arquivo foi criado ainda em {root}.")
    return max(files, key=lambda item: item.stat().st_mtime)
