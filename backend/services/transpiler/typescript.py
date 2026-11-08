"""Python AST -> TypeScript emitter."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from services.transpiler.base import (
    PreparedSource,
    TranspileError,
    annotation_text,
    collect_assigned_names,
    extract_docstring,
    filename_stem,
    function_params,
    is_docstring,
    safe_identifier,
    uses_await,
    uses_yield,
)

_TS_RESERVED = {
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "enum", "export", "extends", "false",
    "finally", "for", "function", "if", "import", "in", "instanceof", "new",
    "null", "return", "super", "switch", "this", "throw", "true", "try",
    "typeof", "var", "void", "while", "with", "as", "implements", "interface",
    "let", "package", "private", "protected", "public", "static", "yield",
    "any", "boolean", "constructor", "declare", "get", "module", "require",
    "number", "set", "string", "symbol", "type", "from", "of", "unknown",
    "never", "readonly", "keyof", "namespace", "abstract", "async", "await",
}

_STDLIB_MODULES = {
    "abc", "base64", "collections", "contextlib", "copy", "csv", "datetime",
    "decimal", "enum", "functools", "hashlib", "io", "itertools", "json",
    "logging", "math", "os", "pathlib", "random", "re", "statistics", "string",
    "sys", "time", "types", "typing", "uuid", "warnings", "weakref",
}

_TS_HELPERS: dict[str, str] = {
    "len": """function len(value: any): number {
  if (value == null) return 0;
  if (typeof value === "string" || Array.isArray(value)) return value.length;
  if (value instanceof Map || value instanceof Set) return value.size;
  if (typeof value === "object") return Object.keys(value).length;
  return 0;
}""",
    "range": """function range(start: number, stop?: number, step = 1): number[] {
  let from = start;
  let to = stop;
  if (to === undefined) {
    to = from;
    from = 0;
  }
  const output: number[] = [];
  if (step === 0) step = 1;
  if (step > 0) {
    for (let index = from; index < to; index += step) output.push(index);
  } else {
    for (let index = from; index > to; index += step) output.push(index);
  }
  return output;
}""",
    "enumerate": """function enumerate(values: any): Array<[number, any]> {
  return Array.from(values ?? []).map((value, index) => [index, value]);
}""",
    "zip": """function zip(...values: any[][]): any[][] {
  const lists = values.map((value) => Array.from(value ?? []));
  const size = lists.reduce((minimum, list) => Math.min(minimum, list.length), Infinity);
  if (!Number.isFinite(size)) return [];
  return Array.from({ length: size }, (_, index) => lists.map((list) => list[index]));
}""",
    "sum": """function sum(values: any): number {
  return Array.from(values ?? []).reduce((total: number, value: any) => total + Number(value), 0);
}""",
    "sorted": """function sorted(values: any): any[] {
  return [...Array.from(values ?? [])].sort((left, right) => (left > right ? 1 : left < right ? -1 : 0));
}""",
    "slice": """function slice(value: any, start: number | null, stop: number | null, step: number | null): any {
  const values = Array.from(value ?? []) as any[];
  const length = values.length;
  let from = start ?? (step !== null && step < 0 ? length - 1 : 0);
  let to = stop ?? (step !== null && step < 0 ? -1 : length);
  let stride = step ?? 1;
  if (from < 0) from += length;
  if (to < 0) to += length;
  if (stride === 0) stride = 1;
  const output: any[] = [];
  if (stride > 0) {
    for (let index = from; index < to && index < length; index += stride) {
      if (index >= 0) output.push(values[index]);
    }
  } else {
    for (let index = from; index > to && index >= 0; index += stride) {
      if (index < length) output.push(values[index]);
    }
  }
  return typeof value === "string" ? output.join("") : output;
}""",
    "at": """function at(value: any, index: any): any {
  if (value == null) return undefined;
  if (typeof index === "number" && index < 0) {
    return value[value.length + index];
  }
  return value[index];
}""",
    "contains": """function contains(container: any, item: any): boolean {
  if (container == null) return false;
  if (typeof container === "string") return container.includes(String(item));
  if (Array.isArray(container)) return container.includes(item);
  if (container instanceof Set || container instanceof Map) return container.has(item);
  if (typeof container === "object") return Object.prototype.hasOwnProperty.call(container, item);
  return false;
}""",
    "title": """function title(value: string): string {
  return value.replace(/\\w\\S*/g, (word) => word[0].toUpperCase() + word.slice(1).toLowerCase());
}""",
}


def _label_name(label: str | None) -> str:
    text = (label or "axiom_node").strip()
    text = re.sub(r"^(async\s+)?def\s+", "", text)
    text = re.sub(r"^class\s+", "", text)
    text = text.replace("()", "").split("(", 1)[0]
    return safe_identifier(text, fallback="axiom_node")


def _ts_name(name: str) -> str:
    safe = safe_identifier(name)
    return f"{safe}_" if safe in _TS_RESERVED else safe


def _ts_method_name(name: str) -> str:
    """Map Python dunders to the closest TypeScript class member."""
    if name == "__init__":
        return "constructor"
    return _ts_name(name)


def _ts_type(annotation: str | None, *, returns: bool = False) -> str:
    if not annotation:
        return "any"
    text = annotation.strip()
    lowered = text.lower()
    if lowered in {"none", "nonetype"}:
        return "void" if returns else "null"
    if lowered in {"str", "string"}:
        return "string"
    if lowered in {"int", "float", "number"}:
        return "number"
    if lowered in {"bool", "boolean"}:
        return "boolean"
    if lowered in {"bytes", "bytearray"}:
        return "Uint8Array"
    if lowered in {"any", "typing.any"}:
        return "any"
    if lowered in {"object", "dict", "mapping"}:
        return "Record<string, any>"
    if lowered.startswith(("list", "tuple", "sequence", "iterable", "set")):
        if lowered.startswith("set"):
            return "Set<any>"
        return "any[]"
    if lowered.startswith("optional[") and text.endswith("]"):
        inner = text[text.index("[") + 1 : -1]
        return f"{_ts_type(inner)} | null"
    if lowered.startswith("union[") and text.endswith("]"):
        inner = text[text.index("[") + 1 : -1]
        parts = [_ts_type(part.strip()) for part in inner.split(",")]
        return " | ".join(dict.fromkeys(parts))
    if lowered.startswith("callable"):
        return "(...args: any[]) => any"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return text
    return "any"


class TypeScriptEmitter:
    """Emit a TypeScript module from a prepared Python AST."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.used_helpers: set[str] = set()
        self.import_lines: set[str] = set()
        self.use_axios = False
        self.use_path = False
        self.function_names: set[str] = set()
        self.class_names: set[str] = set()
        self.class_fields: dict[str, list[tuple[str, ast.expr | None]]] = {}
        self.class_methods: dict[str, set[str]] = {}
        self.function_params: dict[str, list[str]] = {}
        self.current_class: str | None = None
        self.current_methods: set[str] = set()
        self.current_fields: set[str] = set()
        self.declared: set[str] = set()
        self.temp_seq = 0
        self.loop_stack: list[str | None] = []
        self.http_vars: set[str] = set()

    # -- public ------------------------------------------------------
    def emit(self, prepared: PreparedSource, *, label: str | None = None) -> str:
        body = prepared.body
        if not body:
            raise TranspileError("No statements to transpile.", status=422)

        self._collect_definitions(body)
        self._collect_imports(body)

        sections: list[str] = [self._header(prepared, label)]

        functions: list[str] = []
        loose: list[ast.stmt] = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(self._emit_function(node, 0))
            elif isinstance(node, ast.ClassDef):
                functions.append(self._emit_class(node, 0))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            elif is_docstring(node):
                continue
            else:
                loose.append(node)

        if loose:
            functions.append(self._emit_wrapper(loose, label))

        # Imports are collected while the body is emitted (for example a
        # `BaseModel` base class), so render them afterwards.
        imports = self._render_imports()
        if imports:
            sections.append(imports)
        helpers = self._render_helpers()
        if helpers:
            sections.append(helpers)
        sections.extend(functions)
        output = "\n\n".join(section.rstrip() for section in sections if section.strip())
        return output.rstrip() + "\n"

    # -- collection --------------------------------------------------
    def _collect_definitions(self, body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = _ts_name(node.name)
                self.function_names.add(name)
                self.function_params[name] = [
                    param.name for param in function_params(node) if param.kind in {"arg", "posonly"}
                ]
            elif isinstance(node, ast.ClassDef):
                name = _ts_name(node.name)
                self.class_names.add(name)
                fields: list[tuple[str, ast.expr | None]] = []
                methods: set[str] = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.add(_ts_method_name(item.name))
                        for child in ast.walk(item):
                            if (
                                isinstance(child, ast.Attribute)
                                and isinstance(child.value, ast.Name)
                                and child.value.id in {"self", "cls"}
                                and isinstance(child.ctx, ast.Store)
                            ):
                                field_name = _ts_name(child.attr)
                                if not any(existing == field_name for existing, _ in fields):
                                    fields.append((field_name, None))
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                field_name = _ts_name(target.id)
                                if not any(existing == field_name for existing, _ in fields):
                                    fields.append((field_name, item.value))
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        field_name = _ts_name(item.target.id)
                        if not any(existing == field_name for existing, _ in fields):
                            fields.append((field_name, item.value))
                self.class_fields[name] = fields
                self.class_methods[name] = methods

    def _collect_imports(self, body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module == "requests":
                        self.use_axios = True
                    elif module == "axios":
                        self.import_lines.add('import axios from "axios";')
                    elif module in _STDLIB_MODULES:
                        continue
                    elif "." in module:
                        self.warn(
                            f"Python dotted import `{module}` was kept as a module import."
                        )
                        alias_name = _ts_name(alias.asname or module.split(".")[0])
                        self.import_lines.add(f'import * as {alias_name} from "{module}";')
                    else:
                        alias_name = _ts_name(alias.asname or module)
                        self.import_lines.add(f'import * as {alias_name} from "{module}";')
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "requests":
                    self.use_axios = True
                    continue
                if module in _STDLIB_MODULES or module in {"dataclasses", "pydantic"}:
                    if module == "pydantic":
                        for alias in node.names:
                            self.import_lines.add(
                                f'import {{ {alias.name} }} from "pydantic";'
                            )
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        self.import_lines.add(f'import * as {_ts_name(module)} from "{module}";')
                        continue
                    imported = _ts_name(alias.asname or alias.name)
                    self.import_lines.add(
                        f'import {{ {alias.name} as {imported} }} from "{module}";'
                    )

    def _render_imports(self) -> str:
        lines = set(self.import_lines)
        if self.use_axios:
            lines.add('import axios from "axios";')
        if self.use_path:
            lines.add('import path from "node:path";')
        if not lines:
            return ""
        return "\n".join(sorted(lines))

    def _render_helpers(self) -> str:
        if not self.used_helpers:
            return ""
        return "\n\n".join(_TS_HELPERS[name] for name in sorted(self.used_helpers))

    def _use(self, helper: str) -> None:
        self.used_helpers.add(helper)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _header(self, prepared: PreparedSource, label: str | None) -> str:
        source_lines = len(prepared.source.splitlines())
        node_line = f"\n * Node: {label}" if label else ""
        return (
            "/**\n"
            " * AxiomTrace polyglot export\n"
            f" * Source: Python ({source_lines} lines) -> Target: TypeScript{node_line}\n"
            " * Generated deterministically from the Python AST.\n"
            " */"
        )

    # -- functions/classes -------------------------------------------
    def _emit_wrapper(self, body: list[ast.stmt], label: str | None) -> str:
        name = safe_identifier(_label_name(label), fallback="axiomNode")
        if name in _TS_RESERVED:
            name = f"{name}_"
        is_async = uses_await(body) or self._uses_http(body)
        is_generator = uses_yield(body)
        function_keyword = "async function*" if is_async and is_generator else (
            "async function" if is_async else ("function*" if is_generator else "function")
        )
        assigned, _ = collect_assigned_names(body)
        self.declared = set()
        implicit: list[str] = []
        if self._uses_name(body, "self"):
            implicit.append("self: any = {}")
        if self._uses_name(body, "cls"):
            implicit.append("cls: any = {}")
        lines = [f"{function_keyword} {name}({', '.join(implicit)}): any {{"]
        previous_http = self.http_vars
        self.http_vars = set()
        prelude = self._prelude(assigned, excluded={"self", "cls"}, level=1)
        lines.extend(prelude)
        lines.extend(self._emit_body(body, 1))
        if not body:
            lines.append("  /* pass */")
        lines.append("}")
        self.http_vars = previous_http
        return "\n".join(lines)

    def _uses_name(self, body: list[ast.stmt], name: str) -> bool:
        for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
            if isinstance(node, ast.Name) and node.id == name:
                return True
        return False

    def _uses_http(self, body: list[ast.stmt]) -> bool:
        for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"requests", "httpx"}
            ):
                return True
        return False

    def _prelude(self, assigned: list[str], excluded: set[str], level: int) -> list[str]:
        lines: list[str] = []
        for name in assigned:
            if name in excluded or name in {"self", "cls"}:
                continue
            safe = _ts_name(name)
            if safe in self.declared:
                continue
            self.declared.add(safe)
            lines.append(f"{'  ' * level}let {safe}: any;")
        return lines

    def _emit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        level: int,
        *,
        in_class: bool = False,
        class_name: str | None = None,
    ) -> str:
        docstring, body = extract_docstring(list(node.body))
        is_async = (
            isinstance(node, ast.AsyncFunctionDef)
            or uses_await(body)
            or self._uses_http(body)
        )
        is_generator = uses_yield(body)
        params = function_params(node)
        if in_class and params and params[0].name in {"self", "cls"}:
            params = params[1:]

        name = _ts_method_name(node.name) if in_class else _ts_name(node.name)
        decorators = {
            decorator.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Name)
        }
        is_static = "staticmethod" in decorators or "classmethod" in decorators
        is_property = "property" in decorators

        indent = "  " * level
        static = "static " if is_static else ""
        if in_class:
            if is_async and is_generator:
                member = f"async *{name}"
            elif is_generator:
                member = f"*{name}"
            elif is_async:
                member = f"async {name}"
            else:
                member = name
        else:
            if is_async and is_generator:
                member = f"async function* {name}"
            elif is_generator:
                member = f"function* {name}"
            elif is_async:
                member = f"async function {name}"
            else:
                member = f"function {name}"

        param_parts: list[str] = []
        param_names: set[str] = set()
        for param in params:
            param_names.add(param.name)
            safe = _ts_name(param.name)
            if param.kind == "vararg":
                param_parts.append(f"...{safe}: any[]")
                continue
            if param.kind == "kwarg":
                param_parts.append(f"{safe}: Record<string, any> = {{}}")
                continue
            piece = f"{safe}: {_ts_type(param.annotation)}"
            if param.default is not None:
                piece += f" = {self._expr(param.default)}"
            param_parts.append(piece)

        return_type = _ts_type(annotation_text(node.returns), returns=True)
        if in_class and name == "constructor":
            signature = f"{indent}{static}constructor({', '.join(param_parts)})"
        elif is_property:
            signature = f"{indent}{static}get {name}()"
            return_type = _ts_type(annotation_text(node.returns), returns=True)
        else:
            signature = f"{indent}{static}{member}({', '.join(param_parts)}): {return_type}"

        lines: list[str] = []
        if docstring:
            lines.append(f"{indent}/**")
            for doc_line in docstring.splitlines():
                lines.append(f"{indent} * {doc_line}")
            lines.append(f"{indent} */")
        lines.append(signature + " {")
        assigned, _ = collect_assigned_names(body)
        self.declared = set()
        previous_http = self.http_vars
        self.http_vars = set()
        lines.extend(self._prelude(assigned, excluded=param_names, level=level + 1))
        lines.extend(self._emit_body(body, level + 1))
        if not body:
            lines.append(f"{'  ' * (level + 1)}/* pass */")
        lines.append(f"{indent}}}")
        self.http_vars = previous_http
        return "\n".join(lines)

    def _emit_class(self, node: ast.ClassDef, level: int) -> str:
        docstring, body = extract_docstring(list(node.body))
        name = _ts_name(node.name)
        self.current_class = name
        self.current_methods = set(self.class_methods.get(name, set()))
        self.current_fields = {field for field, _ in self.class_fields.get(name, [])}

        bases: list[str] = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id in {"object", "ABC"}:
                    continue
                if base.id == "BaseModel":
                    self.import_lines.add('import { BaseModel } from "pydantic";')
                bases.append(_ts_name(base.id))
            elif isinstance(base, ast.Attribute):
                bases.append(self._expr(base))
        if len(bases) > 1:
            self.warn("Multiple Python bases were reduced to the first TypeScript base class.")

        indent = "  " * level
        header = f"{indent}class {name}"
        if bases:
            header += f" extends {bases[0]}"
        lines: list[str] = []
        if docstring:
            lines.append(f"{indent}/**")
            for doc_line in docstring.splitlines():
                lines.append(f"{indent} * {doc_line}")
            lines.append(f"{indent} */")
        lines.append(header + " {")

        fields = self.class_fields.get(name, [])
        for field_name, initializer in fields:
            if initializer is None:
                lines.append(f"{indent}  {field_name}: any = undefined;")
            else:
                lines.append(
                    f"{indent}  {field_name}: any = {self._expr(initializer)};"
                )

        if fields and "constructor" not in self.current_methods:
            # Python dataclasses (and simple attribute-only classes) receive a
            # generated constructor so `User("ada", 32)` keeps working.
            lines.append(f"{indent}  constructor(...args: any[]) {{")
            for index, (field_name, initializer) in enumerate(fields):
                if initializer is None:
                    lines.append(
                        f"{indent}    this.{field_name} = args[{index}];"
                    )
                else:
                    lines.append(
                        f"{indent}    this.{field_name} = args.length > {index} ? args[{index}] : {self._expr(initializer)};"
                    )
            lines.append(f"{indent}  }}")

        members: list[str] = []
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.append(
                    self._emit_function(
                        item, level + 1, in_class=True, class_name=name
                    )
                )
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                continue
            elif is_docstring(item):
                continue
            elif isinstance(item, ast.Pass):
                continue
            else:
                # Class-level executable statements are rare in parser nodes;
                # keep them as a static initialization block.
                statements = self._emit_stmt(item, level + 1)
                if statements:
                    members.append(statements)
        lines.extend(members)
        if not members and not fields:
            lines.append(f"{indent}  /* no members */")
        lines.append(f"{indent}}}")

        self.current_class = None
        self.current_methods = set()
        self.current_fields = set()
        return "\n".join(lines)

    # -- statements --------------------------------------------------
    def _emit_body(self, body: list[ast.stmt], level: int) -> list[str]:
        lines: list[str] = []
        for node in body:
            if is_docstring(node):
                continue
            emitted = self._emit_stmt(node, level)
            if emitted:
                lines.append(emitted)
        return lines

    def _emit_stmt(self, node: ast.stmt, level: int) -> str:
        indent = "  " * level
        if isinstance(node, ast.Return):
            if node.value is None:
                return f"{indent}return;"
            return f"{indent}return {self._expr(node.value)};"
        if isinstance(node, ast.Assign):
            value = self._expr(node.value)
            if len(node.targets) == 1:
                self._track_http_target(node.targets[0], node.value)
                return f"{indent}{self._target(node.targets[0])} = {value};"
            for target in node.targets:
                self._track_http_target(target, node.value)
            return f"{indent}{' = '.join(self._target(target) for target in node.targets)} = {value};"
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                return f"{indent}{self._target(node.target)} = undefined;"
            self._track_http_target(node.target, node.value)
            return f"{indent}{self._target(node.target)} = {self._expr(node.value)};"
        if isinstance(node, ast.AugAssign):
            target = self._target(node.target)
            value = self._expr(node.value)
            op = self._binop_symbol(node.op)
            if op == "//":
                return f"{indent}{target} = Math.floor({target} / {value});"
            return f"{indent}{target} {op}= {value};"
        if isinstance(node, ast.If):
            lines = [f"{indent}if ({self._expr(node.test)}) {{"]
            lines.extend(self._emit_body(node.body, level + 1))
            if not node.body:
                lines.append(f"{'  ' * (level + 1)}/* pass */")
            if node.orelse:
                if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    nested = self._emit_stmt(node.orelse[0], level)
                    lines.append(f"{indent}}} else " + nested[len(indent) :])
                else:
                    lines.append(f"{indent}}} else {{")
                    lines.extend(self._emit_body(node.orelse, level + 1))
                    lines.append(f"{indent}}}")
            else:
                lines.append(f"{indent}}}")
            return "\n".join(lines)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return self._emit_for(node, level)
        if isinstance(node, ast.While):
            return self._emit_while(node, level)
        if isinstance(node, ast.Break):
            flag = self.loop_stack[-1] if self.loop_stack else None
            if flag:
                return f"{indent}{flag} = true;\n{indent}break;"
            return f"{indent}break;"
        if isinstance(node, ast.Continue):
            return f"{indent}continue;"
        if isinstance(node, ast.Try):
            return self._emit_try(node, level)
        if isinstance(node, ast.Raise):
            if node.exc is None:
                return f"{indent}throw error;"
            if isinstance(node.exc, ast.Call):
                message = self._expr(node.exc.args[0]) if node.exc.args else '""'
                return f"{indent}throw new Error({message});"
            return f"{indent}throw {self._expr(node.exc)};"
        if isinstance(node, ast.Assert):
            message = self._expr(node.msg) if node.msg else '"Assertion failed"'
            return (
                f"{indent}if (!({self._expr(node.test)})) {{\n"
                f"{'  ' * (level + 1)}throw new Error({message});\n"
                f"{indent}}}"
            )
        if isinstance(node, ast.Expr):
            return self._emit_expr_statement(node, level)
        if isinstance(node, ast.Pass):
            return f"{indent}/* pass */"
        if isinstance(node, ast.With):
            return self._emit_with(node, level)
        if isinstance(node, ast.AsyncWith):
            return self._emit_with(node, level, is_async=True)
        if isinstance(node, ast.Delete):
            parts = []
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    parts.append(
                        f"{indent}delete {self._expr(target.value)}[{self._slice_index(target.slice)}];"
                    )
                elif isinstance(target, ast.Attribute):
                    parts.append(f"{indent}delete {self._expr(target.value)}.{_ts_name(target.attr)};")
                elif isinstance(target, ast.Name):
                    parts.append(f"{indent}{_ts_name(target.id)} = undefined;")
            return "\n".join(parts)
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom)):
            return ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._emit_function(node, level)
        if isinstance(node, ast.ClassDef):
            return self._emit_class(node, level)
        if isinstance(node, ast.Match):
            return self._emit_match(node, level)
        raise TranspileError(
            f"TypeScript target does not support `{type(node).__name__}` statements.",
            line=getattr(node, "lineno", None),
            status=422,
        )

    def _is_http_call(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return node.func.value.id in {"requests", "httpx"}
        if isinstance(node, ast.Await):
            return self._is_http_call(node.value)
        return False

    def _track_http_target(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name) and self._is_http_call(value):
            self.http_vars.add(target.id)

    def _emit_expr_statement(self, node: ast.Expr, level: int) -> str:
        indent = "  " * level
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            method = value.func.attr
            receiver = self._expr(value.func.value)
            if method == "append" and len(value.args) == 1:
                return f"{indent}{receiver}.push({self._expr(value.args[0])});"
            if method == "extend" and len(value.args) == 1:
                return f"{indent}{receiver}.push(...{self._expr(value.args[0])});"
            if method == "update" and len(value.args) == 1:
                return f"{indent}Object.assign({receiver}, {self._expr(value.args[0])});"
        return f"{indent}{self._expr(value)};"

    def _emit_for(self, node: ast.For | ast.AsyncFor, level: int) -> str:
        indent = "  " * level
        flag = None
        if node.orelse:
            self.temp_seq += 1
            flag = f"__broke{self.temp_seq}"
        self.loop_stack.append(flag)
        lines: list[str] = []
        if flag:
            lines.append(f"{indent}let {flag} = false;")

        target = self._target_pattern(node.target)
        is_async = isinstance(node, ast.AsyncFor)
        iterable = self._expr(node.iter)

        if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name):
            func_name = node.iter.func.id
            args = [self._expr(arg) for arg in node.iter.args]
            if func_name == "range" and isinstance(node.target, ast.Name):
                variable = self._target(node.target)
                self._use("range")
                lines.append(
                    f"{indent}for ({variable} of range({', '.join(args)})) {{"
                )
                lines.extend(self._emit_body(node.body, level + 1))
                lines.append(f"{indent}}}")
                self.loop_stack.pop()
                if node.orelse:
                    lines.append(f"{indent}if (!{flag}) {{")
                    lines.extend(self._emit_body(node.orelse, level + 1))
                    lines.append(f"{indent}}}")
                return "\n".join(lines)
            if func_name == "enumerate" and isinstance(node.target, (ast.Tuple, ast.List)) and len(node.target.elts) == 2:
                index_target = self._target(node.target.elts[0])
                value_target = self._target(node.target.elts[1])
                source = args[0] if args else "[]"
                lines.append(
                    f"{indent}for ([{index_target}, {value_target}] of Array.from({source}).entries()) {{"
                )
                lines.extend(self._emit_body(node.body, level + 1))
                lines.append(f"{indent}}}")
                self.loop_stack.pop()
                if node.orelse:
                    lines.append(f"{indent}if (!{flag}) {{")
                    lines.extend(self._emit_body(node.orelse, level + 1))
                    lines.append(f"{indent}}}")
                return "\n".join(lines)
            if func_name == "zip" and isinstance(node.target, (ast.Tuple, ast.List)):
                source = f"zip({', '.join(args)})" if args else "[]"
                self._use("zip")
                lines.append(f"{indent}for ({target} of {source}) {{")
                lines.extend(self._emit_body(node.body, level + 1))
                lines.append(f"{indent}}}")
                self.loop_stack.pop()
                if node.orelse:
                    lines.append(f"{indent}if (!{flag}) {{")
                    lines.extend(self._emit_body(node.orelse, level + 1))
                    lines.append(f"{indent}}}")
                return "\n".join(lines)

        if (
            isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Attribute)
            and node.iter.func.attr == "items"
            and isinstance(node.target, (ast.Tuple, ast.List))
            and len(node.target.elts) == 2
        ):
            key_target = self._target(node.target.elts[0])
            value_target = self._target(node.target.elts[1])
            source = self._expr(node.iter.func.value)
            lines.append(f"{indent}for ([{key_target}, {value_target}] of Object.entries({source})) {{")
            lines.extend(self._emit_body(node.body, level + 1))
            lines.append(f"{indent}}}")
            self.loop_stack.pop()
            if node.orelse:
                lines.append(f"{indent}if (!{flag}) {{")
                lines.extend(self._emit_body(node.orelse, level + 1))
                lines.append(f"{indent}}}")
            return "\n".join(lines)

        keyword = "for await" if is_async else "for"
        lines.append(f"{indent}{keyword} ({target} of {iterable}) {{")
        lines.extend(self._emit_body(node.body, level + 1))
        lines.append(f"{indent}}}")
        self.loop_stack.pop()
        if node.orelse:
            lines.append(f"{indent}if (!{flag}) {{")
            lines.extend(self._emit_body(node.orelse, level + 1))
            lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _emit_while(self, node: ast.While, level: int) -> str:
        indent = "  " * level
        flag = None
        if node.orelse:
            self.temp_seq += 1
            flag = f"__broke{self.temp_seq}"
        self.loop_stack.append(flag)
        lines: list[str] = []
        if flag:
            lines.append(f"{indent}let {flag} = false;")
        lines.append(f"{indent}while ({self._expr(node.test)}) {{")
        lines.extend(self._emit_body(node.body, level + 1))
        lines.append(f"{indent}}}")
        self.loop_stack.pop()
        if node.orelse:
            lines.append(f"{indent}if (!{flag}) {{")
            lines.extend(self._emit_body(node.orelse, level + 1))
            lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _emit_try(self, node: ast.Try, level: int) -> str:
        indent = "  " * level
        lines = [f"{indent}try {{"]
        lines.extend(self._emit_body(node.body, level + 1))
        if node.orelse:
            lines.extend(self._emit_body(node.orelse, level + 1))
        if node.handlers:
            handler = node.handlers[0]
            lines.append(f"{indent}}} catch (error) {{")
            if handler.name:
                lines.append(f"{'  ' * (level + 1)}{_ts_name(handler.name)} = error;")
            lines.extend(self._emit_body(handler.body, level + 1))
            for extra in node.handlers[1:]:
                lines.append(f"{'  ' * (level + 1)}/* additional handler: {self._handler_name(extra)} */")
                if extra.name:
                    lines.append(f"{'  ' * (level + 1)}{_ts_name(extra.name)} = error;")
                lines.extend(self._emit_body(extra.body, level + 1))
            if len(node.handlers) > 1:
                self.warn("Multiple Python exception handlers are folded into one catch block.")
            lines.append(f"{indent}}}")
        else:
            lines.append(f"{indent}}} catch (error) {{")
            lines.append(f"{'  ' * (level + 1)}throw error;")
            lines.append(f"{indent}}}")
        if node.finalbody:
            lines.append(f"{indent}finally {{")
            lines.extend(self._emit_body(node.finalbody, level + 1))
            lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _handler_name(self, handler: ast.ExceptHandler) -> str:
        if handler.type is None:
            return "bare except"
        try:
            return ast.unparse(handler.type)
        except Exception:  # noqa: BLE001
            return "except"

    def _emit_with(self, node: ast.With | ast.AsyncWith, level: int, *, is_async: bool = False) -> str:
        indent = "  " * level
        lines: list[str] = []
        for item in node.items:
            value = self._expr(item.context_expr)
            if is_async:
                value = f"await {value}"
            if item.optional_vars is not None:
                lines.append(f"{indent}{self._target(item.optional_vars)} = {value};")
            else:
                lines.append(f"{indent}{value};")
        lines.extend(self._emit_body(node.body, level))
        self.warn("Python `with` blocks are emitted as scoped assignments; cleanup calls are not synthesised.")
        return "\n".join(lines)

    def _emit_match(self, node: ast.Match, level: int) -> str:
        indent = "  " * level
        lines = [f"{indent}switch ({self._expr(node.subject)}) {{"]
        for case in node.cases:
            pattern = self._pattern(case.pattern)
            if pattern is None:
                lines.append(f"{indent}  default: {{")
            else:
                lines.append(f"{indent}  case {pattern}: {{")
            if case.guard is not None:
                lines.append(
                    f"{'  ' * (level + 2)}if (!({self._expr(case.guard)})) break;"
                )
            lines.extend(self._emit_body(case.body, level + 2))
            lines.append(f"{indent}  }}")
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _pattern(self, node: ast.pattern) -> str | None:
        if isinstance(node, ast.MatchAs) and node.pattern is None:
            return None
        if isinstance(node, ast.MatchValue):
            return self._expr(node.value)
        if isinstance(node, ast.MatchSingleton):
            return "null" if node.value is None else ("true" if node.value else "false")
        self.warn("Complex match patterns are emitted with a default branch only.")
        return None

    # -- targets -----------------------------------------------------
    def _target(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return _ts_name(node.id)
        if isinstance(node, ast.Attribute):
            if (
                self.current_class
                and isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
            ):
                return f"this.{_ts_name(node.attr)}"
            return f"{self._expr(node.value)}.{_ts_name(node.attr)}"
        if isinstance(node, ast.Subscript):
            return f"{self._expr(node.value)}[{self._slice_index(node.slice)}]"
        if isinstance(node, (ast.Tuple, ast.List)):
            return f"[{', '.join(self._target(element) for element in node.elts)}]"
        if isinstance(node, ast.Starred):
            return f"...{self._target(node.value)}"
        raise TranspileError(
            f"TypeScript target cannot assign to `{type(node).__name__}`.",
            line=getattr(node, "lineno", None),
            status=422,
        )

    def _target_pattern(self, node: ast.expr) -> str:
        return self._target(node)

    # -- expressions -------------------------------------------------
    def _expr(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            if node.id == "None":
                return "null"
            if node.id == "True":
                return "true"
            if node.id == "False":
                return "false"
            if node.id == "__name__":
                return '"__main__"'
            if node.id in {"self", "cls"}:
                return "this" if self.current_class else _ts_name(node.id)
            return _ts_name(node.id)
        if isinstance(node, ast.Constant):
            return self._literal(node.value)
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.BoolOp):
            operator = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + operator.join(self._expr(value) for value in node.values) + ")"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"!({self._expr(node.operand)})"
            if isinstance(node.op, ast.USub):
                return f"-({self._expr(node.operand)})"
            if isinstance(node.op, ast.UAdd):
                return f"+({self._expr(node.operand)})"
            if isinstance(node.op, ast.Invert):
                return f"~({self._expr(node.operand)})"
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                return self._slice(node.value, node.slice)
            self._use("at")
            return f"at({self._expr(node.value)}, {self._slice_index(node.slice)})"
        if isinstance(node, ast.List):
            return f"[{self._list_items(node.elts)}]"
        if isinstance(node, ast.Tuple):
            return f"[{self._list_items(node.elts)}]"
        if isinstance(node, ast.Set):
            return f"new Set([{self._list_items(node.elts)}])"
        if isinstance(node, ast.Dict):
            entries = []
            for key, value in zip(node.keys, node.values):
                if key is None:
                    entries.append(f"...{self._expr(value)}")
                else:
                    entries.append(f"[{self._expr(key)}]: {self._expr(value)}")
            return "{" + ", ".join(entries) + "}"
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._comprehension(node, "set" if isinstance(node, ast.SetComp) else "list")
        if isinstance(node, ast.DictComp):
            return self._comprehension(node, "dict")
        if isinstance(node, ast.Lambda):
            args = ", ".join(_ts_name(arg.arg) for arg in node.args.args)
            return f"({args}) => {self._expr(node.body)}"
        if isinstance(node, ast.IfExp):
            return f"({self._expr(node.test)} ? {self._expr(node.body)} : {self._expr(node.orelse)})"
        if isinstance(node, ast.JoinedStr):
            return self._fstring(node)
        if isinstance(node, ast.FormattedValue):
            return f"${{{self._expr(node.value)}}}"
        if isinstance(node, ast.Starred):
            return f"...{self._expr(node.value)}"
        if isinstance(node, ast.Await):
            return f"await {self._expr(node.value)}"
        if isinstance(node, ast.Yield):
            return "yield" if node.value is None else f"yield {self._expr(node.value)}"
        if isinstance(node, ast.YieldFrom):
            return f"yield* {self._expr(node.value)}"
        if isinstance(node, ast.NamedExpr):
            return f"({self._target(node.target)} = {self._expr(node.value)})"
        if isinstance(node, ast.Constant):
            return self._literal(node.value)
        raise TranspileError(
            f"TypeScript target does not support `{type(node).__name__}` expressions.",
            line=getattr(node, "lineno", None),
            status=422,
        )

    def _literal(self, value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, bytes):
            return "new Uint8Array([" + ", ".join(str(byte) for byte in value) + "])"
        if isinstance(value, complex):
            raise TranspileError("TypeScript target does not support complex numbers.", status=422)
        if isinstance(value, float):
            if value != value:
                return "Number.NaN"
            if value == float("inf"):
                return "Number.POSITIVE_INFINITY"
            if value == float("-inf"):
                return "Number.NEGATIVE_INFINITY"
            return repr(value)
        if value is Ellipsis:
            return "undefined"
        return repr(value)

    def _binop_symbol(self, op: ast.operator) -> str:
        mapping = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.FloorDiv: "//",
            ast.Mod: "%",
            ast.Pow: "**",
            ast.LShift: "<<",
            ast.RShift: ">>",
            ast.BitOr: "|",
            ast.BitXor: "^",
            ast.BitAnd: "&",
        }
        for kind, symbol in mapping.items():
            if isinstance(op, kind):
                return symbol
        raise TranspileError(
            f"TypeScript target does not support `{type(op).__name__}` operators.",
            status=422,
        )

    def _binop(self, node: ast.BinOp) -> str:
        left = self._expr(node.left)
        right = self._expr(node.right)
        if isinstance(node.op, ast.FloorDiv):
            return f"Math.floor({left} / {right})"
        if isinstance(node.op, ast.Pow):
            return f"({left} ** {right})"
        symbol = self._binop_symbol(node.op)
        return f"({left} {symbol} {right})"

    def _compare(self, node: ast.Compare) -> str:
        parts: list[str] = []
        left = self._expr(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._expr(comparator)
            if isinstance(op, ast.In):
                self._use("contains")
                parts.append(f"contains({right}, {left})")
            elif isinstance(op, ast.NotIn):
                self._use("contains")
                parts.append(f"!contains({right}, {left})")
            elif isinstance(op, ast.Is):
                parts.append(f"({left} === {right})")
            elif isinstance(op, ast.IsNot):
                parts.append(f"({left} !== {right})")
            elif isinstance(op, ast.Eq):
                parts.append(f"({left} === {right})")
            elif isinstance(op, ast.NotEq):
                parts.append(f"({left} !== {right})")
            elif isinstance(op, ast.Lt):
                parts.append(f"({left} < {right})")
            elif isinstance(op, ast.LtE):
                parts.append(f"({left} <= {right})")
            elif isinstance(op, ast.Gt):
                parts.append(f"({left} > {right})")
            elif isinstance(op, ast.GtE):
                parts.append(f"({left} >= {right})")
            else:
                raise TranspileError(
                    f"TypeScript target does not support `{type(op).__name__}` comparisons.",
                    status=422,
                )
            left = right
        return " && ".join(parts) if len(parts) > 1 else parts[0]

    def _attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name):
            base = node.value.id
            if base in {"self", "cls"}:
                if self.current_class:
                    return f"this.{_ts_name(node.attr)}"
                return f"{_ts_name(base)}.{_ts_name(node.attr)}"
            if base == "__name__":
                return '"_main_"'
            if base in self.http_vars:
                if node.attr == "status_code":
                    return f"{_ts_name(base)}.status"
                if node.attr == "text":
                    return f"{_ts_name(base)}.data"
            if base == "math":
                constants = {"pi": "Math.PI", "e": "Math.E", "tau": "Math.PI * 2"}
                if node.attr in constants:
                    return constants[node.attr]
            if base == "sys" and node.attr == "argv":
                return "process.argv"
            if base == "os" and node.attr == "environ":
                return "process.env"
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            if node.value.func.id == "super":
                return f"super.{_ts_name(node.attr)}"
        return f"{self._expr(node.value)}.{_ts_name(node.attr)}"

    def _slice_index(self, node: ast.expr) -> str:
        if isinstance(node, ast.Slice):
            return self._slice_expression(node)
        return self._expr(node)

    def _slice_expression(self, node: ast.Slice) -> str:
        start = "null" if node.lower is None else self._expr(node.lower)
        stop = "null" if node.upper is None else self._expr(node.upper)
        step = "null" if node.step is None else self._expr(node.step)
        return f"slice: {start}:{stop}:{step}"

    def _slice(self, value: ast.expr, node: ast.Slice) -> str:
        source = self._expr(value)
        start = "undefined" if node.lower is None else self._expr(node.lower)
        stop = "undefined" if node.upper is None else self._expr(node.upper)
        if node.step is None:
            return f"{source}.slice({start}, {stop})"
        if (
            isinstance(node.step, ast.UnaryOp)
            and isinstance(node.step.op, ast.USub)
            and isinstance(node.step.operand, ast.Constant)
            and node.step.operand.value == 1
        ):
            if node.lower is None and node.upper is None:
                return f"[...{source}].reverse()"
            self._use("slice")
            start_null = "null" if node.lower is None else self._expr(node.lower)
            stop_null = "null" if node.upper is None else self._expr(node.upper)
            return f"slice({source}, {start_null}, {stop_null}, -1)"
        step = self._expr(node.step)
        self._use("slice")
        start_null = "null" if node.lower is None else self._expr(node.lower)
        stop_null = "null" if node.upper is None else self._expr(node.upper)
        return f"slice({source}, {start_null}, {stop_null}, {step})"

    def _list_items(self, items: list[ast.expr]) -> str:
        return ", ".join(self._expr(item) for item in items)

    def _fstring(self, node: ast.JoinedStr) -> str:
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                text = str(value.value)
                text = text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                parts.append(text)
            elif isinstance(value, ast.FormattedValue):
                expression = self._expr(value.value)
                if value.conversion == 114:
                    expression = f"JSON.stringify({expression})"
                if value.format_spec is not None:
                    self.warn("Python f-string format specifiers are not reproduced exactly.")
                parts.append(f"${{{expression}}}")
        return "`" + "".join(parts) + "`"

    def _comprehension(self, node: ast.expr, kind: str) -> str:
        generators = node.generators
        if not generators:
            return "{}" if kind == "dict" else ("new Set()" if kind == "set" else "[]")
        if any(generator.is_async for generator in generators):
            self.warn("Async comprehensions are emitted as synchronous array operations.")
        elt = node.key if isinstance(node, ast.DictComp) else node.elt
        value = node.value if isinstance(node, ast.DictComp) else None

        def inner(index: int) -> str:
            generator = generators[index]
            source = self._expr(generator.iter)
            target = self._target_pattern(generator.target)
            condition = " && ".join(self._expr(test) for test in generator.ifs)
            if index < len(generators) - 1:
                nested = inner(index + 1)
                if condition:
                    return (
                        f"{source}.filter(({target}) => {condition})"
                        f".flatMap(({target}) => {nested})"
                    )
                return f"{source}.flatMap(({target}) => {nested})"
            if kind == "dict":
                pair = f"[{self._expr(elt)}, {self._expr(value)}]"
                if condition:
                    return f"{source}.filter(({target}) => {condition}).map(({target}) => {pair})"
                return f"{source}.map(({target}) => {pair})"
            if kind == "set":
                if condition:
                    return f"{source}.filter(({target}) => {condition}).map(({target}) => {self._expr(elt)})"
                return f"{source}.map(({target}) => {self._expr(elt)})"
            if condition:
                return f"{source}.filter(({target}) => {condition}).map(({target}) => {self._expr(elt)})"
            return f"{source}.map(({target}) => {self._expr(elt)})"

        result = inner(0)
        if kind == "dict":
            return f"Object.fromEntries({result})"
        if kind == "set":
            return f"new Set({result})"
        return result

    # -- calls -------------------------------------------------------
    def _call(self, node: ast.Call) -> str:
        func = node.func
        args = list(node.args)
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}

        if isinstance(func, ast.Name):
            name = func.id
            if name in {"str", "int", "float", "bool", "list", "tuple", "set", "dict", "type", "repr", "isinstance"}:
                return self._builtin_call(name, args, keywords)
            if name in {"len", "range", "enumerate", "zip", "sum", "sorted", "reversed", "min", "max", "abs", "round"}:
                return self._builtin_call(name, args, keywords)
            if name == "print":
                return f"console.log({', '.join(self._expr(arg) for arg in args)})"
            if name == "super":
                return "super()"
            if name == "open":
                self.warn("Python `open()` calls are emitted as-is; provide a TypeScript file adapter.")
                return f"open({', '.join(self._expr(arg) for arg in args)})"
            if name in self.class_names:
                return f"new {_ts_name(name)}({self._positional(args, keywords)})"
            if name in self.function_names:
                return f"{_ts_name(name)}({self._ordered_args(name, args, keywords)})"
            return f"{_ts_name(name)}({self._positional(args, keywords)})"

        if isinstance(func, ast.Attribute):
            return self._attribute_call(func, args, keywords)

        if isinstance(func, ast.Subscript):
            return f"{self._expr(func)}({self._positional(args, keywords)})"

        return f"({self._expr(func)})({self._positional(args, keywords)})"

    def _builtin_call(self, name: str, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        values = [self._expr(arg) for arg in args]
        if name == "len":
            self._use("len")
            return f"len({values[0] if values else 'null'})"
        if name == "str":
            return f"String({values[0] if values else 'null'})"
        if name == "int":
            return f"Math.trunc(Number({values[0] if values else 'null'}))"
        if name == "float":
            return f"Number({values[0] if values else 'null'})"
        if name == "bool":
            return f"Boolean({values[0] if values else 'null'})"
        if name == "list":
            return f"Array.from({values[0] if values else '[]'})"
        if name == "tuple":
            return f"Array.from({values[0] if values else '[]'})"
        if name == "set":
            return f"new Set({values[0] if values else '[]'})"
        if name == "dict":
            return f"Object.fromEntries({values[0] if values else '[]'})"
        if name == "type":
            return f"typeof ({values[0] if values else 'null'})"
        if name == "repr":
            return f"JSON.stringify({values[0] if values else 'null'})"
        if name == "isinstance":
            return self._isinstance(values, args)
        if name == "range":
            self._use("range")
            return f"range({', '.join(values)})"
        if name == "enumerate":
            self._use("enumerate")
            return f"enumerate({values[0] if values else '[]'})"
        if name == "zip":
            self._use("zip")
            return f"zip({', '.join(values)})"
        if name == "sum":
            self._use("sum")
            return f"sum({values[0] if values else '[]'})"
        if name == "sorted":
            self._use("sorted")
            return f"sorted({values[0] if values else '[]'})"
        if name == "reversed":
            return f"[...{values[0] if values else '[]'}].reverse()"
        if name == "min":
            if len(values) == 1:
                return f"Math.min(...{values[0]})"
            return f"Math.min({', '.join(values)})"
        if name == "max":
            if len(values) == 1:
                return f"Math.max(...{values[0]})"
            return f"Math.max({', '.join(values)})"
        if name == "abs":
            return f"Math.abs({values[0] if values else '0'})"
        if name == "round":
            return f"Math.round({values[0] if values else '0'})"
        return f"{name}({', '.join(values)})"

    def _isinstance(self, values: list[str], args: list[ast.expr]) -> str:
        value = values[0] if values else "null"
        if len(args) < 2:
            return "false"
        target = args[1]
        names: list[str] = []
        if isinstance(target, ast.Tuple):
            names = [self._type_name(item) for item in target.elts]
        else:
            names = [self._type_name(target)]
        checks: list[str] = []
        for name in names:
            lowered = name.lower()
            if lowered in {"str", "string"}:
                checks.append(f'typeof {value} === "string"')
            elif lowered in {"int", "float", "number"}:
                checks.append(f'typeof {value} === "number"')
            elif lowered in {"bool", "boolean"}:
                checks.append(f'typeof {value} === "boolean"')
            elif lowered in {"list", "tuple", "set"}:
                checks.append(f"Array.isArray({value})")
            elif lowered in {"dict", "mapping"}:
                checks.append(
                    f'(typeof {value} === "object" && {value} !== null && !Array.isArray({value}))'
                )
            else:
                checks.append(f"{value} instanceof {name}")
        return "(" + " || ".join(checks) + ")"

    def _type_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Tuple):
            return "tuple"
        return "object"

    def _attribute_call(
        self,
        func: ast.Attribute,
        args: list[ast.expr],
        keywords: dict[str, ast.expr],
    ) -> str:
        receiver_node = func.value
        receiver = self._expr(receiver_node)
        method = func.attr
        values = [self._expr(arg) for arg in args]

        # super().method(...)
        if isinstance(receiver_node, ast.Call) and isinstance(receiver_node.func, ast.Name) and receiver_node.func.id == "super":
            return f"super.{_ts_name(method)}({self._positional(args, keywords)})"

        # Module helpers.
        if isinstance(receiver_node, ast.Name):
            module = receiver_node.id
            if module == "json":
                if method == "dumps":
                    return f"JSON.stringify({values[0] if values else 'null'})"
                if method == "loads":
                    return f"JSON.parse({values[0] if values else 'null'})"
            if module == "requests":
                self.use_axios = True
                self.warn("Python `requests` calls are mapped to axios promises.")
                return self._requests_call(method, args, keywords)
            if module == "math":
                math_map = {
                    "sqrt": "Math.sqrt", "floor": "Math.floor", "ceil": "Math.ceil",
                    "pow": "Math.pow", "log": "Math.log", "log10": "Math.log10",
                    "sin": "Math.sin", "cos": "Math.cos", "tan": "Math.tan",
                    "fabs": "Math.abs", "trunc": "Math.trunc",
                }
                if method in math_map:
                    return f"{math_map[method]}({', '.join(values)})"
            if module == "random":
                if method == "random":
                    return "Math.random()"
                if method == "randint":
                    low = values[0] if values else "0"
                    high = values[1] if len(values) > 1 else "1"
                    return f"(Math.floor(Math.random() * (({high}) - ({low}) + 1)) + ({low}))"
                if method == "choice":
                    source = values[0] if values else "[]"
                    return f"{source}[Math.floor(Math.random() * {source}.length)]"
            if module == "time" and method == "time":
                return "(Date.now() / 1000)"
            if module == "logging":
                return f"console.log({', '.join(values)})"
            if module == "os" and method == "getenv":
                key = values[0] if values else '""'
                fallback = values[1] if len(values) > 1 else "undefined"
                return f"(process.env[{key}] ?? {fallback})"
            if module == "os" and method == "path":
                pass

        # os.path.join
        if (
            isinstance(receiver_node, ast.Attribute)
            and isinstance(receiver_node.value, ast.Name)
            and receiver_node.value.id == "os"
            and receiver_node.attr == "path"
            and method == "join"
        ):
            self.use_path = True
            return f"path.join({', '.join(values)})"

        # self/cls methods.
        if isinstance(receiver_node, ast.Name) and receiver_node.id in {"self", "cls"}:
            if not self.current_class:
                return f"{receiver}.{_ts_name(method)}({self._positional(args, keywords)})"
            safe = _ts_method_name(method)
            if safe in self.current_methods:
                return f"this.{safe}({self._ordered_args(method, args, keywords)})"
            return f"this.{safe}({self._positional(args, keywords)})"

        if (
            isinstance(receiver_node, ast.Name)
            and receiver_node.id in self.http_vars
            and method == "json"
        ):
            return f"{receiver}.data"

        # String/list/dict methods.
        if method == "append":
            return f"({receiver}.push({values[0] if values else 'null'}), {receiver})"
        if method == "extend":
            return f"({receiver}.push(...{values[0] if values else '[]'}), {receiver})"
        if method == "get":
            key = values[0] if values else "null"
            fallback = values[1] if len(values) > 1 else "undefined"
            return f"({receiver}[{key}] ?? {fallback})"
        if method == "keys":
            return f"Object.keys({receiver})"
        if method == "values":
            return f"Object.values({receiver})"
        if method == "items":
            return f"Object.entries({receiver})"
        if method == "strip":
            return f"String({receiver}).trim()"
        if method == "lstrip":
            return f"String({receiver}).trimStart()"
        if method == "rstrip":
            return f"String({receiver}).trimEnd()"
        if method == "lower":
            return f"String({receiver}).toLowerCase()"
        if method == "upper":
            return f"String({receiver}).toUpperCase()"
        if method == "title":
            self._use("title")
            return f"title(String({receiver}))"
        if method == "startswith":
            return f"String({receiver}).startsWith({values[0] if values else '""'})"
        if method == "endswith":
            return f"String({receiver}).endsWith({values[0] if values else '""'})"
        if method == "replace":
            return f"String({receiver}).replace({', '.join(values)})"
        if method == "split":
            return f"String({receiver}).split({values[0] if values else '""'})"
        if method == "join":
            return f"{values[0] if values else '[]'}.join({receiver})"
        if method == "isdigit":
            return f"/^\\d+$/.test(String({receiver}))"
        if method == "isspace":
            return f"/^\\s*$/.test(String({receiver}))"
        if method == "copy":
            return f"[...{receiver}]"
        if method == "update":
            return f"Object.assign({receiver}, {values[0] if values else '{}'})"
        if method == "sort":
            return f"{receiver}.sort()"
        if method == "pop":
            return f"{receiver}.pop()"
        if method == "encode":
            return f"new TextEncoder().encode(String({receiver}))"
        if method == "decode":
            return f"new TextDecoder().decode({receiver})"
        if method == "format":
            self.warn("Python `str.format` calls are emitted as string conversion.")
            return f"String({receiver})"

        return f"{receiver}.{_ts_name(method)}({self._positional(args, keywords)})"

    def _requests_call(self, method: str, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        values = [self._expr(arg) for arg in args]
        url = values[0] if values else '""'
        if method == "get":
            options = self._http_options(keywords)
            return f"await axios.get({url}{', ' + options if options else ''})"
        if method == "post":
            body = "null"
            if "json" in keywords:
                body = self._expr(keywords["json"])
            elif "data" in keywords:
                body = self._expr(keywords["data"])
            elif len(values) > 1:
                body = values[1]
            options = self._http_options(keywords, skip={"json", "data"})
            return f"await axios.post({url}, {body}{', ' + options if options else ''})"
        if method in {"put", "patch"}:
            body = self._expr(keywords.get("json") or keywords.get("data")) if ("json" in keywords or "data" in keywords) else (values[1] if len(values) > 1 else "null")
            options = self._http_options(keywords, skip={"json", "data"})
            return f"await axios.{method}({url}, {body}{', ' + options if options else ''})"
        if method == "delete":
            options = self._http_options(keywords)
            return f"await axios.delete({url}{', ' + options if options else ''})"
        return f"await axios.request({{ method: {json.dumps(method)}, url: {url} }})"

    def _http_options(self, keywords: dict[str, ast.expr], skip: set[str] | None = None) -> str:
        skip = skip or set()
        entries = []
        for key, value in keywords.items():
            if key in skip or key in {"timeout"}:
                continue
            entries.append(f"{key}: {self._expr(value)}")
        if "timeout" in keywords:
            entries.append(f"timeout: Number({self._expr(keywords['timeout'])}) * 1000")
        return "{ " + ", ".join(entries) + " }" if entries else ""

    def _positional(self, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        parts = [self._expr(arg) for arg in args]
        if keywords:
            self.warn("Python keyword arguments are passed positionally in TypeScript.")
            parts.extend(self._expr(value) for value in keywords.values())
        return ", ".join(parts)

    def _ordered_args(self, name: str, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        if not keywords:
            return ", ".join(self._expr(arg) for arg in args)
        params = self.function_params.get(_ts_name(name), [])
        values: list[str] = [self._expr(arg) for arg in args]
        for key, value in keywords.items():
            if key in params:
                index = params.index(key)
                while len(values) <= index:
                    values.append("undefined")
                values[index] = self._expr(value)
            else:
                values.append(self._expr(value))
        return ", ".join(values)

    def _range_parts(self, values: list[str]) -> tuple[str, str, str]:
        if len(values) == 1:
            return "0", values[0], "1"
        if len(values) == 2:
            return values[0], values[1], "1"
        if len(values) >= 3:
            return values[0], values[1], values[2]
        return "0", "0", "1"


__all__ = ["TypeScriptEmitter"]
