"""Shared language traits, type mapping and helpers for the polyglot emitter."""

from __future__ import annotations

import ast
import re

TARGETS = (
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

LINE_COMMENT = {
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

CPP_INCLUDES = (
    "#include <algorithm>",
    "#include <any>",
    "#include <cassert>",
    "#include <cmath>",
    "#include <cstdlib>",
    "#include <ctime>",
    "#include <fstream>",
    "#include <initializer_list>",
    "#include <iomanip>",
    "#include <iostream>",
    "#include <limits>",
    "#include <map>",
    "#include <numeric>",
    "#include <optional>",
    "#include <set>",
    "#include <sstream>",
    "#include <stdexcept>",
    "#include <string>",
    "#include <thread>",
    "#include <tuple>",
    "#include <utility>",
    "#include <vector>",
)

JAVA_IMPORTS = (
    "import java.util.*;",
    "import java.util.stream.Collectors;",
)

CSHARP_IMPORTS = (
    "using System;",
    "using System.Collections.Generic;",
    "using System.Linq;",
)

_PRIMITIVE_TYPES = {
    "int": {
        "python": "int", "javascript": "number", "typescript": "number",
        "java": "int", "csharp": "int", "cpp": "int", "rust": "i64",
        "go": "int", "php": "int",
    },
    "float": {
        "python": "float", "javascript": "number", "typescript": "number",
        "java": "double", "csharp": "double", "cpp": "double", "rust": "f64",
        "go": "float64", "php": "float",
    },
    "str": {
        "python": "str", "javascript": "string", "typescript": "string",
        "java": "String", "csharp": "string", "cpp": "std::string",
        "rust": "String", "go": "string", "php": "string",
    },
    "bool": {
        "python": "bool", "javascript": "boolean", "typescript": "boolean",
        "java": "boolean", "csharp": "bool", "cpp": "bool", "rust": "bool",
        "go": "bool", "php": "bool",
    },
    "bytes": {
        "python": "bytes", "javascript": "Uint8Array", "typescript": "Uint8Array",
        "java": "byte[]", "csharp": "byte[]", "cpp": "std::vector<std::uint8_t>",
        "rust": "Vec<u8>", "go": "[]byte", "php": "string",
    },
    "none": {
        "python": "None", "javascript": "null", "typescript": "null",
        "java": "void", "csharp": "void", "cpp": "void", "rust": "()",
        "go": "", "php": "null",
    },
    "object": {
        "python": "object", "javascript": "any", "typescript": "unknown",
        "java": "Object", "csharp": "object", "cpp": "std::any",
        "rust": "String", "go": "any", "php": "mixed",
    },
    "list": {
        "python": "list", "javascript": "any[]", "typescript": "any[]",
        "java": "List<Object>", "csharp": "List<object>",
        "cpp": "std::vector<std::any>", "rust": "Vec<i64>",
        "go": "[]any", "php": "array",
    },
    "dict": {
        "python": "dict", "javascript": "Record<string, any>",
        "typescript": "Record<string, any>", "java": "Map<String, Object>",
        "csharp": "Dictionary<string, object>",
        "cpp": "std::map<std::string, std::any>",
        "rust": "std::collections::HashMap<String, i64>",
        "go": "map[string]any", "php": "array",
    },
    "set": {
        "python": "set", "javascript": "Set<any>", "typescript": "Set<any>",
        "java": "Set<Object>", "csharp": "HashSet<object>",
        "cpp": "std::set<std::any>", "rust": "std::collections::HashSet<i64>",
        "go": "map[any]struct{}", "php": "array",
    },
    "tuple": {
        "python": "tuple", "javascript": "any[]", "typescript": "any[]",
        "java": "List<Object>", "csharp": "object[]",
        "cpp": "std::vector<std::any>", "rust": "(i64, i64)",
        "go": "[]any", "php": "array",
    },
}

_JAVA_BOX = {
    "int": "Integer", "long": "Long", "double": "Double", "float": "Float",
    "boolean": "Boolean", "char": "Character", "byte": "Byte", "short": "Short",
}

_NONE_LITERAL = {
    "python": "None", "javascript": "null", "typescript": "null",
    "java": "null", "csharp": "null", "cpp": "nullptr", "rust": "None",
    "go": "nil", "php": "null",
}

_BOOL_LITERAL = {
    "python": ("True", "False"), "javascript": ("true", "false"),
    "typescript": ("true", "false"), "java": ("true", "false"),
    "csharp": ("true", "false"), "cpp": ("true", "false"),
    "rust": ("true", "false"), "go": ("true", "false"),
    "php": ("true", "false"),
}


def _normalise_type_name(name: str) -> str:
    """Collapse ``typing`` aliases and dotted names to the canonical base."""
    base = (name or "").strip()
    if "." in base:
        base = base.rsplit(".", 1)[-1]
    aliases = {
        "List": "list", "Dict": "dict", "Set": "set", "Tuple": "tuple",
        "Optional": "optional", "Union": "union", "Any": "object",
        "AnyStr": "str", "Text": "str", "String": "str", "Integer": "int",
        "Long": "int", "Float": "float", "Double": "float", "Boolean": "bool",
        "ByteString": "bytes", "NoneType": "none", "None": "none",
        "Sequence": "list", "Iterable": "list", "MutableSequence": "list",
        "Mapping": "dict", "MutableMapping": "dict", "AbstractSet": "set",
    }
    return aliases.get(base, base)


def _box_java(value: str) -> str:
    return _JAVA_BOX.get(value, value)


def reassigned_names(body: list[ast.stmt]) -> set[str]:
    """Names stored more than once in a scope (used for Rust ``let mut``)."""
    counts: dict[str, int] = {}
    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            counts[node.id] = counts.get(node.id, 0) + 1
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            counts[node.target.id] = counts.get(node.target.id, 0) + 1
    return {name for name, count in counts.items() if count > 1}


class EmitterBase:
    """State and language primitives shared by the expression/statement mixins."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.warnings: list[str] = []
        self.declared_stack: list[set[str]] = [set()]
        self.function_names: set[str] = set()
        self.class_names: set[str] = set()
        self.go_imports: set[str] = set()
        self.class_fields: dict[str, list[tuple[str, str | None, ast.expr | None]]] = {}
        self.class_methods: dict[str, set[str]] = {}
        self.current_class: str | None = None
        self.current_class_fields: dict[str, str] = {}
        self.current_method_static = False
        self._go_function_names: dict[str, str] = {}
        self._go_method_names: dict[str, dict[str, str]] = {}
        self._go_field_names: dict[str, dict[str, str]] = {}
        self.var_types_stack: list[dict[str, str]] = [{}]
        self.param_types: dict[str, str] = {}
        self.function_returns: dict[str, str] = {}
        self.in_main = False
        self.self_alias: str | None = None
        self.extra_imports: set[str] = set()
        self.decl_method_names: dict[str, dict[str, str]] = {}
        self.decl_field_names: dict[str, dict[str, str]] = {}
        self.property_methods: set[tuple[str, str]] = set()
        # Python function/method name -> target-language name.  Both maps are
        # filled during the pre-scan so call sites can use the same spelling
        # as the declaration.
        self.function_target_names: dict[str, str] = {}
        self.method_target_names: dict[str, str] = {}
        self.method_defaults: dict[str, dict] = {}
        self.constructor_defaults: dict[str, dict] = {}
        self.class_field_annotations: dict[str, dict[str, ast.expr | None]] = {}
        self.module_constants: list[ast.stmt] = []
        self.module_constant_names: set[str] = set()
        self.entry_class_name: str | None = None
        # Go has no default arguments.  Defaulted parameters are emitted as
        # trailing variadics and references resolve through a small generic
        # helper; ``go_default_exprs`` maps the Python parameter name to the
        # resolved expression for the current function.
        self.go_default_exprs: dict[str, str] = {}
        self.go_helpers: set[str] = set()
        self.reassigned_names: set[str] = set()

    # -- warnings & scopes -------------------------------------------
    def warn(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)

    @property
    def declared(self) -> set[str]:
        return self.declared_stack[-1]

    @property
    def var_types(self) -> dict[str, str]:
        return self.var_types_stack[-1]

    def push_scope(self) -> None:
        self.declared_stack.append(set())
        self.var_types_stack.append({})

    def pop_scope(self) -> None:
        if len(self.declared_stack) > 1:
            self.declared_stack.pop()
        if len(self.var_types_stack) > 1:
            self.var_types_stack.pop()

    def declare(self, name: str, type_name: str | None = None) -> None:
        self.declared.add(name)
        if type_name:
            self.var_types[name] = type_name

    def var_type(self, name: str) -> str | None:
        for scope in reversed(self.var_types_stack):
            if name in scope:
                return scope[name]
        return self.param_types.get(name)

    # -- language primitives -----------------------------------------
    @property
    def is_dynamic(self) -> bool:
        return self.target in {"javascript", "typescript", "php"}

    @property
    def line_comment(self) -> str:
        return LINE_COMMENT.get(self.target, "//")

    def indent(self, level: int) -> str:
        return "    " * max(0, level)

    def semi(self) -> str:
        return "" if self.target in {"go", "python"} else ";"

    def none(self) -> str:
        return _NONE_LITERAL.get(self.target, "null")

    def bool_literal(self, value: bool) -> str:
        true_lit, false_lit = _BOOL_LITERAL.get(self.target, ("true", "false"))
        return true_lit if value else false_lit

    def string(self, value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        if self.target == "php":
            escaped = escaped.replace("$", "\\$")
        return f'"{escaped}"'

    def comment(self, text: str) -> str:
        return f"{self.line_comment} {text}".rstrip()

    def go_export(self, name: str) -> str:
        """Return an exported Go identifier (``discounted_total`` -> ``DiscountedTotal``)."""
        if not name:
            return name
        parts = [part for part in re.split(r"_+", name) if part]
        if not parts:
            return name
        return "".join(part[:1].upper() + part[1:] for part in parts)

    def function_target(self, name: str) -> str:
        return self.function_target_names.get(name, name)

    def method_target(self, name: str) -> str:
        return self.method_target_names.get(name, name)

    def go_field(self, owner: str | None, name: str) -> str:
        if owner and owner in self._go_field_names:
            return self._go_field_names[owner].get(name, self.go_export(name))
        return self.go_export(name)

    def go_method(self, owner: str | None, name: str) -> str:
        if owner and owner in self._go_method_names:
            return self._go_method_names[owner].get(name, self.go_export(name))
        return self.go_export(name)

    # -- type mapping ------------------------------------------------
    def annotation_text(self, node: ast.expr | None) -> str | None:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:  # noqa: BLE001 - best effort
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                return node.attr
            return None

    def map_type(self, node: ast.expr | None, *, returns: bool = False) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            if node.value is None:
                return self.primitive("none", returns=returns)
            return None
        if isinstance(node, ast.Name):
            return self._map_base(node.id, [])
        if isinstance(node, ast.Attribute):
            return self._map_base(node.attr, [])
        if isinstance(node, ast.Subscript):
            base = self._base_name(node.value)
            args = self._subscript_args(node.slice)
            return self._map_base(base, args)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # PEP 604: X | None / X | Y
            members = self._flatten_union(node)
            non_none = [m for m in members if not (
                isinstance(m, ast.Constant) and m.value is None
            )]
            if len(non_none) == 1:
                inner = self.map_type(non_none[0], returns=returns) or "object"
                return self._optional(inner)
            mapped = [self.map_type(m, returns=returns) for m in non_none]
            mapped = [m for m in mapped if m]
            if self.target in {"javascript", "typescript"}:
                return " | ".join(mapped) if mapped else "unknown"
            return mapped[0] if mapped else "object"
        if isinstance(node, ast.Tuple):
            mapped = [self.map_type(item) or "object" for item in node.elts]
            return self._map_tuple(mapped)
        return None

    def primitive(self, name: str, *, returns: bool = False) -> str:
        name = _normalise_type_name(name)
        if name == "none":
            if self.target == "rust":
                return "()"
            if self.target == "go":
                return ""
            if self.target in {"java", "csharp", "cpp"}:
                return "void"
            return _NONE_LITERAL.get(self.target, "null")
        mapping = _PRIMITIVE_TYPES.get(name, _PRIMITIVE_TYPES["object"])
        return mapping.get(self.target, "object")

    def _base_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return "object"

    def _subscript_args(self, node: ast.expr) -> list[ast.expr]:
        if isinstance(node, ast.Tuple):
            return list(node.elts)
        if isinstance(node, ast.Index):  # pragma: no cover - Python < 3.9
            return [node.value]
        return [node]

    def _flatten_union(self, node: ast.expr) -> list[ast.expr]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._flatten_union(node.left) + self._flatten_union(node.right)
        return [node]

    def _optional(self, inner: str) -> str:
        if self.target in {"javascript", "typescript"}:
            return f"{inner} | null"
        if self.target == "java":
            return inner
        if self.target == "csharp":
            return f"{inner}?"
        if self.target == "cpp":
            return f"std::optional<{inner}>"
        if self.target == "rust":
            return f"Option<{inner}>"
        if self.target == "go":
            return f"*{inner}" if inner not in {"any", ""} else "any"
        if self.target == "php":
            return f"?{inner}"
        return inner

    def _map_tuple(self, items: list[str]) -> str:
        if self.target == "typescript":
            return "[" + ", ".join(items) + "]"
        if self.target == "java":
            return "Object[]"
        if self.target == "csharp":
            return "object[]"
        if self.target == "cpp":
            return "std::tuple<" + ", ".join(items) + ">"
        if self.target == "rust":
            return "(" + ", ".join(items) + ")"
        if self.target == "go":
            return "[]any"
        if self.target == "php":
            return "array"
        return "any[]"

    def _map_base(self, base: str, args: list[ast.expr]) -> str:
        name = _normalise_type_name(base)
        if not args and name in {"list", "dict", "set", "tuple"}:
            return self.primitive(name)
        mapped_args = [self.map_type(arg) or "object" for arg in args]

        if name == "optional":
            inner = mapped_args[0] if mapped_args else "object"
            return self._optional(inner)
        if name == "union":
            if len(mapped_args) == 2 and mapped_args[1] == self.primitive("none"):
                return self._optional(mapped_args[0])
            if self.target in {"javascript", "typescript"}:
                return " | ".join(mapped_args) if mapped_args else "unknown"
            return mapped_args[0] if mapped_args else "object"
        if name == "list":
            inner = mapped_args[0] if mapped_args else "object"
            if self.target == "typescript":
                return f"{inner}[]"
            if self.target == "java":
                return f"List<{_box_java(inner)}>"
            if self.target == "csharp":
                return f"List<{inner}>"
            if self.target == "cpp":
                return f"std::vector<{inner}>"
            if self.target == "rust":
                return f"Vec<{inner}>"
            if self.target == "go":
                return f"[]{inner}"
            if self.target == "php":
                return "array"
            if self.target == "javascript":
                return "any[]"
            return f"list[{inner}]"
        if name == "dict":
            key = mapped_args[0] if mapped_args else self.primitive("str")
            value = mapped_args[1] if len(mapped_args) > 1 else self.primitive("object")
            if self.target in {"javascript", "typescript"}:
                return f"Record<{key if key != 'any' else 'string'}, {value}>"
            if self.target == "java":
                return f"Map<{_box_java(key)}, {_box_java(value)}>"
            if self.target == "csharp":
                return f"Dictionary<{key}, {value}>"
            if self.target == "cpp":
                return f"std::map<{key}, {value}>"
            if self.target == "rust":
                return f"std::collections::HashMap<{key}, {value}>"
            if self.target == "go":
                return f"map[{key}]{value}"
            if self.target == "php":
                return "array"
            return "dict"
        if name == "set":
            inner = mapped_args[0] if mapped_args else "object"
            if self.target == "typescript":
                return f"Set<{inner}>"
            if self.target == "java":
                return f"Set<{_box_java(inner)}>"
            if self.target == "csharp":
                return f"HashSet<{inner}>"
            if self.target == "cpp":
                return f"std::set<{inner}>"
            if self.target == "rust":
                return f"std::collections::HashSet<{inner}>"
            if self.target == "go":
                return "map[any]struct{}"
            if self.target == "php":
                return "array"
            return "Set<any>"
        if name == "tuple":
            return self._map_tuple(mapped_args or ["object"])
        if name in _PRIMITIVE_TYPES:
            return self.primitive(name)
        if not mapped_args:
            return name
        # Generic user type: keep it for languages that support generics.
        if self.target == "typescript":
            return f"{name}<{', '.join(mapped_args)}>"
        if self.target == "java":
            return f"{name}<{', '.join(_box_java(a) for a in mapped_args)}>"
        if self.target == "csharp":
            return f"{name}<{', '.join(mapped_args)}>"
        if self.target == "cpp":
            return f"{name}<{', '.join(mapped_args)}>"
        if self.target == "rust":
            return f"{name}<{', '.join(mapped_args)}>"
        return name

    def py_type_to_target(self, py_type: str | None, *, returns: bool = False) -> str | None:
        if not py_type:
            return None
        try:
            node = ast.parse(py_type, mode="eval").body
        except SyntaxError:
            node = ast.Name(id=py_type)
        return self.map_type(node, returns=returns)

    # -- lightweight type inference ---------------------------------
    def py_type_of(self, node: ast.AST | None) -> str | None:
        """Best-effort canonical Python type ("int", "str", "list", ...)."""
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None:
                return "none"
            if isinstance(value, bool):
                return "bool"
            if isinstance(value, int):
                return "int"
            if isinstance(value, float):
                return "float"
            if isinstance(value, str):
                return "str"
            if isinstance(value, bytes):
                return "bytes"
            return None
        if isinstance(node, ast.JoinedStr):
            return "str"
        if isinstance(node, ast.List):
            return "list"
        if isinstance(node, ast.Tuple):
            return "tuple"
        if isinstance(node, ast.Set):
            return "set"
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, ast.Compare):
            return "bool"
        if isinstance(node, ast.BoolOp):
            return "bool"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "bool"
            return self.py_type_of(node.operand)
        if isinstance(node, ast.IfExp):
            return self.py_type_of(node.body) or self.py_type_of(node.orelse)
        if isinstance(node, ast.Name):
            return self.var_type(node.id)
        if isinstance(node, ast.Attribute):
            value = self.expr(node.value)
            if value in {"self", "this", "$this"} and self.current_class:
                return self.current_class_fields.get(node.attr)
            if node.attr in {"real", "imag"}:
                return "float"
            return None
        if isinstance(node, ast.Subscript):
            container = self.py_type_of(node.value)
            if container == "str":
                return "str"
            if container in {"list", "tuple"}:
                return None
            if container == "dict":
                return None
            return None
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                return "float"
            if isinstance(node.op, ast.FloorDiv):
                return "int"
            if isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                return "str"
            left = self.py_type_of(node.left)
            right = self.py_type_of(node.right)
            if isinstance(node.op, ast.Add) and "str" in {left, right}:
                return "str"
            if isinstance(node.op, ast.Mult) and "str" in {left, right}:
                return "str"
            if left == "float" or right == "float":
                return "float"
            if left == "int" and right == "int":
                return "int"
            return None
        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            if name in self.class_names:
                return name
            if name in {"int", "len", "ord", "round"}:
                return "int"
            if name in {"float"}:
                return "float"
            if name in {"str", "repr", "chr", "input", "format"}:
                return "str"
            if name == "bool":
                return "bool"
            if name in {"list", "sorted", "reversed", "range", "enumerate", "zip"}:
                return "list"
            if name == "dict":
                return "dict"
            if name == "set":
                return "set"
            if name == "tuple":
                return "tuple"
            if name == "abs":
                inner = self.py_type_of(node.args[0]) if node.args else None
                return inner or "float"
            if name in {"min", "max", "sum"}:
                return "float"
            if name in {"isinstance"}:
                return "bool"
            if name in {"type"}:
                return "str"
            if name and name in self.function_returns:
                return self.function_returns[name]
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in {"upper", "lower", "strip", "lstrip", "rstrip", "replace", "join", "title", "capitalize", "format", "decode"}:
                    return "str"
                if method in {"split", "rsplit", "keys", "values", "items"}:
                    return "list"
                if method == "get":
                    return None
                if method in {"copy"}:
                    return self.py_type_of(node.func.value)
                if method in {"count", "index", "find"}:
                    return "int"
            return None
        if isinstance(node, ast.NamedExpr):
            return self.py_type_of(node.value)
        if isinstance(node, ast.Await):
            return self.py_type_of(node.value)
        return None

    def target_type_of(self, node: ast.AST | None, default: str | None = None) -> str:
        kind = self.py_type_of(node)
        if kind:
            mapped = self.py_type_to_target(kind)
            if mapped:
                return mapped
        return default if default is not None else self.primitive("object")

    def infer_return_kind(self, body: list[ast.stmt]) -> str | None:
        kinds: list[str] = []
        for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
            if isinstance(node, ast.Return) and node.value is not None:
                kind = self.py_type_of(node.value)
                if kind and kind != "none":
                    kinds.append(kind)
        if not kinds:
            return None
        if "str" in kinds:
            return "str"
        if "float" in kinds:
            return "float"
        if "dict" in kinds:
            return "dict"
        if "list" in kinds or "tuple" in kinds or "set" in kinds:
            return "list"
        if "bool" in kinds:
            return "bool"
        if "int" in kinds:
            return "int"
        return kinds[0]

    def infer_param_kind(self, function: ast.AST, name: str) -> str | None:
        """Heuristically infer an unannotated parameter's Python type."""
        kind: str | None = None
        for node in ast.walk(function):
            if isinstance(node, ast.JoinedStr):
                if any(
                    isinstance(part, ast.FormattedValue)
                    and isinstance(part.value, ast.Name)
                    and part.value.id == name
                    for part in node.values
                ):
                    return "str"
            if isinstance(node, ast.BinOp):
                sides = (node.left, node.right)
                if any(isinstance(side, ast.Name) and side.id == name for side in sides):
                    other = node.right if isinstance(node.left, ast.Name) and node.left.id == name else node.left
                    other_kind = self.py_type_of(other)
                    if isinstance(node.op, ast.Add) and other_kind == "str":
                        return "str"
                    if isinstance(node.op, ast.Mult) and other_kind == "str":
                        return "str"
                    if isinstance(node.op, ast.Div):
                        kind = kind or "float"
                    elif isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Mod, ast.FloorDiv, ast.Pow)):
                        if other_kind == "float":
                            kind = "float"
                        elif kind is None:
                            kind = "int"
            if isinstance(node, ast.Compare):
                operands = [node.left] + list(node.comparators)
                if any(isinstance(item, ast.Name) and item.id == name for item in operands):
                    others = [item for item in operands if not (isinstance(item, ast.Name) and item.id == name)]
                    if any(self.py_type_of(item) == "str" for item in others):
                        return "str"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == name:
                    method = node.func.attr
                    if method in {"upper", "lower", "strip", "lstrip", "rstrip", "replace", "split", "rsplit", "startswith", "endswith", "join", "title", "capitalize"}:
                        return "str"
                    if method in {"append", "extend", "pop", "sort", "reverse"}:
                        return "list"
                    if method in {"keys", "values", "items", "get", "update"}:
                        return "dict"
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == name:
                slice_kind = self.py_type_of(node.slice)
                if slice_kind == "str":
                    kind = kind or "dict"
                else:
                    kind = kind or "list"
        return kind

    # -- small utilities --------------------------------------------
    def _is_none_constant(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    def _as_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _root_name(self, node: ast.AST) -> str | None:
        current = node
        while isinstance(current, ast.Attribute):
            current = current.value
        return current.id if isinstance(current, ast.Name) else None

    def _call_name(self, node: ast.AST) -> str | None:
        return self._as_name(node)

    def _snake_to_camel(self, name: str) -> str:
        parts = [part for part in re.split(r"_+", name) if part]
        if not parts:
            return name
        return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
