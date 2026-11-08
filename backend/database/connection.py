"""SQLAlchemy engine binding and transaction lifecycle for AxiomTrace.

Uses Flask-SQLAlchemy so the session is bound to the application context
(``db.session``) and automatically removed at the end of every request.
Alembic compatibility: the models in :mod:`database.models` use plain
``db.Column`` declarations so ``alembic revision --autogenerate`` can pick
up every schema change.
"""

from __future__ import annotations

from typing import Any

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

from utils.logger import get_logger

logger = get_logger("database")

db = SQLAlchemy()

_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": ("id", "name", "root_path", "created_at", "updated_at"),
    "node_schemas": (
        "id",
        "project_id",
        "file_path",
        "nodes_json",
        "edges_json",
        "version",
        "created_at",
        "updated_at",
    ),
    "execution_logs": (
        "id",
        "node_id",
        "project_id",
        "input_payload",
        "output_payload",
        "status",
        "runtime_ms",
        "kind",
        "executed_at",
    ),
}


def init_database(app: Flask) -> SQLAlchemy:
    """Bind the extension to ``app`` and initialise tables on startup."""
    db.init_app(app)

    with app.app_context():
        ensure_schema()
        db.create_all()
        db.session.commit()
        logger.info(
            "database ready at %s",
            app.config.get("SQLALCHEMY_DATABASE_URI", "unknown"),
        )

    return db


def ensure_schema() -> None:
    """Detect pre-1.0 tables and drop them so ``create_all`` can rebuild.

    AxiomTrace is a local-first scratch database; when the on-disk tables
    belong to the legacy flat-module layout they are recreated instead of
    migrated.
    """
    try:
        inspector = inspect(db.engine)
        existing = set(inspector.get_table_names())
    except Exception as error:  # noqa: BLE001 - engine may not exist yet
        logger.warning("schema introspection skipped: %s", error)
        return

    if not existing:
        return

    legacy_tables: list[str] = []
    for table, required in _TABLE_COLUMNS.items():
        if table not in existing:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        missing = set(required) - columns
        if missing:
            legacy_tables.append(table)
            logger.warning(
                "legacy table '%s' missing columns %s - recreating",
                table,
                sorted(missing),
            )

    if not legacy_tables:
        return

    try:
        with db.engine.begin() as connection:
            for table in legacy_tables:
                connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        logger.info("legacy tables dropped: %s", legacy_tables)
    except Exception as error:  # noqa: BLE001 - never block startup
        logger.error("failed to drop legacy tables: %s", error)


def commit_or_rollback() -> bool:
    """Commit the active session, rolling back on failure."""
    try:
        db.session.commit()
        return True
    except Exception as error:  # noqa: BLE001 - surfaced to service layer
        logger.error("commit failed: %s", error)
        db.session.rollback()
        return False


def rollback() -> None:
    """Roll back the active session, ignoring missing-session errors."""
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001 - rollback must never raise
        pass


def to_dict_safe(instance: Any) -> dict[str, Any]:
    """Call ``to_dict`` on an ORM instance when available."""
    if instance is None:
        return {}
    converter = getattr(instance, "to_dict", None)
    if callable(converter):
        return converter()
    return {}


__all__ = [
    "db",
    "init_database",
    "ensure_schema",
    "commit_or_rollback",
    "rollback",
    "to_dict_safe",
]
