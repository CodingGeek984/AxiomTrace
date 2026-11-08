"""AxiomTrace AI analysis domain service.

Two capabilities:

``build_lasso_context_dump``
    Turn a lasso-selected sub-graph into a compact Markdown document that an
    LLM (DeepSeek, Claude, GPT ...) can reason over without extra noise.

``predict_edge_cases``
    Detect missing try/except blocks and unguarded network calls - through
    the official OpenAI SDK (OpenAI-compatible endpoints incl. DeepSeek)
    when an API key is available, otherwise via a deterministic offline
    heuristic so the feature always returns actionable output.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from config import config
from utils.logger import get_logger

logger = get_logger("ai")


def _clip(text: str | None, limit: int) -> str:
    """Truncate ``text`` to ``limit`` characters without breaking the flow."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "\u2026"

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
ARCHITECTURE_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL
)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

ARCHITECTURE_SYSTEM_PROMPT = (
    "You are AxiomTrace's software architecture mapper. "
    "Given a design description or source excerpt, identify discrete architectural "
    "components and the relationships between them. "
    "Only return JSON matching this schema: "
    '{"components":[{"name":"string","tag":"service|database|api|function","role":"..."},'
    '"connections":[{"source":"string","target":"string","kind":"call|data_flow|api_request","label":"..."}]]}. '
    "Tags must be one of service, database, api, function. "
    "Prefer coarse-grained components (API Gateway, Auth Service, User Database). "
    "Do not invent implementation details beyond what is stated or clearly implied."
)

LLM_SYSTEM_PROMPT = (
    "You are AxiomTrace's static analysis engine. You receive the source of a "
    "single logical node extracted from an application's AST. Identify: "
    "(1) calls that can raise but are not wrapped in try/except, "
    "(2) network calls issued without a timeout, "
    "(3) unguarded None/null dereferences on results of network or parsing calls. "
    "Respond ONLY with minified JSON matching this schema: "
    '{"findings":[{"category":"missing_try_catch|network_timeout|null_dereference",'
    '"line":1,"snippet":"...","severity":"low|medium|high","message":"...",'
    '"suggestion":"..."}],"summary":"one sentence"}. '
    "Do not invent line numbers outside the provided source."
)


# ----------------------------------------------------------------------
# Lasso context stitcher
# ----------------------------------------------------------------------

def _node_label(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    return str(data.get("label") or node.get("label") or node.get("id") or "node")


def _node_type(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    raw = data.get("type") or node.get("type") or "action"
    return str(raw) if raw in {"function", "condition", "loop", "action"} else "action"


def _node_code(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    code = data.get("code") or node.get("code") or ""
    if not isinstance(code, str):
        code = str(code)
    limit = config.NODE_CODE_MAX_CHARS
    if len(code) > limit:
        return code[:limit].rstrip() + "\n# [... truncated]"
    return code


def _node_language(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    return str(data.get("language") or node.get("language") or "python")


def _node_position_label(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    line = data.get("line") or node.get("line")
    end_line = data.get("end_line") or node.get("end_line")
    if line and end_line and end_line != line:
        return f"L{line}-{end_line}"
    if line:
        return f"L{line}"
    return "L?"


def _fence_for(code: str) -> str:
    return "````" if "```" in code else "```"


def _short_type(node: dict[str, Any]) -> str:
    """Two-letter type tag - keeps the flow table readable and small."""
    mapping = {"function": "fn", "condition": "cond", "loop": "loop", "action": "act"}
    return mapping.get(_node_type(node), "act")


def estimate_tokens(text: str) -> int:
    """Estimate the token footprint of a string.

    Uses the configurable chars-per-token ratio (4 is a good average for
    English + code) and never returns 0 for a non-empty payload.
    """
    if not text:
        return 0
    ratio = max(1, int(config.AI_CHARS_PER_TOKEN))
    return max(1, len(text) // ratio)


def _compact_flow(
    safe_edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """Render the adjacency as one compact line per edge."""
    rows: list[str] = []
    for edge in safe_edges:
        source = node_by_id.get(str(edge.get("source")))
        target = node_by_id.get(str(edge.get("target")))
        if source is None or target is None:
            continue
        label = edge.get("label")
        suffix = f" *{label}*" if label else ""
        edge_kind = str(edge.get("kind") or "sequence")
        marker = " ~async" if edge.get("animated") and edge_kind == "sequence" else ""
        rows.append(
            f"{_node_label(source)}[{_short_type(source)}] -> "
            f"{_node_label(target)}[{_short_type(target)}]{suffix}{marker}"
        )
    return rows


def build_lasso_context_dump(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    project_name: str | None = None,
) -> str:
    """Render selected nodes/edges as a token-efficient Markdown brief.

    Token efficiency comes from three places:

    * identical code blocks are emitted once and referenced afterwards,
    * the flow table uses short type tags instead of full sentences,
    * a character budget truncates the dump instead of producing a prompt
      no model will accept.
    """
    return build_lasso_context_report(nodes, edges, project_name=project_name)["markdown"]


def build_lasso_context_report(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    project_name: str | None = None,
) -> dict[str, Any]:
    """Build the Markdown brief plus the statistics describing it."""
    safe_nodes = [node for node in (nodes or []) if isinstance(node, dict)]
    safe_edges = [edge for edge in (edges or []) if isinstance(edge, dict)]

    if not safe_nodes:
        markdown = (
            "# AxiomTrace - LLM Context Dump\n\n"
            "_No nodes were selected. Lasso a region of the graph and export "
            "again._\n"
        )
        return {
            "markdown": markdown,
            "node_count": 0,
            "edge_count": 0,
            "char_count": len(markdown),
            "estimated_tokens": estimate_tokens(markdown),
            "deduplicated_blocks": 0,
            "truncated": False,
        }

    node_by_id: dict[str, dict[str, Any]] = {
        str(node.get("id")): node for node in safe_nodes
    }
    language = _node_language(safe_nodes[0])

    ordered_nodes = sorted(
        safe_nodes,
        key=lambda node: (
            (node.get("data") or {}).get("line")
            or node.get("line")
            or 10**9
        ),
    )

    budget = max(2000, int(config.AI_CONTEXT_MAX_CHARS))
    used = 0
    truncated = False

    lines: list[str] = []
    lines.append("# AxiomTrace - LLM Context Dump")
    lines.append("")
    scope = project_name or "untitled project"
    lines.append(
        f"> `{scope}` · {len(safe_nodes)} node(s) · {len(safe_edges)} edge(s) · "
        f"`{language}` · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append("")

    # ------------------------------------------------------------------
    # 1. Topology
    # ------------------------------------------------------------------
    lines.append("## Flow")
    lines.append("")
    adjacency_rows = _compact_flow(safe_edges, node_by_id)
    if adjacency_rows:
        lines.extend(adjacency_rows)
    else:
        lines.append("_no internal edges in this selection_")
    lines.append("")

    # ------------------------------------------------------------------
    # 2. Extracted source
    # ------------------------------------------------------------------
    lines.append("## Code")
    lines.append("")
    emitted_code: dict[str, str] = {}
    duplicate_blocks = 0

    for node in ordered_nodes:
        node_id = str(node.get("id"))
        code = _node_code(node)
        header = (
            f"### {node_id} · {_short_type(node)} · "
            f"`{_node_label(node)}` · {_node_position_label(node)}"
        )
        async_marker = " (async)" if (node.get("data") or {}).get("async") else ""

        fingerprint = code.strip()
        if fingerprint and fingerprint in emitted_code:
            duplicate_blocks += 1
            lines.append(f"{header}{async_marker}")
            lines.append(f"_identical to {emitted_code[fingerprint]}_")
            lines.append("")
            continue

        remaining = budget - used - len(header) - 16
        if remaining <= 0:
            truncated = True
            lines.append(f"{header}{async_marker}")
            lines.append("_omitted - context budget exhausted_")
            lines.append("")
            continue

        truncated_code = code
        if len(truncated_code) > remaining:
            truncated_code = truncated_code[: max(0, remaining - 24)].rstrip() + "\n# [truncated]"
            truncated = True

        fence = _fence_for(truncated_code)
        lines.append(f"{header}{async_marker}")
        lines.append(fence + _node_language(node))
        lines.append(truncated_code if truncated_code else "# <empty node>")
        lines.append(fence)
        lines.append("")
        used += len(header) + len(truncated_code) + 16
        if fingerprint:
            emitted_code[fingerprint] = node_id

    # ------------------------------------------------------------------
    # 3. Contract notes for the model
    # ------------------------------------------------------------------
    lines.append("## Review")
    lines.append("")
    lines.append("- Flag unhandled exceptions, missing timeouts and unsafe None handling.")
    lines.append("- Reply with a numbered findings list, then a unified diff patch.")
    lines.append("")

    markdown = "\n".join(lines)
    if len(markdown) > budget:
        markdown = markdown[: max(0, budget - 24)].rstrip() + "\n\n_[dump truncated]_"
        truncated = True

    return {
        "markdown": markdown,
        "node_count": len(safe_nodes),
        "edge_count": len(safe_edges),
        "char_count": len(markdown),
        "estimated_tokens": estimate_tokens(markdown),
        "deduplicated_blocks": duplicate_blocks,
        "truncated": truncated,
    }


# ----------------------------------------------------------------------
# Offline heuristic edge-case scanner
# ----------------------------------------------------------------------

NETWORK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("requests.get", re.compile(r"\brequests\s*\.\s*get\s*\(")),
    ("requests.post", re.compile(r"\brequests\s*\.\s*post\s*\(")),
    ("requests.put", re.compile(r"\brequests\s*\.\s*put\s*\(")),
    ("requests.delete", re.compile(r"\brequests\s*\.\s*delete\s*\(")),
    ("urlopen", re.compile(r"\burllib\s*\.\s*request\s*\.\s*urlopen\s*\(")),
    ("fetch", re.compile(r"\bfetch\s*\(\s*[`'\"]|\bawait\s+fetch\s*\(")),
    ("axios", re.compile(r"\baxios\s*\.\s*(get|post|put|delete|request)\s*\(")),
    ("http.get", re.compile(r"\bhttp\s*\.\s*get\s*\(")),
    ("httpx", re.compile(r"\bhttpx\s*\.\s*(get|post|AsyncClient)\b")),
)

UNGUARDED_PARSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("json.loads", re.compile(r"\bjson\s*\.\s*loads\s*\(")),
    ("res.json()", re.compile(r"\.\s*json\s*\(\s*\)")),
    ("response.body", re.compile(r"\.\s*body\b")),
)

BARE_EXCEPT_PATTERN = re.compile(r"except\s*:")
SWALLOWED_EXCEPT_PATTERN = re.compile(r"except\s+Exception[^:]*:\s*(\n\s*pass\b)?")
EVAL_PATTERN = re.compile(r"\b(eval|exec|Function|compile)\s*\(")
OPEN_WITHOUT_WITH_PATTERN = re.compile(r"^\s*[a-zA-Z_][\w]*\s*=\s*open\s*\(", re.MULTILINE)
DIVISION_PATTERN = re.compile(r"[^/]/[^/=*\s][\w\)\]]*\s*/\s*(?!/)")  # rough


def _line_of(code: str, match: re.Match[str]) -> int:
    return code.count("\n", 0, match.start()) + 1


def _snippet(code: str, line: int) -> str:
    lines = code.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:160]
    return ""


def _has_try_covering_line(code: str, line: int) -> bool:
    """Best-effort check whether a line sits inside any try block."""
    tree_try_lines: list[tuple[int, int]] = []
    try_pattern = re.compile(r"^\s*try\s*:\s*$")
    lines = code.splitlines()
    index = 0
    while index < len(lines):
        if try_pattern.match(lines[index]):
            start = index + 1
            end = start
            while end < len(lines) and not re.match(
                r"^\s*(except|finally)\b", lines[end]
            ):
                end += 1
            tree_try_lines.append((start + 1, end))
            index = end
            continue
        index += 1
    return any(start <= line <= end for start, end in tree_try_lines)


def _heuristic_scan(code: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lines = code.splitlines()

    for label, pattern in NETWORK_PATTERNS:
        for match in pattern.finditer(code):
            line = _line_of(code, match)
            context = _snippet(code, line)
            has_timeout = "timeout" in context
            if not has_timeout:
                window = "\n".join(lines[max(0, line - 2) : min(len(lines), line + 2)])
                has_timeout = "timeout" in window
            if not has_timeout:
                findings.append(
                    {
                        "category": "network_timeout",
                        "line": line,
                        "snippet": context,
                        "severity": "high",
                        "message": f"{label}() is called without an explicit timeout.",
                        "suggestion": (
                            "Pass timeout=5 (or similar) and handle "
                            "TimeoutError/ConnectionError explicitly."
                        ),
                    }
                )
            if not _has_try_covering_line(code, line):
                findings.append(
                    {
                        "category": "missing_try_catch",
                        "line": line,
                        "snippet": context,
                        "severity": "medium",
                        "message": f"{label}() is not wrapped in try/except.",
                        "suggestion": (
                            "Wrap the call in try/except and define a fallback "
                            "value so downstream nodes never see a raw exception."
                        ),
                    }
                )

    for label, pattern in UNGUARDED_PARSE_PATTERNS:
        for match in pattern.finditer(code):
            line = _line_of(code, match)
            if not _has_try_covering_line(code, line):
                findings.append(
                    {
                        "category": "missing_try_catch",
                        "line": line,
                        "snippet": _snippet(code, line),
                        "severity": "medium",
                        "message": f"{label} can raise on malformed payloads but is unguarded.",
                        "suggestion": "Catch ValueError/JSONDecodeError and return a default.",
                    }
                )

    for match in BARE_EXCEPT_PATTERN.finditer(code):
        findings.append(
            {
                "category": "missing_try_catch",
                "line": _line_of(code, match),
                "snippet": _snippet(code, _line_of(code, match)),
                "severity": "medium",
                "message": "Bare `except:` swallows KeyboardInterrupt and SystemExit too.",
                "suggestion": "Catch a specific exception class instead.",
            }
        )

    for match in EVAL_PATTERN.finditer(code):
        findings.append(
            {
                "category": "missing_try_catch",
                "line": _line_of(code, match),
                "snippet": _snippet(code, _line_of(code, match)),
                "severity": "high",
                "message": f"{match.group(1)}() executes dynamic code and can crash on bad input.",
                "suggestion": "Replace with explicit parsing/validation.",
            }
        )

    for match in OPEN_WITHOUT_WITH_PATTERN.finditer(code):
        findings.append(
            {
                "category": "missing_try_catch",
                "line": _line_of(code, match),
                "snippet": _snippet(code, _line_of(code, match)),
                "severity": "low",
                "message": "File handle assigned without a `with` block may leak on error.",
                "suggestion": "Use `with open(...) as handle:` instead.",
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for finding in findings:
        key = (finding["category"], finding["line"], finding["message"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)

    deduped.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 3), item["line"]))
    return deduped

def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    fence_match = JSON_FENCE_PATTERN.search(text)
    candidate = fence_match.group(1) if fence_match else text.strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : index + 1])
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _normalise_findings(raw_findings: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not isinstance(raw_findings, list):
        return findings
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        category = str(item.get("category", "missing_try_catch"))
        if category not in {"missing_try_catch", "network_timeout", "null_dereference"}:
            category = "missing_try_catch"
        line = item.get("line")
        findings.append(
            {
                "category": category,
                "line": int(line) if isinstance(line, (int, float)) else None,
                "snippet": str(item.get("snippet", ""))[:200],
                "severity": severity,
                "message": str(item.get("message", "Potential runtime fault."))[:400],
                "suggestion": str(item.get("suggestion", ""))[:400],
            }
        )
    findings.sort(
        key=lambda item: (SEVERITY_ORDER.get(item["severity"], 3), item["line"] or 0)
    )
    return findings


def _extract_architecture_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = ARCHITECTURE_JSON_FENCE_PATTERN.search(text)
    if match:
        candidate = match.group(1)
    else:
        candidate = text
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _normalise_architecture(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return [], []
    components_raw = raw.get("components") or raw.get("nodes") or []
    connections_raw = raw.get("connections") or raw.get("edges") or []
    components: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    valid_tags = {"service", "database", "api", "function"}
    valid_kinds = {"call", "data_flow", "api_request"}

    if isinstance(components_raw, list):
        for item in components_raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("label") or item.get("id")
            if not name:
                continue
            tag = item.get("tag") or item.get("type")
            if tag not in valid_tags:
                tag = "function"
            components.append(
                {
                    "name": str(name).strip(),
                    "tag": str(tag),
                    "code": _clip(str(item.get("role") or item.get("code") or "")[:400], 400),
                    "line": None,
                    "end_line": None,
                    "source": "ai",
                }
            )

    if isinstance(connections_raw, list):
        for item in connections_raw:
            if not isinstance(item, dict):
                continue
            source = item.get("source") or item.get("from")
            target = item.get("target") or item.get("to")
            if not source or not target:
                continue
            kind = item.get("kind") or item.get("type") or "call"
            if kind not in valid_kinds:
                kind = "call"
            connection: dict[str, Any] = {
                "source": str(source).strip(),
                "target": str(target).strip(),
                "kind": str(kind),
            }
            label = item.get("label")
            if label:
                connection["label"] = str(label)[:32]
            connections.append(connection)

    return components, connections


def _resolve_ai_credentials(api_key: str | None = None):
    resolved_key = (
        (api_key or "").strip()
        or (getattr(config, "DEEPSEEK_API_KEY", None) or "").strip()
        or (getattr(config, "AI_API_KEY", None) or "").strip()
        or (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        or (os.getenv("AXIOM_AI_API_KEY") or "").strip()
    )
    base_url = (
        os.getenv("DEEPSEEK_BASE_URL")
        or getattr(config, "DEEPSEEK_BASE_URL", None)
        or getattr(config, "AI_BASE_URL", None)
        or "https://api.deepseek.com/v1"
    )
    model = (
        os.getenv("DEEPSEEK_MODEL")
        or getattr(config, "DEEPSEEK_MODEL", None)
        or getattr(config, "AI_MODEL", None)
        or "deepseek-chat"
    )
    return resolved_key, base_url, model


def _call_architecture_model(
    text: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from openai import OpenAI
    except ImportError:
        return None, "openai package is not installed (pip install openai)."

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": ARCHITECTURE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Map this design or source to components and connections. "
                        "Return the JSON object only:\n\n"
                        "```text\n"
                        f"{text[:12000]}\n"
                        "```"
                    ),
                },
            ],
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the caller
        return None, f"AI request failed: {type(error).__name__}: {error}"

    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return None, "AI endpoint response is missing choices[0].message.content."

    parsed = _extract_architecture_json(str(content))
    if parsed is None:
        return None, "Could not parse the model's architecture JSON."
    parsed["__raw_text"] = str(content)[:4000]
    return parsed, None


def build_architecture_graph(
    text_prompt_or_code: str,
    current_graph: Any = None,
    prompt: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Build a stitched architecture graph with optional LLM enrichment.

    Uses the deterministic extractor as the base, optionally merges a prompt,
    and enriches via the configured OpenAI-compatible endpoint when an API key
    is present and :data:`config.AI_ARCHITECTURE_ENRICHMENT` is True.
    """
    from services import ast_service

    text = text_prompt_or_code if isinstance(text_prompt_or_code, str) else ""
    merged_prompt = prompt if isinstance(prompt, str) and prompt.strip() else ""
    combined = text
    if merged_prompt and merged_prompt.strip() not in combined:
        combined = f"{combined}\n\n{merged_prompt}".strip() if combined.strip() else merged_prompt

    resolved_key, base_url, model = _resolve_ai_credentials(api_key)
    api_key_present = bool(resolved_key)
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        graph = ast_service.build_architecture_graph(combined or text, current_graph=current_graph)
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"Architecture extraction failed: {error}") from error

    if not api_key_present or not getattr(config, "AI_ARCHITECTURE_ENRICHMENT", True):
        graph = dict(graph)
        graph["api_key_present"] = api_key_present
        graph["ai_source"] = None
        graph["ai_fallback_reason"] = "no_api_key" if not api_key_present else "enrichment_disabled"
        graph["generated_at"] = generated_at
        return graph

    parsed, error = _call_architecture_model(
        combined or text,
        resolved_key,
        base_url,
        model,
        config.AI_TIMEOUT_SECONDS,
    )
    if parsed is None:
        logger.warning("Architecture LLM enrichment unavailable, using heuristic: %s", error)
        graph = dict(graph)
        graph["api_key_present"] = api_key_present
        graph["ai_source"] = None
        graph["ai_fallback_reason"] = error
        graph["generated_at"] = generated_at
        return graph

    comp, conn = _normalise_architecture(parsed)
    try:
        enriched = ast_service.extend_architecture_graph(graph, comp, conn, source="ai")
    except Exception as merge_error:  # noqa: BLE001
        logger.warning("Architecture LLM merge failed, using heuristic: %s", merge_error)
        graph = dict(graph)
        graph["api_key_present"] = api_key_present
        graph["ai_source"] = None
        graph["ai_fallback_reason"] = f"merge_failed: {merge_error}"
        graph["generated_at"] = generated_at
        return graph

    enriched = dict(enriched)
    enriched["api_key_present"] = api_key_present
    enriched["ai_source"] = model
    enriched["ai_fallback_reason"] = None
    enriched["generated_at"] = generated_at
    return enriched


def _merge_findings(
    heuristic: list[dict[str, Any]], model: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union model findings with heuristic ones, de-duplicating by line+category."""
    merged: list[dict[str, Any]] = list(model)
    seen = {(item.get("category"), item.get("line")) for item in model}
    for item in heuristic:
        key = (item.get("category"), item.get("line"))
        if key in seen:
            continue
        merged.append(item)
    merged.sort(
        key=lambda entry: (SEVERITY_ORDER.get(entry["severity"], 3), entry["line"] or 0)
    )
    return merged


# ----------------------------------------------------------------------
# LLM-backed detection (OpenAI SDK)
# ----------------------------------------------------------------------

def _call_chat_model(
    code: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call an OpenAI-compatible chat endpoint via the official SDK."""
    try:
        from openai import OpenAI
    except ImportError:
        return None, "openai package is not installed (pip install openai)."

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyse this source and reply with the JSON object only:\n\n"
                        "```source\n"
                        f"{code[:12000]}\n"
                        "```"
                    ),
                },
            ],
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the caller
        return None, f"AI request failed: {type(error).__name__}: {error}"

    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return None, "AI endpoint response is missing choices[0].message.content."

    parsed = _extract_json_object(str(content))
    if parsed is None:
        return None, "Could not parse the model's findings JSON."
    parsed["__raw_text"] = str(content)[:4000]
    return parsed, None


def predict_edge_cases(code_str: str, api_key: str | None = None) -> dict[str, Any]:
    """Detect missing try/except blocks and network timeout risks.

    Uses the configured OpenAI-compatible endpoint when an API key is
    available, otherwise falls back to a deterministic offline heuristic so
    the feature always returns actionable output.
    """
    code = code_str if isinstance(code_str, str) else ""
    resolved_key, base_url, model = _resolve_ai_credentials(api_key)
    generated_at = datetime.now(timezone.utc).isoformat()

    if not code.strip():
        return {
            "source": "heuristic",
            "model": None,
            "api_key_present": bool(resolved_key),
            "findings": [],
            "summary": "No source supplied for edge-case analysis.",
            "fallback_reason": None,
            "generated_at": generated_at,
            "raw_response": None,
        }

    heuristic_findings = _heuristic_scan(code)

    if not resolved_key:
        return {
            "source": "heuristic",
            "model": None,
            "api_key_present": False,
            "findings": heuristic_findings,
            "summary": (
                f"Heuristic scan found {len(heuristic_findings)} risk(s). "
                "Add your DeepSeek API key in AI Settings for model-assisted analysis."
            ),
            "fallback_reason": "no_api_key",
            "generated_at": generated_at,
            "raw_response": None,
        }

    parsed, error = _call_chat_model(
        code,
        resolved_key,
        base_url,
        model,
        config.AI_TIMEOUT_SECONDS,
    )

    if parsed is not None:
        findings = _normalise_findings(parsed.get("findings"))
        merged = _merge_findings(heuristic_findings, findings)
        return {
            "source": model,
            "model": model,
            "api_key_present": True,
            "findings": merged,
            "summary": str(parsed.get("summary") or "Model analysis complete.")[:400],
            "fallback_reason": None,
            "generated_at": generated_at,
            "raw_response": parsed.get("__raw_text"),
        }

    logger.warning("LLM analysis unavailable, using heuristic: %s", error)
    return {
        "source": "heuristic",
        "model": model,
        "api_key_present": True,
        "findings": heuristic_findings,
        "summary": (
            f"Model analysis unavailable ({error}); heuristic scan found "
            f"{len(heuristic_findings)} risk(s)."
        ),
        "fallback_reason": error,
        "generated_at": generated_at,
        "raw_response": None,
    }


# ----------------------------------------------------------------------
# Service facade
# ----------------------------------------------------------------------

class AIService:
    """Stateless domain service exposing AI context + edge-case analysis."""

    def generate_lasso_context(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        project_name: str | None = None,
    ) -> str:
        """Render a lasso selection as a token-efficient Markdown brief."""
        return build_lasso_context_dump(nodes, edges, project_name=project_name)

    def generate_lasso_context_report(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Render a lasso selection and report its size statistics."""
        return build_lasso_context_report(nodes, edges, project_name=project_name)

    def predict_edge_cases(self, code_str: str, api_key: str | None = None) -> dict[str, Any]:
        """Detect runtime risks via LLM with deterministic heuristic fallback."""
        return predict_edge_cases(code_str, api_key=api_key)

    def build_architecture_graph(
        self,
        text_prompt_or_code: str,
        current_graph: Any = None,
        prompt: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Map prompt/code to a stitched architecture graph with LLM enrichment."""
        return build_architecture_graph(
            text_prompt_or_code,
            current_graph=current_graph,
            prompt=prompt,
            api_key=api_key,
        )


__all__ = [
    "AIService",
    "build_architecture_graph",
    "build_lasso_context_dump",
    "build_lasso_context_report",
    "estimate_tokens",
    "predict_edge_cases",
    "LLM_SYSTEM_PROMPT",
    "ARCHITECTURE_SYSTEM_PROMPT",
]
