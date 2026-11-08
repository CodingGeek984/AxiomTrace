"""Graceful node source resolution shared by every execution surface.

A node can reach the sandbox, the mutation corpus or the transpiler without
its source text attached - scanned project nodes historically carried an empty
``code`` string, a hand-built graph node may only have a label, and an older
persisted workspace may only have a file path. Nothing about those shapes is a
server error, so this module turns *any* of them into executable source:

1. the bound source (``code`` / ``source``) when it contains real text,
2. an explicit ``fallback_code`` snippet supplied by the caller,
3. a generated, deterministic stand-in derived from the node label, file path
   and module name.

The generated snippet is the canonical safe stub (``# No source provided`` /
``def fallback_node(): return True``) extended with the node identity and a
``main(payload)`` entry point, so the sandbox can execute it, the mutation
corpus can stress it and the transpiler can convert it without ever raising
``ValueError: Node source is empty`` or ``SyntaxError: Invalid syntax``.

This module is also the single source of truth for *language matching*: the
sandbox runner is Python-only, so callers use :func:`normalise_language`,
:func:`language_for_path` and :func:`is_python_language` to decide whether a
node's bound text may be executed as-is or must be replaced by the safe stub
instead of being fed to a Python ``exec`` loop.
"""

from __future__ import annotations

import posixpath
from typing import Any

__all__ = [
    "LANGUAGE_ALIASES",
    "LANGUAGE_BY_EXTENSION",
    "MAX_FALLBACK_FIELD",
    "build_fallback_source",
    "is_python_language",
    "language_for_path",
    "module_name_for",
    "normalise_language",
    "resolve_node_source",
]

# Longest label/file/module fragment embedded in a generated snippet. Keeps a
# pathological node name from ballooning the payload while staying readable.
MAX_FALLBACK_FIELD = 160

# Canonical safe stub mandated by the repair directive: this exact header is
# always the first thing a generated fallback contains, so any consumer can
# recognise a fallback run by inspection.
CANONICAL_STUB = "# No source provided\ndef fallback_node(): return True\n"

_GENERATED_NOTE = (
    "# AxiomTrace generated fallback - no source text was bound to this node.\n"
    "# The snippet below mirrors the node's identity so execution, mutation\n"
    "# stress testing and transpilation all keep working end to end.\n"
)

# Common spellings -> canonical language id used across the backend.
LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "python2": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "cs": "csharp",
    "c#": "csharp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "golang": "go",
    "rb": "ruby",
    "kt": "kotlin",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "yml": "yaml",
    "md": "markdown",
    "plaintext": "text",
    "": "",
}

# File extension -> canonical language id (anything unlisted returns "").
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".php": "php",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
    ".vue": "html",
    ".svelte": "html",
    ".astro": "html",
}

# Languages the Python sandbox may execute directly. The empty string covers
# legacy payloads that never declared a language (they default to Python, the
# backend's own implementation language).
_PYTHON_LANGUAGES = frozenset({"", "python", "text", "markdown", "unknown"})


def normalise_language(value: Any) -> str:
    """Canonicalise a language identifier (``"PY"`` -> ``"python"``).

    Returns ``""`` for missing/blank values; unknown ids are lower-cased and
    preserved so callers can still distinguish e.g. ``java`` from ``python``.
    """
    if value is None:
        return ""
    try:
        text = str(value).strip().lower()
    except Exception:  # noqa: BLE001 - never let formatting break a request
        return ""
    if not text:
        return ""
    return LANGUAGE_ALIASES.get(text, text)


def language_for_path(file_path: Any) -> str:
    """Derive a canonical language id from a file path (``""`` if unknown)."""
    if file_path is None:
        return ""
    try:
        text = str(file_path).replace("\\", "/").strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    if not text:
        return ""
    return LANGUAGE_BY_EXTENSION.get(posixpath.splitext(text)[1], "")


def is_python_language(language: Any) -> bool:
    """True when the sandbox's Python runner may execute this language.

    Unset/unknown declarations default to Python so legacy payloads keep
    working; every other declared language (``java``, ``javascript``, ...)
    must be replaced with the safe stub rather than fed to ``exec``.
    """
    return normalise_language(language) in _PYTHON_LANGUAGES


def _clean(value: Any, limit: int = MAX_FALLBACK_FIELD) -> str:
    """Coerce an arbitrary identity field into a safe single-line string."""
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - never let formatting break a request
        return ""
    text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "..."
    return text


def module_name_for(file_path: Any = None, label: Any = None) -> str:
    """Derive a dotted module name from a file path (or the node label).

    ``backend/services/user_service.py`` -> ``backend.services.user_service``.
    Package initialisers collapse to their directory name, and extension-less
    labels pass through unchanged.
    """
    raw = _clean(file_path) or _clean(label)
    if not raw:
        return ""
    normalised = raw.replace("\\", "/").strip("/")
    parts = [part for part in normalised.split("/") if part and part != "."]
    if not parts:
        return ""
    last = parts[-1]
    if "." in last:
        last = last.rsplit(".", 1)[0]
    if last in {"__init__", "index", "mod"} and len(parts) > 1:
        parts = parts[:-1]
    else:
        parts = [*parts[:-1], last]
    dotted = ".".join(part for part in parts if part)
    return _clean(dotted, limit=MAX_FALLBACK_FIELD * 2)


def build_fallback_source(
    label: Any = None,
    file_path: Any = None,
    module: Any = None,
    language: Any = None,
) -> str:
    """Build the canonical safe stub for a node with no runnable source.

    Always starts with ``# No source provided`` / ``def fallback_node():
    return True`` (the directive's safe fallback), then carries the node
    identity plus a ``main(payload)`` entry point - the sandbox's first-choice
    entry - so execution, mutation stress testing and transpilation all keep
    working and the inspector output makes it obvious a fallback ran.
    """
    clean_label = _clean(label) or _clean(file_path) or "unbound-node"
    clean_file = _clean(file_path)
    clean_module = _clean(module) or module_name_for(file_path, label) or clean_label
    clean_language = normalise_language(language) or normalise_language(
        language_for_path(file_path)
    )

    return (
        f"{CANONICAL_STUB}"
        f"{_GENERATED_NOTE}"
        f"NODE_LABEL = {clean_label!r}\n"
        f"NODE_FILE = {clean_file!r}\n"
        f"NODE_MODULE = {clean_module!r}\n"
        f"NODE_LANGUAGE = {clean_language!r}\n"
        "\n"
        "def main(payload=None):\n"
        '    """Generated entry point for a node without bound source text."""\n'
        "    return {\n"
        '        "node": NODE_LABEL,\n'
        '        "file": NODE_FILE,\n'
        '        "module": NODE_MODULE,\n'
        '        "language": NODE_LANGUAGE,\n'
        '        "payload": payload,\n'
        '        "fallback": True,\n'
        "    }\n"
    )


def resolve_node_source(
    code: Any,
    *,
    label: Any = None,
    file_path: Any = None,
    module: Any = None,
    fallback: Any = None,
    language: Any = None,
) -> tuple[str, str]:
    """Resolve executable source for a node plus the origin of the text.

    Returns ``(source, origin)`` where ``origin`` is one of:

    ``bound``
        The caller supplied real source text.
    ``explicit-fallback``
        The caller supplied an explicit fallback snippet.
    ``generated``
        Source was synthesised from the node label / file path / module.

    The result is never empty: when nothing at all is known about the node a
    generic ``unbound-node`` stub is produced instead of raising. The
    generated stub embeds ``language`` (declared or derived from the file
    path) as ``NODE_LANGUAGE``.
    """
    # A whole node/data dictionary is accepted too, so callers can hand over
    # the payload they already have instead of unpacking it first.
    if isinstance(code, dict):
        node = code
        label = label if label is not None else node.get("label")
        file_path = file_path if file_path is not None else (
            node.get("file") or node.get("path")
        )
        module = module if module is not None else node.get("module")
        fallback = fallback if fallback is not None else node.get("fallback_code")
        language = language if language is not None else node.get("language")
        code = next(
            (
                node.get(alias)
                for alias in ("code", "source", "code_snippet", "body", "content")
                if isinstance(node.get(alias), str) and node.get(alias).strip()
            ),
            "",
        )

    if isinstance(code, str) and code.strip():
        return code, "bound"
    if isinstance(fallback, str) and fallback.strip():
        return fallback, "explicit-fallback"

    return (
        build_fallback_source(
            label=label,
            file_path=file_path,
            module=module,
            language=language,
        ),
        "generated",
    )
