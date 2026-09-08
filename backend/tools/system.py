from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
import time
from typing import Any

from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success


def _psutil() -> Any:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("psutil não está instalado. Execute setup.ps1.") from exc
    return psutil


def system_metrics() -> dict[str, Any]:
    psutil = _psutil()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
    network = psutil.net_io_counters()
    battery = psutil.sensors_battery()
    metrics: dict[str, Any] = {
        "platform": platform.platform(),
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / 1024**3, 2),
        "ram_total_gb": round(memory.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "network_sent_mb": round(network.bytes_sent / 1024**2, 2),
        "network_received_mb": round(network.bytes_recv / 1024**2, 2),
        "uptime_seconds": max(0, int(__import__("time").time() - psutil.boot_time())),
        "battery": None,
        "gpu": gpu_metrics(),
    }
    if battery:
        metrics["battery"] = {
            "percent": battery.percent,
            "plugged": battery.power_plugged,
            "seconds_left": battery.secsleft,
        }
    return metrics


def gpu_metrics() -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        gpus = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 5:
                gpus.append(
                    {
                        "name": parts[0],
                        "utilization_percent": _number(parts[1]),
                        "vram_used_mb": _number(parts[2]),
                        "vram_total_mb": _number(parts[3]),
                        "temperature_c": _number(parts[4]),
                    }
                )
        return gpus
    except (OSError, subprocess.SubprocessError):
        return []


def _number(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value


def running_processes(limit: int = 15, sort_by: str = "memory") -> list[dict[str, Any]]:
    psutil = _psutil()
    processes = []
    for process in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
        try:
            info = process.info
            processes.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "desconhecido",
                    "cpu_percent": info.get("cpu_percent") or 0,
                    "memory_mb": round((info.get("memory_info").rss if info.get("memory_info") else 0) / 1024**2, 1),
                    "status": info.get("status"),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    return sorted(processes, key=lambda item: item[key], reverse=True)[: max(1, min(limit, 100))]


def system_diagnosis(focus: str = "auto", limit: int = 8) -> dict[str, Any]:
    psutil = _psutil()
    focus = focus if focus in {"auto", "memory", "cpu", "disk", "gpu"} else "auto"
    limit = max(3, min(int(limit), 20))
    processes = []
    for process in psutil.process_iter(["pid", "name", "memory_info", "status"]):
        try:
            process.cpu_percent(None)
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.45)
    grouped: dict[str, dict[str, Any]] = {}
    for process in processes:
        try:
            name = process.name() or "desconhecido"
            memory_mb = process.memory_info().rss / 1024**2
            cpu_percent = process.cpu_percent(None)
            item = grouped.setdefault(
                name.casefold(),
                {
                    "name": name,
                    "instances": 0,
                    "memory_mb": 0.0,
                    "cpu_percent": 0.0,
                    "pids": [],
                },
            )
            item["instances"] += 1
            item["memory_mb"] += memory_mb
            item["cpu_percent"] += cpu_percent
            if len(item["pids"]) < 8:
                item["pids"].append(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    aggregate = list(grouped.values())
    for item in aggregate:
        item["memory_mb"] = round(item["memory_mb"], 1)
        item["cpu_percent"] = round(item["cpu_percent"], 1)
    metrics = system_metrics()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    metrics.update(
        {
            "ram_available_gb": round(memory.available / 1024**3, 2),
            "ram_cached_gb": round(getattr(memory, "cached", 0) / 1024**3, 2),
            "swap_percent": swap.percent,
            "swap_used_gb": round(swap.used / 1024**3, 2),
        }
    )
    top_memory = sorted(aggregate, key=lambda item: item["memory_mb"], reverse=True)[:limit]
    top_cpu = sorted(aggregate, key=lambda item: item["cpu_percent"], reverse=True)[:limit]
    observations: list[str] = []
    ram_percent = float(metrics["ram_percent"])
    if ram_percent >= 85:
        observations.append(
            "O uso de RAM está alto neste instante; fechar um dos maiores consumidores deve liberar memória."
        )
    elif ram_percent >= 70:
        observations.append("O uso de RAM está elevado, mas ainda há alguma margem disponível.")
    else:
        observations.append("O uso de RAM não está elevado neste instante.")
    if top_memory:
        accounted = sum(float(item["memory_mb"]) for item in top_memory) / 1024
        observations.append(
            f"Os {len(top_memory)} maiores grupos de processos somam cerca de {accounted:.1f} GB; "
            "o restante inclui Windows, cache e outros processos menores."
        )
    cpu_percent = float(metrics["cpu_percent"])
    if cpu_percent >= 85:
        observations.append("A CPU está sob carga alta neste instante.")
    elif cpu_percent >= 60:
        observations.append("A CPU está sob carga moderada neste instante.")
    if float(metrics["disk_percent"]) >= 90:
        observations.append("O disco do sistema está com menos de 10% de espaço livre.")
    return {
        "focus": focus,
        "sample_seconds": 0.45,
        "metrics": metrics,
        "top_memory": top_memory,
        "top_cpu": top_cpu,
        "observations": observations,
        "note": "Medição pontual; repita o diagnóstico se o pico for intermitente.",
    }


def model_recommendation() -> dict[str, Any]:
    metrics = system_metrics()
    ram = metrics["ram_total_gb"]
    vram = max([gpu.get("vram_total_mb", 0) for gpu in metrics["gpu"]] or [0]) / 1024
    if ram >= 32 and vram >= 10:
        mode, size = "qualidade", "modelos 14B quantizados; visão 7B"
    elif ram >= 16 and (vram >= 6 or not metrics["gpu"]):
        mode, size = "equilibrado", "modelos 7B–9B quantizados"
    else:
        mode, size = "leve", "modelos 3B–4B quantizados"
    return {"mode": mode, "recommendation": size, "ram_gb": ram, "vram_gb": round(vram, 1)}


def register_system_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="get_system_metrics",
        description="Consulta uso atual de CPU, RAM, disco, rede, bateria e GPU do computador.",
        parameters={"type": "object", "properties": {}},
        permission_level=PermissionLevel.SAFE,
        category="Sistema",
    )
    async def get_system_metrics() -> dict[str, Any]:
        return explicit_success(await asyncio.to_thread(system_metrics))

    @registry.tool(
        name="get_running_processes",
        description="Lista processos em execução, ordenados por memória ou CPU.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "sort_by": {"type": "string", "enum": ["memory", "cpu"]},
            },
        },
        permission_level=PermissionLevel.SAFE,
        category="Sistema",
    )
    async def get_running_processes(limit: int = 15, sort_by: str = "memory") -> dict[str, Any]:
        return explicit_success(await asyncio.to_thread(running_processes, limit, sort_by))

    @registry.tool(
        name="diagnose_system_usage",
        description=(
            "Investiga uso alto de RAM, CPU, disco ou GPU. Mede o sistema e agrupa os processos "
            "que mais consomem recursos para explicar a causa provável com dados reais."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["auto", "memory", "cpu", "disk", "gpu"],
                },
                "limit": {"type": "integer", "minimum": 3, "maximum": 20},
            },
        },
        permission_level=PermissionLevel.SAFE,
        category="Sistema",
        timeout_seconds=15,
    )
    async def diagnose_system_usage(focus: str = "auto", limit: int = 8) -> dict[str, Any]:
        return explicit_success(await asyncio.to_thread(system_diagnosis, focus, limit))
