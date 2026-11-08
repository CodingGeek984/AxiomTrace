"""Parser tests - including the graceful recovery path.

The parser is the piece the user touches most often, so the contract is:

* valid source produces a graph with nodes, edges and accurate line numbers,
* invalid source does **not** raise: the broken statement is quarantined and
  reported as a diagnostic,
* the language detector picks the right extractor.
"""

from __future__ import annotations

import pytest

from config import config
from services.ast_service import ASTParserService
from services.parse_recovery import recover_python


class TestPythonParsing:
    def test_function_and_body_become_nodes(self, parser):
        graph = parser.parse_code("def main(x):\n    return x * 2\n")
        assert graph["language"] == "python"
        assert graph["errors"] == []
        assert graph["partial"] is False
        assert graph["stats"]["functions"] == 1
        assert graph["stats"]["actions"] >= 1
        assert graph["nodes"][0]["line"] == 1
        assert graph["nodes"][0]["end_line"] == 2

    def test_loop_and_condition_are_classified(self, parser):
        graph = parser.parse_code(
            "def run(items):\n"
            "    for item in items:\n"
            "        if item:\n"
            "            print(item)\n"
        )
        kinds = {node["type"] for node in graph["nodes"]}
        assert "loop" in kinds
        assert "condition" in kinds

    def test_async_functions_are_flagged(self, parser):
        graph = parser.parse_code("async def fetch():\n    return 1\n")
        assert any(node["async"] for node in graph["nodes"])

    def test_edges_connect_the_graph(self, parser):
        graph = parser.parse_code("def a():\n    return 1\n\ndef b():\n    return 2\n")
        assert graph["edges"]
        node_ids = {node["id"] for node in graph["nodes"]}
        for edge in graph["edges"]:
            assert edge["source"] in node_ids
            assert edge["target"] in node_ids

    def test_empty_source_returns_an_empty_graph(self, parser):
        graph = parser.parse_code("   \n  ")
        assert graph["nodes"] == []
        assert graph["errors"] == []

    def test_node_ids_are_stable(self, parser):
        first = parser.parse_code("def a():\n    return 1\n")
        second = parser.parse_code("def a():\n    return 1\n")
        assert [node["id"] for node in first["nodes"]] == [
            node["id"] for node in second["nodes"]
        ]


class TestGracefulRecovery:
    def test_broken_statement_is_quarantined_not_fatal(self, parser):
        graph = parser.parse_code("def main(x):\n    return x * 2\n\nx = )\n")
        assert graph["partial"] is True
        assert graph["errors"]
        assert graph["stats"]["functions"] == 1
        assert graph["stats"]["diagnostics"] >= 1

    def test_error_reports_the_offending_line(self, parser):
        graph = parser.parse_code("a = 1\nb = 2\nc = )\nd = 4\n")
        assert graph["errors"][0]["start_line"] == 3

    def test_valid_statements_around_the_broken_one_survive(self, parser):
        graph = parser.parse_code("a = 1\nb = )\nc = 3\n")
        labels = [node["label"] for node in graph["nodes"]]
        assert any("a" in label for label in labels)
        assert any("c" in label for label in labels)

    def test_diagnostic_node_marks_the_error(self, parser):
        graph = parser.parse_code("def f():\n    pass\n\nx = )\n")
        diagnostics = [
            node for node in graph["nodes"] if node["data"].get("diagnostic")
        ]
        assert diagnostics
        assert diagnostics[0]["data"]["status"] == "error"
        assert diagnostics[0]["data"]["error"]

    def test_diagnostics_do_not_overlap_the_code_nodes(self, parser):
        graph = parser.parse_code("def f():\n    pass\n\nx = )\n")
        code_nodes = [node for node in graph["nodes"] if not node["data"].get("diagnostic")]
        diagnostic_nodes = [
            node for node in graph["nodes"] if node["data"].get("diagnostic")
        ]
        assert code_nodes and diagnostic_nodes
        assert diagnostic_nodes[0]["x"] > max(node["x"] for node in code_nodes)

    def test_recovery_preserves_the_line_count(self):
        source = "a = 1\nb = )\nc = 3\n"
        result = recover_python(source)
        assert result.recovered is True
        assert len(result.code.splitlines()) == len(source.splitlines())

    def test_recovery_reports_unrecoverable_input(self):
        result = recover_python("\x00\x01 not python at all \x02")
        assert isinstance(result.recovered, bool)

    def test_recovery_can_be_disabled(self, monkeypatch, parser):
        monkeypatch.setattr(config, "PARSE_PARTIAL_RECOVERY", False, raising=False)
        with pytest.raises(SyntaxError):
            parser.parse_code("def f():\n    pass\n\nx = )\n")


class TestJavaScriptParsing:
    def test_functions_are_detected(self, parser):
        graph = parser.parse_code(
            "function add(a, b) {\n  return a + b;\n}\nconst x = add(1, 2);\n"
        )
        assert graph["language"] in {"javascript", "typescript"}
        assert graph["stats"]["functions"] >= 1

    def test_unbalanced_braces_produce_a_diagnostic(self, parser):
        graph = parser.parse_code("const x = {\n  a: 1;\n")
        assert graph["partial"] is True
        assert graph["errors"]

    def test_class_method_modifiers_do_not_replace_the_method_name(self, parser):
        graph = parser.parse_code(
            "class Store {\n"
            "  async load(id) {\n"
            "    return id;\n"
            "  }\n"
            "  static parse(input) {\n"
            "    return input;\n"
            "  }\n"
            "  get value() {\n"
            "    return 1;\n"
            "  }\n"
            "  constructor() {\n"
            "    this.ready = true;\n"
            "  }\n"
            "}\n"
        )
        methods = {
            node["label"] for node in graph["nodes"] if node["kind"] == "method"
        }
        assert methods == {
            "async load()",
            "parse()",
            "value()",
            "constructor()",
        }

    def test_non_terminal_branches_rejoin_the_following_statement(self, parser):
        graph = parser.parse_code(
            "function run(flag) {\n"
            "  if (flag) {\n"
            "    yes();\n"
            "  } else {\n"
            "    no();\n"
            "  }\n"
            "  after();\n"
            "}\n"
        )
        by_label = {node["label"]: node["id"] for node in graph["nodes"]}
        pairs = {(edge["source"], edge["target"]) for edge in graph["edges"]}
        assert (by_label["call yes(...)"], by_label["call after(...)"]) in pairs
        assert (by_label["call no(...)"], by_label["call after(...)"]) in pairs

    def test_terminal_branches_do_not_fall_through(self, parser):
        graph = parser.parse_code(
            "function run(flag) {\n"
            "  if (flag) {\n"
            "    return 1;\n"
            "  } else {\n"
            "    throw new Error('no');\n"
            "  }\n"
            "  unreachable();\n"
            "}\n"
        )
        by_label = {node["label"]: node["id"] for node in graph["nodes"]}
        targets = {edge["target"] for edge in graph["edges"]}
        assert by_label["call unreachable(...)"] not in targets


class TestLanguageDetection:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ("def main():\n    return 1\n", "python"),
            ("import os\nprint(os.getcwd())\n", "python"),
            ("const x = 1;\nconsole.log(x);\n", "javascript"),
            ("function f() { return 1; }\n", "javascript"),
            ("interface Foo { bar: string }\n", "typescript"),
        ],
    )
    def test_detection(self, parser, source, expected):
        assert parser.detect_language(source) == expected


class TestServiceFacade:
    def test_explicit_language_overrides_detection(self, parser):
        graph = parser.parse_code("def f():\n    return 1\n", language="python")
        assert graph["language"] == "python"

    def test_parse_files_merges_graphs(self, parser):
        graph = parser.parse_files(
            [
                ("a.py", "def a():\n    return 1\n"),
                ("b.py", "def b():\n    return 2\n"),
            ]
        )
        assert graph["stats"]["files"] == 2
        assert len(graph["nodes"]) >= 2
        ids = [node["id"] for node in graph["nodes"]]
        assert len(ids) == len(set(ids))
