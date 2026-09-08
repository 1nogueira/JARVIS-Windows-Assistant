from __future__ import annotations

import pytest

from backend.api.app import SpeakRequest, api_speak, services
from backend.core.config import settings


@pytest.mark.parametrize("keep_screenshots", [False, True])
@pytest.mark.asyncio
async def test_transient_tts_wav_is_deleted_independently_of_screenshots(
    monkeypatch, tmp_path, keep_screenshots
) -> None:
    path = tmp_path / f"speech-{keep_screenshots}.wav"
    path.write_bytes(b"synthetic wav")

    async def synthesize(text, request_id):
        return path

    original_section = settings.section

    def section(name):
        if name == "privacy":
            return {"keep_screenshots": keep_screenshots}
        return original_section(name)

    monkeypatch.setattr(settings, "section", section)
    monkeypatch.setattr(services.tts, "synthesize", synthesize)
    response = await api_speak(SpeakRequest(text="teste", request_id="cleanup"))
    assert path.exists()
    assert response.background is not None
    await response.background()
    assert not path.exists()
