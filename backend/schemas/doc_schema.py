"""Schemas for documentation generation endpoints."""

from __future__ import annotations

from marshmallow import Schema, fields, validate


class DocGenerateRequestSchema(Schema):
    """POST /api/v1/docs/generate (also openapi/markdown variants)."""

    nodes = fields.List(fields.Dict(), load_default=list)
    edges = fields.List(fields.Dict(), load_default=list)
    project_name = fields.Str(
        load_default="Untitled", validate=validate.Length(max=255)
    )
    version = fields.Str(load_default="1.0.0", validate=validate.Length(max=64))
    base_url = fields.Str(
        load_default="http://127.0.0.1:5000", validate=validate.Length(max=2048)
    )


__all__ = ["DocGenerateRequestSchema"]
