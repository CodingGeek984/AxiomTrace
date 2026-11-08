"""Project directory scan endpoints.

POST /api/v1/project/scan
    { "path": "/absolute/path/to/project" }
    -> layered architecture graph (see services.project_scanner)

POST /api/v1/project/scan/upload
    multipart/form-data with repeated ``files`` parts
    (filename carries the relative path, e.g. ``src/services/user.py``)
    -> same graph, built from the uploaded snapshot
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from services import project_scanner
from utils import get_logger, success_response

logger = get_logger("project-scan")

project_scan_bp = Blueprint("project-scan", __name__, url_prefix="/api/v1/project")

_MAX_UPLOAD_BYTES = 48 * 1024 * 1024


def _normalize_scan_payload(payload: Any) -> dict[str, Any] | None:
    """Normalize an incoming scan payload; never raises a schema rejection.

    Returns a clean dict with ``files`` (list) and optional ``path``, or
    ``None`` when the payload is unusable — the caller then falls back to an
    empty ``{"files": []}`` tree and processes the request successfully.
    """
    if payload is None:
        return {"path": None, "files": []}
    if isinstance(payload, dict):
        raw_files = payload.get("files")
        files: list[Any] = []
        if isinstance(raw_files, list):
            files = raw_files
        path = payload.get("path")
        return {
            "path": path.strip() if isinstance(path, str) else None,
            "files": files,
        }
    if isinstance(payload, list):
        return {"path": None, "files": payload}
    if isinstance(payload, str) and payload.strip():
        return {"path": payload.strip(), "files": []}
    return {"path": None, "files": []}


def _entries_from_json_files(files: Any) -> list[tuple[str, bytes]]:
    """Coerce a JSON ``files`` array into ``(relpath, bytes)`` entries."""
    entries: list[tuple[str, bytes]] = []
    if not isinstance(files, list):
        return entries
    for item in files:
        if isinstance(item, dict):
            relpath = str(
                item.get("relpath") or item.get("path") or item.get("name") or ""
            ).strip()
            content = item.get("content", item.get("code", ""))
            if relpath and isinstance(content, str):
                entries.append((relpath, content.encode("utf-8")))
            elif relpath and isinstance(content, bytes):
                entries.append((relpath, content))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            relpath, content = item
            if isinstance(relpath, str) and isinstance(content, str):
                entries.append((relpath, content.encode("utf-8")))
    return entries


def _empty_scan_response(reason: str | None = None):
    """Successful fallback graph for empty / malformed scans.

    The project scanner treats an empty input as ``{"files": []}`` and
    produces a valid (empty) architecture envelope. A diagnostic records
    *why* the fallback was taken without ever erroring the request.
    """
    graph = project_scanner.scan_uploads([])
    if reason:
        graph["errors"] = [
            {"kind": "empty_input", "file": None, "message": reason}
        ]
    return success_response(graph)


@project_scan_bp.post("/scan")
def scan_project_path() -> tuple[dict[str, Any], int]:
    payload = request.get_json(silent=True)
    try:
        normalized = _normalize_scan_payload(payload) or {"path": None, "files": []}
    except Exception:  # noqa: BLE001 - never surface a schema rejection
        normalized = {"path": None, "files": []}

    # JSON ``files`` tree (``{"files": [...]}``) takes the upload path so
    # clients that post a snapshot as JSON get the same graph.
    json_entries = _entries_from_json_files(normalized.get("files"))
    if json_entries:
        try:
            graph = project_scanner.scan_uploads(json_entries)
        except Exception:  # noqa: BLE001 - fall back, never raise
            logger.exception("Project JSON scan failed")
            return _empty_scan_response("Project scan failed; returned empty graph.")
        return success_response(graph)

    path = normalized.get("path")
    if not isinstance(path, str) or not path.strip():
        logger.info("Project scan received empty/malformed payload; fallback to empty tree")
        return _empty_scan_response(
            "No scan target provided; returning empty project graph."
        )

    try:
        graph = project_scanner.scan_directory(path.strip())
    except ValueError as error:
        logger.info("Project scan path rejected (%s); fallback to empty tree", error)
        return _empty_scan_response(str(error) or "Invalid scan path.")
    except Exception as error:  # noqa: BLE001 - never leak a stack trace
        logger.exception("Project scan failed")
        graph = project_scanner.scan_uploads([])
        graph["errors"] = [
            {
                "kind": "scan_failed",
                "file": None,
                "message": f"Project scan failed: {type(error).__name__}: {error}",
            }
        ]
        return success_response(graph)

    return success_response(graph)


@project_scan_bp.post("/scan/upload")
def scan_project_upload() -> tuple[dict[str, Any], int]:
    content_length = request.content_length or 0
    if content_length > _MAX_UPLOAD_BYTES:
        graph = project_scanner.scan_uploads([])
        graph["errors"] = [
            {
                "kind": "payload_too_large",
                "file": None,
                "message": (
                    f"Upload too large ({content_length // (1024 * 1024)} MiB). "
                    f"Limit is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB; "
                    "returning empty project graph."
                ),
            }
        ]
        return success_response(graph)

    uploads = request.files.getlist("files")
    if not uploads:
        logger.info("Project upload scan received no files; fallback to empty tree")
        return _empty_scan_response("No files received; returning empty project graph.")

    entries: list[tuple[str, bytes]] = []
    for upload in uploads:
        filename = (upload.filename or "").strip()
        if not filename:
            continue
        try:
            payload = upload.read()
        except OSError as error:
            logger.warning("Unreadable upload %s: %s", filename, error)
            continue
        entries.append((filename, payload))

    if not entries:
        logger.info("Project upload scan had no readable files; fallback to empty tree")
        return _empty_scan_response("Every uploaded file was empty or unreadable.")

    try:
        graph = project_scanner.scan_uploads(entries)
    except Exception as error:  # noqa: BLE001 - never leak a stack trace
        logger.exception("Project upload scan failed")
        graph = project_scanner.scan_uploads([])
        graph["errors"] = [
            {
                "kind": "scan_failed",
                "file": None,
                "message": f"Project scan failed: {type(error).__name__}: {error}",
            }
        ]
        return success_response(graph)

    graph["uploaded_files"] = len(entries)
    return success_response(graph)


__all__ = ["project_scan_bp"]
