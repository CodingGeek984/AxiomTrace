"""Python AST -> Go emitter.

The Go target favours a self-contained, compilable module over static type
inference: values are emitted as ``any`` and Python semantics are delegated to
the runtime helpers in :mod:`services.transpiler.go_runtime`. Classes become
structs with exported fields, methods become pointer-receiver methods and
constructors are derived from ``__init__`` (or dataclass fields).
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from services.transpiler.base import (
    Param,
    PreparedSource,
    TranspileError,
    annotation_text,
    collect_assigned_names,
    extract_docstring,
    filename_stem,
    function_params,
    is_docstring,
    safe_identifier,
    snake_to_pascal,
    uses_await,
    uses_yield,
)
from services.transpiler.go_runtime import GO_IMPORT_REFS, GO_IMPORTS, GO_RUNTIME

_GO_RESERVED = {
    "break", "case", "chan", "const", "continue", "default", "defer", "else",
    "fallthrough", "for", "func", "go", "goto", "if", "import", "interface",
    "map", "package", "range", "return", "select", "struct", "switch", "type",
    "var", "any", "bool", "byte", "complex64", "complex128", "error", "float32",
    "float64", "int", "int8", "int16", "int32", "int64", "rune", "string",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr", "true", "false",
    "iota", "nil", "append", "cap", "close", "complex", "copy", "delete",
    "imag", "len", "make", "new", "panic", "print", "println", "real", "recover",
}

_BINOP_HELPERS = {
    ast.Add: "axiomAdd",
    ast.Sub: "axiomSub",
    ast.Mult: "axiomMul",
    ast.Div: "axiomDiv",
    ast.FloorDiv: "axiomFloorDiv",
    ast.Mod: "axiomMod",
    ast.Pow: "axiomPow",
    ast.LShift: ("axiomBit", "shl"),
    ast.RShift: ("axiomBit", "shr"),
    ast.BitOr: ("axiomBit", "or"),
    ast.BitXor: ("axiomBit", "xor"),
    ast.BitAnd: ("axiomBit", "and"),
}

_COMPARE_HELPERS = {
    ast.Eq: ("axiomEq", False),
    ast.NotEq: ("axiomEq", True),
    ast.Lt: ("axiomLt", False),
    ast.LtE: ("axiomLe", False),
    ast.Gt: ("axiomGt", False),
    ast.GtE: ("axiomGe", False),
    ast.Is: ("axiomEq", False),
    ast.IsNot: ("axiomEq", True),
}

_MATH_FUNCTIONS = {
    "sqrt": "math.Sqrt",
    "floor": "math.Floor",
    "ceil": "math.Ceil",
    "pow": "math.Pow",
    "log": "math.Log",
    "log10": "math.Log10",
    "log2": "math.Log2",
    "exp": "math.Exp",
    "sin": "math.Sin",
    "cos": "math.Cos",
    "tan": "math.Tan",
    "asin": "math.Asin",
    "acos": "math.Acos",
    "atan": "math.Atan",
    "atan2": "math.Atan2",
    "fabs": "math.Abs",
    "trunc": "math.Trunc",
    "hypot": "math.Hypot",
}

_AXIOM_ATTRIBUTES = {
    "math.pi": "math.Pi",
    "math.e": "math.E",
    "math.tau": "(2 * math.Pi)",
    "math.inf": "math.Inf(1)",
    "sys.argv": "os.Args",
    "os.environ": "axiomEnviron()",
}


def _go_var(name: str) -> str:
    safe = safe_identifier(name, fallback="axiom_value")
    if safe in _GO_RESERVED:
        safe = f"{safe}_value"
    return safe


def _go_export(name: str) -> str:
    exported = snake_to_pascal(name)
    if not exported:
        exported = "AxiomNode"
    if exported in _GO_RESERVED:
        exported = f"{exported}Value"
    if exported[0].isdigit():
        exported = f"N{exported}"
    return exported


def _go_type_name(name: str) -> str:
    return _go_export(name)


def _go_method_name(name: str) -> str:
    special = {
        "__init__": "Init",
        "__str__": "String",
        "__repr__": "Repr",
        "__len__": "Len",
        "__eq__": "Equals",
        "__hash__": "Hash",
        "__iter__": "Iter",
        "__next__": "Next",
        "__enter__": "Enter",
        "__exit__": "Exit",
        "__call__": "Call",
    }
    if name in special:
        return special[name]
    return _go_export(name)


class GoEmitter:
    """Emit a self-contained Go module from a prepared Python AST."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.function_names: dict[str, list[str]] = {}
        self.class_names: set[str] = set()
        self.class_fields: dict[str, list[tuple[str, ast.expr | None]]] = {}
        self.class_methods: dict[str, set[str]] = {}
        self.class_static: dict[str, set[str]] = {}
        self.class_properties: dict[str, set[str]] = {}
        self.class_inits: dict[str, ast.FunctionDef] = {}
        self.current_class: str | None = None
        self.current_fields: set[str] = set()
        self.current_methods: set[str] = set()
        self.current_static: set[str] = set()
        self.current_properties: set[str] = set()
        self.declared: set[str] = set()
        self.loop_stack: list[str | None] = []
        self.temp_seq = 0
        self.in_defer = False
        self.generator = False
        self.init_mode = False

    # -- public ------------------------------------------------------
    def emit(self, prepared: PreparedSource, *, label: str | None = None) -> str:
        body = prepared.body
        if not body:
            raise TranspileError("No statements to transpile.", status=422)

        self._collect_definitions(body)

        sections = [self._header(prepared, label), "package axiomtrace", GO_IMPORTS, GO_IMPORT_REFS]
        generated: list[str] = []
        loose: list[ast.stmt] = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                generated.append(self._emit_function(node, 0))
            elif isinstance(node, ast.ClassDef):
                generated.append(self._emit_class(node, 0))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            elif is_docstring(node):
                continue
            else:
                loose.append(node)
        if loose:
            generated.append(self._emit_wrapper(loose, label))
        if generated:
            sections.append("\n\n".join(generated))
        sections.append(GO_RUNTIME.strip())
        return "\n\n".join(section for section in sections if section.strip()).rstrip() + "\n"

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- definitions -------------------------------------------------
    def _collect_definitions(self, body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function_names[_go_export(node.name)] = [param.name for param in function_params(node)]
            elif isinstance(node, ast.ClassDef):
                name = _go_type_name(node.name)
                self.class_names.add(name)
                fields: list[tuple[str, ast.expr | None]] = []
                methods: set[str] = set()
                static: set[str] = set()
                properties: set[str] = set()

                def add_field(raw_name: str, initializer: ast.expr | None = None) -> None:
                    field = _go_export(raw_name)
                    if not any(existing == field for existing, _ in fields):
                        fields.append((field, initializer))

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = _go_method_name(item.name)
                        methods.add(method_name)
                        decorators = {
                            decorator.id
                            for decorator in item.decorator_list
                            if isinstance(decorator, ast.Name)
                        }
                        if "staticmethod" in decorators or "classmethod" in decorators:
                            static.add(method_name)
                        if "property" in decorators:
                            properties.add(method_name)
                        if item.name == "__init__":
                            self.class_inits[name] = item
                        for child in ast.walk(item):
                            if (
                                isinstance(child, ast.Attribute)
                                and isinstance(child.value, ast.Name)
                                and child.value.id in {"self", "cls"}
                                and isinstance(child.ctx, ast.Store)
                            ):
                                add_field(child.attr)
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                add_field(target.id, item.value)
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        add_field(item.target.id, item.value)

                self.class_fields[name] = fields
                self.class_methods[name] = methods
                self.class_static[name] = static
                self.class_properties[name] = properties

    # -- header ------------------------------------------------------
    def _header(self, prepared: PreparedSource, label: str | None) -> str:
        source_lines = len(prepared.source.splitlines())
        node_line = f"\n// Node: {label}" if label else ""
        return (
            "// Code generated by AxiomTrace.\n"
            f"// Source: Python ({source_lines} lines) -> Target: Go.{node_line}\n"
            "// The runtime helpers at the bottom of this file reproduce Python's\n"
            "// dynamic semantics on top of Go's any type."
        )

    # -- functions ---------------------------------------------------
    def _split_params(self, params: list[Param]) -> tuple[list[Param], list[Param], Param | None, Param | None]:
        required: list[Param] = []
        optional: list[Param] = []
        vararg: Param | None = None
        kwarg: Param | None = None
        for param in params:
            if param.kind == "vararg":
                vararg = param
            elif param.kind == "kwarg":
                kwarg = param
            elif param.default is None:
                required.append(param)
            else:
                optional.append(param)
        return required, optional, vararg, kwarg

    def _signature(self, params: list[Param]) -> tuple[str, list[str], list[Param], Param | None]:
        required, optional, vararg, kwarg = self._split_params(params)
        parts = [f"{_go_var(param.name)} any" for param in required]
        rest: list[str] = []
        if optional or vararg or kwarg:
            rest.append("__extra ...any")
        signature = ", ".join(parts + rest)
        setup: list[str] = []
        for index, param in enumerate(optional):
            default = self._expr(param.default) if param.default is not None else "nil"
            setup.append(f"var {_go_var(param.name)} any = {default}")
            setup.append(
                f"if len(__extra) > {index} {{ {_go_var(param.name)} = __extra[{index}] }}"
            )
            setup.append(f"_ = {_go_var(param.name)}")
        if vararg is not None:
            setup.append(f"{_go_var(vararg.name)} := __extra[{len(optional)}:]")
            setup.append(f"_ = {_go_var(vararg.name)}")
        if kwarg is not None:
            setup.append(f"{_go_var(kwarg.name)} := map[string]any{{}}")
            setup.append(f"_ = {_go_var(kwarg.name)}")
        return signature, setup, required, vararg

    def _emit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        level: int,
        *,
        receiver: tuple[str, str] | None = None,
        name: str | None = None,
        closure: bool = False,
    ) -> str:
        docstring, body = extract_docstring(list(node.body))
        params = function_params(node)
        if receiver is not None:
            if params and params[0].name in {"self", "cls"}:
                params = params[1:]
        if self._is_static(node) and params and params[0].name == "cls":
            params = params[1:]

        signature_params, setup, _, _ = self._signature(params)
        is_generator = uses_yield(body)
        is_async = isinstance(node, ast.AsyncFunctionDef) or uses_await(body)
        if is_async and not is_generator:
            self.warn("Python async functions become synchronous Go functions.")
        return_type = "[]any" if is_generator else "any"
        function_name = name or _go_export(node.name)
        indent = "\t" * level

        if closure:
            prefix = f"{indent}{_go_var(function_name)} := func({signature_params}) {return_type} {{"
        else:
            receiver_text = ""
            if receiver is not None:
                receiver_type, receiver_name = receiver
                receiver_text = f"({_go_var(receiver_name)} *{receiver_type}) "
            prefix = f"{indent}func {receiver_text}{function_name}({signature_params}) {return_type} {{"

        lines: list[str] = []
        if docstring and not closure:
            for doc_line in docstring.splitlines():
                lines.append(f"{indent}// {doc_line}")
        lines.append(prefix)
        inner = indent + "\t"
        lines.extend(f"{inner}{line}" for line in setup)

        previous_generator = self.generator
        self.generator = is_generator
        if is_generator:
            lines.append(f"{inner}__yield := []any{{}}")
            lines.append(f"{inner}_ = __yield")
        assigned, _ = collect_assigned_names(body)
        self.declared = set()
        excluded = {_go_var(param.name) for param in params}
        prelude = self._prelude(assigned, excluded, level + 1)
        lines.extend(prelude)
        lines.extend(self._emit_body(body, level + 1))

        if is_generator:
            lines.append(f"{inner}return __yield")
        elif self.init_mode:
            lines.append(f"{inner}return self")
        else:
            lines.append(f"{inner}return nil")
        if closure:
            lines.append(f"{indent}}}")
            lines.append(f"{indent}_ = {_go_var(function_name)}")
        else:
            lines.append(f"{indent}}}")
        self.generator = previous_generator
        return "\n".join(lines)

    def _is_static(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(
            isinstance(decorator, ast.Name)
            and decorator.id in {"staticmethod", "classmethod"}
            for decorator in node.decorator_list
        )

    def _prelude(self, assigned: list[str], excluded: set[str], level: int) -> list[str]:
        indent = "\t" * level
        lines: list[str] = []
        for name in assigned:
            variable = _go_var(name)
            if variable in excluded or variable in {"self", "cls"} or variable.startswith("__"):
                continue
            if variable in self.declared:
                continue
            self.declared.add(variable)
            lines.append(f"{indent}var {variable} any")
            lines.append(f"{indent}_ = {variable}")
        return lines

    def _emit_wrapper(self, body: list[ast.stmt], label: str | None) -> str:
        name = _go_export((label or "axiom_node").replace("def ", "").replace("()", ""))
        params: list[str] = []
        if self._uses_name(body, "self"):
            params.append("self any")
        if self._uses_name(body, "cls"):
            params.append("cls any")
        lines = [f"func {name}({', '.join(params)}) any {{"]
        assigned, _ = collect_assigned_names(body)
        self.declared = set()
        lines.extend(self._prelude(assigned, {"self", "cls"}, 1))
        lines.extend(self._emit_body(body, 1))
        lines.append("\treturn nil")
        lines.append("}")
        return "\n".join(lines)

    def _uses_name(self, body: list[ast.stmt], name: str) -> bool:
        for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
            if isinstance(node, ast.Name) and node.id == name:
                return True
        return False

    # -- classes -----------------------------------------------------
    def _emit_class(self, node: ast.ClassDef, level: int) -> str:
        type_name = _go_type_name(node.name)
        fields = self.class_fields.get(type_name, [])
        methods = self.class_methods.get(type_name, set())
        static = self.class_static.get(type_name, set())
        properties = self.class_properties.get(type_name, set())

        self.current_class = type_name
        self.current_fields = {field for field, _ in fields}
        self.current_methods = methods
        self.current_static = static
        self.current_properties = properties

        indent = "\t" * level
        lines = [f"{indent}type {type_name} struct {{"]
        for field, _ in fields:
            lines.append(f"{indent}\t{field} any")
        if not fields:
            lines.append(f"{indent}\t_ struct{{}}")
        lines.append(f"{indent}}}")

        init = self.class_inits.get(type_name)
        if init is not None:
            docstring, init_body = extract_docstring(list(init.body))
            params = function_params(init)
            if params and params[0].name in {"self", "cls"}:
                params = params[1:]
            signature_params, setup, _, _ = self._signature(params)
            constructor = [f"func New{type_name}({signature_params}) *{type_name} {{"]
            self_ = "\tself := &" + type_name + "{}"
            constructor.append(self_)
            constructor.extend(f"\t{line}" for line in setup)
            self.init_mode = True
            self.generator = False
            self.declared = set()
            previous_fields = self.current_fields
            self.current_fields = {field for field, _ in fields}
            constructor_assigned, _ = collect_assigned_names(init_body)
            constructor.extend(
                self._prelude(
                    constructor_assigned,
                    {_go_var(param.name) for param in params} | {"self", "cls"},
                    1,
                )
            )
            constructor.extend(self._emit_body(init_body, 1))
            constructor.append("\treturn self")
            constructor.append("}")
            self.init_mode = False
            self.current_fields = previous_fields
            lines.append("\n".join(constructor))
        else:
            constructor = [f"func New{type_name}(args ...any) *{type_name} {{", "\tself := &" + type_name + "{}"]
            for index, (field, initializer) in enumerate(fields):
                if initializer is not None:
                    constructor.append(f"\tself.{field} = {self._expr(initializer)}")
                constructor.append(f"\tif len(args) > {index} {{ self.{field} = args[{index}] }}")
            constructor.append("\treturn self")
            constructor.append("}")
            lines.append("\n".join(constructor))

        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name == "__init__":
                continue
            method_name = _go_method_name(item.name)
            if method_name in static:
                lines.append(
                    self._emit_function(
                        item,
                        0,
                        name=f"{type_name}{method_name}",
                        closure=False,
                    )
                )
            else:
                lines.append(
                    self._emit_function(
                        item,
                        0,
                        receiver=(type_name, "self"),
                        name=method_name,
                    )
                )

        self.current_class = None
        self.current_fields = set()
        self.current_methods = set()
        self.current_static = set()
        self.current_properties = set()
        return "\n\n".join(lines)

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
        indent = "\t" * level
        if isinstance(node, ast.Return):
            if self.in_defer:
                if node.value is not None:
                    return f"{indent}_ = {self._expr(node.value)}"
                return f"{indent}_ = struct{{}}{{}}"
            if self.generator:
                return f"{indent}return __yield"
            if self.init_mode:
                return f"{indent}return self"
            if node.value is None:
                return f"{indent}return nil"
            return f"{indent}return {self._expr(node.value)}"
        if isinstance(node, ast.Assign):
            value = self._expr(node.value)
            lines: list[str] = []
            for target in node.targets:
                lines.extend(self._assign_target(target, value, level))
            return "\n".join(lines)
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                return f"{indent}_ = {self._target(node.target)}"
            return "\n".join(
                self._assign_target(node.target, self._expr(node.value), level)
            )
        if isinstance(node, ast.AugAssign):
            return self._emit_augassign(node, level)
        if isinstance(node, ast.If):
            lines = [f"{indent}if axiomTruthy({self._expr(node.test)}) {{"]
            lines.extend(self._emit_body(node.body, level + 1))
            if not node.body:
                lines.append(f"{'\t' * (level + 1)}_ = struct{{}}{{}}")
            if node.orelse:
                if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    nested = self._emit_stmt(node.orelse[0], level)
                    nested_lines = nested.splitlines()
                    if nested_lines:
                        lines.append(f"{indent}}} else " + nested_lines[0][len(indent):])
                        lines.extend(nested_lines[1:])
                else:
                    lines.append(f"{indent}}} else {{")
                    lines.extend(self._emit_body(node.orelse, level + 1))
                    if not node.orelse:
                        lines.append(f"{'\t' * (level + 1)}_ = struct{{}}{{}}")
                    lines.append(f"{indent}}}")
            else:
                lines.append(f"{indent}}}")
            return "\n".join(lines)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return self._emit_for(node, level)
        if isinstance(node, ast.While):
            return self._emit_while(node, level)
        if isinstance(node, ast.Break):
            if self.in_defer:
                return f"{indent}_ = struct{{}}{{}} // break is not valid inside a deferred handler"
            flag = self.loop_stack[-1] if self.loop_stack else None
            if flag:
                return f"{indent}{flag} = true\n{indent}break"
            return f"{indent}break"
        if isinstance(node, ast.Continue):
            if self.in_defer:
                return f"{indent}_ = struct{{}}{{}} // continue is not valid inside a deferred handler"
            return f"{indent}continue"
        if isinstance(node, ast.Try):
            return self._emit_try(node, level)
        if isinstance(node, ast.Raise):
            return self._emit_raise(node, level)
        if isinstance(node, ast.Assert):
            message = self._expr(node.msg) if node.msg else '"Assertion failed"'
            return (
                f"{indent}if !axiomTruthy({self._expr(node.test)}) {{\n"
                f"{'\t' * (level + 1)}panic({message})\n"
                f"{indent}}}"
            )
        if isinstance(node, ast.Expr):
            return self._emit_expr_statement(node, level)
        if isinstance(node, ast.Pass):
            return f"{indent}_ = struct{{}}{{}}"
        if isinstance(node, ast.With):
            return self._emit_with(node, level)
        if isinstance(node, ast.AsyncWith):
            return self._emit_with(node, level, is_async=True)
        if isinstance(node, ast.Delete):
            return self._emit_delete(node, level)
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom)):
            return ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._emit_function(node, level, closure=True)
        if isinstance(node, ast.ClassDef):
            self.warn("Nested Go classes cannot be declared inside functions; the class was omitted.")
            return f"{indent}// nested class {node.name} omitted (Go has no nested types)"
        if isinstance(node, ast.Match):
            return self._emit_match(node, level)
        self.warn(f"Go target skipped unsupported statement `{type(node).__name__}`.")
        return f"{indent}// unsupported statement: {type(node).__name__}"

    def _assign_target(self, target: ast.expr, value: str, level: int) -> list[str]:
        indent = "\t" * level
        if isinstance(target, ast.Name):
            return [f"{indent}{_go_var(target.id)} = {value}"]
        if isinstance(target, ast.Attribute):
            if (
                self.current_class
                and isinstance(target.value, ast.Name)
                and target.value.id in {"self", "cls"}
                and _go_export(target.attr) in self.current_fields
            ):
                return [f"{indent}self.{_go_export(target.attr)} = {value}"]
            receiver = self._expr(target.value)
            return [f"{indent}axiomSetAttr({receiver}, \"{target.attr}\", {value})"]
        if isinstance(target, ast.Subscript):
            key = self._slice_or_index(target.slice)
            if isinstance(target.value, ast.Name):
                container = _go_var(target.value.id)
                return [f"{indent}{container} = axiomSetIndex({container}, {key}, {value})"]
            if (
                self.current_class
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id in {"self", "cls"}
                and _go_export(target.value.attr) in self.current_fields
            ):
                container = f"self.{_go_export(target.value.attr)}"
                return [f"{indent}{container} = axiomSetIndex({container}, {key}, {value})"]
            container = self._expr(target.value)
            return [f"{indent}_ = axiomSetIndex({container}, {key}, {value})"]
        if isinstance(target, (ast.Tuple, ast.List)):
            self.temp_seq += 1
            temporary = f"__tuple{self.temp_seq}"
            lines = [f"{indent}{temporary} := axiomIter({value})"]
            for index, element in enumerate(target.elts):
                if isinstance(element, ast.Starred):
                    element = element.value
                    lines.extend(
                        self._assign_target(
                            element, f"axiomAt({temporary}, {index})", level
                        )
                    )
                else:
                    lines.extend(
                        self._assign_target(
                            element, f"axiomAt({temporary}, {index})", level
                        )
                    )
            return lines
        if isinstance(target, ast.Starred):
            return self._assign_target(target.value, value, level)
        self.warn(f"Go target cannot assign to `{type(target).__name__}`.")
        return [f"{indent}_ = {value}"]

    def _emit_augassign(self, node: ast.AugAssign, level: int) -> str:
        indent = "\t" * level
        op = self._binop_expr(node.op, self._expr(node.target), self._expr(node.value))
        if isinstance(node.target, ast.Name):
            name = _go_var(node.target.id)
            return f"{indent}{name} = {op}"
        if isinstance(node.target, ast.Attribute):
            if (
                self.current_class
                and isinstance(node.target.value, ast.Name)
                and node.target.value.id in {"self", "cls"}
                and _go_export(node.target.attr) in self.current_fields
            ):
                field = f"self.{_go_export(node.target.attr)}"
                return f"{indent}{field} = {self._binop_expr(node.op, field, self._expr(node.value))}"
            return f"{indent}axiomSetAttr({self._expr(node.target.value)}, \"{node.target.attr}\", {op})"
        if isinstance(node.target, ast.Subscript):
            key = self._slice_or_index(node.target.slice)
            container = self._expr(node.target.value)
            return (
                f"{indent}_ = axiomSetIndex({container}, {key}, "
                f"{self._binop_expr(node.op, f'axiomAt({container}, {key})', self._expr(node.value))})"
            )
        self.warn("Go target skipped an unsupported augmented assignment.")
        return f"{indent}// unsupported augmented assignment"

    def _emit_for(self, node: ast.For | ast.AsyncFor, level: int) -> str:
        indent = "\t" * level
        self.temp_seq += 1
        item = f"__item{self.temp_seq}"
        pair = f"__pair{self.temp_seq}"
        flag = f"__broke{self.temp_seq}" if node.orelse else None
        self.loop_stack.append(flag)
        lines: list[str] = []
        if flag:
            lines.append(f"{indent}{flag} := false")
            lines.append(f"{indent}_ = {flag}")
        iterable = node.iter
        if (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Name)
            and iterable.func.id == "range"
            and isinstance(node.target, ast.Name)
        ):
            args = ", ".join(self._expr(arg) for arg in iterable.args)
            lines.append(f"{indent}for _, {item} := range axiomRange({args}) {{")
            lines.extend(self._assign_target(node.target, item, level + 1))
        elif (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Name)
            and iterable.func.id == "enumerate"
            and isinstance(node.target, (ast.Tuple, ast.List))
            and len(node.target.elts) == 2
        ):
            source = self._expr(iterable.args[0]) if iterable.args else "nil"
            lines.append(f"{indent}for _, {pair} := range axiomEnumerate({source}) {{")
            lines.extend(self._assign_target(node.target.elts[0], f"{pair}[0]", level + 1))
            lines.extend(self._assign_target(node.target.elts[1], f"{pair}[1]", level + 1))
        elif (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Name)
            and iterable.func.id == "zip"
            and isinstance(node.target, (ast.Tuple, ast.List))
        ):
            args = ", ".join(self._expr(arg) for arg in iterable.args)
            lines.append(f"{indent}for _, {pair} := range axiomZip({args}) {{")
            for index, element in enumerate(node.target.elts):
                lines.extend(self._assign_target(element, f"{pair}[{index}]", level + 1))
        elif (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Attribute)
            and iterable.func.attr == "items"
            and isinstance(node.target, (ast.Tuple, ast.List))
            and len(node.target.elts) == 2
        ):
            source = self._expr(iterable.func.value)
            lines.append(f"{indent}for _, {pair} := range axiomItems({source}) {{")
            lines.extend(self._assign_target(node.target.elts[0], f"{pair}[0]", level + 1))
            lines.extend(self._assign_target(node.target.elts[1], f"{pair}[1]", level + 1))
        else:
            lines.append(f"{indent}for _, {item} := range axiomIter({self._expr(iterable)}) {{")
            lines.extend(self._assign_target(node.target, item, level + 1))
        lines.extend(self._emit_body(node.body, level + 1))
        if not node.body:
            lines.append(f"{'\t' * (level + 1)}_ = struct{{}}{{}}")
        lines.append(f"{indent}}}")
        self.loop_stack.pop()
        if node.orelse:
            lines.append(f"{indent}if !{flag} {{")
            lines.extend(self._emit_body(node.orelse, level + 1))
            lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _emit_while(self, node: ast.While, level: int) -> str:
        indent = "\t" * level
        self.temp_seq += 1
        flag = f"__broke{self.temp_seq}" if node.orelse else None
        self.loop_stack.append(flag)
        lines: list[str] = []
        if flag:
            lines.append(f"{indent}{flag} := false")
            lines.append(f"{indent}_ = {flag}")
        lines.append(f"{indent}for axiomTruthy({self._expr(node.test)}) {{")
        lines.extend(self._emit_body(node.body, level + 1))
        if not node.body:
            lines.append(f"{'\t' * (level + 1)}_ = struct{{}}{{}}")
        lines.append(f"{indent}}}")
        self.loop_stack.pop()
        if node.orelse:
            lines.append(f"{indent}if !{flag} {{")
            lines.extend(self._emit_body(node.orelse, level + 1))
            lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _emit_try(self, node: ast.Try, level: int) -> str:
        indent = "\t" * level
        self.temp_seq += 1
        ok = f"__ok{self.temp_seq}"
        lines: list[str] = []
        if node.orelse:
            lines.append(f"{indent}{ok} := false")
            lines.append(f"{indent}_ = {ok}")
        lines.append(f"{indent}func() {{")
        if node.handlers:
            handler = node.handlers[0]
            handler_name = _go_var(handler.name or "__error")
            lines.append(f"{indent}\tdefer func() {{")
            lines.append(f"{indent}\t\tif __recovered := recover(); __recovered != nil {{")
            lines.append(f"{indent}\t\t\t{handler_name} := __recovered")
            lines.append(f"{indent}\t\t\t_ = {handler_name}")
            if len(node.handlers) > 1:
                lines.append(
                    f"{indent}\t\t\t// additional handlers folded: "
                    + ", ".join(self._handler_label(item) for item in node.handlers[1:])
                )
                self.warn("Multiple Python exception handlers are folded into one Go recover block.")
            previous_defer = self.in_defer
            self.in_defer = True
            lines.extend(self._emit_body(handler.body, level + 3))
            self.in_defer = previous_defer
            lines.append(f"{indent}\t\t}}")
            lines.append(f"{indent}\t}}()")
        elif node.finalbody:
            lines.append(f"{indent}\tdefer func() {{ _ = recover() }}()")
        lines.extend(self._emit_body(node.body, level + 1))
        if not node.body:
            lines.append(f"{indent}\t_ = struct{{}}{{}}")
        if node.orelse:
            lines.append(f"{indent}\t{ok} = true")
        lines.append(f"{indent}}}()")
        if node.orelse:
            lines.append(f"{indent}if {ok} {{")
            lines.extend(self._emit_body(node.orelse, level + 1))
            lines.append(f"{indent}}}")
        if node.finalbody:
            lines.extend(self._emit_body(node.finalbody, level))
        if node.body and any(isinstance(item, ast.Return) for item in node.body):
            self.warn("A `return` inside Python `try` returns from the generated Go closure, not the enclosing function.")
        return "\n".join(lines)

    def _handler_label(self, handler: ast.ExceptHandler) -> str:
        if handler.type is None:
            return "except"
        try:
            return ast.unparse(handler.type)
        except Exception:  # noqa: BLE001
            return "except"

    def _emit_raise(self, node: ast.Raise, level: int) -> str:
        indent = "\t" * level
        if node.exc is None:
            return f"{indent}panic(\"re-raised Python exception\")"
        if isinstance(node.exc, ast.Call):
            if node.exc.args:
                return f"{indent}panic(axiomError({self._expr(node.exc.args[0])}))"
            return f"{indent}panic(axiomError(\"{self._handler_call_name(node.exc)}\"))"
        return f"{indent}panic(axiomError({self._expr(node.exc)}))"

    def _handler_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return "Error"

    def _emit_with(self, node: ast.With | ast.AsyncWith, level: int, *, is_async: bool = False) -> str:
        indent = "\t" * level
        lines: list[str] = []
        for item in node.items:
            value = self._expr(item.context_expr)
            if item.optional_vars is not None:
                lines.extend(self._assign_target(item.optional_vars, value, level))
            else:
                lines.append(f"{indent}_ = {value}")
        lines.extend(self._emit_body(node.body, level))
        if not node.body:
            lines.append(f"{indent}_ = struct{{}}{{}}")
        self.warn("Python `with` blocks are emitted without synthesised cleanup calls.")
        return "\n".join(lines)

    def _emit_delete(self, node: ast.Delete, level: int) -> str:
        indent = "\t" * level
        lines: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                lines.append(f"{indent}{_go_var(target.id)} = nil")
            elif isinstance(target, ast.Attribute):
                lines.append(
                    f"{indent}axiomSetAttr({self._expr(target.value)}, \"{target.attr}\", nil)"
                )
            elif isinstance(target, ast.Subscript):
                key = self._slice_or_index(target.slice)
                container = self._expr(target.value)
                lines.append(f"{indent}_ = axiomSetIndex({container}, {key}, nil)")
            else:
                lines.append(f"{indent}_ = struct{{}}{{}}")
        return "\n".join(lines)

    def _emit_match(self, node: ast.Match, level: int) -> str:
        indent = "\t" * level
        lines = [f"{indent}switch axiomKey({self._expr(node.subject)}) {{"]
        for case in node.cases:
            patterns: list[str] = []
            fallback = False
            pattern = case.pattern
            if isinstance(pattern, ast.MatchValue):
                patterns.append(self._expr(pattern.value))
            elif isinstance(pattern, ast.MatchSingleton):
                patterns.append("nil" if pattern.value is None else ("true" if pattern.value else "false"))
            elif isinstance(pattern, ast.MatchOr):
                for alternative in pattern.patterns:
                    if isinstance(alternative, ast.MatchValue):
                        patterns.append(self._expr(alternative.value))
                    elif isinstance(alternative, ast.MatchSingleton):
                        patterns.append("nil" if alternative.value is None else ("true" if alternative.value else "false"))
            else:
                fallback = True
            if fallback or not patterns:
                lines.append(f"{indent}default:")
            else:
                lines.append(f"{indent}case {', '.join(patterns)}:")
            if case.guard is not None:
                self.warn("Go match guards are evaluated with the case body rather than the pattern.")
            lines.extend(self._emit_body(case.body, level + 1))
            if not case.body:
                lines.append(f"{'\t' * (level + 1)}_ = struct{{}}{{}}")
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    def _emit_expr_statement(self, node: ast.Expr, level: int) -> str:
        indent = "\t" * level
        value = node.value
        if isinstance(value, ast.Yield):
            if self.generator:
                content = "nil" if value.value is None else self._expr(value.value)
                return f"{indent}__yield = append(__yield, {content})"
            return f"{indent}_ = struct{{}}{{}}"
        if isinstance(value, ast.YieldFrom):
            if self.generator:
                return f"{indent}__yield = append(__yield, axiomIter({self._expr(value.value)})...)"
            return f"{indent}_ = struct{{}}{{}}"
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            receiver = value.func.value
            method = value.func.attr
            args = ", ".join(self._expr(arg) for arg in value.args)
            target = self._mutable_target(receiver)
            if target is not None and method == "append" and len(value.args) == 1:
                return f"{indent}{target} = axiomAppend({target}, {args})"
            if target is not None and method == "extend" and len(value.args) == 1:
                return f"{indent}{target} = axiomExtend({target}, {args})"
            if target is not None and method == "update" and len(value.args) == 1:
                return f"{indent}{target} = axiomMergeDict({target}, {args})"
            if target is not None and method == "sort":
                return f"{indent}{target} = axiomSorted({target}, false)"
            if target is not None and method == "reverse":
                return f"{indent}{target} = axiomReversed({target})"
        return f"{indent}_ = {self._expr(value)}"

    def _mutable_target(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return _go_var(node.id)
        if (
            self.current_class
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "cls"}
            and _go_export(node.attr) in self.current_fields
        ):
            return f"self.{_go_export(node.attr)}"
        return None

    # -- expressions -------------------------------------------------
    def _expr(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            if node.id == "None":
                return "nil"
            if node.id == "True":
                return "true"
            if node.id == "False":
                return "false"
            if node.id == "__name__":
                return '"__main__"'
            if node.id in {"self", "cls"}:
                return node.id
            return _go_var(node.id)
        if isinstance(node, ast.Constant):
            return self._literal(node.value)
        if isinstance(node, ast.BinOp):
            return self._binop_expr(node.op, self._expr(node.left), self._expr(node.right))
        if isinstance(node, ast.BoolOp):
            helper = "axiomAnd" if isinstance(node.op, ast.And) else "axiomOr"
            return f"{helper}({', '.join(self._expr(value) for value in node.values)})"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"axiomNot({self._expr(node.operand)})"
            if isinstance(node.op, ast.USub):
                return f"axiomNeg({self._expr(node.operand)})"
            if isinstance(node.op, ast.UAdd):
                return self._expr(node.operand)
            if isinstance(node.op, ast.Invert):
                return f"axiomBitNot({self._expr(node.operand)})"
            raise TranspileError(
                f"Go target does not support `{type(node.op).__name__}` unary operators.",
                line=getattr(node, "lineno", None),
                status=422,
            )
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                return self._slice_expr(node.value, node.slice)
            return f"axiomAt({self._expr(node.value)}, {self._expr(node.slice)})"
        if isinstance(node, ast.List):
            return self._sequence(node.elts, "list")
        if isinstance(node, ast.Tuple):
            return self._sequence(node.elts, "tuple")
        if isinstance(node, ast.Set):
            return f"axiomSetFrom({', '.join(self._expr(item) for item in node.elts)})"
        if isinstance(node, ast.Dict):
            return self._dict(node)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return self._comprehension(node)
        if isinstance(node, ast.Lambda):
            return self._lambda(node)
        if isinstance(node, ast.IfExp):
            return (
                "func() any { if axiomTruthy("
                + self._expr(node.test)
                + ") { return "
                + self._expr(node.body)
                + " }; return "
                + self._expr(node.orelse)
                + " }()"
            )
        if isinstance(node, ast.JoinedStr):
            return self._fstring(node)
        if isinstance(node, ast.FormattedValue):
            return f"axiomStr({self._expr(node.value)})"
        if isinstance(node, ast.Starred):
            return f"axiomIter({self._expr(node.value)})"
        if isinstance(node, ast.Await):
            self.warn("Go has no async runtime; `await` was emitted synchronously.")
            return self._expr(node.value)
        if isinstance(node, ast.Yield):
            self.warn("A Python yield used as an expression becomes nil.")
            return "nil"
        if isinstance(node, ast.YieldFrom):
            self.warn("A Python `yield from` used as an expression becomes nil.")
            return "nil"
        if isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name):
                return f"axiomNamed(&{_go_var(node.target.id)}, {self._expr(node.value)})"
            self.warn("A walrus assignment to a non-name target was unwrapped.")
            return self._expr(node.value)
        raise TranspileError(
            f"Go target does not support `{type(node).__name__}` expressions.",
            line=getattr(node, "lineno", None),
            status=422,
        )

    def _target(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return _go_var(node.id)
        if isinstance(node, ast.Attribute):
            return f"axiomGetAttr({self._expr(node.value)}, \"{node.attr}\")"
        if isinstance(node, ast.Subscript):
            key = self._slice_or_index(node.slice)
            return f"axiomAt({self._expr(node.value)}, {key})"
        return self._expr(node)

    def _literal(self, value: Any) -> str:
        if value is None:
            return "nil"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, bytes):
            return "[]byte{" + ", ".join(str(byte) for byte in value) + "}"
        if isinstance(value, complex):
            raise TranspileError("Go target does not support complex numbers.", status=422)
        if isinstance(value, float):
            if value != value:
                return "math.NaN()"
            if value == float("inf"):
                return "math.Inf(1)"
            if value == float("-inf"):
                return "math.Inf(-1)"
            return repr(value)
        if value is Ellipsis:
            return "nil"
        if isinstance(value, int):
            return str(value)
        return json.dumps(value)

    def _binop_expr(self, op: ast.operator, left: str, right: str) -> str:
        helper = _BINOP_HELPERS.get(type(op))
        if helper is None:
            raise TranspileError(
                f"Go target does not support `{type(op).__name__}` operators.",
                status=422,
            )
        if isinstance(helper, tuple):
            return f"{helper[0]}(\"{helper[1]}\", {left}, {right})"
        return f"{helper}({left}, {right})"

    def _compare(self, node: ast.Compare) -> str:
        parts: list[str] = []
        left = self._expr(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._expr(comparator)
            if isinstance(op, (ast.In, ast.NotIn)):
                expression = f"axiomContains({right}, {left})"
                if isinstance(op, ast.NotIn):
                    expression = f"!{expression}"
                parts.append(expression)
            else:
                helper = _COMPARE_HELPERS.get(type(op))
                if helper is None:
                    raise TranspileError(
                        f"Go target does not support `{type(op).__name__}` comparisons.",
                        status=422,
                    )
                name, negate = helper
                expression = f"{name}({left}, {right})"
                if negate:
                    expression = f"!{expression}"
                parts.append(expression)
            left = right
        if len(parts) == 1:
            return parts[0]
        return f"axiomAnd({', '.join(parts)})"

    def _slice_or_index(self, node: ast.expr) -> str:
        if isinstance(node, ast.Slice):
            return "nil"
        return self._expr(node)

    def _slice_expr(self, value: ast.expr, node: ast.Slice) -> str:
        start = "nil" if node.lower is None else self._expr(node.lower)
        stop = "nil" if node.upper is None else self._expr(node.upper)
        step = "nil" if node.step is None else self._expr(node.step)
        return f"axiomSlice({self._expr(value)}, {start}, {stop}, {step})"

    def _sequence(self, items: list[ast.expr], kind: str) -> str:
        if any(isinstance(item, ast.Starred) for item in items):
            parts = []
            for item in items:
                if isinstance(item, ast.Starred):
                    parts.append(self._expr(item.value))
                else:
                    parts.append(f"[]any{{{self._expr(item)}}}")
            return f"axiomConcat({', '.join(parts)})"
        return f"[]any{{{', '.join(self._expr(item) for item in items)}}}"

    def _dict(self, node: ast.Dict) -> str:
        pairs: list[str] = []
        spreads: list[str] = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                spreads.append(self._expr(value))
            else:
                pairs.append(self._expr(key))
                pairs.append(self._expr(value))
        base = f"axiomDict({', '.join(pairs)})" if pairs else "axiomDict()"
        for spread in spreads:
            base = f"axiomMergeDict({base}, {spread})"
        return base

    def _attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name):
            base = node.value.id
            qualified = f"{base}.{node.attr}"
            if qualified in _AXIOM_ATTRIBUTES:
                return _AXIOM_ATTRIBUTES[qualified]
            if base in {"self", "cls"} and self.current_class:
                field = _go_export(node.attr)
                if field in self.current_fields:
                    return f"self.{field}"
                if field in self.current_properties:
                    return f"self.{field}()"
                return f"axiomGetAttr(self, \"{node.attr}\")"
            if base == "__name__":
                return '"__main__"'
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "super"
        ):
            self.warn("Python `super()` attribute access is not modelled by the Go target.")
            return "self"
        return f"axiomGetAttr({self._expr(node.value)}, \"{node.attr}\")"

    def _lambda(self, node: ast.Lambda) -> str:
        params = [arg.arg for arg in node.args.args]
        lines = ["func(__args ...any) any {"]
        for index, name in enumerate(params):
            lines.append(f"\t{_go_var(name)} := axiomAt(__args, {index})")
            lines.append(f"\t_ = {_go_var(name)}")
        lines.append(f"\treturn {self._expr(node.body)}")
        lines.append("}")
        return "\n".join(lines)

    def _fstring(self, node: ast.JoinedStr) -> str:
        parts = []
        args = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                if value.format_spec is not None:
                    self.warn("Python f-string format specifiers are not reproduced exactly in Go.")
                parts.append("{}")
                args.append(self._expr(value.value))
        if not args:
            return json.dumps("".join(parts))
        return f"axiomFormat({json.dumps(''.join(parts))}, {', '.join(args)})"

    def _comprehension(self, node: ast.expr) -> str:
        is_dict = isinstance(node, ast.DictComp)
        is_set = isinstance(node, ast.SetComp)
        generators = node.generators
        self.temp_seq += 1
        result = f"__result{self.temp_seq}"
        lines: list[str] = []
        if is_dict:
            lines.append(f"{result} := map[string]any{{}}")
        else:
            lines.append(f"{result} := []any{{}}")
        for target_name in self._comprehension_names(generators):
            lines.append(f"\tvar {target_name} any")
            lines.append(f"\t_ = {target_name}")
        loops = self._comprehension_loops(node, generators, 0, result, 1)
        lines.extend(loops)
        if is_dict:
            lines.append(f"\treturn {result}")
            return "func() map[string]any {\n" + "\n".join(lines) + "\n}()"
        if is_set:
            lines.append(f"\treturn axiomSetFrom({result}...)")
        else:
            lines.append(f"\treturn {result}")
        return "func() []any {\n" + "\n".join(lines) + "\n}()"

    def _comprehension_names(self, generators: list[ast.comprehension]) -> list[str]:
        names: list[str] = []

        def collect(node: ast.expr | None) -> None:
            if node is None:
                return
            if isinstance(node, ast.Name):
                variable = _go_var(node.id)
                if variable not in names:
                    names.append(variable)
            elif isinstance(node, (ast.Tuple, ast.List)):
                for element in node.elts:
                    collect(element)
            elif isinstance(node, ast.Starred):
                collect(node.value)

        for generator in generators:
            collect(generator.target)
        return names

    def _comprehension_loops(
        self,
        node: ast.expr,
        generators: list[ast.comprehension],
        index: int,
        result: str,
        level: int,
    ) -> list[str]:
        if index >= len(generators):
            indent = "\t" * level
            if isinstance(node, ast.DictComp):
                return [
                    f"{indent}{result}[axiomKey({self._expr(node.key)})] = {self._expr(node.value)}"
                ]
            return [f"{indent}{result} = append({result}, {self._expr(node.elt)})"]
        generator = generators[index]
        self.temp_seq += 1
        item = f"__item{self.temp_seq}"
        indent = "\t" * level
        lines = [f"{indent}for _, {item} := range axiomIter({self._expr(generator.iter)}) {{"]
        lines.extend(self._assign_target(generator.target, item, level + 1))
        if generator.ifs:
            condition = " && ".join(f"axiomTruthy({self._expr(test)})" for test in generator.ifs)
            lines.append(f"{'\t' * (level + 1)}if {condition} {{")
            lines.extend(self._comprehension_loops(node, generators, index + 1, result, level + 2))
            lines.append(f"{'\t' * (level + 1)}}}")
        else:
            lines.extend(self._comprehension_loops(node, generators, index + 1, result, level + 1))
        lines.append(f"{indent}}}")
        return lines

    # -- calls -------------------------------------------------------
    def _call(self, node: ast.Call) -> str:
        func = node.func
        args = list(node.args)
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        starred = any(isinstance(arg, ast.Starred) for arg in args)

        if isinstance(func, ast.Name):
            name = func.id
            if name in self._BUILTINS:
                return self._builtin_call(name, args, keywords)
            if _go_type_name(name) in self.class_names:
                if starred:
                    return f"axiomCallValue(New{_go_type_name(name)}, {self._spread_args(args, keywords)}...)"
                return f"New{_go_type_name(name)}({self._call_values(args, keywords)})"
            if _go_export(name) in self.function_names:
                if starred:
                    return f"axiomCallValue({_go_export(name)}, {self._spread_args(args, keywords)}...)"
                return f"{_go_export(name)}({self._call_values(args, keywords)})"
            self.warn(f"External Python symbol `{name}` must exist in the generated Go package.")
            if starred:
                return f"axiomCallValue({_go_export(name)}, {self._spread_args(args, keywords)}...)"
            return f"{_go_export(name)}({self._call_values(args, keywords)})"

        if isinstance(func, ast.Attribute):
            return self._attribute_call(func, args, keywords, starred)

        target = self._expr(func)
        if starred:
            return f"axiomCallValue({target}, {self._spread_args(args, keywords)}...)"
        return f"axiomCallValue({target}, {self._call_values(args, keywords)})"

    _BUILTINS = {
        "len", "str", "int", "float", "bool", "list", "tuple", "set", "dict",
        "type", "repr", "isinstance", "print", "open", "range", "enumerate",
        "zip", "sum", "sorted", "reversed", "min", "max", "abs", "round",
        "map", "filter", "any", "all", "getattr", "setattr", "hasattr",
        "callable", "ord", "chr", "bytes", "format", "input",
    }

    def _call_values(self, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        values: list[str] = []
        for arg in args:
            if isinstance(arg, ast.Starred):
                values.append(f"axiomIter({self._expr(arg.value)})")
            else:
                values.append(self._expr(arg))
        if keywords:
            self.warn("Python keyword arguments are passed positionally in Go.")
            values.extend(self._expr(value) for value in keywords.values())
        return ", ".join(values)

    def _spread_args(self, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        parts: list[str] = []
        for arg in args:
            if isinstance(arg, ast.Starred):
                parts.append(self._expr(arg.value))
            else:
                parts.append(f"[]any{{{self._expr(arg)}}}")
        if keywords:
            self.warn("Python keyword arguments are passed positionally in Go.")
            parts.extend(f"[]any{{{self._expr(value)}}}" for value in keywords.values())
        return f"axiomConcat({', '.join(parts)})" if parts else "[]any{}"

    def _builtin_call(self, name: str, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        values = [self._expr(arg) for arg in args if not isinstance(arg, ast.Starred)]
        starred = [self._expr(arg.value) for arg in args if isinstance(arg, ast.Starred)]
        first = values[0] if values else "nil"

        if name == "len":
            return f"axiomLen({first})"
        if name == "str":
            return f"axiomStr({first})"
        if name == "int":
            return f"axiomInt({first})"
        if name == "float":
            return f"axiomFloat({first})"
        if name == "bool":
            return f"axiomTruthy({first})"
        if name in {"list", "tuple"}:
            return f"axiomIter({first})"
        if name == "set":
            return f"axiomToSet({first})"
        if name == "dict":
            return f"axiomToDict({first})"
        if name == "bytes":
            return f"[]byte(axiomStr({first}))"
        if name == "type":
            return f"axiomType({first})"
        if name == "repr":
            return f"axiomStr({first})"
        if name == "format":
            if len(values) > 1:
                return f"axiomFormat(axiomStr({values[1]}), {values[0]})"
            return f"axiomStr({first})"
        if name == "isinstance":
            targets = []
            if len(args) > 1:
                target = args[1]
                elements = target.elts if isinstance(target, ast.Tuple) else [target]
                for element in elements:
                    if isinstance(element, ast.Name):
                        targets.append(json.dumps(element.id))
                    else:
                        targets.append(json.dumps(self._type_name(element)))
            return f"axiomIsInstance({first}, {', '.join(targets)})"
        if name == "print":
            return f"axiomPrint({', '.join(values)})"
        if name == "open":
            return f"axiomReadFile({first})"
        if name == "range":
            return f"axiomRange({', '.join(values)})"
        if name == "enumerate":
            return f"axiomEnumerate({first})"
        if name == "zip":
            return f"axiomZip({', '.join(values)})"
        if name == "sum":
            return f"axiomSum({first})"
        if name == "sorted":
            reverse = "false"
            if "reverse" in keywords:
                reverse = self._expr(keywords["reverse"])
            return f"axiomSorted({first}, {reverse})"
        if name == "reversed":
            return f"axiomReversed({first})"
        if name in {"min", "max"}:
            return f"axiom{name.capitalize()}({', '.join(values)})"
        if name == "abs":
            return f"axiomAbs({first})"
        if name == "round":
            return f"axiomRound({first})"
        if name == "map":
            return f"axiomMap({', '.join(values)})"
        if name == "filter":
            return f"axiomFilter({', '.join(values)})"
        if name == "any":
            return f"axiomAny({first})"
        if name == "all":
            return f"axiomAll({first})"
        if name == "getattr":
            fallback = values[2] if len(values) > 2 else ""
            if fallback:
                return f"axiomGetAttr({values[0]}, axiomStr({values[1]}))"
            return f"axiomGetAttr({values[0]}, axiomStr({values[1]}))"
        if name == "setattr":
            return f"axiomSetAttr({values[0]}, axiomStr({values[1]}), {values[2] if len(values) > 2 else 'nil'})"
        if name == "hasattr":
            return f"(axiomGetAttr({values[0]}, axiomStr({values[1]})) != nil)"
        if name == "callable":
            return f"axiomCallable({first})"
        if name == "ord":
            return f"int64([]rune(axiomStr({first}))[0])"
        if name == "chr":
            return f"string(rune(axiomInt({first})))"
        if name == "input":
            return 'axiomStr(axiomReadFile("/dev/stdin"))'
        return f"{_go_export(name)}({', '.join(values)})"

    def _type_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, (ast.Tuple, ast.List)):
            return "tuple"
        return "object"

    def _attribute_call(
        self,
        func: ast.Attribute,
        args: list[ast.expr],
        keywords: dict[str, ast.expr],
        starred: bool,
    ) -> str:
        receiver_node = func.value
        method = func.attr
        receiver = self._expr(receiver_node)
        values = [self._expr(arg) for arg in args if not isinstance(arg, ast.Starred)]
        spread = [self._expr(arg.value) for arg in args if isinstance(arg, ast.Starred)]
        first = values[0] if values else "nil"

        if isinstance(receiver_node, ast.Name):
            module = receiver_node.id
            if module == "json":
                if method == "dumps":
                    return f"axiomJSONDumps({first})"
                if method == "loads":
                    return f"axiomJSONLoads({first})"
            if module == "math":
                if method in {"isnan"}:
                    return f"math.IsNaN(axiomFloat({first}))"
                if method in {"isinf"}:
                    return f"math.IsInf(axiomFloat({first}), 0)"
                if method in _MATH_FUNCTIONS:
                    return f"{_MATH_FUNCTIONS[method]}({', '.join(f'axiomFloat({value})' for value in values)})"
            if module == "random":
                if method == "random":
                    return "axiomRandom()"
                if method == "randint":
                    return f"axiomRandint({', '.join(values)})"
                if method == "choice":
                    return f"axiomChoice({first})"
                if method == "shuffle":
                    self.warn("Python `random.shuffle` mutates lists in place; Go output returns a new list.")
                    return f"axiomReversed({first})"
            if module == "time":
                if method == "time":
                    return "axiomTime()"
                if method == "sleep":
                    return f"axiomSleep({first})"
            if module == "os":
                if method == "getenv":
                    if len(values) > 1:
                        return f"axiomGetenv({values[0]}, {values[1]})"
                    return f"axiomGetenv({first}, \"\")"
                if method == "makedirs":
                    return f"axiomMkdirs({first})"
            if module == "logging":
                return f"axiomPrint({', '.join(values)})"
            if module == "sys" and method == "exit":
                return f"os.Exit(int(axiomInt({first})))"
            if module in {"requests", "httpx"}:
                return self._requests_call(method, args, keywords)

        if (
            isinstance(receiver_node, ast.Attribute)
            and isinstance(receiver_node.value, ast.Name)
            and receiver_node.value.id == "os"
            and receiver_node.attr == "path"
            and method == "join"
        ):
            return f"axiomPathJoin({', '.join(values)})"

        if (
            isinstance(receiver_node, ast.Name)
            and receiver_node.id in {"self", "cls"}
            and self.current_class
        ):
            method_name = _go_method_name(method)
            if method_name in self.current_static:
                return f"{self.current_class}{method_name}({self._call_values(args, keywords)})"
            if method_name in self.current_methods:
                if starred:
                    return f"axiomCallMethod(self, \"{method}\", {self._spread_args(args, keywords)}...)"
                return f"self.{method_name}({self._call_values(args, keywords)})"
            if starred:
                return f"axiomCallMethod(self, \"{method}\", {self._spread_args(args, keywords)}...)"
            return f"axiomCallMethod(self, \"{method}\", {self._call_values(args, keywords)})"

        if (
            isinstance(receiver_node, ast.Name)
            and _go_type_name(receiver_node.id) in self.class_names
            and method in self._class_static_methods(_go_type_name(receiver_node.id))
        ):
            return f"{_go_type_name(receiver_node.id)}{_go_method_name(method)}({self._call_values(args, keywords)})"

        if method == "append":
            self.warn("`append()` used as an expression is returned as a new value in Go.")
        if starred:
            return f"axiomCallMethod({receiver}, \"{method}\", {self._spread_args(args, keywords)}...)"
        return f"axiomCallMethod({receiver}, \"{method}\", {self._call_values(args, keywords)})"

    def _class_static_methods(self, class_name: str) -> set[str]:
        return {
            _go_method_name(method)
            for method in self.class_static.get(class_name, set())
        }

    def _requests_call(self, method: str, args: list[ast.expr], keywords: dict[str, ast.expr]) -> str:
        values = [self._expr(arg) for arg in args if not isinstance(arg, ast.Starred)]
        url = values[0] if values else '""'
        body = "nil"
        if method == "post" or method in {"put", "patch"}:
            if "json" in keywords:
                body = self._expr(keywords["json"])
            elif "data" in keywords:
                body = self._expr(keywords["data"])
            elif len(values) > 1:
                body = values[1]
        options = "nil"
        option_pairs: list[str] = []
        if "headers" in keywords:
            option_pairs.append(f"\"headers\", {self._expr(keywords['headers'])}")
        if "timeout" in keywords:
            option_pairs.append(f"\"timeout\", {self._expr(keywords['timeout'])}")
        if option_pairs:
            options = f"axiomDict({', '.join(option_pairs)})"
        self.warn("Python `requests` calls are mapped to Go net/http helpers.")
        return f"axiomHTTP(\"{method}\", {url}, {body}, {options})"


