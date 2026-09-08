from __future__ import annotations

import asyncio
import re
import shutil
from typing import Any

from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success


def _winget() -> str:
    executable = shutil.which("winget")
    if not executable:
        raise RuntimeError("winget não está instalado neste Windows.")
    return executable


async def winget_search(query: str) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        _winget(), "search", "--query", query, "--source", "winget", "--accept-source-agreements",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except (asyncio.CancelledError, TimeoutError):
        process.terminate()
        await process.wait()
        raise
    text = stdout.decode(errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-500:] or "Falha ao pesquisar no winget.")
    return {"query": query, "results": text[-12_000:]}


async def winget_install(package_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._+-]{2,160}", package_id):
        raise ValueError("Identificador de pacote inválido.")
    process = await asyncio.create_subprocess_exec(
        _winget(), "install", "--id", package_id, "--exact", "--source", "winget",
        "--accept-package-agreements", "--accept-source-agreements", "--silent", "--disable-interactivity",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=1200)
    except asyncio.CancelledError:
        process.terminate()
        await process.wait()
        raise
    except TimeoutError as exc:
        process.terminate()
        await process.wait()
        raise RuntimeError("A instalação ultrapassou 20 minutos.") from exc
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-800:] or stdout.decode(errors="replace")[-800:])
    verify = await asyncio.create_subprocess_exec(
        _winget(), "list", "--id", package_id, "--exact", "--source", "winget",
        "--accept-source-agreements", "--disable-interactivity",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    verify_out, verify_err = await asyncio.wait_for(verify.communicate(), timeout=90)
    listing = verify_out.decode(errors="replace")
    if verify.returncode != 0 or package_id.casefold() not in listing.casefold():
        raise RuntimeError(
            verify_err.decode(errors="replace")[-800:]
            or "O winget terminou a instalação, mas o pacote não apareceu em winget list."
        )
    return {
        "installed": package_id,
        "detail": stdout.decode(errors="replace")[-1200:],
        "verification": "package_id_present_in_winget_list",
    }


def register_software_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="search_program",
        description="Pesquisa programas disponíveis no catálogo oficial do winget.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        category="Programas",
    )
    async def search_program(query: str) -> dict[str, Any]:
        return explicit_success(await winget_search(query))

    @registry.tool(
        name="install_program",
        description="Baixa e instala silenciosamente um pacote pelo identificador exato do winget.",
        parameters={"type": "object", "properties": {"package_id": {"type": "string"}}, "required": ["package_id"]},
        permission_level=PermissionLevel.RESTRICTED,
        category="Programas",
        confirmation_text="Quer que eu baixe e instale esse programa, senhor?",
        timeout_seconds=1200,
    )
    async def install_program(package_id: str) -> dict[str, Any]:
        return explicit_success(await winget_install(package_id))
