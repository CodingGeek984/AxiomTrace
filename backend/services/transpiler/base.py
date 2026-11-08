"""Shared AST preparation and naming utilities for the polyglot transpiler.

The parser emits node snippets with the indentation of their original source
location, so a snippet can be a complete module, a function/class definition,
or an indented fragment such as ``if``/``try``. :func:`prepare_source`
normalises every shape into a :class:`PreparedSource` that the target
emitters can walk without special cases.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from typing import Iterable

TARGET_IDS = ("typescript", "go", "php")


class TranspileError(ValueError):
    """Raised for invalid source, unsupported targets or unmappable syntax."""

    def __init__(self, message: str, *, line: int | None = None, status: int = 400) -> None:
        super().__init__(message)
        self.message = str(message)
        self.line = line
        self.status = int(status)


@dataclass
class PreparedSource:
    """A parsed Python module plus metadata needed by the emitters."""

    tree: ast.Module
    wrapped: bool = False
    docstring: str | None = None
    source: str = ""
    line_offset: int = 0

    @property
    def body(self) -> list[ast.stmt]:
        return list(self.tree.body)


@dataclass
class Param:
    """One Python function parameter."""

    name: str
    annotation: str | None = None
    default: ast.expr | None = None
    kind: str = "arg"  # posonly | arg | vararg | kwonly | kwarg


def _strip_docstring(body: list[ast.stmt]) -> tuple[str | None, list[ast.stmt]]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[0].value.value, body[1:]
    return None, body


def _module(body: Iterable[ast.stmt]) -> ast.Module:
    return ast.Module(body=list(body), type_ignores=[])


def _parse_except_fragment(source: str) -> ast.Module | None:
    try:
        tree = ast.parse("try:\n    pass\n" + source)
    except SyntaxError:
        return None
    try_node = tree.body[0] if tree.body else None
    if not isinstance(try_node, ast.Try) or not try_node.handlers:
        return None
    handler = try_node.handlers[0]
    body = list(handler.body)
    if handler.name:
        body.insert(
            0,
            ast.Assign(
                targets=[ast.Name(id=handler.name, ctx=ast.Store())],
                value=ast.Name(id="error", ctx=ast.Load()),
            ),
        )
    return _module(body)


def _parse_else_fragment(source: str) -> ast.Module | None:
    try:
        tree = ast.parse("if True:\n    pass\n" + source)
    except SyntaxError:
        return None
    if_node = tree.body[0] if tree.body else None
    if not isinstance(if_node, ast.If) or not if_node.orelse:
        return None
    return _module(if_node.orelse)


def prepare_source(code: str, *, language: str | None = None) -> PreparedSource:
    """Parse a Python snippet, accepting complete modules and fragments."""
    if language not in (None, "", "python", "py", "python3"):
        raise TranspileError(
            f"Only Python source can be transpiled (received {language!r}).",
            status=400,
        )
    if not isinstance(code, str) or not code.strip():
        raise TranspileError("There is no source code to transpile.", status=400)
    if "\x00" in code:
        raise TranspileError("Source contains a NUL byte and cannot be parsed.", status=400)

    cleaned = textwrap.dedent(code).strip("\n")
    if not cleaned.strip():
        raise TranspileError("There is no source code to transpile.", status=400)

    first_error: SyntaxError | None = None
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as error:
        first_error = error
    else:
        docstring, body = _strip_docstring(list(tree.body))
        # ``ast.parse`` accepts control-flow statements that are illegal at
        # module level (``return``, ``break``, ``continue``).  Treat such
        # snippets as function-body fragments so the generated wrapper is
        # syntactically valid in every target language.
        wrapped = any(isinstance(node, (ast.Return, ast.Break, ast.Continue)) for node in tree.body)
        return PreparedSource(
            tree=_module(body),
            wrapped=wrapped,
            docstring=docstring,
            source=cleaned,
        )

    stripped = cleaned.lstrip()
    if re.match(r"^(except|finally)\b", stripped):
        fragment = _parse_except_fragment(cleaned)
        if fragment is not None:
            docstring, body = _strip_docstring(list(fragment.body))
            return PreparedSource(
                tree=_module(body),
                wrapped=True,
                docstring=docstring,
                source=cleaned,
            )
    if re.match(r"^(elif|else)\b", stripped):
        fragment = _parse_else_fragment(cleaned)
        if fragment is not None:
            docstring, body = _strip_docstring(list(fragment.body))
            return PreparedSource(
                tree=_module(body),
                wrapped=True,
                docstring=docstring,
                source=cleaned,
            )

    try:
        wrapped = "def __axiom_fragment__():\n" + textwrap.indent(cleaned, "    ")
        tree = ast.parse(wrapped)
        function = tree.body[0] if tree.body else None
        if isinstance(function, ast.FunctionDef):
            docstring, body = _strip_docstring(list(function.body))
            return PreparedSource(
                tree=_module(body),
                wrapped=True,
                docstring=docstring,
                source=cleaned,
                line_offset=1,
            )
    except SyntaxError:
        pass

    line = first_error.lineno if first_error else None
    message = first_error.msg if first_error else "invalid syntax"
    raise TranspileError(
        f"Python syntax error at line {line or 1}: {message}",
        line=line,
        status=422,
    ) from first_error


def extract_docstring(body: list[ast.stmt]) -> tuple[str | None, list[ast.stmt]]:
    """Return ``(docstring, remaining_statements)`` for a function/class body."""
    return _strip_docstring(body)


def annotation_text(node: ast.expr | None) -> str | None:
    """Render an annotation without depending on a specific Python version."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - annotations are best effort
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def function_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Param]:
    """Flatten Python's argument groups into a single ordered list."""
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    params: list[Param] = []
    for index, argument in enumerate(positional):
        params.append(
            Param(
                name=argument.arg,
                annotation=annotation_text(argument.annotation),
                default=defaults[index] if index < len(defaults) else None,
                kind="posonly" if index < len(args.posonlyargs) else "arg",
            )
        )
    if args.vararg:
        params.append(
            Param(
                name=args.vararg.arg,
                annotation=annotation_text(args.vararg.annotation),
                kind="vararg",
            )
        )
    for argument, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(
            Param(
                name=argument.arg,
                annotation=annotation_text(argument.annotation),
                default=default,
                kind="kwonly",
            )
        )
    if args.kwarg:
        params.append(
            Param(
                name=args.kwarg.arg,
                annotation=annotation_text(args.kwarg.annotation),
                kind="kwarg",
            )
        )
    return params


def is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def docstring_text(node: ast.stmt) -> str:
    if is_docstring(node):
        return str(node.value.value).strip()
    return ""


def _walk_scope(body: Iterable[ast.stmt]) -> Iterable[ast.AST]:
    """Yield nodes in the current scope, skipping nested definitions."""
    stack = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        if isinstance(
            node,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))


def collect_assigned_names(
    body: Iterable[ast.stmt],
) -> tuple[list[str], set[str]]:
    """Collect local names assigned in a scope and names declared global.

    The result is used to predeclare locals in TypeScript/Go so a Python
    pattern such as ``if cond: x = 1`` followed by ``return x`` does not turn
    into a block-scoped JavaScript variable that is missing after the branch.
    """
    assigned: list[str] = []
    seen: set[str] = set()
    globals_: set[str] = set()

    def add(name: str | None) -> None:
        if not name or name in seen or name == "_":
            return
        seen.add(name)
        assigned.append(name)

    for node in _walk_scope(body):
        if isinstance(node, ast.Global):
            globals_.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            globals_.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            add(node.name)
        elif isinstance(node, ast.arg):
            add(node.arg)
    return [name for name in assigned if name not in globals_], globals_


def uses_await(body: Iterable[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Await) for node in _walk_scope(body))


def uses_yield(body: Iterable[ast.stmt]) -> bool:
    return any(
        isinstance(node, (ast.Yield, ast.YieldFrom)) for node in _walk_scope(body)
    )


def has_return(body: Iterable[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Return) for node in _walk_scope(body))


def contains_node(body: Iterable[ast.stmt], kinds: tuple[type[ast.AST], ...]) -> bool:
    return any(isinstance(node, kinds) for node in _walk_scope(body))


def free_names(body: Iterable[ast.stmt], bound: Iterable[str] = ()) -> list[str]:
    """Best-effort free-variable analysis used by PHP closures."""
    bound_set = set(bound)
    names: list[str] = []
    seen: set[str] = set()
    for node in _walk_scope(body):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound_set and node.id not in seen and node.id != "self":
                seen.add(node.id)
                names.append(node.id)
    return names


def safe_identifier(value: str, fallback: str = "axiom_node") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value or "").strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def snake_to_pascal(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    if not parts:
        return "AxiomNode"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def snake_to_camel(value: str) -> str:
    pascal = snake_to_pascal(value)
    return pascal[:1].lower() + pascal[1:] if pascal else "axiomNode"


def filename_stem(label: str | None, fallback: str = "axiom_node") -> str:
    return safe_identifier(label or fallback, fallback=fallback).lower()


__all__ = [
    "Param",
    "PreparedSource",
    "TARGET_IDS",
    "TranspileError",
    "annotation_text",
    "collect_assigned_names",
    "contains_node",
    "docstring_text",
    "extract_docstring",
    "filename_stem",
    "free_names",
    "function_params",
    "has_return",
    "is_docstring",
    "prepare_source",
    "safe_identifier",
    "snake_to_camel",
    "snake_to_pascal",
    "uses_await",
    "uses_yield",
]
