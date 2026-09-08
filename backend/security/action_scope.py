from __future__ import annotations

import re
import unicodedata
from typing import Any


def validate_effect_scope(
    message: str, tool_name: str, arguments: dict[str, Any]
) -> tuple[bool, str]:
    """Require lexical authorization for every effectful LLM-selected tool.

    Deterministic intents already construct calls from explicit grammar. This
    guard applies to model-selected calls and prevents a second, unrequested
    action from being smuggled beside a legitimate one.
    """

    text = _normalize(message)
    patterns: dict[str, str] = {
        "open_app": r"\b(?:abra|abre|abrir|inicie|iniciar|execute|executar|open|launch|start)\b",
        "open_camera": r"\b(?:abra|abre|abrir|inicie|iniciar|open|launch|start)\b.*\bcamera\b",
        "open_website": r"\b(?:abra|abre|abrir|acesse|acessar|navegador|site|url|link|open|launch|website|browser)\b",
        "open_private_browser": r"\b(?:anonim|incognito|inprivate|privad)\w*\b",
        "browser_open": r"\b(?:abra|abre|abrir|navegador|browser)\b",
        "browser_navigate": r"\b(?:navegue|acesse|abra|url|site|pagina)\b",
        "browser_search": r"\b(?:pesquise|procure|busque)\b",
        "browser_click": r"\b(?:clique|clicar)\b",
        "browser_type": r"\b(?:digite|preencha|escreva)\b",
        "browser_scroll": r"\b(?:role|rolar|scroll)\b",
        "browser_back": r"\b(?:volte|voltar|anterior)\b",
        "browser_forward": r"\b(?:avance|avancar|seguinte)\b",
        "browser_close": r"\b(?:feche|fecha|fechar|encerre)\b.*\b(?:navegador|browser)\b",
        "open_file": r"\b(?:abra|abre|abrir)\b.*\barquivo\b",
        "open_folder": r"\b(?:abra|abre|abrir)\b.*\b(?:pasta|diretorio)\b",
        "open_in_vscode": r"\b(?:abra|abre|abrir)\b.*\b(?:vscode|vs code|visual studio code|arquivo|pasta)\b",
        "open_latest_artifact": r"\b(?:abra|abre|abrir)\b.*\b(?:isso|ele|arquivo|relatorio)\b",
        "write_text_file": r"\b(?:grave|gravar|salve|salvar|escreva|sobrescreva)\b",
        "create_and_open_text_file": r"\b(?:crie|cria|criar|gere|gerar|faca|materialize)\w*\b.*\b(?:arquivo|documento|relatorio|txt)\b",
        "replace_text_in_file": r"\b(?:altere|troque|substitua|corrija|edite)\b.*\barquivo\b",
        "create_folder": r"\b(?:crie|cria|criar)\b.*\b(?:pasta|diretorio)\b",
        "copy_file": r"\b(?:copie|copiar)\b",
        "move_file": r"\b(?:mova|mover)\b(?!.*\blixeira\b)",
        "rename_file": r"\b(?:renomeie|renomear)\b",
        "delete_file": r"\b(?:delete|deletar|apague|apagar|exclua|excluir|lixeira)\b",
        "move_to_recycle_bin": r"\b(?:delete|deletar|apague|apagar|exclua|excluir|joga|jogue|mova|mande)\b.*\b(?:arquivo|lixeira)\b",
        "empty_recycle_bin": r"\b(?:esvazie|esvazia|esvaziar|limpe|limpa|limpar)\b.*\blixeira\b",
        "close_process": r"\b(?:feche|fecha|fechar|encerre|encerrar)\b",
        "set_volume": r"\b(?:volume|som)\b",
        "set_mute": r"\b(?:muta|mute|mutar|desmuta|desmute|audio|som)\b",
        "set_microphone_mute": r"\b(?:microfone|mic)\b",
        "write_clipboard": r"\b(?:copie|copiar|clipboard|area de transferencia)\b",
        "open_windows_settings": r"\b(?:abra|abre|abrir)\b.*\b(?:configuracoes|settings|windows)\b",
        "lock_computer": r"\b(?:bloqueie|bloqueia|bloquear|trave|trava)\b.*\b(?:pc|computador|sessao|windows)\b",
        "shutdown_computer": r"\b(?:desligue|desliga|desligar)\b.*\b(?:pc|computador|maquina|windows)\b",
        "restart_computer": r"\b(?:reinicie|reinicia|reiniciar|reboot)\b",
        "remember": r"\b(?:lembre|memorize|guarde)\b",
        "forget_memory": r"\b(?:esqueca|apague|remova|exclua)\b.*\bmemoria\b",
        "capture_screen": r"\b(?:capture|captura|print|screenshot)\b",
        "capture_window": r"\b(?:capture|captura|print|screenshot)\b.*\bjanela\b",
        "create_reminder": r"\b(?:lembre|avise|lembrete|alarme)\b",
        "update_reminder": r"\b(?:altere|mude|ajuste|corrija)\b.*\blembrete\b",
        "delete_reminder": r"\b(?:apague|delete|exclua|cancele)\b.*\blembrete\b",
        "open_discord_conversation": r"\b(?:abra|abre|abrir)\b.*\b(?:conversa|discord)\b",
        "desktop_automation": r"\b(?:clique|digite|pressione|atalho|role|interaja|execute)\b",
        "whatsapp_message": r"\b(?:mande|envie|digite|mensagem)\b.*\b(?:whatsapp|contato|mae)\b",
        "install_program": r"\b(?:instale|instalar|baixe|baixar)\b",
        "control_theme_music": r"\b(?:musica|tema|the clash|toque|pause|continue|retome)\b",
    }
    pattern = patterns.get(tool_name)
    if not pattern:
        return False, f"A tool efetiva {tool_name} não possui regra de escopo para chamadas do modelo."
    if not re.search(pattern, text):
        return False, f"O pedido não autorizou lexicalmente a ação {tool_name}."
    target_fields = {
        "open_app": ("query",),
        "open_website": ("target",),
        "open_file": ("path",),
        "open_folder": ("path",),
        "open_in_vscode": ("path",),
        "write_text_file": ("path",),
        "create_and_open_text_file": ("filename",),
        "replace_text_in_file": ("path",),
        "create_folder": ("path",),
        "copy_file": ("source", "destination"),
        "move_file": ("source", "destination"),
        "rename_file": ("source", "destination"),
        "delete_file": ("path",),
        "move_to_recycle_bin": ("path",),
        "open_discord_conversation": ("username",),
        "whatsapp_message": ("contact",),
        "install_program": ("package_id",),
    }
    for field in target_fields.get(tool_name, ()):
        if not arguments.get(field):
            continue
        target = _argument_target(str(arguments[field]))
        aliases = {
            "visual studio code": ("vscode", "vs code", "visual studio code"),
            "code": ("vscode", "vs code", "visual studio code"),
            "microsoft windows camera": ("camera",),
        }
        alternatives = aliases.get(target, _target_alternatives(target))
        meaningful = [value for value in alternatives if len(value) >= 3]
        if meaningful and not any(value in text for value in meaningful):
            return False, f"O alvo {arguments[field]} não aparece no pedido autorizado."
    if tool_name == "empty_recycle_bin" and arguments:
        unknown = set(arguments) - {"drive"}
        if unknown:
            return False, "A chamada de Lixeira contém argumentos não autorizados."
    return True, "effect_scope_authorized"


def _normalize(value: str) -> str:
    return (
        unicodedata.normalize("NFKD", value.casefold())
        .encode("ascii", "ignore")
        .decode()
    )


def _argument_target(value: str) -> str:
    plain = _normalize(value).replace("\\", "/").rstrip("/")
    leaf = plain.rsplit("/", 1)[-1]
    leaf = re.sub(r"\.(?:exe|com|bat|cmd|txt|md|json|py|pdf)$", "", leaf)
    leaf = re.sub(r"^(?:https?://)?(?:www\.)?", "", leaf)
    return leaf.split("?", 1)[0].strip()


def _target_alternatives(target: str) -> tuple[str, ...]:
    pieces = [item for item in re.split(r"[^a-z0-9]+", target) if len(item) >= 4]
    values = [target, *pieces]
    return tuple(dict.fromkeys(values))
