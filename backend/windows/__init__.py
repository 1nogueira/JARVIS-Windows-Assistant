"""Windows-specific discovery and launch primitives."""

from backend.windows.app_resolver import (
    AppCandidate,
    AppResolution,
    WindowsAppResolver,
    get_app_resolver,
)

__all__ = ["AppCandidate", "AppResolution", "WindowsAppResolver", "get_app_resolver"]
