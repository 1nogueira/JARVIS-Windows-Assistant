from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass, field


DEFAULT_ALLOWED_ORIGINS = frozenset(
    {
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }
)


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    """Per-process identity shared with the desktop shell without persisting it."""

    token: str
    instance_id: str
    allowed_origins: frozenset[str] = field(default=DEFAULT_ALLOWED_ORIGINS)

    @classmethod
    def from_environment(cls) -> "SessionCredentials":
        configured = os.getenv("JARVIS_SESSION_TOKEN", "").strip()
        valid = (32 <= len(configured) <= 256) and all(
            character.isascii() and (character.isalnum() or character in "-_")
            for character in configured
        )
        token = configured if valid else secrets.token_urlsafe(48)
        return cls(token=token, instance_id=secrets.token_urlsafe(24))

    def verify(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(self.token, candidate)

    def origin_allowed(self, origin: str | None) -> bool:
        return bool(origin) and origin.rstrip("/") in self.allowed_origins


session_credentials = SessionCredentials.from_environment()


def bearer_token(authorization: str | None, explicit_token: str | None = None) -> str | None:
    if explicit_token:
        return explicit_token.strip()
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.casefold() == "bearer":
        return value.strip()
    return None


def websocket_session_token(protocols: str | None) -> str | None:
    for protocol in (protocols or "").split(","):
        candidate = protocol.strip()
        if candidate.startswith("jarvis-session."):
            return candidate.removeprefix("jarvis-session.")
    return None
