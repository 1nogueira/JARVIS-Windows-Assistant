from __future__ import annotations

import re
import unicodedata

from backend.routing.models import RouteDecision, RouteMode
from backend.skills.registry import SkillRegistry


class RequestRouter:
    """Low-latency capability router; it performs no model inference."""

    ACTION_VERBS = {
        "abra", "abre", "abrir", "crie", "cria", "criar", "apague", "apagar",
        "delete", "exclua", "mova", "copie", "renomeie", "envie", "mande",
        "instale", "execute", "executar", "feche", "mute", "desmute", "toque", "pause",
        "inicie", "inicia", "iniciar", "continue", "continuar", "reproduza", "reproduzir",
        "acesse", "acessar", "salve", "salvar", "baixe", "baixar", "imprima", "imprimir",
        "desliga", "desligue", "reinicia", "reinicie", "reiniciar", "bloqueia", "bloqueie",
        "esvazia", "esvazie", "limpa", "limpe", "joga", "jogue", "apaga",
        "aumenta", "aumente", "abaixa", "abaixe", "diminua", "reduza", "muta", "desmuta",
        "trava", "trave", "restaure", "restaurar",
        "open", "launch", "start", "close", "create", "delete", "remove", "move", "copy",
        "rename", "send", "install", "run", "search", "shutdown", "restart", "lock", "mute",
    }
    RESEARCH_PHRASES = {
        "pesquisa profunda", "pesquise profundamente", "investigue profundamente",
        "cruze fontes", "analise varias fontes", "relatorio de pesquisa",
    }
    SCHEDULE_PHRASES = {
        "todo dia", "toda manha", "toda tarde", "toda noite", "toda semana",
        "diariamente", "semanalmente", "a cada hora", "monitore", "monitorar",
        "every day", "every week", "daily", "weekly", "monitor", "monitoring",
    }

    def __init__(self, skills: SkillRegistry) -> None:
        self.skills = skills

    def route(self, message: str) -> RouteDecision:
        normalized = _normalize(message)
        matches = self.skills.match(normalized)
        names = tuple(skill.name for skill in matches)
        if any(phrase in normalized for phrase in self.SCHEDULE_PHRASES):
            return RouteDecision(
                mode=RouteMode.SCHEDULED,
                agent="persistent",
                skills=names,
                confidence=0.96,
                reason="solicitação recorrente ou de monitoramento",
                requires_tools=True,
            )
        if any(phrase in normalized for phrase in self.RESEARCH_PHRASES):
            return RouteDecision(
                mode=RouteMode.RESEARCH,
                agent="research",
                skills=names or ("web_research",),
                confidence=0.97,
                reason="pedido explícito de pesquisa iterativa",
                requires_tools=True,
            )
        words = set(re.findall(r"[a-z0-9]+", normalized))
        action_count = _action_target_count(normalized)
        if has_external_action_intent(normalized):
            multi = action_count > 1
            return RouteDecision(
                mode=RouteMode.TASK if multi else RouteMode.DIRECT_ACTION,
                agent="orchestrator" if multi else "direct",
                skills=names,
                confidence=0.93,
                reason="multiple explicit actions" if multi and "and" in normalized else ("múltiplas ações explícitas" if multi else "ação local explícita"),
                requires_tools=True,
            )
        if "pesquise" in words or "pesquisar" in words or _requires_current_data(normalized):
            return RouteDecision(
                mode=RouteMode.RESEARCH,
                agent="research",
                skills=names or ("web_research",),
                confidence=0.88,
                reason="informação atual ou pesquisa solicitada",
                requires_tools=True,
            )
        return RouteDecision(
            mode=RouteMode.SIMPLE,
            agent="simple",
            skills=(),
            confidence=0.98,
            reason="conversa ou pergunta sem efeito externo",
            requires_tools=False,
        )


def _action_target_count(value: str) -> int:
    if not re.search(r"\b(?:abra|abre|abrir|inicie|iniciar|open|launch|start)\b", value):
        return 1
    body = re.split(r"\b(?:abra|abre|abrir|inicie|iniciar|open|launch|start)\b", value, maxsplit=1)[-1]
    parts = [part.strip() for part in re.split(r"\s*,\s*|\s+(?:e|and)\s+", body) if part.strip()]
    return max(1, len(parts))


def has_external_action_intent(value: str) -> bool:
    normalized = _normalize(value)
    words = set(re.findall(r"[a-z0-9]+", normalized))
    return bool(words & RequestRouter.ACTION_VERBS)


def _requires_current_data(value: str) -> bool:
    return any(
        term in value
        for term in (
            "hoje", "agora", "atualmente", "mais recente", "ultima noticia", "today", "now", "currently", "latest", "news",
            "preco atual", "placar", "quem ganhou", "presidente atual",
        )
    )


def _normalize(value: str) -> str:
    return (
        unicodedata.normalize("NFKD", value.casefold())
        .encode("ascii", "ignore")
        .decode()
    )
