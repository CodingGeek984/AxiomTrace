"""Polyglot code transpilation for AxiomTrace graph nodes.

The service keeps a stable facade for the HTTP controller while delegating the
actual translation to the deterministic engine in :mod:`services.transpiler`.
Python source is parsed with :func:`prepare_source`, which understands complete
modules as well as indented fragments such as ``return total`` or an isolated
``if`` block. Every target language (Python, JavaScript, TypeScript, C++,
C#, Java, Rust, Go and PHP) has a dedicated emitter.

When the deterministic engine cannot process a snippet (for example because it
is not valid Python, or because the source language is not Python), the service
optionally asks the configured OpenAI-compatible model. If that is unavailable,
the caller still receives HTTP 200 with the original snippet preserved in a
comment block and an explicit list of warnings - never a server error.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from services.transpiler.base import TranspileError, prepare_source
from services.transpiler.python_emitter import emit_source
from utils.logger import get_logger

logger = get_logger("transpiler")

SUPPORTED_LANGUAGES = (
    "python",
    "javascript",
    "typescript",
    "cpp",
    "csharp",
    "java",
    "rust",
    "go",
    "php",
)
DEFAULT_TARGET = "typescript"

LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "python2": "python",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "ts": "typescript",
    "c++": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "c": "cpp",
    "c#": "csharp",
    "cs": "csharp",
    "dotnet": "csharp",
    "golang": "go",
    "rs": "rust",
}

_LINE_COMMENT = {
    "python": "#",
    "javascript": "//",
    "typescript": "//",
    "cpp": "//",
    "csharp": "//",
    "java": "//",
    "rust": "//",
    "go": "//",
    "php": "//",
}


@dataclass(frozen=True)
class TranspileResult:
    """Outcome of a single node transpilation."""

    source_language: str
    target_language: str
    source_code: str
    translated_code: str
    confidence: float
    warnings: tuple[str, ...]
    used_ai_fallback: bool
    fallback_reason: str | None = None


@dataclass(frozen=True)
class TranspileContext:
    """Optional context to improve translation quality."""

    imports: tuple[str, ...] = ()
    type_hints: Mapping[str, str] = ()
    surrounding_scope: str = ""


class TranspilationError(ValueError):
    """Raised when a request cannot be transpiled at all (invalid input)."""


def normalize_language(value: str | None, *, default: str | None = None) -> str | None:
    """Return the canonical language id for ``value`` (aliases accepted)."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in SUPPORTED_LANGUAGES:
        return raw
    return LANGUAGE_ALIASES.get(raw, default)


def _strip_common_indent(code: str) -> str:
    lines = code.expandtabs(4).splitlines()
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return code
    indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    if indent == 0:
        return code
    return "\n".join(line[indent:] if line.startswith(" " * indent) else line for line in lines)


# ---------------------------------------------------------------------------
# LLM engine (DeepSeek / OpenAI-compatible)
# ---------------------------------------------------------------------------

def _resolve_llm_credentials(api_key: str | None) -> tuple[str, str, str]:
    from services.ai_service import _resolve_ai_credentials

    return _resolve_ai_credentials(api_key)


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    fence = re.match(r"^```(?:\w+)?\s*\n(.*?)\n```$", stripped, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    if stripped.startswith("```"):
        stripped = stripped[3:].lstrip("\n")
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
        first_line, separator, rest = stripped.partition("\n")
        if separator and re.fullmatch(r"[A-Za-z0-9+#_-]+", first_line.strip()):
            stripped = rest
    return stripped.strip()


def _llm_translate(
    code: str,
    source_language: str,
    target_language: str,
    api_key: str | None,
) -> tuple[str | None, str | None]:
    """Translate through the configured chat model. Returns ``(code, error)``."""
    resolved_key, base_url, model = _resolve_llm_credentials(api_key)
    if not resolved_key:
        return None, "no_api_key"
    try:
        from openai import OpenAI
    except ImportError:
        return None, "openai_package_missing"

    system_prompt = (
        "You are a principal compiler engineer specialised in cross-language translation. "
        f"Translate the {source_language} snippet below into idiomatic, production-grade "
        f"{target_language}. Rules:\n"
        "- Preserve identifiers, semantics and control flow.\n"
        "- Use the target language's native types, containers and idioms "
        "(e.g. std::vector/HashMap for C++, Vec<>/HashMap for Rust, ArrayList/Map for Java).\n"
        "- Keep the code complete and self-contained; do not leave placeholders, TODOs or ellipses.\n"
        "- Output ONLY the translated code. No markdown fences, no explanations."
    )
    try:
        client = OpenAI(api_key=resolved_key, base_url=base_url, timeout=45)
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"```{source_language}\n{code}\n```"},
            ],
        )
        content = response.choices[0].message.content or ""
    except Exception as error:  # noqa: BLE001 - reported through warnings
        logger.warning("LLM transpile failed: %s", error)
        if "401" in str(error) or "authentication" in str(error).lower():
            return None, "invalid_api_key"
        return None, f"llm_error: {type(error).__name__}"

    translated = _strip_code_fences(str(content))
    if not translated.strip():
        return None, "llm_empty_response"
    return translated, None


# ---------------------------------------------------------------------------
# Deterministic fallbacks
# ---------------------------------------------------------------------------

def _comment_block(code: str, target_language: str) -> list[str]:
    comment = _LINE_COMMENT.get(target_language, "//")
    return [f"{comment} {line}" if line.strip() else comment for line in code.splitlines()]


def _fallback_snippet(
    code: str,
    source_language: str,
    target_language: str,
    reason: str | None,
) -> str:
    comment = _LINE_COMMENT.get(target_language, "//")
    reason_text = (reason or "the snippet could not be parsed as Python").strip().rstrip(".")
    header_lines = [
        f"{comment} [AxiomTrace] Automatic translation to {target_language} was not possible.",
        f"{comment} Reason: {reason_text}.",
        f"{comment} The original {source_language} snippet is preserved below for manual porting.",
        "",
    ]
    return "\n".join(header_lines + _comment_block(code, target_language))


def _fallback_message(reason: str | None) -> str:
    if reason == "no_api_key":
        return (
            "No API key configured - add a DeepSeek key in AI Settings for model-assisted "
            "translation, or fix the Python syntax so the deterministic engine can run."
        )
    if reason == "invalid_api_key":
        return "The DeepSeek API rejected the configured key. Check it in AI Settings."
    if reason == "openai_package_missing":
        return "The OpenAI Python package is not installed; model-assisted translation is unavailable."
    if reason and reason.startswith("syntax:"):
        return f"Python syntax error: {reason.split(':', 1)[1].strip()}."
    return f"Automatic translation was unavailable ({reason or 'unknown reason'})."


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class TranspilerService:
    """Facade for node-level cross-language transpilation."""

    def transpile_node(
        self,
        source_code: str,
        source_language: str = "python",
        target_language: str = DEFAULT_TARGET,
        context: TranspileContext | None = None,
        api_key: str | None = None,
    ) -> TranspileResult:
        source = normalize_language(source_language, default="python")
        target = normalize_language(target_language, default=DEFAULT_TARGET)

        if source not in SUPPORTED_LANGUAGES:
            raise TranspilationError(
                f"Unsupported source language: {source_language}. "
                f"Supported: {', '.join(SUPPORTED_LANGUAGES)}"
            )
        if target not in SUPPORTED_LANGUAGES:
            raise TranspilationError(
                f"Unsupported target language: {target_language}. "
                f"Supported: {', '.join(SUPPORTED_LANGUAGES)}"
            )
        if not isinstance(source_code, str) or not source_code.strip():
            raise TranspilationError("No source code supplied to the transpiler.")
        if "\x00" in source_code:
            raise TranspilationError("Source code contains a NUL byte and cannot be parsed.")

        if source == target:
            return TranspileResult(
                source_language=source,
                target_language=target,
                source_code=source_code,
                translated_code=source_code,
                confidence=1.0,
                warnings=(),
                used_ai_fallback=False,
            )

        clean_code = _strip_common_indent(source_code)
        warnings: list[str] = []
        failure_reason: str | None = None

        # ------------------------------------------------------------------
        # Deterministic engine (Python source only)
        # ------------------------------------------------------------------
        if source == "python":
            try:
                prepared = prepare_source(clean_code, language=source)
                translated, engine_warnings = emit_source(
                    prepared,
                    target,
                    label="axiom_node",
                )
                warnings.extend(engine_warnings)
                if not translated.strip():
                    raise TranspileError("The deterministic engine produced no output.", status=422)
                confidence = 0.92 if not warnings else max(0.65, 0.92 - 0.04 * min(len(warnings), 6))
                return TranspileResult(
                    source_language=source,
                    target_language=target,
                    source_code=source_code,
                    translated_code=translated.strip(),
                    confidence=round(confidence, 2),
                    warnings=tuple(warnings),
                    used_ai_fallback=False,
                )
            except TranspileError as error:
                failure_reason = f"syntax: {error.message}"
                logger.info("Deterministic transpile rejected source: %s", error.message)
            except Exception as error:  # noqa: BLE001 - fall through to the fallback path
                failure_reason = f"engine_error: {type(error).__name__}: {error}"
                logger.exception("Deterministic transpile failed")

        # ------------------------------------------------------------------
        # Model-assisted path
        # ------------------------------------------------------------------
        llm_code, llm_error = _llm_translate(clean_code, source, target, api_key)
        if llm_code:
            if failure_reason:
                warnings.append(
                    "The deterministic engine could not translate this snippet; "
                    "the model-assisted engine produced the output."
                )
            return TranspileResult(
                source_language=source,
                target_language=target,
                source_code=source_code,
                translated_code=llm_code.strip(),
                confidence=0.85,
                warnings=tuple(warnings),
                used_ai_fallback=True,
            )

        # ------------------------------------------------------------------
        # Clean, non-crashing fallback
        # ------------------------------------------------------------------
        reason = failure_reason or llm_error or (
            "the deterministic engine supports Python source only"
            if source != "python"
            else "unsupported source"
        )
        if llm_error and llm_error != "no_api_key":
            warnings.append(f"Model-assisted translation failed: {llm_error}.")
        warnings.append(_fallback_message(reason))
        fallback = _fallback_snippet(clean_code, source, target, reason)
        return TranspileResult(
            source_language=source,
            target_language=target,
            source_code=source_code,
            translated_code=fallback,
            confidence=0.25,
            warnings=tuple(warnings),
            used_ai_fallback=True,
            fallback_reason=reason,
        )

    def fallback_result(
        self,
        source_code: str,
        target_language: str = DEFAULT_TARGET,
        *,
        reason: str = "internal transpiler error",
    ) -> TranspileResult:
        """Build a safe fallback result for unexpected controller failures."""
        target = normalize_language(target_language, default=DEFAULT_TARGET) or DEFAULT_TARGET
        source = normalize_language("python", default="python") or "python"
        fallback = _fallback_snippet(source_code, source, target, reason)
        warnings = (
            "Automatic translation failed unexpectedly; the original snippet is preserved below.",
            _fallback_message(reason),
        )
        return TranspileResult(
            source_language=source,
            target_language=target,
            source_code=source_code,
            translated_code=fallback,
            confidence=0.2,
            warnings=warnings,
            used_ai_fallback=True,
            fallback_reason=reason,
        )


__all__ = [
    "DEFAULT_TARGET",
    "LANGUAGE_ALIASES",
    "SUPPORTED_LANGUAGES",
    "TranspileContext",
    "TranspileResult",
    "TranspilationError",
    "TranspilerService",
    "normalize_language",
]
