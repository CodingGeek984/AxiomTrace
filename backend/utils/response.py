"""Structured JSON response envelope helpers.

Every controller returns the same shape so the desktop frontend can treat
the API uniformly::

    success  ->  {"ok": True,  "code": 200, **data}
    error    ->  {"ok": False, "code": 400, "error": "...", "details": ...}

When ``data`` is a dictionary its keys are merged into the envelope (single
fetch clients receive metadata and payload together). Non-dictionary data is
nested under ``data`` to keep the envelope schema stable.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Raised for client mistakes - rendered as a JSON 4xx response."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)


def success_response(data: Any = None, code: int = 200) -> tuple[dict[str, Any], int]:
    """Build a successful JSON envelope together with its HTTP status code."""
    envelope: dict[str, Any] = {"ok": True, "code": int(code)}

    if data is None:
        return envelope, int(code)

    if isinstance(data, dict):
        for key, value in data.items():
            if key not in envelope:
                envelope[key] = value
    else:
        envelope["data"] = data

    return envelope, int(code)


def error_response(
    message: str,
    code: int = 400,
    details: Any = None,
) -> tuple[dict[str, Any], int]:
    """Build an error JSON envelope together with its HTTP status code."""
    envelope: dict[str, Any] = {
        "ok": False,
        "code": int(code),
        "error": str(message),
    }
    if details is not None:
        envelope["details"] = details
    return envelope, int(code)


__all__ = ["success_response", "error_response", "ApiError"]
