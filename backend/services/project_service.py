"""AxiomTrace project persistence domain service.

Owns every read/write against the workspace database (projects, saved graph
schemas, execution logs). Controllers call these helpers instead of touching
``db.session`` directly so transaction boundaries stay in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import config
from database.connection import db
from database.models import (
    ExecutionLogModel,
    NodeSchemaModel,
    ProjectModel,
    utcnow,
)
from utils.logger import get_logger

logger = get_logger("project")


def dumps(payload: Any) -> str:
    """Serialize a payload to JSON, degrading gracefully on exotic types."""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserializable": repr(payload)[:500]}, ensure_ascii=False)


def get_or_create_project(name: str, path: str) -> tuple[ProjectModel, bool]:
    """Load a project by path or persist a brand new record."""
    normalized_path = str(path or "").strip() or name
    clean_name = str(name or "").strip() or Path(normalized_path).name or "untitled"

    project = ProjectModel.query.filter(
        ProjectModel.root_path == normalized_path
    ).one_or_none()

    if project is not None:
        if project.name != clean_name:
            project.name = clean_name
            project.updated_at = utcnow()
            db.session.commit()
        db.session.refresh(project)
        return project, False

    project = ProjectModel(name=clean_name, root_path=normalized_path)
    db.session.add(project)
    db.session.commit()
    db.session.refresh(project)
    return project, True


def get_project(project_id: int | None) -> ProjectModel | None:
    """Fetch a project by primary key (``None``-safe)."""
    if project_id is None:
        return None
    return db.session.get(ProjectModel, int(project_id))


def save_graph(
    project_id: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    file_path: str | None = None,
) -> NodeSchemaModel:
    """Upsert the latest graph snapshot for a project.

    The snapshot is clamped to the configured node/edge ceilings so a
    runaway client cannot grow the scratch database without bound.
    """
    nodes = list(nodes or [])
    edges = list(edges or [])

    max_nodes = max(1, int(config.MAX_GRAPH_NODES))
    max_edges = max(1, int(config.MAX_GRAPH_EDGES))
    if len(nodes) > max_nodes:
        logger.warning(
            "Graph for project %s truncated from %s to %s nodes",
            project_id,
            len(nodes),
            max_nodes,
        )
        nodes = nodes[:max_nodes]
    if len(edges) > max_edges:
        logger.warning(
            "Graph for project %s truncated from %s to %s edges",
            project_id,
            len(edges),
            max_edges,
        )
        edges = edges[:max_edges]

    schema = (
        NodeSchemaModel.query.filter(NodeSchemaModel.project_id == int(project_id))
        .order_by(NodeSchemaModel.updated_at.desc())
        .first()
    )

    if schema is None:
        schema = NodeSchemaModel(
            project_id=int(project_id),
            file_path=file_path,
            nodes_json=dumps(nodes),
            edges_json=dumps(edges),
            version=1,
        )
        db.session.add(schema)
    else:
        schema.nodes_json = dumps(nodes)
        schema.edges_json = dumps(edges)
        schema.updated_at = utcnow()
        if file_path is not None:
            schema.file_path = file_path
        schema.version = int(schema.version or 1) + 1

    db.session.commit()
    db.session.refresh(schema)
    return schema


def load_graph(project_id: int) -> dict[str, Any] | None:
    """Return the most recent graph snapshot for a project, if any."""
    schema = (
        NodeSchemaModel.query.filter(NodeSchemaModel.project_id == int(project_id))
        .order_by(NodeSchemaModel.updated_at.desc())
        .first()
    )
    if schema is None:
        return None
    return schema.to_dict()


def record_execution(
    node_id: str,
    input_payload: Any,
    output_payload: Any,
    status: str,
    runtime_ms: float,
    project_id: int | None = None,
    kind: str = "execute",
) -> ExecutionLogModel:
    """Append one execution record (``status`` normalised to SUCCESS/ERROR).

    The log table is a bounded ring: after every insert the oldest rows are
    pruned so the scratch database never grows without bound.
    """
    normalized_status = "SUCCESS" if str(status or "").lower() == "success" else "ERROR"
    log = ExecutionLogModel(
        node_id=str(node_id or "unknown"),
        project_id=int(project_id) if project_id is not None else None,
        input_payload=dumps(input_payload),
        output_payload=dumps(output_payload),
        status=normalized_status,
        runtime_ms=float(runtime_ms or 0.0),
        kind=str(kind or "execute")[:32],
    )
    db.session.add(log)
    db.session.commit()
    db.session.refresh(log)

    prune_execution_logs()
    return log


def prune_execution_logs(limit: int | None = None) -> int:
    """Delete execution-log rows beyond the retention ceiling.

    Returns:
        The number of removed rows.
    """
    retention = int(limit if limit is not None else config.EXECUTION_LOG_RETENTION)
    if retention <= 0:
        return 0

    try:
        total = int(ExecutionLogModel.query.count())
    except Exception:  # noqa: BLE001 - pruning must never break a write
        logger.exception("Could not count execution logs")
        return 0

    if total <= retention:
        return 0

    keep_ids = [
        row.id
        for row in ExecutionLogModel.query.order_by(ExecutionLogModel.id.desc())
        .limit(retention)
        .all()
    ]
    try:
        removed = int(
            ExecutionLogModel.query.filter(
                ExecutionLogModel.id.notin_(keep_ids)
            ).delete(synchronize_session=False)
        )
        db.session.commit()
    except Exception:  # noqa: BLE001 - pruning must never break a write
        db.session.rollback()
        logger.exception("Could not prune execution logs")
        return 0

    logger.info("Pruned %s execution log row(s) (retention=%s)", removed, retention)
    return removed


def count_execution_logs(project_id: int | None = None) -> int:
    """Number of stored execution records (optionally for one project)."""
    query = ExecutionLogModel.query
    if project_id is not None:
        query = query.filter(ExecutionLogModel.project_id == int(project_id))
    return int(query.count())


def fetch_execution_logs(
    project_id: int | None = None,
    node_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent execution logs, newest first."""
    query = ExecutionLogModel.query.order_by(ExecutionLogModel.id.desc())
    if project_id is not None:
        query = query.filter(ExecutionLogModel.project_id == int(project_id))
    if node_id:
        query = query.filter(ExecutionLogModel.node_id == str(node_id))
    rows = query.limit(max(1, min(int(limit), 500))).all()
    return [row.to_dict() for row in rows]


def count_projects() -> int:
    """Total number of persisted projects."""
    return int(ProjectModel.query.count())


__all__ = [
    "dumps",
    "get_or_create_project",
    "get_project",
    "save_graph",
    "load_graph",
    "record_execution",
    "prune_execution_logs",
    "count_execution_logs",
    "fetch_execution_logs",
    "count_projects",
]
