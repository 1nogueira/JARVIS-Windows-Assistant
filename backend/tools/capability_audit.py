from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    effectful: bool
    executor: str
    verification_method: str


def _read(executor: str, verification: str) -> CapabilityContract:
    return CapabilityContract(False, executor, verification)


def _effect(executor: str, verification: str) -> CapabilityContract:
    return CapabilityContract(True, executor, verification)


# This map is deliberately exhaustive. ``scripts/audit_all_tools.py`` fails if
# a registered production tool is absent, preventing hidden legacy capabilities.
CAPABILITY_CONTRACTS: dict[str, CapabilityContract] = {
    "get_system_metrics": _read("psutil/Windows", "metrics returned by operating system"),
    "get_running_processes": _read("psutil", "PID snapshot read from operating system"),
    "diagnose_system_usage": _read("psutil/Windows", "sampled metrics and process snapshot"),
    "list_directory": _read("pathlib", "directory enumerated from filesystem"),
    "find_files": _read("os.walk", "filesystem search completed"),
    "read_text_file": _read("pathlib", "existing file read from disk"),
    "open_file": _effect("Windows shell", "source exists plus associated process/window evidence"),
    "open_folder": _effect("Windows Explorer", "folder exists plus Explorer window evidence"),
    "open_in_vscode": _effect("VS Code process", "VS Code process/window evidence"),
    "open_latest_artifact": _effect("Windows shell", "artifact exists plus associated process/window evidence"),
    "write_text_file": _effect("atomic filesystem replace", "file exists and exact content read back"),
    "create_and_open_text_file": _effect("filesystem/Windows shell", "file content plus application evidence"),
    "replace_text_in_file": _effect("atomic filesystem replace", "updated content read back"),
    "create_folder": _effect("pathlib", "directory exists after creation"),
    "copy_file": _effect("shutil", "source and destination exist with identical content"),
    "move_file": _effect("shutil", "source absent and destination content verified"),
    "rename_file": _effect("pathlib", "source absent and destination content verified"),
    "delete_file": _effect("Windows Recycle Bin", "origin absent and recycle-bin count/API evidence"),
    "move_to_recycle_bin": _effect("Windows Recycle Bin", "origin absent and recycle-bin count/API evidence"),
    "empty_recycle_bin": _effect("Windows Shell API", "SHEmptyRecycleBin result plus zero items read back"),
    "find_installed_apps": _read("Windows application index", "installed-app index queried"),
    "open_app": _effect("Windows application resolver", "new/running process or visible window"),
    "close_process": _effect("psutil", "PID no longer exists"),
    "set_volume": _effect("Windows Core Audio", "master volume scalar read back"),
    "set_mute": _effect("Windows Core Audio", "speaker mute state read back"),
    "set_microphone_mute": _effect("Windows Core Audio", "microphone mute state read back"),
    "open_camera": _effect("Windows application resolver", "Camera process or visible window"),
    "read_clipboard": _read("Windows clipboard", "clipboard text read back"),
    "write_clipboard": _effect("Windows clipboard", "exact clipboard text read back"),
    "open_windows_settings": _effect("Windows URI handler", "SystemSettings process/window evidence"),
    "lock_computer": _effect("Windows user32", "LockWorkStation accepted by Windows"),
    "shutdown_computer": _effect("shutdown.exe", "production request accepted; automated smoke never invokes power"),
    "restart_computer": _effect("shutdown.exe", "production request accepted; automated smoke never invokes power"),
    "remember": _effect("SQLite", "inserted memory row read back"),
    "search_memory": _read("SQLite", "query results read from database"),
    "forget_memory": _effect("SQLite", "row absence verified after deletion"),
    "web_search": _read("SearXNG/search fallback", "non-empty HTTP search results"),
    "get_current_weather": _read("Open-Meteo", "HTTP response contains requested observation/forecast"),
    "open_website": _effect("default browser", "visible browser window or browser process"),
    "open_private_browser": _effect("browser executable", "private browser process/window"),
    "browser_open": _effect("Playwright", "browser connected and page open"),
    "browser_navigate": _effect("Playwright", "page URL/title/status read back"),
    "browser_search": _effect("Playwright/SearXNG/HTTPS fallback", "search page URL/title/status read back"),
    "browser_read_page": _read("Playwright", "page DOM text and links read"),
    "browser_click": _effect("Playwright", "locator action completed and page state read back"),
    "browser_type": _effect("Playwright", "input value read back; submit state recorded"),
    "browser_scroll": _effect("Playwright", "scroll position read back"),
    "browser_back": _effect("Playwright", "history URL read back"),
    "browser_forward": _effect("Playwright", "history URL read back"),
    "browser_close": _effect("Playwright", "browser disconnected/page closed"),
    "capture_screen": _effect("Pillow ImageGrab", "PNG exists, non-empty, dimensions read back"),
    "capture_window": _effect("Pillow ImageGrab", "PNG exists, non-empty, dimensions read back"),
    "analyze_screen": _read("local vision model", "analysis response tied to existing image"),
    "create_reminder": _effect("SQLite", "inserted reminder row read back"),
    "list_reminders": _read("SQLite", "active reminder rows read"),
    "update_reminder": _effect("SQLite", "updated reminder row read back"),
    "delete_reminder": _effect("SQLite", "reminder row absence verified"),
    "get_screen_size": _read("Windows desktop API", "screen dimensions read"),
    "open_discord_conversation": _effect("Discord/PyAutoGUI", "contact must be present in foreground-window title; otherwise explicit failure"),
    "desktop_automation": _effect("PyAutoGUI", "input sequence count and final desktop state sampled"),
    "whatsapp_message": _effect("WhatsApp/PyAutoGUI", "exact contact and draft/input state read back; delivery is never assumed"),
    "search_program": _read("winget", "winget successful search response"),
    "install_program": _effect("winget", "package presence verified with winget list"),
    "control_theme_music": _effect("Windows MCI", "player mode read back after command"),
}


def contract_for(tool_name: str) -> CapabilityContract | None:
    return CAPABILITY_CONTRACTS.get(tool_name)
