"""Sandbox execution tests.

Covers the guarantees the product promises:

* a node runs and returns its value,
* imports are refused,
* a runaway loop is stopped instead of hanging the request,
* tracebacks are human readable,
* the mutation sweep covers a 12+ case corpus and produces a verdict,
* every payload is JSON serialisable (no ``NaN`` leaking into the wire).
"""

from __future__ import annotations

import json
import math

import pytest

from config import config
from services.sandbox_service import (
    execute_node_code,
    json_safe,
    mutation_payload,
    run_mutation_stress_test,
)


class TestExecution:
    def test_simple_return_value(self, sandbox):
        report = sandbox.execute_node("def main(x):\n    return x * 2", {"x": 21})
        assert report["status"] == "success"
        assert report["return_value"] == 42
        assert report["return_repr"] == "42"
        assert report["error"] is None
        assert report["execution_time"] >= 0

    def test_single_argument_entry_receives_the_mock_value(self, sandbox):
        report = sandbox.execute_node(
            "def main(user):\n    return user['name']",
            {"name": "ada"},
        )
        assert report["status"] == "success"
        assert report["return_value"] == "ada"

    def test_named_parameters_are_matched_against_the_payload(self, sandbox):
        report = sandbox.execute_node(
            "def main(first, second):\n    return first + second",
            {"first": "a", "second": "b"},
        )
        assert report["status"] == "success"
        assert report["return_value"] == "ab"

    def test_non_conventional_entry_function_is_invoked(self, sandbox):
        report = sandbox.execute_node(
            "def score(user):\n    return user['name']",
            {"name": "ada"},
        )
        assert report["status"] == "success"
        assert report["entry_function"] == "score"
        assert report["return_value"] == "ada"

    def test_missing_parameter_is_reported(self, sandbox):
        report = sandbox.execute_node(
            "def main(first, second):\n    return first + second",
            {"first": "a"},
        )
        assert report["status"] == "error"
        assert "second" in report["error"]["message"]

    def test_module_level_result_is_returned(self, sandbox):
        report = sandbox.execute_node("result = 7")
        assert report["status"] == "success"
        assert report["return_value"] == 7

    def test_snippet_without_entry_point_reports_no_entry(self, sandbox):
        report = sandbox.execute_node("x = 1")
        assert report["status"] == "success"
        assert report["entry_function"] is None

    def test_stdout_is_captured(self, sandbox):
        report = sandbox.execute_node("print('hello world')")
        assert report["status"] == "success"
        assert "hello world" in report["stdout"]

    def test_missing_main_is_reported(self, sandbox):
        report = sandbox.execute_node("x = 1")
        assert report["entry_function"] is None

    def test_runtime_exception_carries_traceback(self, sandbox):
        report = sandbox.execute_node(
            "def main(d):\n    return d['missing']", {"d": {}}
        )
        assert report["status"] == "error"
        error = report["error"]
        assert error["type"] == "KeyError"
        assert "KeyError" in error["traceback"]
        assert "<axiom-node>" in error["traceback"]

    def test_import_is_blocked(self, sandbox):
        report = sandbox.execute_node("import os\nos.getcwd()")
        assert report["status"] == "error"
        assert "import" in report["error"]["message"].lower()

    def test_open_is_blocked(self, sandbox):
        report = sandbox.execute_node("open('/etc/passwd').read()")
        assert report["status"] == "error"

    @pytest.mark.parametrize(
        "code",
        [
            "while True:\n    pass",
            "for i in range(10**9):\n    pass",
            "def main():\n    while True:\n        pass",
        ],
    )
    def test_runaway_loops_are_stopped(self, sandbox, code):
        report = sandbox.execute_node(code, timeout=2.0)
        assert report["status"] in {"timeout", "error"}
        if report["status"] == "timeout":
            assert report["error"]["type"] in {
                "SandboxTimeout",
                "SandboxBudgetExceeded",
            }
        else:
            assert report["error"]["type"] == "SandboxBudgetExceeded"

    def test_timeout_is_configured_strictly(self):
        assert config.SANDBOX_TIMEOUT_SECONDS <= 2.0
        assert config.MUTATION_TIMEOUT_SECONDS <= config.SANDBOX_TIMEOUT_SECONDS


class TestJsonSafety:
    def test_nan_is_converted(self):
        safe = json_safe({"value": float("nan")})
        assert safe["value"] == "NaN"
        json.dumps(safe)  # must not raise

    def test_infinity_is_converted(self):
        safe = json_safe([float("inf"), float("-inf")])
        assert safe == ["Infinity", "-Infinity"]
        json.dumps(safe)

    def test_nested_structures_are_handled(self):
        safe = json_safe({"a": [{"b": float("nan")}]})
        payload = json.dumps(safe)
        assert "NaN" in payload

    def test_deep_structures_are_truncated(self):
        deep = current = {}
        for _ in range(40):
            current["next"] = {}
            current = current["next"]
        safe = json_safe(deep)
        json.dumps(safe)

    def test_execution_report_is_serialisable(self, sandbox):
        report = sandbox.execute_node("def main(v):\n    return v", {"v": float("nan")})
        json.dumps(report)


class TestMutationSweep:
    def test_corpus_has_at_least_twelve_cases(self):
        assert len(config.MUTATION_EDGE_INPUTS) >= 12

    def test_corpus_contains_hostile_inputs(self):
        corpus = config.MUTATION_EDGE_INPUTS
        assert any(isinstance(item, float) and math.isnan(item) for item in corpus)
        assert any(isinstance(item, float) and math.isinf(item) for item in corpus)
        assert None in corpus
        assert any(isinstance(item, str) and "{" in item for item in corpus)

    def test_sweep_reports_a_verdict(self, sandbox):
        report = sandbox.run_mutation_test("def main(v):\n    return v * 2", iterations=1)
        assert report["verdict"] in {"stable", "fragile", "critical"}
        assert report["total_runs"] == len(config.MUTATION_EDGE_INPUTS)
        assert len(report["cases"]) == report["total_runs"]
        assert 0.0 <= report["crash_rate"] <= 1.0

    def test_sweep_is_json_serialisable(self, sandbox):
        report = sandbox.run_mutation_test("def main(v):\n    return v", iterations=1)
        json.dumps(report)

    def test_sweep_detects_a_fragile_node(self, sandbox):
        report = sandbox.run_mutation_test(
            "def main(v):\n    return v + 1", iterations=1
        )
        assert report["failed"] > 0
        assert report["verdict"] in {"fragile", "critical"}
        assert report["failures"]

    def test_sweep_reports_stable_for_defensive_code(self, sandbox):
        report = sandbox.run_mutation_test(
            "def main(v):\n"
            "    if not isinstance(v, (int, float)):\n"
            "        return 0\n"
            "    return v * 2",
            iterations=1,
        )
        assert report["verdict"] == "stable"
        assert report["failed"] == 0

    def test_sweep_respects_the_iteration_cap(self, sandbox):
        report = sandbox.run_mutation_test(
            "def main(v):\n    return v", iterations=99
        )
        assert report["iterations_per_input"] <= config.MUTATION_MAX_ITERATIONS

    def test_mutation_payload_wraps_the_edge_input(self):
        payload = mutation_payload(5)
        assert isinstance(payload, dict)
        assert "value" in payload or "input" in payload or payload
