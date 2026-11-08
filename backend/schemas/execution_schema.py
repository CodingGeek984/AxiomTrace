"""Schemas for sandbox execution, mutation stress testing and AI endpoints."""

from __future__ import annotations

from marshmallow import Schema, fields, validate


class ExecuteRequestSchema(Schema):
    """POST /api/v1/sandbox/execute

    ``input_payload`` is the canonical key; ``input_data`` is accepted as a
    legacy alias so existing desktop clients keep working unchanged.
    Graceful policy: missing ``code`` normalises to ``""`` and the sandbox
    then derives executable fallback source from ``label``/``file``/``module``
    (or an explicit ``fallback_code`` snippet) instead of rejecting the run.
    """

    node_id = fields.Str(load_default="manual")
    code = fields.Str(load_default="")
    source = fields.Str(load_default=None, allow_none=True)
    label = fields.Str(load_default=None, allow_none=True)
    file = fields.Str(load_default=None, allow_none=True)
    module = fields.Str(load_default=None, allow_none=True)
    fallback_code = fields.Str(load_default=None, allow_none=True)
    language = fields.Str(load_default=None, allow_none=True)
    input_payload = fields.Raw(load_default=None)
    input_data = fields.Raw(load_default=None)
    timeout = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0.1, max=30.0))
    project_id = fields.Int(load_default=None, allow_none=True)


class MutateRequestSchema(Schema):
    """POST /api/v1/sandbox/mutate — same graceful policy as execute."""

    node_id = fields.Str(load_default="manual")
    code = fields.Str(load_default="")
    source = fields.Str(load_default=None, allow_none=True)
    label = fields.Str(load_default=None, allow_none=True)
    file = fields.Str(load_default=None, allow_none=True)
    module = fields.Str(load_default=None, allow_none=True)
    fallback_code = fields.Str(load_default=None, allow_none=True)
    language = fields.Str(load_default=None, allow_none=True)
    iterations = fields.Int(load_default=1, validate=validate.Range(min=1, max=5))
    timeout = fields.Float(load_default=None, allow_none=True, validate=validate.Range(min=0.1, max=30.0))
    project_id = fields.Int(load_default=None, allow_none=True)


class AIContextRequestSchema(Schema):
    """POST /api/v1/ai/context — missing nodes/edges normalise to ``[]``."""

    nodes = fields.List(fields.Dict(), load_default=list)
    edges = fields.List(fields.Dict(), load_default=list)
    project_name = fields.Str(load_default=None, allow_none=True)
    project_id = fields.Int(load_default=None, allow_none=True)


class EdgeCaseRequestSchema(Schema):
    """POST /api/v1/ai/edge-cases"""

    code = fields.Str(load_default="")
    api_key = fields.Str(load_default=None, allow_none=True)
    node_id = fields.Str(load_default=None, allow_none=True)
    project_id = fields.Int(load_default=None, allow_none=True)


class LogsQuerySchema(Schema):
    """GET /api/v1/projects/<id>/logs"""

    project_id = fields.Int(load_default=None, allow_none=True)
    node_id = fields.Str(load_default=None, allow_none=True)
    limit = fields.Int(load_default=100, validate=validate.Range(min=1, max=500))


__all__ = [
    "ExecuteRequestSchema",
    "MutateRequestSchema",
    "AIContextRequestSchema",
    "EdgeCaseRequestSchema",
    "LogsQuerySchema",
]
