"""Security scanning endpoint.

POST /api/v1/security/scan

Two request shapes are accepted:

* ``{"nodes": [...]}`` - scan a full set of flow nodes (as stored on the
  canvas). Each node's ``data.code`` snippet is inspected and the node is
  enriched in place with ``is_vulnerable`` / ``severity`` / ``mitigation``
  metadata. The mutated nodes are returned so the client can patch its
  canvas state without a full graph reload.
* ``{"code": "...", "language": "python", "node_id": "n1"}`` - scan a raw
  source snippet (or the selected node only).

The heavy lifting lives in :mod:`services.security_scanner`; this module
only validates input and shapes the response envelope.
"""

from __future__ import annotations

import copy
from typing import Any

from flask import Blueprint, request
from marshmallow import Schema, fields, validate

from services import security_scanner
from utils import ApiError, get_logger, safe_load, success_response

logger = get_logger("security")

security_bp = Blueprint("security", __name__, url_prefix="/api/v1/security")

_MAX_NODES = 2000
_MAX_CODE_CHARS = 200_000


class SecurityScanSchema(Schema):
    nodes = fields.List(fields.Dict(), load_default=None, allow_none=True)
    code = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=_MAX_CODE_CHARS))
    language = fields.Str(load_default="python", validate=validate.Length(max=30))
    node_id = fields.Str(load_default=None, allow_none=True)


def _load_request() -> dict[str, Any]:
    raw = request.get_json(silent=True) or {}
    payload = dict(raw) if isinstance(raw, dict) else {}
    return safe_load(
        SecurityScanSchema(),
        payload,
        defaults={"nodes": None, "code": None, "language": "python", "node_id": None},
    )


def _summarise(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity = {severity: 0 for severity in security_scanner.SEVERITY_ORDER}
    findings: list[dict[str, Any]] = []
    vulnerable = 0
    for node in nodes:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        if not (data.get("is_vulnerable") or node.get("is_vulnerable")):
            continue
        vulnerable += 1
        node_findings = data.get("security_findings") or node.get("security_findings") or []
        for finding in node_findings:
            if not isinstance(finding, dict):
                continue
            findings.append({"node_id": str(node.get("id") or ""), **finding})
            severity = str(finding.get("severity") or "LOW").upper()
            by_severity[severity] = by_severity.get(severity, 0) + 1
    return {
        "vulnerable_nodes": vulnerable,
        "total_findings": len(findings),
        "by_severity": by_severity,
        "findings": findings,
    }


@security_bp.post("/scan")
def scan_security() -> tuple[dict[str, Any], int]:
    data = _load_request()

    if data.get("code"):
        findings = security_scanner.scan_source(data["code"], language=data.get("language") or "python")
        node_id = data.get("node_id")
        tagged = [{"node_id": node_id, **finding} for finding in findings]
        index = security_scanner.security_index(tagged)
        return success_response(
            {
                "node_id": node_id,
                "findings": tagged,
                "summary": {
                    "vulnerable_nodes": 1 if tagged else 0,
                    "total_findings": len(tagged),
                    "by_severity": _count_by_severity(tagged),
                    "security_index": index,
                    "future_risk_rating": security_scanner.future_risk_rating(tagged, index),
                    "findings": tagged,
                },
                "nodes": [],
            }
        )

    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ApiError("Provide either 'code' or a non-empty 'nodes' list.", 400)
    if len(nodes) > _MAX_NODES:
        raise ApiError(f"Too many nodes in scan request (max {_MAX_NODES}).", 413)

    # Deep-copy so a partially-built scan never leaves the caller's object
    # mutated when validation fails midway.
    working = copy.deepcopy(nodes)
    graph = {"nodes": working}
    try:
        security_scanner.scan_graph(graph)
    except Exception as error:  # noqa: BLE001 - scanner must not 500 the API
        logger.exception("Security scan failed")
        raise ApiError(f"Security scan failed: {type(error).__name__}: {error}", 500) from error

    scanned_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else working
    summary = graph.get("security") if isinstance(graph.get("security"), dict) else _summarise(scanned_nodes)
    findings = summary.get("findings") if isinstance(summary.get("findings"), list) else []
    vulnerable_ids = [
        str(node.get("id"))
        for node in scanned_nodes
        if isinstance(node, dict)
        and ((node.get("data") or {}).get("is_vulnerable") if isinstance(node.get("data"), dict) else node.get("is_vulnerable"))
    ]

    return success_response(
        {
            "findings": findings,
            "summary": summary,
            "nodes": scanned_nodes,
            "vulnerable_node_ids": vulnerable_ids,
        }
    )


def _count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {severity: 0 for severity in security_scanner.SEVERITY_ORDER}
    for finding in findings:
        severity = str(finding.get("severity") or "LOW").upper()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


@security_bp.get("/health")
def security_health() -> tuple[dict[str, Any], int]:
    return success_response({"scanner": "ok", "patterns": [
        "hardcoded_secret", "unsafe_exec", "sql_injection", "missing_auth",
        "bola", "ssrf", "nosql_injection", "xss", "unsafe_deserialization",
        "race_condition", "unvalidated_payload",
    ]})


__all__ = ["security_bp"]
