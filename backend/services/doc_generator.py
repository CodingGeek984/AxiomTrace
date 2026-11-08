"""Documentation generation for AxiomTrace graphs.

The service turns a parsed graph into two artefacts:

``openapi``
    A self-contained OpenAPI 3.0 document. Every public entry point/service
    becomes an operation, while the graph topology is attached through
    ``x-axiomtrace`` extensions so tooling can round-trip the architecture.

``markdown``
    A human-readable architecture specification with a Mermaid diagram,
    component/dependency tables, an API surface summary and risk notes.

The generator is intentionally deterministic apart from ``generated_at`` and
never depends on the AI layer, so documentation can be produced offline for
every graph shape the parser emits (including recovered/partial graphs).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from utils.logger import get_logger

logger = get_logger("docs")

DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_VERSION = "1.0.0"

_GROUP_LABELS = {
    "entrypoint": "Entry Point",
    "service": "Services",
    "data": "Data Layer",
    "integration": "Integrations",
    "utility": "Utilities",
    "error": "Error Handling",
    "control": "Control Flow",
}

_TYPE_GROUP = {
    "entrypoint": "entrypoint",
    "service": "service",
    "database": "data",
    "utility": "utility",
    "error_handler": "error",
    "condition": "control",
    "loop": "control",
    "function": "service",
    "action": "utility",
}

_PUBLIC_TYPES = {"entrypoint", "service"}
_PRIVATE_PREFIXES = ("_", "__")


class DocGenerationError(ValueError):
    """Raised when a documentation request cannot be fulfilled."""


@dataclass(frozen=True)
class GraphNode:
    """Normalised view over a React Flow / parser node."""

    id: str
    label: str
    type: str
    group: str
    description: str
    code: str
    line: int | None
    end_line: int | None
    parameters: tuple[str, ...]
    returns: str | None
    language: str
    is_entry: bool
    is_vulnerable: bool
    vulnerability_type: str | None
    severity: str | None

    @property
    def name(self) -> str:
        """Best-effort callable name used for paths and operation ids."""
        raw = self.label.strip()
        raw = re.sub(r"^(async\s+)?def\s+", "", raw)
        raw = re.sub(r"^class\s+", "", raw)
        raw = raw.replace("()", "")
        raw = raw.split("(", 1)[0].strip()
        return raw or self.id

    @property
    def is_public(self) -> bool:
        return (
            self.type in _PUBLIC_TYPES
            and not self.name.startswith(_PRIVATE_PREFIXES)
            and self.type != "error_handler"
        )


@dataclass(frozen=True)
class GraphEdge:
    """Normalised directed dependency."""

    id: str
    source: str
    target: str
    kind: str
    label: str | None


@dataclass
class Topology:
    """Derived topology metrics shared by both output formats."""

    adjacency: dict[str, list[str]] = field(default_factory=dict)
    reverse: dict[str, list[str]] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    leaves: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    order: list[str] = field(default_factory=list)

    @property
    def cycle_count(self) -> int:
        return len(self.cycles)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            name = item.get("name") or item.get("label") or item.get("id")
            if name:
                result.append(str(name))
        elif item is not None:
            text = str(item).strip()
            if text:
                result.append(text)
    return tuple(result)


def _node_from_raw(raw: Mapping[str, Any], index: int) -> GraphNode | None:
    if not isinstance(raw, Mapping):
        return None
    node_id = _as_text(raw.get("id"), f"n{index + 1}")
    if not node_id:
        return None
    data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    merged: dict[str, Any] = {**raw, **data}

    semantic_type = _as_text(merged.get("type"), "action").lower()
    if semantic_type not in _TYPE_GROUP:
        semantic_type = "action"

    group = _as_text(merged.get("group")) or _TYPE_GROUP.get(semantic_type, "utility")
    label = _as_text(merged.get("label"), node_id) or node_id
    description = (
        _as_text(merged.get("node_description"))
        or _as_text(merged.get("description"))
        or _as_text(merged.get("preview"))
    )

    return GraphNode(
        id=node_id,
        label=label,
        type=semantic_type,
        group=group,
        description=description,
        code=_as_text(merged.get("code")),
        line=_as_int(merged.get("line")),
        end_line=_as_int(merged.get("end_line")),
        parameters=_as_str_list(merged.get("parameters")),
        returns=_as_text(merged.get("returns")) or None,
        language=_as_text(merged.get("language"), "python") or "python",
        is_entry=bool(merged.get("is_entry")),
        is_vulnerable=bool(merged.get("is_vulnerable")),
        vulnerability_type=_as_text(merged.get("vulnerability_type")) or None,
        severity=_as_text(merged.get("severity")) or None,
    )


def _edge_from_raw(raw: Mapping[str, Any], index: int) -> GraphEdge | None:
    if not isinstance(raw, Mapping):
        return None
    source = _as_text(raw.get("source"))
    target = _as_text(raw.get("target"))
    if not source or not target or source == target:
        return None
    edge_id = _as_text(raw.get("id"), f"e{index + 1}") or f"e{index + 1}"
    data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    kind = _as_text(raw.get("kind")) or _as_text(data.get("kind")) or "sequence"
    return GraphEdge(
        id=edge_id,
        source=source,
        target=target,
        kind=kind,
        label=_as_text(raw.get("label")) or None,
    )


def normalise_graph(
    nodes: Iterable[Mapping[str, Any]] | None,
    edges: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Normalise arbitrary node/edge payloads and drop dangling edges."""
    safe_nodes: list[GraphNode] = []
    seen: set[str] = set()
    for index, raw in enumerate(nodes or []):
        node = _node_from_raw(raw, index)
        if node is None or node.id in seen:
            continue
        seen.add(node.id)
        safe_nodes.append(node)

    safe_edges: list[GraphEdge] = []
    edge_seen: set[str] = set()
    for index, raw in enumerate(edges or []):
        edge = _edge_from_raw(raw, index)
        if edge is None or edge.id in edge_seen:
            continue
        if edge.source not in seen or edge.target not in seen:
            continue
        edge_seen.add(edge.id)
        safe_edges.append(edge)
    return safe_nodes, safe_edges


def analyse_topology(nodes: list[GraphNode], edges: list[GraphEdge]) -> Topology:
    """Build adjacency/reverse maps plus roots, leaves, orphans and cycles."""
    node_ids = [node.id for node in nodes]
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    reverse: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        adjacency[edge.source].append(edge.target)
        reverse[edge.target].append(edge.source)

    for values in adjacency.values():
        values.sort()
    for values in reverse.values():
        values.sort()

    roots = sorted(node_id for node_id in node_ids if not reverse[node_id])
    leaves = sorted(node_id for node_id in node_ids if not adjacency[node_id])
    orphans = sorted(
        node_id
        for node_id in node_ids
        if not reverse[node_id] and not adjacency[node_id] and len(node_ids) > 1
    )

    # Iterative DFS with an explicit stack: parser graphs are capped at 400
    # nodes, but avoiding recursion keeps the service safe for direct callers.
    state: dict[str, int] = {node_id: 0 for node_id in node_ids}
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def canonical(cycle: list[str]) -> tuple[str, ...]:
        if not cycle:
            return ()
        pivot = min(range(len(cycle)), key=lambda position: cycle[position])
        rotated = cycle[pivot:] + cycle[:pivot]
        return tuple(rotated)

    for start in node_ids:
        if state[start] != 0:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        path_index: dict[str, int] = {}
        while stack:
            node_id, child_index = stack[-1]
            if child_index == 0:
                state[node_id] = 1
                path_index[node_id] = len(path)
                path.append(node_id)
            children = adjacency.get(node_id, [])
            if child_index < len(children):
                child = children[child_index]
                stack[-1] = (node_id, child_index + 1)
                if state.get(child, 0) == 1:
                    cycle_start = path_index.get(child, 0)
                    cycle = path[cycle_start:]
                    key = canonical(cycle)
                    if key and key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append(list(key))
                elif state.get(child, 0) == 0:
                    stack.append((child, 0))
            else:
                stack.pop()
                state[node_id] = 2
                path_index.pop(node_id, None)
                if path and path[-1] == node_id:
                    path.pop()

    # Kahn ordering gives the API surface a stable, dependency-first order.
    indegree = {node_id: len(reverse[node_id]) for node_id in node_ids}
    queue = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for child in adjacency.get(current, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
                queue.sort()
    if len(order) < len(node_ids):
        order.extend(node_id for node_id in node_ids if node_id not in order)

    return Topology(
        adjacency=adjacency,
        reverse=reverse,
        roots=roots,
        leaves=leaves,
        orphans=orphans,
        cycles=cycles,
        order=order,
    )


def _slugify(value: str, fallback: str = "axiomtrace") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def _operation_id(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words:
        return "operation"
    first = words[0].lower()
    rest = "".join(word[:1].upper() + word[1:] for word in words[1:])
    operation = f"{first}{rest}"
    if operation[0].isdigit():
        operation = f"op{operation}"
    return operation


def _schema_type(parameter: str) -> str:
    """Infer a JSON schema type from a parameter name (best effort)."""
    name = parameter.lower()
    if name.endswith(("_id", "id")):
        return "string"
    if name.endswith(("count", "size", "limit", "offset", "page", "index", "qty")):
        return "integer"
    if name.endswith(("ratio", "rate", "score", "price", "amount", "weight")):
        return "number"
    if name.startswith(("is_", "has_", "should_", "enable", "allow")) or name.endswith(
        ("_enabled", "_flag", "_ok")
    ):
        return "boolean"
    if name.endswith(("items", "records", "rows", "values", "list", "tags")):
        return "array"
    return "string"


def _public_nodes(nodes: list[GraphNode], topology: Topology) -> list[GraphNode]:
    """Pick the nodes that should receive an HTTP operation.

    Entry points and services are the primary surface. When a graph only
    contains generic functions/actions, roots are used instead so the spec is
    still useful rather than containing just ``/health``.
    """
    public = [node for node in nodes if node.is_public]
    if public:
        return sorted(
            public,
            key=lambda node: (topology.order.index(node.id), node.label.lower())
            if node.id in topology.order
            else (len(topology.order), node.label.lower()),
        )

    fallback = [
        node
        for node in nodes
        if node.id in topology.roots
        and node.type not in {"condition", "loop", "error_handler"}
        and not node.name.startswith(_PRIVATE_PREFIXES)
    ]
    return sorted(fallback, key=lambda node: node.label.lower())


def _unique_paths(nodes: list[GraphNode]) -> dict[str, str]:
    paths: dict[str, str] = {}
    used: set[str] = set()
    for node in nodes:
        slug = _slugify(node.name, fallback=f"node-{node.id}")
        candidate = f"/api/{slug}"
        suffix = 2
        while candidate in used:
            candidate = f"/api/{slug}-{suffix}"
            suffix += 1
        used.add(candidate)
        paths[node.id] = candidate
    return paths


def _operation_for(
    node: GraphNode,
    path: str,
    topology: Topology,
    path_map: dict[str, str],
) -> dict[str, Any]:
    method = "post" if node.parameters else "get"
    operation_id = _operation_id(node.name)
    if operation_id in {"get", "post", "put", "delete", "patch"}:
        operation_id = f"{operation_id}Node"

    dependencies = {
        "upstream": topology.reverse.get(node.id, []),
        "downstream": topology.adjacency.get(node.id, []),
    }
    downstream_paths = [
        path_map[downstream]
        for downstream in topology.adjacency.get(node.id, [])
        if downstream in path_map
    ]

    parameters: list[dict[str, Any]] = []
    request_body: dict[str, Any] | None = None
    if method == "get":
        for parameter in node.parameters:
            parameters.append(
                {
                    "name": parameter,
                    "in": "query",
                    "required": False,
                    "schema": {"type": _schema_type(parameter)},
                    "description": f"Value supplied for `{parameter}`.",
                }
            )
    else:
        properties = {
            parameter: {
                "type": _schema_type(parameter),
                "description": f"Value supplied for `{parameter}`.",
            }
            for parameter in node.parameters
        }
        request_body = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "additionalProperties": True,
                    }
                }
            },
        }

    description = node.description or (
        f"Invokes the `{node.name}` {node.type.replace('_', ' ')} node."
    )
    if downstream_paths:
        description = (
            f"{description}\n\nDownstream operations: "
            + ", ".join(f"`{value}`" for value in downstream_paths)
            + "."
        )

    operation: dict[str, Any] = {
        "operationId": operation_id,
        "summary": node.description or node.label,
        "description": description,
        "tags": [_GROUP_LABELS.get(node.group, node.group.title())],
        "responses": {
            "200": {
                "description": "Node completed successfully.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/AxiomNodeResult"}
                    }
                },
            },
            "400": {
                "description": "The request payload was rejected.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/AxiomError"}
                    }
                },
            },
            "500": {
                "description": "The node raised an unhandled exception.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/AxiomError"}
                    }
                },
            },
        },
        "x-axiomtrace": {
            "node_id": node.id,
            "node_type": node.type,
            "group": node.group,
            "language": node.language,
            "line": node.line,
            "end_line": node.end_line,
            "returns": node.returns,
            "dependencies": dependencies,
            "downstream_paths": downstream_paths,
        },
    }
    if parameters:
        operation["parameters"] = parameters
    if request_body is not None:
        operation["requestBody"] = request_body
    if node.is_vulnerable:
        operation["x-axiomtrace"]["security"] = {
            "vulnerability_type": node.vulnerability_type,
            "severity": node.severity,
        }
    return {method: operation}


def _component_schemas(nodes: list[GraphNode]) -> dict[str, Any]:
    schemas: dict[str, Any] = {
        "AxiomNodeResult": {
            "type": "object",
            "required": ["ok", "node_id"],
            "properties": {
                "ok": {"type": "boolean", "example": True},
                "node_id": {"type": "string"},
                "result": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Return value produced by the node.",
                },
                "execution_time_ms": {"type": "number", "format": "double"},
            },
        },
        "AxiomError": {
            "type": "object",
            "required": ["ok", "error"],
            "properties": {
                "ok": {"type": "boolean", "example": False},
                "error": {"type": "string"},
                "details": {"type": "object", "additionalProperties": True},
            },
        },
        "HealthStatus": {
            "type": "object",
            "required": ["ok", "status"],
            "properties": {
                "ok": {"type": "boolean"},
                "status": {"type": "string", "example": "ok"},
                "app": {"type": "string", "example": "AxiomTrace"},
                "version": {"type": "string"},
            },
        },
    }

    for node in nodes:
        if node.type != "database":
            continue
        schema_name = f"{_operation_id(node.name)[:1].upper()}{_operation_id(node.name)[1:]}Record"
        if schema_name in schemas:
            continue
        schemas[schema_name] = {
            "type": "object",
            "description": node.description or f"Record persisted by `{node.name}`.",
            "properties": {
                "id": {"type": "string"},
                "data": {"type": "object", "additionalProperties": True},
                "updated_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": True,
        }
    return schemas


def build_openapi(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    topology: Topology,
    *,
    project_name: str,
    version: str,
    base_url: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build a complete OpenAPI 3.0 document for the graph."""
    public = _public_nodes(nodes, topology)
    path_map = _unique_paths(public)

    tags: list[dict[str, str]] = []
    seen_groups: set[str] = set()
    for node in nodes:
        if node.group in seen_groups:
            continue
        seen_groups.add(node.group)
        tags.append(
            {
                "name": _GROUP_LABELS.get(node.group, node.group.title()),
                "description": f"Nodes in the {node.group} domain.",
            }
        )

    paths: dict[str, Any] = {
        "/health": {
            "get": {
                "operationId": "getHealth",
                "summary": "Runtime liveness probe.",
                "description": "Returns the AxiomTrace runtime status.",
                "tags": ["Runtime"],
                "responses": {
                    "200": {
                        "description": "The runtime is available.",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/HealthStatus"}
                            }
                        },
                    }
                },
            }
        }
    }
    for node in public:
        path = path_map[node.id]
        paths[path] = _operation_for(node, path, topology, path_map)

    component_nodes = [
        {
            "id": node.id,
            "label": node.label,
            "type": node.type,
            "group": node.group,
            "line": node.line,
            "end_line": node.end_line,
            "parameters": list(node.parameters),
            "returns": node.returns,
            "language": node.language,
        }
        for node in nodes
    ]
    component_edges = [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "kind": edge.kind,
            "label": edge.label,
        }
        for edge in edges
    ]

    return {
        "openapi": "3.0.3",
        "info": {
            "title": f"{project_name} API",
            "version": version,
            "description": (
                f"Generated by AxiomTrace from a {len(nodes)}-node, "
                f"{len(edges)}-edge architecture graph. The document exposes "
                "the public entry points and services discovered by the parser."
            ),
            "x-axiomtrace": {
                "generated_at": generated_at,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "cycle_count": topology.cycle_count,
                "orphan_count": len(topology.orphans),
            },
        },
        "servers": [{"url": base_url, "description": "AxiomTrace runtime"}],
        "tags": tags,
        "paths": paths,
        "components": {
            "schemas": _component_schemas(nodes),
        },
        "x-axiomtrace": {
            "generator": "AxiomTrace DocGenerator",
            "graph": {
                "nodes": component_nodes,
                "edges": component_edges,
                "roots": topology.roots,
                "leaves": topology.leaves,
                "cycles": topology.cycles,
            },
        },
    }


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _mermaid_label(value: str) -> str:
    text = value.replace('"', "'").replace("[", "(").replace("]", ")")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:90] or "node"


def _fence(code: str, language: str) -> str:
    safe = code.replace("```", "`` `")
    return f"```{language}\n{safe.rstrip()}\n```"


def _node_signature(node: GraphNode) -> str:
    params = ", ".join(node.parameters) if node.parameters else ""
    signature = f"{node.name}({params})"
    if node.returns:
        signature = f"{signature} -> {node.returns}"
    return signature


def build_markdown(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    topology: Topology,
    *,
    project_name: str,
    version: str,
    generated_at: str,
    openapi_paths: Mapping[str, str] | None = None,
) -> str:
    """Build the Markdown architecture specification."""
    node_by_id = {node.id: node for node in nodes}
    languages = sorted({node.language for node in nodes if node.language})
    groups = sorted({node.group for node in nodes})
    public = _public_nodes(nodes, topology)
    path_map = dict(openapi_paths or _unique_paths(public))

    lines: list[str] = [
        f"# {project_name} - Architecture Specification",
        "",
        "> Generated by **AxiomTrace**. This document is produced deterministically from the parsed graph; no source is executed.",
        "",
        "| Property | Value |",
        "| --- | --- |",
        f"| Generated at | {generated_at} |",
        f"| Version | {version} |",
        f"| Nodes | {len(nodes)} |",
        f"| Edges | {len(edges)} |",
        f"| Languages | {', '.join(languages) if languages else 'unknown'} |",
        f"| Domains | {', '.join(_GROUP_LABELS.get(group, group) for group in groups) if groups else 'none'} |",
        f"| Cycles | {topology.cycle_count} |",
        f"| Orphans | {len(topology.orphans)} |",
        "",
        "## Overview",
        "",
    ]

    if nodes:
        entry_labels = [
            node.label for node in nodes if node.id in topology.roots and node.is_public
        ]
        entry_text = (
            "The public surface starts at "
            + ", ".join(f"`{label}`" for label in entry_labels)
            + "."
            if entry_labels
            else "No dedicated entry point was detected; the graph is analysed as a component network."
        )
        lines.extend(
            [
                f"The architecture contains **{len(nodes)} components** connected by "
                f"**{len(edges)} directed dependencies**. {entry_text}",
                "",
            ]
        )
        if topology.cycles:
            lines.append(
                f"**Topology warning:** {len(topology.cycles)} cycle(s) were detected. "
                "Review the risk section before deploying changes."
            )
            lines.append("")
    else:
        lines.extend(
            [
                "The graph is empty. The generated OpenAPI document still contains a "
                "valid `/health` operation so downstream tooling can consume it.",
                "",
            ]
        )

    lines.extend(["## Architecture Diagram", ""])
    if nodes:
        lines.append("```mermaid")
        lines.append("flowchart LR")
        for node in nodes:
            label = _mermaid_label(node.label)
            lines.append(f'  {node.id}["{label}"]')
        for edge in edges:
            arrow = "-->" if edge.kind not in {"async", "event"} else "-.->"
            label = _mermaid_label(edge.label or edge.kind)
            lines.append(f'  {edge.source} {arrow}|"{label}"| {edge.target}')
        for group in groups:
            members = [node.id for node in nodes if node.group == group]
            if not members:
                continue
            lines.append(f"  classDef {group} fill:#F4F5F7,stroke:#E3E5E9,color:#0B0C0E;")
            lines.append(f"  class {','.join(members)} {group};")
        lines.append("```")
    else:
        lines.append("_No nodes available to render a diagram._")
    lines.append("")

    lines.extend(["## Components", ""])
    if nodes:
        lines.extend(
            [
                "| ID | Component | Type | Domain | Lines | Parameters | Returns | Description |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for node in nodes:
            line_range = "-"
            if node.line and node.end_line and node.end_line != node.line:
                line_range = f"L{node.line}-L{node.end_line}"
            elif node.line:
                line_range = f"L{node.line}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_escape(node.id),
                        _md_escape(node.label),
                        _md_escape(node.type.replace("_", " ")),
                        _md_escape(_GROUP_LABELS.get(node.group, node.group)),
                        line_range,
                        _md_escape(", ".join(node.parameters) or "-"),
                        _md_escape(node.returns or "-"),
                        _md_escape(node.description or "-"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("_No components recorded._")
    lines.append("")

    lines.extend(["## Dependencies", ""])
    if edges:
        lines.extend(
            [
                "| Source | Target | Kind | Label |",
                "| --- | --- | --- | --- |",
            ]
        )
        for edge in edges:
            source = node_by_id.get(edge.source)
            target = node_by_id.get(edge.target)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_escape(source.label if source else edge.source),
                        _md_escape(target.label if target else edge.target),
                        _md_escape(edge.kind),
                        _md_escape(edge.label or "-"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("_No dependencies recorded._")
    lines.append("")

    lines.extend(["## API Surface", ""])
    if public:
        lines.extend(
            [
                "| Method | Path | Operation | Request | Response |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for node in public:
            method = "POST" if node.parameters else "GET"
            request = (
                ", ".join(node.parameters)
                if node.parameters
                else "no parameters"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        method,
                        f"`{path_map.get(node.id, '/api/' + _slugify(node.name))}`",
                        _md_escape(node.description or node.label),
                        _md_escape(request),
                        "`AxiomNodeResult`",
                    ]
                )
                + " |"
            )
    else:
        lines.append("_No public operations detected._")
    lines.extend(
        [
            "",
            "Every generated operation returns the shared `AxiomNodeResult` envelope "
            "and uses `AxiomError` for rejected or failed executions.",
            "",
        ]
    )

    lines.extend(["## Topology Risks", ""])
    risk_rows: list[str] = []
    if topology.cycles:
        for cycle in topology.cycles:
            labels = [
                node_by_id[node_id].label if node_id in node_by_id else node_id
                for node_id in cycle
            ]
            risk_rows.append(
                "- **Cycle:** " + " -> ".join(f"`{label}`" for label in labels)
            )
    if topology.orphans:
        labels = [
            node_by_id[node_id].label if node_id in node_by_id else node_id
            for node_id in topology.orphans
        ]
        risk_rows.append(
            "- **Orphan components:** " + ", ".join(f"`{label}`" for label in labels)
        )
    vulnerable = [node for node in nodes if node.is_vulnerable]
    if vulnerable:
        for node in vulnerable:
            risk_rows.append(
                f"- **Security ({_md_escape(node.severity or 'HIGH')}):** "
                f"`{_md_escape(node.label)}`"
                + (
                    f" - {_md_escape(node.vulnerability_type)}"
                    if node.vulnerability_type
                    else ""
                )
            )
    if not risk_rows:
        risk_rows.append(
            "- No cycles, orphan components or security findings were detected."
        )
    lines.extend(risk_rows)
    lines.append("")

    lines.extend(["## Node Details", ""])
    if nodes:
        for node in nodes:
            lines.extend(
                [
                    f"### {node.label}",
                    "",
                    f"- **ID:** `{node.id}`",
                    f"- **Type:** {node.type.replace('_', ' ')}",
                    f"- **Domain:** {_GROUP_LABELS.get(node.group, node.group)}",
                    f"- **Signature:** `{_node_signature(node)}`",
                    f"- **Language:** {node.language}",
                ]
            )
            if node.line:
                location = f"L{node.line}"
                if node.end_line and node.end_line != node.line:
                    location += f"-L{node.end_line}"
                lines.append(f"- **Location:** {location}")
            if node.description:
                lines.extend(["", node.description])
            if node.code:
                lines.extend(["", _fence(node.code, node.language or "text")])
            lines.append("")
    else:
        lines.append("_No node details available._")

    lines.extend(
        [
            "## Reproducibility",
            "",
            "This document was generated without network access. The same graph and "
            "version always produce the same structure; only the generation timestamp "
            "changes.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _filename_stem(project_name: str) -> str:
    return _slugify(project_name, fallback="axiomtrace")


class DocGenerationService:
    """Facade used by the HTTP layer and direct integrations."""

    def generate(
        self,
        nodes: Iterable[Mapping[str, Any]] | None,
        edges: Iterable[Mapping[str, Any]] | None,
        *,
        project_name: str = "Untitled",
        version: str = DEFAULT_VERSION,
        base_url: str = DEFAULT_BASE_URL,
    ) -> dict[str, Any]:
        """Return both documentation artefacts plus summary metadata."""
        try:
            safe_name = _as_text(project_name, "Untitled") or "Untitled"
            safe_version = _as_text(version, DEFAULT_VERSION) or DEFAULT_VERSION
            safe_base_url = _as_text(base_url, DEFAULT_BASE_URL) or DEFAULT_BASE_URL
            if not re.match(r"^https?://", safe_base_url):
                safe_base_url = f"http://{safe_base_url}"

            safe_nodes, safe_edges = normalise_graph(nodes, edges)
            topology = analyse_topology(safe_nodes, safe_edges)
            generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            public = _public_nodes(safe_nodes, topology)
            path_map = _unique_paths(public)

            openapi = build_openapi(
                safe_nodes,
                safe_edges,
                topology,
                project_name=safe_name,
                version=safe_version,
                base_url=safe_base_url,
                generated_at=generated_at,
            )
            markdown = build_markdown(
                safe_nodes,
                safe_edges,
                topology,
                project_name=safe_name,
                version=safe_version,
                generated_at=generated_at,
                openapi_paths=path_map,
            )

            # JSON round-trip is a cheap validity guard: a malformed value
            # (NaN, a dataclass, a non-string key) fails here instead of in
            # the browser's JSON.parse.
            openapi = json.loads(json.dumps(openapi, allow_nan=False))
            stem = _filename_stem(safe_name)
            return {
                "project_name": safe_name,
                "version": safe_version,
                "generated_at": generated_at,
                "openapi": openapi,
                "markdown": markdown,
                "summary": {
                    "nodes": len(safe_nodes),
                    "edges": len(safe_edges),
                    "paths": len(openapi.get("paths", {})),
                    "operations": len(public) + 1,
                    "groups": sorted({node.group for node in safe_nodes}),
                    "cycles": topology.cycle_count,
                    "orphans": len(topology.orphans),
                    "public_nodes": [node.id for node in public],
                },
                "filenames": {
                    "openapi": f"{stem}.openapi.json",
                    "markdown": f"{stem}.architecture.md",
                },
            }
        except DocGenerationError:
            raise
        except Exception as error:  # noqa: BLE001 - service boundary
            logger.exception("Documentation generation failed")
            raise DocGenerationError(
                f"Documentation generation failed: {type(error).__name__}: {error}"
            ) from error

    def generate_openapi(
        self,
        nodes: Iterable[Mapping[str, Any]] | None,
        edges: Iterable[Mapping[str, Any]] | None,
        *,
        project_name: str = "Untitled",
        version: str = DEFAULT_VERSION,
        base_url: str = DEFAULT_BASE_URL,
    ) -> dict[str, Any]:
        result = self.generate(
            nodes,
            edges,
            project_name=project_name,
            version=version,
            base_url=base_url,
        )
        return {
            "project_name": result["project_name"],
            "version": result["version"],
            "generated_at": result["generated_at"],
            "spec": result["openapi"],
            "summary": result["summary"],
            "filename": result["filenames"]["openapi"],
        }

    def generate_markdown(
        self,
        nodes: Iterable[Mapping[str, Any]] | None,
        edges: Iterable[Mapping[str, Any]] | None,
        *,
        project_name: str = "Untitled",
        version: str = DEFAULT_VERSION,
    ) -> dict[str, Any]:
        result = self.generate(
            nodes,
            edges,
            project_name=project_name,
            version=version,
        )
        return {
            "project_name": result["project_name"],
            "version": result["version"],
            "generated_at": result["generated_at"],
            "markdown": result["markdown"],
            "summary": result["summary"],
            "filename": result["filenames"]["markdown"],
        }


__all__ = [
    "DocGenerationError",
    "DocGenerationService",
    "GraphEdge",
    "GraphNode",
    "Topology",
    "analyse_topology",
    "build_markdown",
    "build_openapi",
    "normalise_graph",
]
