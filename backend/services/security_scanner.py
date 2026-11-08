"""Static security scanning for parsed code graphs.

The scanner is deliberately dependency free: it walks the ``code`` snippets
already produced by the AST parser and uses conservative, line-oriented
patterns to flag the highest-signal issues first. It never executes source
code and never exposes a detected secret value - only the identifier and a
redacted explanation are returned.

Public entry points:

* :func:`scan_source` - inspect a raw source string and return findings.
* :func:`scan_graph` - enrich a parsed graph with per-node security metadata
  and a graph-level summary.
* :class:`SecurityScanner` - small facade mirroring the other domain services.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

SEVERITY_ORDER = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}

HARDCODED_SECRET = "HARDCODED_SECRET"
UNSAFE_CODE_EXECUTION = "UNSAFE_CODE_EXECUTION"
SQL_INJECTION_RISK = "SQL_INJECTION_RISK"
MISSING_AUTH = "MISSING_AUTH"
BOLA_RISK = "BOLA_RISK"
SSRF_RISK = "SSRF_RISK"
NOSQL_INJECTION_RISK = "NOSQL_INJECTION_RISK"
XSS_RISK = "XSS_RISK"
UNSAFE_DESERIALIZATION = "UNSAFE_DESERIALIZATION"
RACE_CONDITION_RISK = "RACE_CONDITION_RISK"
UNVALIDATED_PAYLOAD = "UNVALIDATED_PAYLOAD"

# Points deducted from a perfect 100 Security Index per finding.
_SEVERITY_WEIGHTS = {
    "CRITICAL": 25,
    "HIGH": 15,
    "MEDIUM": 8,
    "LOW": 3,
}

# Production-ready mitigations keyed by vulnerability type. Each patch is a
# drop-in snippet that eliminates the flagged pattern without changing the
# surrounding control flow.
_FIX_PATCHES: dict[str, str] = {
    HARDCODED_SECRET: (
        'import os\n\nAPI_KEY = os.environ["API_KEY"]\n'
        "# Rotate the previously committed secret: it is already compromised."
    ),
    UNSAFE_CODE_EXECUTION: (
        "ALLOWED_ACTIONS = {'publish': publish, 'archive': archive}\n"
        "# Replace eval/exec with an explicit dispatch table.\n"
        "action = ALLOWED_ACTIONS.get(name)\n"
        "if action is None:\n"
        '    raise ValueError(f"Unknown action: {name!r}")\n'
        "action(payload)"
    ),
    SQL_INJECTION_RISK: (
        "cursor.execute(\n"
        '    "SELECT * FROM users WHERE name = %s",\n'
        "    (user_input,),\n"
        ")"
    ),
    MISSING_AUTH: (
        "from flask_login import login_required\n\n"
        "@app.route('/admin')\n"
        "@login_required\n"
        "def admin():\n"
        "    return 'ok'"
    ),
    BOLA_RISK: (
        "from flask import abort\n\n"
        "record = db.get(Account, account_id)\n"
        "# Enforce object-level authorization: callers may only read\n"
        "# objects that belong to them.\n"
        "if record is None or record.owner_id != current_user.id:\n"
        "    abort(404)\n"
        "return render_template('account.html', account=record)"
    ),
    SSRF_RISK: (
        "from urllib.parse import urlparse\n\n"
        "ALLOWED_HOSTS = {'api.example.com'}\n"
        "parsed = urlparse(url)\n"
        "if parsed.scheme not in {'https'} or parsed.hostname not in ALLOWED_HOSTS:\n"
        "    raise ValueError('URL host is not allow-listed')\n"
        "requests.post(parsed.geturl(), timeout=5)"
    ),
    NOSQL_INJECTION_RISK: (
        "from bson import ObjectId\n\n"
        "_id = ObjectId(validated_id)\n"
        "document = collection.find_one({'_id': _id})\n"
        "# Operator allow-list: never interpolate raw user input into $where,\n"
        "# $regex, or operator dictionaries."
    ),
    XSS_RISK: (
        "from jinja2 import escape\n\n"
        "# Auto-escaping templates (render_template with autoescape) or an\n"
        "# explicit escape both close the reflected/reflected-DOM hole:\n"
        "html = f'<p>{escape(user_input)}</p>'\n"
        "return html"
    ),
    UNSAFE_DESERIALIZATION: (
        "import json\n\n"
        "# JSON (data-only) instead of pickle/marshal; if YAML is required:\n"
        "data = json.loads(payload)\n"
        "# yaml.load(payload, Loader=yaml.SafeLoader)"
    ),
    RACE_CONDITION_RISK: (
        "import os\n\n"
        "# Atomic create-or-fail avoids the check-then-act (TOCTOU) race.\n"
        "fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
        "os.write(fd, content)\n"
        "os.close(fd)"
    ),
    UNVALIDATED_PAYLOAD: (
        "from pydantic import BaseModel, ValidationError\n\n"
        "class UserIn(BaseModel):\n"
        "    user_id: int\n"
        "    name: str\n\n"
        "try:\n"
        "    payload = UserIn.model_validate(request.get_json())\n"
        "except ValidationError:\n"
        "    abort(422, 'Invalid payload')"
    ),
}

# How an attacker exercises each flaw, phrased for the Inspector so users
# understand the real-world impact before applying the patch.
_EXPLOITS: dict[str, str] = {
    HARDCODED_SECRET:(
        "Anyone with read access to the repository (or a leaked backup) can "
        "reuse the secret to impersonate the service until it is rotated."
    ),
    UNSAFE_CODE_EXECUTION: (
        "Attacker-controlled input reaches the interpreter; a crafted string "
        "executes arbitrary commands with the process privileges."
    ),
    SQL_INJECTION_RISK: (
        "Interpolated input breaks out of the quoted literal, letting the "
        "attacker read or modify arbitrary tables, exfiltrate data, or "
        "bypass authentication checks."
    ),
    MISSING_AUTH: (
        "An unauthenticated caller can reach the handler directly with a "
        "plain HTTP request - no session, token, or role is ever verified."
    ),
    BOLA_RISK: (
        "Enumerating object identifiers (orders, invoices, profiles) lets "
        "one authenticated user read or mutate another user's records "
        "(insecure direct object reference)."
    ),
    SSRF_RISK: (
        "A user-supplied URL is fetched server-side, so an attacker can "
        "probe internal services, cloud metadata endpoints (169.254.169.254), "
        "or smuggle requests past the network perimeter."
    ),
    NOSQL_INJECTION_RISK: (
        "Operator objects such as $gt/$ne or string $where expressions "
        "smuggle logic into the query, dumping collections or bypassing "
        "login comparisons."
    ),
    XSS_RISK: (
        "Unescaped markup is reflected/DOM-injected into the victim's "
        "browser, stealing sessions, performing actions as the victim, or "
        "distributing drive-by content."
    ),
    UNSAFE_DESERIALIZATION: (
        "A crafted payload deserializes into arbitrary objects/code "
        "execution before any application check runs (pickle/marshal RCE)."
    ),
    RACE_CONDITION_RISK: (
        "Between the existence check and the file operation, a local "
        "attacker swaps the path (symlink/rename) to redirect writes or "
        "overwrite privileged files (TOCTOU)."
    ),
    UNVALIDATED_PAYLOAD: (
        "Untrusted JSON/form data flows straight into queries, filesystem, "
        "or command sinks; a single malformed field can corrupt state or "
        "trigger an injection sink downstream."
    ),
}

_REQUEST_DATA_RE = re.compile(
    r"\b(?:request|req)(?:\.(?:json|form|args|data|get_json|query_params?|params|body|query|headers|view_args)"
    r"|\s*\[|\s*\()"
)

_URL_FETCH_RE = re.compile(
    r"\b(?:requests\s*\.\s*(?:get|post|put|patch|delete|request|head|options)"
    r"|urllib\.request\.urlopen|urlopen|fetch\s*\(|axios\s*\.\s*(?:get|post|put|delete|request)"
    r"|http\s*\.\s*get|https?\s*\.\s*get)"
)

_URL_ARG_RE = re.compile(
    r"(?i)\b(?:url|uri|endpoint|target_url|webhook_url|callback_url|href|link|src)\b|\brequest\s*\."
)

_OBJECT_ACCESS_RE = re.compile(
    r"db\.session\.(?:get|query)\s*\(|\.query\s*\.\s*filter|\.query\s*\.\s*get\s*\("
    r"|\bfind_one\s*\(|\bfindById\s*\(|\bfind_by_id\s*\(|\bobjects\.get\s*\("
    r"|\.first\s*\(|\brepository\s*\.\s*get\s*\(|\bModel\s*\.\s*get\s*\("
)

_ID_LOOKUP_RE = re.compile(
    r"(?i)\b(?:request|req)\s*(?:\.args|\.form|\.json|\.view_args|\.params|\.query_params)"
    r"\s*(?:\.get\s*\(|\[|\()\s*[\"']?(?:[a-z_]*?)\bid(?:[a-z_]*?)\b"
    r"|\b(?:request|req)\s*(?:\.args|\.form|\.json|\.view_args|\.params)\s*"
    r"(?:\.get\s*\(|\[|\()\s*[\"']?(?:[a-z_]*?)\b(?:uid|user|account|order|object|key)(?:[a-z_]*?)\b"
)

_OWNERSHIP_RE = re.compile(
    r"(?i)\b(?:current_user|request\.user|session\[['\"]?user|owner_id\s*==|is_admin|has_permission"
    r"|require_role|can_access|check_owner|verify_ownership|user_id\s*==\s*|created_by\s*==)"
)

_DESERIALIZATION_RE = re.compile(
    r"\b(?:pickle\s*\.\s*(?:loads|load)|marshal\s*\.\s*(?:loads|load)|shelve\.open"
    r"|jsonpickle\s*\.\s*decode|torch\.load|__import__\s*\([\"']pickle[\"']\))"
)

_YAML_LOAD_RE = re.compile(r"\byaml\.load\s*\(\s*(?!.*Loader\s*=)")

_RACE_CHECK_RE = re.compile(
    r"(?:os\.path\.exists|pathlib\.Path\s*\([^)]*\)\.exists|os\.access|os\.stat\s*\()"
)
_FILE_USE_RE = re.compile(r"\bopen\s*\(|\bread_text\s*\(|\bwrite_text\s*\(|\bos\.(?:remove|rename|unlink)\s*\(|\bshutil\.")

_NOSQL_OPERATOR_RE = re.compile(
    r"\$where|\$regex|\$gt\s*:|\$ne\s*:|\$nin\s*:|\$exists\s*:"
)

_XSS_SINK_RE = re.compile(
    r"render_template_string\s*\(|\.innerHTML\s*=|document\.write\s*\(|v-html|__html|dangerouslySetInnerHTML"
)

_EXFIL_TOKEN_RE = re.compile(
    r"(?i)\b(?:authorization|bearer|cookie|session|passwd|ssn)\b"
)

_SECURITY_FIELDS = (
    "is_vulnerable",
    "severity",
    "vulnerability_type",
    "description",
    "mitigation",
)

_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|static|final|readonly)\s+)*"
    r"(?:(?:const|let|var)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?::\s*[^=\n]+?)?"
    r"=\s*"
    r"(?:(?P<dq>\")(?P<dval>(?:\\.|[^\"\\])*)\""
    r"|(?P<sq>')(?P<sval>(?:\\.|[^'\\])*)'"
    r"|(?P<bq>`)(?P<bval>(?:\\.|[^`\\])*)`)",
    re.MULTILINE,
)

_SECRET_WORDS = frozenset(
    {"password", "passwd", "pwd", "secret", "token", "credential", "credentials"}
)
_SECRET_COMPOUNDS = (
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "authkey",
    "secretkey",
    "sessiontoken",
)

_SQL_RE = re.compile(
    r"(?is)\b(?:select\b[\s\S]{0,220}?\bfrom\b"
    r"|insert\s+into\b"
    r"|update\s+[\w.`\"\[\]]+\s+set\b"
    r"|delete\s+from\b"
    r"|create\s+table\b"
    r"|drop\s+table\b"
    r"|alter\s+table\b"
    r"|union\s+select\b)"
)

_EXECUTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.])eval\s*\(", re.IGNORECASE), "eval()"),
    (re.compile(r"(?<![\w.])exec\s*\(", re.IGNORECASE), "exec()"),
    (re.compile(r"\bos\.system\s*\(", re.IGNORECASE), "os.system()"),
    (
        re.compile(
            r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\("
            r"[\s\S]*?\bshell\s*=\s*True",
            re.IGNORECASE,
        ),
        "subprocess shell=True",
    ),
    (
        re.compile(r"\bchild_process\.(?:exec|execSync)\s*\(", re.IGNORECASE),
        "child_process.exec()",
    ),
    (re.compile(r"\bnew\s+Function\s*\(", re.IGNORECASE), "new Function()"),
)

_DECLARATION_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|function)\s+[\w$]*\s*\(", re.IGNORECASE
)
_ROUTE_DECORATOR_RE = re.compile(
    r"(?i)"
    r"@[\w.]*\.(?:route|api_route|get|post|put|patch|delete|websocket)\s*\("
    r"|@(?:route|api_route|get|post|put|patch|delete|websocket)\s*\("
    r"|(?:^|\s)(?:app|router|blueprint|api|server|fastify|bp|routes)"
    r"\s*\.\s*(?:get|post|put|patch|delete|websocket|route|api_route)\s*\("
)
_AUTH_DECORATOR_NAME_RE = re.compile(
    r"(?i)(auth|login|permission|role|jwt|token|guard|protect|security|requires)"
)
_JS_ROUTE_RE = re.compile(
    r"(?i)\b(?:app|router|server|api|fastify)\.(?:get|post|put|patch|delete|all)"
    r"\s*\(\s*(?P<quote>['\"`])"
)
_JS_AUTH_RE = re.compile(
    r"(?i)\b(?:requireauth|require_auth|isauthenticated|is_authenticated"
    r"|authenticate|authorize|verifytoken|verify_token|authmiddleware"
    r"|auth_middleware|jwt|passport|guard|permission|role|currentuser"
    r"|current_user|getuser|get_user)\b"
)
_ROUTE_PATH_RE = re.compile(r"['\"`]([^'\"`]+)['\"`]")

# Nodes whose `code` spans their entire body. They are still scanned for
# missing auth, but findings already reported by a precise child statement are
# suppressed so one flaw does not light up both the function and its line.
_CONTAINER_KINDS = frozenset(
    {"function", "method", "class", "constructor", "arrow", "interface"}
)


def _clip(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _finding(
    vulnerability_type: str,
    severity: str,
    description: str,
    mitigation: str,
    *,
    line: int | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "is_vulnerable": True,
        "severity": severity,
        "vulnerability_type": vulnerability_type,
        "description": description,
        "mitigation": mitigation,
        "exploit": _EXPLOITS.get(vulnerability_type, ""),
        "patch": _FIX_PATCHES.get(vulnerability_type, ""),
    }
    if line is not None:
        finding["line"] = line
    if evidence:
        finding["evidence"] = _clip(evidence)
    return finding


def security_index(findings: Iterable[dict[str, Any]]) -> int:
    """Project Security Index: 100 minus weighted finding penalties."""
    penalty = 0
    for finding in findings:
        penalty += _SEVERITY_WEIGHTS.get(
            str(finding.get("severity") or "MEDIUM").upper(), 8
        )
    return max(0, min(100, 100 - penalty))


def future_risk_rating(findings: Iterable[dict[str, Any]], index: int | None = None) -> str:
    """Predictive rating of future exploitability from the finding set."""
    max_severity = 0
    found = False
    for finding in findings:
        found = True
        max_severity = max(
            max_severity, SEVERITY_ORDER.get(str(finding.get("severity") or "").upper(), 0)
        )
    if not found:
        return "LOW"
    if max_severity >= SEVERITY_ORDER["CRITICAL"]:
        return "CRITICAL"
    score = security_index(findings) if index is None else int(index)
    if score < 50 or max_severity >= SEVERITY_ORDER["HIGH"]:
        return "HIGH"
    if score < 80:
        return "MEDIUM"
    return "LOW"


def _name_words(name: str) -> set[str]:
    spaced = re.sub(r"[^0-9A-Za-z]+", " ", str(name or ""))
    return {
        word.lower()
        for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", spaced)
    }


def _looks_like_secret_name(name: str) -> bool:
    words = _name_words(name)
    if words & _SECRET_WORDS:
        return True
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return any(marker in normalized for marker in _SECRET_COMPOUNDS)


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return (
        not stripped
        or stripped.startswith("#")
        or stripped.startswith("//")
        or stripped.startswith("/*")
        or stripped.startswith("*")
    )


def _iter_code_lines(code: str, base_line: int | None) -> Iterable[tuple[int, str]]:
    start = int(base_line) if isinstance(base_line, int) and base_line > 0 else 1
    for offset, line in enumerate(str(code or "").splitlines()):
        if _is_comment_line(line):
            continue
        yield start + offset, line


def _secret_value(match: re.Match[str]) -> str:
    for key in ("dval", "sval", "bval"):
        value = match.group(key)
        if value is not None:
            return value
    return ""


def _scan_hardcoded_secrets(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        value = _secret_value(match)
        if not value.strip() or not _looks_like_secret_name(name):
            continue
        findings.append(
            _finding(
                HARDCODED_SECRET,
                "CRITICAL",
                f"Hardcoded secret detected in '{name}'.",
                "Move secrets into environment variables or a secure secret "
                "manager and rotate the exposed value.",
                line=line_no,
                evidence=f"{name} = [redacted]",
            )
        )
    return findings


def _scan_unsafe_execution(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        # Scan one-line declarations too (`function f() { return eval(x); }`),
        # but drop the declaration prefix so a parameter/name called `exec`
        # is not mistaken for a call.
        scan_line = _DECLARATION_RE.sub("", line) if _DECLARATION_RE.match(line) else line
        for pattern, label in _EXECUTION_PATTERNS:
            match = pattern.search(scan_line)
            if not match:
                continue
            findings.append(
                _finding(
                    UNSAFE_CODE_EXECUTION,
                    "CRITICAL",
                    f"Unsafe dynamic code execution via '{label}'.",
                    "Avoid eval/exec with untrusted input; use a safe parser, "
                    "an allowlist, or an explicit dispatch table.",
                    line=line_no,
                    evidence=line,
                )
            )
            break
    return findings


def _sql_interpolation(line: str) -> str | None:
    if re.search(r"(?i)\b(?:rf|fr|f)\s*['\"]", line) and "{" in line:
        return "f-string"
    if "`" in line and "${" in line:
        return "template literal"
    if ".format(" in line:
        return "str.format"
    if re.search(r"%\s+(?:\(|[A-Za-z_$\[{])", line):
        return "% formatting"
    if re.search(
        r"['\"]\s*\+\s*[A-Za-z_$]|[A-Za-z_$][\w$]*(?:\[[^\]]*\]|\.[\w$]+)*\s*\+\s*['\"]",
        line,
    ):
        return "string concatenation"
    return None


def _scan_sql_injection(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        if not _SQL_RE.search(line):
            continue
        interpolation = _sql_interpolation(line)
        if not interpolation:
            continue
        findings.append(
            _finding(
                SQL_INJECTION_RISK,
                "HIGH",
                "Raw string formatting used in database query execution.",
                "Use parameterized queries or ORM query builders.",
                line=line_no,
                evidence=line,
            )
        )
    return findings


def _route_path(text: str) -> str | None:
    match = _ROUTE_PATH_RE.search(text or "")
    return match.group(1) if match else None


def _decorator_names(decorators: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for decorator in decorators or ():
        text = str(decorator or "").strip().lstrip("@")
        names.append(text.split("(", 1)[0].strip())
    return names


def _has_auth_guard(decorators: Iterable[Any], code: str = "") -> bool:
    decorator_text = " ".join(str(item or "") for item in decorators or ())
    for name in _decorator_names(decorators):
        if _AUTH_DECORATOR_NAME_RE.search(name):
            return True
    lower = decorator_text.lower()
    if re.search(r"\b(?:depends|security)\s*\(", lower) and re.search(
        r"auth|user|token|permission|role|jwt|principal", lower
    ):
        return True
    if re.search(
        r"(?i)@\w*(?:auth|login|permission|role|jwt|token|guard|protect|security|requires)",
        code or "",
    ):
        return True
    return bool(_JS_AUTH_RE.search(code or ""))


def _scan_missing_auth(
    node: dict[str, Any],
    data: dict[str, Any],
    code: str,
    base_line: int | None,
) -> list[dict[str, Any]]:
    decorators = data.get("decorators") or node.get("decorators") or []
    if not isinstance(decorators, list):
        decorators = [decorators]

    route_decorator = None
    for decorator in decorators:
        if _ROUTE_DECORATOR_RE.search(str(decorator or "")):
            route_decorator = str(decorator)
            break

    route_text = ""
    if route_decorator is not None:
        if _has_auth_guard(decorators, code):
            return []
        route_text = route_decorator
    else:
        language = str(
            data.get("language") or node.get("language") or ""
        ).lower()
        is_js = language.startswith(
            ("javascript", "typescript", "js", "ts", "node")
        )
        if not is_js:
            return []
        match = _JS_ROUTE_RE.search(code or "")
        if not match:
            return []
        if _has_auth_guard(decorators, code):
            return []
        route_text = code

    path = _route_path(route_text)
    target = f"API route '{path}'" if path else "API route"
    return [
        _finding(
            MISSING_AUTH,
            "HIGH",
            f"{target} is missing an authentication or authorization guard.",
            "Require authentication before the handler executes - for example "
            "with @login_required, an auth dependency, or an auth middleware.",
            line=base_line,
            evidence=route_text,
        )
    ]


def _scan_deserialization(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        if _DESERIALIZATION_RE.search(line) or _YAML_LOAD_RE.search(line):
            label = "yaml.load without SafeLoader" if "\\byaml\\" in line else "pickle/marshal-class deserialization"
            findings.append(
                _finding(
                    UNSAFE_DESERIALIZATION,
                    "CRITICAL",
                    f"Unsafe object deserialization ({label}) can execute code during parsing.",
                    "Use a data-only format (JSON) or, for YAML, "
                    "yaml.load(payload, Loader=yaml.SafeLoader).",
                    line=line_no,
                    evidence=line,
                )
            )
            break
    return findings


def _scan_ssrf(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        if _URL_FETCH_RE.search(line) and _URL_ARG_RE.search(line):
            # Allow-listed hosts / literal URLs are acceptable targets.
            if re.search(r"(?i)allowlist|allowed_hosts|allow_list|whitelist|urlparse", code):
                continue
            findings.append(
                _finding(
                    SSRF_RISK,
                    "HIGH",
                    "Server-side HTTP request built from a user-controlled URL.",
                    "Validate the URL against a host allow-list and pin the "
                    "scheme to https; never fetch arbitrary user-supplied URLs.",
                    line=line_no,
                    evidence=line,
                )
            )
            break
    return findings


def _scan_nosql(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        raw = re.search(r"\$where", line)
        operator = _NOSQL_OPERATOR_RE.search(line)
        if not operator:
            continue
        tainted = bool(raw) or _sql_interpolation(line) or _REQUEST_DATA_RE.search(line)
        if not tainted:
            continue
        findings.append(
            _finding(
                NOSQL_INJECTION_RISK,
                "HIGH",
                "NoSQL operator query built from unvalidated input ($where/$regex/f-string).",
                "Cast identifiers (ObjectId/int) and build queries from an "
                "operator allow-list; never interpolate raw input into $where.",
                line=line_no,
                evidence=line,
            )
        )
        break
    return findings


def _scan_xss(code: str, base_line: int | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in _iter_code_lines(code, base_line):
        if _XSS_SINK_RE.search(line) and (
            _REQUEST_DATA_RE.search(line) or _REQUEST_DATA_RE.search(code)
        ):
            if re.search(r"(?i)escape|sanitize|bleach|autoescape", code):
                continue
            findings.append(
                _finding(
                    XSS_RISK,
                    "HIGH",
                    "User-controlled data reaches an XSS sink without escaping.",
                    "Render through an auto-escaping template or escape the "
                    "value (jinja2.escape / html.escape) before insertion.",
                    line=line_no,
                    evidence=line,
                )
            )
            break
    return findings


def _scan_bola(code: str, base_line: int | None) -> list[dict[str, Any]]:
    if not _ID_LOOKUP_RE.search(code) or not _OBJECT_ACCESS_RE.search(code):
        return []
    if _OWNERSHIP_RE.search(code):
        return []
    line_no = (int(base_line) if isinstance(base_line, int) and base_line > 0 else 1)
    return [
        _finding(
            BOLA_RISK,
            "HIGH",
            "Object fetched by a caller-supplied identifier with no "
            "ownership/authorization check (BOLA / IDOR).",
            "Verify the object belongs to the caller (owner_id == "
            "current_user.id) and return 404 for foreign objects.",
            line=line_no,
            evidence=_clip(code),
        )
    ]


def _scan_race_condition(code: str, base_line: int | None) -> list[dict[str, Any]]:
    if re.search(r"(?i)fcntl|flock|threading\.Lock|O_EXCL|O_CREAT", code):
        return []
    lines = list(_iter_code_lines(code, base_line))
    checked = [no for no, text in lines if _RACE_CHECK_RE.search(text)]
    if not checked:
        return []
    used = [no for no, text in lines if _FILE_USE_RE.search(text) and no > min(checked)]
    if not used:
        return []
    return [
        _finding(
            RACE_CONDITION_RISK,
            "MEDIUM",
            "Check-then-act filesystem pattern (TOCTOU race window) between "
            "existence check and file operation.",
            "Open atomically with O_CREAT|O_EXCL or guard the region with a "
            "file lock (fcntl.flock / threading.Lock).",
            line=min(checked),
            evidence=lines[0][1] if lines else "",
        )
    ]


def _scan_unvalidated_payload(code: str, base_line: int | None) -> list[dict[str, Any]]:
    if not _REQUEST_DATA_RE.search(code):
        return []
    if re.search(r"(?i)isinstance|pydantic|BaseModel|marshmallow|jsonschema|validator|validate|schema", code):
        return []
    sink = re.search(r"\b(?:execute|executemany|os\.system|subprocess|eval|exec|open|query|update|insert|remove|delete)\s*\(", code)
    if not sink:
        return []
    start = int(base_line) if isinstance(base_line, int) and base_line > 0 else 1
    lines = code.splitlines()
    # Compute the one-based line of the sink from its byte offset.
    line_no = start + code[: sink.start()].count("\n")
    evidence_index = max(0, min(code[: sink.start()].count("\n"), len(lines) - 1))
    return [
        _finding(
            UNVALIDATED_PAYLOAD,
            "HIGH",
            "Untrusted request payload reaches a sensitive sink without "
            "validation or schema checks.",
            "Validate the payload against a schema (pydantic / marshmallow) "
            "and cast identifiers before use.",
            line=line_no,
            evidence=lines[evidence_index] if lines else "",
        )
    ]


def scan_node(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every security finding attached to a single graph node."""
    if not isinstance(node, dict):
        return []

    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    code = str(data.get("code") or node.get("code") or "")
    if not code.strip():
        return []

    line = data.get("line", node.get("line"))
    findings: list[dict[str, Any]] = []
    findings.extend(_scan_hardcoded_secrets(code, line))
    findings.extend(_scan_unsafe_execution(code, line))
    findings.extend(_scan_sql_injection(code, line))
    findings.extend(_scan_missing_auth(node, data, code, line))
    findings.extend(_scan_deserialization(code, line))
    findings.extend(_scan_ssrf(code, line))
    findings.extend(_scan_nosql(code, line))
    findings.extend(_scan_xss(code, line))
    findings.extend(_scan_bola(code, line))
    findings.extend(_scan_race_condition(code, line))
    findings.extend(_scan_unvalidated_payload(code, line))

    # A single node can match several patterns; keep one finding per unique
    # issue so an aggregated assignment block reports each secret.
    deduped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for finding in findings:
        key = (
            finding.get("vulnerability_type"),
            finding.get("line"),
            finding.get("evidence"),
        )
        deduped.setdefault(key, finding)
    return list(deduped.values())


def _primary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        findings,
        key=lambda finding: SEVERITY_ORDER.get(finding.get("severity"), 0),
    )


def _attach_findings(
    node: dict[str, Any], findings: list[dict[str, Any]]
) -> dict[str, Any]:
    primary = _primary(findings)
    data = node.get("data") if isinstance(node.get("data"), dict) else None
    if data is None:
        data = {}
        node["data"] = data

    # Preserve the parser's human summary before the security description
    # takes the reserved `description` key.
    original_description = data.get("description") or node.get("description")
    if original_description and not data.get("node_description"):
        data["node_description"] = original_description
    if node.get("description") and not node.get("node_description"):
        node["node_description"] = node["description"]

    index = security_index(findings)
    security = {
        "is_vulnerable": True,
        "severity": primary["severity"],
        "vulnerability_type": primary["vulnerability_type"],
        "description": primary["description"],
        "mitigation": primary["mitigation"],
        "exploit": primary.get("exploit") or "",
        "patch": primary.get("patch") or "",
        "security_index": index,
        "future_risk_rating": future_risk_rating(findings, index),
        "findings": findings,
    }

    for target in (node, data):
        target.update(
            {
                "is_vulnerable": True,
                "severity": security["severity"],
                "vulnerability_type": security["vulnerability_type"],
                "description": security["description"],
                "mitigation": security["mitigation"],
                "security_description": security["description"],
                "security_findings": findings,
                "security_index": index,
                "future_risk_rating": security["future_risk_rating"],
                "exploit_risk": security["future_risk_rating"],
            }
        )
        if target is data:
            target["security"] = security

    return node


def scan_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Attach security metadata to every vulnerable node in ``graph``.

    The input dictionary is mutated and returned, matching the style of the
    graph builders. A graph-level ``security`` summary is always present so
    consumers can render a vulnerability counter without walking every node.
    """
    if not isinstance(graph, dict):
        return graph

    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    all_findings: list[dict[str, Any]] = []
    vulnerable_nodes = 0

    scanned: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    specific_keys: set[tuple[str, Any, Any]] = set()

    for node in nodes:
        findings = scan_node(node)
        scanned.append((node, findings))
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        kind = str(data.get("kind") or node.get("kind") or "")
        if kind not in _CONTAINER_KINDS:
            for finding in findings:
                specific_keys.add(
                    (
                        finding.get("vulnerability_type"),
                        finding.get("line"),
                        finding.get("evidence"),
                    )
                )

    for node, findings in scanned:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        kind = str(data.get("kind") or node.get("kind") or "")
        if kind in _CONTAINER_KINDS:
            findings = [
                finding
                for finding in findings
                if (
                    finding.get("vulnerability_type"),
                    finding.get("line"),
                    finding.get("evidence"),
                )
                not in specific_keys
            ]
        if not findings:
            continue
        _attach_findings(node, findings)
        vulnerable_nodes += 1
        node_id = str(node.get("id") or "")
        for finding in findings:
            all_findings.append({"node_id": node_id, **finding})

    by_severity = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in all_findings:
        severity = str(finding.get("severity") or "").upper()
        by_severity[severity] = by_severity.get(severity, 0) + 1

    index = security_index(all_findings)
    graph["security"] = {
        "vulnerable_nodes": vulnerable_nodes,
        "total_findings": len(all_findings),
        "by_severity": by_severity,
        "security_index": index,
        "future_risk_rating": future_risk_rating(all_findings, index),
        "findings": all_findings,
    }

    stats = graph.get("stats")
    if isinstance(stats, dict):
        stats["vulnerabilities"] = vulnerable_nodes

    return graph


def scan_source(source: str, language: str = "python") -> list[dict[str, Any]]:
    """Inspect a raw source string and return security findings.

    Findings carry a one-based ``line`` number. ``language`` is accepted for
    API symmetry; the conservative patterns work across Python and JS/TS.
    """
    del language  # Pattern set is intentionally language-agnostic.
    text = str(source or "")
    findings: list[dict[str, Any]] = []
    findings.extend(_scan_hardcoded_secrets(text, 1))
    findings.extend(_scan_unsafe_execution(text, 1))
    findings.extend(_scan_sql_injection(text, 1))
    findings.extend(_scan_deserialization(text, 1))
    findings.extend(_scan_ssrf(text, 1))
    findings.extend(_scan_nosql(text, 1))
    findings.extend(_scan_xss(text, 1))
    findings.extend(_scan_bola(text, 1))
    findings.extend(_scan_race_condition(text, 1))
    findings.extend(_scan_unvalidated_payload(text, 1))
    # One finding per (type, line, evidence) so overlapping heuristics do
    # not double-count the same flawed line.
    deduped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for finding in findings:
        key = (
            finding.get("vulnerability_type"),
            finding.get("line"),
            finding.get("evidence"),
        )
        deduped.setdefault(key, finding)
    findings = list(deduped.values())

    # Source-level auth scan: a route decorator followed by a function without
    # an auth decorator in between is treated as an exposed handler.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _ROUTE_DECORATOR_RE.search(line):
            continue
        guard = _has_auth_guard([line], "\n".join(lines[index : index + 6]))
        if guard:
            continue
        path = _route_path(line)
        target = f"API route '{path}'" if path else "API route"
        findings.append(
            _finding(
                MISSING_AUTH,
                "HIGH",
                f"{target} is missing an authentication or authorization guard.",
                "Require authentication before the handler executes - for "
                "example with @login_required, an auth dependency, or an auth "
                "middleware.",
                line=index + 1,
                evidence=line,
            )
        )
    return findings


class SecurityScanner:
    """Facade used by callers that prefer an injected service object."""

    def scan_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        return scan_graph(graph)

    def scan_source(self, source: str, language: str = "python") -> list[dict[str, Any]]:
        return scan_source(source, language=language)

    def scan_node(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        return scan_node(node)


__all__ = [
    "BOLA_RISK",
    "HARDCODED_SECRET",
    "MISSING_AUTH",
    "NOSQL_INJECTION_RISK",
    "RACE_CONDITION_RISK",
    "SEVERITY_ORDER",
    "SQL_INJECTION_RISK",
    "SSRF_RISK",
    "UNSAFE_CODE_EXECUTION",
    "UNSAFE_DESERIALIZATION",
    "UNVALIDATED_PAYLOAD",
    "XSS_RISK",
    "SecurityScanner",
    "future_risk_rating",
    "scan_graph",
    "scan_node",
    "scan_source",
    "security_index",
]
