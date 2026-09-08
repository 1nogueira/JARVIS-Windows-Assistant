# ruff: noqa: E402 -- executable script bootstraps the repository import path.

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.routing import RequestRouter
from backend.skills import SkillRegistry
from backend.skills.windows_open import task_graph_for_open


def local_benchmarks(rounds: int) -> dict[str, Any]:
    skills = SkillRegistry()
    skills.discover()
    router = RequestRouter(skills)
    prompts = (
        "What is the capital of Australia?",
        "Open Discord.",
        "Open YouTube, VS Code, Discord, Edge, Epic Games and WhatsApp.",
        "Research sodium batteries in depth.",
        "Summarize this every morning.",
    )
    started = time.perf_counter()
    for _ in range(rounds):
        for prompt in prompts:
            router.route(prompt)
    router_ms = (time.perf_counter() - started) * 1000 / (rounds * len(prompts))
    started = time.perf_counter()
    for index in range(rounds):
        task_graph_for_open(prompts[2], f"bench-{index}")
    graph_ms = (time.perf_counter() - started) * 1000 / rounds
    return {
        "router_ms_average": round(router_ms, 4),
        "six_target_graph_ms_average": round(graph_ms, 4),
        "rounds": rounds,
    }


def stream_chat(
    client: httpx.Client,
    token: str,
    message: str,
    conversation: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token: float | None = None
    response_payload: dict[str, Any] = {}
    with client.stream(
        "POST",
        "/api/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        json={
            "request_id": str(uuid4()),
            "message": message,
            "conversation_id": conversation,
        },
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:].lstrip())
            if event.get("type") == "token" and first_token is None:
                first_token = time.perf_counter()
            if event.get("type") == "complete":
                response_payload = event.get("response") or {}
    completed = time.perf_counter()
    metrics = response_payload.get("metrics") or {}
    return {
        "wall_ms": round((completed - started) * 1000, 2),
        "observed_ttft_ms": round((first_token - started) * 1000, 2) if first_token else None,
        "backend_ttft_ms": metrics.get("ttft_ms"),
        "tokens_per_second": metrics.get("tokens_per_second"),
        "output_tokens": metrics.get("output_tokens"),
        "route": response_payload.get("route"),
        "agent": response_payload.get("agent"),
        "model": response_payload.get("model"),
    }


def remote_benchmarks(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token or os.getenv("JARVIS_SESSION_TOKEN", "")
    if len(token) < 32:
        return {"skipped": "Set --token or JARVIS_SESSION_TOKEN to benchmark the authenticated API."}
    result: dict[str, Any] = {}
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        status_times = []
        for _ in range(5):
            started = time.perf_counter()
            response = client.get("/api/status")
            response.raise_for_status()
            status_times.append((time.perf_counter() - started) * 1000)
        result["status_warm_ms_median"] = round(statistics.median(status_times), 2)
        result["simple_cold"] = stream_chat(
            client, token, "Answer in one sentence: what is the capital of Australia?", "benchmark-cold"
        )
        result["simple_warm"] = stream_chat(
            client, token, "Responda apenas: Canberra", "benchmark-warm"
        )
        if args.web:
            result["research"] = stream_chat(
                client, token, "Research recent advances in sodium batteries in depth.", "benchmark-web"
            )
        if args.voice:
            started = time.perf_counter()
            response = client.post(
                "/api/voice/speak",
                headers={"Authorization": f"Bearer {token}"},
                json={"text": "Local speech synthesis test.", "request_id": str(uuid4())},
            )
            response.raise_for_status()
            result["tts_startup_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if args.audio:
            audio = Path(args.audio)
            started = time.perf_counter()
            response = client.post(
                "/api/voice/transcribe",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/wav"},
                content=audio.read_bytes(),
            )
            response.raise_for_status()
            result["stt_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducible benchmark of the local JARVIS runtime")
    parser.add_argument("--base-url", default="http://127.0.0.1:8742")
    parser.add_argument("--token", default="")
    parser.add_argument("--rounds", type=int, default=1_000)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--web", action="store_true", help="Include optional external web research")
    parser.add_argument("--voice", action="store_true", help="Include optional Piper speech synthesis")
    parser.add_argument("--audio", help="WAV file for the optional speech recognition benchmark")
    parser.add_argument("--output", type=Path, help="Output JSON file")
    args = parser.parse_args()
    report = {
        "timestamp": time.time(),
        "local": local_benchmarks(max(1, args.rounds)),
        "runtime": remote_benchmarks(args),
        "notes": {
            "external_web_opt_in": bool(args.web),
            "voice_opt_in": bool(args.voice),
            "stt_audio_provided": bool(args.audio),
            "destructive_or_app_open_benchmarks": "not run automatically",
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
