from __future__ import annotations

from backend.agents.base import BaseAgent
from backend.core.registry import Registry


AgentRegistry: Registry[type[BaseAgent]] = Registry("agente")

