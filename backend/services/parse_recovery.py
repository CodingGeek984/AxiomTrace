"""Graceful recovery for partially written source code.

AxiomTrace is an editor: the user is almost always looking at code that is
*mid-edit*. Rejecting the whole document because of one broken statement
wipes the canvas and destroys the feedback loop.

This module rebuilds a parsable version of a Python source by quarantining
only the statements that actually fail to compile, then reports the
quarantined regions so the UI can surface them as diagnostics.

Guarantees:

* The recovered source has exactly the same number of lines as the input,
  so every ``lineno`` in the resulting AST still points at the original
  text. Node code snippets therefore stay correct.
* Recovery never invents or removes code - a broken statement is replaced
  by whitespace, never by a different statement.
* The number of parse attempts is bounded, so a pathological document can
  never turn a request into an unbounded CPU burn.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

# Maximum number of quarantine rounds. Each round removes at least one
# line, and real-world typos converge in one or two rounds.
MAX_RECOVERY_ATTEMPTS = 24

# Source larger than this is not worth salvaging: the O(attempts * lines)
# cost stops being worth it and the user is most likely pasting a huge
# file rather than editing it.
MAX_RECOVERY_BYTES = 256 * 1024

_STRING_OR_COMMENT = re.compile(
    r"(\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|#[^\n]*)",
    re.DOTALL,
)

_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")", "]", "}"}


@dataclass
class RecoveryRegion:
    """A quarantined region of the original source."""

    start_line: int
    end_line: int
    message: str
    kind: str = "syntax_error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "message": self.message,
            "kind": self.kind,
        }


@dataclass
class RecoveryResult:
    """Outcome of a recovery attempt."""

    code: str
    regions: list[RecoveryRegion] = field(default_factory=list)
    recovered: bool = False
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "attempts": self.attempts,
            "regions": [region.to_dict() for region in self.regions],
        }


def _strip_literals(text: str) -> str:
    """Blank out string literals and comments so brace counting is reliable."""
    return _STRING_OR_COMMENT.sub(lambda match: " " * len(match.group(0)), text)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _bracket_delta(line: str) -> int:
    """Net bracket depth contributed by a single line."""
    delta = 0
    for char in _strip_literals(line):
        if char in _OPENERS:
            delta += 1
        elif char in _CLOSERS:
            delta -= 1
    return delta


def _statement_extent(lines: list[str], index: int) -> tuple[int, int]:
    """Return the ``(start, end)`` line range of the statement at ``index``.

    The range covers an unbalanced bracket construct or the indented body
    that belongs to the statement, whichever applies.
    """
    depth = _bracket_delta(lines[index])
    if depth > 0:
        end = index
        running = depth
        while end + 1 < len(lines):
            running += _bracket_delta(lines[end + 1])
            end += 1
            if running <= 0:
                break
        return index, end

    indent = _indent_of(lines[index])
    end = index
    while end + 1 < len(lines):
        following = lines[end + 1]
        if not following.strip():
            end += 1
            continue
        if _indent_of(following) > indent:
            end += 1
            continue
        break
    return index, end


_REFERENCED_LINE = re.compile(r"on line (\d+)\s*$")


def _referenced_line(message: str) -> int | None:
    """Extract the line a CPython error message points back to.

    ``expected an indented block after 'if' statement on line 4`` reports the
    *header* line, which is exactly the statement that has to be quarantined
    when the reported position is the (already blanked) body line.
    """
    match = _REFERENCED_LINE.search(message or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _target_range(lines: list[str], error: SyntaxError) -> tuple[int, int]:
    """Return the line range that should be quarantined for ``error``."""
    message = error.msg or ""
    lineno = error.lineno or 1

    if "expected an indented block" in message:
        referenced = _referenced_line(message)
        if referenced and 1 <= referenced <= len(lines):
            index = referenced - 1
            return index, index

    index = max(0, min(lineno - 1, len(lines) - 1))
    start, end = _statement_extent(lines, index)
    return max(0, start), min(len(lines) - 1, end)


def _build_masked(lines: list[str], blanked: set[int]) -> str:
    """Return the source with the quarantined lines replaced by whitespace."""
    output: list[str] = []
    for position, line in enumerate(lines):
        if position in blanked:
            output.append(" " * len(line))
        else:
            output.append(line)
    return "\n".join(output)


def _merge_regions(regions: list[RecoveryRegion]) -> list[RecoveryRegion]:
    """Collapse overlapping/adjacent regions so the UI shows one marker."""
    if not regions:
        return []
    ordered = sorted(regions, key=lambda region: (region.start_line, region.end_line))
    merged = [ordered[0]]
    for region in ordered[1:]:
        last = merged[-1]
        if region.start_line <= last.end_line + 1:
            last.end_line = max(last.end_line, region.end_line)
            if region.message and region.message not in last.message:
                last.message = f"{last.message}; {region.message}"
        else:
            merged.append(region)
    return merged


def recover_python(source: str) -> RecoveryResult:
    """Return a parsable variant of ``source`` plus the quarantined regions.

    Args:
        source: The original Python source.

    Returns:
        A :class:`RecoveryResult`. ``recovered`` is ``False`` when the source
        could not be salvaged, in which case ``code`` is the original input
        and the caller should surface a hard error instead.
    """
    if not source.strip():
        return RecoveryResult(code=source, recovered=True)

    if len(source.encode("utf-8", errors="ignore")) > MAX_RECOVERY_BYTES:
        return RecoveryResult(code=source, recovered=False)

    lines = source.splitlines()
    if not lines:
        return RecoveryResult(code=source, recovered=True)

    blanked: set[int] = set()
    regions: list[RecoveryRegion] = []
    attempts = 0

    while attempts < MAX_RECOVERY_ATTEMPTS:
        attempts += 1
        masked = _build_masked(lines, blanked)
        try:
            ast.parse(masked)
        except SyntaxError as error:
            lineno = error.lineno
            if not lineno or lineno < 1 or lineno > len(lines):
                break
            start, end = _target_range(lines, error)
            newly = [
                position
                for position in range(start, end + 1)
                if position not in blanked
            ]
            if not newly:
                # The reported statement is already quarantined - fall back
                # to the line the message points back to (block headers).
                referenced = _referenced_line(error.msg or "")
                if referenced and 1 <= referenced <= len(lines):
                    index = referenced - 1
                    if index not in blanked:
                        newly = [index]
            if not newly:
                break
            blanked.update(newly)
            regions.append(
                RecoveryRegion(
                    start_line=min(newly) + 1,
                    end_line=max(newly) + 1,
                    message=error.msg or "invalid syntax",
                )
            )
            continue
        except (ValueError, MemoryError, RecursionError):
            break
        else:
            return RecoveryResult(
                code=masked,
                regions=_merge_regions(regions),
                recovered=bool(regions),
                attempts=attempts,
            )

    return RecoveryResult(code=source, regions=_merge_regions(regions), recovered=False, attempts=attempts)


def recover_javascript(lines: list[str]) -> list[RecoveryRegion]:
    """Report unbalanced braces/parens in a JavaScript source.

    The JS extractor is structural and never raises, so this is used to
    attach a diagnostic to the graph instead of failing the request.
    """
    regions: list[RecoveryRegion] = []
    stack: list[tuple[str, int]] = []

    for index, line in enumerate(lines):
        stripped = _strip_literals(line)
        for char in stripped:
            if char in _OPENERS:
                stack.append((char, index))
            elif char in _CLOSERS:
                if stack:
                    stack.pop()
                else:
                    regions.append(
                        RecoveryRegion(
                            start_line=index + 1,
                            end_line=index + 1,
                            message=f"unmatched '{char}'",
                            kind="syntax_error",
                        )
                    )
    for opener, index in stack:
        regions.append(
            RecoveryRegion(
                start_line=index + 1,
                end_line=index + 1,
                message=f"unclosed '{opener}'",
                kind="syntax_error",
            )
        )
    return regions


__all__ = [
    "RecoveryRegion",
    "RecoveryResult",
    "recover_python",
    "recover_javascript",
    "MAX_RECOVERY_ATTEMPTS",
]
