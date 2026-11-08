"""Helpers for normalising Marshmallow ``load()`` results.

Validation policy (DIAGNOSTIC & AUTO-FIX DIRECTIVE): a request payload that
is empty, partially malformed, or missing optional metadata must never
reject the request with a 4xx schema rejection. Instead it is
gracefully normalised — known-good fields are kept (``error.valid_data``),
missing fields receive safe defaults (e.g. ``{"files": []}``), and the
controller decides from the sanitised payload whether a *specific* business
rule (e.g. "code is required") still applies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from marshmallow import EXCLUDE, ValidationError


def loaded_dict(loaded: Any) -> dict[str, Any]:
    """Return a marshmallow ``load()`` result as a plain dictionary.

    Marshmallow's return annotation is untyped, so normalising here keeps
    static type checkers from treating the result as an arbitrary object.
    """
    if isinstance(loaded, Mapping):
        return dict(loaded)
    return dict(vars(loaded))


def safe_load(
    schema: Any,
    data: Any,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load ``data`` with ``schema`` without ever raising ``ValidationError``.

    On validation failure the valid subset (``error.valid_data``) is merged
    over ``defaults`` so downstream controllers always receive a dict of the
    expected shape. Non-dict payloads normalise to ``{}``.
    """
    defaults_dict: dict[str, Any] = dict(defaults or {})
    payload: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}
    try:
        loaded = loaded_dict(schema.load(payload, unknown=EXCLUDE))
    except ValidationError as error:
        valid = dict(error.valid_data) if isinstance(error.valid_data, Mapping) else {}
        return {**defaults_dict, **valid}
    except Exception:  # noqa: BLE001 - normalisation must never raise
        return dict(defaults_dict)
    return {**defaults_dict, **loaded}


__all__ = ["loaded_dict", "safe_load"]
