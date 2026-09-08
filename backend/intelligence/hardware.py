from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class GpuInfo:
    name: str
    vendor: str = "unknown"
    vram_gb: float = 0.0


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    os: str
    architecture: str
    cpu: str
    cpu_cores: int
    ram_gb: float
    gpus: tuple[GpuInfo, ...] = field(default_factory=tuple)

    @property
    def available_vram_gb(self) -> float:
        return max((gpu.vram_gb for gpu in self.gpus), default=0.0)

    def recommendation(self) -> dict[str, object]:
        budget = self.available_vram_gb or self.ram_gb * 0.55
        if budget >= 20:
            size = "14B–32B quantizado"
        elif budget >= 10:
            size = "7B–14B quantizado"
        elif budget >= 5:
            size = "3B–8B quantizado"
        else:
            size = "1B–4B quantizado"
        return {
            "engine": "ollama",
            "model_class": size,
            "reason": (
                f"{self.ram_gb:g} GB de RAM"
                + (f" e {self.available_vram_gb:g} GB de VRAM" if self.gpus else "")
            ),
        }

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["recommendation"] = self.recommendation()
        return value


@lru_cache(maxsize=1)
def detect_hardware() -> HardwareProfile:
    try:
        import psutil

        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
        cpu_cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    except ImportError:
        ram_gb = 0.0
        cpu_cores = os.cpu_count() or 1
    cpu = platform.processor().strip() or platform.machine()
    return HardwareProfile(
        os=platform.system(),
        architecture=platform.machine(),
        cpu=cpu,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpus=tuple(_detect_nvidia_gpus()),
    )


def _detect_nvidia_gpus() -> list[GpuInfo]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode:
        return []
    gpus: list[GpuInfo] = []
    for line in completed.stdout.splitlines():
        name, separator, memory = line.rpartition(",")
        if not separator:
            continue
        try:
            vram_gb = round(float(memory.strip()) / 1024, 1)
        except ValueError:
            vram_gb = 0.0
        gpus.append(GpuInfo(name=name.strip(), vendor="NVIDIA", vram_gb=vram_gb))
    return gpus

