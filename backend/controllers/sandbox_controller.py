"""Sandbox execution and mutation stress-test endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from schemas import ExecuteRequestSchema, MutateRequestSchema
from services import SandboxExecutionService, record_execution
from utils import get_logger, safe_load, success_response

logger = get_logger("sandbox")

sandbox_bp = Blueprint("sandbox", __name__, url_prefix="/api/v1/sandbox")

_sandbox = SandboxExecutionService()


def _resolve_input(data: dict[str, Any]) -> Any:
    """Prefer the canonical ``input_payload`` key, fall back to ``input_data``."""
    payload = data.get("input_payload")
    if payload is None:
        payload = data.get("input_data")
    return {} if payload is None else payload


def _resolve_code(data: dict[str, Any]) -> str:
    """Prefer ``code``; accept the legacy ``source`` alias for the same text."""
    code = data.get("code")
    if isinstance(code, str) and code.strip():
        return code
    source = data.get("source")
    if isinstance(source, str) and source.strip():
        return source
    return ""


def _source_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """Identity fields the sandbox uses to synthesise fallback source."""
    return {
        "label": data.get("label"),
        "file_path": data.get("file"),
        "module": data.get("module"),
        "fallback_code": data.get("fallback_code"),
        "language": data.get("language"),
    }


def _safe_log(
    node_id: str,
    input_payload: Any,
    report: dict[str, Any],
    status: str,
    runtime_ms: float,
    project_id: int | None,
    kind: str,
) -> None:
    try:
        record_execution(
            node_id=node_id,
            input_payload=input_payload,
            output_payload=report,
            status=status,
            runtime_ms=runtime_ms,
            project_id=project_id,
            kind=kind,
        )
    except Exception:  # noqa: BLE001 - logging must not break execution
        logger.warning("Could not persist %s log for node %s", kind, node_id)


@sandbox_bp.post("/execute")
def execute_node() -> tuple[dict[str, Any], int]:
    """POST /api/v1/sandbox/execute"""
    data = safe_load(
        ExecuteRequestSchema(),
        request.get_json(silent=True) or {},
        defaults={"node_id": "manual", "code": ""},
    )
    input_payload = _resolve_input(data)

    report = _sandbox.execute_node(
        _resolve_code(data),
        input_payload,
        timeout=data.get("timeout"),
        **_source_kwargs(data),
    )

    _safe_log(
        node_id=data["node_id"],
        input_payload=input_payload,
        report=report,
        status=str(report.get("status", "error")),
        runtime_ms=float(report.get("execution_time", 0.0)),
        project_id=data.get("project_id"),
        kind="execute",
    )

    return success_response({"node_id": data["node_id"], "result": report})


@sandbox_bp.post("/mutate")
def mutate_node() -> tuple[dict[str, Any], int]:
    """POST /api/v1/sandbox/mutate"""
    data = safe_load(
        MutateRequestSchema(),
        request.get_json(silent=True) or {},
        defaults={"node_id": "manual", "code": "", "iterations": 1},
    )

    report = _sandbox.run_mutation_test(
        _resolve_code(data),
        iterations=data.get("iterations", 1),
        timeout=data.get("timeout"),
        **_source_kwargs(data),
    )

    _safe_log(
        node_id=data["node_id"],
        input_payload={"mutation": True, "iterations": data.get("iterations", 1)},
        report={
            "verdict": report.get("verdict"),
            "crash_rate": report.get("crash_rate"),
            "failed": report.get("failed"),
            "total_runs": report.get("total_runs"),
        },
        status="error" if report.get("failed") else "success",
        runtime_ms=float(report.get("duration_ms", 0.0)),
        project_id=data.get("project_id"),
        kind="mutate",
    )

    return success_response({"node_id": data["node_id"], "report": report})


__all__ = ["sandbox_bp"]
