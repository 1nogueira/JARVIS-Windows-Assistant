from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app, services, websocket_events
from backend.core.cancellation import task_manager
from backend.core.events import event_bus
from backend.core.config import settings
from backend.security.session import session_credentials


def test_sensitive_http_endpoints_require_session_credential() -> None:
    client = TestClient(app)
    assert client.get("/api/settings").status_code == 401
    assert client.get(
        "/api/settings", headers={"Authorization": "Bearer incorrect"}
    ).status_code == 401

    response = client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {session_credentials.token}"},
    )
    assert response.status_code == 200


def test_identity_requires_authentication_and_reports_pid() -> None:
    client = TestClient(app)
    assert client.get("/api/identity").status_code == 401
    response = client.get(
        "/api/identity",
        headers={"X-Jarvis-Session": session_credentials.token},
    )
    assert response.status_code == 200
    assert response.json()["instance_id"] == session_credentials.instance_id
    assert isinstance(response.json()["pid"], int)


def test_unauthenticated_bodyless_post_has_no_effect(monkeypatch) -> None:
    calls = {"tts": 0, "cancel": 0}

    async def stop_tts() -> bool:
        calls["tts"] += 1
        return True

    async def cancel_tasks(request_id=None) -> int:
        calls["cancel"] += 1
        return 1

    monkeypatch.setattr(services.tts, "stop", stop_tts)
    monkeypatch.setattr(task_manager, "cancel", cancel_tasks)
    response = TestClient(app).post("/api/cancel")
    assert response.status_code == 401
    assert calls == {"tts": 0, "cancel": 0}


@pytest.mark.parametrize(
    "path",
    [
        "/api/cancel",
        "/api/voice/wakeword/start",
        "/api/voice/theme/play",
        "/api/voice/microphone/mute",
    ],
)
def test_bodyless_mutating_endpoints_require_authentication(path: str) -> None:
    assert TestClient(app).post(path).status_code == 401


def test_invalid_settings_request_does_not_touch_persisted_file() -> None:
    before = settings.path.read_bytes()
    response = TestClient(app).put(
        "/api/settings",
        headers={"Authorization": f"Bearer {session_credentials.token}"},
        json={"settings": {"agent": {"max_steps": 0}}},
    )
    assert response.status_code == 422
    assert settings.path.read_bytes() == before


class FakeWebSocket:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers
        self.accepted = False
        self.accepted_protocol: str | None = None
        self.closed_code: int | None = None
        self.sent: list[dict[str, object]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.accepted_protocol = subprotocol

    async def close(self, code: int, reason: str = "") -> None:
        self.closed_code = code

    async def send_json(self, value: dict[str, object]) -> None:
        self.sent.append(value)
        raise RuntimeError("connection closed by test")


@pytest.mark.asyncio
async def test_websocket_rejects_before_accept_without_session() -> None:
    socket = FakeWebSocket({"origin": "http://127.0.0.1:1420"})
    await websocket_events(socket)  # type: ignore[arg-type]
    assert not socket.accepted
    assert socket.closed_code == 1008


@pytest.mark.asyncio
async def test_websocket_rejects_untrusted_origin_even_with_session() -> None:
    socket = FakeWebSocket(
        {
            "origin": "https://attacker.example",
            "sec-websocket-protocol": (
                f"jarvis-events, jarvis-session.{session_credentials.token}"
            ),
        }
    )
    await websocket_events(socket)  # type: ignore[arg-type]
    assert not socket.accepted
    assert socket.closed_code == 1008


@pytest.mark.asyncio
async def test_legitimate_websocket_origin_and_session_receive_events(monkeypatch) -> None:
    async def one_event() -> AsyncIterator[dict[str, object]]:
        yield {"event": "ready"}

    monkeypatch.setattr(event_bus, "subscribe", one_event)
    socket = FakeWebSocket(
        {
            "origin": "http://127.0.0.1:1420",
            "sec-websocket-protocol": (
                f"jarvis-events, jarvis-session.{session_credentials.token}"
            ),
        }
    )
    await websocket_events(socket)  # type: ignore[arg-type]
    assert socket.accepted
    assert socket.accepted_protocol == "jarvis-events"
    assert socket.sent == [{"event": "ready"}]
