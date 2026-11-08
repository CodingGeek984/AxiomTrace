"""Polyglot code transpilation endpoint.

POST /api/v1/transpile
{
  "code": "def process(user_id: int) -> str: ...",
  "source_language": "python",
  "target_language": "typescript",
  "node_id": "n123",        // optional, for context
  "api_key": "sk-..."       // optional; also accepted via X-API-Key
}
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from flask import Blueprint, request
from marshmallow import Schema, fields

from services.node_source import resolve_node_source
from services.transpiler_service import (
    DEFAULT_TARGET,
    SUPPORTED_LANGUAGES,
    TranspileContext,
    TranspilationError,
    TranspilerService,
    normalize_language,
)
from utils import ApiError, get_logger, safe_load, success_response

logger = get_logger("transpiler")

transpile_bp = Blueprint("transpile", __name__, url_prefix="/api/v1/transpile")

_service = TranspilerService()


class TranspileRequestSchema(Schema):
    code = fields.Str(load_default="")
    label = fields.Str(load_default=None, allow_none=True)
    file = fields.Str(load_default=None, allow_none=True)
    module = fields.Str(load_default=None, allow_none=True)
    fallback_code = fields.Str(load_default=None, allow_none=True)
    source_language = fields.Str(load_default="python")
    target_language = fields.Str(load_default=DEFAULT_TARGET)
    node_id = fields.Str(load_default=None, allow_none=True)
    api_key = fields.Str(load_default=None, allow_none=True)
    context = fields.Dict(load_default=None, allow_none=True)


def _load_request() -> dict[str, Any]:
    raw = request.get_json(silent=True) or {}
    payload = dict(raw) if isinstance(raw, dict) else {}
    # Accept a couple of friendly aliases without changing the documented
    # schema, then let the service perform canonical validation.
    if "source" in payload and "source_language" not in payload:
        payload["source_language"] = payload["source"]
    if "target" in payload and "target_language" not in payload:
        payload["target_language"] = payload["target"]
    # Graceful normalisation: missing code falls back to "".
    return safe_load(
        TranspileRequestSchema(),
        payload,
        defaults={"code": "", "source_language": "python", "target_language": DEFAULT_TARGET},
    )


def _api_key_from_request(data: dict[str, Any]) -> str | None:
    key = data.get("api_key")
    if key:
        return str(key).strip() or None
    header_key = request.headers.get("X-API-Key")
    return header_key.strip() if header_key else None


def _payload(result: Any, node_id: str | None) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "source_language": result.source_language,
        "target_language": result.target_language,
        "source_code": result.source_code,
        "translated_code": result.translated_code,
        "confidence": result.confidence,
        "warnings": list(result.warnings),
        "used_ai_fallback": result.used_ai_fallback,
        "fallback_reason": result.fallback_reason,
    }


@transpile_bp.post("")
def transpile_node() -> tuple[dict[str, Any], int]:
    """Transpile a code snippet from one language to another."""
    data = _load_request()
    api_key = _api_key_from_request(data)

    # Graceful source resolution: a node with no bound text transpiles a
    # generated fallback derived from its label / file path / module instead
    # of failing with "No source code supplied to the transpiler."
    raw_code = data.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code.strip() else ""
    source_origin = "bound"
    if not code:
        code, source_origin = resolve_node_source(
            "",
            label=data.get("label"),
            file_path=data.get("file"),
            module=data.get("module"),
            fallback=data.get("fallback_code"),
            language=data.get("source_language"),
        )
        # The generated stand-in is always Python, whatever the node claims.
        data["source_language"] = "python"

    try:
        context = None
        if data.get("context"):
            ctx_data = data["context"]
            context = TranspileContext(
                imports=tuple(ctx_data.get("imports", [])),
                type_hints=ctx_data.get("type_hints", {}),
                surrounding_scope=ctx_data.get("surrounding_scope", ""),
            )

        result = _service.transpile_node(
            code,
            source_language=data["source_language"],
            target_language=data["target_language"],
            context=context,
            api_key=api_key,
        )
    except TranspilationError as error:
        # Invalid language ids or empty source are genuine client mistakes.
        raise ApiError(str(error), 400) from error
    except Exception as error:  # noqa: BLE001 - never surface a 500 to the UI
        logger.exception("Unexpected transpilation failure")
        target = normalize_language(data.get("target_language"), default=DEFAULT_TARGET) or DEFAULT_TARGET
        result = _service.fallback_result(
            code,
            target_language=target,
            reason=f"{type(error).__name__}: {error}",
        )

    if source_origin != "bound":
        note = (
            "No source text was bound to this node; a generated fallback "
            "derived from the node label/file path was transpiled instead."
        )
        result = replace(result, warnings=tuple(result.warnings) + (note,))

    return success_response(_payload(result, data.get("node_id")))


@transpile_bp.get("/languages")
def list_languages() -> tuple[dict[str, Any], int]:
    """Return the list of supported source/target languages."""
    return success_response(
        {
            "languages": list(SUPPORTED_LANGUAGES),
            "default_target": DEFAULT_TARGET,
        }
    )


__all__ = ["transpile_bp"]
