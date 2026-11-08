"""Expression rendering for the deterministic Python -> polyglot emitter."""

from __future__ import annotations

import ast

from services.transpiler.emitter_base import EmitterBase


class ExpressionMixin(EmitterBase):
    """Render Python expressions as idiomatic target-language expressions."""

    # -- public entrypoint ------------------------------------------
    def expr(self, node: ast.AST | None, *, paren: bool = False) -> str:
        if node is None:
            return self.none()
        handler = getattr(self, f"_expr_{type(node).__name__}", None)
        if handler is None:
            self.warn(
                f"Expression {type(node).__name__} has no direct {self.target} mapping; "
                "a neutral value was emitted."
            )
            return self.none()
        rendered = handler(node)
        if paren and isinstance(
            node, (ast.BinOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Lambda)
        ):
            return f"({rendered})"
        return rendered

    # -- atoms -------------------------------------------------------
    def _expr_Constant(self, node: ast.Constant) -> str:
        value = node.value
        if value is None:
            return self.none()
        if isinstance(value, bool):
            return self.bool_literal(value)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value != value:  # NaN
                return "0.0"
            if value == float("inf"):
                return "float('inf')" if self.target == "python" else (
                    "std::numeric_limits<double>::infinity()"
                    if self.target == "cpp" else "math.Inf(1)"
                    if self.target == "go" else "Double.POSITIVE_INFINITY"
                    if self.target == "java" else "double.POSITIVE_INFINITY"
                    if self.target == "csharp" else "f64::INFINITY"
                    if self.target == "rust" else "INF"
                )
            return repr(value)
        if isinstance(value, str):
            if self.target == "rust":
                return f"String::from({self.string(value)})"
            return self.string(value)
        if isinstance(value, bytes):
            return f"bytes.fromhex('{value.hex()}')" if self.target == "python" else (
                f'"{value.hex()}"'
            )
        if value is Ellipsis:
            return self.none()
        return self.none()

    def _expr_Name(self, node: ast.Name) -> str:
        name = node.id
        if name == "True":
            return self.bool_literal(True)
        if name == "False":
            return self.bool_literal(False)
        if name == "None":
            return self.none()
        if name in {"self", "cls"}:
            if self.target in {"rust", "go"} and self.self_alias:
                return self.self_alias
            if self.target == "php":
                return "$this"
            if self.target == "rust":
                return "self"
            if self.target in {"javascript", "typescript", "java", "csharp", "cpp"}:
                return "this"
            return name
        if self.target == "php":
            # Function and class names are not variables in PHP.
            if name in self.function_names or name in self.function_target_names or name in self.class_names:
                return name
            if name in self.module_constant_names:
                return name
            return f"${name}"
        if name in self.module_constant_names and self.current_class and self.entry_class_name and self.target in {"java", "csharp"}:
            return f"{self.entry_class_name}.{name}"
        if self.target == "go" and name in self.go_default_exprs:
            return self.go_default_exprs[name]
        mapped = self.function_target_names.get(name)
        if mapped:
            return mapped
        return name

    def _expr_Attribute(self, node: ast.Attribute) -> str:
        root = self._root_name(node)
        attr = node.attr

        if root == "math":
            return self._math_constant(attr)
        if root in {"sys", "os"} and attr in {"argv", "environ"}:
            return self._os_constant(root, attr)
        if root == "json" and attr == "dumps":
            return "_json_dumps"

        value = self.expr(node.value)
        owner = self.current_class
        is_self = value in {"self", "this", "$this", "__self"}
        if owner and is_self:
            method = self.decl_method_names.get(owner, {}).get(attr)
            field = self.decl_field_names.get(owner, {}).get(attr)
            if field:
                if self.target == "go":
                    return f"{value}.{field}"
                if self.target == "php":
                    return f"{value}->{field}"
                if self.target == "cpp" and value == "this":
                    return f"this->{field}"
                return f"{value}.{field}"
            if method:
                if self.target == "php":
                    rendered = f"{value}->{method}"
                elif self.target == "cpp" and value == "this":
                    rendered = f"this->{method}"
                else:
                    rendered = f"{value}.{method}"
                if (owner, attr) in self.property_methods and self.target in {"java", "cpp", "rust", "go", "php"}:
                    rendered += "()"
                return rendered
        if self.target == "php":
            return f"{value}->{attr}"
        if self.target == "cpp" and value == "this":
            return f"this->{attr}"
        if self.target == "rust" and attr in {"items", "keys", "values"}:
            return f"{value}.{attr}()"
        if self.target == "go":
            owner = self.current_class if value in {"self", "this"} else None
            if value in {"self", "this"} and owner:
                return f"{value}.{self.go_field(owner, attr)}"
            return f"{value}.{self.go_export(attr)}"
        if self.target in {"javascript", "typescript", "java", "csharp"}:
            mapped = self.method_target_names.get(attr)
            if mapped:
                return f"{value}.{mapped}"
        if self.target in {"java", "csharp"} and self.current_class and value in {"this", "self"}:
            return f"{value}.{attr}"
        return f"{value}.{attr}"

    def _math_constant(self, attr: str) -> str:
        mapping = {
            "pi": {"javascript": "Math.PI", "typescript": "Math.PI", "java": "Math.PI",
                   "csharp": "Math.PI", "cpp": "M_PI", "rust": "std::f64::consts::PI",
                   "go": "math.Pi", "php": "M_PI"},
            "e": {"javascript": "Math.E", "typescript": "Math.E", "java": "Math.E",
                  "csharp": "Math.E", "cpp": "M_E", "rust": "std::f64::consts::E",
                  "go": "math.E", "php": "M_E"},
            "inf": {"javascript": "Infinity", "typescript": "Infinity", "java": "Double.POSITIVE_INFINITY",
                    "csharp": "double.PositiveInfinity", "cpp": "std::numeric_limits<double>::infinity()",
                    "rust": "f64::INFINITY", "go": "math.Inf(1)", "php": "INF"},
        }
        return mapping.get(attr, {}).get(self.target, attr)

    def _os_constant(self, root: str, attr: str) -> str:
        if root == "sys" and attr == "argv":
            return {
                "javascript": "process.argv", "typescript": "process.argv",
                "java": "new String[0]", "csharp": "Environment.GetCommandLineArgs()",
                "cpp": "std::vector<std::string>{}", "rust": "std::env::args().collect::<Vec<_>>()",
                "go": "os.Args", "php": "$argv ?? []",
            }.get(self.target, "[]")
        if root == "os" and attr == "environ":
            return {
                "javascript": "process.env", "typescript": "process.env",
                "java": "System.getenv()", "csharp": "Environment.GetEnvironmentVariables()",
                "cpp": "std::map<std::string, std::string>{}",
                "rust": "std::env::vars().collect::<std::collections::HashMap<_, _>>()",
                "go": "func() map[string]string { m := map[string]string{}; for _, e := range os.Environ() { if i := strings.IndexByte(e, '='); i >= 0 { m[e[:i]] = e[i+1:] } }; return m }()",
                "php": "$_ENV",
            }.get(self.target, "{}")
        return attr

    # -- containers --------------------------------------------------
    def _expr_List(self, node: ast.List) -> str:
        return self._list_literal(node.elts)

    def _expr_Tuple(self, node: ast.Tuple) -> str:
        return self._tuple_literal(node.elts)

    def _expr_Set(self, node: ast.Set) -> str:
        items = ", ".join(self.expr(item) for item in node.elts)
        if self.target == "typescript":
            return f"new Set<any>([{items}])"
        if self.target == "javascript":
            return f"new Set([{items}])"
        if self.target == "java":
            return f"new HashSet<>(Arrays.asList({items}))" if items else "new HashSet<>()"
        if self.target == "csharp":
            return f"new HashSet<object> {{ {items} }}" if items else "new HashSet<object>()"
        if self.target == "cpp":
            return f"std::set<std::any>{{{items}}}" if items else "std::set<std::any>{}"
        if self.target == "rust":
            return f"[{items}].into_iter().collect::<std::collections::HashSet<_>>()"
        if self.target == "go":
            entries = ", ".join(f"{self.expr(item)}: {{}}" for item in node.elts)
            return f"map[any]struct{{}}{{{entries}}}"
        if self.target == "php":
            return f"[{items}]"
        return f"{{{items}}}"

    def _expr_Dict(self, node: ast.Dict) -> str:
        pairs: list[tuple[str, str]] = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                continue
            pairs.append((self.expr(key), self.expr(value)))
        if self.target in {"javascript", "typescript"}:
            body = ", ".join(f"[{k}]: {v}" for k, v in pairs)
            return "{ " + body + " }" if body else "{}"
        if self.target == "php":
            return "[" + ", ".join(f"{k} => {v}" for k, v in pairs) + "]"
        if self.target == "java":
            if not pairs:
                return "new HashMap<>()"
            entries = ", ".join(f"Map.entry({k}, {v})" for k, v in pairs)
            if len(pairs) <= 10:
                return f"Map.ofEntries({entries})"
            return f"Map.ofEntries({entries})"
        if self.target == "csharp":
            body = ", ".join(f"[{k}] = {v}" for k, v in pairs)
            return f"new Dictionary<string, object> {{ {body} }}" if pairs else "new Dictionary<string, object>()"
        if self.target == "cpp":
            value_type = self._homogeneous_type([value for _, value in zip(node.keys, node.values) if value is not None])
            body = ", ".join(f"{{{k}, {v}}}" for k, v in pairs)
            return f"std::map<std::string, {value_type}>{{{body}}}" if pairs else f"std::map<std::string, {value_type}>{{}}"
        if self.target == "rust":
            entries = ", ".join(f"({k}.to_string(), {v})" for k, v in pairs)
            return f"std::collections::HashMap::from([{entries}])"
        if self.target == "go":
            return "map[string]any{" + ", ".join(f"{k}: {v}" for k, v in pairs) + "}"
        return "{" + ", ".join(f"{k}: {v}" for k, v in pairs) + "}"

    def _list_literal(self, elts: list[ast.expr]) -> str:
        items = ", ".join(self.expr(item) for item in elts)
        if self.target in {"javascript", "typescript"}:
            return f"[{items}]"
        if self.target == "php":
            return f"[{items}]"
        if self.target == "java":
            return f"new ArrayList<>(Arrays.asList({items}))" if items else "new ArrayList<>()"
        if self.target == "csharp":
            element = self._homogeneous_type(elts)
            return f"new List<{element}> {{ {items} }}" if items else f"new List<{element}>()"
        if self.target == "cpp":
            element = self._homogeneous_type(elts)
            return f"std::vector<{element}>{{{items}}}" if items else f"std::vector<{element}>{{}}"
        if self.target == "rust":
            return f"vec![{items}]" if items else "Vec::<i64>::new()"
        if self.target == "go":
            element = self._homogeneous_type(elts)
            return f"[]{element}{{{items}}}" if items else f"[]{element}{{}}"
        return f"[{items}]"

    def _tuple_literal(self, elts: list[ast.expr]) -> str:
        items = ", ".join(self.expr(item) for item in elts)
        if self.target == "typescript":
            return f"[{items}]"
        if self.target == "javascript":
            return f"[{items}]"
        if self.target == "java":
            return f"new Object[] {{ {items} }}" if items else "new Object[] {}"
        if self.target == "csharp":
            return f"new object[] {{ {items} }}" if items else "Array.Empty<object>()"
        if self.target == "cpp":
            return f"std::make_tuple({items})"
        if self.target == "rust":
            if len(elts) == 1:
                return f"({items},)"
            return f"({items})"
        if self.target == "go":
            return f"[]any{{{items}}}"
        if self.target == "php":
            return f"[{items}]"
        return f"[{items}]"

    def _homogeneous_type(self, elts: list[ast.expr]) -> str:
        kinds: set[str] = set()
        for elt in elts:
            if isinstance(elt, ast.Constant):
                if isinstance(elt.value, bool):
                    kinds.add("bool")
                elif isinstance(elt.value, int):
                    kinds.add("int")
                elif isinstance(elt.value, float):
                    kinds.add("float")
                elif isinstance(elt.value, str):
                    kinds.add("str")
                elif elt.value is None:
                    kinds.add("none")
            elif isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name) and elt.func.id in self.class_names:
                kinds.add(elt.func.id)
            elif isinstance(elt, ast.Dict):
                kinds.add("dict")
            elif isinstance(elt, (ast.List, ast.Tuple, ast.Set)):
                kinds.add("list")
        if not kinds:
            return self.primitive("object")
        if len(kinds) == 1:
            only = next(iter(kinds))
            if only not in {"bool", "int", "float", "str", "none", "dict", "list"}:
                return only
        if kinds == {"bool"}:
            return self.primitive("bool")
        if kinds <= {"int", "float"}:
            return self.primitive("float" if "float" in kinds else "int")
        if kinds == {"str"}:
            return self.primitive("str")
        if kinds == {"dict"}:
            return self.primitive("dict")
        if kinds == {"list"}:
            return self.primitive("list")
        return self.primitive("object")

    # -- operators ---------------------------------------------------
    def _expr_BinOp(self, node: ast.BinOp) -> str:
        left = self.expr(node.left)
        right = self.expr(node.right)
        if isinstance(node.left, (ast.BinOp, ast.BoolOp, ast.IfExp, ast.Compare)):
            left = f"({left})"
        if isinstance(node.right, (ast.BinOp, ast.BoolOp, ast.IfExp, ast.Compare)):
            right = f"({right})"

        if isinstance(node.op, ast.Pow):
            return self._pow(left, right)
        if isinstance(node.op, ast.Div):
            return self._true_division(left, right)
        if isinstance(node.op, ast.FloorDiv):
            return self._floor_division(left, right)
        if isinstance(node.op, ast.Mod) and self.target in {"javascript", "typescript"}:
            return f"{left} % {right}"
        if isinstance(node.op, ast.Add) and self.target == "rust":
            left_is_text = isinstance(node.left, ast.JoinedStr) or (
                isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
            )
            right_is_text = isinstance(node.right, ast.JoinedStr) or (
                isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
            )
            if left_is_text or right_is_text:
                return f"format!(\"{{}}{{}}\", {left}, {right})"
        op_map = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Mod: "%",
            ast.BitXor: "^", ast.BitAnd: "&", ast.BitOr: "|",
            ast.LShift: "<<", ast.RShift: ">>",
        }
        op = op_map.get(type(node.op))
        if op is None:
            self.warn(f"Binary operator {type(node.op).__name__} was approximated.")
            op = "+"
        return f"{left} {op} {right}"

    def _pow(self, left: str, right: str) -> str:
        if self.target in {"javascript", "typescript"}:
            return f"Math.pow({left}, {right})"
        if self.target == "java":
            return f"Math.pow({left}, {right})"
        if self.target == "csharp":
            return f"Math.Pow({left}, {right})"
        if self.target == "cpp":
            return f"std::pow({left}, {right})"
        if self.target == "rust":
            return f"({left} as f64).powf({right} as f64)"
        if self.target == "go":
            self.go_imports.add("math")
            return f"math.Pow({left}, {right})"
        if self.target == "php":
            return f"pow({left}, {right})"
        return f"({left} ** {right})"

    def _true_division(self, left: str, right: str) -> str:
        if self.target == "cpp":
            return f"static_cast<double>({left}) / ({right})"
        if self.target == "java":
            return f"(double) ({left}) / ({right})"
        if self.target == "csharp":
            return f"(double) ({left}) / ({right})"
        if self.target == "rust":
            return f"({left} as f64) / ({right} as f64)"
        if self.target == "go":
            return f"float64({left}) / float64({right})"
        return f"{left} / {right}"

    def _floor_division(self, left: str, right: str) -> str:
        if self.target in {"javascript", "typescript"}:
            return f"Math.floor({left} / {right})"
        if self.target == "java":
            return f"Math.floorDiv({left}, {right})"
        if self.target == "csharp":
            return f"Math.Floor((double) ({left}) / ({right}))"
        if self.target == "cpp":
            return f"static_cast<long>(std::floor(static_cast<double>({left}) / ({right})))"
        if self.target == "rust":
            return f"({left} as f64 / {right} as f64).floor() as i64"
        if self.target == "go":
            return f"{left} / {right}"
        if self.target == "php":
            return f"intdiv({left}, {right})"
        return f"{left} // {right}"

    def _expr_UnaryOp(self, node: ast.UnaryOp) -> str:
        operand = self.expr(node.operand, paren=True)
        if isinstance(node.op, ast.Not):
            return f"!{operand}"
        if isinstance(node.op, ast.USub):
            return f"-{operand}"
        if isinstance(node.op, ast.UAdd):
            return f"+{operand}"
        if isinstance(node.op, ast.Invert):
            return f"~{operand}"
        return operand

    def _expr_BoolOp(self, node: ast.BoolOp) -> str:
        if self.target in {"javascript", "typescript", "java", "csharp", "cpp", "rust", "go", "php"}:
            op = " && " if isinstance(node.op, ast.And) else " || "
        else:
            op = " and " if isinstance(node.op, ast.And) else " or "
        parts = []
        for value in node.values:
            rendered = self.expr(value)
            if isinstance(value, (ast.BoolOp, ast.Compare, ast.BinOp)):
                rendered = f"({rendered})"
            parts.append(rendered)
        return op.join(parts)

    def _expr_Compare(self, node: ast.Compare) -> str:
        left = self.expr(node.left)
        if isinstance(node.left, (ast.BinOp, ast.BoolOp)):
            left = f"({left})"
        parts: list[str] = []
        previous = left

        for op, comparator in zip(node.ops, node.comparators):
            right = self.expr(comparator)
            if isinstance(comparator, (ast.BinOp, ast.BoolOp)):
                right = f"({right})"
            if isinstance(op, (ast.Is, ast.IsNot)):
                if self._is_none_constant(comparator):
                    parts.append(self._none_check(previous, negative=isinstance(op, ast.IsNot)))
                else:
                    parts.append(f"{previous} {'!=' if isinstance(op, ast.IsNot) else '=='} {right}")
            elif isinstance(op, (ast.In, ast.NotIn)):
                parts.append(self._membership(previous, right, negative=isinstance(op, ast.NotIn)))
            else:
                operator = {
                    ast.Eq: "===" if self.target in {"javascript", "typescript"} else "==",
                    ast.NotEq: "!==" if self.target in {"javascript", "typescript"} else "!=",
                    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
                }.get(type(op), "==")
                parts.append(f"{previous} {operator} {right}")
            previous = right

        if len(parts) == 1:
            return parts[0]
        # Python chained comparisons: a < b < c means a < b && b < c.
        chain = []
        cursor = left
        for op, comparator in zip(node.ops, node.comparators):
            right = self.expr(comparator)
            operator = {
                ast.Eq: "===" if self.target in {"javascript", "typescript"} else "==",
                ast.NotEq: "!==" if self.target in {"javascript", "typescript"} else "!=",
                ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
                ast.Is: "==", ast.IsNot: "!=",
            }.get(type(op), "==")
            chain.append(f"{cursor} {operator} {right}")
            cursor = right
        return " && ".join(chain)

    def _none_check(self, value: str, *, negative: bool) -> str:
        if self.target == "go":
            return f"{value} {'!=' if negative else '=='} nil"
        if self.target == "rust":
            return f"{value}.{'is_some()' if negative else 'is_none()'}"
        if self.target == "php":
            return f"{value} {'!==' if negative else '==='} null"
        return f"{value} {'!=' if negative else '=='} null"

    def _membership(self, item: str, container: str, *, negative: bool) -> str:
        if self.target in {"javascript", "typescript"}:
            expr = f"{container}.includes({item})"
        elif self.target == "java":
            expr = f"{container}.contains({item})"
        elif self.target == "csharp":
            expr = f"{container}.Contains({item})"
        elif self.target == "cpp":
            expr = f"std::find({container}.begin(), {container}.end(), {item}) != {container}.end()"
        elif self.target == "rust":
            expr = f"{container}.contains(&{item})"
        elif self.target == "go":
            self.go_imports.add("slices")
            expr = f"slices.Contains({container}, {item})"
        elif self.target == "php":
            expr = f"in_array({item}, {container}, true)"
        else:
            expr = f"{item} in {container}"
        return f"!({expr})" if negative else expr

    def _expr_IfExp(self, node: ast.IfExp) -> str:
        test = self.expr(node.test)
        body = self.expr(node.body)
        orelse = self.expr(node.orelse)
        if self.target == "python":
            return f"({body} if {test} else {orelse})"
        if self.target == "rust":
            return f"(if {test} {{ {body} }} else {{ {orelse} }})"
        if self.target == "go":
            kind = self.py_type_of(node)
            result_type = self.py_type_to_target(kind) if kind else "any"
            return f"func() {result_type} {{ if {test} {{ return {body} }}; return {orelse} }}()"
        return f"({test} ? {body} : {orelse})"

    # -- indexing / slicing -----------------------------------------
    def _expr_Subscript(self, node: ast.Subscript) -> str:
        value = self.expr(node.value)
        if isinstance(node.slice, ast.Slice):
            return self._slice(value, node.slice)
        index = self.expr(node.slice)
        if self.target in {"javascript", "typescript"} and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, int) and node.slice.value < 0:
                return f"{value}.at({index})"
        return f"{value}[{index}]"

    def _slice(self, value: str, node: ast.Slice) -> str:
        lower = self.expr(node.lower) if node.lower else ""
        upper = self.expr(node.upper) if node.upper else ""
        if self.target in {"javascript", "typescript"}:
            if lower and upper:
                return f"{value}.slice({lower}, {upper})"
            if lower:
                return f"{value}.slice({lower})"
            return f"{value}.slice(0, {upper})" if upper else f"{value}.slice()"
        if self.target == "java":
            return f"{value}.substring({lower}, {upper})"
        if self.target == "csharp":
            return f"{value}.Substring({lower}, {upper})" if upper else f"{value}.Substring({lower})"
        if self.target == "rust":
            return f"{value}[{lower}..{upper}]"
        if self.target == "go":
            return f"{value}[{lower}:{upper}]"
        if self.target == "php":
            return f"array_slice({value}, {lower or '0'}, {upper} - {lower or '0'})"
        if self.target == "cpp":
            return f"{value}.substr({lower or '0'}, {upper})"
        return f"{value}[{lower}:{upper}]"

    # -- f-strings ---------------------------------------------------
    def _expr_JoinedStr(self, node: ast.JoinedStr) -> str:
        return self.fstring(node)

    def fstring_parts(self, node: ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(("text", str(value.value)))
            elif isinstance(value, ast.FormattedValue):
                spec = ""
                if value.format_spec is not None:
                    if isinstance(value.format_spec, ast.JoinedStr):
                        spec = "".join(
                            str(part.value)
                            for part in value.format_spec.values
                            if isinstance(part, ast.Constant)
                        )
                    elif isinstance(value.format_spec, ast.Constant):
                        spec = str(value.format_spec.value)
                parts.append(("expr", self.expr(value.value), spec, value.conversion))
        return parts

    def fstring(self, node: ast.JoinedStr) -> str:
        parts = self.fstring_parts(node)
        if self.target in {"javascript", "typescript"}:
            body = []
            for part in parts:
                if part[0] == "text":
                    body.append(part[1].replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
                else:
                    code, spec = part[1], part[2]
                    if spec.endswith("f") and "." in spec:
                        digits = spec.split(".")[1].rstrip("f") or "2"
                        body.append("${Number(" + code + ").toFixed(" + digits + ")}")
                    elif part[3] == ord("r"):
                        body.append("${JSON.stringify(" + code + ")}")
                    else:
                        body.append("${" + code + "}")
            return "`" + "".join(body) + "`"
        if self.target == "csharp":
            body = []
            for part in parts:
                if part[0] == "text":
                    body.append(part[1].replace("{", "{{").replace("}", "}}").replace('"', '\\"'))
                else:
                    code, spec = part[1], self._csharp_spec(part[2])
                    body.append("{" + code + (":" + spec if spec else "") + "}")
            return '$"' + "".join(body) + '"'
        if self.target == "java":
            fmt, args = self._format_template(parts, percent=True)
            return f'String.format("{fmt}", {", ".join(args)})' if args else f'"{fmt}"'
        if self.target == "go":
            self.go_imports.add("fmt")
            fmt, args = self._format_template(parts, percent=True)
            fmt = fmt.replace("%s", "%v")
            return f'fmt.Sprintf("{fmt}", {", ".join(args)})' if args else f'"{fmt}"'
        if self.target == "rust":
            fmt, args = self._format_template(parts, braces=True)
            return f'format!("{fmt}", {", ".join(args)})' if args else f'"{fmt}"'
        if self.target == "php":
            pieces = []
            for part in parts:
                if part[0] == "text":
                    pieces.append(self.string(part[1]))
                else:
                    code, spec = part[1], part[2]
                    if spec.endswith("f") and "." in spec:
                        digits = spec.split(".")[1].rstrip("f") or "2"
                        pieces.append(f'sprintf("%.{digits}f", {code})')
                    elif spec.endswith("d"):
                        pieces.append(f'sprintf("%d", {code})')
                    else:
                        pieces.append(code)
            return " . ".join(pieces) if pieces else '""'
        if self.target == "cpp":
            pieces = []
            for part in parts:
                if part[0] == "text":
                    pieces.append(self.string(part[1]))
                else:
                    code, spec = part[1], part[2]
                    if spec.endswith("f") and "." in spec:
                        digits = spec.split(".")[1].rstrip("f") or "2"
                        pieces.append(
                            "[&]() { std::ostringstream __os; __os << std::fixed "
                            f"<< std::setprecision({digits}) << {code}; return __os.str(); }}()"
                        )
                    else:
                        pieces.append(
                            "[&]() { std::ostringstream __os; __os << " + code + "; return __os.str(); }()"
                        )
            return " + ".join(pieces) if pieces else '""'
        # Python
        body = []
        for part in parts:
            if part[0] == "text":
                body.append(part[1].replace("{", "{{").replace("}", "}}"))
            else:
                body.append("{" + part[1] + (":" + part[2] if part[2] else "") + "}")
        return 'f"' + "".join(body) + '"'

    def _format_template(self, parts, *, percent: bool = False, braces: bool = False) -> tuple[str, list[str]]:
        fmt: list[str] = []
        args: list[str] = []
        for part in parts:
            if part[0] == "text":
                text = part[1]
                if percent:
                    text = text.replace("%", "%%")
                if braces:
                    text = text.replace("{", "{{").replace("}", "}}")
                fmt.append(text)
            else:
                code, spec = part[1], part[2]
                if percent:
                    if spec.endswith("f") and "." in spec:
                        digits = spec.split(".")[1].rstrip("f") or "2"
                        fmt.append(f"%.{digits}f")
                    elif spec.endswith("d"):
                        fmt.append("%d")
                    else:
                        fmt.append("%s")
                else:
                    if spec.endswith("f") and "." in spec:
                        digits = spec.split(".")[1].rstrip("f") or "2"
                        fmt.append(f"{{:.{digits}}}")
                    else:
                        fmt.append("{}")
                args.append(code)
        return "".join(fmt), args

    def _csharp_spec(self, spec: str) -> str:
        if spec.endswith("f") and "." in spec:
            digits = spec.split(".")[1].rstrip("f") or "2"
            return f"F{digits}"
        if spec.endswith("d"):
            return "D"
        return spec

    # -- lambdas / comprehensions -----------------------------------
    def _expr_Lambda(self, node: ast.Lambda) -> str:
        params = [self.expr(arg) for arg in node.args.args]
        body = self.expr(node.body)
        if self.target in {"javascript", "typescript"}:
            return f"({', '.join(params)}) => {body}"
        if self.target == "java":
            return f"({', '.join(params)}) -> {body}"
        if self.target == "csharp":
            return f"({', '.join(params)}) => {body}"
        if self.target == "cpp":
            return f"[&](auto {', auto '.join(params)}) {{ return {body}; }}"
        if self.target == "rust":
            return f"|{', '.join(params)}| {body}"
        if self.target == "go":
            return f"func({', '.join(params)} any) any {{ return {body} }}"
        if self.target == "php":
            return f"fn({', '.join('$' + p for p in params)}) => {body}"
        return f"lambda {', '.join(params)}: {body}"

    def _expr_ListComp(self, node: ast.ListComp) -> str:
        return self._comprehension(node, "list")

    def _expr_GeneratorExp(self, node: ast.GeneratorExp) -> str:
        return self._comprehension(node, "list")

    def _expr_SetComp(self, node: ast.SetComp) -> str:
        return self._comprehension(node, "set")

    def _expr_DictComp(self, node: ast.DictComp) -> str:
        return self._comprehension(node, "dict")

    def _comprehension(self, node: ast.AST, kind: str) -> str:
        generators = list(getattr(node, "generators", []))
        if len(generators) != 1:
            self.warn("Nested comprehensions were flattened to a best-effort expression.")
        if not generators:
            return self._list_literal([])
        generator = generators[0]
        target_node = generator.target
        if isinstance(target_node, (ast.Tuple, ast.List)):
            names = [self.expr(item) for item in target_node.elts]
            target = ", ".join(names)
        else:
            target = self.expr(target_node)

        iterable = self.expr(generator.iter)
        conditions = [self.expr(cond) for cond in generator.ifs]

        if kind == "dict":
            key = self.expr(node.key)
            value = self.expr(node.value)
            if self.target in {"javascript", "typescript"}:
                return f"Object.fromEntries({iterable}.map(({target}) => [{key}, {value}]))"
            if self.target == "java":
                return (
                    f"{iterable}.stream().collect(java.util.stream.Collectors.toMap("
                    f"{target} -> {key}, {target} -> {value}))"
                )
            if self.target == "csharp":
                return f"{iterable}.ToDictionary({target} => {key}, {target} => {value})"
            if self.target == "php":
                return f"array_combine(array_map(fn(${target}) => {key}, {iterable}), array_map(fn(${target}) => {value}, {iterable}))"
            return self._comprehension(node, "list")

        expression = self.expr(node.elt if kind in {"list", "set"} else node.elt)
        condition = " && ".join(conditions)

        if self.target in {"javascript", "typescript"}:
            builder = f"{iterable}"
            builder += ".filter((" + target + ") => " + condition + ")" if condition else ""
            builder += ".map((" + target + ") => " + expression + ")"
            return f"new Set({builder})" if kind == "set" else builder
        if self.target == "java":
            stream = f"{iterable}.stream()"
            if condition:
                stream += f".filter({target} -> {condition})"
            stream += f".map({target} -> {expression})"
            return stream + (".collect(Collectors.toSet())" if kind == "set" else ".collect(Collectors.toList())")
        if self.target == "csharp":
            query = iterable
            if condition:
                query += f".Where({target} => {condition})"
            query += f".Select({target} => {expression})"
            return query + (".ToHashSet()" if kind == "set" else ".ToList()")
        if self.target == "cpp":
            body = []
            if condition:
                body.append(f"if ({condition})")
            body.append(f"__out.push_back({expression});")
            return (
                f"[&]() {{ std::vector<std::decay_t<decltype({expression})>> __out; "
                f"for (const auto& {target} : {iterable}) {{ {' '.join(body)} }} "
                f"return __out; }}()"
            )
        if self.target == "rust":
            iterator = f"{iterable}.iter()"
            if condition:
                iterator += f".filter(|{target}| {condition})"
            iterator += f".map(|{target}| {expression})"
            return iterator + (".collect::<std::collections::HashSet<_>>()" if kind == "set" else ".collect::<Vec<_>>()")
        if self.target == "go":
            body = [f"__out := make([]any, 0)"]
            if condition:
                body.append(f"if {condition} {{")
            body.append(f"__out = append(__out, {expression})")
            if condition:
                body.append("}")
            body.append("return __out")
            return f"func() []any {{ {' '.join(body)} }}()"
        if self.target == "php":
            mapper = f"array_map(fn(${target}) => {expression}, {iterable})"
            if condition:
                filtered = (
                    f"array_filter({iterable}, fn(${target}) => {condition})"
                )
                return f"array_values(array_map(fn(${target}) => {expression}, {filtered}))"
            return f"array_values({mapper})" if kind == "set" else mapper
        return expression

    # -- misc --------------------------------------------------------
    def _expr_Await(self, node: ast.Await) -> str:
        value = self.expr(node.value)
        if self.target == "rust":
            return f"{value}.await"
        if self.target in {"javascript", "typescript", "csharp"}:
            return f"await {value}"
        return value

    def _expr_Starred(self, node: ast.Starred) -> str:
        return self.expr(node.value)

    def _expr_NamedExpr(self, node: ast.NamedExpr) -> str:
        target = self.expr(node.target)
        value = self.expr(node.value)
        if self.target in {"javascript", "typescript", "php"}:
            return f"({target} = {value})"
        self.warn("Assignment expressions are not native to the target; rendered as a value.")
        return value
