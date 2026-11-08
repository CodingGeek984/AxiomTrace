"""Security scanner tests - secrets, dynamic execution, SQL and auth."""

from __future__ import annotations

from services.security_scanner import (
    BOLA_RISK,
    HARDCODED_SECRET,
    MISSING_AUTH,
    NOSQL_INJECTION_RISK,
    RACE_CONDITION_RISK,
    SQL_INJECTION_RISK,
    SSRF_RISK,
    UNSAFE_CODE_EXECUTION,
    UNSAFE_DESERIALIZATION,
    UNVALIDATED_PAYLOAD,
    XSS_RISK,
    future_risk_rating,
    scan_source,
    security_index,
)


def _find(graph, vulnerability_type):
    return [
        node
        for node in graph["nodes"]
        if node.get("vulnerability_type") == vulnerability_type
    ]


class TestGraphSecurityScan:
    def test_hardcoded_secret_is_flagged(self, parser):
        graph = parser.parse_code('API_KEY = "sk-live-123456"\n')
        nodes = _find(graph, HARDCODED_SECRET)
        assert nodes
        node = nodes[0]
        assert node["is_vulnerable"] is True
        assert node["severity"] == "CRITICAL"
        assert node["data"]["is_vulnerable"] is True
        assert node["data"]["vulnerability_type"] == HARDCODED_SECRET
        assert node["data"]["mitigation"]
        assert "sk-live" not in node["data"]["description"]

    def test_sql_injection_is_flagged(self, parser):
        graph = parser.parse_code(
            'def users(user_input):\n'
            '    query = f"SELECT * FROM users WHERE name = {user_input}"\n'
            '    cursor.execute(query)\n'
        )
        nodes = _find(graph, SQL_INJECTION_RISK)
        assert nodes
        node = nodes[0]
        assert node["severity"] == "HIGH"
        assert node["data"]["description"] == (
            "Raw string formatting used in database query execution."
        )
        assert node["data"]["mitigation"] == (
            "Use parameterized queries or ORM query builders."
        )

    def test_parameterized_query_is_not_flagged(self, parser):
        graph = parser.parse_code(
            'def find(cursor, name):\n'
            '    cursor.execute("SELECT * FROM users WHERE name = %s", (name,))\n'
        )
        assert _find(graph, SQL_INJECTION_RISK) == []

    def test_unsafe_execution_is_flagged(self, parser):
        for source in (
            "eval(user_input)\n",
            "exec(user_input)\n",
            "os.system(command)\n",
        ):
            graph = parser.parse_code(source)
            assert _find(graph, UNSAFE_CODE_EXECUTION), source

    def test_unprotected_route_is_flagged(self, parser):
        graph = parser.parse_code(
            '@app.route("/admin")\n'
            "def admin():\n"
            '    return "ok"\n'
        )
        nodes = _find(graph, MISSING_AUTH)
        assert nodes
        assert nodes[0]["severity"] == "HIGH"
        assert "/admin" in nodes[0]["data"]["description"]

    def test_authenticated_route_is_not_flagged(self, parser):
        graph = parser.parse_code(
            '@app.route("/admin")\n'
            "@login_required\n"
            "def admin():\n"
            '    return "ok"\n'
        )
        assert _find(graph, MISSING_AUTH) == []

    def test_graph_summary_counts_findings(self, parser):
        graph = parser.parse_code(
            'API_KEY = "sk-live-123456"\n'
            "eval(user_input)\n"
        )
        summary = graph["security"]
        assert summary["vulnerable_nodes"] >= 2
        assert summary["total_findings"] >= 2
        assert summary["by_severity"]["CRITICAL"] >= 2
        assert graph["stats"]["vulnerabilities"] == summary["vulnerable_nodes"]

    def test_javascript_template_literal_is_flagged_by_source_scan(self):
        findings = scan_source(
            "const q = `SELECT * FROM users WHERE name = ${name}`;\n",
            language="javascript",
        )
        assert any(
            finding["vulnerability_type"] == SQL_INJECTION_RISK
            for finding in findings
        )


class TestSourceSecurityScan:
    def test_source_scan_reports_line_numbers(self):
        findings = scan_source(
            "x = 1\n"
            'password = "hunter2"\n'
            'query = f"SELECT * FROM users WHERE id = {uid}"\n'
        )
        by_type = {finding["vulnerability_type"]: finding for finding in findings}
        assert by_type[HARDCODED_SECRET]["line"] == 2
        assert by_type[SQL_INJECTION_RISK]["line"] == 3
        assert by_type[HARDCODED_SECRET]["evidence"] == "password = [redacted]"

    def test_comment_only_patterns_are_ignored(self):
        findings = scan_source(
            "# eval(user_input)\n"
            '# password = "hunter2"\n'
        )
        assert findings == []


class TestAdvancedPatterns:
    def test_bola_without_ownership_check(self):
        findings = scan_source(
            "def order():\n"
            '    account = db.session.get(Account, request.args.get("id"))\n'
        )
        by_type = {f["vulnerability_type"]: f for f in findings}
        assert BOLA_RISK in by_type
        assert by_type[BOLA_RISK]["severity"] == "HIGH"
        assert by_type[BOLA_RISK]["patch"]
        assert by_type[BOLA_RISK]["exploit"]

    def test_bola_with_ownership_check_is_clean(self):
        findings = scan_source(
            "def order():\n"
            '    account = db.session.get(Account, request.args.get("id"))\n'
            "    if account.owner_id != current_user.id:\n"
            "        abort(403)\n"
        )
        assert BOLA_RISK not in {f["vulnerability_type"] for f in findings}

    def test_ssrf_flagged(self):
        findings = scan_source(
            "import requests\n"
            "def proxy():\n"
            '    return requests.get(request.args.get("url"))\n'
        )
        assert SSRF_RISK in {f["vulnerability_type"] for f in findings}

    def test_deserialization_flagged(self):
        for source in (
            "import pickle\npickle.loads(data)\n",
            "import yaml\nyaml.load(data)\n",
        ):
            findings = scan_source(source)
            by_type = {f["vulnerability_type"]: f for f in findings}
            assert UNSAFE_DESERIALIZATION in by_type
            assert by_type[UNSAFE_DESERIALIZATION]["severity"] == "CRITICAL"

    def test_safe_yaml_loader_is_clean(self):
        findings = scan_source(
            "import yaml\nyaml.load(data, Loader=yaml.SafeLoader)\n"
        )
        assert UNSAFE_DESERIALIZATION not in {
            f["vulnerability_type"] for f in findings
        }

    def test_nosql_flagged(self):
        findings = scan_source(
            "def f(uid):\n"
            '    return db.users.find({"$where": f"this.age > {uid}"})\n'
        )
        assert NOSQL_INJECTION_RISK in {f["vulnerability_type"] for f in findings}

    def test_xss_flagged(self):
        findings = scan_source(
            "def page():\n"
            '    q = request.args.get("q")\n'
            '    return render_template_string("<p>" + q + "</p>")\n'
        )
        assert XSS_RISK in {f["vulnerability_type"] for f in findings}

    def test_race_flagged(self):
        findings = scan_source(
            "import os\n"
            "def f(p):\n"
            "    if os.path.exists(p):\n"
            "        return open(p).read()\n"
        )
        assert RACE_CONDITION_RISK in {f["vulnerability_type"] for f in findings}

    def test_unvalidated_payload_flagged(self):
        findings = scan_source(
            "def f():\n"
            "    data = request.get_json()\n"
            "    cursor.execute('SELECT 1')\n"
        )
        assert UNVALIDATED_PAYLOAD in {f["vulnerability_type"] for f in findings}

    def test_validated_payload_is_clean(self):
        findings = scan_source(
            "def f():\n"
            "    data = UserIn.model_validate(request.get_json())\n"
            "    cursor.execute('SELECT 1')\n"
        )
        assert UNVALIDATED_PAYLOAD not in {f["vulnerability_type"] for f in findings}


class TestSecurityIndex:
    def test_clean_code_scores_100(self):
        findings = scan_source("x = 1\n")
        assert findings == []
        assert security_index(findings) == 100
        assert future_risk_rating(findings) == "LOW"

    def test_critical_lowers_index_and_rating(self):
        findings = scan_source("import pickle\npickle.loads(data)\n")
        assert security_index(findings) == 75
        assert future_risk_rating(findings) == "CRITICAL"

    def test_graph_summary_carries_score_and_rating(self, parser):
        graph = parser.parse_code("import pickle\npickle.loads(data)\n")
        summary = graph["security"]
        assert summary["security_index"] < 100
        assert summary["future_risk_rating"] == "CRITICAL"


class TestSecurityApi:
    def test_parse_endpoint_returns_security_metadata(self, client):
        response = client.post(
            "/api/v1/parse/code",
            json={
                "code": (
                    '@app.route("/users")\n'
                    "def users(user_input):\n"
                    '    query = f"SELECT * FROM users WHERE name = {user_input}"\n'
                    "    eval(user_input)\n"
                )
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        graph = payload["data"]
        assert graph["security"]["total_findings"] >= 2
        vulnerable = [
            node for node in graph["nodes"] if node.get("is_vulnerable")
        ]
        assert vulnerable
        assert all(node["data"]["mitigation"] for node in vulnerable)
        assert any(
            node["data"]["vulnerability_type"] == SQL_INJECTION_RISK
            for node in vulnerable
        )
