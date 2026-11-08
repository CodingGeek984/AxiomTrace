"""Full-project directory scanner & architecture graph generator.

Walks a source tree (or an uploaded snapshot of one), classifies every
source file into one of five architectural layers and emits a graph that
fits the frontend's canvas contract::

    { nodes, edges, groups, tiers, language, stats, errors, partial, layers, entry_node }

Layers (strictly ordered, ids are stable):

* ``presentation`` - views, components, frontend pages
* ``api``          - controllers, routers, endpoints, middleware
* ``service``      - core services, handlers, utilities
* ``data``         - models, schemas, migrations, repositories
* ``integration``  - third-party SDKs and outbound connectors

The canvas renders these layers as four enterprise swimlane tiers
(top-to-bottom): presentation/entrypoints (Tier 1), api (Tier 2),
service (Tier 3), data + integration connectors (Tier 4). Every node
carries its ``tier`` so the frontend can band it without re-classifying.

Design notes:

* Everything is deterministic and dependency-free. Import extraction uses
  conservative per-language regular expressions; unresolved imports simply
  produce no edge instead of failing the scan.
* Junk directories (``node_modules``, ``.git``, ``dist`` …) never enter the
  graph. Oversized files are skipped and reported in ``stats.skipped``.
* Paths are sanitised: absolute paths and ``..`` segments are rejected, and
  every resolved file must live inside the scanned root.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any, Iterable

LAYER_PRESENTATION = "presentation"
LAYER_API = "api"
LAYER_SERVICE = "service"
LAYER_DATA = "data"
LAYER_INTEGRATION = "integration"

LAYER_ORDER = (
    LAYER_PRESENTATION,
    LAYER_API,
    LAYER_SERVICE,
    LAYER_DATA,
    LAYER_INTEGRATION,
)

LAYER_META: dict[str, dict[str, Any]] = {
    LAYER_PRESENTATION: {
        "label": "Presentation / UI",
        "short": "UI",
        "description": "Views, components, frontend pages and templates.",
        "order": 0,
        "tier": 1,
        "color": "#0B0C0E",
    },
    LAYER_API: {
        "label": "API & Routing",
        "short": "API",
        "description": "Controllers, routers, endpoints and middleware.",
        "order": 1,
        "tier": 2,
        "color": "#30D158",
    },
    LAYER_SERVICE: {
        "label": "Business Logic / Services",
        "short": "Service",
        "description": "Core services, use cases, handlers and utilities.",
        "order": 2,
        "tier": 3,
        "color": "#007AFF",
    },
    LAYER_DATA: {
        "label": "Data & Storage",
        "short": "Data",
        "description": "Models, schemas, migrations, repositories and database clients.",
        "order": 3,
        "tier": 4,
        "color": "#5856D6",
    },
    LAYER_INTEGRATION: {
        "label": "External Integrations",
        "short": "I/O",
        "description": "Third-party SDKs, gateways and outbound connectors.",
        "order": 4,
        "tier": 4,
        "color": "#FF9500",
    },
}

TIER_ORDER = (1, 2, 3, 4)

TIER_META: dict[int, dict[str, Any]] = {
    1: {
        "label": "Client / Presentation Layer",
        "short": "Client",
        "description": "UI components, views, pages and entry points.",
        "color": "#0B0C0E",
    },
    2: {
        "label": "API & Gateway Layer",
        "short": "API",
        "description": "Controllers, routes, endpoints and middleware.",
        "color": "#30D158",
    },
    3: {
        "label": "Business Logic / Service Layer",
        "short": "Logic",
        "description": "Core domain services, handlers, utilities and control flow.",
        "color": "#007AFF",
    },
    4: {
        "label": "Data & Storage Layer",
        "short": "Data",
        "description": "Models, schemas, databases and external connectors.",
        "color": "#5856D6",
    },
}

JUNK_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        "out",
        "target",
        "vendor",
        ".next",
        ".nuxt",
        ".turbo",
        ".cache",
        ".parcel-cache",
        "coverage",
        ".idea",
        ".vscode",
        ".tox",
        ".eggs",
        "site-packages",
        ".angular",
        ".svelte-kit",
    }
)

MAX_FILES = 1500
MAX_BYTES_PER_FILE = 512 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_DEPTH = 20
MAX_IMPORTS_PER_FILE = 80
MAX_PRINCIPAL_SYMBOLS = 10
MAX_EDGES = 2000
# Per-node source cap. Keeps a single pathological file from ballooning the
# graph payload while still shipping the complete text of every normal source
# file to the canvas / inspector / sandbox.
MAX_NODE_SOURCE_CHARS = 128 * 1024
# Cap on inferred database-reference edges emitted per module so a service
# that mentions twenty tables cannot drown the graph in edges.
MAX_DB_EDGES_PER_FILE = 6

_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".php": "php",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objective-c",
    ".mm": "objective-c",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".htm": "html",
    ".sql": "sql",
    ".prisma": "prisma",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
    ".sh": "shell",
    ".bash": "shell",
    ".lua": "lua",
}

_PRESENTATION_EXT = frozenset({".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm", ".astro"})
_DATA_EXT = frozenset({".sql", ".prisma", ".graphql", ".gql"})

_PATH_LAYER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|/)(components|pages|views|screens|templates|layouts|widgets|ui|storybook)(/|$)"), LAYER_PRESENTATION),
    (re.compile(r"(^|/)(controllers?|routes?|routers?|endpoints?|middleware|api)(/|$)"), LAYER_API),
    (re.compile(r"(^|/)(migrations?|models?|entities?|repositories?|repository|dao|schemas?|prisma)(/|$)"), LAYER_DATA),
    (re.compile(r"(^|/)(integrations?|adapters?|gateways?|connectors?|webhooks?|third[_-]?party)(/|$)"), LAYER_INTEGRATION),
    (re.compile(r"(^|/)(services?|domain|usecases?|use[_-]cases?|handlers?|utils?|utilities?|helpers?|core|business)(/|$)"), LAYER_SERVICE),
)

_API_CONTENT_RE = re.compile(
    r"(@app\.(route|get|post|put|patch|delete)\s*\(|@\w+\.(route|get|post|put|patch|delete)\s*\(\s*[\"']|"
    r"Blueprint\s*\(|add_url_rule\s*\(|APIRouter\s*\(|"
    r"\b(app|router|server|fastify|mux)\.(get|post|put|patch|delete|all|route|use)\s*\(\s*[\"']|"
    r"@(RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b|"
    r"@RequestMapping|http\.HandleFunc\s*\(|gin\.(GET|POST|PUT|PATCH|DELETE)\s*\(|HandleFunc\s*\()",
    re.IGNORECASE,
)

_DATA_CONTENT_RE = re.compile(
    r"(declarative_base\s*\(|SQLAlchemy|sqlalchemy\.|Column\s*\(|relationship\s*\(|"
    r"mongoose\.Schema|@Schema\s*\(|@Entity\s*\(|@Document\s*\(|gorm\.Model|ActiveRecord::Base|"
    r"class\s+\w+\s*\(\s*models?\.Model|MongoClient|psycopg2|sqlite3\.connect|create_all\s*\()",
    re.IGNORECASE,
)

_INTEGRATION_CONTENT_RE = re.compile(
    r"(boto3|stripe\.|twilio|sendgrid|slack_sdk|googleapiclient|OpenAI\(|Anthropic\(|"
    r"aws-sdk|@aws-sdk|firebase-admin|mailgun|paypalrestsdk|braintree|plaid|"
    r"kafka|rabbitmq|pika\.BlockingConnection|celery\.Celery|\bnew\s+Pusher|"
    r"graphql-request|HttpBasicAuth\(\s*[\"'])",
    re.IGNORECASE,
)

_HTTP_LITERAL_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/:]|$|[\"'`])", re.IGNORECASE)
_EXTERNAL_URL_HOSTS = (
    "api.stripe.com",
    "api.twilio.com",
    "api.sendgrid.com",
    "hooks.slack.com",
    "api.openai.com",
    "api.anthropic.com",
)

_PRESENTATION_CONTENT_RE = re.compile(
    r"(<(div|span|main|section|input|button|form|ul|li|header|footer|nav|img)\b|"
    r"return\s*\(\s*<[A-Za-z]|React\.createElement|v-for=|v-model=|ng-if=)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# path handling
# ---------------------------------------------------------------------------


def _normalise_relpath(raw: str) -> str | None:
    """Normalise an upload/walk path to a clean posix relative path."""
    if not raw:
        return None
    candidate = str(raw).replace("\\", "/").strip()
    # Strip any drive prefix
    candidate = re.sub(r"^[A-Za-z]:/", "/", candidate)
    candidate = posixpath.normpath(candidate)
    candidate = candidate.lstrip("/")
    if candidate in ("", "."):
        return None
    if candidate.startswith("../") or candidate == "..":
        return None
    if any(part in ("", ".") for part in candidate.split("/")):
        return None
    return candidate


def _is_junk_path(parts: list[str]) -> bool:
    return any(part.lower() in JUNK_DIRS for part in parts[:-1])


def _depth(relpath: str) -> int:
    return relpath.count("/") + 1


def _dotted_module_name(relpath: str) -> str:
    """``backend/services/user.py`` -> ``backend.services.user``."""
    normalised = relpath.replace("\\", "/")
    stem, _ext = posixpath.splitext(normalised)
    parts = [part for part in stem.split("/") if part and part != "."]
    if not parts:
        return ""
    if parts[-1] in {"__init__", "index", "mod"} and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def _cap_source(content: str) -> str:
    """Trim a file's text to the per-node source budget with a visible marker."""
    if len(content) <= MAX_NODE_SOURCE_CHARS:
        return content
    return (
        content[:MAX_NODE_SOURCE_CHARS]
        + f"\n# ... source truncated by AxiomTrace at {MAX_NODE_SOURCE_CHARS} characters ...\n"
    )


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def _classify_layer(relpath: str, content: str) -> str:
    lower = relpath.lower()
    ext = posixpath.splitext(lower)[1]
    parts = lower.split("/")

    if ext in _PRESENTATION_EXT:
        return LAYER_PRESENTATION
    if ext in _DATA_EXT:
        return LAYER_DATA

    for pattern, layer in _PATH_LAYER_RULES:
        if pattern.search(lower):
            return layer

    # Next.js / Nuxt conventions: app|src/pages or route segments named route.[jt]s
    if re.search(r"(^|/)app/.*route\.(ts|js|tsx|jsx)$", lower):
        return LAYER_API
    if re.search(r"(^|/)app/.*page\.(tsx|jsx|ts|js)$", lower):
        return LAYER_PRESENTATION
    if lower.endswith(("middleware.ts", "middleware.js")):
        return LAYER_API

    if _API_CONTENT_RE.search(content):
        return LAYER_API
    if _DATA_CONTENT_RE.search(content):
        return LAYER_DATA
    if _INTEGRATION_CONTENT_RE.search(content):
        return LAYER_INTEGRATION
    if any(host in content.lower() for host in _EXTERNAL_URL_HOSTS):
        return LAYER_INTEGRATION
    if _PRESENTATION_CONTENT_RE.search(content):
        return LAYER_PRESENTATION
    if _HTTP_LITERAL_RE.search(content):
        return LAYER_INTEGRATION
    return LAYER_SERVICE


def _language_for(relpath: str) -> str:
    ext = posixpath.splitext(relpath.lower())[1]
    if ext in _LANGUAGE_BY_EXT:
        return _LANGUAGE_BY_EXT[ext]
    name = posixpath.basename(relpath.lower())
    if name in {"dockerfile", "makefile"}:
        return "shell"
    return ext.lstrip(".") or "text"


def _is_source_file(relpath: str) -> bool:
    ext = posixpath.splitext(relpath.lower())[1]
    return ext in _LANGUAGE_BY_EXT


# ---------------------------------------------------------------------------
# imports / symbols
# ---------------------------------------------------------------------------

_PY_SINGLE_RE = re.compile(r"^\s*(?:from\s+([.\w][\w.]*)\s+import\s+([^#\n]+)|import\s+([^#\n]+))")
_PY_IMPORT_RE = _PY_SINGLE_RE  # backwards-compatible alias
_JS_IMPORT_RE = re.compile(
    r"(?:import\s+(?:[\w*{}\s,]+\s+from\s+)?|export\s+[\w*{}\s,]+\s+from\s+|require\s*\(\s*|import\s*\(\s*)"
    r"[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
_GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\(([^)]*)\)", re.MULTILINE | re.DOTALL)
_GO_SINGLE_IMPORT_RE = re.compile(r"^\s*import\s+(?:\w+\s+)?\"([^\"]+)\"", re.MULTILINE)
_GO_QUOTED_RE = re.compile(r"\"([^\"]+)\"")
_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
_PHP_USE_RE = re.compile(r"^\s*use\s+([\\\w]+)\s*;", re.MULTILINE)
_PHP_REQUIRE_RE = re.compile(r"(?:require|include)(?:_once)?\s*\(?\s*[\"']([^\"']+)[\"']", re.MULTILINE)

_JS_SYMBOL_RE = re.compile(
    r"(?:export\s+(?:default\s+)?(?:async\s+)?(?:function\*?\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=|let\s+(\w+)\s*=))"
    r"|(?:^|\n)\s*(?:async\s+)?function\s+(\w+)"
    r"|(?:^|\n)\s*class\s+(\w+)",
    re.MULTILINE,
)
_PY_SYMBOL_RE = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
_GO_SYMBOL_RE = re.compile(r"^\s*(?:func(?:\s*\([^)]*\))?\s+([A-Za-z_]\w*)|type\s+([A-Za-z_]\w*)\s+(?:struct|interface))", re.MULTILINE)
_JAVA_SYMBOL_RE = re.compile(
    r"(?:class|interface|enum|record)\s+([A-Za-z_]\w*)",
)
_PHP_SYMBOL_RE = re.compile(r"(?:function|class|interface|trait)\s+([A-Za-z_]\w*)")


def _trim_symbols(found: Iterable[str | None]) -> list[str]:
    symbols: list[str] = []
    for item in found:
        if not item:
            continue
        name = item.strip()
        if name and name not in symbols:
            symbols.append(name)
        if len(symbols) >= MAX_PRINCIPAL_SYMBOLS:
            break
    return symbols


def _extract_symbols(relpath: str, content: str) -> list[str]:
    language = _language_for(relpath)
    if language == "python":
        return _trim_symbols(_PY_SYMBOL_RE.findall(content))
    if language in {"javascript", "typescript", "vue", "svelte"}:
        found: list[str | None] = []
        for match in _JS_SYMBOL_RE.finditer(content):
            found.append(next((g for g in match.groups() if g), None))
        return _trim_symbols(found)
    if language == "go":
        found = []
        for match in _GO_SYMBOL_RE.finditer(content):
            found.append(next((g for g in match.groups() if g), None))
        return _trim_symbols(found)
    if language in {"java", "kotlin", "scala"}:
        return _trim_symbols(_JAVA_SYMBOL_RE.findall(content))
    if language == "php":
        return _trim_symbols(_PHP_SYMBOL_RE.findall(content))
    return []


def _extract_imports(relpath: str, content: str) -> list[str]:
    language = _language_for(relpath)
    found: list[str] = []

    def add(candidate: str) -> None:
        cleaned = candidate.strip().strip("\"'")
        if cleaned and cleaned not in found:
            found.append(cleaned)

    if language == "python":
        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            match = _PY_SINGLE_RE.match(line)
            if not match:
                continue
            module, names, plain = match.groups()
            if module:
                module = module.strip()
                add(module)
                if names:
                    for name in re.split(r"[,()\s]+", names):
                        name = name.strip()
                        if name and name != "*":
                            add(f"{module}.{name}")
            elif plain:
                for segment in plain.split(","):
                    segment = segment.strip()
                    if not segment:
                        continue
                    token = segment.split()[0] if segment.split() else ""
                    if token:
                        add(token)
    elif language in {"javascript", "typescript", "vue", "svelte"}:
        for match in _JS_IMPORT_RE.finditer(content):
            add(match.group(1))
    elif language == "go":
        for block in _GO_IMPORT_BLOCK_RE.findall(content):
            for match in _GO_QUOTED_RE.finditer(block):
                add(match.group(1))
        for match in _GO_SINGLE_IMPORT_RE.finditer(content):
            add(match.group(1))
    elif language in {"java", "kotlin", "scala"}:
        for match in _JAVA_IMPORT_RE.finditer(content):
            add(match.group(1))
    elif language == "php":
        for match in _PHP_USE_RE.finditer(content):
            add(match.group(1))
        for match in _PHP_REQUIRE_RE.finditer(content):
            add(match.group(1))
    else:
        # Generic fallback: quoted strings that look like relative paths
        for match in re.finditer(r"[\"'](\.{1,2}/[^\"'\s]+)[\"']", content):
            add(match.group(1))

    return found[:MAX_IMPORTS_PER_FILE]


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def _strip_ext(path: str) -> str:
    base, ext = posixpath.splitext(path)
    return base if ext else path


def _spec_tail(spec: str) -> str:
    """Last meaningful segment of an import spec, lower-cased.

    ``services.user.create_user`` -> ``create_user`` (symbol imports),
    ``./models/orders``          -> ``orders`` (relative requires),
    ``com.app.handlers.Order``   -> ``order`` (Java-style FQCNs).
    """
    cleaned = str(spec or "").strip().strip("\"'").replace("\\", "/")
    if not cleaned or cleaned.startswith("."):
        # Relative specs were already resolved by the literal pass; their
        # remaining tail would only collide with unrelated file stems.
        return ""
    tail = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned.rsplit(".", 1)[-1]
    tail = posixpath.splitext(tail)[0].lower()
    return tail


def _index_modules(modules: list[dict[str, Any]]) -> dict[str, str]:
    """Map every module to its id via several lookup keys."""
    index: dict[str, str] = {}

    def put(key: str, module_id: str) -> None:
        key = key.lower()
        index.setdefault(key, module_id)

    for module in modules:
        rel = module["relpath"]
        module_id = module["id"]
        no_ext = _strip_ext(rel)
        put(no_ext, module_id)
        put(rel, module_id)
        # package style: dir/__init__.py -> dir
        if posixpath.basename(no_ext) == "__init__":
            put(posixpath.dirname(no_ext), module_id)
        # index files: dir/index.ts -> dir
        if posixpath.basename(no_ext) == "index":
            put(posixpath.dirname(no_ext), module_id)
    return index


def _python_module_paths(module: str) -> list[str]:
    if module.startswith("."):
        return [module]
    return [module.replace(".", "/")]


def _resolve_import(
    importer: dict[str, Any],
    spec: str,
    index: dict[str, str],
    modules: list[dict[str, Any]],
    go_module: str | None,
) -> str | None:
    rel = importer["relpath"]
    importer_dir = posixpath.dirname(rel)

    def lookup(candidate: str) -> str | None:
        candidate = candidate.strip().replace("\\", "/")
        candidate = posixpath.normpath(candidate).lstrip("/").lower()
        if not candidate or candidate == ".":
            return None
        hit = index.get(candidate)
        if hit:
            return hit
        # Suffix match: files are often resolved against a package root
        # (e.g. `backend/`) that the importer does not know about.
        suffix = "/" + candidate
        matches = [key for key in index if key.endswith(suffix)]
        if len(matches) == 1:
            return index[matches[0]]
        if matches:
            return index[min(matches, key=len)]
        return None

    language = _language_for(rel)

    if language == "python":
        if spec.startswith("."):
            # relative import: dots encode up-levels
            dots = len(spec) - len(spec.lstrip("."))
            remainder = spec[dots:].replace(".", "/")
            base = importer_dir
            for _ in range(max(dots - 1, 0)):
                base = posixpath.dirname(base)
            candidate = posixpath.normpath(posixpath.join(base, remainder)) if remainder else base
            return lookup(candidate) or lookup(posixpath.join(candidate, "__init__"))
        for candidate in _python_module_paths(spec):
            # try every ancestor prefix (backend/, app/, etc.)
            parts = candidate.split("/")
            for start in range(0, len(parts)):
                sub = "/".join(parts[start:])
                hit = lookup(sub) or lookup(posixpath.join(sub, "__init__"))
                if hit:
                    return hit
        return None

    if language == "go":
        if go_module and spec.startswith(go_module):
            remainder = spec[len(go_module):].lstrip("/")
            for module in modules:
                candidate = module["relpath"]
                dir_name = posixpath.dirname(candidate)
                if remainder == "" and dir_name == "":
                    return module["id"]
                if dir_name == remainder and not candidate.endswith("_test.go"):
                    return module["id"]
                if remainder and posixpath.normpath(candidate).startswith(remainder + "/"):
                    # prefer shallow, non-test files
                    if not candidate.endswith("_test.go"):
                        return module["id"]
        return None

    if language in {"java", "kotlin", "scala"}:
        candidate = spec.replace(".", "/")
        return lookup(candidate)

    if language == "php":
        if spec.startswith(("./", "../", "/")):
            candidate = posixpath.normpath(posixpath.join(importer_dir, spec))
            return lookup(candidate)
        candidate = spec.lstrip("\\").replace("\\", "/")
        hit = lookup(candidate)
        if hit:
            return hit
        # PSR-style: match by suffix anywhere in the tree
        lowered = candidate.lower()
        for key, module_id in index.items():
            if key.endswith("/" + lowered):
                return module_id
        return None

    # JavaScript / TypeScript / generic
    # Common bundler aliases: "@/x", "~/x", "#/x" -> "src/x" (or tree root)
    for alias_prefix in ("@/", "~/", "#/", "@"):
        if spec.startswith(alias_prefix):
            remainder = spec[len(alias_prefix):]
            hit = lookup("src/" + remainder)
            if hit:
                return hit
            return lookup(remainder)
    if spec.startswith("."):
        candidate = posixpath.normpath(posixpath.join(importer_dir, spec))
        return lookup(candidate)
    # absolute-ish imports (src/...) resolve from repo root
    return lookup(spec)


# ---------------------------------------------------------------------------
# graph assembly
# ---------------------------------------------------------------------------

_ENTRY_NAMES = {
    "main",
    "__main__",
    "index",
    "app",
    "server",
    "bootstrap",
    "program",
    "main_test",
}


def _is_entry_file(relpath: str) -> bool:
    name = posixpath.basename(relpath)
    stem = posixpath.splitext(name)[0].lower()
    if stem in _ENTRY_NAMES:
        return True
    if name.lower().startswith("main."):
        return True
    return False


def _humanize_file(relpath: str) -> str:
    return posixpath.basename(relpath)


def _read_go_module(root_files: dict[str, str]) -> str | None:
    for name, content in root_files.items():
        if posixpath.basename(name) != "go.mod":
            continue
        match = re.search(r"^\s*module\s+(\S+)", content, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


# Shared layout contract (mirrors ``LAYOUT`` in frontend src/config/theme.js
# and ``layoutWithDagre`` in src/hooks/useGraphLayout.js): strict top-to-bottom
# flow with ``rankDir: TB``, tier bands stacked with a full rankSep (160)
# gap between them, and cards inside a band wrapped into at most
# ``MAX_TIER_COLUMNS`` columns with nodeSep (100) between cards. The payload
# therefore never degenerates into one flat horizontal row of touching cards,
# and any consumer reading raw positions sees the same multi-tier structure
# the canvas renders.
LAYOUT_RANK_DIR = "TB"
LAYOUT_NODE_WIDTH = 300
LAYOUT_NODE_HEIGHT = 150
TIER_RANK_SEP = 160
TIER_NODE_SEP = 100
MAX_TIER_COLUMNS = 6
TIER_X_ORIGIN = 80
TIER_Y_ORIGIN = 40


def _tier_positions(
    modules: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[int, int]]:
    """Assign strict TB swimlane coordinates to every scanned module.

    Modules are partitioned into the four architecture tiers (T1 Client at
    the top ... T4 Data at the bottom). Each tier band is stacked below the
    previous one with a ``TIER_RANK_SEP`` gap, and the band's cards wrap into
    columns of at most ``MAX_TIER_COLUMNS`` - ``TIER_NODE_SEP`` between
    cards, ``TIER_RANK_SEP`` between wrapped rows.

    Returns ``(positions_by_node_id, tier_band_top)``.
    """
    by_tier: dict[int, list[dict[str, Any]]] = {tier: [] for tier in TIER_ORDER}
    for module in modules:
        tier = int(LAYER_META[module["layer"]]["tier"])
        by_tier[tier].append(module)

    positions: dict[str, dict[str, int]] = {}
    tier_top: dict[int, int] = {}
    cursor_y = TIER_Y_ORIGIN
    for tier in TIER_ORDER:
        tier_modules = by_tier[tier]
        if not tier_modules:
            continue
        # Deterministic order: architectural layer first, then file path -
        # the same ordering the frontend layout applies inside each band.
        tier_modules.sort(
            key=lambda m: (LAYER_META[m["layer"]]["order"], m["relpath"])
        )
        tier_top[tier] = cursor_y
        rows = -(-len(tier_modules) // MAX_TIER_COLUMNS)
        for slot, module in enumerate(tier_modules):
            col = slot % MAX_TIER_COLUMNS
            row = slot // MAX_TIER_COLUMNS
            positions[module["id"]] = {
                "x": TIER_X_ORIGIN + col * (LAYOUT_NODE_WIDTH + TIER_NODE_SEP),
                "y": cursor_y + row * (LAYOUT_NODE_HEIGHT + TIER_RANK_SEP),
            }
        band_height = rows * LAYOUT_NODE_HEIGHT + (rows - 1) * TIER_RANK_SEP
        cursor_y += band_height + TIER_RANK_SEP
    return positions, tier_top


def _build_graph(
    modules: list[dict[str, Any]],
    go_module: str | None,
    *,
    diagnostics: list[dict[str, Any]],
    truncated: bool,
    lines_total: int,
) -> dict[str, Any]:
    # deterministic node numbering
    modules = sorted(modules, key=lambda m: m["relpath"])
    for seq, module in enumerate(modules, start=1):
        module["id"] = f"p{seq}"

    index = _index_modules(modules)

    # Strict TB swimlane coordinates for every node (tier bands stacked with
    # rankSep 160, cards wrapped at MAX_TIER_COLUMNS with nodeSep 100).
    node_positions, tier_top = _tier_positions(modules)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_seen: set[tuple[str, str]] = set()
    group_ids_by_layer: dict[str, list[str]] = {layer: [] for layer in LAYER_ORDER}
    layer_counts = {layer: 0 for layer in LAYER_ORDER}

    for module in modules:
        layer = module["layer"]
        layer_counts[layer] += 1
        meta = LAYER_META[layer]
        tier = int(meta["tier"])
        position = node_positions[module["id"]]
        node_id = module["id"]
        group_ids_by_layer[layer].append(node_id)

        data = {
            "id": node_id,
            "type": module["semantic"],
            "semantic_type": module["semantic"],
            "label": _humanize_file(module["relpath"]),
            # Full file text + identity, so a click on the card hands the
            # inspector and the sandbox real source instead of "".
            "code": module.get("source", ""),
            "source": module.get("source", ""),
            "preview": " / ".join(module["symbols"][:2]),
            "file": module["relpath"],
            "path": module["relpath"],
            "module": module.get("module_name", ""),
            "line": 1,
            "end_line": module["lines"],
            "kind": "module",
            "language": module["language"],
            "status": "idle",
            "category": meta["label"],
            "group": layer,
            "group_label": meta["label"],
            "tier": meta["tier"],
            "tier_label": TIER_META[meta["tier"]]["label"],
            "layer": meta["order"],
            "description": (
                f"{meta['label']} · {module['language']} · {module['lines']} line(s) · "
                f"{len(module['imports'])} import(s)"
            ),
            "complexity": "O(1)",
            "parameters": module["symbols"],
            "returns": None,
            "parent_id": None,
            "parent_label": None,
            "is_entry": module["is_entry"],
            "imports": module["imports"],
            "symbols": module["symbols"],
            "external_deps": module["external_deps"],
        }
        nodes.append(
            {
                "id": node_id,
                "type": module["semantic"],
                "semantic_type": module["semantic"],
                "label": data["label"],
                "code": data["code"],
                "source": data["source"],
                "file": data["file"],
                "path": data["path"],
                "module": data["module"],
                "x": position["x"],
                "y": position["y"],
                "position": dict(position),
                "line": 1,
                "language": module["language"],
                "kind": "module",
                "category": data["category"],
                "group": layer,
                "tier": tier,
                "complexity": "O(1)",
                "description": data["description"],
                "parameters": module["symbols"],
                "returns": None,
                "layer": meta["order"],
                "parent_id": None,
                "parent_label": None,
                "is_entry": module["is_entry"],
                "data": data,
            }
        )

    edge_seq = 0

    def add_edge(
        source_id: str,
        target_id: str | None,
        kind: str,
        label: str,
    ) -> bool:
        """Append one edge; returns False once the edge budget is exhausted."""
        nonlocal edge_seq
        if len(edges) >= MAX_EDGES:
            return False
        if not target_id or target_id == source_id:
            return True
        key = (source_id, target_id)
        if key in edge_seen:
            return True
        edge_seen.add(key)
        edge_seq += 1
        edges.append(
            {
                "id": f"pe{edge_seq}",
                "source": source_id,
                "target": target_id,
                "animated": False,
                "kind": kind,
                "label": label,
            }
        )
        return len(edges) < MAX_EDGES

    # ---- pass 1: resolved imports (UI -> API -> service -> data) ----
    for module in modules:
        source_id = module["id"]
        for spec in module["imports"]:
            target_id = _resolve_import(module, spec, index, modules, go_module)
            if not add_edge(source_id, target_id, "import", "imports"):
                break
        if len(edges) >= MAX_EDGES:
            break

    # ---- pass 2: unresolved specs matched by module stem or symbol ----
    # Dynamic requires, database/model references and symbol imports
    # (`from services.user import create_user`) often fail literal resolution;
    # they still produce a real edge when the tail token names a file stem or
    # an exported symbol somewhere in the tree.
    if len(edges) < MAX_EDGES:
        stem_index: dict[str, str] = {}
        symbol_index: dict[str, str] = {}
        for module in modules:
            stem = posixpath.splitext(posixpath.basename(module["relpath"]))[0].lower()
            if stem not in {"__init__", "index"}:
                stem_index.setdefault(stem, module["id"])
            for symbol in module.get("symbols") or []:
                symbol_index.setdefault(str(symbol).lower(), module["id"])

        for module in modules:
            source_id = module["id"]
            for spec in module["imports"]:
                if len(edges) >= MAX_EDGES:
                    break
                tail = _spec_tail(spec)
                if not tail:
                    continue
                target_id = stem_index.get(tail) or symbol_index.get(tail)
                if not add_edge(source_id, target_id, "reference", "references"):
                    break
            if len(edges) >= MAX_EDGES:
                break

    # ---- pass 3: database / model references ----
    # A service or controller that mentions `users`, `orders`, `invoices` ...
    # is wired to the data-layer file that owns that name, even without an
    # import statement (raw SQL, string table names, registry lookups).
    if len(edges) < MAX_EDGES:
        _DB_STOPWORDS = frozenset(
            {
                "index", "main", "base", "types", "utils", "util", "core",
                "config", "models", "model", "schema", "schemas", "entity",
                "entities", "table", "tables", "migrations", "seed", "data",
            }
        )
        db_targets: dict[str, str] = {}
        for module in modules:
            if module["layer"] != LAYER_DATA:
                continue
            stem = posixpath.splitext(posixpath.basename(module["relpath"]))[0].lower()
            if stem.startswith("_") or len(stem) < 4 or stem in _DB_STOPWORDS:
                continue
            db_targets.setdefault(stem, module["id"])

        if db_targets:
            stems = sorted(db_targets, key=len, reverse=True)
            db_re = re.compile(
                r"\b(?:" + "|".join(re.escape(stem) for stem in stems) + r")\b",
                re.IGNORECASE,
            )
            for module in modules:
                if len(edges) >= MAX_EDGES:
                    break
                if module["layer"] == LAYER_DATA:
                    continue
                content = module.get("source") or ""
                if not content:
                    continue
                hits = 0
                for match in db_re.finditer(content):
                    target_id = db_targets.get(match.group(0).lower())
                    if not add_edge(module["id"], target_id, "database", "reads"):
                        break
                    hits += 1
                    if hits >= MAX_DB_EDGES_PER_FILE:
                        break

    groups: list[dict[str, Any]] = []
    for layer in LAYER_ORDER:
        ids = group_ids_by_layer[layer]
        if not ids:
            continue
        meta = LAYER_META[layer]
        groups.append(
            {
                "id": layer,
                "label": meta["label"],
                "short": meta["short"],
                "description": meta["description"],
                "order": meta["order"],
                "tier": meta["tier"],
                "tier_label": TIER_META[meta["tier"]]["label"],
                "color": meta["color"],
                "count": len(ids),
                "nodeIds": ids,
            }
        )

    tier_ids_by_tier: dict[int, list[str]] = {tier: [] for tier in TIER_ORDER}
    tier_counts = {tier: 0 for tier in TIER_ORDER}
    for layer in LAYER_ORDER:
        tier = LAYER_META[layer]["tier"]
        tier_ids_by_tier[tier].extend(group_ids_by_layer[layer])
        tier_counts[tier] += layer_counts[layer]

    tiers: list[dict[str, Any]] = []
    for tier in TIER_ORDER:
        ids = tier_ids_by_tier[tier]
        if not ids:
            continue
        meta = TIER_META[tier]
        tiers.append(
            {
                "id": tier,
                "label": meta["label"],
                "short": meta["short"],
                "description": meta["description"],
                "color": meta["color"],
                "count": len(ids),
                "nodeIds": ids,
            }
        )

    entry_node = next((n["id"] for n in nodes if (n.get("data") or {}).get("is_entry")), None)

    return {
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "tiers": tiers,
        "language": "mixed",
        "errors": diagnostics,
        "partial": bool(diagnostics),
        "entry_node": entry_node,
        "layers": {
            layer: {"label": LAYER_META[layer]["label"], "count": layer_counts[layer]}
            for layer in LAYER_ORDER
        },
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "files": len(modules),
            "lines": lines_total,
            "diagnostics": len(diagnostics),
            "truncated": truncated,
            "language": "mixed",
            **{f"layer_{layer}": layer_counts[layer] for layer in LAYER_ORDER},
            **{f"tier_{tier}": tier_counts[tier] for tier in TIER_ORDER},
        },
        "layout": {
            "rankDir": LAYOUT_RANK_DIR,
            "rankSep": TIER_RANK_SEP,
            "nodeSep": TIER_NODE_SEP,
            "nodeWidth": LAYOUT_NODE_WIDTH,
            "nodeHeight": LAYOUT_NODE_HEIGHT,
            "maxColumns": MAX_TIER_COLUMNS,
            "tierY": {tier: tier_top[tier] for tier in TIER_ORDER if tier in tier_top},
            "fitView": {"duration": 800, "padding": 0.25, "maxZoom": 0.85},
        },
    }


def _semantic_for(layer: str) -> str:
    return {
        LAYER_PRESENTATION: "presentation",
        LAYER_API: "api",
        LAYER_SERVICE: "service",
        LAYER_DATA: "database",
        LAYER_INTEGRATION: "integration",
    }[layer]


def _module_record(relpath: str, content: str) -> dict[str, Any]:
    language = _language_for(relpath)
    lines = content.count("\n") + (0 if content.endswith("\n") else 1)
    imports = _extract_imports(relpath, content)
    symbols = _extract_symbols(relpath, content)
    external = [spec for spec in imports if not spec.startswith((".", "/", "\\"))]
    return {
        "relpath": relpath,
        "module_name": _dotted_module_name(relpath),
        "language": language,
        "layer": _classify_layer(relpath, content),
        "semantic": None,  # filled later
        "lines": lines,
        "imports": imports,
        "symbols": symbols,
        "external_deps": len(external),
        "is_entry": _is_entry_file(relpath),
        # Full file text bound to the node so the canvas, inspector and
        # sandbox all operate on the real source instead of an empty string.
        "source": _cap_source(content),
    }


def _finish_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for module in modules:
        module["semantic"] = _semantic_for(module["layer"])
    return modules


def _ingest(
    relpath: str,
    content: str,
    diagnostics: list[dict[str, Any]],
    state: dict[str, int],
) -> dict[str, Any] | None:
    state["total_bytes"] += len(content.encode("utf-8", errors="ignore"))
    if len(content.encode("utf-8", errors="ignore")) > MAX_BYTES_PER_FILE:
        diagnostics.append(
            {
                "kind": "file_too_large",
                "file": relpath,
                "message": f"Skipped {relpath} - exceeds {MAX_BYTES_PER_FILE // 1024} KiB limit.",
            }
        )
        state["skipped"] += 1
        return None
    if state["total_bytes"] > MAX_TOTAL_BYTES:
        diagnostics.append(
            {
                "kind": "payload_too_large",
                "file": relpath,
                "message": "Total payload exceeded; remaining files were skipped.",
            }
        )
        state["skipped"] += 1
        return None
    module = _module_record(relpath, content)
    state["lines_total"] += module["lines"]
    return module


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def scan_directory(root: str) -> dict[str, Any]:
    """Recursively scan ``root`` and build the architecture graph."""
    root_path = Path(root).expanduser()
    try:
        resolved = root_path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as error:
        raise ValueError(f"Project path does not exist or cannot be resolved: {error}") from error
    if not resolved.is_dir():
        raise ValueError(f"Project path is not a directory: {resolved}")
    if resolved == Path(resolved.anchor):
        raise ValueError("Refusing to scan the filesystem root.")

    diagnostics: list[dict[str, Any]] = []
    state = {"total_bytes": 0, "lines_total": 0, "skipped": 0}
    modules: list[dict[str, Any]] = []
    truncated = False
    go_module: str | None = None

    candidates: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in resolved.walk():
        # prune junk in-place so os.walk never descends into it
        dirnames[:] = [d for d in dirnames if d.lower() not in JUNK_DIRS]
        rel_dir = dirpath.relative_to(resolved) if dirpath != resolved else Path(".")
        depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
        if depth >= MAX_DEPTH:
            diagnostics.append(
                {
                    "kind": "max_depth",
                    "file": str(rel_dir).replace("\\", "/"),
                    "message": f"Max depth ({MAX_DEPTH}) reached; deeper files skipped.",
                }
            )
            dirnames[:] = []
            continue
        for filename in filenames:
            file_path = dirpath / filename
            rel = posixpath.normpath(str(file_path.relative_to(resolved)).replace("\\", "/"))
            candidates.append((rel, file_path))

    candidates.sort(key=lambda item: item[0])

    for rel, file_path in candidates:
        parts = rel.split("/")
        if _is_junk_path(parts):
            continue
        if not _is_source_file(rel):
            continue
        if posixpath.basename(rel) == "go.mod":
            continue
        if len(modules) >= MAX_FILES:
            truncated = True
            break
        try:
            raw = file_path.read_bytes()
        except OSError as error:
            diagnostics.append(
                {"kind": "unreadable", "file": rel, "message": f"Could not read file: {error}"}
            )
            state["skipped"] += 1
            continue
        if len(raw) > MAX_BYTES_PER_FILE:
            diagnostics.append(
                {"kind": "file_too_large", "file": rel, "message": f"Skipped {rel} - too large."}
            )
            state["skipped"] += 1
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="ignore")
        module = _ingest(rel, content, diagnostics, state)
        if module is not None:
            modules.append(module)

    if truncated:
        diagnostics.append(
            {
                "kind": "truncated",
                "file": None,
                "message": f"Project exceeded {MAX_FILES} source files; only the first {MAX_FILES} were analysed.",
            }
        )

    # go.mod may live at the root
    go_mod_path = resolved / "go.mod"
    if go_mod_path.is_file():
        try:
            go_module = _read_go_module({"go.mod": go_mod_path.read_text("utf-8", errors="ignore")})
        except OSError:
            go_module = None

    modules = _finish_modules(modules)
    graph = _build_graph(
        modules,
        go_module,
        diagnostics=diagnostics,
        truncated=truncated,
        lines_total=state["lines_total"],
    )
    graph["root"] = str(resolved)
    return graph


def scan_uploads(
    files: Iterable[tuple[str, bytes]],
    go_mod_content: str | None = None,
) -> dict[str, Any]:
    """Build the architecture graph from uploaded ``(relpath, content)`` pairs."""
    diagnostics: list[dict[str, Any]] = []
    state = {"total_bytes": 0, "lines_total": 0, "skipped": 0}
    modules: list[dict[str, Any]] = []

    cleaned: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for raw_name, payload in files:
        rel = _normalise_relpath(raw_name)
        if rel is None:
            diagnostics.append(
                {"kind": "bad_path", "file": str(raw_name), "message": "Illegal upload path skipped."}
            )
            continue
        parts = rel.split("/")
        if _is_junk_path(parts):
            continue
        if _depth(rel) > MAX_DEPTH:
            diagnostics.append(
                {"kind": "max_depth", "file": rel, "message": f"Deeper than {MAX_DEPTH} levels; skipped."}
            )
            continue
        if not _is_source_file(rel):
            continue
        if rel in seen:
            continue
        seen.add(rel)
        cleaned.append((rel, payload))

    cleaned.sort(key=lambda item: item[0])
    truncated = False

    for rel, payload in cleaned:
        if len(modules) >= MAX_FILES:
            truncated = True
            break
        if len(payload) > MAX_BYTES_PER_FILE:
            diagnostics.append(
                {"kind": "file_too_large", "file": rel, "message": f"Skipped {rel} - exceeds {MAX_BYTES_PER_FILE // 1024} KiB."}
            )
            state["skipped"] += 1
            continue
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            content = payload.decode("utf-8", errors="ignore")
        module = _ingest(rel, content, diagnostics, state)
        if module is not None:
            modules.append(module)

    if truncated:
        diagnostics.append(
            {
                "kind": "truncated",
                "file": None,
                "message": f"Upload exceeded {MAX_FILES} source files; only the first {MAX_FILES} were analysed.",
            }
        )

    go_module = None
    if go_mod_content:
        go_module = _read_go_module({"go.mod": go_mod_content})
    else:
        for rel, payload in cleaned:
            if posixpath.basename(rel) == "go.mod":
                try:
                    go_module = _read_go_module({"go.mod": payload.decode("utf-8", errors="ignore")})
                except Exception:  # noqa: BLE001 - best effort
                    go_module = None
                break

    modules = _finish_modules(modules)
    graph = _build_graph(
        modules,
        go_module,
        diagnostics=diagnostics,
        truncated=truncated,
        lines_total=state["lines_total"],
    )
    graph["root"] = None
    return graph


def summarize(graph: dict[str, Any]) -> dict[str, Any]:
    stats = graph.get("stats") or {}
    return {
        "files": stats.get("files", 0),
        "nodes": stats.get("nodes", 0),
        "edges": stats.get("edges", 0),
        "truncated": bool(stats.get("truncated")),
        "layers": graph.get("layers", {}),
    }


__all__ = [
    "LAYER_PRESENTATION",
    "LAYER_API",
    "LAYER_SERVICE",
    "LAYER_DATA",
    "LAYER_INTEGRATION",
    "LAYER_META",
    "TIER_ORDER",
    "TIER_META",
    "JUNK_DIRS",
    "scan_directory",
    "scan_uploads",
    "summarize",
]
