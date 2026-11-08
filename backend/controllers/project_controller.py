"""Project, graph-schema, logs and health endpoints."""

from __future__ import annotations

import sys
from typing import Any

from flask import Blueprint, request

from config import config
from schemas import LogsQuerySchema, ProjectGraphSaveSchema, ProjectOpenSchema
from services import (
    count_projects,
    fetch_execution_logs,
    get_or_create_project,
    get_project,
    load_graph,
    save_graph,
)
from services.sandbox_service import describe_isolation
from utils import ApiError, get_logger, safe_load, success_response

logger = get_logger("projects")

project_bp = Blueprint("projects", __name__, url_prefix="/api/v1/projects")

# Standalone v1 blueprint for endpoints that do not belong to a project
# resource (the desktop client polls /api/v1/logs for the terminal feed).
logs_bp = Blueprint("logs", __name__, url_prefix="/api/v1")


# ----------------------------------------------------------------------
# Shared response builders (used by both v1 views and legacy aliases)
# ----------------------------------------------------------------------

def health_view() -> tuple[dict[str, Any], int]:
    """GET /api/v1/health and GET /api/health"""
    try:
        project_count = count_projects()
    except Exception:  # noqa: BLE001 - health must never fail
        project_count = None

    return success_response(
        {
            "status": "ok",
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "python": sys.version.split()[0],
            "debug": bool(config.DEBUG),
            "project_count": project_count,
            "sandbox": describe_isolation(),
        }
    )


def _open_response(name: str, path: str) -> tuple[dict[str, Any], int]:
    project, created = get_or_create_project(name, path)
    graph = load_graph(project.id)
    return success_response(
        {
            "project": project.to_dict(),
            "created": created,
            "graph": graph,
        }
    )


def _save_graph_response(
    project_id: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    file_path: str | None = None,
) -> tuple[dict[str, Any], int]:
    if get_project(project_id) is None:
        raise ApiError(f"Project {project_id} does not exist.", 404)
    schema = save_graph(project_id, nodes, edges, file_path=file_path)
    return success_response(
        {
            "graph_id": schema.id,
            "version": schema.version,
            "nodes": len(nodes),
            "edges": len(edges),
        }
    )


def _load_graph_response(project_id: int) -> tuple[dict[str, Any], int]:
    graph = load_graph(project_id)
    if graph is None:
        raise ApiError(f"No saved graph for project {project_id}.", 404)
    return success_response({"graph": graph})


def _logs_response(
    project_id: int | None = None,
    node_id: str | None = None,
    limit: int = 100,
) -> tuple[dict[str, Any], int]:
    entries = fetch_execution_logs(project_id=project_id, node_id=node_id, limit=limit)
    return success_response({"logs": entries, "count": len(entries)})


# ----------------------------------------------------------------------
# v1 views
# ----------------------------------------------------------------------

@project_bp.post("/open")
def open_project() -> tuple[dict[str, Any], int]:
    """POST /api/v1/projects/open"""
    # Graceful normalisation: partial payloads fall back to name<->path.
    data = safe_load(
        ProjectOpenSchema(),
        request.get_json(silent=True) or {},
        defaults={"name": None, "path": None, "scan": False},
    )
    name = str(data.get("name") or "").strip()
    path = str(data.get("path") or "").strip()
    if not name and not path:
        raise ApiError("Project name and path are required.", 400)
    return _open_response(name or path.split("/")[-1], path or name)


@project_bp.get("/<int:project_id>/schema")
def get_schema(project_id: int) -> tuple[dict[str, Any], int]:
    """GET /api/v1/projects/<id>/schema"""
    return _load_graph_response(project_id)


@project_bp.post("/<int:project_id>/schema")
def put_schema(project_id: int) -> tuple[dict[str, Any], int]:
    """POST /api/v1/projects/<id>/schema"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError("Request body must be a JSON object.", 400)
    data = safe_load(
        ProjectGraphSaveSchema(),
        {**body, "project_id": project_id},
        defaults={"project_id": project_id, "nodes": [], "edges": [], "file_path": None},
    )
    return _save_graph_response(
        project_id,
        data.get("nodes", []),
        data.get("edges", []),
        file_path=data.get("file_path"),
    )


@project_bp.get("/<int:project_id>/logs")
def project_logs(project_id: int) -> tuple[dict[str, Any], int]:
    """GET /api/v1/projects/<id>/logs"""
    query = safe_load(
        LogsQuerySchema(),
        {**request.args.to_dict(), "project_id": project_id},
        defaults={"node_id": None, "limit": 100},
    )
    return _logs_response(
        project_id=project_id,
        node_id=query.get("node_id"),
        limit=query.get("limit", 100),
    )


@project_bp.get("")
def list_projects() -> tuple[dict[str, Any], int]:
    """GET /api/v1/projects"""
    return success_response({"count": count_projects()})


@logs_bp.get("/logs")
def v1_logs() -> tuple[dict[str, Any], int]:
    """GET /api/v1/logs - recent execution records (terminal feed)."""
    query = safe_load(LogsQuerySchema(), request.args.to_dict(), defaults={"limit": 100})
    return _logs_response(
        project_id=query.get("project_id"),
        node_id=query.get("node_id"),
        limit=query.get("limit", 100),
    )


# ----------------------------------------------------------------------
# Legacy desktop-client aliases (kept so the shipped frontend keeps working)
# ----------------------------------------------------------------------

def legacy_open_project() -> tuple[dict[str, Any], int]:
    """POST /api/project/open - lenient name/path fallbacks."""
    payload = request.get_json(silent=True)
    if payload is None or not isinstance(payload, dict):
        raise ApiError("Request body must be JSON.", 400)
    name = str(payload.get("name") or "").strip()
    path = str(payload.get("path") or "").strip()
    if not path and not name:
        raise ApiError("Provide at least one of 'name' or 'path'.", 400)
    return _open_response(name or path.split("/")[-1], path or name)


def legacy_graph_save() -> tuple[dict[str, Any], int]:
    """POST /api/project/graph - project_id in the JSON body."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError("Request body must be a JSON object.", 400)
    data = safe_load(
        ProjectGraphSaveSchema(),
        body,
        defaults={"project_id": None, "nodes": [], "edges": [], "file_path": None},
    )
    if not data.get("project_id"):
        raise ApiError("Query param 'project_id' is required.", 400)
    return _save_graph_response(
        data["project_id"],
        data.get("nodes", []),
        data.get("edges", []),
        file_path=data.get("file_path"),
    )


def legacy_graph_load() -> tuple[dict[str, Any], int]:
    """GET /api/project/graph?project_id=N"""
    query = safe_load(LogsQuerySchema(), request.args.to_dict(), defaults={"limit": 100})
    project_id = query.get("project_id")
    if project_id is None:
        raise ApiError("Query param 'project_id' is required.", 400)
    return _load_graph_response(int(project_id))


def legacy_logs() -> tuple[dict[str, Any], int]:
    """GET /api/logs?project_id=&node_id=&limit="""
    query = safe_load(LogsQuerySchema(), request.args.to_dict(), defaults={"limit": 100})
    return _logs_response(
        project_id=query.get("project_id"),
        node_id=query.get("node_id"),
        limit=query.get("limit", 100),
    )


__all__ = [
    "project_bp",
    "logs_bp",
    "health_view",
    "legacy_open_project",
    "legacy_graph_save",
    "legacy_graph_load",
    "legacy_logs",
]
