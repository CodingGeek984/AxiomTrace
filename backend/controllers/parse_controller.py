"""AST parsing endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from schemas import ArchitectureRequestSchema, ParseRequestSchema
from services import ASTParserService, get_project, save_graph
from services.ai_service import build_architecture_graph
from utils import ApiError, get_logger, safe_load, success_response

logger = get_logger("parse")

parse_bp = Blueprint("parse", __name__, url_prefix="/api/v1/parse")

_parser = ASTParserService()

_CODE_ALIASES = (
    "source",
    "source_code",
    "sourceCode",
    "code_content",
    "codeContent",
    "content",
    "text",
    "snippet",
    "file_content",
    "fileContent",
    "input",
)
_VALID_NODE_TYPES = {
    "function",
    "condition",
    "loop",
    "action",
    "entrypoint",
    "service",
    "database",
    "utility",
    "error_handler",
}


def _coerce_number(value: Any, fallback: Any = 0) -> float | int:
    for candidate in (value, fallback, 0):
        try:
            number = float(candidate)
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        return int(number) if number.is_integer() else number
    return 0


def _coerce_project_id(value: Any) -> int | None:
    """Accept numeric ids from any client; ignore unrelated identifiers.

    ``project_id`` is optional persistence metadata. A malformed id must not
    reject an otherwise valid parse, so non-numeric values simply disable
    persistence for the request.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        project_id = int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric parse project_id: %r", value)
        return None
    return project_id if project_id > 0 else None


def _normalise_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, item in enumerate(value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        node = dict(item)
        node_id = str(node.get("id") or f"n{index + 1}")
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        data = dict(data)
        data.setdefault("id", node_id)

        position = (
            node.get("position") if isinstance(node.get("position"), dict) else {}
        )
        x = _coerce_number(position.get("x", node.get("x", 0)))
        y = _coerce_number(position.get("y", node.get("y", 0)))
        position = {"x": x, "y": y}

        node_type = str(node.get("type") or data.get("type") or "action")
        if node_type not in _VALID_NODE_TYPES:
            candidate = str(data.get("type") or "")
            node_type = candidate if candidate in _VALID_NODE_TYPES else "action"
        node["id"] = node_id
        node["type"] = node_type
        data["type"] = node_type
        node["position"] = position
        node["x"] = x
        node["y"] = y
        node["data"] = data
        nodes.append(node)
    return nodes


def _normalise_edges(value: Any) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for index, item in enumerate(value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        edge = dict(item)
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        edge["id"] = str(edge.get("id") or f"e{index + 1}")
        edge["source"] = source
        edge["target"] = target
        edge["animated"] = bool(edge.get("animated"))
        edge.setdefault("kind", "sequence")
        edges.append(edge)
    return edges


def _graph_envelope(graph: Any) -> tuple[dict[str, Any], int]:
    """Return a graph payload in both the flat and ``data`` envelope shapes.

    The shipped desktop client reads the endpoint-specific keys directly
    (``response.nodes``), while newer clients expect the nested
    ``response.data.nodes`` form. Emitting both keeps every consumer working
    and guarantees the arrays are always present, even for a partial/empty
    parse.
    """
    try:
        safe: dict[str, Any] = dict(graph) if isinstance(graph, dict) else {}
        safe["nodes"] = _normalise_nodes(safe.get("nodes"))
        safe["edges"] = _normalise_edges(safe.get("edges"))
        if not isinstance(safe.get("language"), str) or not safe["language"]:
            safe["language"] = "python"
        if not isinstance(safe.get("errors"), list):
            safe["errors"] = []
        if not isinstance(safe.get("stats"), dict):
            safe["stats"] = {}
        safe["partial"] = bool(safe.get("partial"))
        safe["status"] = "success"

        data = {key: value for key, value in safe.items() if key not in {"status", "data"}}
        safe["data"] = data
        return success_response(safe)
    except Exception as error:  # noqa: BLE001
        logger.exception("Failed to build graph envelope")
        raise ApiError(f"Failed to format parse response: {error}", 500) from error


def _load_parse_request() -> dict[str, Any]:
    """Read and validate the parse payload, accepting the common aliases.

    ``parse_code`` is called from several entrypoints (the button, the legacy
    desktop client and direct API integrations), so a request may name the
    source ``code``, ``source``, ``code_content`` or ``text``. The canonical
    key is ``code``; the aliases are folded into it before validation.
    """
    raw = request.get_json(silent=True) or {}
    payload: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}

    if not isinstance(payload.get("code"), str):
        for key in _CODE_ALIASES:
            value = payload.get(key)
            if isinstance(value, str):
                payload["code"] = value
                break

    project_id = _coerce_project_id(payload.get("project_id"))
    # Persistence metadata must not block parsing, so it is validated
    # separately from the source payload.
    validation_payload = {
        key: value for key, value in payload.items() if key != "project_id"
    }
    # Graceful normalisation: malformed fields fall back to safe defaults
    # instead of rejecting the request.
    data = safe_load(
        ParseRequestSchema(), validation_payload, defaults={"code": ""}
    )
    code = data.get("code")
    if not isinstance(code, str):
        raise ApiError("'code' must be a string of source code.", 400)

    language = data.get("language")
    if isinstance(language, str):
        data["language"] = language.strip().lower() or None
    data["project_id"] = project_id
    return data


def _parse_code_request() -> tuple[dict[str, Any], int]:
    try:
        data = _load_parse_request() or {}
    except ApiError:
        raise
    except Exception as error:  # noqa: BLE001
        logger.exception("Failed to load parse request")
        raise ApiError(f"Invalid parse request: {error}", 400) from error

    try:
        code = data.get("code") if isinstance(data, dict) else None
        if not isinstance(code, str) or code.strip() == "":
            raise ApiError("'code' must be a non-empty string of source code.", 400)
        graph = _parser.parse_code(code, language=data.get("language") if isinstance(data, dict) else None)
    except ApiError:
        raise
    except (SyntaxError, ValueError, TypeError) as error:
        raise ApiError(f"Parse failed: {error}", 400) from error
    except (MemoryError, RecursionError) as error:
        raise ApiError(
            "Source is too large or too deeply nested to parse.", 413
        ) from error
    except Exception as error:  # noqa: BLE001 - never let a parser crash the worker
        logger.exception("Unexpected parser failure")
        raise ApiError(
            f"Parser failed unexpectedly: {type(error).__name__}: {error}", 500
        ) from error

    if not isinstance(graph, dict):
        raise ApiError("Parser returned an invalid graph payload.", 500)

    # Bind the originating file path (and declared language) onto every node
    # so the inspector, sandbox and transpiler always know where the source
    # came from - single-file parses historically dropped it.
    request_file = data.get("file") if isinstance(data, dict) else None
    request_language = data.get("language") if isinstance(data, dict) else None
    if isinstance(request_file, str) and request_file.strip():
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node.setdefault("file", request_file)
            data_block = node.get("data")
            if not isinstance(data_block, dict):
                data_block = {}
                node["data"] = data_block
            data_block.setdefault("file", request_file)
    if isinstance(request_language, str) and request_language.strip():
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node.setdefault("language", request_language)
            data_block = node.get("data")
            if isinstance(data_block, dict):
                data_block.setdefault("language", request_language)

    project_id = data.get("project_id") if isinstance(data, dict) else None
    if project_id is not None and not graph.get("partial"):
        # A salvaged graph is a transient editing state - persisting it would
        # overwrite the last known-good snapshot with a broken one.
        try:
            if get_project(project_id) is not None:
                save_graph(project_id, graph.get("nodes") or [], graph.get("edges") or [])
        except Exception:  # noqa: BLE001 - persistence must not break parsing
            logger.warning("Could not persist graph for project %s", project_id)

    if graph.get("partial"):
        logger.info(
            "Parsed project %s with %d recoverable error(s)",
            project_id,
            len(graph.get("errors") or []),
        )

    return _graph_envelope(graph)


@parse_bp.post("/code")
def parse_code() -> tuple[dict[str, Any], int]:
    """POST /api/v1/parse/code - AST -> {nodes, edges, language, stats}

    A document that does not compile is *salvaged*: the response still
    carries a graph built from the statements that do parse, plus an
    ``errors`` array describing the quarantined regions.
    """
    return _parse_code_request()


@parse_bp.post("")
@parse_bp.post("/")
def parse_source() -> tuple[dict[str, Any], int]:
    """POST /api/v1/parse - alias of /api/v1/parse/code.

    Accepts the same payload (``code`` plus optional ``language`` and
    ``project_id``) so API consumers that target the short route keep working.
    """
    return _parse_code_request()


@parse_bp.post("/architecture")
def parse_architecture() -> tuple[dict[str, Any], int]:
    """POST /api/v1/parse/architecture - prompt/code -> component graph."""
    raw = request.get_json(silent=True) or {}
    payload = dict(raw) if isinstance(raw, dict) else {}
    # Graceful normalisation: partial payloads keep their valid fields.
    data = safe_load(ArchitectureRequestSchema(), payload)

    prompt = data.get("prompt") or ""
    code_context = data.get("code_context") or ""
    text = code_context if code_context.strip() else prompt
    api_key = data.get("api_key")
    current_graph = data.get("current_graph")

    if not text.strip():
        raise ApiError("prompt or code_context is required", status=400)

    try:
        result = build_architecture_graph(
            text,
            current_graph=current_graph,
            prompt=prompt if code_context.strip() else None,
            api_key=api_key,
        )
    except (ValueError, TypeError) as exc:
        raise ApiError(str(exc), status=400) from exc
    except (MemoryError, RecursionError) as exc:
        raise ApiError(
            "Architecture input is too large or too deeply nested.", 413
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never let extraction crash the worker
        logger.exception("Unexpected architecture extraction failure")
        raise ApiError(
            f"Architecture extraction failed: {type(exc).__name__}: {exc}", 500
        ) from exc

    return _graph_envelope(result)


__all__ = ["parse_bp"]
