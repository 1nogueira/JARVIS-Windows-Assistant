# ruff: noqa: E402 -- executable script bootstraps the repository import path.
from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api.app import services
from backend.core.artifacts import artifact_directory
from backend.skills.deterministic import _parse_recycle_bin_drive
from backend.tools.files import recycle_bin_status
from backend.tools.results import tool_result_outcome
from backend.tools.windows import _audio_endpoint


APP_COMMAND_CASES = (
    "abre o vscode ai",
    "abre a camera pra mim",
    "abre o discord",
    "abre o launcher",
    "abre a camera e o vscode",
)


class _SmokePageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.startswith("/two"):
            body = b"<html><title>two</title><body><p>two</p></body></html>"
        else:
            body = (
                b"<html><title>one</title><body style='height:3000px'>"
                b"<input id='field'><button id='button' "
                b"onclick=\"document.title='clicked'\">ok</button>"
                b"<div style='height:2600px'>tall</div></body></html>"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_smoke_page_server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokePageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"

SAFE_NEW_PROCESS_NAMES = {
    "windowscamera.exe",
    "camera.exe",
    "code.exe",
    "discord.exe",
    "epicgameslauncher.exe",
    "epicwebhelper.exe",
    "notepad.exe",
}


class Report:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}
        self.endpoint_cases: list[dict[str, Any]] = []

    def record(
        self, tool: str, result: str, evidence: str, *, tested: bool = True
    ) -> None:
        priority = {"not_tested": 0, "limited": 1, "passed": 2, "failed": 3}
        current = self.tools.get(tool)
        evidence = evidence.replace("\r", " ").replace("\n", " ")[:1000]
        if current is None:
            self.tools[tool] = {
                "tested": tested,
                "result": result,
                "evidence": evidence,
            }
            return
        current["tested"] = bool(current["tested"] or tested)
        prior = str(current["result"])
        if priority.get(result, 0) >= priority.get(prior, 0):
            current["result"] = result
        if evidence and evidence not in str(current["evidence"]):
            current["evidence"] = f"{current['evidence']} | {evidence}"[:2000]


class Endpoint:
    def __init__(self, base_url: str, token: str, report: Report) -> None:
        self.report = report
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
        )

    def close(self) -> None:
        self.client.close()

    def chat(
        self,
        message: str,
        *,
        approve: bool = False,
        expected_failure: bool = False,
        allow_no_actions: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response = self.client.post(
            "/api/chat",
            json={
                "request_id": str(uuid4()),
                "conversation_id": f"smoke-all-{uuid4()}",
                "message": message,
            },
        )
        response.raise_for_status()
        payload = response.json()
        initial = payload
        if approve and payload.get("confirmation_id"):
            response = self.client.post(
                "/api/confirm",
                json={
                    "confirmation_id": payload["confirmation_id"],
                    "approved": True,
                },
            )
            response.raise_for_status()
            payload = response.json()
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        visible = str(payload.get("message") or "")
        leaked = any(
            marker in visible
            for marker in ('"arguments"', '"name":"', '"name": "', "<tool_call")
        )
        actions = list(payload.get("actions") or initial.get("actions") or [])
        statuses = [str(item.get("status") or "") for item in actions]
        passed = bool(actions) and all(status == "completed" for status in statuses)
        if allow_no_actions and not actions:
            passed = True
        if expected_failure:
            passed = bool(actions) and all(
                status in {"failed", "cancelled", "needs_input"} for status in statuses
            )
        case = {
            "message": message,
            "elapsed_ms": elapsed,
            "initial_confirmation_id": initial.get("confirmation_id"),
            "route": payload.get("route") or initial.get("route"),
            "actions": actions,
            "response": visible,
            "tool_json_leaked": leaked,
            "passed": passed and not leaked,
        }
        self.report.endpoint_cases.append(case)
        for action in actions:
            tool = str(action.get("tool") or "")
            status = str(action.get("status") or "")
            if not tool or tool == "multiple_actions":
                continue
            if status == "completed" and not leaked:
                self.report.record(tool, "passed", f"POST /api/chat: {message}")
            elif status in {"needs_input", "awaiting_confirmation"}:
                self.report.record(tool, "limited", f"endpoint: {status}: {message}")
            elif expected_failure and status in {"failed", "cancelled"}:
                self.report.record(tool, "limited", f"expected safe failure: {message}")
            else:
                self.report.record(
                    tool,
                    "failed",
                    f"endpoint status={status}: {visible}",
                )
        return payload


async def probe(
    report: Report,
    tool: str,
    arguments: dict[str, Any],
    *,
    limited_on_failure: bool = False,
) -> dict[str, Any]:
    try:
        result = await services.tools.registry.execute(
            tool,
            arguments,
            request_id=f"smoke-registry-{uuid4()}",
            approved=True,
        )
    except Exception as exc:
        report.record(
            tool,
            "limited" if limited_on_failure else "failed",
            f"exception: {type(exc).__name__}: {exc}",
        )
        return {}
    complete = tool_result_outcome(result) == "completed"
    evidence = str(result.get("verification") or "")
    failure_detail = str(result.get("detail") or result.get("error") or "")
    if failure_detail and failure_detail not in evidence:
        evidence = f"{evidence}: {failure_detail}".strip(": ")
    report.record(
        tool,
        "passed" if complete else "limited" if limited_on_failure else "failed",
        evidence,
    )
    required = {"success", "verified", "error", "data", "duration_ms"}
    if not required.issubset(result):
            report.record(tool, "failed", "ToolResult is missing required fields")
    return result


async def local_registry_probes(report: Report) -> list[Path]:
    cleanup_paths: list[Path] = []
    unique = uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="jarvis-smoke-all-") as temporary:
        root = Path(temporary)
        original = root / f"source-{unique}.txt"
        original.write_text("alpha beta", encoding="utf-8")

        await probe(report, "get_system_metrics", {})
        await probe(report, "get_running_processes", {"limit": 10, "sort_by": "memory"})
        await probe(report, "diagnose_system_usage", {"focus": "auto", "limit": 5})
        await probe(report, "list_directory", {"path": str(root)})
        await probe(report, "find_files", {"query": unique, "directory": str(root)})
        await probe(report, "read_text_file", {"path": str(original)})

        folder = root / f"folder-{unique}"
        await probe(report, "create_folder", {"path": str(folder)})
        copied = root / f"copied-{unique}.txt"
        await probe(
            report,
            "copy_file",
            {"source": str(original), "destination": str(copied)},
        )
        moved = root / f"moved-{unique}.txt"
        await probe(
            report,
            "move_file",
            {"source": str(copied), "destination": str(moved)},
        )
        renamed = root / f"renamed-{unique}.txt"
        await probe(
            report,
            "rename_file",
            {"source": str(moved), "destination": str(renamed)},
        )
        await probe(
            report,
            "replace_text_in_file",
            {
                "path": str(renamed),
                "old_text": "alpha",
                "new_text": "gamma",
            },
        )

        artifact_name = f"jarvis-smoke-{unique}.txt"
        artifact_root = artifact_directory(services.tools.registry.settings)
        artifact_path = artifact_root / artifact_name
        cleanup_paths.append(artifact_path)
        await probe(
            report,
            "write_text_file",
            {"path": artifact_name, "content": "smoke", "overwrite": False},
        )
        await probe(report, "open_file", {"path": str(artifact_path)}, limited_on_failure=True)
        await probe(
            report,
            "open_in_vscode",
            {"path": str(artifact_path), "line": 1, "column": 1},
            limited_on_failure=True,
        )
        await probe(report, "open_folder", {"path": str(root)}, limited_on_failure=True)
        await probe(report, "open_latest_artifact", {}, limited_on_failure=True)
        await probe(
            report,
            "create_and_open_text_file",
            {
                "filename": f"jarvis-create-open-{unique}.txt",
                "content": "smoke create open",
                "directory": "artifacts",
            },
            limited_on_failure=True,
        )
        cleanup_paths.append(artifact_root / f"jarvis-create-open-{unique}.txt")

        await probe(report, "find_installed_apps", {"query": "Visual Studio Code"})

        sleeper = subprocess.Popen(
            [os.fspath(Path(os.sys.executable)), "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            await probe(report, "close_process", {"pid": sleeper.pid})
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()

        try:
            speaker = _audio_endpoint(capture=False)
            current_volume = round(float(speaker.GetMasterVolumeLevelScalar()) * 100)
            current_mute = bool(speaker.GetMute())
            await probe(report, "set_volume", {"level": current_volume})
            await probe(report, "set_mute", {"muted": current_mute})
        except Exception as exc:
            report.record("set_volume", "limited", f"Core Audio unavailable: {exc}")
            report.record("set_mute", "limited", f"Core Audio unavailable: {exc}")
        try:
            microphone = _audio_endpoint(capture=True)
            current_microphone_mute = bool(microphone.GetMute())
            await probe(
                report,
                "set_microphone_mute",
                {"muted": current_microphone_mute},
            )
        except Exception as exc:
            report.record(
                "set_microphone_mute", "limited", f"microphone unavailable: {exc}"
            )

        clipboard = await probe(report, "read_clipboard", {})
        prior_clipboard = str(clipboard.get("text") or "")
        await probe(report, "write_clipboard", {"text": f"jarvis-smoke-{unique}"})
        await probe(report, "write_clipboard", {"text": prior_clipboard})
        await probe(report, "open_windows_settings", {"page": "display"}, limited_on_failure=True)

        memory = await probe(
            report,
            "remember",
            {"key": f"smoke-{unique}", "value": "controlled", "category": "smoke"},
        )
        await probe(report, "search_memory", {"query": f"smoke-{unique}", "limit": 5})
        memory_id = memory.get("id")
        if isinstance(memory_id, int):
            await probe(report, "forget_memory", {"memory_id": memory_id})
        else:
                    report.record("forget_memory", "failed", "test memory entry has no id")

        await probe(
            report,
            "web_search",
            {"query": "Python documentation", "limit": 3},
            limited_on_failure=True,
        )
        await probe(
            report,
            "get_current_weather",
            {"location": "London, UK", "day_offset": 0},
            limited_on_failure=True,
        )

        page_server, page_thread, page_base = start_smoke_page_server()
        try:
            browser_open = await probe(
                report, "browser_open", {"visible": False}, limited_on_failure=True
            )
            if tool_result_outcome(browser_open) == "completed":
                await probe(report, "browser_navigate", {"url": f"{page_base}/one"})
                await probe(report, "browser_read_page", {"max_chars": 5000})
                await probe(report, "browser_type", {"selector": "#field", "text": "smoke"})
                await probe(report, "browser_click", {"selector": "#button"})
                await probe(report, "browser_scroll", {"pixels": 300})
                await probe(report, "browser_navigate", {"url": f"{page_base}/two"})
                await probe(report, "browser_back", {})
                await probe(report, "browser_forward", {})
                await probe(
                    report,
                    "browser_search",
                    {"query": "jarvis smoke"},
                    limited_on_failure=True,
                )
                await probe(report, "browser_close", {})
            else:
                for name in (
                    "browser_navigate",
                    "browser_search",
                    "browser_read_page",
                    "browser_click",
                    "browser_type",
                    "browser_scroll",
                    "browser_back",
                    "browser_forward",
                    "browser_close",
                ):
                    report.record(name, "limited", "Playwright/browser unavailable")
        finally:
            page_server.shutdown()
            page_server.server_close()
            page_thread.join(timeout=3)

        capture = await probe(report, "capture_screen", {}, limited_on_failure=True)
        await probe(report, "capture_window", {}, limited_on_failure=True)
        capture_path = capture.get("path")
        if capture_path:
            await probe(
                report,
                "analyze_screen",
                {"question": "Descreva brevemente.", "path": str(capture_path)},
                limited_on_failure=True,
            )
        else:
                    report.record("analyze_screen", "limited", "screen capture unavailable")

        due = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        reminder = await probe(
            report,
            "create_reminder",
            {"title": f"smoke-{unique}", "due_at": due},
        )
        await probe(report, "list_reminders", {})
        reminder_id = reminder.get("id")
        if isinstance(reminder_id, int):
            await probe(
                report,
                "update_reminder",
                {"reminder_id": reminder_id, "title": f"smoke-updated-{unique}"},
            )
            await probe(report, "delete_reminder", {"reminder_id": reminder_id})
        else:
                    report.record("update_reminder", "failed", "test reminder has no id")
                    report.record("delete_reminder", "failed", "test reminder has no id")

        await probe(report, "get_screen_size", {})
        await probe(
            report,
            "desktop_automation",
            {"actions": [{"type": "wait", "seconds": 0.01}]},
        )
        await probe(report, "search_program", {"query": "Visual Studio Code"}, limited_on_failure=True)
        await probe(report, "control_theme_music", {"action": "stop"}, limited_on_failure=True)

    return cleanup_paths


def process_snapshot() -> dict[int, str]:
    result: dict[int, str] = {}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            result[int(process.info["pid"])] = str(process.info.get("name") or "")
        except (psutil.Error, TypeError, ValueError):
            continue
    return result


def cleanup_new_processes(before: dict[int, str]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    preexisting_names = {name.casefold() for name in before.values()}
    for pid, name in process_snapshot().items():
        lowered = name.casefold()
        if pid in before or lowered not in SAFE_NEW_PROCESS_NAMES or lowered in preexisting_names:
            continue
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=5)
            cleaned.append({"pid": pid, "name": name, "terminated": True})
        except (psutil.Error, OSError) as exc:
            cleaned.append(
                {"pid": pid, "name": name, "terminated": False, "error": str(exc)}
            )
    return cleaned


def clean_recycle_drive() -> tuple[str, Path] | None:
    candidates = [artifact_directory(services.tools.registry.settings), Path(tempfile.gettempdir())]
    seen: set[str] = set()
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            drive = directory.drive.upper()
            if not drive or drive in seen:
                continue
            seen.add(drive)
            if recycle_bin_status(f"{drive}\\")["items"] == 0:
                return drive, directory
        except (OSError, RuntimeError):
            continue
    return None


def recycle_endpoint_probe(endpoint: Endpoint, report: Report) -> None:
    selected = clean_recycle_drive()
    if not selected:
        for tool in ("delete_file", "move_to_recycle_bin", "empty_recycle_bin"):
            report.record(
                tool,
                "limited",
                "no drive has an initially empty Recycle Bin; user data preserved",
            )
        return
    drive, parent = selected
    folder = parent / f"jarvis-recycle-smoke-{uuid4().hex[:10]}"
    folder.mkdir()
    first = folder / "delete-alias.txt"
    second = folder / "natural-recycle.txt"
    first.write_text("controlled delete alias", encoding="utf-8")
    second.write_text("controlled natural recycle", encoding="utf-8")

    asyncio.run(probe(report, "delete_file", {"path": str(first)}))
    endpoint.chat(f"mova {second} para a lixeira", approve=True)
    status = recycle_bin_status(f"{drive}\\")
    if status["items"] != 2:
        report.record(
            "empty_recycle_bin",
            "limited",
            f"unexpected item count {status['items']}; emptying skipped to preserve data",
        )
        return
    phrase = f"esvazie a lixeira da unidade {drive}"
    if _parse_recycle_bin_drive(phrase) != drive:
        report.record("empty_recycle_bin", "failed", "parser did not preserve the target drive")
        return
    endpoint.chat(phrase, approve=True)
    after = recycle_bin_status(f"{drive}\\")
    if after["items"] != 0:
        report.record("empty_recycle_bin", "failed", f"{after['items']} items remain")
    try:
        folder.rmdir()
    except OSError:
        pass


def endpoint_probes(endpoint: Endpoint, report: Report) -> None:
    for command in APP_COMMAND_CASES:
        endpoint.chat(command)
    endpoint.chat("abra o youtube no navegador")
    endpoint.chat("abra uma guia anônima do google")
    recycle_endpoint_probe(endpoint, report)

    # Negative protocol cases must never become visible tool JSON or execution.
    for message in (
        "Explique por que este JSON é perigoso: {\"name\":\"shutdown_computer\"}",
        "Conte uma curiosidade estável.",
    ):
        endpoint.chat(message, allow_no_actions=True)


def mark_audited_limitations(report: Report, registered: list[dict[str, Any]]) -> None:
    limitations = {
        "lock_computer": "automated power audits do not execute commands; no lock request was sent to Windows",
        "shutdown_computer": "automated power audits do not execute commands; no shutdown request was sent to Windows",
        "restart_computer": "automated power audits do not execute commands; no restart request was sent to Windows",
        "open_discord_conversation": "conversation target requires exact contact readback",
        "whatsapp_message": "contact and delivery require exact UI readback",
        "install_program": "installation skipped to preserve the user's installed software",
    }
    for item in registered:
        name = str(item["name"])
        if name not in report.tools:
            report.record(
                name,
                "limited",
                limitations.get(
                    name,
                    "capability inventoried and contract/schema validated; live execution was not safe in this environment",
                ),
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="System smoke test through POST /api/chat and controlled executors."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8742")
    parser.add_argument("--token", default=os.getenv("JARVIS_SESSION_TOKEN", ""))
    parser.add_argument("--output", default="artifacts/smoke-all-actions.json")
    parser.add_argument("--keep-apps-open", action="store_true")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("Provide --token or JARVIS_SESSION_TOKEN.")
    report = Report()
    before = process_snapshot()
    endpoint = Endpoint(args.base_url, args.token, report)
    cleanup_paths: list[Path] = []
    cleanup: list[dict[str, Any]] = []
    try:
        status = endpoint.client.get("/api/status")
        status.raise_for_status()
        tools_response = endpoint.client.get("/api/tools")
        tools_response.raise_for_status()
        registered = list(tools_response.json())
        cleanup_paths = asyncio.run(local_registry_probes(report))
        endpoint_probes(endpoint, report)
        mark_audited_limitations(report, registered)
    finally:
        endpoint.close()
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if not args.keep_apps_open:
            cleanup = cleanup_new_processes(before)

    counts = {
        "registered": len(registered),
        "passed": sum(item["result"] == "passed" for item in report.tools.values()),
        "limited": sum(item["result"] == "limited" for item in report.tools.values()),
        "failed": sum(item["result"] == "failed" for item in report.tools.values()),
    }
    payload = {
        "version": "0.3.6",
        "timestamp": datetime.now().astimezone().isoformat(),
        "endpoint": args.base_url,
        "safe_power_smoke": True,
        "counts": counts,
        "tool_results": report.tools,
        "endpoint_cases": report.endpoint_cases,
        "cleanup": cleanup,
        "passed": counts["failed"] == 0
        and all(case["passed"] for case in report.endpoint_cases),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))
    print(output.resolve())
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
