from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.i18n import tr


_LABELS = {
    "browser": ("Navegador", "Browser", "Abre sites e controla navegação local quando solicitado.", "Open websites and control local browsing when requested."),
    "coding": ("Código local", "Local code", "Lê e edita código com caminhos explícitos e confirmações de mutação.", "Read and edit code with explicit paths and confirmation for changes."),
    "files": ("Arquivos locais", "Local files", "Lê, cria, organiza e abre arquivos preservando o caminho real retornado.", "Read, create, organize and open files while preserving the actual returned path."),
    "media": ("Mídia e áudio", "Media and audio", "Controla mídia, tema e áudio local do Windows.", "Control media, theme music and local Windows audio."),
    "reminders": ("Lembretes e agenda", "Reminders and schedule", "Cria lembretes locais e tarefas recorrentes persistentes.", "Create local reminders and persistent recurring tasks."),
    "system": ("Sistema Windows", "Windows system", "Consulta métricas, processos, áudio, clipboard e configurações do Windows.", "Read Windows metrics, processes, audio, clipboard and settings."),
    "vision": ("Visão local", "Local vision", "Captura e analisa tela, janela ou câmera com ferramentas locais.", "Capture and analyze the screen, a window or camera using local tools."),
    "voice": ("Voz", "Voice", "Integra wake word, push-to-talk, STT, TTS e barge-in locais.", "Integrate local wake word, push-to-talk, speech recognition, synthesis and interruption."),
    "web_research": ("Pesquisa profunda", "Deep research", "Pesquisa fontes múltiplas, cruza evidências e produz resposta citada.", "Research multiple sources, compare evidence and produce a cited answer."),
    "whatsapp": ("WhatsApp", "WhatsApp", "Abre o aplicativo nativo e prepara ou envia mensagens mediante confirmação.", "Open the native application and prepare or send messages with confirmation."),
    "windows_apps": ("Aplicativos do Windows", "Windows applications", "Descobre, abre e gerencia aplicativos instalados sem executar shell arbitrário.", "Find, open and manage installed applications without arbitrary shell execution."),
}
_CATEGORIES = {
    "Desenvolvimento": "Development", "Development": "Development",
    "Produtividade": "Productivity", "Productivity": "Productivity",
    "Voz": "Voice", "Voice": "Voice",
    "Comunicação": "Communication", "Communication": "Communication",
}


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    title: str
    description: str
    tools: tuple[str, ...]
    capabilities: tuple[str, ...]
    keywords: tuple[str, ...]
    category: str = "General"
    version: str = "1.0.0"
    deterministic: bool = False
    subskills: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        labels = _LABELS.get(self.name)
        if labels:
            pt_title, en_title, pt_description, en_description = labels
        else:
            pt_title = en_title = self.title
            pt_description = en_description = self.description
        return {
            "name": self.name,
            "title": tr(pt_title, en_title),
            "description": tr(pt_description, en_description),
            "tools": list(self.tools),
            "capabilities": list(self.capabilities),
            "category": tr(self.category, _CATEGORIES.get(self.category, self.category)),
            "version": self.version,
            "deterministic": self.deterministic,
            "subskills": list(self.subskills),
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class SkillManifest:
    summary: SkillSummary
    instructions: str = ""
    path: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {**self.summary.public_dict(), "instructions": self.instructions}
