from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import SettingsStore
from backend.core.events import EventBus
from backend.core.logging import JsonlAuditLog
from backend.core.reminders import ReminderStore
from backend.memory.long_term import LongTermMemory
from backend.security.confirmations import ConfirmationManager
from backend.tools.browser import BrowserController, register_browser_tools
from backend.tools.desktop import register_desktop_tools
from backend.tools.files import register_file_tools
from backend.tools.memory import register_memory_tools
from backend.tools.registry import ToolRegistry
from backend.tools.reminders import register_reminder_tools
from backend.tools.software import register_software_tools
from backend.tools.system import register_system_tools
from backend.tools.vision import register_vision_tools
from backend.tools.web_search import SearxngSearch, register_web_tools
from backend.tools.weather import register_weather_tools
from backend.tools.windows import register_windows_tools
from backend.vision.analyzer import VisionAnalyzer
from backend.vision.screenshot import ScreenshotService


@dataclass(slots=True)
class ToolServices:
    registry: ToolRegistry
    browser: BrowserController
    search: SearxngSearch
    screenshots: ScreenshotService
    vision: VisionAnalyzer
    reminders: ReminderStore


def create_tool_services(
    settings: SettingsStore,
    events: EventBus,
    confirmations: ConfirmationManager,
    audit_log: JsonlAuditLog,
    memory: LongTermMemory,
) -> ToolServices:
    registry = ToolRegistry(settings, events, confirmations, audit_log)
    browser = BrowserController(settings)
    search = SearxngSearch(settings)
    screenshots = ScreenshotService(settings)
    vision = VisionAnalyzer(settings, screenshots)
    reminders = ReminderStore(memory.database)

    register_system_tools(registry)
    register_file_tools(registry, settings)
    register_windows_tools(registry)
    register_memory_tools(registry, memory, settings)
    register_web_tools(registry, search)
    register_weather_tools(registry, settings)
    register_browser_tools(registry, browser)
    register_vision_tools(registry, screenshots, vision)
    register_reminder_tools(registry, reminders)
    register_desktop_tools(registry)
    register_software_tools(registry)
    return ToolServices(registry, browser, search, screenshots, vision, reminders)
