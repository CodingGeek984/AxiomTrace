from __future__ import annotations


def test_parse_architecture_prompt_stitches_nodes_and_edges(client):
    response = client.post(
        "/api/v1/parse/architecture",
        json={
            "prompt": "API Gateway routes requests to the Auth Service. The Auth Service writes to the User Database."
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    data = payload
    assert data["source"] == "prompt"
    assert data["language"] == "text"
    assert len(data["nodes"]) >= 3
    assert len(data["edges"]) >= 2
    tags = {node["tag"] for node in data["nodes"]}
    assert {"api", "service", "database"} <= tags
    for node in data["nodes"]:
        assert node["tag"] in {"service", "database", "api", "function"}
        assert node["data"]["tag"] == node["tag"]
    for edge in data["edges"]:
        assert edge["animated"] is True
        assert edge["kind"] in {"call", "data_flow", "api_request"}


def test_parse_architecture_code_context(client):
    response = client.post(
        "/api/v1/parse/architecture",
        json={
            "code_context": """
import requests
class OrderService:
    def place(self):
        requests.post('https://api.internal/pay')
        db.save(self)
""",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    data = payload
    tags = {node["tag"] for node in data["nodes"]}
    assert "service" in tags
    assert any(node["tag"] in {"api", "database"} for node in data["nodes"])


def test_parse_architecture_stitches_against_current_graph(client):
    base = client.post(
        "/api/v1/parse/architecture",
        json={"prompt": "API Gateway -> Auth Service"},
    ).get_json()
    response = client.post(
        "/api/v1/parse/architecture",
        json={
            "prompt": "Add a Notification Service",
            "current_graph": {"nodes": base["nodes"], "edges": base["edges"]},
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    ids = [node["id"] for node in data["nodes"]]
    assert any("notification" in node_id for node_id in ids)
    assert data["stats"]["linked"] >= 1


def test_parse_architecture_legacy_alias(client):
    response = client.post(
        "/api/parse/architecture",
        json={"prompt": "Web App -> API -> Database"},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_parse_architecture_requires_prompt_or_code_context(client):
    response = client.post("/api/v1/parse/architecture", json={})
    assert response.status_code == 400


def test_parse_architecture_empty_string_rejected(client):
    response = client.post("/api/v1/parse/architecture", json={"prompt": "   "})
    assert response.status_code == 400


def test_parse_architecture_too_large(client):
    big = "Service A -> Service B\n" * 8000
    response = client.post("/api/v1/parse/architecture", json={"prompt": big})
    assert response.status_code == 400


def test_default_auto_architect_blueprint_is_connected(client):
    prompt = "Frontend App -> API Gateway -> Flask Controller -> Database"
    response = client.post("/api/v1/parse/architecture", json={"prompt": prompt})
    assert response.status_code == 200
    data = response.get_json()
    labels = {node["label"] for node in data["nodes"]}
    assert {"Frontend App", "API Gateway", "Flask Controller", "Database"} <= labels
    assert len(data["edges"]) >= 3
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["edges"])


def test_auto_architect_is_idempotent_with_current_graph(client):
    prompt = "Frontend App -> API Gateway -> Flask Controller -> Database"
    first = client.post("/api/v1/parse/architecture", json={"prompt": prompt}).get_json()
    second_response = client.post(
        "/api/v1/parse/architecture",
        json={
            "prompt": prompt,
            "current_graph": {"nodes": first["nodes"], "edges": first["edges"]},
        },
    )
    assert second_response.status_code == 200
    second = second_response.get_json()
    assert second["nodes"] == []
    assert second["edges"] == []
    assert second["stats"]["linked"] == 0
