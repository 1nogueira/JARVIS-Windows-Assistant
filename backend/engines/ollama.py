from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from backend.core.config import SettingsStore
from backend.engines.base import EngineCapabilities, InferenceEngine, StreamChunk
from backend.engines.registry import EngineRegistry


class OllamaUnavailable(RuntimeError):
    pass


@EngineRegistry.register("ollama")
class OllamaEngine(InferenceEngine):
    """Ollama engine with a reusable client and TTL-based model discovery."""

    engine_id = "ollama"
    capabilities = EngineCapabilities(
        streaming=True,
        tool_calling=True,
        vision=True,
        embeddings=True,
        local=True,
    )

    def __init__(self, settings: SettingsStore, *, model_cache_ttl: float = 30.0) -> None:
        self.settings = settings
        self.model_cache_ttl = model_cache_ttl
        self._client: httpx.AsyncClient | None = None
        self._models: list[dict[str, Any]] = []
        self._models_at = 0.0
        self._models_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return str(
            self.settings.section("ollama").get("url", "http://127.0.0.1:11434")
        ).rstrip("/")

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout = float(
                self.settings.section("ollama").get("request_timeout_seconds", 90)
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            )
        return self._client

    async def list_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force and self._models and now - self._models_at < self.model_cache_ttl:
            return [dict(item) for item in self._models]
        async with self._models_lock:
            now = time.monotonic()
            if not force and self._models and now - self._models_at < self.model_cache_ttl:
                return [dict(item) for item in self._models]
            try:
                response = await self._http().get("/api/tags", timeout=3.0)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                if self._models:
                    return [dict(item) for item in self._models]
                raise OllamaUnavailable("Não foi possível consultar os modelos do Ollama.") from exc
            payload = response.json()
            self._models = [dict(item) for item in payload.get("models", [])]
            self._models_at = time.monotonic()
            return [dict(item) for item in self._models]

    async def resolve_model(self, requested: str | None = None) -> str:
        configured = requested or str(
            self.settings.section("ollama").get("chat_model", "")
        ).strip()
        models = await self.list_models()
        names = [str(item.get("name", "")) for item in models if item.get("name")]
        if configured in names:
            return configured
        base_match = next(
            (name for name in names if name.split(":", 1)[0] == configured), None
        )
        if base_match:
            return base_match
        if not names:
            raise OllamaUnavailable(
                "O Ollama está offline ou não possui modelos instalados."
            )
        priorities = ("qwen3", "qwen2.5", "llama3.2", "llama3.1", "mistral", "granite")
        return next(
            (name for prefix in priorities for name in names if prefix in name.casefold()),
            names[0],
        )

    def _payload(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        *,
        stream: bool,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        config = self.settings.section("ollama")
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": stream,
            "think": bool(config.get("think", False)),
            "keep_alive": str(config.get("keep_alive", "30m")),
            "options": {
                "temperature": float(config.get("temperature", 0.2)),
                "num_ctx": int(config.get("context_size", 8192)),
                "num_predict": int(config.get("max_output_tokens", 512)),
            },
        }
        if tools:
            payload["tools"] = list(tools)
        return payload

    async def generate(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resolved = await self.resolve_model(model)
        try:
            response = await self._http().post(
                "/api/chat", json=self._payload(messages, resolved, stream=False, tools=tools)
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise OllamaUnavailable("Não foi possível conectar ao Ollama.") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaUnavailable(
                f"O Ollama recusou a solicitação: {exc.response.text[:500]}"
            ) from exc
        return {"model": resolved, **response.json()}

    async def stream(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved = await self.resolve_model(model)
        try:
            async with self._http().stream(
                "POST", "/api/chat", json=self._payload(messages, resolved, stream=True)
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = data.get("message") or {}
                    yield StreamChunk(
                        content=str(message.get("content") or ""),
                        done=bool(data.get("done", False)),
                        model=str(data.get("model") or resolved),
                        prompt_tokens=int(data.get("prompt_eval_count") or 0),
                        output_tokens=int(data.get("eval_count") or 0),
                        total_duration_ns=int(data.get("total_duration") or 0),
                        eval_duration_ns=int(data.get("eval_duration") or 0),
                    )
        except httpx.ConnectError as exc:
            raise OllamaUnavailable("Não foi possível conectar ao Ollama.") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaUnavailable(
                f"O Ollama recusou a solicitação: {exc.response.text[:500]}"
            ) from exc

    async def health(self) -> dict[str, Any]:
        try:
            models = await self.list_models()
            return {
                "online": True,
                "engine": self.engine_id,
                "models": models,
                "capabilities": self.capabilities.__dict__
                if hasattr(self.capabilities, "__dict__")
                else {
                    "streaming": self.capabilities.streaming,
                    "tool_calling": self.capabilities.tool_calling,
                    "vision": self.capabilities.vision,
                    "embeddings": self.capabilities.embeddings,
                    "local": self.capabilities.local,
                },
            }
        except OllamaUnavailable:
            return {
                "online": False,
                "engine": self.engine_id,
                "models": [dict(item) for item in self._models],
                "capabilities": {
                    "streaming": True,
                    "tool_calling": True,
                    "vision": True,
                    "embeddings": True,
                    "local": True,
                },
            }

    async def warmup(self) -> None:
        try:
            model = await self.resolve_model()
            config = self.settings.section("ollama")
            response = await self._http().post(
                "/api/generate",
                json={
                    "model": model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": str(config.get("keep_alive", "30m")),
                },
                timeout=120,
            )
            response.raise_for_status()
        except (httpx.HTTPError, OllamaUnavailable):
            return

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

