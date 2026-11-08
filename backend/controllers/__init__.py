"""Controller layer for the AxiomTrace HTTP API (v1 + legacy aliases)."""

from controllers.ai_controller import ai_bp
from controllers.doc_controller import doc_bp
from controllers.parse_controller import parse_bp
from controllers.project_controller import (
    health_view,
    legacy_graph_load,
    legacy_graph_save,
    legacy_logs,
    legacy_open_project,
    logs_bp,
    project_bp,
)
from controllers.project_scan_controller import project_scan_bp
from controllers.sandbox_controller import sandbox_bp
from controllers.security_controller import security_bp
from controllers.transpile_controller import transpile_bp

__all__ = [
    "project_bp",
    "project_scan_bp",
    "logs_bp",
    "parse_bp",
    "sandbox_bp",
    "ai_bp",
    "doc_bp",
    "transpile_bp",
    "security_bp",
    "health_view",
    "legacy_open_project",
    "legacy_graph_save",
    "legacy_graph_load",
    "legacy_logs",
]
