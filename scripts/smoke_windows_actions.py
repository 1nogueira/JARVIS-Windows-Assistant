from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psutil


COMMANDS = (
    "abre o vscode ai",
    "abre a camera pra mim",
    "abre o discord",
    "abre o launcher",
    "abre a camera e o vscode",
    "abra o youtube no navegador",
    "abra o instagram no navegador",
    "abra o tiktok no navegador",
    "abra youtube instagram e tiktok no navegador",
)
SAFE_SMOKE_PROCESS_NAMES = {
    "windowscamera.exe",
    "camera.exe",
    "code.exe",
    "discord.exe",
    "epicgameslauncher.exe",
    "epicwebhelper.exe",
}


def process_snapshot() -> dict[int, dict[str, str]]:
    snapshot: dict[int, dict[str, str]] = {}
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            snapshot[int(process.info["pid"])] = {
                "name": str(process.info.get("name") or ""),
                "exe": str(process.info.get("exe") or ""),
            }
        except (psutil.Error, TypeError, ValueError):
            continue
    return snapshot


def post_command(client: httpx.Client, command: str) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(
        "/api/chat",
        json={
            "request_id": str(uuid4()),
            "conversation_id": f"windows-smoke-{uuid4()}",
            "message": command,
        },
        timeout=45,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.raise_for_status()
    payload = response.json()
    actions = payload.get("actions") or []
    statuses = [str(item.get("status")) for item in actions]
    return {
        "command": command,
        "elapsed_ms": elapsed_ms,
        "route": payload.get("route"),
        "agent": payload.get("agent"),
        "message": payload.get("message"),
        "actions": actions,
        "passed": bool(actions) and all(
            status in {"completed", "needs_input"} for status in statuses
        ),
        "tool_json_leaked": any(
            marker in str(payload.get("message") or "")
            for marker in ('"arguments"', '"name":"open_', '"name": "open_')
        ),
    }


def cleanup_new_smoke_processes(
    before: dict[int, dict[str, str]], after: dict[int, dict[str, str]]
) -> list[dict[str, Any]]:
    preexisting_names = {item["name"].casefold() for item in before.values()}
    cleaned: list[dict[str, Any]] = []
    for pid, item in after.items():
        name = item["name"].casefold()
        if pid in before or name not in SAFE_SMOKE_PROCESS_NAMES or name in preexisting_names:
            continue
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=5)
            cleaned.append({"pid": pid, "name": item["name"], "terminated": True})
        except (psutil.Error, OSError) as exc:
            cleaned.append(
                {"pid": pid, "name": item["name"], "terminated": False, "error": str(exc)}
            )
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test Windows actions through the frontend's POST /api/chat endpoint."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8742")
    parser.add_argument("--token", default=os.getenv("JARVIS_SESSION_TOKEN", ""))
    parser.add_argument("--output", default="artifacts/windows-actions-smoke.json")
    parser.add_argument("--keep-apps-open", action="store_true")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("Provide --token or JARVIS_SESSION_TOKEN.")

    before = process_snapshot()
    results: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {args.token}"},
    ) as client:
        status = client.get("/api/status", timeout=15)
        status.raise_for_status()
        for command in COMMANDS:
            result = post_command(client, command)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))

    after = process_snapshot()
    new_processes = [
        {"pid": pid, **item} for pid, item in after.items() if pid not in before
    ]
    cleanup = [] if args.keep_apps_open else cleanup_new_smoke_processes(before, after)
    report = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "endpoint": args.base_url,
        "commands": results,
        "new_processes": new_processes,
        "cleanup": cleanup,
        "passed": all(item["passed"] and not item["tool_json_leaked"] for item in results),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output.resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
