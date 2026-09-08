from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.core.i18n import tr


ToolOutcome = Literal["completed", "failed", "needs_input"]
_CONTRACT_FIELDS = {
    "success",
    "verified",
    "error",
    "data",
    "duration_ms",
    "verification",
    "detail",
}


@dataclass(slots=True)
class ToolResult:
    """Single result contract shared by every executable capability."""

    success: bool
    verified: bool
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    verification: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # Keep top-level fields compatible with deterministic readers.
        return {**value, **self.data}


def explicit_success(
    result: Any = None,
    *,
    verified: bool = True,
    verification: str = "result_read_back",
    detail: str = "",
) -> dict[str, Any]:
    """Build a complete successful result envelope."""

    if isinstance(result, ToolResult):
        return result.as_dict()
    if isinstance(result, dict):
        if _has_complete_contract(result):
            return normalize_tool_result(result)
        data = {
            key: value
            for key, value in result.items()
            if key not in _CONTRACT_FIELDS
        }
        verification = str(result.get("verification") or verification)
        detail = str(result.get("detail") or detail)
    elif isinstance(result, list):
        data = {"items": result}
    elif result is None:
        data = {}
    else:
        data = {"value": result}
    return ToolResult(
        success=True,
        verified=verified,
        error=None,
        data=data,
        verification=verification,
        detail=detail,
    ).as_dict()


def explicit_failure(
    error: str,
    *,
    detail: str = "",
    data: dict[str, Any] | None = None,
    verification: str = "",
) -> dict[str, Any]:
    return ToolResult(
        success=False,
        verified=False,
        error=error,
        data=data or {},
        verification=verification,
        detail=detail or error,
    ).as_dict()


def normalize_tool_result(result: Any, *, duration_ms: float | None = None) -> dict[str, Any]:
    """Validate and normalize a handler result without inventing success."""

    if isinstance(result, ToolResult):
        normalized = result.as_dict()
    elif isinstance(result, dict) and _has_complete_contract(result):
        data = result.get("data") or {}
        assert isinstance(data, dict)
        mirrored = {
            key: value
            for key, value in result.items()
            if key not in _CONTRACT_FIELDS
        }
        normalized = ToolResult(
            success=result["success"],
            verified=result["verified"],
            error=result.get("error"),
            data={**data, **mirrored},
            duration_ms=float(result.get("duration_ms") or 0.0),
            verification=str(result.get("verification") or ""),
            detail=str(result.get("detail") or ""),
        ).as_dict()
    else:
        normalized = explicit_failure(
            "invalid_tool_result",
            detail=(
                tr("A ferramenta violou o contrato: o resultado precisa declarar "
                "success, verified, error, data e duration_ms.", "The tool violated the result contract: it must declare "
                "success, verified, error, data and duration_ms.")
            ),
            verification="contract_rejected",
        )
    if duration_ms is not None:
        normalized["duration_ms"] = round(max(0.0, duration_ms), 3)
    if normalized["success"] is True and normalized["verified"] is not True:
        failed = explicit_failure(
            "verification_required",
            detail=tr("A ferramenta declarou sucesso sem comprovar o efeito.", "The tool reported success without verifying its effect."),
            data=dict(normalized.get("data") or {}),
            verification=str(normalized.get("verification") or "not_verified"),
        )
        failed["duration_ms"] = normalized["duration_ms"]
        return failed
    return normalized


def tool_result_outcome(result: Any) -> ToolOutcome:
    """Only an explicit and verified success may become COMPLETED."""

    if not isinstance(result, dict):
        return "failed"
    if result.get("success") is True and result.get("verified") is True:
        return "completed"
    if result.get("error") in {
        "ambiguous_application",
        "needs_input",
        "ambiguous",
    }:
        return "needs_input"
    return "failed"


def tool_result_detail(result: Any) -> str:
    if isinstance(result, dict):
        if tool_result_outcome(result) == "completed" and result.get("verification"):
            return str(result["verification"])
        for key in (
            "detail",
            "error",
            "opened",
            "written",
            "created",
            "closed",
            "path",
        ):
            if result.get(key):
                return str(result[key])
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("opened", "written", "created", "closed", "path"):
                if data.get(key):
                    return str(data[key])
    return tr("A ferramenta não retornou sucesso explícito e verificável.", "The tool did not return explicit, verifiable success.")


def tool_result_succeeded(result: Any) -> bool:
    return tool_result_outcome(result) == "completed"


def _has_complete_contract(result: dict[str, Any]) -> bool:
    return (
        isinstance(result.get("success"), bool)
        and isinstance(result.get("verified"), bool)
        and "error" in result
        and ("data" not in result or isinstance(result.get("data"), dict))
        and (
            "duration_ms" not in result
            or isinstance(result.get("duration_ms"), (int, float))
        )
    )
