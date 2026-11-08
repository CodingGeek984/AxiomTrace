"""Call and method translation for the deterministic polyglot emitter."""

from __future__ import annotations

import ast

from services.transpiler.emitter_base import EmitterBase


class CallMixin(EmitterBase):
    """Translate Python builtins, methods and standard-library calls."""

    def _expr_Call(self, node: ast.Call) -> str:
        name = self._call_name(node.func)
        args = [self.expr(arg) for arg in node.args if not isinstance(arg, ast.Starred)]
        keywords = {
            keyword.arg: self.expr(keyword.value)
            for keyword in node.keywords
            if keyword.arg
        }

        if isinstance(node.func, ast.Attribute):
            receiver_node = node.func.value
            module = self._root_name(receiver_node)
            if module in {"math", "random", "time", "os", "json", "sys"} and isinstance(receiver_node, ast.Name):
                return self._module_call(module, node.func.attr, node, args, keywords)
            if name == "format" and isinstance(receiver_node, ast.Constant):
                return self._format_call(receiver_node.value, args, keywords)
            return self._method_call(receiver_node, node.func.attr, node, args, keywords)

        if name in {
            "print", "len", "str", "repr", "int", "float", "bool", "abs",
            "min", "max", "sum", "round", "sorted", "reversed", "range",
            "enumerate", "zip", "list", "dict", "set", "tuple", "isinstance",
            "type", "ord", "chr", "input", "open", "format", "iter", "next",
        }:
            return self._builtin_call(name, node, args, keywords)

        rendered_args = self._render_args(args, keywords)
        return f"{self.expr(node.func)}({rendered_args})"

    def _render_args(self, args: list[str], keywords: dict[str, str]) -> str:
        parts = list(args)
        if keywords:
            self.warn("Python keyword arguments were passed positionally to the target call.")
            parts.extend(keywords.values())
        return ", ".join(parts)

    # -- print -------------------------------------------------------
    def _call_print(self, node: ast.Call, args: list[str], keywords: dict[str, str]) -> str:
        end = keywords.get("end")
        if node.args and len(node.args) == 1 and isinstance(node.args[0], ast.JoinedStr):
            return self._print_fstring(node.args[0], end=end)

        if not args:
            return {
                "javascript": "console.log()", "typescript": "console.log()",
                "java": "System.out.println()", "csharp": "Console.WriteLine()",
                "cpp": "std::cout << std::endl", "rust": 'println!()',
                "go": "fmt.Println()", "php": "echo PHP_EOL;", "python": "print()",
            }.get(self.target, "print()")

        rendered = ", ".join(args)
        if self.target in {"javascript", "typescript"}:
            if end and end not in {'"\\n"', "null"}:
                self.warn("console.log always appends a newline; `end=` was ignored.")
            return f"console.log({rendered})"
        if self.target == "java":
            method = "print" if end in {'""', "null"} else "println"
            if len(args) > 1:
                joined = ' + " " + '.join(args)
                return f"System.out.{method}({joined})"
            return f"System.out.{method}({rendered})"
        if self.target == "csharp":
            method = "Write" if end in {'""', "null"} else "WriteLine"
            if len(args) > 1:
                joined = ' + " " + '.join(args)
                return f"Console.{method}({joined})"
            return f"Console.{method}({rendered})"
        if self.target == "cpp":
            if len(args) > 1:
                body = ' << " " << '.join(args)
            else:
                body = args[0]
            suffix = "" if end in {'""', "null"} else " << std::endl"
            return f"std::cout << {body}{suffix}"
        if self.target == "rust":
            macro = "print!" if end in {'""', "null"} else "println!"
            if len(args) == 1:
                return f"{macro}(\"{{}}\", {args[0]})"
            template = " ".join(["{}"] * len(args))
            return f'{macro}("{template}", {rendered})'
        if self.target == "go":
            self.go_imports.add("fmt")
            function = "fmt.Print" if end in {'""', "null"} else "fmt.Println"
            return f"{function}({rendered})"
        if self.target == "php":
            if len(args) > 1:
                return "echo " + ' . " " . '.join(args) + ";"
            return f"echo {rendered};"
        return f"print({rendered})"

    def _print_fstring(self, node: ast.JoinedStr, *, end: str | None) -> str:
        parts = self.fstring_parts(node)
        if self.target in {"javascript", "typescript"}:
            return f"console.log({self.fstring(node)})"
        if self.target == "csharp":
            return f"Console.WriteLine({self.fstring(node)})"
        if self.target == "php":
            return "echo " + self.fstring(node) + ";"
        if self.target == "java":
            fmt, values = self._format_template(parts, percent=True)
            if values:
                return f'System.out.println(String.format("{fmt}", {", ".join(values)}))'
            return f'System.out.println("{fmt}")'
        if self.target == "go":
            self.go_imports.add("fmt")
            fmt, values = self._format_template(parts, percent=True)
            fmt = fmt.replace("%s", "%v")
            function = "fmt.Printf" if end not in {'""', "null"} else "fmt.Printf"
            if values:
                return f'{function}("{fmt}\\n", {", ".join(values)})'
            return f'{function}("{fmt}\\n")'
        if self.target == "rust":
            fmt, values = self._format_template(parts, braces=True)
            macro = "print!" if end in {'""', "null"} else "println!"
            if values:
                return f'{macro}("{fmt}", {", ".join(values)})'
            return f'{macro}("{fmt}")'
        if self.target == "cpp":
            pieces = []
            for part in parts:
                pieces.append(self.string(part[1]) if part[0] == "text" else part[1])
            return "std::cout << " + " << ".join(pieces) + " << std::endl"
        return f"print({self.fstring(node)})"

    # -- builtins ----------------------------------------------------
    def _builtin_call(self, name: str, node: ast.Call, args: list[str], keywords: dict[str, str]) -> str:
        if name == "print":
            return self._call_print(node, args, keywords)
        if name == "len":
            return self._len(args)
        if name in {"str", "repr"}:
            return self._str(args)
        if name == "int":
            return self._int(args)
        if name == "float":
            return self._float(args)
        if name == "bool":
            return self._bool(args)
        if name == "abs":
            return self._abs(args)
        if name in {"min", "max"}:
            return self._min_max(name, args)
        if name == "sum":
            return self._sum(args)
        if name == "round":
            return self._round(args)
        if name == "sorted":
            return self._sorted(args)
        if name == "reversed":
            return self._reversed(args)
        if name == "range":
            return self._range_expr(args)
        if name == "enumerate":
            return self._enumerate_expr(args)
        if name == "zip":
            return self._zip_expr(args)
        if name == "list":
            return self._list_call(args)
        if name in {"dict", "set", "tuple"}:
            return self._collection_call(name, args)
        if name == "isinstance":
            if len(node.args) >= 2:
                return self._isinstance(node.args[0], node.args[1])
            return self.bool_literal(False)
        if name == "type":
            return self._type_call(args)
        if name == "ord":
            return self._ord(args)
        if name == "chr":
            return self._chr(args)
        if name == "input":
            return self._input(keywords.get("prompt", '""'))
        if name == "open":
            self.warn("`open()` needs target-specific I/O plumbing; a generic handle was emitted.")
            return f"open({', '.join(args)})"
        if name == "format":
            return self._format_call(None, args, keywords)
        if name in {"iter", "next"}:
            return f"{args[0] if args else 'null'}"
        rendered = self._render_args(args, keywords)
        return f"{name}({rendered})"

    def _len(self, args: list[str]) -> str:
        value = args[0] if args else '""'
        if self.target in {"javascript", "typescript"}:
            return f"Array.isArray({value}) || typeof {value} === \"string\" ? {value}.length : ({value} instanceof Map || {value} instanceof Set ? {value}.size : Object.keys({value} ?? {{}}).length)"
        if self.target == "java":
            return f"(({value}) instanceof String ? ((String) ({value})).length() : ((java.util.Collection<?>) ({value})).size())"
        if self.target == "csharp":
            return f"(({value}) is string ? ((string) ({value})).Length : System.Linq.Enumerable.Count((System.Collections.IEnumerable) ({value})))"
        if self.target == "cpp":
            return f"static_cast<long>({value}.size())"
        if self.target == "rust":
            return f"{value}.len() as i64"
        if self.target == "go":
            return f"len({value})"
        if self.target == "php":
            return f"(is_array({value}) ? count({value}) : strlen((string) ({value})))"
        return f"len({value})"

    def _str(self, args: list[str]) -> str:
        value = args[0] if args else '""'
        if self.target in {"javascript", "typescript"}:
            return f"String({value})"
        if self.target == "java":
            return f"String.valueOf({value})"
        if self.target == "csharp":
            return f"Convert.ToString({value}) ?? \"\""
        if self.target == "cpp":
            return f"[&]() {{ std::ostringstream __s; __s << {value}; return __s.str(); }}()"
        if self.target == "rust":
            return f"format!(\"{{}}\", {value})"
        if self.target == "go":
            self.go_imports.add("fmt")
            return f"fmt.Sprint({value})"
        if self.target == "php":
            return f"(string) ({value})"
        return f"str({value})"

    def _int(self, args: list[str]) -> str:
        value = args[0] if args else "0"
        if self.target in {"javascript", "typescript"}:
            return f"parseInt(String({value}), 10)"
        if self.target == "java":
            return f"Integer.parseInt(String.valueOf({value}))"
        if self.target == "csharp":
            return f"Convert.ToInt32({value})"
        if self.target == "cpp":
            return f"static_cast<int>({value})"
        if self.target == "rust":
            return f"({value}).to_string().parse::<i64>().unwrap_or_default()"
        if self.target == "go":
            self.go_imports.update({"strconv", "fmt"})
            return f"func() int {{ v, _ := strconv.Atoi(fmt.Sprint({value})); return v }}()"
        if self.target == "php":
            return f"intval({value})"
        return f"int({value})"

    def _float(self, args: list[str]) -> str:
        value = args[0] if args else "0.0"
        if self.target in {"javascript", "typescript"}:
            return f"parseFloat(String({value}))"
        if self.target == "java":
            return f"Double.parseDouble(String.valueOf({value}))"
        if self.target == "csharp":
            return f"Convert.ToDouble({value})"
        if self.target == "cpp":
            return f"static_cast<double>({value})"
        if self.target == "rust":
            return f"({value}).to_string().parse::<f64>().unwrap_or_default()"
        if self.target == "go":
            self.go_imports.update({"strconv", "fmt"})
            return f"func() float64 {{ v, _ := strconv.ParseFloat(fmt.Sprint({value}), 64); return v }}()"
        if self.target == "php":
            return f"floatval({value})"
        return f"float({value})"

    def _bool(self, args: list[str]) -> str:
        value = args[0] if args else "false"
        if self.target in {"javascript", "typescript"}:
            return f"Boolean({value})"
        if self.target == "java":
            return f"Boolean.parseBoolean(String.valueOf({value}))"
        if self.target == "csharp":
            return f"Convert.ToBoolean({value})"
        if self.target == "cpp":
            return f"static_cast<bool>({value})"
        if self.target == "rust":
            return f"({value}) != 0"
        if self.target == "go":
            return f"({value}) != nil && ({value}) != false && ({value}) != 0"
        if self.target == "php":
            return f"(bool) ({value})"
        return f"bool({value})"

    def _abs(self, args: list[str]) -> str:
        value = args[0] if args else "0"
        if self.target in {"javascript", "typescript"}:
            return f"Math.abs({value})"
        if self.target == "java":
            return f"Math.abs({value})"
        if self.target == "csharp":
            return f"Math.Abs({value})"
        if self.target == "cpp":
            return f"std::abs({value})"
        if self.target == "rust":
            return f"({value}).abs()"
        if self.target == "go":
            self.go_imports.add("math")
            return f"math.Abs({value})"
        if self.target == "php":
            return f"abs({value})"
        return f"abs({value})"

    def _min_max(self, name: str, args: list[str]) -> str:
        if len(args) == 1:
            source = args[0]
            if self.target in {"javascript", "typescript"}:
                return f"Math.{name}(...{source})"
            if self.target == "java":
                return f"{source}.stream().mapToDouble(Number::doubleValue).{name}().orElse(0)"
            if self.target == "csharp":
                return f"{source}.{('Min' if name == 'min' else 'Max')}()"
            if self.target == "cpp":
                return f"*std::{name}_element({source}.begin(), {source}.end())"
            if self.target == "rust":
                return f"*{source}.iter().{name}().unwrap()"
            if self.target == "go":
                return f"func() any {{ v := {source}[0]; for _, x := range {source} {{ if x < v {{ v = x }} }}; return v }}()"
            if self.target == "php":
                return f"{name}({source})"
        a = args[0] if args else "0"
        b = args[1] if len(args) > 1 else "0"
        if self.target in {"javascript", "typescript"}:
            return f"Math.{name}({a}, {b})"
        if self.target == "java":
            return f"Math.{name}({a}, {b})"
        if self.target == "csharp":
            return f"Math.{'Min' if name == 'min' else 'Max'}({a}, {b})"
        if self.target == "cpp":
            return f"std::{name}({a}, {b})"
        if self.target == "rust":
            return f"std::cmp::{name}({a}, {b})"
        if self.target == "go":
            return f"{name}({a}, {b})"
        if self.target == "php":
            return f"{name}({a}, {b})"
        return f"{name}({a}, {b})"

    def _sum(self, args: list[str]) -> str:
        source = args[0] if args else "[]"
        if self.target in {"javascript", "typescript"}:
            return f"{source}.reduce((total, value) => total + Number(value), 0)"
        if self.target == "java":
            return f"{source}.stream().mapToDouble(Number::doubleValue).sum()"
        if self.target == "csharp":
            return f"{source}.Sum(value => Convert.ToDouble(value))"
        if self.target == "cpp":
            return f"std::accumulate({source}.begin(), {source}.end(), 0.0)"
        if self.target == "rust":
            return f"{source}.iter().sum::<f64>()"
        if self.target == "go":
            return f"func() float64 {{ total := 0.0; for _, value := range {source} {{ total += float64(value.(int)) }}; return total }}()"
        if self.target == "php":
            return f"array_sum({source})"
        return f"sum({source})"

    def _round(self, args: list[str]) -> str:
        value = args[0] if args else "0"
        digits = args[1] if len(args) > 1 else None
        if self.target in {"javascript", "typescript"}:
            if digits:
                return f"Number(Math.round(Number({value}) * Math.pow(10, {digits})) / Math.pow(10, {digits}))"
            return f"Math.round({value})"
        if self.target == "java":
            if digits:
                return f"(double) Math.round(({value}) * Math.pow(10, {digits})) / Math.pow(10, {digits})"
            return f"Math.round({value})"
        if self.target == "csharp":
            return f"Math.Round({value}, {digits})" if digits else f"Math.Round({value})"
        if self.target == "cpp":
            if digits:
                return f"std::round(({value}) * std::pow(10, {digits})) / std::pow(10, {digits})"
            return f"std::round({value})"
        if self.target == "rust":
            if digits:
                return f"(({value}) * 10f64.powi({digits})).round() / 10f64.powi({digits})"
            return f"({value}).round()"
        if self.target == "go":
            self.go_imports.add("math")
            if digits:
                return f"math.Round(({value}) * math.Pow(10, {digits})) / math.Pow(10, {digits})"
            return f"math.Round({value})"
        if self.target == "php":
            return f"round({value}, {digits})" if digits else f"round({value})"
        return f"round({value})"

    def _sorted(self, args: list[str]) -> str:
        source = args[0] if args else "[]"
        if self.target in {"javascript", "typescript"}:
            return f"[...{source}].sort()"
        if self.target == "java":
            return f"{source}.stream().sorted().toList()"
        if self.target == "csharp":
            return f"{source}.OrderBy(value => value).ToList()"
        if self.target == "cpp":
            return f"[&]() {{ auto __values = {source}; std::sort(__values.begin(), __values.end()); return __values; }}()"
        if self.target == "rust":
            return f"{{ let mut __values = {source}.clone(); __values.sort(); __values }}"
        if self.target == "go":
            self.go_imports.update({"sort", "fmt"})
            return f"func() []any {{ __values := append([]any{{}}, {source}...); sort.Slice(__values, func(i, j int) bool {{ return fmt.Sprint(__values[i]) < fmt.Sprint(__values[j]) }}); return __values }}()"
        if self.target == "php":
            return f"(function (array $values): array {{ sort($values); return $values; }})({source})"
        return f"sorted({source})"

    def _reversed(self, args: list[str]) -> str:
        source = args[0] if args else "[]"
        if self.target in {"javascript", "typescript"}:
            return f"[...{source}].reverse()"
        if self.target == "java":
            return f"[...{source}].reversed()" if False else f"java.util.stream.StreamSupport.stream({source}.spliterator(), false).toList().reversed()"
        if self.target == "csharp":
            return f"{source}.Reverse().ToList()"
        if self.target == "cpp":
            return f"[&]() {{ auto __values = {source}; std::reverse(__values.begin(), __values.end()); return __values; }}()"
        if self.target == "rust":
            return f"{{ let mut __values = {source}.clone(); __values.reverse(); __values }}"
        if self.target == "go":
            return f"func() []any {{ __values := make([]any, len({source})); for i := range {source} {{ __values[len({source})-1-i] = {source}[i] }}; return __values }}()"
        if self.target == "php":
            return f"array_reverse({source})"
        return f"reversed({source})"

    def _range_expr(self, args: list[str]) -> str:
        start, stop, step = self._range_parts(args)
        if self.target in {"javascript", "typescript"}:
            return (
                f"Array.from({{ length: Math.max(0, Math.ceil(({stop} - {start}) / ({step}))) }}, "
                f"(_, index) => {start} + index * ({step}))"
            )
        if self.target == "java":
            return f"java.util.stream.IntStream.iterate({start}, value -> value + {step}).limit(Math.max(0, ({stop} - {start}) / ({step}))).boxed().toList()"
        if self.target == "csharp":
            return f"Enumerable.Range({start}, Math.Max(0, ({stop} - {start}) / ({step}))).Select(value => {start} + value * ({step})).ToList()"
        if self.target == "cpp":
            return f"[&]() {{ std::vector<int> __values; for (int value = {start}; value < {stop}; value += {step}) __values.push_back(value); return __values; }}()"
        if self.target == "rust":
            return f"({start}..{stop}).step_by({step} as usize).collect::<Vec<_>>()"
        if self.target == "go":
            return f"func() []int {{ __values := []int{{}}; for value := {start}; value < {stop}; value += {step} {{ __values = append(__values, value) }}; return __values }}()"
        if self.target == "php":
            return f"range({start}, {stop} - {step}, {step})"
        return f"range({', '.join(args)})"

    def _range_parts(self, args: list[str]) -> tuple[str, str, str]:
        if len(args) == 1:
            return "0", args[0], "1"
        if len(args) == 2:
            return args[0], args[1], "1"
        if len(args) >= 3:
            return args[0], args[1], args[2]
        return "0", "0", "1"

    def _enumerate_expr(self, args: list[str]) -> str:
        source = args[0] if args else "[]"
        if self.target in {"javascript", "typescript"}:
            return f"Array.from({source}).map((value, index) => [index, value])"
        if self.target == "java":
            return f"java.util.stream.IntStream.range(0, {source}.size()).mapToObj(index -> new Object[] {{ index, {source}.get(index) }}).toList()"
        if self.target == "csharp":
            return f"{source}.Select((value, index) => new object[] {{ index, value }}).ToList()"
        if self.target == "cpp":
            return f"[&]() {{ std::vector<std::pair<std::size_t, std::decay_t<decltype({source}[0])>>> __values; for (std::size_t index = 0; index < {source}.size(); ++index) __values.emplace_back(index, {source}[index]); return __values; }}()"
        if self.target == "rust":
            return f"{source}.iter().enumerate().collect::<Vec<_>>()"
        if self.target == "go":
            return f"func() []any {{ __values := make([]any, 0, len({source})); for index, value := range {source} {{ __values = append(__values, []any{{index, value}}) }}; return __values }}()"
        if self.target == "php":
            return f"array_map(fn($key, $value) => [$key, $value], array_keys({source}), {source})"
        return f"enumerate({source})"

    def _zip_expr(self, args: list[str]) -> str:
        if not args:
            return "[]"
        source = args[0]
        if self.target in {"javascript", "typescript"}:
            return f"Array.from({{ length: Math.min({', '.join(f'{value}.length' for value in args)}) }}, (_, index) => [{', '.join(f'{value}[index]' for value in args)}])"
        if self.target == "java":
            return f"java.util.stream.IntStream.range(0, Math.min({', '.join(f'{value}.size()' for value in args)})).mapToObj(index -> new Object[] {{ {', '.join(f'{value}.get(index)' for value in args)} }}).toList()"
        if self.target == "csharp":
            return f"Enumerable.Range(0, new[] {{ {', '.join(f'{value}.Count' for value in args)} }}.Min()).Select(index => new object[] {{ {', '.join(f'{value}[index]' for value in args)} }}).ToList()"
        if self.target == "rust":
            return f"{source}.iter().zip({'(' + ', '.join(args[1:]) + ')' if len(args) > 1 else '[]'}.iter()).collect::<Vec<_>>()" if len(args) == 2 else f"{source}.iter().collect::<Vec<_>>()"
        if self.target == "php":
            return f"array_map(null, {', '.join(args)})"
        if self.target == "go":
            return f"func() []any {{ __values := []any{{}}; for index := 0; index < len({source}); index++ {{ __values = append(__values, []any{{ {', '.join(f'{value}[index]' for value in args)} }}) }}; return __values }}()"
        return f"zip({', '.join(args)})"

    def _list_call(self, args: list[str]) -> str:
        if not args:
            return self._list_literal([])
        source = args[0]
        if self.target in {"javascript", "typescript"}:
            return f"Array.from({source})"
        if self.target == "java":
            return f"new ArrayList<>({source})"
        if self.target == "csharp":
            return f"new List<object>({source})"
        if self.target == "cpp":
            return f"std::vector<std::any>({source}.begin(), {source}.end())"
        if self.target == "rust":
            return f"{source}.iter().cloned().collect::<Vec<_>>()"
        if self.target == "go":
            return f"{source}"
        if self.target == "php":
            return f"array_values({source})"
        return f"list({source})"

    def _collection_call(self, name: str, args: list[str]) -> str:
        if name == "dict":
            if not args:
                return self._expr_Dict(ast.Dict(keys=[], values=[]))
            return args[0]
        if name == "set":
            source = args[0] if args else "[]"
            if self.target in {"javascript", "typescript"}:
                return f"new Set({source})"
            if self.target == "java":
                return f"new HashSet<>({source})"
            if self.target == "csharp":
                return f"new HashSet<object>({source})"
            if self.target == "rust":
                return f"{source}.into_iter().collect::<std::collections::HashSet<_>>()"
            return source
        if name == "tuple":
            return self._tuple_literal([]) if not args else args[0]
        return name

    def _type_call(self, args: list[str]) -> str:
        value = args[0] if args else "null"
        if self.target in {"javascript", "typescript"}:
            return f"typeof {value}"
        if self.target == "php":
            return f"gettype({value})"
        self.warn("Python `type()` was mapped to a runtime type check.")
        return f"{value}.GetType()" if self.target == "csharp" else f"{value}.getClass()" if self.target == "java" else f"type({value})"

    def _ord(self, args: list[str]) -> str:
        value = args[0] if args else '""'
        if self.target == "java":
            return f"(int) ({value}).charAt(0)"
        if self.target == "csharp":
            return f"(int) ({value})[0]"
        if self.target == "cpp":
            return f"static_cast<int>({value}[0])"
        if self.target == "rust":
            return f"{value}.chars().next().unwrap() as i64"
        if self.target == "go":
            return f"int([]rune({value})[0])"
        if self.target == "php":
            return f"ord({value})"
        return f"({value}).charCodeAt(0)"

    def _chr(self, args: list[str]) -> str:
        value = args[0] if args else "0"
        if self.target == "java":
            return f"String.valueOf((char) (int) ({value}))"
        if self.target == "csharp":
            return f"((char) (int) ({value})).ToString()"
        if self.target == "cpp":
            return f"std::string(1, static_cast<char>({value}))"
        if self.target == "rust":
            return f"char::from_u32({value} as u32).unwrap().to_string()"
        if self.target == "go":
            return f"string(rune({value}))"
        if self.target == "php":
            return f"chr({value})"
        return f"String.fromCharCode({value})"

    def _input(self, prompt: str) -> str:
        if self.target in {"javascript", "typescript"}:
            return f"prompt({prompt}) ?? \"\""
        if self.target == "java":
            return "new Scanner(System.in).nextLine()"
        if self.target == "csharp":
            return "Console.ReadLine() ?? \"\""
        if self.target == "cpp":
            return "[&]() { std::string __line; std::getline(std::cin, __line); return __line; }()"
        if self.target == "rust":
            return "{ let mut __line = String::new(); std::io::stdin().read_line(&mut __line).unwrap(); __line.trim().to_string() }"
        if self.target == "go":
            self.go_imports.add("fmt")
            return "func() string { var __line string; fmt.Scanln(&__line); return __line }()"
        if self.target == "php":
            return "trim(fgets(STDIN))"
        return f"input({prompt})"

    def _format_call(self, template, args: list[str], keywords: dict[str, str]) -> str:
        if template is not None:
            text = str(template)
            values = list(args) + list(keywords.values())
            if self.target in {"javascript", "typescript"}:
                return f"String({self.string(text)}).replace(/\\{{(\\d+)\\}}/g, (_, index) => String([{', '.join(values)}][Number(index)]))"
            if self.target == "php":
                return f"sprintf({self.string(text.replace('{', '%s'))}, {', '.join(values)})"
        self.warn("`str.format` was translated as a target formatting call.")
        if self.target in {"javascript", "typescript"}:
            return f"({args[0] if args else '\"\"'})"
        return f"({args[0] if args else '\"\"'})"

    # -- methods -----------------------------------------------------
    def _method_call(self, receiver_node: ast.AST, method: str, node: ast.Call, args: list[str], keywords: dict[str, str]) -> str:
        receiver = self.expr(receiver_node)
        if self.target in {"rust", "go"}:
            info = self.method_defaults.get(method)
            if info and len(args) < info["max"]:
                for index in range(len(args), info["max"]):
                    default = info["defaults"].get(index)
                    if default is not None:
                        args.append(self.expr(default))
        arg_text = ", ".join(args)
        if method == "append":
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.push({arg_text})"
            if self.target == "java":
                return f"{receiver}.add({arg_text})"
            if self.target == "csharp":
                return f"{receiver}.Add({arg_text})"
            if self.target == "cpp":
                return f"{receiver}.push_back({arg_text})"
            if self.target == "rust":
                return f"{receiver}.push({arg_text})"
            if self.target == "go":
                return f"append({receiver}, {arg_text})"
            if self.target == "php":
                return f"{receiver}[] = {arg_text}"
            return f"{receiver}.append({arg_text})"
        if method == "extend":
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.push(...{arg_text})"
            if self.target == "java":
                return f"{receiver}.addAll({arg_text})"
            if self.target == "csharp":
                return f"{receiver}.AddRange({arg_text})"
            if self.target == "cpp":
                return f"{receiver}.insert({receiver}.end(), {arg_text}.begin(), {arg_text}.end())"
            if self.target == "rust":
                return f"{receiver}.extend({arg_text})"
            if self.target == "php":
                return f"array_push({receiver}, ...{arg_text})"
            return f"{receiver}.extend({arg_text})"
        if method == "insert":
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.splice({args[0] if args else '0'}, 0, {args[1] if len(args) > 1 else 'null'})"
            if self.target in {"java", "csharp"}:
                return f"{receiver}.Insert({arg_text})" if self.target == "csharp" else f"{receiver}.add({arg_text})"
            if self.target == "cpp":
                return f"{receiver}.insert({receiver}.begin() + {args[0] if args else '0'}, {args[1] if len(args) > 1 else 'null'})"
            if self.target == "php":
                return f"array_splice({receiver}, {args[0] if args else '0'}, 0, {args[1] if len(args) > 1 else 'null'})"
            return f"{receiver}.insert({arg_text})"
        if method == "pop":
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.pop()"
            if self.target == "java":
                return f"{receiver}.remove({receiver}.size() - 1)"
            if self.target == "csharp":
                return f"{receiver}[{receiver}.Count - 1]"
            if self.target == "cpp":
                return f"[&]() {{ auto __value = {receiver}.back(); {receiver}.pop_back(); return __value; }}()"
            if self.target == "rust":
                return f"{receiver}.pop().unwrap()"
            if self.target == "php":
                return f"array_pop({receiver})"
            return f"{receiver}.pop()"
        if method == "clear":
            if self.target in {"javascript", "typescript", "java", "csharp", "cpp", "php"}:
                return f"{receiver}.clear()" if self.target != "php" else f"{receiver}.clear()"
            if self.target == "rust":
                return f"{receiver}.clear()"
            return f"{receiver}.clear()"
        if method in {"sort", "reverse"}:
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.{method}()"
            if self.target == "java":
                return f"{'Collections.sort' if method == 'sort' else 'Collections.reverse'}({receiver})"
            if self.target == "csharp":
                return f"{receiver}.{('Sort' if method == 'sort' else 'Reverse')}()"
            if self.target == "cpp":
                return f"std::{method}({receiver}.begin(), {receiver}.end())"
            if self.target == "rust":
                return f"{receiver}.{method}()"
            if self.target == "php":
                return f"{'sort' if method == 'sort' else 'array_reverse'}({receiver})"
            return f"{receiver}.{method}()"
        if method == "get":
            key = args[0] if args else '""'
            default = args[1] if len(args) > 1 else self.none()
            if self.target in {"javascript", "typescript"}:
                return f"({receiver}[{key}] ?? {default})"
            if self.target == "java":
                return f"{receiver}.getOrDefault({key}, {default})"
            if self.target == "csharp":
                return f"{receiver}.GetValueOrDefault({key}, {default})"
            if self.target == "cpp":
                return f"({receiver}.count({key}) ? {receiver}.at({key}) : {default})"
            if self.target == "rust":
                return f"{receiver}.get({key}).cloned().unwrap_or({default})"
            if self.target == "go":
                return f"{receiver}[{key}]"
            if self.target == "php":
                return f"({receiver}[{key}] ?? {default})"
            return f"{receiver}.get({arg_text})"
        if method == "keys":
            return f"{receiver}.keys()" if self.target in {"javascript", "typescript", "python"} else (
                f"{receiver}.keySet()" if self.target == "java" else f"{receiver}.Keys" if self.target == "csharp" else f"func() []any {{ keys := []any{{}}; for key := range {receiver} {{ keys = append(keys, key) }}; return keys }}()" if self.target == "go" else f"{receiver}.keys()" if self.target == "rust" else f"array_keys({receiver})"
            )
        if method == "values":
            return f"{receiver}.values()" if self.target in {"javascript", "typescript", "python"} else (
                f"{receiver}.values()" if self.target == "java" else f"{receiver}.Values" if self.target == "csharp" else f"func() []any {{ values := []any{{}}; for _, value := range {receiver} {{ values = append(values, value) }}; return values }}()" if self.target == "go" else f"{receiver}.values()" if self.target == "rust" else f"array_values({receiver})"
            )
        if method == "items":
            if self.target in {"javascript", "typescript"}:
                return f"Object.entries({receiver})"
            if self.target == "java":
                return f"{receiver}.entrySet()"
            if self.target == "csharp":
                return f"{receiver}.Select(entry => new object[] {{ entry.Key, entry.Value }})"
            if self.target == "go":
                return f"func() []any {{ items := []any{{}}; for key, value := range {receiver} {{ items = append(items, []any{{key, value}}) }}; return items }}()"
            if self.target == "rust":
                return f"{receiver}.iter()"
            if self.target == "php":
                return f"array_map(null, array_keys({receiver}), array_values({receiver}))"
            return f"{receiver}.items()"
        if method == "update":
            if self.target in {"javascript", "typescript"}:
                return f"Object.assign({receiver}, {arg_text})"
            if self.target == "java":
                return f"{receiver}.putAll({arg_text})"
            if self.target == "csharp":
                return f"{receiver}.Update({arg_text})" if False else f"{receiver}.TryAdd({arg_text})"
            if self.target == "php":
                return f"{receiver} = array_merge({receiver}, {arg_text})"
            return f"{receiver}.update({arg_text})"
        if method in {"split", "rsplit"}:
            separator = args[0] if args else '" "'
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.split({separator})"
            if self.target == "java":
                return f"{receiver}.split({separator})"
            if self.target == "csharp":
                return f"{receiver}.Split({separator})"
            if self.target == "rust":
                return f"{receiver}.split({separator}).map(|part| part.to_string()).collect::<Vec<_>>()"
            if self.target == "go":
                self.go_imports.add("strings")
                return f"strings.Split({receiver}, {separator})"
            if self.target == "php":
                return f"explode({separator}, {receiver})"
            return f"{receiver}.split({separator})"
        if method == "join":
            separator = receiver
            values = args[0] if args else "[]"
            if self.target in {"javascript", "typescript", "java", "csharp", "rust", "go", "php"}:
                if self.target in {"javascript", "typescript", "rust"}:
                    return f"{values}.join({separator})"
                if self.target == "java":
                    return f"String.join({separator}, {values})"
                if self.target == "csharp":
                    return f"string.Join({separator}, {values})"
                if self.target == "go":
                    self.go_imports.add("strings")
                    return f"strings.Join({values}, {separator})"
                if self.target == "php":
                    return f"implode({separator}, {values})"
            return f"{separator}.join({values})"
        if method in {"strip", "lstrip", "rstrip"}:
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.{method}()"
            if self.target == "java":
                return f"{receiver}.strip()" if method == "strip" else f"{receiver}.stripLeading()" if method == "lstrip" else f"{receiver}.stripTrailing()"
            if self.target == "csharp":
                return f"{receiver}.Trim()" if method == "strip" else f"{receiver}.TrimStart()" if method == "lstrip" else f"{receiver}.TrimEnd()"
            if self.target == "cpp":
                return f"[&]() {{ std::string __s = {receiver}; auto __pred = [](unsigned char c) {{ return !std::isspace(c); }}; __s.erase(__s.begin(), std::find_if(__s.begin(), __s.end(), __pred)); __s.erase(std::find_if(__s.rbegin(), __s.rend(), __pred).base(), __s.end()); return __s; }}()"
            if self.target == "rust":
                return f"{receiver}.trim().to_string()" if method == "strip" else f"{receiver}.trim_start().to_string()" if method == "lstrip" else f"{receiver}.trim_end().to_string()"
            if self.target == "go":
                self.go_imports.add("strings")
                return f"strings.{'TrimSpace' if method == 'strip' else 'TrimLeft' if method == 'lstrip' else 'TrimRight'}({receiver})"
            if self.target == "php":
                return f"{'trim' if method == 'strip' else 'ltrim' if method == 'lstrip' else 'rtrim'}({receiver})"
            return f"{receiver}.{method}()"
        if method in {"upper", "lower", "title", "capitalize"}:
            if self.target in {"javascript", "typescript"}:
                if method == "upper":
                    return f"{receiver}.toUpperCase()"
                if method == "lower":
                    return f"{receiver}.toLowerCase()"
                if method == "capitalize":
                    return f"{receiver}.charAt(0).toUpperCase() + {receiver}.slice(1)"
                return f"{receiver}.replace(/\\b\\w/g, char => char.toUpperCase())"
            if self.target == "java":
                return f"{receiver}.toUpperCase()" if method == "upper" else f"{receiver}.toLowerCase()" if method == "lower" else f"{receiver}.substring(0, 1).toUpperCase() + {receiver}.substring(1)"
            if self.target == "csharp":
                return f"{receiver}.ToUpper()" if method == "upper" else f"{receiver}.ToLower()" if method == "lower" else f"System.Globalization.CultureInfo.CurrentCulture.TextInfo.ToTitleCase({receiver})" if method == "title" else f"{receiver}.Substring(0, 1).ToUpper() + {receiver}.Substring(1)"
            if self.target == "rust":
                return f"{receiver}.to_uppercase()" if method == "upper" else f"{receiver}.to_lowercase()" if method == "lower" else f"{receiver}.to_string()"
            if self.target == "go":
                self.go_imports.add("strings")
                return f"strings.{'ToUpper' if method == 'upper' else 'ToLower' if method == 'lower' else 'Title'}({receiver})"
            if self.target == "php":
                return f"{'strtoupper' if method == 'upper' else 'strtolower' if method == 'lower' else 'ucfirst' if method == 'capitalize' else 'ucwords'}({receiver})"
            if self.target == "cpp":
                return f"[&]() {{ std::string __s = {receiver}; std::transform(__s.begin(), __s.end(), __s.begin(), [](unsigned char c) {{ return {'std::toupper' if method == 'upper' else 'std::tolower'}(c); }}); return __s; }}()"
            return f"{receiver}.{method}()"
        if method == "replace":
            old = args[0] if args else '""'
            new = args[1] if len(args) > 1 else '""'
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.replaceAll({old}, {new})"
            if self.target == "java":
                return f"{receiver}.replace({old}, {new})"
            if self.target == "csharp":
                return f"{receiver}.Replace({old}, {new})"
            if self.target == "cpp":
                return f"[&]() {{ std::string __s = {receiver}; std::string __from = {old}; std::string __to = {new}; std::size_t __pos = 0; while ((__pos = __s.find(__from, __pos)) != std::string::npos) {{ __s.replace(__pos, __from.size(), __to); __pos += __to.size(); }} return __s; }}()"
            if self.target == "rust":
                return f"{receiver}.replace({old}, {new})"
            if self.target == "go":
                self.go_imports.add("strings")
                return f"strings.ReplaceAll({receiver}, {old}, {new})"
            if self.target == "php":
                return f"str_replace({old}, {new}, {receiver})"
            return f"{receiver}.replace({old}, {new})"
        if method in {"startswith", "endswith"}:
            value = args[0] if args else '""'
            if self.target in {"javascript", "typescript"}:
                return f"{receiver}.{'startsWith' if method == 'startswith' else 'endsWith'}({value})"
            if self.target == "java":
                return f"{receiver}.{'startsWith' if method == 'startswith' else 'endsWith'}({value})"
            if self.target == "csharp":
                return f"{receiver}.{'StartsWith' if method == 'startswith' else 'EndsWith'}({value})"
            if self.target == "rust":
                return f"{receiver}.{'starts_with' if method == 'startswith' else 'ends_with'}({value})"
            if self.target == "go":
                self.go_imports.add("strings")
                return f"strings.{'HasPrefix' if method == 'startswith' else 'HasSuffix'}({receiver}, {value})"
            if self.target == "php":
                return f"{'str_starts_with' if method == 'startswith' else 'str_ends_with'}({receiver}, {value})"
            return f"{receiver}.{method}({value})"
        if method == "format":
            return self._format_call(None, [receiver] + args, keywords)
        if method == "encode":
            return f"{receiver}.encode()" if self.target in {"javascript", "typescript", "rust", "python"} else receiver
        if method == "decode":
            return f"{receiver}.decode()" if self.target in {"javascript", "typescript", "rust", "python"} else receiver
        if method in {"setdefault", "copy"}:
            return f"{receiver}.{method}({arg_text})"
        rendered_args = self._render_args(args, keywords)
        if method in {"read", "write", "close", "flush", "readline"}:
            return f"{receiver}.{method}({rendered_args})"
        mapped = self.method_target_names.get(method)
        if mapped and mapped != method:
            return f"{receiver}.{mapped}({rendered_args})"
        return f"{receiver}.{method}({rendered_args})"

    # -- modules -----------------------------------------------------
    def _module_call(self, module: str, method: str, node: ast.Call, args: list[str], keywords: dict[str, str]) -> str:
        if module == "math":
            return self._math_call(method, args)
        if module == "random":
            if method == "random":
                if self.target in {"javascript", "typescript"}:
                    return "Math.random()"
                if self.target == "java":
                    return "Math.random()"
                if self.target == "csharp":
                    return "(new Random()).NextDouble()"
                if self.target == "cpp":
                    return "static_cast<double>(std::rand()) / RAND_MAX"
                if self.target == "rust":
                    return "rand::random::<f64>()"
                if self.target == "go":
                    self.go_imports.add("math/rand")
                    return "rand.Float64()"
                if self.target == "php":
                    return "(mt_rand() / mt_getrandmax())"
            if method == "randint":
                low = args[0] if args else "0"
                high = args[1] if len(args) > 1 else "0"
                if self.target in {"javascript", "typescript"}:
                    return f"Math.floor(Math.random() * ({high} - {low} + 1)) + {low}"
                if self.target == "java":
                    return f"java.util.concurrent.ThreadLocalRandom.current().nextInt({low}, {high} + 1)"
                if self.target == "csharp":
                    return f"(new Random()).Next({low}, {high} + 1)"
                if self.target == "cpp":
                    return f"({low} + std::rand() % ({high} - {low} + 1))"
                if self.target == "rust":
                    return f"rand::thread_rng().gen_range({low}..={high})"
                if self.target == "go":
                    self.go_imports.add("math/rand")
                    return f"({low} + rand.Intn({high} - {low} + 1))"
                if self.target == "php":
                    return f"random_int({low}, {high})"
            if method == "choice":
                source = args[0] if args else "[]"
                if self.target in {"javascript", "typescript"}:
                    return f"{source}[Math.floor(Math.random() * {source}.length)]"
                if self.target == "php":
                    return f"{source}[array_rand({source})]"
                if self.target == "go":
                    self.go_imports.add("math/rand")
                    return f"{source}[rand.Intn(len({source}))]"
                if self.target == "rust":
                    self.warn("`random.choice` needs the rand crate in Rust.")
                    return f"{source}[rand::thread_rng().gen_range(0..{source}.len())]"
            if method == "shuffle":
                source = args[0] if args else "[]"
                if self.target in {"javascript", "typescript"}:
                    return f"[...{source}].sort(() => Math.random() - 0.5)"
                if self.target == "php":
                    return f"(function (array $values): array {{ shuffle($values); return $values; }})({source})"
                if self.target == "go":
                    self.go_imports.add("math/rand")
                    return f"func() []any {{ __values := append([]any{{}}, {source}...); rand.Shuffle(len(__values), func(i, j int) {{ __values[i], __values[j] = __values[j], __values[i] }}); return __values }}()"
        if module == "time":
            if method == "time":
                if self.target in {"javascript", "typescript"}:
                    return "Date.now() / 1000"
                if self.target == "java":
                    return "System.currentTimeMillis() / 1000.0"
                if self.target == "csharp":
                    return "DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0"
                if self.target == "cpp":
                    return "static_cast<double>(std::time(nullptr))"
                if self.target == "rust":
                    return "std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()"
                if self.target == "go":
                    self.go_imports.add("time")
                    return "float64(time.Now().UnixNano()) / 1e9"
                if self.target == "php":
                    return "microtime(true)"
            if method == "sleep":
                seconds = args[0] if args else "1"
                if self.target == "php":
                    return f"sleep((int) ({seconds}))"
                if self.target == "go":
                    self.go_imports.add("time")
                    return f"time.Sleep(time.Duration(float64({seconds}) * float64(time.Second)))"
                if self.target == "cpp":
                    return f"std::this_thread::sleep_for(std::chrono::seconds({seconds}))"
                if self.target == "rust":
                    return f"std::thread::sleep(std::time::Duration::from_secs_f64({seconds} as f64))"
                return f"sleep({seconds})"
        if module == "os" and method == "getenv":
            key = args[0] if args else '""'
            if self.target in {"javascript", "typescript"}:
                return f"process.env[{key}]"
            if self.target == "java":
                return f"System.getenv(String.valueOf({key}))"
            if self.target == "csharp":
                return f"Environment.GetEnvironmentVariable(Convert.ToString({key}) ?? \"\")"
            if self.target == "cpp":
                return f"std::getenv(String.valueOf({key}).c_str())"
            if self.target == "rust":
                return f"std::env::var(String::from({key})).unwrap_or_default()"
            if self.target == "go":
                self.go_imports.update({"os", "fmt"})
                return f"os.Getenv(fmt.Sprint({key}))"
            if self.target == "php":
                return f"getenv({key})"
        if module == "json":
            if method == "dumps":
                value = args[0] if args else "null"
                if self.target in {"javascript", "typescript"}:
                    return f"JSON.stringify({value})"
                if self.target == "java":
                    return f"String.valueOf({value})"
                if self.target == "csharp":
                    return f"System.Text.Json.JsonSerializer.Serialize({value})"
                if self.target == "go":
                    self.go_imports.add("encoding/json")
                    return f"func() string {{ __bytes, _ := json.Marshal({value}); return string(__bytes) }}()"
                if self.target == "php":
                    return f"json_encode({value})"
                if self.target == "rust":
                    return f"serde_json::to_string(&{value}).unwrap_or_default()"
                if self.target == "cpp":
                    return f"std::string({value})"
            if method == "loads":
                value = args[0] if args else '""'
                if self.target in {"javascript", "typescript"}:
                    return f"JSON.parse({value})"
                if self.target == "csharp":
                    return f"System.Text.Json.JsonSerializer.Deserialize<object>({value})"
                if self.target == "go":
                    self.go_imports.add("encoding/json")
                    return f"func() any {{ var __value any; json.Unmarshal([]byte({value}), &__value); return __value }}()"
                if self.target == "php":
                    return f"json_decode({value}, true)"
                if self.target == "rust":
                    return f"serde_json::from_str::<serde_json::Value>({value}).unwrap_or_default()"
                if self.target == "java":
                    return value
                if self.target == "cpp":
                    return value
        if module == "sys" and method == "exit":
            code = args[0] if args else "0"
            if self.target in {"javascript", "typescript"}:
                return f"process.exit({code})"
            if self.target == "java":
                return f"System.exit({code})"
            if self.target == "csharp":
                return f"Environment.Exit({code})"
            if self.target == "cpp":
                return f"std::exit({code})"
            if self.target == "rust":
                return f"std::process::exit({code} as i32)"
            if self.target == "go":
                self.go_imports.add("os")
                return f"os.Exit({code})"
            if self.target == "php":
                return f"exit({code})"
        rendered = self._render_args(args, keywords)
        return f"{self.expr(node.func)}({rendered})"

    def _math_call(self, method: str, args: list[str]) -> str:
        rendered = ", ".join(args)
        first = args[0] if args else "0"
        mapping = {
            "sqrt": {"javascript": f"Math.sqrt({rendered})", "typescript": f"Math.sqrt({rendered})",
                     "java": f"Math.sqrt({rendered})", "csharp": f"Math.Sqrt({rendered})",
                     "cpp": f"std::sqrt({rendered})", "rust": f"({first} as f64).sqrt()",
                     "go": f"math.Sqrt({rendered})", "php": f"sqrt({rendered})"},
            "ceil": {"javascript": f"Math.ceil({rendered})", "typescript": f"Math.ceil({rendered})",
                     "java": f"Math.ceil({rendered})", "csharp": f"Math.Ceiling({rendered})",
                     "cpp": f"std::ceil({rendered})", "rust": f"({first} as f64).ceil()",
                     "go": f"math.Ceil({rendered})", "php": f"ceil({rendered})"},
            "floor": {"javascript": f"Math.floor({rendered})", "typescript": f"Math.floor({rendered})",
                      "java": f"Math.floor({rendered})", "csharp": f"Math.Floor({rendered})",
                      "cpp": f"std::floor({rendered})", "rust": f"({first} as f64).floor()",
                      "go": f"math.Floor({rendered})", "php": f"floor({rendered})"},
            "pow": {"javascript": f"Math.pow({rendered})", "typescript": f"Math.pow({rendered})",
                    "java": f"Math.pow({rendered})", "csharp": f"Math.Pow({rendered})",
                    "cpp": f"std::pow({rendered})", "rust": f"({first} as f64).powf({args[1] if len(args) > 1 else '0.0'} as f64)",
                    "go": f"math.Pow({rendered})", "php": f"pow({rendered})"},
            "log": {"javascript": f"Math.log({rendered})", "typescript": f"Math.log({rendered})",
                    "java": f"Math.log({rendered})", "csharp": f"Math.Log({rendered})",
                    "cpp": f"std::log({rendered})", "rust": f"({first} as f64).ln()",
                    "go": f"math.Log({rendered})", "php": f"log({rendered})"},
            "sin": {"javascript": f"Math.sin({rendered})", "typescript": f"Math.sin({rendered})",
                    "java": f"Math.sin({rendered})", "csharp": f"Math.Sin({rendered})",
                    "cpp": f"std::sin({rendered})", "rust": f"({first} as f64).sin()",
                    "go": f"math.Sin({rendered})", "php": f"sin({rendered})"},
            "cos": {"javascript": f"Math.cos({rendered})", "typescript": f"Math.cos({rendered})",
                    "java": f"Math.cos({rendered})", "csharp": f"Math.Cos({rendered})",
                    "cpp": f"std::cos({rendered})", "rust": f"({first} as f64).cos()",
                    "go": f"math.Cos({rendered})", "php": f"cos({rendered})"},
            "tan": {"javascript": f"Math.tan({rendered})", "typescript": f"Math.tan({rendered})",
                    "java": f"Math.tan({rendered})", "csharp": f"Math.Tan({rendered})",
                    "cpp": f"std::tan({rendered})", "rust": f"({first} as f64).tan()",
                    "go": f"math.Tan({rendered})", "php": f"tan({rendered})"},
        }
        result = mapping.get(method, {}).get(self.target)
        if result:
            if self.target == "go":
                self.go_imports.add("math")
            return result
        self.warn(f"math.{method} was emitted as a direct target call.")
        return f"{method}({rendered})"

    # -- isinstance --------------------------------------------------
    def _isinstance(self, value_node: ast.AST, type_node: ast.AST) -> str:
        value = self.expr(value_node)
        names = []
        if isinstance(type_node, ast.Tuple):
            names = [self._as_name(item) or "" for item in type_node.elts]
        else:
            name = self._as_name(type_node)
            names = [name or ""]

        checks = [self._isinstance_one(value, name) for name in names if name]
        if len(checks) == 1:
            return checks[0]
        return " || ".join(checks) if checks else self.bool_literal(False)

    def _isinstance_one(self, value: str, name: str) -> str:
        name = name.lower()
        if self.target in {"javascript", "typescript"}:
            if name in {"int", "float", "complex"}:
                return f"typeof {value} === \"number\""
            if name == "str":
                return f"typeof {value} === \"string\""
            if name == "bool":
                return f"typeof {value} === \"boolean\""
            if name in {"list", "tuple", "set"}:
                return f"Array.isArray({value})"
            if name == "dict":
                return f"({value} !== null && typeof {value} === \"object\" && !Array.isArray({value}))"
            return f"({value} instanceof {name})"
        if self.target == "java":
            mapping = {
                "int": f"{value} instanceof Number", "float": f"{value} instanceof Number",
                "bool": f"{value} instanceof Boolean", "str": f"{value} instanceof String",
                "list": f"{value} instanceof java.util.List", "tuple": f"{value} instanceof java.util.List",
                "dict": f"{value} instanceof java.util.Map", "set": f"{value} instanceof java.util.Set",
            }
            return mapping.get(name, f"{value} instanceof {name}")
        if self.target == "csharp":
            mapping = {
                "int": f"{value} is int or long", "float": f"{value} is double or float",
                "bool": f"{value} is bool", "str": f"{value} is string",
                "list": f"{value} is System.Collections.IList", "tuple": f"{value} is System.Collections.IList",
                "dict": f"{value} is System.Collections.IDictionary", "set": f"{value} is System.Collections.IEnumerable",
            }
            return mapping.get(name, f"{value} is {name}")
        if self.target == "cpp":
            cpp = {"int": "int", "float": "double", "bool": "bool", "str": "std::string"}
            type_name = cpp.get(name, name)
            return f"std::is_same_v<std::decay_t<decltype({value})>, {type_name}>"
        if self.target == "rust":
            rust = {"int": "i64", "float": "f64", "bool": "bool", "str": "String"}
            type_name = rust.get(name, name)
            return f"{value}.is::<{type_name}>()"
        if self.target == "go":
            go = {"int": "int", "float": "float64", "bool": "bool", "str": "string"}
            type_name = go.get(name, "any")
            return f"func() bool {{ _, ok := {value}.({type_name}); return ok }}()"
        if self.target == "php":
            mapping = {
                "int": f"is_int({value})", "float": f"is_float({value})",
                "bool": f"is_bool({value})", "str": f"is_string({value})",
                "list": f"is_array({value})", "tuple": f"is_array({value})",
                "dict": f"is_array({value})", "set": f"is_array({value})",
            }
            return mapping.get(name, f"is_a({value}, {self.string(name)})")
        return f"isinstance({value}, {name})"
