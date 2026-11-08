"""Schemas for project open/save/load endpoints."""

from __future__ import annotations

from marshmallow import Schema, fields, validate


class ProjectOpenSchema(Schema):
    """POST /api/v1/projects/open

    Graceful policy: fields default to ``None``; the controller supplies
    safe fallbacks (name from path, path from name) instead of rejecting
    partial payloads.
    """

    name = fields.Str(load_default=None, allow_none=True)
    path = fields.Str(load_default=None, allow_none=True)
    scan = fields.Bool(load_default=False)


class ProjectGraphSaveSchema(Schema):
    """POST /api/v1/projects/<id>/schema

    Graceful policy: ``nodes``/``edges`` default to ``[]`` (empty graph);
    a missing ``project_id`` is diagnosed by the controller specifically.
    """

    project_id = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=1))
    nodes = fields.List(fields.Dict(), load_default=list)
    edges = fields.List(fields.Dict(), load_default=list)
    file_path = fields.Str(load_default=None, allow_none=True)


__all__ = ["ProjectOpenSchema", "ProjectGraphSaveSchema"]
