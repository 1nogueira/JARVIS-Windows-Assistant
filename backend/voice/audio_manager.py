from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class AudioManager:
    @staticmethod
    def find_ffmpeg() -> str | None:
        configured = os.getenv("JARVIS_FFMPEG", "").strip()
        if configured and Path(configured).is_file():
            return configured
        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if local_app_data:
            package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            matches = sorted(package_root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"), reverse=True)
            if matches:
                return str(matches[0])
        for candidate in (
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    async def normalize_to_wav(self, data: bytes, content_type: str = "audio/wav") -> bytes:
        if content_type in {"audio/wav", "audio/x-wav", "audio/wave"} and data[:4] == b"RIFF":
            return data
        ffmpeg = self.find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "O áudio do navegador precisa do FFmpeg para conversão. Instale-o ou envie WAV."
            )
        return await asyncio.to_thread(self._convert, ffmpeg, data)

    @staticmethod
    def _convert(ffmpeg: str, data: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="jarvis-audio-") as directory:
            source = Path(directory) / "input.webm"
            target = Path(directory) / "output.wav"
            source.write_bytes(data)
            process = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-ar", "16000", "-ac", "1", str(target)],
                capture_output=True,
                timeout=30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if process.returncode != 0 or not target.exists():
                raise RuntimeError("Não foi possível converter o áudio capturado.")
            return target.read_bytes()
