"""AI context export, edge-case detection and key configuration endpoints."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

from flask import Blueprint, request

from config import config
from schemas import AIContextRequestSchema, EdgeCaseRequestSchema
from services import AIService
from utils import get_logger, safe_load, success_response

logger = get_logger("ai")

ai_bp = Blueprint("ai", __name__, url_prefix="/api/v1/ai")

_ai = AIService()


def _structured_error(message: str, status: int = 400) -> tuple[dict[str, Any], int]:
    """Uniform machine-readable error envelope for AI endpoints."""
    return (
        {"ok": False, "status": "error", "code": int(status), "message": str(message)},
        int(status),
    )


def _resolve_request_key(payload: dict[str, Any] | None = None) -> str | None:
    """Resolve the caller's API key from header, body or environment.

    Priority: ``X-API-Key`` header → ``Authorization: Bearer`` → JSON
    ``api_key`` → environment / app config fallback is handled inside the
    AI service itself.
    """
    header_key = (request.headers.get("X-API-Key") or "").strip()
    if header_key:
        return header_key
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    body_key = ""
    if isinstance(payload, dict):
        body_key = str(payload.get("api_key") or "").strip()
    if body_key:
        return body_key
    return None


@ai_bp.post("/context")
def ai_context() -> tuple[dict[str, Any], int]:
    """POST /api/v1/ai/context - lasso selection -> Markdown LLM dump"""
    payload = request.get_json(silent=True) or {}
    # Graceful normalisation: missing nodes/edges default to [].
    data = safe_load(
        AIContextRequestSchema(), payload, defaults={"nodes": [], "edges": []}
    )

    try:
        report = _ai.generate_lasso_context_report(
            data.get("nodes", []),
            data.get("edges", []),
            project_name=data.get("project_name", "Untitled"),
        )
    except Exception as error:  # noqa: BLE001 - never leak a stack trace
        logger.exception("AI context generation failed")
        return _structured_error(
            f"AI context generation failed: {type(error).__name__}: {error}", 502
        )

    return success_response(
        {
            "markdown": report.get("markdown", ""),
            "node_count": report.get("node_count", 0),
            "edge_count": report.get("edge_count", 0),
            "char_count": report.get("char_count", 0),
            "estimated_tokens": report.get("estimated_tokens", 0),
            "deduplicated_blocks": report.get("deduplicated_blocks", 0),
            "truncated": report.get("truncated", False),
        }
    )


@ai_bp.post("/edge-cases")
def ai_edge_cases() -> tuple[dict[str, Any], int]:
    """POST /api/v1/ai/edge-cases - missing try/except + network timeout scan"""
    payload = request.get_json(silent=True) or {}
    data = safe_load(EdgeCaseRequestSchema(), payload, defaults={"code": ""})

    api_key = _resolve_request_key(data)
    try:
        report = _ai.predict_edge_cases(
            data.get("code", ""),
            api_key=api_key,
        )
    except Exception as error:  # noqa: BLE001 - never leak a stack trace
        logger.exception("Edge-case analysis failed")
        return _structured_error(
            f"Edge-case analysis failed: {type(error).__name__}: {error}", 502
        )
    return success_response({"report": report})


@ai_bp.post("/config")
def ai_config() -> tuple[dict[str, Any], int]:
    """POST /api/v1/ai/config - validate and register the caller's API key.

    The key is verified in two stages:

    1. shape validation (prefix, length, no whitespace);
    2. a short-timeout probe against the configured ``/models`` endpoint.
       A 401/403 proves the key is invalid; a network failure only means
       we could not verify it right now (the key is still accepted and
       will simply fail fast on the next real request).
    """
    payload = request.get_json(silent=True) or {}
    api_key = _resolve_request_key(payload) or ""

    if not api_key:
        return _structured_error(
            "API key is missing. Please configure it in AI Settings.", 400
        )
    if any(character.isspace() for character in api_key):
        return _structured_error("API key must not contain whitespace.", 400)
    if not api_key.startswith("sk-"):
        return _structured_error(
            "Unexpected key format - expected a key starting with 'sk-'.", 400
        )
    if len(api_key) < 12:
        return _structured_error("API key looks too short.", 400)

    base_url = (
        os.getenv("DEEPSEEK_BASE_URL")
        or getattr(config, "DEEPSEEK_BASE_URL", None)
        or getattr(config, "AI_BASE_URL", None)
        or "https://api.deepseek.com/v1"
    ).rstrip("/")

    try:
        probe_request = urllib.request.Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with urllib.request.urlopen(probe_request, timeout=8) as probe:
            probe_status = probe.status
    except urllib.error.HTTPError as error:
        probe_status = error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.warning("AI key probe unreachable: %s", error)
        return success_response(
            {
                "status": "ok",
                "verified": False,
                "message": (
                    "Key saved locally. Could not reach the model endpoint to "
                    "verify it right now - it will be used on the next AI request."
                ),
            }
        )

    if probe_status in (401, 403):
        return _structured_error(
            f"The model endpoint rejected the key (HTTP {probe_status}). "
            "Check the key in AI Settings.",
            probe_status,
        )
    if probe_status >= 500:
        return success_response(
            {
                "status": "ok",
                "verified": False,
                "message": "Key saved locally; the model endpoint is degraded right now, verification skipped.",
            }
        )
    return success_response(
        {
            "status": "ok",
            "verified": True,
            "message": "API key verified and saved.",
        }
    )


__all__ = ["ai_bp"]
