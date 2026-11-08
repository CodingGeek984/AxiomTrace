"""Schemas for AST node/edge payloads and the parse endpoint."""

from __future__ import annotations

from marshmallow import Schema, fields, validate

VALID_NODE_TYPES = (
    "function",
    "condition",
    "loop",
    "action",
    "entrypoint",
    "service",
    "database",
    "utility",
    "error_handler",
)


class ASTNodeSchema(Schema):
    """One visual node in the React Flow graph."""

    id = fields.Str(required=True)
    type = fields.Str(load_default="action", validate=validate.OneOf(VALID_NODE_TYPES))
    label = fields.Str(load_default="")
    code = fields.Str(load_default="")
    position = fields.Dict(load_default=dict)
    line = fields.Int(load_default=None, allow_none=True)
    end_line = fields.Int(load_default=None, allow_none=True)
    language = fields.Str(load_default="python")
    data = fields.Dict(load_default=dict)


class GraphEdgeSchema(Schema):
    """One directed edge between nodes."""

    id = fields.Str(required=True)
    source = fields.Str(required=True)
    target = fields.Str(required=True)
    animated = fields.Bool(load_default=False)
    kind = fields.Str(load_default="sequence")
    label = fields.Str(load_default=None, allow_none=True)


class ParseRequestSchema(Schema):
    """POST /api/v1/parse/code

    Graceful policy: a missing/partial payload normalises to
    ``{"code": ""}`` instead of rejecting the request; the parse controller
    then decides (empty code -> empty-graph diagnostics, never a bare
    schema rejection).
    """

    code = fields.Str(load_default="")
    language = fields.Str(load_default=None, allow_none=True)
    file = fields.Str(load_default=None, allow_none=True)
    project_id = fields.Int(load_default=None, allow_none=True)


class ArchitectureRequestSchema(Schema):
    """POST /api/v1/parse/architecture

    Graceful policy: missing ``prompt``/``code_context`` normalises to
    ``None`` fields; the controller supplies the default blueprint prompt
    instead of rejecting the request.
    """

    prompt = fields.Str(load_default=None, allow_none=True)
    code_context = fields.Str(load_default=None, allow_none=True)
    current_graph = fields.Dict(load_default=None, allow_none=True)
    project_id = fields.Int(load_default=None, allow_none=True)
    api_key = fields.Str(load_default=None, allow_none=True)


__all__ = [
    "ASTNodeSchema",
    "GraphEdgeSchema",
    "ParseRequestSchema",
    "ArchitectureRequestSchema",
    "VALID_NODE_TYPES",
]
