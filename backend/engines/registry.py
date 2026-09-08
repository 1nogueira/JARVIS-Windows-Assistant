from __future__ import annotations

from backend.core.registry import Registry
from backend.engines.base import InferenceEngine


EngineRegistry: Registry[type[InferenceEngine]] = Registry("engine")

