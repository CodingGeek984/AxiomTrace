"""ORM models for the AxiomTrace local workspace database.

Tables
------
projects      - opened codebases / single-file workspaces
node_schemas  - latest parsed graph (nodes + edges JSON) per project
execution_logs- sandbox runs and mutation stress runs
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import db


def utcnow() -> datetime:
    """Timezone-aware current time used for every timestamp column."""
    return datetime.now(timezone.utc)


def safe_json_loads(raw: str | None, fallback: Any) -> Any:
    """Parse a JSON column, returning ``fallback`` on malformed data."""
    if raw is None or raw == "":
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class ProjectModel(db.Model):
    """A codebase workspace opened inside AxiomTrace."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    node_schemas: Mapped[list["NodeSchemaModel"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    execution_logs: Mapped[list["ExecutionLogModel"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.root_path,
            "root_path": self.root_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NodeSchemaModel(db.Model):
    """Serialized React Flow graph snapshot for a project."""

    __tablename__ = "node_schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    nodes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    project: Mapped["ProjectModel"] = relationship(back_populates="node_schemas")

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return safe_json_loads(self.nodes_json, [])

    @property
    def edges(self) -> list[dict[str, Any]]:
        return safe_json_loads(self.edges_json, [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "file_path": self.file_path,
            "version": self.version,
            "nodes": self.nodes,
            "edges": self.edges,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExecutionLogModel(db.Model):
    """One sandbox execution (single node run or mutation stress run)."""

    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    input_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="SUCCESS", index=True
    )
    runtime_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="execute")
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    project: Mapped["ProjectModel"] = relationship(back_populates="execution_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "project_id": self.project_id,
            "input_payload": safe_json_loads(self.input_payload, {}),
            "output_payload": safe_json_loads(self.output_payload, {}),
            "status": self.status,
            "runtime_ms": self.runtime_ms,
            "kind": self.kind,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


__all__ = ["ProjectModel", "NodeSchemaModel", "ExecutionLogModel", "utcnow", "safe_json_loads"]
