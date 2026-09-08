from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
from typing import Any


_language: ContextVar[str] = ContextVar("jarvis_language", default="pt-BR")


def get_language(settings: Any = None) -> str:
    if settings is None:
        return _language.get()
    if isinstance(settings, str):
        value = settings
    else:
        user = settings.section("user") if hasattr(settings, "section") else settings.get("user", {})
        value = user.get("language", "pt-BR")
    return "en-US" if value == "en-US" else "pt-BR"


@contextmanager
def language_context(settings_or_locale: Any) -> Iterator[None]:
    token = _language.set(get_language(settings_or_locale))
    try:
        yield
    finally:
        _language.reset(token)


def tr(pt: str, en: str, *, language: str | None = None) -> str:
    return en if (language or get_language()) == "en-US" else pt


def form_of_address(settings: Any) -> str:
    user = settings.section("user") if hasattr(settings, "section") else settings.get("user", {})
    title = str(user.get("form_of_address") or "").strip()
    if title.casefold() in {"", "senhor", "sir"}:
        return tr("senhor", "sir", language=get_language(settings))
    return title


def localized(method: Any) -> Any:
    # Each request keeps its language across tasks and awaits.
    if inspect.isasyncgenfunction(method):
        @wraps(method)
        async def stream(self: Any, *args: Any, **kwargs: Any) -> Any:
            with language_context(self.settings):
                async for item in method(self, *args, **kwargs):
                    yield item
        return stream

    @wraps(method)
    async def run(self: Any, *args: Any, **kwargs: Any) -> Any:
        with language_context(self.settings):
            return await method(self, *args, **kwargs)
    return run
