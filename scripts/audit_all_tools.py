# ruff: noqa: E402 -- executable script bootstraps the repository import path.
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api.app import services
from backend.tools.capability_audit import CAPABILITY_CONTRACTS


def load_smoke(path: str) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    return dict(payload.get("tool_results") or {})


def build_matrix(smoke: dict[str, Any]) -> list[dict[str, Any]]:
    registry = services.tools.registry
    registered = {item["name"] for item in registry.list_public()}
    contracted = set(CAPABILITY_CONTRACTS)
    missing = sorted(registered - contracted)
    stale = sorted(contracted - registered)
    if missing or stale:
        raise RuntimeError(
            f"Inventory mismatch. Missing contracts={missing}; contracts without tools={stale}"
        )
    matrix: list[dict[str, Any]] = []
    for item in registry.capability_matrix():
        evidence = smoke.get(item["tool"]) or {}
        matrix.append(
            {
                "Tool": item["tool"],
                "action": item["action"],
                "risk": item["risk"],
                "confirmation": item["confirmation_required"],
                "executor": item["executor"],
                "verification": item["verification_method"],
                "external_effect": item["effectful"],
                "tested": bool(evidence.get("tested")),
                "result": str(evidence.get("result") or "not_tested"),
                "evidence": str(evidence.get("evidence") or ""),
            }
        )
    return matrix


def markdown(matrix: list[dict[str, Any]], timestamp: str) -> str:
    lines = [
        "# JARVIS 0.3.6 capability matrix",
        "",
        f"Generated at: {timestamp}",
        "",
        "| Tool | Action | Risk | Confirmation | Executor | Verification | Tested | Result |",
        "|---|---|---|---:|---|---|---:|---|",
    ]
    for row in matrix:
        values = [
            row["Tool"],
            row["action"],
            row["risk"],
            "yes" if row["confirmation"] else "no",
            row["executor"],
            row["verification"],
            "yes" if row["tested"] else "no",
            row["result"],
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit all registered tools.")
    parser.add_argument("--smoke-report", default="")
    parser.add_argument(
        "--output-json", default="artifacts/tool-capability-matrix.json"
    )
    parser.add_argument(
        "--output-markdown", default="artifacts/tool-capability-matrix.md"
    )
    args = parser.parse_args()

    timestamp = datetime.now().astimezone().isoformat()
    matrix = build_matrix(load_smoke(args.smoke_report))
    counts = {
        "registered": len(matrix),
        "effectful": sum(bool(row["external_effect"]) for row in matrix),
        "confirmation_required": sum(bool(row["confirmation"]) for row in matrix),
        "tested": sum(bool(row["tested"]) for row in matrix),
        "passed": sum(row["result"] == "passed" for row in matrix),
        "limited": sum(row["result"] == "limited" for row in matrix),
        "failed": sum(row["result"] == "failed" for row in matrix),
    }
    payload = {
        "version": "0.3.6",
        "generated_at": timestamp,
        "counts": counts,
        "matrix": matrix,
        "unsupported_power_actions": ["logoff", "sleep", "hibernate"],
    }
    json_path = Path(args.output_json)
    md_path = Path(args.output_markdown)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(markdown(matrix, timestamp), encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))
    print(json_path.resolve())
    print(md_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
