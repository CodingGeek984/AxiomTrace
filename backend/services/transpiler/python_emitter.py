"""Module assembly for the deterministic Python -> polyglot transpiler."""

from __future__ import annotations

import ast

from services.transpiler.base import PreparedSource
from services.transpiler.emitter_base import reassigned_names
from services.transpiler.emitter_calls import CallMixin
from services.transpiler.emitter_decl import DeclarationMixin
from services.transpiler.emitter_expr import ExpressionMixin
from services.transpiler.emitter_stmt import StatementMixin


class PythonEmitter(StatementMixin, DeclarationMixin, CallMixin, ExpressionMixin):
    """Translate a parsed Python module or fragment into a target language."""

    # -- class instantiation and super() -----------------------------
    def _expr_Call(self, node: ast.Call) -> str:
        if self._is_super_init(node):
            args = ", ".join(self.expr(arg) for arg in node.args)
            if self.target == "python":
                return f"super().__init__({args})"
            if self.target in {"javascript", "typescript"}:
                return f"super({args})"
            return ""
        if isinstance(node.func, ast.Name) and node.func.id in self.class_names:
            positional_nodes = [arg for arg in node.args if not isinstance(arg, ast.Starred)]
            args = [self.expr(arg) for arg in positional_nodes]
            keyword_values = [self.expr(keyword.value) for keyword in node.keywords if keyword.arg]
            if self.target == "rust":
                fields = self.class_fields.get(node.func.id, [])
                for index, arg_node in enumerate(positional_nodes):
                    if index >= len(fields):
                        break
                    kind = fields[index][1]
                    if kind == "optional":
                        if self._is_none_constant(arg_node):
                            args[index] = "None"
                        else:
                            args[index] = f"Some({args[index]})"
                info = self.constructor_defaults.get(node.func.id)
                if info and not node.keywords and len(args) < info["max"]:
                    for index in range(len(args), info["max"]):
                        default = info["defaults"].get(index)
                        kind = fields[index][1] if index < len(fields) else None
                        if default is None:
                            break
                        rendered_default = self.expr(default)
                        if kind == "optional":
                            rendered_default = "None" if self._is_none_constant(default) else f"Some({rendered_default})"
                        args.append(rendered_default)
            if keyword_values:
                self.warn(
                    "Constructor keyword arguments were passed positionally in the target language."
                )
            rendered = ", ".join(args + keyword_values)
            if self.target == "go":
                return f"New{node.func.id}({rendered})"
            if self.target == "rust":
                return f"{node.func.id}::new({rendered})"
            if self.target in {"python", "cpp"}:
                return f"{node.func.id}({rendered})"
            return f"new {node.func.id}({rendered})"
        return super()._expr_Call(node)

    def _is_super_init(self, node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "super"
        )

    # -- public entry point ------------------------------------------
    def render(self, prepared: PreparedSource, label: str = "axiom_node") -> str:
        body = list(prepared.tree.body)
        self.prescan(body)
        if self.target == "python":
            return self._render_python(prepared, body)
        imports, declarations, executable, main_guard = self._split_module(body)
        self.statements(imports, 0)  # emit warnings for unsupported imports
        run_body = list(executable) + list(main_guard)
        if self.target in {"javascript", "typescript"}:
            return self._render_js(prepared, declarations, run_body)
        if self.target == "cpp":
            return self._render_cpp(prepared, declarations, run_body)
        if self.target == "csharp":
            return self._render_csharp(prepared, declarations, run_body)
        if self.target == "java":
            return self._render_java(prepared, declarations, run_body)
        if self.target == "go":
            return self._render_go(prepared, declarations, run_body)
        if self.target == "rust":
            return self._render_rust(prepared, declarations, run_body)
        if self.target == "php":
            return self._render_php(prepared, declarations, run_body)
        return ""

    # -- module splitting --------------------------------------------
    def _split_module(
        self,
        body: list[ast.stmt],
    ) -> tuple[list[ast.stmt], list[ast.stmt], list[ast.stmt], list[ast.stmt]]:
        imports: list[ast.stmt] = []
        declarations: list[ast.stmt] = []
        executable: list[ast.stmt] = []
        main_guard: list[ast.stmt] = []
        constants: list[ast.stmt] = []
        # A name stored anywhere else at module/run level is mutable: it must
        # stay in the executable flow (``let``/``var``/``let mut``) instead of
        # being hoisted as an immutable ``const``/``define``.
        mutated = self._mutated_names(body)
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                declarations.append(node)
            elif (
                isinstance(node, ast.If)
                and self._is_main_guard(node)
            ):
                main_guard.extend(node.body)
            elif self._is_module_constant(node) and not self._constant_is_mutated(node, mutated):
                constants.append(node)
            elif isinstance(node, ast.Pass):
                continue
            else:
                executable.append(node)
        self.module_constants = constants
        names: set[str] = set()
        for node in constants:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        self.module_constant_names = names
        return imports, declarations, executable, main_guard

    @staticmethod
    def _mutated_names(body: list[ast.stmt]) -> set[str]:
        """Module/run-level names stored more than once (mutable variables)."""
        counts: dict[str, int] = {}

        def walk(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                counts[node.id] = counts.get(node.id, 0) + 1
            for child in ast.iter_child_nodes(node):
                walk(child)

        for statement in body:
            walk(statement)
        return {name for name, count in counts.items() if count > 1}

    @staticmethod
    def _constant_is_mutated(node: ast.stmt, mutated: set[str]) -> bool:
        if isinstance(node, ast.Assign):
            return any(
                isinstance(target, ast.Name) and target.id in mutated
                for target in node.targets
            )
        if isinstance(node, ast.AnnAssign):
            return isinstance(node.target, ast.Name) and node.target.id in mutated
        return False

    def _is_module_constant(self, node: ast.stmt) -> bool:
        if isinstance(node, ast.AnnAssign):
            return isinstance(node.target, ast.Name) and node.value is not None and self._is_constant_expr(node.value)
        if not isinstance(node, ast.Assign):
            return False
        if not all(isinstance(target, ast.Name) for target in node.targets):
            return False
        return self._is_constant_expr(node.value)

    def _is_constant_expr(self, node: ast.expr | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self._is_constant_expr(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(
                key is not None and self._is_constant_expr(key) and self._is_constant_expr(value)
                for key, value in zip(node.keys, node.values)
            )
        if isinstance(node, ast.UnaryOp):
            return self._is_constant_expr(node.operand)
        if isinstance(node, ast.BinOp):
            return self._is_constant_expr(node.left) and self._is_constant_expr(node.right)
        return False

    def _constant_type(self, node: ast.expr | None, fallback: str) -> str:
        kind = self.py_type_of(node)
        if kind:
            mapped = self.py_type_to_target(kind)
            if mapped and mapped not in {"void", "nullptr"}:
                return mapped
        return fallback

    def _render_module_constants(self, level: int = 0) -> str:
        if not self.module_constants:
            return ""
        pad = self.indent(level)
        lines: list[str] = []
        for node in self.module_constants:
            if isinstance(node, ast.Assign):
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                default = node.value
                annotation = None
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                default = node.value
                annotation = node.annotation
            else:
                continue
            value = self.expr(default)
            for name in targets:
                if self.target in {"javascript", "typescript"}:
                    keyword = "const"
                    if self.target == "typescript":
                        type_text = self.map_type(annotation) if annotation is not None else self._constant_type(default, "any")
                        lines.append(f"{pad}{keyword} {name}: {type_text} = {value};")
                    else:
                        lines.append(f"{pad}{keyword} {name} = {value};")
                elif self.target == "cpp":
                    type_text = self.map_type(annotation) if annotation is not None else self._constant_type(default, "auto")
                    lines.append(f"{pad}const {type_text} {name} = {value};")
                elif self.target == "csharp":
                    type_text = self.map_type(annotation) if annotation is not None else self._constant_type(default, "object")
                    lines.append(f"{pad}public static readonly {type_text} {name} = {value};")
                elif self.target == "java":
                    type_text = self.map_type(annotation) if annotation is not None else self._constant_type(default, "Object")
                    lines.append(f"{pad}static final {type_text} {name} = {value};")
                elif self.target == "rust":
                    if isinstance(default, ast.Constant) and isinstance(default.value, str):
                        lines.append(f'{pad}const {name}: &str = {self.string(default.value)};')
                    else:
                        type_text = self.map_type(annotation) if annotation is not None else self._constant_type(default, "f64")
                        lines.append(f"{pad}const {name}: {type_text} = {value};")
                elif self.target == "go":
                    lines.append(f"{pad}var {name} = {value}")
                elif self.target == "php":
                    lines.append(f'{pad}define("{name}", {value});')
        return "\n".join(lines)

    def _is_main_guard(self, node: ast.If) -> bool:
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        left, right = test.left, test.comparators[0]
        return (
            (isinstance(left, ast.Name) and left.id == "__name__" and self._is_main_constant(right))
            or (isinstance(right, ast.Name) and right.id == "__name__" and self._is_main_constant(left))
        )

    def _is_main_constant(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Constant) and node.value == "__main__"

    def _doc_comment(self, docstring: str | None, level: int = 0) -> str:
        if not docstring:
            return ""
        return self._comment_lines(level, docstring)

    def _emit_run_body(self, body: list[ast.stmt], level: int, *, in_main: bool) -> str:
        old = self.in_main
        old_reassigned = self.reassigned_names
        old_go_defaults = self.go_default_exprs
        self.in_main = in_main
        self.reassigned_names = reassigned_names(body)
        self.go_default_exprs = {}
        self.push_scope()
        try:
            return self.statements(body, level)
        finally:
            self.pop_scope()
            self.in_main = old
            self.reassigned_names = old_reassigned
            self.go_default_exprs = old_go_defaults

    def _has_top_level_return(self, body: list[ast.stmt]) -> bool:
        return any(isinstance(node, ast.Return) for node in body)

    # -- Python ------------------------------------------------------
    def _render_python(self, prepared: PreparedSource, body: list[ast.stmt]) -> str:
        # Python is the source language: preserve the author's formatting and
        # comments for complete modules.  Fragments still need a wrapper so a
        # bare ``return`` remains syntactically valid.
        if not prepared.wrapped and prepared.source.strip():
            return prepared.source.rstrip() + "\n"
        lines: list[str] = []
        if prepared.docstring:
            lines.append(self._python_docstring(prepared.docstring, 0))
        lines.append("def main():")
        inner = self.statements(body, 1)
        lines.append(inner if inner else "    pass")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    main()")
        return "\n".join(lines).strip("\n") + "\n"

    def _python_docstring(self, docstring: str, level: int) -> str:
        pad = self.indent(level)
        escaped = docstring.replace('"""', '\\"\\"\\"')
        if "\n" not in escaped:
            return f'{pad}"""{escaped}"""'
        return "\n".join(
            [f'{pad}"""'] + [f"{pad}{line}" for line in escaped.splitlines()] + [f'{pad}"""']
        )

    # -- JavaScript / TypeScript ------------------------------------
    def _render_js(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        parts: list[str] = []
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        const_text = self._render_module_constants(0)
        if const_text:
            parts.append(const_text)
        decl_text = self.statements(declarations, 0)
        if decl_text:
            parts.append(decl_text)
        if run_body:
            if prepared.wrapped:
                lines = []
                if self.target == "typescript":
                    lines.append("function main(): any {")
                else:
                    lines.append("function main() {")
                inner = self._emit_run_body(run_body, 1, in_main=False)
                if inner:
                    lines.append(inner)
                lines.append("}")
                lines.append("")
                if self.target == "typescript":
                    lines.append("const __axiom_result: any = main();")
                else:
                    lines.append("const __axiom_result = main();")
                lines.append("if (__axiom_result !== undefined) {")
                lines.append("    console.log(__axiom_result);")
                lines.append("}")
                parts.append("\n".join(lines))
            else:
                text = self._emit_run_body(run_body, 0, in_main=False)
                if text:
                    parts.append(text)
        return "\n\n".join(part for part in parts if part).strip() + "\n"

    # -- C++ ---------------------------------------------------------
    def _render_cpp(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        decl_text = self.statements(declarations, 0)
        const_text = self._render_module_constants(0)
        main_lines: list[str] = []
        if run_body:
            main_lines.append("int main() {")
            inner = self._emit_run_body(run_body, 1, in_main=True)
            if inner:
                main_lines.append(inner)
            if not self._has_top_level_return(run_body):
                main_lines.append("    return 0;")
            main_lines.append("}")
        body_text = "\n\n".join(part for part in [const_text, decl_text, "\n".join(main_lines)] if part)
        includes = self._cpp_include_block(body_text)
        parts: list[str] = []
        if includes:
            parts.append(includes)
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"

    def _cpp_include_block(self, text: str) -> str:
        if not text.strip():
            return ""
        includes: set[str] = set()
        rules = {
            "std::cout": "iostream", "std::cin": "iostream", "std::endl": "iostream",
            "std::cerr": "iostream", "std::vector": "vector", "std::map": "map",
            "std::set": "set", "std::string": "string", "std::any": "any",
            "std::optional": "optional", "std::tuple": "tuple", "std::make_tuple": "tuple",
            "std::pair": "utility", "std::accumulate": "numeric", "std::sort": "algorithm",
            "std::find": "algorithm", "std::reverse": "algorithm", "std::transform": "algorithm",
            "std::pow": "cmath", "std::sqrt": "cmath", "std::floor": "cmath",
            "std::ceil": "cmath", "std::sin": "cmath", "std::cos": "cmath",
            "std::tan": "cmath", "std::log": "cmath", "std::abs": "cstdlib",
            "std::rand": "cstdlib", "std::getenv": "cstdlib", "std::exit": "cstdlib",
            "std::ostringstream": "sstream", "std::istringstream": "sstream",
            "std::setprecision": "iomanip", "std::invalid_argument": "stdexcept",
            "std::runtime_error": "stdexcept", "std::out_of_range": "stdexcept",
            "std::domain_error": "stdexcept", "std::bad_cast": "stdexcept",
            "std::numeric_limits": "limits", "std::time": "ctime",
            "std::this_thread": "thread", "std::ifstream": "fstream",
            "std::ofstream": "fstream", "std::fstream": "fstream",
        }
        for token, header in rules.items():
            if token in text:
                includes.add(header)
        if "assert(" in text:
            includes.add("cassert")
        if "std::string" in text or "__os.str()" in text:
            includes.add("string")
        if not includes:
            return ""
        return "\n".join(f"#include <{header}>" for header in sorted(includes))

    # -- C# ----------------------------------------------------------
    def _render_csharp(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        classes = [node for node in declarations if isinstance(node, ast.ClassDef)]
        functions = [node for node in declarations if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        wrapper = "Program" if "Program" not in self.class_names else "AxiomProgram"
        self.entry_class_name = wrapper
        class_text = self.statements(classes, 0)
        program_text = ""
        if functions or run_body or self.module_constants:
            lines = [f"public static class {wrapper}", "{"]
            constants_text = self._render_module_constants(1)
            if constants_text:
                lines.append(constants_text)
            for function in functions:
                lines.append(self.emit_function(function, 1))
            if run_body:
                lines.append("    public static void Main(string[] args)")
                lines.append("    {")
                inner = self._emit_run_body(run_body, 2, in_main=True)
                if inner:
                    lines.append(inner)
                lines.append("    }")
            lines.append("}")
            program_text = "\n".join(lines)
        body_text = "\n\n".join(part for part in [class_text, program_text] if part)
        usings = self._csharp_usings(body_text)
        parts: list[str] = []
        if usings:
            parts.append(usings)
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"

    def _csharp_usings(self, text: str) -> str:
        if not text.strip():
            return ""
        usings = ["System", "System.Collections.Generic", "System.Linq"]
        if "Task<" in text or "Task " in text:
            usings.append("System.Threading.Tasks")
        if "JsonSerializer" in text:
            usings.append("System.Text.Json")
        if "CultureInfo" in text:
            usings.append("System.Globalization")
        if "System.IO" in text:
            usings.append("System.IO")
        seen: set[str] = set()
        lines = []
        for using in usings:
            if using not in seen:
                seen.add(using)
                lines.append(f"using {using};")
        return "\n".join(lines)

    # -- Java --------------------------------------------------------
    def _render_java(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        classes = [node for node in declarations if isinstance(node, ast.ClassDef)]
        functions = [node for node in declarations if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        wrapper = "Main" if "Main" not in self.class_names else "AxiomMain"
        self.entry_class_name = wrapper
        class_text = self.statements(classes, 0)
        wrapper_text = ""
        if functions or run_body or self.module_constants:
            lines = [f"public class {wrapper}", "{"]
            constants_text = self._render_module_constants(1)
            if constants_text:
                lines.append(constants_text)
            for function in functions:
                lines.append(self.emit_function(function, 1))
            if run_body:
                lines.append("    public static void main(String[] args) {")
                inner = self._emit_run_body(run_body, 2, in_main=True)
                if inner:
                    lines.append(inner)
                lines.append("    }")
            lines.append("}")
            wrapper_text = "\n".join(lines)
        body_text = "\n\n".join(part for part in [class_text, wrapper_text] if part)
        parts: list[str] = []
        if body_text.strip():
            parts.append("import java.util.*;\nimport java.util.stream.*;")
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"

    # -- Go ----------------------------------------------------------
    def _render_go(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        decl_text = self.statements(declarations, 0)
        main_text = ""
        if run_body:
            lines = ["func main() {"]
            inner = self._emit_run_body(run_body, 1, in_main=True)
            if inner:
                lines.append(inner)
            lines.append("}")
            main_text = "\n".join(lines)
        body_text = "\n\n".join(part for part in [decl_text, main_text] if part)
        parts = ["package main"]
        imports = self._go_import_block(self.go_imports)
        if imports:
            parts.append(imports)
        const_text = self._render_module_constants(0)
        if const_text:
            parts.append(const_text)
        if self.go_helpers & {"first"}:
            parts.append(
                "func axiomFirst[T any](values []T, fallback T) T {\n"
                "    if len(values) > 0 {\n"
                "        return values[0]\n"
                "    }\n"
                "    return fallback\n"
                "}"
            )
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"

    def _go_import_block(self, imports: set[str]) -> str:
        if not imports:
            return ""
        lines = ["import ("]
        for path in sorted(imports):
            lines.append(f'    "{path}"')
        lines.append(")")
        return "\n".join(lines)

    # -- Rust --------------------------------------------------------
    def _render_rust(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        decl_text = self.statements(declarations, 0)
        const_text = self._render_module_constants(0)
        main_text = ""
        if run_body:
            lines = ["fn main() {"]
            inner = self._emit_run_body(run_body, 1, in_main=True)
            if inner:
                lines.append(inner)
            lines.append("}")
            main_text = "\n".join(lines)
        parts: list[str] = []
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        body_text = "\n\n".join(part for part in [const_text, decl_text, main_text] if part)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"

    # -- PHP ---------------------------------------------------------
    def _render_php(
        self,
        prepared: PreparedSource,
        declarations: list[ast.stmt],
        run_body: list[ast.stmt],
    ) -> str:
        decl_text = self.statements(declarations, 0)
        const_text = self._render_module_constants(0)
        run_text = ""
        if run_body:
            run_text = self._emit_run_body(run_body, 0, in_main=True)
        parts = ["<?php"]
        doc = self._doc_comment(prepared.docstring)
        if doc:
            parts.append(doc)
        if const_text:
            parts.append(const_text)
        body_text = "\n\n".join(part for part in [decl_text, run_text] if part)
        if body_text:
            parts.append(body_text)
        return "\n\n".join(parts).strip() + "\n"


def emit_source(prepared: PreparedSource, target: str, label: str = "axiom_node") -> tuple[str, list[str]]:
    """Translate ``prepared`` into ``target`` and return ``(code, warnings)``."""
    emitter = PythonEmitter(target)
    code = emitter.render(prepared, label=label)
    return code, list(emitter.warnings)


__all__ = ["PythonEmitter", "emit_source"]
