"""Documentation generation endpoints.

Three routes share the same validated payload:

* ``POST /api/v1/docs/generate`` - OpenAPI + Markdown in one response
* ``POST /api/v1/docs/openapi``  - OpenAPI 3.0 JSON only
* ``POST /api/v1/docs/markdown`` - Markdown architecture specification only

The split lets the frontend download either artefact without transferring the
other, while the combined route keeps integrations simple.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from schemas import DocGenerateRequestSchema
from services.doc_generator import DocGenerationError, DocGenerationService
from utils import ApiError, get_logger, safe_load, success_response

logger = get_logger("docs")

doc_bp = Blueprint("docs", __name__, url_prefix="/api/v1/docs")

_service = DocGenerationService()


def _load_request() -> dict[str, Any]:
    raw = request.get_json(silent=True) or {}
    payload = dict(raw) if isinstance(raw, dict) else {}
    return safe_load(
        DocGenerateRequestSchema(),
        payload,
        defaults={
            "nodes": [],
            "edges": [],
            "project_name": "Untitled",
            "version": "1.0.0",
            "base_url": "http://127.0.0.1:5000",
        },
    )


def _generate(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return _service.generate(
            data.get("nodes", []),
            data.get("edges", []),
            project_name=data.get("project_name") or "Untitled",
            version=data.get("version") or "1.0.0",
            base_url=data.get("base_url") or "http://127.0.0.1:5000",
        )
    except DocGenerationError as error:
        raise ApiError(str(error), 400) from error
    except Exception as error:  # noqa: BLE001 - never let docs crash the worker
        logger.exception("Unexpected documentation failure")
        raise ApiError(
            f"Documentation generation failed: {type(error).__name__}: {error}",
            500,
        ) from error


@doc_bp.post("/generate")
def generate_docs() -> tuple[dict[str, Any], int]:
    """Generate both the OpenAPI document and the Markdown specification."""
    data = _load_request()
    result = _generate(data)
    return success_response(
        {
            "project_name": result["project_name"],
            "version": result["version"],
            "generated_at": result["generated_at"],
            "openapi": result["openapi"],
            "markdown": result["markdown"],
            "summary": result["summary"],
            "filenames": result["filenames"],
        }
    )


@doc_bp.post("/openapi")
def generate_openapi_docs() -> tuple[dict[str, Any], int]:
    """Generate only the OpenAPI 3.0 document."""
    data = _load_request()
    result = _generate(data)
    return success_response(
        {
            "project_name": result["project_name"],
            "version": result["version"],
            "generated_at": result["generated_at"],
            "spec": result["openapi"],
            "summary": result["summary"],
            "filename": result["filenames"]["openapi"],
        }
    )


@doc_bp.post("/markdown")
def generate_markdown_docs() -> tuple[dict[str, Any], int]:
    """Generate only the Markdown architecture specification."""
    data = _load_request()
    result = _generate(data)
    return success_response(
        {
            "project_name": result["project_name"],
            "version": result["version"],
            "generated_at": result["generated_at"],
            "markdown": result["markdown"],
            "summary": result["summary"],
            "filename": result["filenames"]["markdown"],
        }
    )


__all__ = ["doc_bp", "generate_docs", "generate_markdown_docs", "generate_openapi_docs"]
