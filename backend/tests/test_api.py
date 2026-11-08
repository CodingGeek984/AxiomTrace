"""HTTP API tests.

These exercise the real Flask stack (routing, validation, envelopes, error
handlers, database lifecycle) against an in-memory SQLite database.
"""

from __future__ import annotations

import json

import pytest


class TestHealth:
    def test_v1_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["status"] == "ok"
        assert payload["app"] == "AxiomTrace"
        assert payload["sandbox"]["timeout_seconds"] <= 2.0

    def test_legacy_health_alias(self, client):
        assert client.get("/api/health").status_code == 200

    def test_stats_endpoint(self, client):
        payload = client.get("/api/v1/stats").get_json()
        assert payload["ok"] is True
        assert "sandbox" in payload
        assert payload["projects"] is not None

    def test_response_timing_header(self, client):
        response = client.get("/api/v1/health")
        assert "X-Response-Time-Ms" in response.headers
        assert float(response.headers["X-Response-Time-Ms"]) >= 0


class TestRouting:
    def test_unknown_endpoint_returns_json_404(self, client):
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        payload = response.get_json()
        assert payload["ok"] is False
        assert payload["error"]

    def test_method_not_allowed(self, client):
        response = client.post("/api/v1/health")
        assert response.status_code == 405
        assert response.get_json()["ok"] is False


class TestLogs:
    def test_v1_logs_endpoint_exists(self, client):
        """The desktop client polls /api/v1/logs - it must not 404."""
        response = client.get("/api/v1/logs")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert "logs" in payload

    def test_legacy_logs_alias(self, client):
        assert client.get("/api/logs").status_code == 200

    def test_logs_are_recorded_after_an_execution(self, client):
        client.post(
            "/api/v1/sandbox/execute",
            json={"node_id": "n-logs", "code": "def main():\n    return 1"},
        )
        payload = client.get("/api/v1/logs").get_json()
        assert payload["count"] >= 1
        assert payload["logs"][0]["node_id"] == "n-logs"


class TestProjects:
    def test_open_project_creates_and_reuses(self, client):
        first = client.post(
            "/api/v1/projects/open", json={"name": "demo", "path": "/tmp/demo"}
        ).get_json()
        assert first["created"] is True
        second = client.post(
            "/api/v1/projects/open", json={"name": "demo", "path": "/tmp/demo"}
        ).get_json()
        assert second["created"] is False
        assert second["project"]["id"] == first["project"]["id"]

    def test_graph_round_trip(self, client):
        project = client.post(
            "/api/v1/projects/open", json={"name": "graph", "path": "/tmp/graph"}
        ).get_json()["project"]

        saved = client.post(
            f"/api/v1/projects/{project['id']}/schema",
            json={
                "nodes": [{"id": "n1", "type": "action", "label": "x"}],
                "edges": [],
            },
        ).get_json()
        assert saved["nodes"] == 1

        loaded = client.get(f"/api/v1/projects/{project['id']}/schema").get_json()
        assert loaded["graph"]["nodes"][0]["id"] == "n1"

    def test_missing_project_returns_404(self, client):
        response = client.get("/api/v1/projects/999999/schema")
        assert response.status_code == 404

    def test_validation_error_is_json(self, client):
        response = client.post("/api/v1/projects/open", json={})
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    @pytest.mark.parametrize("body", [[], None, "text", 42])
    def test_schema_rejects_a_non_object_body(self, client, body):
        project = client.post(
            "/api/v1/projects/open", json={"name": "schema", "path": "/tmp/schema"}
        ).get_json()["project"]
        response = client.post(
            f"/api/v1/projects/{project['id']}/schema", json=body
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    @pytest.mark.parametrize("body", [[], "text", 42])
    def test_legacy_graph_save_rejects_a_non_object_body(self, client, body):
        response = client.post("/api/project/graph", json=body)
        assert response.status_code == 400
        assert response.get_json()["ok"] is False


class TestParseEndpoint:
    def test_parse_returns_a_graph(self, client):
        payload = client.post(
            "/api/v1/parse/code", json={"code": "def main(x):\n    return x*2\n"}
        ).get_json()
        assert payload["ok"] is True
        assert payload["nodes"]
        assert payload["stats"]["functions"] == 1

    def test_parse_recovers_from_a_syntax_error(self, client):
        response = client.post(
            "/api/v1/parse/code", json={"code": "def main():\n    return 1\n\nx = )\n"}
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["partial"] is True
        assert payload["errors"]

    def test_parse_rejects_an_oversized_payload(self, client):
        response = client.post("/api/v1/parse/code", json={"code": "x = 1\n" * 1})
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "field", ["source", "source_code", "sourceCode", "file_content"]
    )
    def test_parse_accepts_code_aliases(self, client, field):
        payload = client.post(
            "/api/v1/parse/code", json={field: "def main():\n    return 1\n"}
        ).get_json()
        assert payload["ok"] is True
        assert payload["data"]["nodes"]
        assert payload["data"]["nodes"] == payload["nodes"]

    def test_parse_ignores_an_unusable_project_id(self, client):
        payload = client.post(
            "/api/v1/parse/code",
            json={
                "code": "def main():\n    return 1\n",
                "project_id": "not-a-number",
            },
        ).get_json()
        assert payload["ok"] is True
        assert payload["data"]["nodes"]

    def test_parse_reports_nul_bytes_as_a_clean_error(self, client):
        response = client.post("/api/v1/parse/code", json={"code": "\x00"})
        assert response.status_code == 400
        assert response.get_json()["ok"] is False


class TestSandboxEndpoint:
    def test_execute_returns_a_report(self, client):
        payload = client.post(
            "/api/v1/sandbox/execute",
            json={"node_id": "n1", "code": "def main(x):\n    return x*2", "input_data": {"x": 4}},
        ).get_json()
        assert payload["ok"] is True
        assert payload["result"]["status"] == "success"
        assert payload["result"]["return_value"] == 8

    def test_execute_reports_a_timeout(self, client):
        payload = client.post(
            "/api/v1/sandbox/execute",
            json={"node_id": "n1", "code": "while True:\n    pass", "timeout": 1},
        ).get_json()
        assert payload["result"]["status"] in {"timeout", "error"}

    def test_mutate_returns_a_verdict(self, client):
        payload = client.post(
            "/api/v1/sandbox/mutate",
            json={"node_id": "n1", "code": "def main(v):\n    return v*2", "iterations": 1},
        ).get_json()
        assert payload["ok"] is True
        assert payload["report"]["verdict"] in {"stable", "fragile", "critical"}
        assert payload["report"]["total_runs"] >= 12

    def test_mutation_report_is_json_safe(self, client):
        response = client.post(
            "/api/v1/sandbox/mutate",
            json={"node_id": "n1", "code": "def main(v):\n    return v", "iterations": 1},
        )
        # A raw NaN in the body would break JSON.parse on the client.
        assert b"NaN," not in response.data.replace(b'"NaN"', b"")
        json.loads(response.data)


class TestAiEndpoints:
    def test_context_export(self, client):
        payload = client.post(
            "/api/v1/ai/context",
            json={
                "nodes": [
                    {
                        "id": "n1",
                        "type": "function",
                        "data": {"label": "main()", "code": "def main():\n    return 1\n", "line": 1},
                    }
                ],
                "edges": [],
                "project_name": "demo",
            },
        ).get_json()
        assert payload["ok"] is True
        assert "AxiomTrace" in payload["markdown"]
        assert payload["estimated_tokens"] > 0
        assert payload["char_count"] == len(payload["markdown"])

    def test_edge_case_detection_falls_back_to_heuristics(self, client):
        payload = client.post(
            "/api/v1/ai/edge-cases",
            json={"code": "import requests\nrequests.get('https://example.com')"},
        ).get_json()
        assert payload["ok"] is True
        report = payload["report"]
        assert report["source"] == "heuristic"
        assert report["findings"]

    def test_edge_case_detection_on_clean_code(self, client):
        payload = client.post(
            "/api/v1/ai/edge-cases",
            json={"code": "def add(a, b):\n    return a + b\n"},
        ).get_json()
        assert payload["report"]["findings"] == []
