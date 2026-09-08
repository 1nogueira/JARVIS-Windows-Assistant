from __future__ import annotations

from typing import Any

from backend.core.i18n import get_language, language_context, tr
from backend.tools.results import tool_result_detail, tool_result_outcome


def response_from_tool_result(
    tool: str,
    result: dict[str, Any],
    *,
    form_of_address: str = "senhor",
    language: str | None = None,
) -> str:
    """Render only facts contained in a real ToolResult."""

    if language is not None and language != get_language():
        with language_context(language):
            return response_from_tool_result(tool, result, form_of_address=form_of_address)
    title = form_of_address.strip() or tr("senhor", "sir")
    if title in {"senhor", "sir"}:
        title = tr("senhor", "sir")
    if tool_result_outcome(result) != "completed":
        detail = tool_result_detail(result)
        return tr(f"Não consegui concluir {tool.replace('_', ' ')}: {detail}, {title}.", f"I could not complete {tool.replace('_', ' ')}: {detail}, {title}.")

    data = result.get("data") if isinstance(result.get("data"), dict) else result
    assert isinstance(data, dict)
    if tool in {"delete_file", "move_to_recycle_bin"}:
        return tr(f"Movi {data.get('moved_to_recycle_bin', 'o arquivo')} para a Lixeira, {title}.", f"I moved {data.get('moved_to_recycle_bin', 'the file')} to the Recycle Bin, {title}.")
    if tool == "empty_recycle_bin":
        drive = str(data.get("drive") or "").strip()
        scope = tr(f" da unidade {drive}", f" on drive {drive}") if drive and drive != "all" else ""
        return tr(f"Esvaziei a Lixeira{scope} e confirmei que ela ficou sem itens, {title}.", f"I emptied the Recycle Bin{scope} and verified it has no items, {title}.")
    if tool == "shutdown_computer":
        if data.get("safe_smoke"):
            return tr(f"O Windows aceitou o teste de desligamento e eu o cancelei imediatamente, {title}.", f"Windows accepted the shutdown test and I immediately cancelled it, {title}.")
        return tr(f"O Windows aceitou o pedido de desligamento, {title}.", f"Windows accepted the shutdown request, {title}.")
    if tool == "restart_computer":
        if data.get("safe_smoke"):
            return tr(f"O Windows aceitou o teste de reinicialização e eu o cancelei imediatamente, {title}.", f"Windows accepted the restart test and I immediately cancelled it, {title}.")
        return tr(f"O Windows aceitou o pedido de reinicialização, {title}.", f"Windows accepted the restart request, {title}.")
    if tool == "lock_computer":
        return tr(f"O Windows aceitou o bloqueio da sessão, {title}.", f"Windows accepted the session lock request, {title}.")
    if tool == "close_process":
        return tr(f"Encerrei o processo {data.get('closed', data.get('pid', 'solicitado'))}, {title}.", f"I closed process {data.get('closed', data.get('pid', 'requested'))}, {title}.")
    if tool == "set_volume":
        return tr(f"Ajustei e confirmei o volume em {data.get('volume')}%, {title}.", f"I set and verified the volume at {data.get('volume')}%, {title}.")
    if tool == "set_mute":
        return tr(f"O áudio ficou {'mudo' if data.get('muted') else 'ativo'}, {title}.", f"Audio is {'muted' if data.get('muted') else 'unmuted'}, {title}.")
    if tool == "set_microphone_mute":
        return tr(f"O microfone ficou {'mudo' if data.get('microphone_muted') else 'ativo'}, {title}.", f"The microphone is {'muted' if data.get('microphone_muted') else 'unmuted'}, {title}.")
    if tool == "write_clipboard":
        return tr(f"Copiei o texto e confirmei a Área de Transferência, {title}.", f"I copied the text and verified the clipboard, {title}.")
    if tool in {"open_app", "open_camera"}:
        return tr(f"Abri e confirmei {data.get('opened', 'o aplicativo')}, {title}.", f"I opened and verified {data.get('opened', 'the application')}, {title}.")
    if tool in {"open_file", "open_folder", "open_in_vscode", "open_latest_artifact"}:
        return tr(f"Abri {data.get('opened', 'o caminho')} e confirmei a janela associada, {title}.", f"I opened {data.get('opened', 'the path')} and verified its window, {title}.")
    if tool in {"open_website", "open_private_browser"}:
        return (
            tr(f"Enviei {data.get('url') or data.get('opened') or 'o endereço'} ao navegador "
            f"e confirmei o processo ou uma janela visível, {title}.", f"I sent {data.get('url') or data.get('opened') or 'the address'} to the browser "
            f"and verified its process or a visible window, {title}.")
        )
    if tool == "open_windows_settings":
        return (
            tr(f"Enviei a página {data.get('opened', '')} às Configurações e confirmei "
            f"o processo do Windows, {title}.", f"I sent page {data.get('opened', '')} to Settings and verified "
            f"the Windows process, {title}.")
        )
    if tool in {"browser_open", "browser_navigate", "browser_search", "browser_click", "browser_type", "browser_scroll", "browser_back", "browser_forward", "browser_close"}:
        return tr(f"Concluí {tool.replace('_', ' ')} e li de volta o estado do navegador, {title}.", f"I completed {tool.replace('_', ' ')} and read back the browser state, {title}.")
    if tool == "write_text_file":
        return tr(f"Gravei e conferi {data.get('written', 'o arquivo')}, {title}.", f"I saved and verified {data.get('written', 'the file')}, {title}.")
    if tool == "create_folder":
        return tr(f"Criei e confirmei a pasta {data.get('created', '')}, {title}.", f"I created and verified folder {data.get('created', '')}, {title}.")
    if tool in {"copy_file", "move_file", "rename_file"}:
        target = data.get("copied_to") or data.get("moved_to") or data.get("renamed_to")
        return tr(f"Concluí e conferi a operação no arquivo {target or ''}, {title}.", f"I completed and verified the operation on file {target or ''}, {title}.")
    if tool == "delete_reminder":
        return tr(f"Excluí e conferi o lembrete {data.get('deleted', '')}, {title}.", f"I deleted and verified reminder {data.get('deleted', '')}, {title}.")
    if tool == "forget_memory":
        return tr(f"Excluí e conferi a memória {data.get('deleted', '')}, {title}.", f"I deleted and verified memory {data.get('deleted', '')}, {title}.")
    if tool == "whatsapp_message":
        if data.get("sent") is True:
            return tr(f"O WhatsApp aceitou o envio para {data.get('contact', 'o contato')}, {title}.", f"WhatsApp accepted the message to {data.get('contact', 'the contact')}, {title}.")
        return tr(f"Deixei a mensagem conferida no campo de {data.get('contact', 'o contato')}, {title}.", f"I left the verified message in the input field for {data.get('contact', 'the contact')}, {title}.")
    if tool == "create_reminder":
        return tr(f"Criei e li de volta o lembrete {data.get('title', '')}, {title}.", f"I created and read back reminder {data.get('title', '')}, {title}.")
    if tool == "update_reminder":
        return tr(f"Atualizei e li de volta o lembrete {data.get('title', '')}, {title}.", f"I updated and read back reminder {data.get('title', '')}, {title}.")
    if tool == "remember":
        return tr(f"Guardei e li de volta a memória {data.get('key', '')}, {title}.", f"I saved and read back memory {data.get('key', '')}, {title}.")
    if tool in {"capture_screen", "capture_window"}:
        return tr(f"Capturei e validei a imagem {data.get('path', '')}, {title}.", f"I captured and validated image {data.get('path', '')}, {title}.")
    if tool == "control_theme_music":
        return tr(f"Apliquei o controle de música e conferi o estado do player, {title}.", f"I applied the music control and verified the player state, {title}.")
    return (
        tr(f"A ação {tool.replace('_', ' ')} foi concluída e verificada pelo executor, {title}.", f"The executor completed and verified action {tool.replace('_', ' ')}, {title}.")
    )
