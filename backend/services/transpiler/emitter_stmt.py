"""Statement, control-flow and block translation for the polyglot emitter."""

from __future__ import annotations

import ast

from services.transpiler.emitter_base import EmitterBase

_IGNORED_IMPORTS = {
    "abc", "asyncio", "base64", "collections", "copy", "datetime", "dataclasses",
    "decimal", "enum", "functools", "hashlib", "io", "itertools", "json",
    "logging", "math", "os", "pathlib", "random", "re", "shutil", "string",
    "subprocess", "sys", "tempfile", "threading", "time", "typing", "uuid",
}

_EXCEPTIONS = {
    "ValueError": {
        "java": "IllegalArgumentException", "csharp": "ArgumentException",
        "cpp": "std::invalid_argument", "php": "InvalidArgumentException",
        "javascript": "Error", "typescript": "Error",
    },
    "TypeError": {
        "java": "ClassCastException", "csharp": "InvalidCastException",
        "cpp": "std::bad_cast", "php": "TypeError",
        "javascript": "TypeError", "typescript": "TypeError",
    },
    "KeyError": {
        "java": "java.util.NoSuchElementException", "csharp": "KeyNotFoundException",
        "cpp": "std::out_of_range", "php": "OutOfBoundsException",
        "javascript": "Error", "typescript": "Error",
    },
    "IndexError": {
        "java": "IndexOutOfBoundsException", "csharp": "IndexOutOfRangeException",
        "cpp": "std::out_of_range", "php": "OutOfRangeException",
        "javascript": "RangeError", "typescript": "RangeError",
    },
    "ZeroDivisionError": {
        "java": "ArithmeticException", "csharp": "DivideByZeroException",
        "cpp": "std::domain_error", "php": "DivisionByZeroError",
        "javascript": "RangeError", "typescript": "RangeError",
    },
    "FileNotFoundError": {
        "java": "java.io.FileNotFoundException", "csharp": "System.IO.FileNotFoundException",
        "cpp": "std::runtime_error", "php": "RuntimeException",
        "javascript": "Error", "typescript": "Error",
    },
    "RuntimeError": {
        "java": "RuntimeException", "csharp": "InvalidOperationException",
        "cpp": "std::runtime_error", "php": "RuntimeException",
        "javascript": "Error", "typescript": "Error",
    },
    "OSError": {
        "java": "java.io.IOException", "csharp": "System.IO.IOException",
        "cpp": "std::runtime_error", "php": "RuntimeException",
        "javascript": "Error", "typescript": "Error",
    },
    "AssertionError": {
        "java": "AssertionError", "csharp": "InvalidOperationException",
        "cpp": "std::runtime_error", "php": "AssertionError",
        "javascript": "Error", "typescript": "Error",
    },
    "Exception": {
        "java": "RuntimeException", "csharp": "Exception",
        "cpp": "std::runtime_error", "php": "RuntimeException",
        "javascript": "Error", "typescript": "Error",
    },
}


class StatementMixin(EmitterBase):
    """Render Python statements for every supported target language."""

    # -- entry points ------------------------------------------------
    def statements(self, body: list[ast.stmt], level: int = 0) -> str:
        rendered: list[str] = []
        for node in body:
            text = self.stmt(node, level)
            if text:
                rendered.append(text)
        return "\n".join(rendered)

    def stmt(self, node: ast.stmt, level: int = 0) -> str:
        handler = getattr(self, f"_stmt_{type(node).__name__}", None)
        if handler is None:
            self.warn(f"Statement {type(node).__name__} has no {self.target} mapping and was skipped.")
            return f"{self.indent(level)}{self.comment(f'Unsupported statement: {type(node).__name__}')}"
        return handler(node, level)

    # -- formatting helpers ------------------------------------------
    def _braced(self, header: str, body: list[ast.stmt], level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}{header} {{"]
        inner = self.statements(body, level + 1)
        if inner:
            lines.append(inner)
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _colon_block(self, header: str, body: list[ast.stmt], level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}{header}:"]
        inner = self.statements(body, level + 1)
        lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        return "\n".join(lines)

    def _cond(self, node: ast.expr) -> str:
        text = self.expr(node)
        if self.target in {"go", "rust"}:
            return text
        return f"({text})"

    def _terminate(self, text: str) -> str:
        if self.target in {"go", "python"}:
            return text
        if text.rstrip().endswith(";"):
            return text
        return f"{text};"

    def _line(self, level: int, text: str, *, terminate: bool = True) -> str:
        body = self._terminate(text) if terminate else text
        return f"{self.indent(level)}{body}"

    def _comment_lines(self, level: int, text: str) -> str:
        pad = self.indent(level)
        lines = [f"{pad}{self.comment(line)}" for line in text.strip().splitlines()] if text.strip() else []
        return "\n".join(lines)

    def _print_value(self, expression: str, level: int) -> str:
        pad = self.indent(level)
        if self.target == "python":
            return f"{pad}print({expression})"
        if self.target in {"javascript", "typescript"}:
            return f"{pad}console.log({expression});"
        if self.target == "java":
            return f"{pad}System.out.println({expression});"
        if self.target == "csharp":
            return f"{pad}Console.WriteLine({expression});"
        if self.target == "cpp":
            return f"{pad}std::cout << {expression} << std::endl;"
        if self.target == "rust":
            return f'{pad}println!("{{}}", {expression});'
        if self.target == "go":
            self.go_imports.add("fmt")
            return f"{pad}fmt.Println({expression})"
        if self.target == "php":
            return f"{pad}echo {expression} . PHP_EOL;"
        return f"{pad}{expression};"

    def _return_statement(self, level: int, expression: str | None) -> str:
        pad = self.indent(level)
        if self.in_main and self.target == "php":
            return self._print_value(expression, level) if expression else ""
        if self.in_main and self.target in {"cpp", "csharp", "java", "go", "rust"}:
            lines: list[str] = []
            if expression:
                lines.append(self._print_value(expression, level))
            closing = "return 0;" if self.target == "cpp" else "return"
            lines.append(f"{pad}{closing}{';' if self.target not in {'go'} and not closing.endswith(';') else ''}")
            return "\n".join(lines)
        if self.target == "python":
            return f"{pad}return {expression}" if expression else f"{pad}return"
        if expression:
            return f"{pad}{self._terminate(f'return {expression}')}"
        return f"{pad}return"

    # -- simple statements -------------------------------------------
    def _stmt_Pass(self, node: ast.Pass, level: int) -> str:
        return f"{self.indent(level)}pass" if self.target == "python" else ""

    def _stmt_Break(self, node: ast.Break, level: int) -> str:
        return self._line(level, "break")

    def _stmt_Continue(self, node: ast.Continue, level: int) -> str:
        return self._line(level, "continue")

    def _stmt_Expr(self, node: ast.Expr, level: int) -> str:
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return self._comment_lines(level, value.value)
        if isinstance(value, ast.Yield):
            return self._yield_statement(value, level)
        if isinstance(value, ast.YieldFrom):
            return self._yield_statement(value, level, from_expr=value.value)
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            method = value.func.attr
            receiver = self.expr(value.func.value)
            args = ", ".join(self.expr(arg) for arg in value.args if not isinstance(arg, ast.Starred))
            if self.target == "go" and method == "append":
                return self._line(level, f"{receiver} = append({receiver}, {args})", terminate=False)
            if self.target == "go" and method in {"extend", "insert", "pop"}:
                rendered = self.expr(value)
                return self._line(level, rendered, terminate=False)
        if isinstance(value, ast.Call) and self._is_print_like(value) and self.target == "php":
            return self._line(level, self.expr(value), terminate=False)
        rendered = self.expr(value)
        return self._line(level, rendered)

    def _is_print_like(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and self._call_name(node.func) in {"print"}

    def _yield_statement(self, node: ast.Yield | ast.YieldFrom, level: int, from_expr: ast.expr | None = None) -> str:
        pad = self.indent(level)
        expression = self.expr(from_expr if from_expr is not None else node.value) if (from_expr is not None or node.value) else None
        if self.target == "python":
            return f"{pad}yield {expression}" if expression else f"{pad}yield"
        if self.target in {"javascript", "typescript", "php"}:
            return f"{pad}yield {expression};" if expression else f"{pad}yield;"
        if self.target == "csharp":
            return f"{pad}yield return {expression};" if expression else f"{pad}yield break;"
        self.warn("Generators are not native to the target; the yielded value was emitted as a statement.")
        return f"{pad}{expression};" if expression else ""

    def _stmt_Return(self, node: ast.Return, level: int) -> str:
        expression = self.expr(node.value) if node.value is not None else None
        return self._return_statement(level, expression)

    def _stmt_Assert(self, node: ast.Assert, level: int) -> str:
        condition = self.expr(node.test)
        message = self.expr(node.msg) if node.msg is not None else self.string("Assertion failed")
        pad = self.indent(level)
        if self.target == "python":
            return f"{pad}assert {condition}" + (f", {message}" if node.msg is not None else "")
        if self.target == "cpp":
            return f"{pad}assert({condition});"
        if self.target == "rust":
            return f"{pad}assert!({condition}, \"{{}}\", {message});"
        if self.target == "go":
            return f"{pad}if !({condition}) {{ panic({message}) }}"
        if self.target == "php":
            return f"{pad}if (!({condition})) {{ throw new AssertionError({message}); }}"
        if self.target in {"javascript", "typescript"}:
            return f"{pad}if (!({condition})) {{ throw new Error({message}); }}"
        return f"{pad}if (!({condition})) {{ throw new {self._exception_type('AssertionError')}({message}); }}"

    def _stmt_Raise(self, node: ast.Raise, level: int) -> str:
        pad = self.indent(level)
        if node.exc is None:
            if self.target == "python":
                return f"{pad}raise"
            if self.target in {"javascript", "typescript"}:
                return f"{pad}throw error;"
            if self.target == "go":
                return f'{pad}panic("re-raised exception")'
            if self.target == "rust":
                return f'{pad}panic!("re-raised exception");'
            return f"{pad}throw;"
        if isinstance(node.exc, ast.Call):
            name = self._call_name(node.exc.func) or "Exception"
            args = [self.expr(arg) for arg in node.exc.args]
            message = args[0] if args else self.string(name)
        else:
            name = self._call_name(node.exc) or "Exception"
            message = self.string(name)
        if self.target == "python":
            call = self.expr(node.exc) if isinstance(node.exc, ast.Call) else name
            return f"{pad}raise {call}"
        if self.target == "go":
            return f"{pad}panic({message})"
        if self.target == "rust":
            return f'{pad}panic!("{{}}", {message});'
        if self.target == "cpp":
            return f"{pad}throw {self._exception_type(name)}({message});"
        if self.target == "php":
            return f"{pad}throw new {self._exception_type(name)}({message});"
        if self.target == "javascript":
            return f"{pad}throw new {self._exception_type(name)}({message});"
        return f"{pad}throw new {self._exception_type(name)}({message});"

    def _exception_type(self, name: str) -> str:
        name = name.rsplit(".", 1)[-1]
        if name in _EXCEPTIONS:
            return _EXCEPTIONS[name].get(self.target, name)
        if self.target == "cpp":
            return "std::runtime_error"
        if self.target == "php":
            return "RuntimeException"
        if self.target == "javascript":
            return "Error"
        return name

    def _stmt_Delete(self, node: ast.Delete, level: int) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                container = self.expr(target.value)
                key = self.expr(target.slice)
                if self.target == "python":
                    lines.append(f"{pad}del {container}[{key}]")
                elif self.target in {"javascript", "typescript"}:
                    lines.append(f"{pad}delete {container}[{key}];")
                elif self.target == "java":
                    lines.append(f"{pad}{container}.remove({key});")
                elif self.target == "csharp":
                    lines.append(f"{pad}{container}.Remove({key});")
                elif self.target == "cpp":
                    lines.append(f"{pad}{container}.erase({key});")
                elif self.target == "rust":
                    lines.append(f"{pad}{container}.remove(&{key});")
                elif self.target == "go":
                    lines.append(f"{pad}delete({container}, {key})")
                elif self.target == "php":
                    lines.append(f"{pad}unset({container}[{key}]);")
            elif isinstance(target, ast.Attribute):
                if self.target == "php":
                    lines.append(f"{pad}unset({self.expr(target.value)}->{target.attr});")
                elif self.target == "python":
                    lines.append(f"{pad}del {self.expr(target)}")
                else:
                    self.warn("Deleting an attribute is not portable; the field was reset instead.")
                    lines.append(self._line(level, f"{self.expr(target)} = {self.none()}"))
            else:
                self.warn("Deleting a local variable has no portable equivalent and was skipped.")
        return "\n".join(lines)

    def _stmt_Global(self, node: ast.Global, level: int) -> str:
        self.warn("Python `global` declarations have no direct target equivalent.")
        return ""

    def _stmt_Nonlocal(self, node: ast.Nonlocal, level: int) -> str:
        self.warn("Python `nonlocal` declarations have no direct target equivalent.")
        return ""

    # -- imports -----------------------------------------------------
    def _stmt_Import(self, node: ast.Import, level: int) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if self.target == "python":
                suffix = f" as {alias.asname}" if alias.asname else ""
                lines.append(f"{pad}import {alias.name}{suffix}")
            elif root not in _IGNORED_IMPORTS:
                self.warn(
                    f"Python import `{alias.name}` has no bundled {self.target} equivalent; "
                    "the package must be provided by the surrounding project."
                )
        return "\n".join(lines)

    def _stmt_ImportFrom(self, node: ast.ImportFrom, level: int) -> str:
        pad = self.indent(level)
        module = node.module or ""
        root = module.split(".", 1)[0]
        if self.target == "python":
            names = ", ".join(
                alias.name + (f" as {alias.asname}" if alias.asname else "")
                for alias in node.names
            )
            return f"{pad}from {module} import {names}"
        if root not in _IGNORED_IMPORTS and root != "__future__":
            self.warn(
                f"Python import `{module}` has no bundled {self.target} equivalent; "
                "the package must be provided by the surrounding project."
            )
        return ""

    # -- assignment --------------------------------------------------
    def _assign_target(self, target: ast.expr) -> str:
        if isinstance(target, ast.Name):
            return self.expr(target)
        if isinstance(target, ast.Attribute):
            value = self.expr(target.value)
            if self.target == "cpp" and value == "this":
                return f"this->{target.attr}"
            if self.target == "go":
                owner = self.current_class if value in {"self", "this"} else None
                if owner:
                    return f"{value}.{self.go_field(owner, target.attr)}"
                return f"{value}.{self.go_export(target.attr)}"
            if self.target == "php":
                return f"{value}->{target.attr}"
            return f"{value}.{target.attr}"
        if isinstance(target, ast.Subscript):
            container = self.expr(target.value)
            key = self.expr(target.slice)
            return f"{container}[{key}]"
        return self.expr(target)

    def _store_subscript(self, target: ast.Subscript, value: str, level: int) -> str:
        container = self.expr(target.value)
        key = self.expr(target.slice)
        key_is_text = isinstance(target.slice, (ast.Constant, ast.JoinedStr)) and (
            isinstance(target.slice, ast.JoinedStr)
            or isinstance(getattr(target.slice, "value", None), str)
        )
        pad = self.indent(level)
        if self.target == "python":
            return f"{pad}{container}[{key}] = {value}"
        if self.target == "java":
            method = "put" if key_is_text else "set"
            return f"{pad}{container}.{method}({key}, {value});"
        if self.target == "rust":
            method = "insert" if key_is_text else "set"
            if method == "insert":
                return f"{pad}{container}.insert({key}, {value});"
            return f"{pad}{container}[{key}] = {value};"
        if self.target == "go":
            return f"{pad}{container}[{key}] = {value}"
        if self.target == "php":
            return f"{pad}{container}[{key}] = {value};"
        return f"{pad}{container}[{key}] = {value};"

    def _declare_assignment(self, name: str, value: str, level: int, *, type_node: ast.expr | None = None, value_node: ast.expr | None = None) -> str:
        pad = self.indent(level)
        first = name not in self.declared
        inferred = self.py_type_of(value_node) if value_node is not None else None
        target_type = self.map_type(type_node) if type_node is not None else None
        if self.target == "python":
            if type_node is not None:
                return f"{pad}{name}: {self.annotation_text(type_node)} = {value}"
            return f"{pad}{name} = {value}"
        if self.target in {"javascript", "typescript"}:
            if self.target == "typescript":
                annotation = target_type or (self.primitive(inferred) if inferred else "any")
                if type_node is not None and value:
                    statement = f"{pad}let {name}: {annotation} = {value};"
                elif type_node is not None:
                    statement = f"{pad}let {name}: {annotation};"
                elif first:
                    statement = f"{pad}let {name} = {value};"
                else:
                    statement = f"{pad}{name} = {value};"
            else:
                statement = f"{pad}let {name} = {value};" if first else f"{pad}{name} = {value};"
            if first:
                self.declare(name, inferred)
            return statement
        if self.target == "cpp":
            if first:
                annotation = target_type or "auto"
                statement = f"{pad}{annotation} {name} = {value};"
                self.declare(name, inferred)
                return statement
            return f"{pad}{name} = {value};"
        if self.target == "csharp":
            if type_node is not None:
                annotation = target_type or "object"
            elif first:
                annotation = "var"
            else:
                return f"{pad}{name} = {value};"
            if first:
                self.declare(name, inferred)
            return f"{pad}{annotation} {name} = {value};"
        if self.target == "java":
            if type_node is not None:
                annotation = target_type or "Object"
            elif first:
                annotation = "var"
            else:
                return f"{pad}{name} = {value};"
            if first:
                self.declare(name, inferred)
            return f"{pad}{annotation} {name} = {value};"
        if self.target == "rust":
            if first:
                annotation = f": {target_type}" if target_type else ""
                mutable = "mut " if name in self.reassigned_names else ""
                self.declare(name, inferred)
                return f"{pad}let {mutable}{name}{annotation} = {value};"
            return f"{pad}{name} = {value};"
        if self.target == "go":
            if first:
                if type_node is not None:
                    annotation = target_type or "any"
                    self.declare(name, inferred)
                    return f"{pad}var {name} {annotation} = {value}"
                self.declare(name, inferred)
                return f"{pad}{name} := {value}"
            return f"{pad}{name} = {value}"
        if self.target == "php":
            self.declare(name, inferred)
            return f"{pad}${name} = {value};"
        return f"{pad}{name} = {value}"

    def _stmt_Assign(self, node: ast.Assign, level: int) -> str:
        pad = self.indent(level)
        value = self.expr(node.value)
        target = node.targets[0] if node.targets else None
        if target is None:
            return ""

        # Tuple destructuring: expand literal tuples into individual values.
        if isinstance(target, (ast.Tuple, ast.List)):
            names = list(target.elts)
            if isinstance(node.value, ast.Tuple) and len(node.value.elts) == len(names):
                lines: list[str] = []
                for name, item in zip(names, node.value.elts):
                    lines.append(self._assign_one(name, self.expr(item), level, item))
                return "\n".join(line for line in lines if line)
            if self.target == "python":
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}{targets} = {value}"
            if self.target in {"javascript", "typescript"}:
                targets = ", ".join(self.expr(item) for item in names)
                prefix = "let " if all(isinstance(item, ast.Name) and item.id not in self.declared for item in names) else ""
                for item in names:
                    if isinstance(item, ast.Name):
                        self.declare(item.id)
                return f"{pad}{prefix}[{targets}] = {value};"
            if self.target == "csharp":
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}var ({targets}) = {value};"
            if self.target == "cpp":
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}auto [{targets}] = {value};"
            if self.target == "rust":
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}let ({targets}) = {value};"
            if self.target == "go":
                if isinstance(node.value, ast.Tuple):
                    return ""
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}{targets} := {value}"
            if self.target == "java":
                names_text = ", ".join(self.expr(item) for item in names)
                temp = "__tuple"
                lines = [f"{pad}var {temp} = {value};"]
                for index, item in enumerate(names):
                    if isinstance(item, ast.Name) and item.id != "_":
                        lines.append(f"{pad}var {item.id} = {temp}[{index}];")
                return "\n".join(lines)
            if self.target == "php":
                targets = ", ".join(self.expr(item) for item in names)
                return f"{pad}[{targets}] = {value};"

        # Chained assignment: evaluate once through the first target.
        if len(node.targets) > 1:
            first_target = node.targets[0]
            lines = [self._assign_one(first_target, value, level, node.value)]
            for extra in node.targets[1:]:
                receiver = self._assign_target(first_target)
                lines.append(self._assign_one(extra, receiver, level, node.value))
            return "\n".join(line for line in lines if line)

        return self._assign_one(target, value, level, node.value)

    def _assign_one(self, target: ast.expr, value: str, level: int, value_node: ast.expr | None) -> str:
        if isinstance(target, ast.Name):
            if target.id == "_":
                return ""
            return self._declare_assignment(target.id, value, level, value_node=value_node)
        if isinstance(target, ast.Subscript):
            return self._store_subscript(target, value, level)
        if isinstance(target, ast.Attribute):
            target_text = self._assign_target(target)
            pad = self.indent(level)
            if self.current_class and isinstance(target.value, ast.Name) and target.value.id == "self":
                self.current_class_fields.setdefault(target.attr, self.py_type_of(value_node) or "object")
            return f"{pad}{self._terminate(f'{target_text} = {value}')}"
        if isinstance(target, (ast.Tuple, ast.List)):
            # Nested destructuring is flattened with a best-effort note.
            self.warn("Nested destructuring was flattened into a single assignment.")
            return self._line(level, f"{self._assign_target(target)} = {value}")
        return self._line(level, f"{self._assign_target(target)} = {value}")

    def _stmt_AnnAssign(self, node: ast.AnnAssign, level: int) -> str:
        if not isinstance(node.target, ast.Name):
            if node.value is None:
                return ""
            return self._assign_one(node.target, self.expr(node.value), level, node.value)
        if node.value is None:
            return self._declare_uninitialised(node.target.id, node.annotation, level)
        return self._declare_assignment(
            node.target.id,
            self.expr(node.value),
            level,
            type_node=node.annotation,
            value_node=node.value,
        )

    def _declare_uninitialised(self, name: str, annotation: ast.expr, level: int) -> str:
        pad = self.indent(level)
        py_kind = self.annotation_text(annotation) or ""
        target_type = self.map_type(annotation)
        if self.target == "python":
            return f"{pad}{name}: {py_kind}"
        if self.target == "typescript":
            self.declare(name)
            return f"{pad}let {name}: {target_type or 'any'};"
        if self.target == "javascript":
            self.declare(name)
            return f"{pad}let {name};"
        if self.target == "cpp":
            self.declare(name)
            return f"{pad}{target_type or 'std::any'} {name}{{}};"
        if self.target == "csharp":
            self.declare(name)
            return f"{pad}{target_type or 'object'} {name} = default;"
        if self.target == "java":
            self.declare(name)
            default = self._java_default(target_type or "Object")
            return f"{pad}{target_type or 'Object'} {name} = {default};"
        if self.target == "rust":
            self.declare(name)
            return f"{pad}let mut {name}: {target_type or 'i64'} = Default::default();"
        if self.target == "go":
            self.declare(name)
            return f"{pad}var {name} {target_type or 'any'}"
        if self.target == "php":
            self.declare(name)
            return f"{pad}${name} = null;"
        return f"{pad}{name} = {self.none()}"

    def _java_default(self, type_name: str) -> str:
        if type_name in {"int", "long", "short", "byte", "double", "float"}:
            return "0"
        if type_name == "boolean":
            return "false"
        if type_name == "char":
            return "'\\0'"
        return "null"

    def _stmt_AugAssign(self, node: ast.AugAssign, level: int) -> str:
        target = self._assign_target(node.target)
        value = self.expr(node.value)
        pad = self.indent(level)
        if isinstance(node.op, ast.Pow):
            if self.target == "python":
                return f"{pad}{target} **= {value}"
            return self._line(level, f"{target} = {self._pow(target, value)}")
        if isinstance(node.op, ast.FloorDiv) and self.target != "python":
            if self.target == "go":
                return f"{pad}{target} /= {value}"
            return self._line(level, f"{target} = {self._floor_division(target, value)}")
        operator = {
            ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.Div: "/=",
            ast.Mod: "%=", ast.FloorDiv: "//=", ast.BitAnd: "&=",
            ast.BitOr: "|=", ast.BitXor: "^=", ast.LShift: "<<=", ast.RShift: ">>=",
        }.get(type(node.op), "+=")
        if self.target == "python":
            return f"{pad}{target} {operator} {value}"
        return f"{pad}{self._terminate(f'{target} {operator} {value}')}"

    # -- conditionals ------------------------------------------------
    def _stmt_If(self, node: ast.If, level: int) -> str:
        if self.target == "python":
            return self._python_if(node, level, "if")
        return "\n".join(self._brace_if(node, level, "if"))

    def _python_if(self, node: ast.If, level: int, keyword: str) -> str:
        pad = self.indent(level)
        lines = [f"{pad}{keyword} {self.expr(node.test)}:"]
        inner = self.statements(node.body, level + 1)
        lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                lines.append(self._python_if(node.orelse[0], level, "elif"))
            else:
                lines.append(f"{pad}else:")
                inner = self.statements(node.orelse, level + 1)
                lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        return "\n".join(lines)

    def _brace_if(self, node: ast.If, level: int, keyword: str) -> list[str]:
        pad = self.indent(level)
        lines = [f"{pad}{keyword} {self._cond(node.test)} {{"]
        inner = self.statements(node.body, level + 1)
        if inner:
            lines.append(inner)
        current = node
        while current.orelse:
            if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                nested = current.orelse[0]
                lines.append(f"{pad}}} else if {self._cond(nested.test)} {{")
                inner = self.statements(nested.body, level + 1)
                if inner:
                    lines.append(inner)
                current = nested
                continue
            lines.append(f"{pad}}} else {{")
            inner = self.statements(current.orelse, level + 1)
            if inner:
                lines.append(inner)
            break
        lines.append(f"{pad}}}")
        return lines

    # -- loops -------------------------------------------------------
    def _range_parts_node(self, iter_node: ast.expr) -> tuple[str, str, str] | None:
        if not (isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range"):
            return None
        parts = [self.expr(arg) for arg in iter_node.args]
        return self._range_parts(parts)

    def _enumerate_parts(self, iter_node: ast.expr) -> str | None:
        if (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Name)
            and iter_node.func.id == "enumerate"
            and iter_node.args
        ):
            return self.expr(iter_node.args[0])
        return None

    def _target_names(self, target: ast.expr) -> list[str]:
        if isinstance(target, (ast.Tuple, ast.List)):
            return [self._assign_target(item) for item in target.elts]
        return [self._assign_target(target)]

    def _stmt_For(self, node: ast.For, level: int) -> str:
        if self.target == "python":
            return self._python_for(node, level)
        text = self._brace_for(node, level)
        if node.orelse:
            self.warn("Python `for ... else` has no direct target equivalent; the else block runs after the loop.")
            extra = self.statements(node.orelse, level)
            if extra:
                text = f"{text}\n{extra}"
        return text

    def _python_for(self, node: ast.For, level: int) -> str:
        pad = self.indent(level)
        target = ", ".join(self._target_names(node.target)) if isinstance(node.target, (ast.Tuple, ast.List)) else self.expr(node.target)
        lines = [f"{pad}for {target} in {self.expr(node.iter)}:"]
        inner = self.statements(node.body, level + 1)
        lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        if node.orelse:
            lines.append(f"{pad}else:")
            inner = self.statements(node.orelse, level + 1)
            lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        return "\n".join(lines)

    def _brace_for(self, node: ast.For, level: int) -> str:
        pad = self.indent(level)
        names = self._target_names(node.target)
        name = names[0] if len(names) == 1 else ""
        iterable = self.expr(node.iter)
        range_parts = self._range_parts_node(node.iter)
        enumerate_source = self._enumerate_parts(node.iter)
        lines: list[str] = []
        indent_extra = 0

        if self.target in {"javascript", "typescript"}:
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for (let {name} = {start}; {name} {comparator} {stop}; {name} += {step}) {{")
            elif enumerate_source:
                if len(names) == 2:
                    lines.append(f"{pad}for (const [{names[0]}, {names[1]}] of Array.from({enumerate_source}).entries()) {{")
                else:
                    lines.append(f"{pad}for (const {name} of {enumerate_source}) {{")
            elif len(names) > 1:
                lines.append(f"{pad}for (const [{', '.join(names)}] of {iterable}) {{")
            else:
                lines.append(f"{pad}for (const {name} of {iterable}) {{")
        elif self.target == "cpp":
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for (int {name} = {start}; {name} {comparator} {stop}; {name} += {step}) {{")
            elif len(names) > 1:
                lines.append(f"{pad}for (const auto& [{', '.join(names)}] : {iterable}) {{")
            else:
                loop_name = name if name != "_" else "__unused"
                lines.append(f"{pad}for (const auto& {loop_name} : {iterable}) {{")
        elif self.target == "csharp":
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for (var {name} = {start}; {name} {comparator} {stop}; {name} += {step}) {{")
            elif len(names) > 1:
                lines.append(f"{pad}foreach (var __pair in {iterable}) {{")
                for index, item in enumerate(names):
                    if item != "_":
                        lines.append(f"{self.indent(level + 1)}dynamic {item} = __pair[{index}];")
                indent_extra = 1
            else:
                lines.append(f"{pad}foreach (var {name} in {iterable}) {{")
        elif self.target == "java":
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for (var {name} = {start}; {name} {comparator} {stop}; {name} += {step}) {{")
            elif len(names) > 1:
                items_source = self._dict_items_source(node.iter)
                if items_source:
                    lines.append(f"{pad}for (var __entry : {items_source}.entrySet()) {{")
                    lines.append(f"{self.indent(level + 1)}var {names[0]} = __entry.getKey();")
                    lines.append(f"{self.indent(level + 1)}var {names[1]} = __entry.getValue();")
                else:
                    lines.append(f"{pad}for (var __pair : {iterable}) {{")
                    for index, item in enumerate(names):
                        if item != "_":
                            lines.append(f"{self.indent(level + 1)}var {item} = __pair[{index}];")
                indent_extra = 1
            else:
                lines.append(f"{pad}for (var {name} : {iterable}) {{")
        elif self.target == "rust":
            if range_parts:
                start, stop, step = range_parts
                if step == "1":
                    lines.append(f"{pad}for {name} in {start}..{stop} {{")
                elif step.lstrip().startswith("-"):
                    if step in {"-1", "- 1"}:
                        lines.append(f"{pad}for {name} in ({stop} + 1..={start}).rev() {{")
                    else:
                        self.warn("Negative Rust range steps other than -1 were approximated.")
                        lines.append(f"{pad}for {name} in ({stop}..{start}).rev() {{")
                else:
                    lines.append(f"{pad}for {name} in ({start}..{stop}).step_by({step} as usize) {{")
            elif len(names) > 1:
                lines.append(f"{pad}for ({', '.join(names)}) in {iterable} {{")
            else:
                source = iterable
                if isinstance(node.iter, (ast.Name, ast.Attribute)) and not self._is_items_call(node.iter):
                    source = f"{iterable}.iter().cloned()"
                lines.append(f"{pad}for {name} in {source} {{")
        elif self.target == "go":
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for {name} := {start}; {name} {comparator} {stop}; {name} += {step} {{")
            elif enumerate_source:
                if len(names) == 2:
                    lines.append(f"{pad}for {names[0]}, {names[1]} := range {enumerate_source} {{")
                else:
                    lines.append(f"{pad}for _, {name} := range {enumerate_source} {{")
            elif len(names) > 1:
                items_source = self._dict_items_source(node.iter)
                if items_source:
                    lines.append(f"{pad}for {names[0]}, {names[1]} := range {items_source} {{")
                else:
                    lines.append(f"{pad}for _, __pair := range {iterable} {{")
                    for index, item in enumerate(names):
                        if item != "_":
                            lines.append(f"{self.indent(level + 1)}{item} := __pair[{index}]")
                    indent_extra = 1
            else:
                if name == "_":
                    lines.append(f"{pad}for range {iterable} {{")
                elif isinstance(node.iter, ast.Name) and self.var_type(node.iter.id) == "dict":
                    lines.append(f"{pad}for {name} := range {iterable} {{")
                else:
                    lines.append(f"{pad}for _, {name} := range {iterable} {{")
        elif self.target == "php":
            plain = name[1:] if name.startswith("$") else name
            if range_parts:
                start, stop, step = range_parts
                comparator = ">" if step.lstrip().startswith("-") else "<"
                lines.append(f"{pad}for (${plain} = {start}; ${plain} {comparator} {stop}; ${plain} += {step}) {{")
            elif len(names) > 1:
                slots = ", ".join(item if item != "_" else "" for item in names)
                lines.append(f"{pad}foreach ({iterable} as [{slots}]) {{")
            else:
                lines.append(f"{pad}foreach ({iterable} as ${plain}) {{")
        else:
            return ""

        body = self.statements(node.body, level + 1 + indent_extra)
        if body:
            lines.append(body)
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _is_items_call(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "items"

    def _dict_items_source(self, node: ast.expr) -> str | None:
        if self._is_items_call(node):
            return self.expr(node.func.value)
        return None

    def _stmt_AsyncFor(self, node: ast.AsyncFor, level: int) -> str:
        self.warn("`async for` was emitted as a regular loop.")
        return self._stmt_For(ast.For(target=node.target, iter=node.iter, body=node.body, orelse=node.orelse, type_comment=None), level)

    def _stmt_While(self, node: ast.While, level: int) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        if self.target == "python":
            lines.append(f"{pad}while {self.expr(node.test)}:")
            inner = self.statements(node.body, level + 1)
            lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        else:
            lines.append(f"{pad}while {self._cond(node.test)} {{")
            inner = self.statements(node.body, level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
        if node.orelse:
            self.warn("Python `while ... else` has no direct target equivalent; the else block runs after the loop.")
            extra = self.statements(node.orelse, level)
            if extra:
                lines.append(extra)
        return "\n".join(lines)

    # -- exception handling -----------------------------------------
    def _stmt_Try(self, node: ast.Try, level: int) -> str:
        if self.target == "python":
            return self._python_try(node, level)
        if self.target in {"javascript", "typescript"}:
            return self._javascript_try(node, level)
        if self.target == "go":
            return self._go_try(node, level)
        if self.target == "rust":
            return self._rust_try(node, level)
        return self._traditional_try(node, level)

    def _python_try(self, node: ast.Try, level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}try:"]
        inner = self.statements(node.body, level + 1)
        lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        for handler in node.handlers:
            header = "except"
            if handler.type is not None:
                header += f" {self.expr(handler.type)}"
            if handler.name:
                header += f" as {handler.name}"
            lines.append(f"{pad}{header}:")
            inner = self.statements(handler.body, level + 1)
            lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        if node.orelse:
            lines.append(f"{pad}else:")
            inner = self.statements(node.orelse, level + 1)
            lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        if node.finalbody:
            lines.append(f"{pad}finally:")
            inner = self.statements(node.finalbody, level + 1)
            lines.append(inner if inner else f"{self.indent(level + 1)}pass")
        return "\n".join(lines)

    def _javascript_try(self, node: ast.Try, level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}try {{"]
        inner = self.statements(node.body, level + 1)
        if inner:
            lines.append(inner)
        lines.append(f"{pad}}} catch (__error) {{")
        lines.extend(self._js_handlers(node.handlers, level + 1))
        lines.append(f"{pad}}}")
        if node.finalbody:
            lines.append(f"{pad}finally {{")
            inner = self.statements(node.finalbody, level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
        if node.orelse:
            self.warn("Python `try ... else` was emitted after the try/catch block.")
            extra = self.statements(node.orelse, level)
            if extra:
                lines.append(extra)
        return "\n".join(lines)

    def _js_handlers(self, handlers: list[ast.ExceptHandler], level: int) -> list[str]:
        pad = self.indent(level)
        if not handlers:
            return []
        if len(handlers) == 1 and handlers[0].type is None:
            return [self._handler_body(handlers[0], level, error_name="__error")]
        lines: list[str] = []
        for index, handler in enumerate(handlers):
            condition = self._js_exception_condition(handler.type)
            prefix = "if" if index == 0 else "else if"
            if condition is None:
                if index == 0:
                    lines.append(self._handler_body(handler, level, error_name="__error"))
                else:
                    lines.append(f"{pad}else {{")
                    lines.append(self._handler_body(handler, level + 1, error_name="__error"))
                    lines.append(f"{pad}}}")
                break
            lines.append(f"{pad}{prefix} ({condition}) {{")
            lines.append(self._handler_body(handler, level + 1, error_name="__error"))
            lines.append(f"{pad}}}")
        return lines

    def _js_exception_condition(self, type_node: ast.expr | None) -> str | None:
        if type_node is None:
            return None
        names = [self._call_name(item) or "" for item in type_node.elts] if isinstance(type_node, ast.Tuple) else [self._call_name(type_node) or ""]
        checks = []
        for name in names:
            mapped = self._exception_type(name)
            if mapped == "Error":
                return f"__error instanceof Error"
            checks.append(f"__error instanceof {mapped}")
        return " || ".join(checks) if checks else None

    def _handler_body(self, handler: ast.ExceptHandler, level: int, *, error_name: str) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        py_name = handler.name
        if py_name:
            if self.target == "php":
                lines.append(f"{pad}${py_name} = {error_name};")
            elif self.target == "go":
                lines.append(f"{pad}{py_name} := {error_name}")
            elif self.target == "javascript":
                lines.append(f"{pad}const {py_name} = {error_name};")
            elif self.target == "typescript":
                lines.append(f"{pad}const {py_name} = {error_name};")
            else:
                lines.append(f"{pad}var {py_name} = {error_name};")
        inner = self.statements(handler.body, level)
        if inner:
            lines.append(inner)
        return "\n".join(lines)

    def _go_try(self, node: ast.Try, level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}func() {{"]
        if node.handlers:
            lines.append(f"{self.indent(level + 1)}defer func() {{")
            lines.append(f"{self.indent(level + 2)}if __recovered := recover(); __recovered != nil {{")
            handler = node.handlers[0]
            lines.append(self._handler_body(handler, level + 3, error_name="__recovered"))
            lines.append(f"{self.indent(level + 2)}}}")
            lines.append(f"{self.indent(level + 1)}}}()")
            if len(node.handlers) > 1:
                self.warn("Go supports one recovery path; only the first `except` handler was emitted.")
        lines.append(self.statements(node.body, level + 1))
        lines.append(f"{pad}}}()")
        if node.finalbody:
            extra = self.statements(node.finalbody, level)
            if extra:
                lines.append(extra)
        if node.orelse:
            extra = self.statements(node.orelse, level)
            if extra:
                lines.append(extra)
        if node.handlers:
            self.warn("Go has no exceptions; the handler runs through `recover` inside a closure.")
        return "\n".join(lines)

    def _rust_try(self, node: ast.Try, level: int) -> str:
        self.warn("Rust has no exception handling; the `try` body was emitted as a plain block.")
        pad = self.indent(level)
        lines = [f"{pad}{{"]
        inner = self.statements(node.body, level + 1)
        if inner:
            lines.append(inner)
        lines.append(f"{pad}}}")
        if node.finalbody:
            extra = self.statements(node.finalbody, level)
            if extra:
                lines.append(extra)
        if node.orelse:
            extra = self.statements(node.orelse, level)
            if extra:
                lines.append(extra)
        return "\n".join(lines)

    def _traditional_try(self, node: ast.Try, level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}try {{"]
        inner = self.statements(node.body, level + 1)
        if inner:
            lines.append(inner)
        lines.append(f"{pad}}}")
        for handler in node.handlers:
            if handler.type is not None:
                exception = self._exception_type(self._call_name(handler.type) or "Exception")
            else:
                exception = {
                    "cpp": "const std::exception&",
                    "java": "Exception",
                    "csharp": "Exception",
                    "php": "\\Throwable",
                }.get(self.target, "Exception")
            if self.target == "cpp":
                header = f"{pad}catch (const {exception}& "
            elif self.target == "php":
                header = f"{pad}catch ({exception} "
            else:
                header = f"{pad}catch ({exception} "
            if handler.name:
                variable = f"${handler.name}" if self.target == "php" else handler.name
                header += f"{variable}) {{"
            else:
                header = header.rstrip() + (" ) {" if self.target != "cpp" else ") {")
            lines.append(header)
            inner = self.statements(handler.body, level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
        if node.finalbody:
            keyword = "finally"
            lines.append(f"{pad}{keyword} {{")
            inner = self.statements(node.finalbody, level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
        if node.orelse:
            self.warn("Python `try ... else` was emitted after the try/catch block.")
            extra = self.statements(node.orelse, level)
            if extra:
                lines.append(extra)
        return "\n".join(lines)

    # -- context managers -------------------------------------------
    def _stmt_With(self, node: ast.With, level: int) -> str:
        pad = self.indent(level)
        items = node.items
        if not items:
            return self.statements(node.body, level)
        lines: list[str] = []
        declarations: list[str] = []
        for item in items:
            expression = self._context_expression(item.context_expr)
            if item.optional_vars is None:
                declarations.append(self._line(level, expression))
                continue
            name = self._assign_target(item.optional_vars)
            if self.target == "python":
                declarations.append(f"{pad}{name} = {expression}")
            elif self.target in {"javascript", "typescript"}:
                declarations.append(f"{pad}const {name} = {expression};")
            elif self.target == "cpp":
                declarations.append(f"{pad}auto {name} = {expression};")
            elif self.target == "csharp":
                declarations.append(f"{pad}using var {name} = {expression};")
            elif self.target == "java":
                declarations.append(f"{pad}var {name} = {expression};")
            elif self.target == "rust":
                declarations.append(f"{pad}let mut {name} = {expression};")
            elif self.target == "go":
                declarations.append(f"{pad}{name} := {expression}")
            elif self.target == "php":
                declarations.append(f"{pad}{name if name.startswith('$') else '$' + name} = {expression};")
            else:
                declarations.append(self._line(level, f"{name} = {expression}"))

        if self.target == "csharp":
            # `using var` keeps the resource alive until the end of scope.
            lines.extend(declarations)
            lines.append(self.statements(node.body, level))
            return "\n".join(line for line in lines if line)
        if self.target == "python":
            for declaration in declarations:
                lines.append(declaration)
            lines.append(self.statements(node.body, level))
            return "\n".join(line for line in lines if line)

        lines.extend(declarations)
        body = self.statements(node.body, level)
        if body:
            lines.append(body)
        for item in items:
            if item.optional_vars is not None and self.target in {"javascript", "typescript", "java", "cpp", "rust", "go", "php"}:
                name = self._assign_target(item.optional_vars)
                if self.target == "go":
                    lines.append(f"{pad}defer {name}.Close()")
                elif self.target == "rust":
                    lines.append(f"{pad}drop({name});")
                elif self.target == "cpp":
                    lines.append(f"{pad}{name}.close();")
                elif self.target == "php":
                    handle = name if name.startswith("$") else f"${name}"
                    lines.append(f"{pad}fclose({handle});")
                else:
                    lines.append(f"{pad}{name}.close();")
        return "\n".join(line for line in lines if line)

    def _context_expression(self, node: ast.expr) -> str:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            path = self.expr(node.args[0]) if node.args else self.string("")
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            if self.target in {"javascript", "typescript"}:
                if "w" in mode or "a" in mode:
                    return path
                return f'require("fs").readFileSync({path}, "utf8")'
            if self.target == "csharp":
                if "w" in mode or "a" in mode:
                    return path
                return f"System.IO.File.ReadAllText({path})"
            if self.target == "java":
                if "w" in mode or "a" in mode:
                    return f"java.nio.file.Path.of({path})"
                return f"java.nio.file.Files.readString(java.nio.file.Path.of({path}))"
            if self.target == "cpp":
                keyword = "std::ofstream" if ("w" in mode or "a" in mode) else "std::ifstream"
                return f"{keyword}({path})"
            if self.target == "rust":
                if "w" in mode or "a" in mode:
                    return f"std::fs::File::create({path}).unwrap()"
                return f"std::fs::read_to_string({path}).unwrap()"
            if self.target == "go":
                self.go_imports.add("os")
                return f"os.Open({path})" if not ("w" in mode or "a" in mode) else f"os.Create({path})"
            if self.target == "php":
                return f'fopen({path}, "{mode or "r"}")'
        return self.expr(node)

    def _stmt_AsyncWith(self, node: ast.AsyncWith, level: int) -> str:
        self.warn("`async with` was emitted as a regular context manager.")
        return self._stmt_With(ast.With(items=node.items, body=node.body, type_comment=None), level)

    # -- match -------------------------------------------------------
    def _stmt_Match(self, node: ast.Match, level: int) -> str:
        if self.target == "rust":
            return self._rust_match(node, level)
        if self.target == "python":
            pad = self.indent(level)
            lines = [f"{pad}match {self.expr(node.subject)}:"]
            for case in node.cases:
                guard = f" if {self.expr(case.guard)}" if case.guard else ""
                lines.append(f"{self.indent(level + 1)}case {self.expr(case.pattern)}{guard}:")
                inner = self.statements(case.body, level + 2)
                lines.append(inner if inner else f"{self.indent(level + 2)}pass")
            return "\n".join(lines)
        return self._match_as_if(node, level)

    def _pattern_condition(self, subject: str, pattern: ast.pattern, *, first: bool) -> tuple[str | None, str | None]:
        if isinstance(pattern, ast.MatchValue):
            return f"{subject} {'===' if self.target in {'javascript', 'typescript'} else '=='} {self.expr(pattern.value)}", None
        if isinstance(pattern, ast.MatchSingleton):
            literal = self.bool_literal(pattern.value) if isinstance(pattern.value, bool) else self.none()
            return f"{subject} {'===' if self.target in {'javascript', 'typescript'} else '=='} {literal}", None
        if isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                condition, binding = self._pattern_condition(subject, pattern.pattern, first=first)
                return condition, pattern.name or binding
            return None, pattern.name
        if isinstance(pattern, ast.MatchOr):
            checks = [self._pattern_condition(subject, item, first=first)[0] for item in pattern.patterns]
            checks = [check for check in checks if check]
            return " || ".join(f"({check})" for check in checks) if checks else None, None
        return None, None

    def _match_as_if(self, node: ast.Match, level: int) -> str:
        subject_text = self.expr(node.subject)
        pad = self.indent(level)
        lines: list[str] = []
        temporary = "__match"
        lines.append(self._line(level, f"var {temporary} = {subject_text}"))
        first = True
        for case in node.cases:
            condition, binding = self._pattern_condition(temporary, case.pattern, first=first)
            if condition is None:
                if first:
                    if binding:
                        lines.append(self._line(level, f"{binding} = {temporary}"))
                    lines.append(self.statements(case.body, level))
                else:
                    lines.append(f"{pad}else {{")
                    if binding:
                        lines.append(self._line(level + 1, f"{binding} = {temporary}"))
                    lines.append(self.statements(case.body, level + 1))
                    lines.append(f"{pad}}}")
                break
            if case.guard is not None:
                condition = f"({condition}) && ({self.expr(case.guard)})"
            keyword = "if" if first else "else if"
            lines.append(f"{pad}{keyword} ({condition}) {{")
            if binding:
                lines.append(self._line(level + 1, f"{binding} = {temporary}"))
            lines.append(self.statements(case.body, level + 1))
            lines.append(f"{pad}}}")
            first = False
        return "\n".join(line for line in lines if line)

    def _rust_match(self, node: ast.Match, level: int) -> str:
        pad = self.indent(level)
        lines = [f"{pad}match {self.expr(node.subject)} {{"]
        for case in node.cases:
            pattern = self._rust_pattern(case.pattern)
            guard = f" if {self.expr(case.guard)}" if case.guard else ""
            lines.append(f"{self.indent(level + 1)}{pattern}{guard} => {{")
            lines.append(self.statements(case.body, level + 2))
            lines.append(f"{self.indent(level + 1)}}}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _rust_pattern(self, pattern: ast.pattern) -> str:
        if isinstance(pattern, ast.MatchValue):
            if isinstance(pattern.value, ast.Constant) and isinstance(pattern.value.value, str):
                return self.string(pattern.value.value)
            return self.expr(pattern.value)
        if isinstance(pattern, ast.MatchSingleton):
            if isinstance(pattern.value, bool):
                return self.bool_literal(pattern.value)
            return self.none()
        if isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                return self._rust_pattern(pattern.pattern)
            return pattern.name or "_"
        if isinstance(pattern, ast.MatchOr):
            return " | ".join(self._rust_pattern(item) for item in pattern.patterns)
        return "_"

    # -- declarations ------------------------------------------------
    def _stmt_FunctionDef(self, node: ast.FunctionDef, level: int) -> str:
        return self.emit_function(node, level)

    def _stmt_AsyncFunctionDef(self, node: ast.AsyncFunctionDef, level: int) -> str:
        return self.emit_function(node, level, is_async=True)

    def _stmt_ClassDef(self, node: ast.ClassDef, level: int) -> str:
        return self.emit_class(node, level)
