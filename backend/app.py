"""AxiomTrace Flask application factory.

Layout (enterprise MVC / clean architecture)::

    backend/
    ├── app.py                     <- this file (factory + wiring)
    ├── config.py                  <- environment-driven configuration classes
    ├── database/                  <- engine binding + ORM models
    ├── schemas/                   <- Marshmallow request validation
    ├── services/                  <- domain logic (AST, sandbox, AI, project)
    ├── controllers/               <- HTTP blueprints (/api/v1 + legacy aliases)
    └── utils/                     <- response envelope + logging

Run directly (``python app.py``) or import ``create_app`` from tests/tooling.
"""

from __future__ import annotations

import atexit
import os
import sys
import time

from flask import Flask, g, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from marshmallow import ValidationError

from config import get_config
from controllers import (
    ai_bp,
    doc_bp,
    health_view,
    legacy_graph_load,
    legacy_graph_save,
    legacy_logs,
    legacy_open_project,
    logs_bp,
    parse_bp,
    project_bp,
    project_scan_bp,
    sandbox_bp,
    security_bp,
    transpile_bp,
)
from controllers.ai_controller import ai_edge_cases, ai_context
from controllers.doc_controller import (
    generate_docs,
    generate_markdown_docs,
    generate_openapi_docs,
)
from controllers.parse_controller import parse_code
from controllers.sandbox_controller import execute_node, mutate_node
from controllers.transpile_controller import transpile_node
from database import db, init_database
from services import count_execution_logs, count_projects
from services.sandbox_service import describe_isolation
from utils import ApiError, error_response, get_logger, success_response

logger = get_logger("app")


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ValidationError)
    def _handle_validation(error: ValidationError):
        # Safety net only — controllers validate via safe_load() so this
        # should be unreachable in normal flows. If it ever fires, return a
        # descriptive payload error (not the generic schema error).
        logger.warning("Unhandled schema validation issue: %s", error.messages)
        return error_response("Invalid request payload.", 400, details=error.messages)

    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        return error_response(error.message, error.status)

    @app.errorhandler(404)
    def _handle_not_found(_error):
        return error_response("Endpoint not found.", 404)

    @app.errorhandler(405)
    def _handle_method_not_allowed(_error):
        return error_response("Method not allowed.", 405)

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        if isinstance(error, ApiError):
            return error_response(error.message, error.status)
        logger.exception("Unhandled API error: %s", error)
        return error_response(f"{type(error).__name__}: {error}", 500)


def _register_request_hooks(app: Flask) -> None:
    """Attach a server-timing header to every response.

    The header is used by the desktop client to display the round-trip cost
    of an action and makes latency regressions visible in ``curl -I``.
    """

    @app.before_request
    def _start_timer() -> None:
        g.request_started_at = time.perf_counter()

    @app.after_request
    def _apply_timing(response):
        started_at = getattr(g, "request_started_at", None)
        if started_at is not None:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
            response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.2f}"
        return response

    @app.teardown_request
    def _release_session(exception: BaseException | None):
        # Never leave a half-finished transaction behind: the next request
        # would otherwise inherit the broken session state.
        if exception is not None:
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001 - teardown must never raise
                logger.debug("session rollback during teardown failed", exc_info=True)


def _register_stats_route(app: Flask) -> None:
    """Expose a small runtime-statistics endpoint for the header indicator."""

    @app.get("/api/v1/stats")
    def stats_view():
        try:
            projects = count_projects()
        except Exception:  # noqa: BLE001 - stats must never break the app
            projects = None
        try:
            logs = count_execution_logs()
        except Exception:  # noqa: BLE001 - stats must never break the app
            logs = None

        return success_response(
            {
                "projects": projects,
                "execution_logs": logs,
                "sandbox": describe_isolation(),
                "pid": os.getpid(),
                "python": sys.version.split()[0],
            }
        )


def _register_shutdown_hook(app: Flask) -> None:
    """Dispose the SQLAlchemy engine when the process exits.

    Without this the SQLite handle stays open until the interpreter is
    torn down, which shows up as a lingering file lock on Windows.
    """
    try:
        with app.app_context():
            engine = db.engine
    except Exception:  # noqa: BLE001 - shutdown hook is best effort
        return

    def _shutdown() -> None:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            logger.debug("engine dispose failed", exc_info=True)

    atexit.register(_shutdown)


def _register_legacy_compat(app: Flask) -> None:
    """Alias routes under /api/* so the shipped desktop frontend keeps working.

    The frontend axios client targets ``/api/...``; v1 lives at ``/api/v1/...``.
    Where the v1 view already accepts the legacy payload shape (execute,
    mutate, parse, ai) the exact same view function is reused.
    """
    from flask import Blueprint

    from controllers.parse_controller import parse_architecture, parse_code
    from controllers.security_controller import scan_security
    from controllers.ai_controller import ai_config
    from controllers.project_scan_controller import (
        scan_project_path,
        scan_project_upload,
    )

    compat = Blueprint("compat", __name__, url_prefix="/api")

    compat.add_url_rule("/health", "health", view_func=health_view, methods=["GET"])
    compat.add_url_rule(
        "/project/open", "project_open", view_func=legacy_open_project, methods=["POST"]
    )
    compat.add_url_rule(
        "/project/graph", "project_graph_save",
        view_func=legacy_graph_save, methods=["POST"],
    )
    compat.add_url_rule(
        "/project/graph", "project_graph_load",
        view_func=legacy_graph_load, methods=["GET"],
    )
    compat.add_url_rule("/parse", "parse", view_func=parse_code, methods=["POST"])
    compat.add_url_rule("/parse/code", "parse_code_legacy", view_func=parse_code, methods=["POST"])
    compat.add_url_rule(
        "/parse/architecture",
        "parse_architecture_legacy",
        view_func=parse_architecture,
        methods=["POST"],
    )
    compat.add_url_rule(
        "/node/execute", "node_execute", view_func=execute_node, methods=["POST"]
    )
    compat.add_url_rule(
        "/node/mutate", "node_mutate", view_func=mutate_node, methods=["POST"]
    )
    compat.add_url_rule(
        "/ai/config", "ai_config", view_func=ai_config, methods=["POST"]
    )
    compat.add_url_rule("/ai/context", "ai_context", view_func=ai_context, methods=["POST"])
    compat.add_url_rule(
        "/ai/detect", "ai_detect", view_func=ai_edge_cases, methods=["POST"]
    )
    compat.add_url_rule(
        "/docs/generate", "docs_generate", view_func=generate_docs, methods=["POST"]
    )
    compat.add_url_rule(
        "/docs/openapi",
        "docs_openapi",
        view_func=generate_openapi_docs,
        methods=["POST"],
    )
    compat.add_url_rule(
        "/docs/markdown",
        "docs_markdown",
        view_func=generate_markdown_docs,
        methods=["POST"],
    )
    compat.add_url_rule(
        "/security/scan", "security_scan", view_func=scan_security, methods=["POST"]
    )
    compat.add_url_rule(
        "/project/scan", "project_scan", view_func=scan_project_path, methods=["POST"]
    )
    compat.add_url_rule(
        "/project/scan/upload",
        "project_scan_upload",
        view_func=scan_project_upload,
        methods=["POST"],
    )
    compat.add_url_rule("/transpile", "transpile", view_func=transpile_node, methods=["POST"])
    compat.add_url_rule("/logs", "logs", view_func=legacy_logs, methods=["GET"])

    app.register_blueprint(compat)


def create_app(config_name: str | None = None) -> Flask:
    """Build the configured Flask application."""
    config_cls = get_config(config_name)

    app = Flask(__name__)
    app.config.from_object(config_cls)
    app.config["PROPAGATE_EXCEPTIONS"] = False

    cors_origins = list(config_cls.CORS_ORIGINS or [])
    cors_regex = getattr(config_cls, "CORS_ORIGIN_REGEX", None)
    if cors_regex:
        import re

        cors_origins.append(re.compile(cors_regex))

    CORS(
        app,
        resources={r"/api/*": {"origins": cors_origins or "*"}},
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        methods=["GET", "POST", "OPTIONS"],
    )

    _register_error_handlers(app)
    _register_request_hooks(app)

    # Domain blueprints (/api/v1/*)
    app.register_blueprint(project_bp)
    app.register_blueprint(project_scan_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(parse_bp)
    app.register_blueprint(sandbox_bp)
    app.register_blueprint(security_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(doc_bp)
    app.register_blueprint(transpile_bp)

    # v1 health + runtime statistics
    app.add_url_rule("/api/v1/health", "health_v1", view_func=health_view, methods=["GET"])
    _register_stats_route(app)

    # Legacy aliases for the desktop frontend
    _register_legacy_compat(app)

    # Database lifecycle
    init_database(app)
    _register_shutdown_hook(app)

    logger.info("%s v%s ready (%s)", config_cls.APP_NAME, config_cls.APP_VERSION, config_cls.__name__)
    return app


def main() -> None:
    config_cls = get_config()
    app = create_app()
    print(
        f"[AxiomTrace] {config_cls.APP_NAME} v{config_cls.APP_VERSION} "
        f"listening on http://{config_cls.HOST}:{config_cls.PORT} "
        f"(python {sys.version.split()[0]})",
        flush=True,
    )
    app.run(
        host=config_cls.HOST,
        port=config_cls.PORT,
        debug=bool(config_cls.DEBUG),
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()


__all__ = ["create_app", "main", "db"]
