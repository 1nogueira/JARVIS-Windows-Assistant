from __future__ import annotations

from enum import StrEnum

from backend.core.config import SettingsStore


class PermissionLevel(StrEnum):
    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    RESTRICTED = "RESTRICTED"


class PermissionPolicy:
    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings

    def level_for(self, tool_name: str, default: PermissionLevel) -> PermissionLevel:
        value = self.settings.section("permissions").get(tool_name, default.value)
        try:
            configured = PermissionLevel(value)
        except ValueError:
            return default
        # Restricted tools cannot bypass confirmation through a SAFE override.
        if default is PermissionLevel.RESTRICTED and configured is PermissionLevel.SAFE:
            return PermissionLevel.CONFIRM
        return configured

    def requires_confirmation(self, tool_name: str, default: PermissionLevel) -> bool:
        return self.level_for(tool_name, default) is not PermissionLevel.SAFE
