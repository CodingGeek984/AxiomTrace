"""Polyglot transpiler tests - all nine targets, fragments and fallbacks."""

from __future__ import annotations

import pytest

from services.transpiler.base import prepare_source
from services.transpiler.python_emitter import emit_source
from services.transpiler_service import (
    SUPPORTED_LANGUAGES,
    TranspilationError,
    TranspilerService,
    normalize_language,
)

FUNCTION_SOURCE = """\
_TAX = 0.2


def calculate_total(items: list) -> float:
    total = 0.0
    for item in items:
        total += item
    return round(total * (1 + _TAX), 2)
"""

CLASS_SOURCE = """\
class BankAccount:
    def __init__(self, owner: str, balance: float = 0.0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount
"""

LOOP_SOURCE = """\
total = 0.0
for value in [1, 2, 3, 4]:
    if value % 2 == 0:
        total += value * 1.5
    else:
        total -= value
"""

SAMPLES = {
    "function": FUNCTION_SOURCE,
    "class": CLASS_SOURCE,
    "loop": LOOP_SOURCE,
    "fragment_return": "return str(total)\n",
}


def _emit(code: str, target: str) -> str:
    output, _warnings = emit_source(prepare_source(code), target)
    return output


class TestAllTargets:
    @pytest.mark.parametrize("target", SUPPORTED_LANGUAGES)
    def test_every_target_produces_code(self, target):
        for code in SAMPLES.values():
            output = _emit(code, target)
            assert output.strip(), f"{target} produced no output for {code!r}"

    @pytest.mark.parametrize("target", SUPPORTED_LANGUAGES)
    def test_no_placeholders(self, target):
        for code in SAMPLES.values():
            output = _emit(code, target)
            assert "// TODO" not in output
            assert "# TODO" not in output
            assert "TODO:" not in output
            # Ellipses are legitimate in Go (...T variadics); a bare
            # placeholder line is not.
            assert "\n...\n" not in output
            assert not any(
                line.strip() == "..." for line in output.splitlines()
            )

    def test_idiomatic_markers(self):
        java = _emit(FUNCTION_SOURCE, "java")
        assert "ArrayList" in java or "List<" in java
        cpp = _emit(FUNCTION_SOURCE, "cpp")
        assert "std::vector" in cpp
        rust = _emit(FUNCTION_SOURCE, "rust")
        assert "Vec<" in rust
        go = _emit(CLASS_SOURCE, "go")
        assert "package main" in go
        php = _emit(CLASS_SOURCE, "php")
        assert php.lstrip().startswith("<?php")

    def test_mutable_loop_variable_not_const(self):
        for target in ("javascript", "typescript"):
            output = _emit(LOOP_SOURCE, target)
            assert "const total" not in output
            assert "let total" in output
        rust = _emit(LOOP_SOURCE, "rust")
        assert "let mut total" in rust
        php = _emit(LOOP_SOURCE, "php")
        assert 'define("total"' not in php
        assert "$total" in php

    def test_true_constant_stays_const(self):
        java = _emit(FUNCTION_SOURCE, "java")
        assert "static final" in java
        js = _emit(FUNCTION_SOURCE, "javascript")
        assert "const _TAX" in js

    def test_python_roundtrip(self):
        output = _emit(FUNCTION_SOURCE, "python")
        assert output.strip() == FUNCTION_SOURCE.strip()

    def test_fragment_return_wrapped(self):
        for target in SUPPORTED_LANGUAGES:
            output = _emit("return str(total)\n", target)
            assert output.strip(), target


class TestFacade:
    def test_aliases_normalised(self):
        assert normalize_language("js") == "javascript"
        assert normalize_language("golang") == "go"
        assert normalize_language("c++") == "cpp"
        assert normalize_language("py") == "python"
        assert normalize_language("bogus", default="typescript") == "typescript"

    def test_transpile_node_success(self):
        result = TranspilerService().transpile_node(
            FUNCTION_SOURCE, "python", "rust"
        )
        assert result.target_language == "rust"
        assert result.translated_code.strip()
        assert "fn " in result.translated_code

    def test_syntax_error_returns_fallback_not_exception(self):
        result = TranspilerService().transpile_node(
            "def broken(:\n    pass\n", "python", "go"
        )
        assert result.fallback_reason is not None
        assert "syntax" in result.fallback_reason.lower()
        assert result.translated_code.strip()

    def test_empty_source_raises(self):
        with pytest.raises(TranspilationError):
            TranspilerService().transpile_node("   ", "python", "go")

    def test_unsupported_target_falls_back_to_default(self):
        # Unknown ids degrade to the default target instead of raising, so
        # the UI never shows a server error for a stale selector value.
        result = TranspilerService().transpile_node(
            FUNCTION_SOURCE, "python", "kotlin"
        )
        assert result.target_language == "typescript"
        assert result.translated_code.strip()

    def test_unsupported_source_degrades_to_python(self):
        result = TranspilerService().transpile_node("x = 1\n", "kotlin", "go")
        assert result.source_language == "python"
        assert result.target_language == "go"
        assert result.translated_code.strip()


class TestEndpoint:
    # The envelope spreads dict payloads at the root (no ``data`` key).
    def test_transpile_endpoint_success(self, client):
        response = client.post(
            "/api/v1/transpile",
            json={"code": FUNCTION_SOURCE, "target_language": "java"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["target_language"] == "java"
        assert payload["translated_code"].strip()
        assert payload["fallback_reason"] is None

    def test_transpile_endpoint_syntax_error_is_200(self, client):
        response = client.post(
            "/api/v1/transpile",
            json={"code": "def broken(:\n", "target_language": "rust"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["translated_code"].strip()
        assert payload["fallback_reason"] is not None

    def test_languages_endpoint(self, client):
        payload = client.get("/api/v1/transpile/languages").get_json()
        assert set(payload["languages"]) == set(SUPPORTED_LANGUAGES)
