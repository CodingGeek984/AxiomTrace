"""Function and class declaration translation for the polyglot emitter."""

from __future__ import annotations

import ast

from services.transpiler.base import extract_docstring, function_params, has_return
from services.transpiler.emitter_base import (
    EmitterBase,
    _normalise_type_name,
    reassigned_names,
)


_DUNDER_NAMES = {
    "__str__": {
        "javascript": "toString", "typescript": "toString", "java": "toString",
        "csharp": "ToString", "cpp": "to_string", "rust": "to_string",
        "go": "String", "php": "__toString", "python": "__str__",
    },
    "__repr__": {
        "javascript": "toString", "typescript": "toString", "java": "toString",
        "csharp": "ToString", "cpp": "to_string", "rust": "to_string",
        "go": "String", "php": "__toString", "python": "__repr__",
    },
    "__len__": {
        "javascript": "size", "typescript": "size", "java": "size",
        "csharp": "Count", "cpp": "size", "rust": "len",
        "go": "Len", "php": "__len", "python": "__len__",
    },
    "__eq__": {
        "javascript": "equals", "typescript": "equals", "java": "equals",
        "csharp": "Equals", "cpp": "operator==", "rust": "eq",
        "go": "Equals", "php": "__eq", "python": "__eq__",
    },
}


class DeclarationMixin(EmitterBase):
    """Emit functions and classes for every supported target language."""

    # -- pre-scan ----------------------------------------------------
    def prescan(self, body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function_names.add(node.name)
                self.function_target_names[node.name] = self._function_name(node.name)
                kind = self.infer_return_kind(node.body)
                if kind:
                    self.function_returns[node.name] = kind
            elif isinstance(node, ast.ClassDef):
                self.class_names.add(node.name)
                self._prepare_class(node)

    def _prepare_class(self, node: ast.ClassDef) -> None:
        if node.name in getattr(self, "_prepared_classes", set()):
            return
        if not hasattr(self, "_prepared_classes"):
            self._prepared_classes: set[str] = set()
        self._prepared_classes.add(node.name)

        fields: list[tuple[str, str | None, ast.expr | None]] = []
        annotations: dict[str, ast.expr | None] = {}
        seen: set[str] = set()
        indexes: dict[str, int] = {}

        def add_field(name: str, kind: str | None, default: ast.expr | None, annotation: ast.expr | None = None) -> None:
            if not name or name.startswith("__"):
                return
            if name in seen:
                index = indexes[name]
                current_name, current_kind, current_default = fields[index]
                if current_kind is None and kind is not None:
                    # ``self.value = value`` in ``__init__`` was scanned before
                    # the constructor parameter type was known; upgrade it now
                    # so statically typed targets use a precise field type.
                    fields[index] = (current_name, kind, current_default)
                    annotations[name] = annotation
                return
            seen.add(name)
            indexes[name] = len(fields)
            fields.append((name, kind, default))
            annotations[name] = annotation

        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                kind = self._kind_from_annotation(item.annotation)
                add_field(item.target.id, kind, item.value, item.annotation)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        add_field(target.id, self.py_type_of(item.value), item.value)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_name = self._method_name(node.name, item)
                self.decl_method_names.setdefault(node.name, {})[item.name] = method_name
                self.method_target_names.setdefault(item.name, method_name)
                self._register_method_defaults(node.name, item)
                if "property" in self._decorators(item):
                    self.property_methods.add((node.name, item.name))
                if item.name == "__init__":
                    # Constructor parameters are the source of truth for field
                    # types when the class body does not annotate them.
                    init_params = {
                        record["name"]: record
                        for record in self._parameter_records(
                            item, in_class=True, static=False, property_method=False
                        )
                    }
                    for statement in item.body:
                        for target in self._self_assignment_targets(statement):
                            default = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
                            annotation = statement.annotation if isinstance(statement, ast.AnnAssign) else None
                            kind = self._kind_from_annotation(annotation) if annotation is not None else None
                            if kind is None and isinstance(default, ast.Name):
                                record = init_params.get(default.id)
                                if record is not None:
                                    kind = record.get("kind")
                                    annotation = record.get("annotation")
                            if kind is None:
                                kind = self.py_type_of(default)
                            add_field(target.attr, kind, None, annotation)
            elif isinstance(item, ast.ClassDef):
                self.class_names.add(item.name)

        # Fields assigned to self outside __init__ are part of the object state.
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name != "__init__":
                for statement in item.body:
                    for target in self._self_assignment_targets(statement):
                        if target.attr not in seen:
                            add_field(target.attr, self.py_type_of(getattr(statement, "value", None)), None)

        self.class_fields[node.name] = fields
        self.class_field_annotations[node.name] = annotations
        if "dataclass" in self._decorators(node) and any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"
            for item in node.body
        ) is False:
            defaults = {
                index: default
                for index, (_name, _kind, default) in enumerate(fields)
                if default is not None
            }
            if defaults:
                self.constructor_defaults[node.name] = {"max": len(fields), "defaults": defaults}
        field_names = {name: (self.go_export(name) if self.target == "go" else name) for name, _, _ in fields}
        self.decl_field_names[node.name] = field_names
        self._go_field_names[node.name] = field_names

        method_names = self.decl_method_names.setdefault(node.name, {})
        self._go_method_names[node.name] = {
            name: self.go_export(target) for name, target in method_names.items()
        }

    def _field_type(self, class_name: str, name: str, kind: str | None, fallback: str) -> str:
        """Map a class field using its annotation when one is available."""
        annotation = self.class_field_annotations.get(class_name, {}).get(name)
        mapped = self.map_type(annotation) if annotation is not None else None
        if not mapped:
            mapped = self.py_type_to_target(kind) if kind else None
        if not mapped or mapped in {"void", "nullptr"}:
            return fallback
        return mapped

    def _go_zero_value(self, type_text: str) -> str:
        if type_text.startswith(("*", "[]", "map", "func")):
            return "nil"
        if type_text == "string":
            return '""'
        if type_text == "bool":
            return "false"
        if type_text in {"int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "float32", "float64", "byte", "rune", "any"}:
            return "0" if type_text != "any" else "nil"
        if type_text.startswith("interface"):
            return "nil"
        return f"{type_text}{{}}"

    def _register_method_defaults(self, class_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        records = self._parameter_records(node, in_class=True, static=False, property_method=False)
        defaults = {
            index: record["default"]
            for index, record in enumerate(records)
            if record["default"] is not None
        }
        if not defaults:
            return
        info = {"max": len(records), "defaults": defaults}
        if node.name == "__init__":
            self.constructor_defaults[class_name] = info
        self.method_defaults[node.name] = info

    def _self_assignment_targets(self, statement: ast.stmt) -> list[ast.Attribute]:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
            targets = [statement.target]
        result: list[ast.Attribute] = []
        for target in targets:
            for item in ast.walk(target):
                if (
                    isinstance(item, ast.Attribute)
                    and isinstance(item.value, ast.Name)
                    and item.value.id in {"self", "cls"}
                ):
                    result.append(item)
        return result

    def _kind_from_annotation(self, annotation: ast.expr | None) -> str | None:
        if annotation is None:
            return None
        if isinstance(annotation, ast.Constant):
            return "none" if annotation.value is None else None
        if isinstance(annotation, ast.Name):
            return _normalise_type_name(annotation.id)
        if isinstance(annotation, ast.Attribute):
            return _normalise_type_name(annotation.attr)
        if isinstance(annotation, ast.Subscript):
            base = annotation.value
            name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
            return _normalise_type_name(name)
        if isinstance(annotation, ast.BinOp):
            return self._kind_from_annotation(annotation.left)
        return None

    def _decorators(self, node: ast.AST) -> list[str]:
        decorators = getattr(node, "decorator_list", [])
        names: list[str] = []
        for decorator in decorators:
            if isinstance(decorator, ast.Name):
                names.append(decorator.id)
            elif isinstance(decorator, ast.Attribute):
                names.append(decorator.attr)
            elif isinstance(decorator, ast.Call):
                names.append(self._call_name(decorator.func) or "")
        return [name for name in names if name]

    def _method_name(self, class_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        if node.name in _DUNDER_NAMES:
            return _DUNDER_NAMES[node.name].get(self.target, node.name)
        if self.target in {"javascript", "typescript", "java"}:
            return self._snake_to_camel(node.name)
        if self.target == "csharp":
            camel = self._snake_to_camel(node.name)
            return camel[:1].upper() + camel[1:] if camel else node.name
        if self.target == "go":
            return self.go_export(node.name)
        return node.name

    def _function_name(self, name: str) -> str:
        if self.target in {"javascript", "typescript", "java"}:
            mapped = self._snake_to_camel(name)
        elif self.target == "csharp":
            camel = self._snake_to_camel(name)
            mapped = camel[:1].upper() + camel[1:] if camel else name
        else:
            mapped = name
        # C++/Go/Rust reserve the entry-point name for the generated wrapper.
        if name == "main":
            if self.target == "csharp":
                return "Run"
            if self.target in {"cpp", "go", "rust", "java"}:
                return "run"
        return mapped

    # -- functions ---------------------------------------------------
    def emit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        level: int = 0,
        *,
        is_async: bool = False,
        in_class: bool = False,
    ) -> str:
        is_async = is_async or isinstance(node, ast.AsyncFunctionDef)
        decorators = self._decorators(node)
        static = "staticmethod" in decorators or "classmethod" in decorators
        property_method = "property" in decorators
        class_name = self.current_class
        pad = self.indent(level)
        docstring, body = extract_docstring(list(node.body))
        params = self._parameter_records(node, in_class=in_class, static=static, property_method=property_method)
        return_type = self._return_type(node, in_class=in_class)
        name = (
            self.decl_method_names.get(class_name or "", {}).get(node.name)
            if in_class
            else self._function_name(node.name)
        ) or node.name

        old_params = self.param_types
        old_declared = self.declared_stack
        old_types = self.var_types_stack
        old_class = self.current_class
        old_self_alias = self.self_alias
        old_go_defaults = self.go_default_exprs
        old_reassigned = self.reassigned_names
        self.param_types = dict(old_params)
        self.declared_stack = [set(self.declared_stack[0])]
        self.var_types_stack = [dict(self.var_types_stack[0])]
        self.current_class = class_name if in_class else None
        self.self_alias = None
        self.go_default_exprs = {}
        self.reassigned_names = reassigned_names(body)
        for record in params:
            self.param_types[record["name"]] = record["kind"] or "object"
            self.declare(record["name"], record["kind"])

        try:
            header = self._function_header(
                node,
                name=name,
                params=params,
                return_type=return_type,
                level=level,
                is_async=is_async,
                in_class=in_class,
                static=static,
                property_method=property_method,
            )
            body_lines: list[str] = []
            if docstring and self.target == "python":
                body_lines.append(self._python_docstring(docstring, level + 1))
            emitted = self.statements(body, level + 1)
            if emitted:
                body_lines.append(emitted)
            elif self.target == "python":
                body_lines.append(f"{self.indent(level + 1)}pass")
            if self.target == "python":
                lines = [f"{pad}@{decorator}" for decorator in decorators]
                lines.append(header)
                lines.extend(body_lines)
                return "\n".join(line for line in lines if line)
            lines: list[str] = []
            if docstring:
                lines.append(self._comment_lines(level, docstring))
            if self.target == "rust":
                attribute = self._rust_attributes(node)
                if attribute:
                    lines.append(f"{pad}{attribute}")
            lines.append(header)
            lines.extend(body_lines)
            lines.append(f"{pad}}}")
            if self.target == "java" and in_class and node.name != "__str__":
                trailing = 0
                for record in reversed(params):
                    if record["default"] is not None and record["param_kind"] not in {"vararg", "kwarg"}:
                        trailing += 1
                    else:
                        break
                for keep in range(len(params) - 1, len(params) - trailing - 1, -1):
                    subset = params[:keep]
                    forwarded = [record["name"] for record in subset]
                    forwarded.extend(self.expr(record["default"]) for record in params[keep:])
                    lines.append(f"{pad}public {'static ' if static else ''}{return_type or 'void'} {name}({self._java_params(subset)}) {{")
                    target = f"{class_name}.{name}" if static else f"this.{name}"
                    if return_type and return_type != "void":
                        lines.append(f"{self.indent(level + 1)}return {target}({', '.join(forwarded)});")
                    else:
                        lines.append(f"{self.indent(level + 1)}{target}({', '.join(forwarded)});")
                    lines.append(f"{pad}}}")
            return "\n".join(line for line in lines if line)
        finally:
            self.param_types = old_params
            self.declared_stack = old_declared
            self.var_types_stack = old_types
            self.current_class = old_class
            self.self_alias = old_self_alias
            self.go_default_exprs = old_go_defaults
            self.reassigned_names = old_reassigned

    def _python_docstring(self, docstring: str, level: int) -> str:
        pad = self.indent(level)
        escaped = docstring.replace('"""', '\\"\\"\\"')
        if "\n" not in escaped:
            return f'{pad}"""{escaped}"""'
        lines = escaped.splitlines()
        return "\n".join([f'{pad}"""'] + [f"{pad}{line}" for line in lines] + [f'{pad}"""'])

    def _rust_attributes(self, node: ast.AST) -> str:
        decorators = self._decorators(node)
        attributes = []
        if "staticmethod" in decorators:
            attributes.append("#[allow(dead_code)]")
        return " ".join(attributes)

    def _parameter_records(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        in_class: bool,
        static: bool,
        property_method: bool,
    ) -> list[dict]:
        records: list[dict] = []
        raw = function_params(node)
        if property_method:
            raw = [param for param in raw if param.name not in {"self", "cls"}]
        for index, param in enumerate(raw):
            if in_class and not static and param.name in {"self", "cls"}:
                continue
            kind = None
            if param.annotation:
                mapped_kind = self._kind_from_annotation(ast.parse(param.annotation, mode="eval").body)
                kind = mapped_kind
            if kind is None:
                kind = self.infer_param_kind(node, param.name)
            annotation_node = None
            if param.annotation:
                try:
                    annotation_node = ast.parse(param.annotation, mode="eval").body
                except SyntaxError:
                    annotation_node = None
            target_type = self.map_type(annotation_node) if annotation_node is not None else None
            if target_type is None and kind:
                target_type = self.py_type_to_target(kind)
            records.append(
                {
                    "name": param.name,
                    "kind": kind,
                    "target_type": target_type,
                    "default": param.default,
                    "param_kind": param.kind,
                    "index": index,
                    "annotation": annotation_node,
                }
            )
        return records

    def _param_target_type(self, record: dict, *, template_name: str | None = None) -> str:
        if template_name:
            return template_name
        target_type = record.get("target_type")
        kind = record.get("kind")
        if self.target in {"javascript"}:
            return ""
        if self.target == "typescript":
            return target_type or "any"
        if self.target == "php":
            if kind and kind not in {"object", "none"} and target_type:
                return target_type
            return ""
        if self.target == "go":
            return target_type or "any"
        if self.target == "rust":
            return target_type or "i64"
        if self.target == "csharp":
            return target_type or "object"
        if self.target == "java":
            return target_type or "Object"
        if self.target == "cpp":
            return target_type or "std::any"
        return target_type or ""

    def _function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        name: str,
        params: list[dict],
        return_type: str | None,
        level: int,
        is_async: bool,
        in_class: bool,
        static: bool,
        property_method: bool,
    ) -> str:
        pad = self.indent(level)
        if self.target == "python":
            signature = self._python_signature(node, name)
            prefix = "async " if is_async else ""
            annotation = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
            return f"{pad}{prefix}def {signature}{annotation}:"
        if self.target in {"javascript", "typescript"}:
            rendered = self._js_params(params)
            ts_return = f": {return_type or 'void'}" if self.target == "typescript" and not property_method else ""
            if in_class:
                prefix = "static " if static else ""
                if is_async:
                    prefix += "async "
                if property_method:
                    return f"{pad}get {name}() {ts_return} {{"
                return f"{pad}{prefix}{name}({rendered}){ts_return} {{"
            prefix = "async " if is_async else ""
            return f"{pad}{prefix}function {name}({rendered}){ts_return} {{"
        if self.target == "cpp":
            template, rendered = self._cpp_params(params)
            prefix = "static " if (in_class and static) else ""
            const = " const" if property_method else ""
            return_type = return_type or "void"
            header = f"{pad}{prefix}{return_type} {name}({rendered}){const} {{"
            return f"{template}{header}"
        if self.target == "csharp":
            rendered = self._csharp_params(params)
            access = "public " if in_class else "public static "
            prefix = access
            if in_class and static and not prefix.endswith("static "):
                prefix = "public static "
            if in_class and node.name == "__str__":
                return f"{pad}public override string ToString() {{"
            if property_method:
                return f"{pad}public {return_type or 'object'} {name} {{ get; }}"
            if is_async:
                self.extra_imports.add("System.Threading.Tasks")
                task_type = "Task" if (return_type or "void") == "void" else f"Task<{return_type}>"
                return f"{pad}{prefix}async {task_type} {name}({rendered}) {{"
            return f"{pad}{prefix}{return_type or 'void'} {name}({rendered}) {{"
        if self.target == "java":
            rendered = self._java_params(params)
            access = "public " if in_class else "public static "
            prefix = access
            if in_class and static:
                prefix = "public static "
            if is_async:
                self.warn("Java has no `async` functions; the method was emitted synchronously.")
            if in_class and node.name == "__str__":
                return f"{pad}@Override\n{pad}public String toString() {{"
            return f"{pad}{prefix}{return_type or 'void'} {name}({rendered}) {{"
        if self.target == "rust":
            rendered = self._rust_params(params)
            prefix = "pub " if in_class else ""
            if is_async:
                prefix += "async "
            receiver = "" if static or not in_class else "&self"
            if in_class and not static:
                rendered = f"{receiver}, {rendered}" if rendered else receiver
            receiver_mut = ""
            if in_class and not static and self._method_mutates(node):
                receiver_mut = "&mut self"
                rendered = f"{receiver_mut}, {rendered[len('&self'):].lstrip(', ')}" if rendered.startswith("&self") else receiver_mut
            annotation = f" -> {return_type}" if return_type else ""
            return f"{pad}{prefix}fn {name}({rendered}){annotation} {{"
        if self.target == "go":
            rendered = self._go_params(params)
            if in_class:
                return_type_text = f" {return_type}" if return_type else ""
                return f"{pad}func (self *{self.current_class}) {name}({rendered}){return_type_text} {{"
            return_type_text = f" {return_type}" if return_type else ""
            return f"{pad}func {name}({rendered}){return_type_text} {{"
        if self.target == "php":
            rendered = self._php_params(params)
            access = "public " if in_class else ""
            prefix = f"{access}static " if (in_class and static) else access
            if is_async:
                self.warn("PHP has no native `async` functions; the function was emitted synchronously.")
            annotation = f": {return_type}" if return_type else ""
            return f"{pad}{prefix}function {name}({rendered}){annotation} {{"
        return f"{pad}function {name}() {{"

    def _python_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> str:
        args = node.args
        positional = list(args.posonlyargs) + list(args.args)
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        parts: list[str] = []
        positional_only_count = len(args.posonlyargs)
        for index, (arg, default) in enumerate(zip(positional, defaults)):
            text = arg.arg
            if arg.annotation is not None:
                text += f": {ast.unparse(arg.annotation)}"
            if default is not None:
                text += f" = {self.expr(default)}"
            parts.append(text)
            if positional_only_count and index + 1 == positional_only_count:
                parts.append("/")
        if args.vararg:
            text = f"*{args.vararg.arg}"
            if args.vararg.annotation is not None:
                text += f": {ast.unparse(args.vararg.annotation)}"
            parts.append(text)
        elif args.kwonlyargs:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            text = arg.arg
            if arg.annotation is not None:
                text += f": {ast.unparse(arg.annotation)}"
            if default is not None:
                text += f" = {self.expr(default)}"
            parts.append(text)
        if args.kwarg:
            text = f"**{args.kwarg.arg}"
            if args.kwarg.annotation is not None:
                text += f": {ast.unparse(args.kwarg.annotation)}"
            parts.append(text)
        return f"{name}({', '.join(parts)})"

    def _js_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"...{record['name']}")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(record["name"])
                continue
            text = record["name"]
            if self.target == "typescript":
                text += f": {self._param_target_type(record)}"
            if record["default"] is not None:
                text += f" = {self.expr(record['default'])}"
            parts.append(text)
        if any(record["param_kind"] == "kwarg" for record in params):
            self.warn("Python `**kwargs` was emitted as a plain parameter in the dynamic target.")
        return ", ".join(parts)

    def _cpp_params(self, params: list[dict]) -> tuple[str, str]:
        parts: list[str] = []
        template_names: list[str] = []
        for index, record in enumerate(params):
            if record["param_kind"] == "vararg":
                parts.append(f"std::vector<std::any> {record['name']}")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"std::map<std::string, std::any> {record['name']}")
                continue
            template_name = None
            if record.get("target_type") is None and record.get("kind") is None:
                template_name = f"T{index}"
                template_names.append(template_name)
            type_text = self._param_target_type(record, template_name=template_name)
            text = f"{type_text} {record['name']}"
            if record["default"] is not None:
                if type_text.startswith("std::optional") and self._is_none_constant(record["default"]):
                    text += " = std::nullopt"
                else:
                    text += f" = {self.expr(record['default'])}"
            parts.append(text)
        if any(record["param_kind"] == "kwarg" for record in params):
            self.warn("`**kwargs` is represented as a `std::map` in C++.")
        if template_names:
            template = "template <" + ", ".join(f"typename {name}" for name in template_names) + ">\n"
        else:
            template = ""
        return template, ", ".join(parts)

    def _csharp_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"params object[] {record['name']}")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"Dictionary<string, object> {record['name']}")
                continue
            text = f"{self._param_target_type(record)} {record['name']}"
            if record["default"] is not None:
                text += f" = {self.expr(record['default'])}"
            parts.append(text)
        return ", ".join(parts)

    def _java_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"Object... {record['name']}")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"Map<String, Object> {record['name']}")
                continue
            if record["default"] is not None:
                self.warn("Java has no default arguments; the default value was dropped.")
            parts.append(f"{self._param_target_type(record)} {record['name']}")
        return ", ".join(parts)

    def _rust_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"{record['name']}: Vec<i64>")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"{record['name']}: std::collections::HashMap<String, String>")
                continue
            if record["default"] is not None:
                self.warn("Rust has no default arguments; the default value was dropped.")
            parts.append(f"{record['name']}: {self._param_target_type(record)}")
        return ", ".join(parts)

    def _go_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"{record['name']} ...any")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"{record['name']} map[string]any")
                continue
            if record["default"] is not None:
                # Go has no default arguments; accept the value as a trailing
                # variadic so callers that omit it still compile.  References
                # to the parameter resolve through ``axiomFirst``.  Pointer
                # element types are dereferenced so call sites pass plain
                # values; constructors re-wrap them into pointers.
                target_type = self._param_target_type(record) or "any"
                if target_type.startswith("*"):
                    element = target_type[1:] or "any"
                    parts.append(f"{record['name']} ...{element}")
                    if self._is_none_constant(record["default"]):
                        fallback = self._go_zero_value(element)
                    else:
                        fallback = self.expr(record["default"])
                else:
                    parts.append(f"{record['name']} ...{target_type}")
                    fallback = self.expr(record["default"])
                self.go_default_exprs[record["name"]] = (
                    f"axiomFirst({record['name']}, {fallback})"
                )
                self.go_helpers.add("first")
                continue
            parts.append(f"{record['name']} {self._param_target_type(record)}")
        return ", ".join(parts)

    def _php_params(self, params: list[dict]) -> str:
        parts: list[str] = []
        for record in params:
            if record["param_kind"] == "vararg":
                parts.append(f"...${record['name']}")
                continue
            if record["param_kind"] == "kwarg":
                parts.append(f"array ${record['name']}")
                continue
            type_text = self._param_target_type(record)
            prefix = f"{type_text} " if type_text else ""
            text = f"{prefix}${record['name']}"
            if record["default"] is not None:
                text += f" = {self.expr(record['default'])}"
            parts.append(text)
        return ", ".join(parts)

    def _return_type(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, in_class: bool) -> str | None:
        if node.name == "__init__" and in_class:
            return None
        if node.name in _DUNDER_NAMES and in_class:
            special = {
                "csharp": "string" if node.name in {"__str__", "__repr__"} else "int",
                "java": "String" if node.name in {"__str__", "__repr__"} else "int",
                "cpp": "std::string" if node.name in {"__str__", "__repr__"} else "std::size_t",
                "rust": "String" if node.name in {"__str__", "__repr__"} else "usize",
                "go": "string" if node.name in {"__str__", "__repr__"} else "int",
                "php": "string" if node.name in {"__str__", "__repr__"} else "int",
                "javascript": None, "typescript": "string" if node.name in {"__str__", "__repr__"} else "number",
            }.get(self.target)
            if special or special is None:
                return special
        if node.returns is not None:
            mapped = self.map_type(node.returns, returns=True)
            if mapped:
                if mapped == self.primitive("none", returns=True):
                    if self.target in {"rust", "go"}:
                        return None
                    if self.target == "python":
                        return "None"
                    return "void" if self.target in {"java", "csharp", "cpp"} else None
                return mapped
        kind = self.infer_return_kind(node.body)
        returns_value = has_return(node.body)
        if self.target == "javascript":
            return None
        if self.target == "typescript":
            return self.py_type_to_target(kind, returns=True) if kind else ("any" if returns_value else "void")
        if self.target == "cpp":
            if not returns_value:
                return "void"
            return self.py_type_to_target(kind, returns=True) if kind else "auto"
        if self.target == "csharp":
            if not returns_value:
                return "void"
            return self.py_type_to_target(kind, returns=True) if kind else "object"
        if self.target == "java":
            if not returns_value:
                return "void"
            return self.py_type_to_target(kind, returns=True) if kind else "Object"
        if self.target == "rust":
            if not returns_value:
                return None
            return self.py_type_to_target(kind, returns=True) if kind else "i64"
        if self.target == "go":
            if not returns_value:
                return None
            return self.py_type_to_target(kind, returns=True) if kind else "any"
        if self.target == "php":
            if not returns_value:
                return None
            return self.py_type_to_target(kind, returns=True) if kind else None
        if self.target == "python":
            return None
        return None

    def _method_mutates(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for item in ast.walk(node):
            if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                        return True
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
                if isinstance(item.func.value, ast.Name) and item.func.value.id == "self":
                    if item.func.attr in {"append", "extend", "insert", "pop", "clear", "update", "sort", "reverse", "add", "remove", "discard"}:
                        return True
        return False

    # -- classes -----------------------------------------------------
    def emit_class(self, node: ast.ClassDef, level: int) -> str:
        self._prepare_class(node)
        docstring, _ = extract_docstring(list(node.body))
        old_class = self.current_class
        old_fields = self.current_class_fields
        self.current_class = node.name
        self.current_class_fields = {
            name: (kind or "object") for name, kind, _ in self.class_fields.get(node.name, [])
        }
        try:
            if self.target == "python":
                return self._python_class(node, level, docstring)
            if self.target in {"javascript", "typescript"}:
                return self._js_class(node, level, docstring)
            if self.target == "cpp":
                return self._cpp_class(node, level, docstring)
            if self.target == "csharp":
                return self._csharp_class(node, level, docstring)
            if self.target == "java":
                return self._java_class(node, level, docstring)
            if self.target == "rust":
                return self._rust_class(node, level, docstring)
            if self.target == "go":
                return self._go_class(node, level, docstring)
            if self.target == "php":
                return self._php_class(node, level, docstring)
            return ""
        finally:
            self.current_class = old_class
            self.current_class_fields = old_fields

    def _bases(self, node: ast.ClassDef) -> list[str]:
        return [self.expr(base) for base in node.bases]

    def _class_methods(self, node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return [
            item for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

    def _constructor(self, node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        existing = next((item for item in self._class_methods(node) if item.name == "__init__"), None)
        if existing is not None:
            return existing
        if "dataclass" in self._decorators(node):
            return self._synthetic_dataclass_constructor(node)
        return None

    def _synthetic_dataclass_constructor(self, node: ast.ClassDef) -> ast.FunctionDef | None:
        fields = self.class_fields.get(node.name, [])
        if not fields:
            return None
        args: list[ast.arg] = []
        defaults: list[ast.expr | None] = []
        body: list[ast.stmt] = []
        annotations = self.class_field_annotations.get(node.name, {})
        for name, kind, default in fields:
            annotation = annotations.get(name)
            if annotation is None and kind:
                annotation = ast.Name(id=kind)
            args.append(ast.arg(arg=name, annotation=annotation))
            defaults.append(default)
            body.append(
                ast.Assign(
                    targets=[ast.Attribute(value=ast.Name(id="self"), attr=name, ctx=ast.Store())],
                    value=ast.Name(id=name, ctx=ast.Load()),
                )
            )
        trailing: list[ast.expr] = []
        for value in reversed(defaults):
            if value is None:
                break
            trailing.insert(0, value)
        arguments = ast.arguments(
            posonlyargs=[],
            args=args,
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=trailing,
        )
        return ast.FunctionDef(
            name="__init__",
            args=arguments,
            body=body,
            decorator_list=[],
            returns=None,
            type_comment=None,
        )

    def _method_doc(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        return extract_docstring(list(node.body))[0]

    def _python_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = ", ".join(self._bases(node))
        header = f"{pad}class {node.name}" + (f"({bases})" if bases else "") + ":"
        lines: list[str] = []
        for decorator in node.decorator_list:
            lines.append(f"{pad}@{ast.unparse(decorator)}")
        lines.append(header)
        body_level = level + 1
        if docstring:
            lines.append(self._python_docstring(docstring, body_level))
        body = self.statements(self._non_docstring_body(node.body), body_level)
        if body:
            lines.append(body)
        elif not docstring:
            lines.append(f"{self.indent(body_level)}pass")
        return "\n".join(lines)

    def _non_docstring_body(self, body: list[ast.stmt]) -> list[ast.stmt]:
        _, remaining = extract_docstring(list(body))
        return remaining

    def _js_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = self._bases(node)
        header = f"{pad}class {node.name}" + (f" extends {bases[0]}" if bases else "") + " {"
        lines = [header]
        if docstring:
            lines.append(self._comment_lines(level + 1, docstring))
        fields = self.class_fields.get(node.name, [])
        for name, kind, default in fields:
            field_name = self.decl_field_names[node.name].get(name, name)
            if self.target == "typescript":
                type_text = self._field_type(node.name, name, kind, "any")
                if default is not None:
                    lines.append(f"{self.indent(level + 1)}{field_name}: {type_text} = {self.expr(default)};")
                else:
                    lines.append(f"{self.indent(level + 1)}{field_name}: {type_text};")
            elif default is not None:
                lines.append(f"{self.indent(level + 1)}{field_name} = {self.expr(default)};")
            else:
                lines.append(f"{self.indent(level + 1)}{field_name};")
        constructor = self._constructor(node)
        if constructor is not None:
            lines.append(self._js_constructor(constructor, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _js_constructor(self, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        params = self._parameter_records(node, in_class=True, static=False, property_method=False)
        rendered = self._js_params(params)
        self.push_scope()
        for record in params:
            self.declare(record["name"], record["kind"])
            self.param_types[record["name"]] = record["kind"] or "object"
        self.param_types.pop("self", None)
        self.declare("self")
        docstring, body = extract_docstring(list(node.body))
        try:
            lines = [f"{pad}constructor({rendered}) {{"]
            inner = self.statements(body, level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()

    def _cpp_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = self._bases(node)
        header = f"{pad}class {node.name}" + (f" : public {bases[0]}" if bases else "") + " {"
        lines = [header, f"{self.indent(level + 1)}public:"]
        if docstring:
            lines.append(self._comment_lines(level + 1, docstring))
        for name, kind, default in self.class_fields.get(node.name, []):
            type_text = self._field_type(node.name, name, kind, "std::any")
            text = f"{self.indent(level + 1)}{type_text} {name}"
            if default is not None:
                if type_text.startswith("std::optional") and self._is_none_constant(default):
                    text += " = std::nullopt"
                else:
                    text += f" = {self.expr(default)}"
            lines.append(text + ";")
        constructor = self._constructor(node)
        if constructor is not None:
            lines.append(self._cpp_constructor(node, constructor, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}};")
        return "\n".join(lines)

    def _cpp_constructor(self, cls: ast.ClassDef, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        params = self._parameter_records(node, in_class=True, static=False, property_method=False)
        template, rendered = self._cpp_params(params)
        self.push_scope()
        for record in params:
            self.declare(record["name"], record["kind"])
            self.param_types[record["name"]] = record["kind"] or "object"
        self.param_types.pop("self", None)
        self.declare("self")
        try:
            lines = [f"{template}{pad}{cls.name}({rendered}) {{"]
            inner = self.statements(self._constructor_body(node), level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()

    def _constructor_body(self, node: ast.FunctionDef) -> list[ast.stmt]:
        _, body = extract_docstring(list(node.body))
        return [
            statement for statement in body
            if not self._is_super_call(statement)
        ]

    def _is_super_call(self, statement: ast.stmt) -> bool:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return False
        return self._call_name(statement.value.func) == "super"

    def _csharp_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = self._bases(node)
        header = f"{pad}public class {node.name}" + (f" : {bases[0]}" if bases else "") + f"\n{pad}{{"
        lines = [header]
        if docstring:
            lines.append(self._comment_lines(level + 1, docstring))
        for name, kind, default in self.class_fields.get(node.name, []):
            type_text = self._field_type(node.name, name, kind, "object")
            text = f"{self.indent(level + 1)}public {type_text} {name}"
            if default is not None:
                text += f" = {self.expr(default)}"
            lines.append(text + ";")
        constructor = self._constructor(node)
        if constructor is not None:
            lines.append(self._csharp_constructor(node, constructor, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            if (node.name, method.name) in self.property_methods:
                lines.append(self._csharp_property(method, level + 1))
            else:
                lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _csharp_constructor(self, cls: ast.ClassDef, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        params = self._parameter_records(node, in_class=True, static=False, property_method=False)
        rendered = self._csharp_params(params)
        self.push_scope()
        for record in params:
            self.declare(record["name"], record["kind"])
            self.param_types[record["name"]] = record["kind"] or "object"
        self.param_types.pop("self", None)
        self.declare("self")
        try:
            lines = [f"{pad}public {cls.name}({rendered})\n{pad}{{"]
            inner = self.statements(self._constructor_body(node), level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()

    def _csharp_property(self, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        name = self.decl_method_names[node.name] if False else None
        name = self.decl_method_names.get(self.current_class or "", {}).get(node.name, node.name)
        return_type = self._return_type(node, in_class=True) or "object"
        property_name = name[:1].upper() + name[1:]
        _docstring, body = extract_docstring(list(node.body))
        self.push_scope()
        self.declare("self")
        try:
            lines = [f"{pad}public {return_type} {property_name}", f"{pad}{{"]
            lines.append(f"{self.indent(level + 1)}get")
            lines.append(f"{self.indent(level + 1)}{{")
            inner = self.statements(body, level + 2)
            if inner:
                lines.append(inner)
            lines.append(f"{self.indent(level + 1)}}}")
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()

    def _java_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = self._bases(node)
        header = f"{pad}class {node.name}" + (f" extends {bases[0]}" if bases else "") + " {"
        lines = [header]
        if docstring:
            lines.append(self._comment_lines(level + 1, docstring))
        for name, kind, default in self.class_fields.get(node.name, []):
            type_text = self._field_type(node.name, name, kind, "Object")
            text = f"{self.indent(level + 1)}public {type_text} {name}"
            if default is not None:
                text += f" = {self.expr(default)}"
            lines.append(text + ";")
        constructor = self._constructor(node)
        if constructor is not None:
            lines.append(self._java_constructor(node, constructor, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _java_constructor(self, cls: ast.ClassDef, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        params = self._parameter_records(node, in_class=True, static=False, property_method=False)
        rendered = self._java_params(params)
        self.push_scope()
        for record in params:
            self.declare(record["name"], record["kind"])
            self.param_types[record["name"]] = record["kind"] or "object"
        self.param_types.pop("self", None)
        self.declare("self")
        try:
            lines = [f"{pad}public {cls.name}({rendered}) {{"]
            inner = self.statements(self._constructor_body(node), level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
            trailing = 0
            for record in reversed(params):
                if record["default"] is not None and record["param_kind"] not in {"vararg", "kwarg"}:
                    trailing += 1
                else:
                    break
            for keep in range(len(params) - 1, len(params) - trailing - 1, -1):
                subset = params[:keep]
                forwarded = [record["name"] for record in subset]
                forwarded.extend(self.expr(record["default"]) for record in params[keep:])
                lines.append(f"{pad}public {cls.name}({self._java_params(subset)}) {{")
                lines.append(f"{self.indent(level + 1)}this({', '.join(forwarded)});")
                lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()

    def _rust_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        if docstring:
            lines.append(self._comment_lines(level, docstring))
        lines.append(f"{pad}#[derive(Default, Clone)]")
        lines.append(f"{pad}struct {node.name} {{")
        for name, kind, _default in self.class_fields.get(node.name, []):
            type_text = self._field_type(node.name, name, kind, "i64")
            lines.append(f"{self.indent(level + 1)}pub {name}: {type_text},")
        lines.append(f"{pad}}}")
        lines.append("")
        lines.append(f"{pad}impl {node.name} {{")
        lines.append(self._rust_constructor(node, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}}")
        return "\n".join(line for line in lines if line is not None)

    def _rust_constructor(self, node: ast.ClassDef, level: int) -> str:
        pad = self.indent(level)
        constructor = self._constructor(node)
        params: list[dict] = []
        if constructor is not None:
            params = self._parameter_records(constructor, in_class=True, static=False, property_method=False)
        rendered = self._rust_params(params)
        assigned: dict[str, ast.expr] = {}
        remaining: list[ast.stmt] = []
        if constructor is not None:
            _, body = extract_docstring(list(constructor.body))
            for statement in body:
                extracted = self._extract_field_initialisers(statement)
                if extracted is not None:
                    assigned.update(extracted)
                elif not self._is_super_call(statement):
                    remaining.append(statement)
        for name, _kind, default in self.class_fields.get(node.name, []):
            if name not in assigned and default is not None:
                assigned[name] = default
        for record in params:
            self.param_types[record["name"]] = record["kind"] or "object"
        self.declare("self")
        lines = [f"{pad}pub fn new({rendered}) -> Self {{"]
        initialisers = [
            f"{name}: {self.expr(value)}"
            for name, value in assigned.items()
        ]
        literal = "Self { " + ", ".join(initialisers) + ", ..Default::default() }" if initialisers else "Self::default()"
        if not remaining:
            lines.append(f"{self.indent(level + 1)}{literal}")
        else:
            lines.append(f"{self.indent(level + 1)}let mut __self = {literal};")
            old_alias = self.self_alias
            self.self_alias = "__self"
            self.push_scope()
            try:
                inner = self.statements(remaining, level + 1)
                if inner:
                    lines.append(inner)
                lines.append(f"{self.indent(level + 1)}__self")
            finally:
                self.pop_scope()
                self.self_alias = old_alias
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _extract_field_initialisers(self, statement: ast.stmt) -> dict[str, ast.expr] | None:
        if not isinstance(statement, ast.Assign):
            return None
        targets = [target for target in statement.targets if isinstance(target, ast.Attribute)]
        if not targets:
            return None
        result: dict[str, ast.expr] = {}
        for target in targets:
            if isinstance(target.value, ast.Name) and target.value.id == "self":
                result[target.attr] = statement.value
        return result or None

    def _go_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        lines: list[str] = []
        if docstring:
            lines.append(self._comment_lines(level, docstring))
        lines.append(f"{pad}type {node.name} struct {{")
        for name, kind, _default in self.class_fields.get(node.name, []):
            type_text = self._field_type(node.name, name, kind, "any")
            lines.append(f"{self.indent(level + 1)}{self.go_export(name)} {type_text}")
        lines.append(f"{pad}}}")
        lines.append("")
        lines.append(self._go_constructor(node, level))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level, in_class=True))
        return "\n".join(lines)

    def _go_constructor(self, node: ast.ClassDef, level: int) -> str:
        pad = self.indent(level)
        old_go_defaults = self.go_default_exprs
        self.go_default_exprs = {}
        constructor = self._constructor(node)
        params: list[dict] = []
        if constructor is not None:
            params = self._parameter_records(constructor, in_class=True, static=False, property_method=False)
        rendered = self._go_params(params)
        assigned: dict[str, ast.expr] = {}
        remaining: list[ast.stmt] = []
        if constructor is not None:
            _, body = extract_docstring(list(constructor.body))
            for statement in body:
                extracted = self._extract_field_initialisers(statement)
                if extracted is not None:
                    assigned.update(extracted)
                elif not self._is_super_call(statement):
                    remaining.append(statement)
        for name, _kind, default in self.class_fields.get(node.name, []):
            if name not in assigned and default is not None:
                assigned[name] = default
        for record in params:
            self.param_types[record["name"]] = record["kind"] or "object"
        self.declare("self")
        lines = [f"{pad}func New{node.name}({rendered}) *{node.name} {{"]
        field_names = self.decl_field_names.get(node.name, {})
        params_by_name = {record["name"]: record for record in params}
        field_kinds = {
            field_name: field_kind
            for field_name, field_kind, _default in self.class_fields.get(node.name, [])
        }
        initialisers: list[str] = []
        for name, value in assigned.items():
            record = params_by_name.get(name)
            rendered_value = self.expr(value)
            if (
                record is not None
                and record["default"] is not None
                and isinstance(value, ast.Name)
                and value.id == record["name"]
            ):
                field_type = self._field_type(node.name, name, field_kinds.get(name), "any")
                # Defaulted parameters are variadic in Go and the pointer is
                # stripped (``...string``), so take the address of the element.
                if field_type.startswith("*"):
                    item = f"&{name}[0]"
                    rendered_value = (
                        f"func() {field_type} {{ if len({name}) > 0 {{ return {item} }}; return nil }}()"
                    )
                else:
                    fallback = self._go_zero_value(field_type) if self._is_none_constant(record["default"]) else self.expr(record["default"])
                    rendered_value = (
                        f"func() {field_type} {{ if len({name}) > 0 {{ return {name}[0] }}; return {fallback} }}()"
                    )
            initialisers.append(f"{field_names.get(name, self.go_export(name))}: {rendered_value}")
        literal = f"&{node.name}{{{', '.join(initialisers)}}}"
        if not remaining:
            lines.append(f"{self.indent(level + 1)}return {literal}")
        else:
            lines.append(f"{self.indent(level + 1)}__self := {literal}")
            old_alias = self.self_alias
            self.self_alias = "__self"
            self.push_scope()
            try:
                inner = self.statements(remaining, level + 1)
                if inner:
                    lines.append(inner)
                lines.append(f"{self.indent(level + 1)}return __self")
            finally:
                self.pop_scope()
                self.self_alias = old_alias
        lines.append(f"{pad}}}")
        self.go_default_exprs = old_go_defaults
        return "\n".join(lines)

    def _php_class(self, node: ast.ClassDef, level: int, docstring: str | None) -> str:
        pad = self.indent(level)
        bases = self._bases(node)
        header = f"{pad}class {node.name}" + (f" extends {bases[0]}" if bases else "") + " {"
        lines = [header]
        if docstring:
            lines.append(self._comment_lines(level + 1, docstring))
        for name, kind, default in self.class_fields.get(node.name, []):
            type_text = ""
            mapped = self.py_type_to_target(kind) if kind else None
            if mapped in {"int", "float", "string", "bool", "array"}:
                type_text = mapped + " "
            text = f"{self.indent(level + 1)}public {type_text}${name}"
            if default is not None:
                text += f" = {self.expr(default)}"
            lines.append(text + ";")
        constructor = self._constructor(node)
        if constructor is not None:
            lines.append(self._php_constructor(node, constructor, level + 1))
        for method in self._class_methods(node):
            if method.name == "__init__":
                continue
            lines.append(self.emit_function(method, level + 1, in_class=True))
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    def _php_constructor(self, cls: ast.ClassDef, node: ast.FunctionDef, level: int) -> str:
        pad = self.indent(level)
        params = self._parameter_records(node, in_class=True, static=False, property_method=False)
        rendered = self._php_params(params)
        self.push_scope()
        for record in params:
            self.declare(record["name"], record["kind"])
            self.param_types[record["name"]] = record["kind"] or "object"
        self.param_types.pop("self", None)
        self.declare("self")
        try:
            lines = [f"{pad}public function __construct({rendered})", f"{pad}{{"]
            inner = self.statements(self._constructor_body(node), level + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        finally:
            self.pop_scope()
