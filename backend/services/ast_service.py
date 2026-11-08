"""AxiomTrace AST parsing domain service.

Wraps the verified Python (:mod:`ast`) and JavaScript/TypeScript (structural
scanner) graph extractors behind a single service entrypoint used by the
parse controller.

Design notes:

* Python parsing is fault tolerant: a document that does not compile is
  *salvaged* by :mod:`services.parse_recovery`, which quarantines only the
  broken statements. The response then carries both the partial graph and
  a list of diagnostics instead of a hard 400.
* Every node keeps its original ``line``/``end_line`` so the inspector can
  map a node back onto the source even after recovery.
* Engine internals are ported verbatim from the battle-tested
  ``ast_parser`` module.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Iterable

from config import config
from services.parse_recovery import recover_javascript, recover_python
from services.security_scanner import scan_graph as scan_security_graph

FUNCTION = "function"
CONDITION = "condition"
LOOP = "loop"
ACTION = "action"

# Architectural roles. These are the semantic types the canvas renders and the
# inspector explains; the four legacy types above remain in use for inside-out
# control flow so existing consumers keep working.
ENTRYPOINT = "entrypoint"
SERVICE = "service"
DATABASE = "database"
UTILITY = "utility"
ERROR_HANDLER = "error_handler"

SEMANTIC_TYPES = (ENTRYPOINT, SERVICE, DATABASE, UTILITY, ERROR_HANDLER)

VALID_TYPES = (
    FUNCTION,
    CONDITION,
    LOOP,
    ACTION,
    ENTRYPOINT,
    SERVICE,
    DATABASE,
    UTILITY,
    ERROR_HANDLER,
)

# Cohesive architectural domains. Every node is assigned to exactly one so the
# canvas can render clean bands/badges instead of an undifferentiated mesh.
GROUP_ENTRYPOINT = "entrypoint"
GROUP_API = "api"
GROUP_PRESENTATION = "presentation"
GROUP_SERVICE = "service"
GROUP_DATA = "data"
GROUP_INTEGRATION = "integration"
GROUP_UTILITY = "utility"
GROUP_ERROR = "error"
GROUP_CONTROL = "control"

GROUP_META: dict[str, dict[str, Any]] = {
    GROUP_PRESENTATION: {
        "label": "Presentation / UI",
        "short": "UI",
        "description": "Views, components, pages and templates.",
        "order": 0,
        "tier": 1,
    },
    GROUP_API: {
        "label": "API & Routing",
        "short": "API",
        "description": "Controllers, routers, endpoints and middleware.",
        "order": 1,
        "tier": 2,
    },
    GROUP_ENTRYPOINT: {
        "label": "Entry Point / Controllers",
        "short": "Entry",
        "description": "Public entry points, request handlers and orchestration roots.",
        "order": 2,
        "tier": 1,
    },
    GROUP_SERVICE: {
        "label": "Business Logic / Services",
        "short": "Service",
        "description": "Domain rules, use cases and the processing pipeline.",
        "order": 3,
        "tier": 3,
    },
    GROUP_DATA: {
        "label": "Data & Storage",
        "short": "Data",
        "description": "Models, schemas, repositories and persistence operations.",
        "order": 4,
        "tier": 4,
    },
    GROUP_INTEGRATION: {
        "label": "External Integrations",
        "short": "I/O",
        "description": "HTTP calls, third-party SDKs and outbound side effects.",
        "order": 5,
        "tier": 4,
    },
    GROUP_UTILITY: {
        "label": "Utilities / Support",
        "short": "Utility",
        "description": "Small helpers, formatting and value preparation.",
        "order": 6,
        "tier": 3,
    },
    GROUP_ERROR: {
        "label": "Error Handling",
        "short": "Errors",
        "description": "Guards, raised exceptions and recovery paths.",
        "order": 7,
        "tier": 3,
    },
    GROUP_CONTROL: {
        "label": "Control Flow",
        "short": "Flow",
        "description": "Branches and loops that drive the execution path.",
        "order": 8,
        "tier": 3,
    },
}

# Canonical four-tier enterprise architecture. Tier 1: client/presentation
# (UI, views, entry points); Tier 2: API & gateway; Tier 3: business logic;
# Tier 4: data & storage (models, schemas, DBs, external connectors).
#
# Shared layout contract (mirrors frontend ``LAYOUT`` in src/config/theme.js,
# ``layoutWithDagre`` in src/hooks/useGraphLayout.js and the project scanner
# in services/project_scanner.py): strict top-to-bottom flow (rankDir TB),
# tier bands stacked with rankSep 160 between them, and cards inside a band
# wrapped into at most MAX_TIER_COLUMNS columns with nodeSep 100 - so both
# PARSE CODE and SCAN PROJECT emit the same clean multi-tier structure.
LAYOUT_RANK_DIR = "TB"
LAYOUT_NODE_WIDTH = 300
LAYOUT_NODE_HEIGHT = 150
TIER_RANK_SEP = 160
TIER_NODE_SEP = 100
MAX_TIER_COLUMNS = 6
TIER_X_ORIGIN = 80
TIER_Y_ORIGIN = 40
TIER_ORDER: tuple[int, ...] = (1, 2, 3, 4)
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

SEMANTIC_META: dict[str, dict[str, str]] = {
    ENTRYPOINT: {
        "label": "Entry Point",
        "group": GROUP_ENTRYPOINT,
        "category": "Entry Point",
    },
    SERVICE: {
        "label": "Service Logic",
        "group": GROUP_SERVICE,
        "category": "Service Logic",
    },
    DATABASE: {
        "label": "Data Layer",
        "group": GROUP_DATA,
        "category": "Data Layer",
    },
    UTILITY: {
        "label": "Utility",
        "group": GROUP_UTILITY,
        "category": "Utility",
    },
    ERROR_HANDLER: {
        "label": "Error Handling",
        "group": GROUP_ERROR,
        "category": "Error Handling",
    },
}

_ENTRYPOINT_TOKENS = frozenset({
    "main", "run", "start", "bootstrap", "entry", "entrypoint", "cli",
    "handle", "handler", "endpoint", "route", "router", "controller",
    "view", "views", "api", "gateway", "dispatch", "serve", "server",
    "listen", "app", "application", "invoke",
})
_SERVICE_TOKENS = frozenset({
    "service", "services", "logic", "usecase", "usecases", "case", "manager",
    "engine", "processor", "worker", "pipeline", "workflow", "orchestrator",
    "coordinator", "facade", "domain", "core", "feature", "command",
    "consumer", "producer", "scheduler", "job", "task", "resolver",
    "transformer", "aggregator", "builder",
})
_DATABASE_TOKENS = frozenset({
    "db", "database", "databases", "sql", "sqlite", "postgres", "postgresql",
    "mysql", "mongo", "mongodb", "redis", "cache", "dao", "repository",
    "repo", "repository", "model", "models", "schema", "schemas", "entity",
    "entities", "table", "tables", "store", "storage", "persist", "persistence",
    "record", "records", "ledger", "transaction", "transactions", "query",
    "queries", "migration", "warehouse", "snapshot", "archive", "collection",
})
_ERROR_TOKENS = frozenset({
    "error", "errors", "exception", "exceptions", "fail", "failure", "catch",
    "handler", "fallback", "retry", "guard", "invalid", "validationerror",
    "notfound", "unauthorized", "forbidden", "timeout", "raise", "throws",
})
_UTILITY_TOKENS = frozenset({
    "util", "utils", "utility", "helper", "helpers", "format", "formatter",
    "parse", "parser", "normalize", "normalizer", "serialize", "serializer",
    "deserialize", "convert", "converter", "encode", "decode", "sanitize",
    "clip", "truncate", "slug", "to", "from", "coerce", "validate_shape",
    "collect", "selector", "mapper", "adapter", "constant", "constants",
})
_INTEGRATION_TOKENS = frozenset({
    "http", "https", "api", "client", "fetch", "request", "requests", "axios",
    "httpx", "aiohttp", "webhook", "upload", "download", "sdk", "rpc", "grpc",
    "graphql", "socket", "email", "sms", "payment", "gateway", "stripe",
})
_HTTP_CLIENT_TOKENS = frozenset({
    "requests", "httpx", "aiohttp", "urllib", "axios", "fetch", "got", "superagent",
    "client", "session", "http", "rest", "webclient", "apiclient",
})
_DB_RECEIVER_TOKENS = frozenset({
    "db", "database", "cursor", "conn", "connection", "session", "engine",
    "redis", "cache", "collection", "table", "repo", "repository", "store",
    "sqlite3", "sqlalchemy", "mongo", "mongodb", "prisma", "queryset",
})
_DB_OPERATION_TOKENS = frozenset({
    "query", "execute", "executemany", "fetchall", "fetchone", "fetchmany",
    "find", "findone", "find_one", "insert", "insertmany", "insert_one",
    "upsert", "save", "update", "delete", "aggregate", "commit", "rollback",
    "count", "create", "create_all", "bulk_save", "get", "filter", "all",
})
_LOG_TOKENS = frozenset({
    "log", "logger", "logging", "print", "console", "debug", "info", "warn",
    "warning", "error", "trace", "report",
})


def _word_tokens(name: str | None) -> list[str]:
    """Split ``processPayment`` / ``process_payment`` into lower-case words."""
    words: list[str] = []
    for chunk in re.split(r"[^0-9A-Za-z]+", str(name or "")):
        if not chunk:
            continue
        words.extend(
            part.lower()
            for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk)
        )
    return words


def _humanize(name: str | None) -> str:
    words = _word_tokens(name)
    return " ".join(words) if words else str(name or "").strip()


def _first_sentence(text: str | None, limit: int = 180) -> str:
    """Return a clean single-sentence summary from a docstring/comment."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ""
    match = re.search(r"(.+?[.!?])(?:\s|$)", normalized)
    sentence = match.group(1) if match else normalized
    return _clip(sentence, limit).strip()


def _describe_symbol(
    name: str | None,
    kind: str,
    *,
    doc: str | None = None,
    returns: str | None = None,
    parent: str | None = None,
) -> str:
    """Build a human-readable explanation for the inspector."""
    documented = _first_sentence(doc)
    if documented:
        return documented

    human = _humanize(name) or kind or "symbol"
    words = _word_tokens(name)
    verb = ""
    if words:
        verb = {
            "get": "Retrieves", "fetch": "Fetches", "load": "Loads",
            "read": "Reads", "query": "Queries", "find": "Looks up",
            "save": "Persists", "store": "Stores", "persist": "Persists",
            "write": "Writes", "create": "Creates", "insert": "Inserts",
            "update": "Updates", "delete": "Deletes", "remove": "Removes",
            "process": "Processes", "handle": "Handles", "run": "Runs",
            "start": "Starts", "execute": "Executes", "build": "Builds",
            "validate": "Validates", "verify": "Verifies", "check": "Checks",
            "parse": "Parses", "transform": "Transforms", "convert": "Converts",
            "format": "Formats", "normalize": "Normalises", "render": "Renders",
            "send": "Sends", "publish": "Publishes", "notify": "Notifies",
            "main": "Coordinates", "init": "Initialises", "setup": "Configures",
            "configure": "Configures", "register": "Registers",
            "authenticate": "Authenticates", "authorize": "Authorises",
            "calculate": "Calculates", "compute": "Computes", "sum": "Sums",
            "pay": "Processes payment for", "refund": "Refunds",
            "commit": "Commits", "rollback": "Rolls back",
            "dispatch": "Dispatches", "route": "Routes",
            "collect": "Collects", "aggregate": "Aggregates",
            "cache": "Caches", "resolve": "Resolves", "sync": "Synchronises",
        }.get(words[0], "")
    rest = " ".join(words[1:]) if verb else human
    if verb:
        sentence = f"{verb} {rest}".strip()
    elif kind in {"class", "service", "database"}:
        sentence = f"Encapsulates the {human} domain behaviour."
    elif kind in {"condition", "if", "else", "else-if", "switch", "try"}:
        sentence = f"Branches the execution flow for {human}."
    elif kind in {"loop", "for", "while", "do"}:
        sentence = f"Iterates while executing {human}."
    elif kind in {"raise", "throw", "except", "catch", "finally"}:
        sentence = f"Handles an error path for {human}."
    else:
        sentence = f"Supports the {human} step."
    if parent and kind in {"function", "method", "arrow", "constructor"}:
        sentence = f"{sentence} Part of {parent}."
    if returns and returns not in {"None", "void", "undefined"}:
        sentence = f"{sentence} Returns {returns}."
    return _clip(sentence, 220)


def _classify_semantic(
    name: str | None,
    *,
    kind: str = "function",
    parent_name: str | None = None,
    parent_semantic: str | None = None,
    decorators: Iterable[str] | None = None,
    has_db_call: bool = False,
    has_http_call: bool = False,
) -> str:
    """Resolve the architectural role for a symbol.

    The rules are intentionally ordered: explicit framework decorators win,
    then surrounding container semantics, then the identifier vocabulary, and
    finally the observed side effects. The result is always one of the five
    semantic types so the renderer never receives an unknown badge.
    """
    words = set(_word_tokens(name))
    parent_words = set(_word_tokens(parent_name))
    decorator_text = " ".join(decorators or ()).lower()

    if any(
        marker in decorator_text
        for marker in ("route", "get(", "post(", "put(", "patch(", "delete(",
                       "websocket", "webhook", "controller", "endpoint")
    ):
        return ENTRYPOINT

    if kind in {"raise", "throw", "except", "catch", "finally"}:
        return ERROR_HANDLER

    if kind in {"error_handler", "error"}:
        return ERROR_HANDLER

    if has_db_call or (words & _DATABASE_TOKENS) or (parent_words & _DATABASE_TOKENS):
        return DATABASE

    if words & _ERROR_TOKENS:
        return ERROR_HANDLER

    if has_http_call or (words & _INTEGRATION_TOKENS):
        return SERVICE if parent_semantic == SERVICE else UTILITY

    if words & _ENTRYPOINT_TOKENS:
        return ENTRYPOINT

    if parent_words & _ENTRYPOINT_TOKENS or parent_semantic == ENTRYPOINT:
        return SERVICE

    if words & _SERVICE_TOKENS or (parent_words & _SERVICE_TOKENS):
        return SERVICE

    if words & _UTILITY_TOKENS:
        return UTILITY

    if kind in {"class", "service"}:
        return SERVICE
    if kind in {"database", "model", "repository"}:
        return DATABASE
    if kind in {"function", "method", "arrow", "constructor"}:
        return SERVICE
    if kind in {"condition", "loop", "return", "break", "continue", "assign"}:
        return UTILITY
    return UTILITY


def _group_for_semantic(semantic_type: str, *, kind: str = "") -> str:
    if kind in {"condition", "loop", "break", "continue", "return"}:
        return GROUP_CONTROL
    if semantic_type in SEMANTIC_META:
        return SEMANTIC_META[semantic_type]["group"]
    return GROUP_UTILITY


def _category_for(semantic_type: str, group: str, kind: str | None = None) -> str:
    if group == GROUP_INTEGRATION:
        return "External Integration"
    if kind in {"condition", "loop", "break", "continue"}:
        return "Control Flow"
    if kind == "return":
        return "Exit Path"
    return SEMANTIC_META.get(semantic_type, SEMANTIC_META[UTILITY])["category"]


def _complexity_label(loop_depth: int, *, recursive: bool = False) -> str:
    if loop_depth <= 0:
        return "O(N)" if recursive else "O(1)"
    if recursive:
        return "O(N log N)"
    if loop_depth == 1:
        return "O(N)"
    if loop_depth == 2:
        return "O(N\u00b2)"
    return f"O(N^{loop_depth})"


def _build_groups(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate nodes into the ordered architectural domains."""
    buckets: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        group = str(data.get("group") or node.get("group") or GROUP_UTILITY)
        buckets.setdefault(group, []).append(str(node.get("id") or ""))

    groups: list[dict[str, Any]] = []
    for group_id, meta in sorted(
        GROUP_META.items(), key=lambda item: item[1].get("order", 99)
    ):
        node_ids = [node_id for node_id in buckets.get(group_id, []) if node_id]
        if not node_ids:
            continue
        groups.append(
            {
                "id": group_id,
                "label": meta["label"],
                "short": meta["short"],
                "description": meta["description"],
                "order": meta["order"],
                "tier": meta.get("tier", 3),
                "tier_label": TIER_META[meta.get("tier", 3)]["label"],
                "count": len(node_ids),
                "nodeIds": node_ids,
            }
        )
    return groups


def _build_tiers(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate nodes into the four ordered enterprise swimlane tiers."""
    buckets: dict[int, list[str]] = {tier: [] for tier in TIER_META}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        tier = data.get("tier") or node.get("tier")
        if tier is None:
            group = str(data.get("group") or node.get("group") or GROUP_UTILITY)
            tier = GROUP_META.get(group, GROUP_META[GROUP_UTILITY]).get("tier", 3)
        try:
            tier_int = int(tier)
        except (TypeError, ValueError):
            tier_int = 3
        if tier_int not in TIER_META:
            tier_int = 3
        node_id = str(node.get("id") or "")
        if node_id:
            buckets[tier_int].append(node_id)

    tiers: list[dict[str, Any]] = []
    for tier in sorted(buckets):
        node_ids = buckets[tier]
        if not node_ids:
            continue
        meta = TIER_META[tier]
        tiers.append(
            {
                "id": tier,
                "label": meta["label"],
                "short": meta["short"],
                "description": meta["description"],
                "color": meta["color"],
                "count": len(node_ids),
                "nodeIds": node_ids,
            }
        )
    return tiers


_JS_KEYWORD_METHODS = {
    "if", "for", "while", "switch", "catch", "return", "function", "constructor",
    "do", "else", "try", "finally", "typeof", "new", "await", "async", "yield",
    "super", "this", "import", "export", "class", "delete", "in", "of", "instanceof",
}


# ----------------------------------------------------------------------
# Generic graph container
# ----------------------------------------------------------------------

class GraphBuilder:
    """Accumulates nodes/edges and layers them into architectural domains."""

    def __init__(self, language: str, source: str) -> None:
        self.language = language
        self.source = source
        self.lines = source.splitlines()
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._node_seq = 0
        self._edge_seq = 0
        self._column_cursor: dict[int, int] = {}
        self._tier_cursors: dict[int, int] = {}
        self._node_index: dict[str, dict[str, Any]] = {}
        self._max_depth = 0

    # -- layout -------------------------------------------------------
    def next_position(self, depth: int) -> dict[str, int]:
        """Legacy depth-based position (kept for diagnostics plumbing)."""
        column = self._column_cursor.get(depth, 0)
        self._column_cursor[depth] = column + 1
        self._max_depth = max(self._max_depth, depth)
        return {
            "x": 80 + max(depth, 0) * config.LAYOUT_X_GAP,
            "y": 80 + column * config.LAYOUT_Y_GAP,
        }

    def next_tier_position(self, tier: int, depth: int = 0) -> dict[str, int]:
        """Provisional swimlane slot for one enterprise tier.

        Nodes arrive in source order, so the four band tops are not known
        while parsing. Each node claims the next slot inside its tier -
        columns wrap at ``MAX_TIER_COLUMNS`` with ``TIER_NODE_SEP`` between
        cards and ``TIER_RANK_SEP`` between wrapped rows. ``payload()`` then
        runs :meth:`_stack_tier_bands` to apply the final top-to-bottom band
        offsets of the shared TB layout contract.
        """
        try:
            safe_tier = int(tier)
        except (TypeError, ValueError):
            safe_tier = 3
        if safe_tier not in TIER_META:
            safe_tier = 3
        slot = self._tier_cursors.get(safe_tier, 0)
        self._tier_cursors[safe_tier] = slot + 1
        self._max_depth = max(self._max_depth, max(int(depth), 0))
        col = slot % MAX_TIER_COLUMNS
        row = slot // MAX_TIER_COLUMNS
        return {
            "x": TIER_X_ORIGIN + col * (LAYOUT_NODE_WIDTH + TIER_NODE_SEP),
            "y": TIER_Y_ORIGIN + row * (LAYOUT_NODE_HEIGHT + TIER_RANK_SEP),
        }

    def _stack_tier_bands(self) -> dict[int, int]:
        """Restack every node into four top-to-bottom tier bands.

        Mirrors ``layoutWithDagre`` on the canvas: bands are separated by a
        full ``TIER_RANK_SEP`` gap (T1 Client at the top ... T4 Data at the
        bottom), and cards inside a band keep a deterministic order (layer,
        then label) while wrapping into columns of at most
        ``MAX_TIER_COLUMNS``. Overwrites the provisional slot coordinates in
        place and returns the band-top y per populated tier.
        """
        by_tier: dict[int, list[dict[str, Any]]] = {tier: [] for tier in TIER_ORDER}
        for node in self.nodes:
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            raw_tier = node.get("tier", data.get("tier", 3))
            try:
                tier = int(raw_tier)
            except (TypeError, ValueError):
                tier = 3
            if tier not in by_tier:
                tier = 3
            by_tier[tier].append(node)

        tier_top: dict[int, int] = {}
        cursor_y = TIER_Y_ORIGIN
        for tier in TIER_ORDER:
            members = by_tier[tier]
            if not members:
                continue
            members.sort(
                key=lambda n: (
                    int(n.get("layer") if n.get("layer") is not None else 99),
                    str(n.get("label") or n.get("id") or ""),
                )
            )
            tier_top[tier] = cursor_y
            rows = -(-len(members) // MAX_TIER_COLUMNS)
            for slot, node in enumerate(members):
                col = slot % MAX_TIER_COLUMNS
                row = slot // MAX_TIER_COLUMNS
                x = TIER_X_ORIGIN + col * (LAYOUT_NODE_WIDTH + TIER_NODE_SEP)
                y = cursor_y + row * (LAYOUT_NODE_HEIGHT + TIER_RANK_SEP)
                node["x"] = x
                node["y"] = y
                node["position"] = {"x": x, "y": y}
            band_height = rows * LAYOUT_NODE_HEIGHT + (rows - 1) * TIER_RANK_SEP
            cursor_y += band_height + TIER_RANK_SEP
        return tier_top

    # -- nodes --------------------------------------------------------
    def add_node(
        self,
        node_type: str,
        label: str,
        code: str,
        depth: int,
        line: int | None = None,
        end_line: int | None = None,
        kind: str | None = None,
        is_async: bool = False,
        *,
        semantic_type: str | None = None,
        category: str | None = None,
        group: str | None = None,
        description: str | None = None,
        parameters: Iterable[str] | None = None,
        returns: str | None = None,
        complexity: str | None = None,
        parent_id: str | None = None,
        parent_label: str | None = None,
        decorators: Iterable[str] | None = None,
        is_entry: bool = False,
    ) -> str:
        """Add one enriched node.

        ``node_type`` is either a semantic role (``service``/``database``/...)
        or one of the legacy control-flow types. The rich metadata is written
        to both the node and its ``data`` payload so every consumer - canvas,
        inspector, export, persistence - receives the explanation.
        """
        if node_type not in VALID_TYPES:
            node_type = ACTION

        semantic = semantic_type if semantic_type in SEMANTIC_TYPES else None
        if semantic is None:
            semantic = node_type if node_type in SEMANTIC_TYPES else UTILITY

        # A function whose role is known is published as that semantic role;
        # control-flow nodes keep their fine-grained type so the renderer can
        # still draw branch/loop affordances.
        public_type = node_type
        if node_type == FUNCTION and semantic in SEMANTIC_TYPES:
            public_type = semantic
        elif node_type in SEMANTIC_TYPES:
            public_type = semantic

        group_id = group or _group_for_semantic(semantic, kind=kind or node_type)
        category_label = category or _category_for(semantic, group_id, kind)
        complexity_label = complexity or "O(1)"
        parameter_list = [
            str(parameter) for parameter in (parameters or []) if str(parameter).strip()
        ]
        return_label = str(returns).strip() if returns else None
        clean_label = _clip(" ".join(str(label).split()), config.NODE_LABEL_MAX_CHARS)
        clean_code = _clip(code.strip("\n"), config.NODE_CODE_MAX_CHARS)
        preview = _clip(" / ".join(l.strip() for l in clean_code.splitlines()[:2]), 140)
        summary = description or _describe_symbol(
            clean_label, kind or public_type, returns=return_label
        )

        self._node_seq += 1
        node_id = f"n{self._node_seq}"
        node_kind = kind or public_type
        layer = GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY])["order"]
        tier = GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY]).get("tier", 3)
        tier_meta = TIER_META.get(tier, TIER_META[3])
        # Strict layered coordinates: distribute into 4 swimlanes at
        # y = 0 / 150 / 300 / 450 with generous x spacing (nodeSep 90).
        position = self.next_tier_position(tier, depth)

        metadata: dict[str, Any] = {
            "id": node_id,
            "type": public_type,
            "semantic_type": semantic,
            "label": clean_label or public_type,
            "code": clean_code,
            "preview": preview,
            "line": line,
            "end_line": end_line,
            "kind": node_kind,
            "async": bool(is_async),
            "language": self.language,
            "status": "idle",
            "category": category_label,
            "group": group_id,
            "group_label": GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY])["label"],
            "tier": tier,
            "tier_label": tier_meta["label"],
            "layer": layer,
            "description": summary,
            "complexity": complexity_label,
            "parameters": parameter_list,
            "returns": return_label,
            "parent_id": parent_id,
            "parent_label": parent_label,
            "decorators": [str(item) for item in (decorators or [])],
            "is_entry": bool(is_entry),
        }

        node: dict[str, Any] = {
            "id": node_id,
            "type": public_type,
            "semantic_type": semantic,
            "label": clean_label or public_type,
            "code": clean_code,
            "x": position["x"],
            "y": position["y"],
            "position": position,
            "line": line,
            "end_line": end_line,
            "language": self.language,
            "kind": node_kind,
            "async": bool(is_async),
            "category": category_label,
            "group": group_id,
            "complexity": complexity_label,
            "description": summary,
            "parameters": parameter_list,
            "returns": return_label,
            "layer": layer,
            "parent_id": parent_id,
            "parent_label": parent_label,
            "is_entry": bool(is_entry),
            "data": metadata,
        }
        node["group_label"] = GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY])["label"]
        self.nodes.append(node)
        self._node_index[node_id] = node
        return node_id

    def get_node(self, node_id: str) -> dict[str, Any]:
        """Return a live reference to a stored node (mutating it updates output)."""
        node = self._node_index.get(node_id)
        if node is None:
            raise KeyError(f"Unknown node id '{node_id}'")
        return node

    def mark_entry(self, node_id: str) -> None:
        """Flag a node as the graph entry point (first executable root)."""
        node = self.get_node(node_id)
        node["is_entry"] = True
        node["data"]["is_entry"] = True

    # -- diagnostics --------------------------------------------------
    def add_diagnostic(
        self,
        label: str,
        code: str,
        line: int,
        end_line: int,
        message: str,
        kind: str = "syntax_error",
    ) -> str:
        """Add a non-executable marker describing a problem in the source.

        Diagnostics live in their own column to the right of the graph so
        they never overlap the nodes extracted from the valid code.
        """
        self._node_seq += 1
        node_id = f"n{self._node_seq}"
        depth = self._max_depth + 1
        position = self.next_position(depth)

        clean_label = _clip(" ".join(str(label).split()), config.NODE_LABEL_MAX_CHARS)
        clean_code = _clip(code.strip("\n"), config.NODE_CODE_MAX_CHARS)

        node: dict[str, Any] = {
            "id": node_id,
            "type": ACTION,
            "semantic_type": ERROR_HANDLER,
            "label": clean_label or "syntax error",
            "code": clean_code,
            "x": position["x"],
            "y": position["y"],
            "position": position,
            "line": line,
            "end_line": end_line,
            "language": self.language,
            "kind": kind,
            "async": False,
            "category": "Parser Diagnostics",
            "group": GROUP_ERROR,
            "complexity": "O(1)",
            "description": str(message or "The parser recovered this region."),
            "parameters": [],
            "returns": None,
            "data": {
                "id": node_id,
                "type": ACTION,
                "semantic_type": ERROR_HANDLER,
                "label": clean_label or "syntax error",
                "code": clean_code,
                "preview": _clip(clean_code, 140),
                "line": line,
                "end_line": end_line,
                "kind": kind,
                "async": False,
                "language": self.language,
                "status": "error",
                "category": "Parser Diagnostics",
                "group": GROUP_ERROR,
                "group_label": GROUP_META[GROUP_ERROR]["label"],
                "layer": GROUP_META[GROUP_ERROR]["order"],
                "complexity": "O(1)",
                "description": str(message or "The parser recovered this region."),
                "parameters": [],
                "returns": None,
                "decorators": [],
                "is_entry": False,
                "diagnostic": True,
                "error": message,
            },
        }
        self.nodes.append(node)
        self._node_index[node_id] = node
        return node_id

    # -- edges --------------------------------------------------------
    def connect(
        self,
        source_id: str,
        target_id: str,
        animated: bool = False,
        label: str | None = None,
        kind: str = "sequence",
    ) -> None:
        if not source_id or not target_id or source_id == target_id:
            return
        if source_id not in self._node_index or target_id not in self._node_index:
            return
        for existing in self.edges:
            if existing["source"] == source_id and existing["target"] == target_id:
                if animated:
                    existing["animated"] = True
                if label and not existing.get("label"):
                    existing["label"] = label
                return

        self._edge_seq += 1
        edge: dict[str, Any] = {
            "id": f"e{self._edge_seq}",
            "source": source_id,
            "target": target_id,
            "animated": bool(animated),
            "kind": kind,
        }
        if label:
            edge["label"] = _clip(label, 32)
        self.edges.append(edge)

    # -- output -------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        counts = {node_type: 0 for node_type in VALID_TYPES}
        semantic_counts = {semantic: 0 for semantic in SEMANTIC_TYPES}
        function_like = 0
        for node in self.nodes:
            counts[node["type"]] = counts.get(node["type"], 0) + 1
            data = node.get("data") or {}
            semantic = data.get("semantic_type")
            if semantic in semantic_counts:
                semantic_counts[semantic] += 1
            if data.get("kind") in {"function", "method", "class", "arrow", "constructor", "lambda"}:
                function_like += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "functions": function_like,
            "conditions": counts[CONDITION],
            "loops": counts[LOOP],
            "actions": counts[ACTION],
            "entrypoints": semantic_counts[ENTRYPOINT],
            "services": semantic_counts[SERVICE],
            "databases": semantic_counts[DATABASE],
            "utilities": semantic_counts[UTILITY],
            "error_handlers": semantic_counts[ERROR_HANDLER],
            "async_nodes": sum(1 for node in self.nodes if node.get("async")),
            "diagnostics": sum(
                1 for node in self.nodes if (node.get("data") or {}).get("diagnostic")
            ),
            "lines": len(self.lines),
            "language": self.language,
        }

    def payload(self) -> dict[str, Any]:
        # Apply the shared TB swimlane layout before serialising so the AST
        # payload and a project-scan payload carry identical band geometry.
        tier_top = self._stack_tier_bands()
        groups = _build_groups(self.nodes)
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "groups": groups,
            "tiers": _build_tiers(self.nodes),
            "language": self.language,
            "stats": self.stats(),
            "layout": {
                "rankDir": LAYOUT_RANK_DIR,
                "rankSep": TIER_RANK_SEP,
                "nodeSep": TIER_NODE_SEP,
                "nodeWidth": LAYOUT_NODE_WIDTH,
                "nodeHeight": LAYOUT_NODE_HEIGHT,
                "maxColumns": MAX_TIER_COLUMNS,
                "tierY": {tier: tier_top[tier] for tier in sorted(tier_top)},
                "fitView": {"duration": 800, "padding": 0.25, "maxZoom": 0.85},
            },
        }


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "\u2026"


def detect_language(code: str) -> str:
    """Best-effort language sniffing (python / javascript / typescript)."""
    stripped = code.strip()
    if not stripped:
        return "python"

    js_signals = len(
        re.findall(
            r"(=>|\bconst\s+\w+\s*=|\blet\s+\w+\s*=|\bvar\s+\w+\s*=|"
            r"\bfunction\s*(?:[A-Za-z_$][\w$]*\s*)?\(|"
            r"\bclass\s+[A-Za-z_$][\w$]*|"
            r"console\.\w+\(|require\(|\bmodule\.exports\b|\bexport\s+(default\s+)?|"
            r"\bimport\s+.*\s+from\s+['\"]|;\s*$)",
            code,
            re.MULTILINE,
        )
    )
    py_signals = len(
        re.findall(
            r"(^\s*def\s+\w+\s*\(|^\s*elif\s+.+:|^\s*self\s*,|^\s*@\w+|"
            r"^\s*from\s+\S+\s+import\s+|^\s*print\s*\(|:\s*$)",
            code,
            re.MULTILINE,
        )
    )

    python_parses = False
    try:
        ast.parse(stripped)
        python_parses = True
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        python_parses = False

    if re.search(r"\b(interface\s+\w+\s*\{|:\s*(string|number|boolean|any|void)\b|"
                 r"<\w+>\s*\(|as\s+const\b|enum\s+\w+\s*\{)", code):
        return "typescript"

    if py_signals > js_signals or (py_signals > 0 and js_signals == 0):
        return "python"
    if python_parses and js_signals == 0:
        return "python"
    # With no JavaScript signal at all the safest default is Python: the
    # Python builder has a recovery path, so a malformed snippet surfaces a
    # diagnostic instead of silently disappearing through the JS scanner.
    if js_signals == 0:
        return "python"
    return "javascript"


def _slice_lines(source: str, start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    lines = source.splitlines()
    first = max(1, start)
    last = max(first, end or start)
    return "\n".join(lines[first - 1 : last])


# ----------------------------------------------------------------------
# Python (ast) parsing
# ----------------------------------------------------------------------

def _py_segment(code: str, node: ast.AST) -> str:
    try:
        segment = ast.get_source_segment(code, node)
    except Exception:
        segment = None
    if segment:
        return segment
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None) or lineno
    if lineno is None:
        return ""
    return _slice_lines(code, lineno, end_lineno)


def _py_expr(node: ast.AST | None, limit: int = 60) -> str:
    if node is None:
        return ""
    try:
        return _clip(ast.unparse(node), limit)
    except Exception:
        return _clip(getattr(node, "id", None) or type(node).__name__, limit)


def _py_call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return _py_expr(func, 40)


def _py_stmt_label(stmt: ast.stmt, limit: int | None = None) -> str:
    max_chars = limit or config.NODE_LABEL_MAX_CHARS
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(stmt, ast.AsyncFunctionDef) else "def"
        return _clip(f"{prefix} {stmt.name}()", max_chars)
    if isinstance(stmt, ast.ClassDef):
        return _clip(f"class {stmt.name}", max_chars)
    if isinstance(stmt, ast.If):
        return _clip(f"if {_py_expr(stmt.test)}", max_chars)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _clip(
            f"for {_py_expr(stmt.target, 24)} in {_py_expr(stmt.iter, 30)}", max_chars
        )
    if isinstance(stmt, ast.While):
        return _clip(f"while {_py_expr(stmt.test)}", max_chars)
    if isinstance(stmt, ast.Try):
        return "try"
    if isinstance(stmt, ast.ExceptHandler):
        return _clip(f"except {_py_expr(stmt.type) or 'Exception'}", max_chars)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        targets = ", ".join(
            _py_expr(getattr(item, "optional_vars", None), 24) for item in stmt.items
        )
        return _clip(f"with {targets}".strip(), max_chars) or "with"
    if isinstance(stmt, ast.Return):
        return _clip(f"return {_py_expr(stmt.value)}".rstrip(), max_chars)
    if isinstance(stmt, ast.Assign):
        targets = ", ".join(_py_expr(t, 24) for t in stmt.targets)
        return _clip(f"{targets} = {_py_expr(stmt.value, 36)}", max_chars)
    if isinstance(stmt, ast.AnnAssign):
        target = _py_expr(stmt.target, 24)
        annotation = _py_expr(stmt.annotation, 24)
        value = _py_expr(stmt.value, 24)
        return _clip(f"{target}: {annotation} = {value}".rstrip(), max_chars)
    if isinstance(stmt, ast.AugAssign):
        target = _py_expr(stmt.target, 24)
        op = type(stmt.op).__name__
        return _clip(f"{target} {op} {_py_expr(stmt.value, 30)}", max_chars)
    if isinstance(stmt, ast.Expr):
        value = stmt.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            doc = " ".join(str(value.value).split())
            return _clip(f'docstring "{doc}"', max_chars)
        if isinstance(value, ast.Call):
            return _clip(f"call {_py_call_name(value)}(...)", max_chars)
        if isinstance(value, ast.Await):
            return _clip(f"await {_py_expr(value.value, 48)}", max_chars)
        return _clip(_py_expr(value, max_chars), max_chars)
    if isinstance(stmt, ast.Import):
        names = ", ".join(alias.name for alias in stmt.names)
        return _clip(f"import {names}", max_chars)
    if isinstance(stmt, ast.ImportFrom):
        names = ", ".join(alias.name for alias in stmt.names)
        return _clip(f"from {stmt.module or ''} import {names}", max_chars)
    if isinstance(stmt, ast.Raise):
        return _clip(f"raise {_py_expr(stmt.exc)}".rstrip(), max_chars)
    if isinstance(stmt, ast.Assert):
        return _clip(f"assert {_py_expr(stmt.test)}", max_chars)
    if isinstance(stmt, ast.Delete):
        return _clip(
            "del " + ", ".join(_py_expr(t, 20) for t in stmt.targets), max_chars
        )
    if isinstance(stmt, ast.Break):
        return "break"
    if isinstance(stmt, ast.Continue):
        return "continue"
    if isinstance(stmt, ast.Pass):
        return "pass"
    try:
        return _clip(ast.unparse(stmt), max_chars)
    except Exception:
        return _clip(type(stmt).__name__, max_chars)


def _py_stmt_kind(stmt: ast.stmt) -> str:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    if isinstance(stmt, ast.ClassDef):
        return "class"
    if isinstance(stmt, ast.If):
        return "if"
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return "for"
    if isinstance(stmt, ast.While):
        return "while"
    if isinstance(stmt, ast.Try):
        return "try"
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return "with"
    if isinstance(stmt, ast.Return):
        return "return"
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return "assign"
    if isinstance(stmt, ast.Expr):
        if isinstance(stmt.value, ast.Call):
            return "call"
        if _contains_await(stmt):
            return "await"
        return "expr"
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return "import"
    if isinstance(stmt, ast.Raise):
        return "raise"
    if isinstance(stmt, ast.Assert):
        return "assert"
    if isinstance(stmt, ast.Break):
        return "break"
    if isinstance(stmt, ast.Continue):
        return "continue"
    return type(stmt).__name__.lower()


def _contains_await(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Await):
            return True
    return False


def _is_async_stmt(stmt: ast.stmt) -> bool:
    if isinstance(stmt, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
        return True
    return _contains_await(stmt)


# ----------------------------------------------------------------------
# Python semantic enrichment helpers
# ----------------------------------------------------------------------

def _py_docstring(node: ast.AST) -> str:
    body = getattr(node, "body", None)
    if not body:
        return ""
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        value = first.value.value
        if isinstance(value, str):
            return value
    return ""


def _py_arg_label(argument: ast.arg, default: ast.AST | None = None) -> str:
    label = argument.arg
    annotation = _py_expr(argument.annotation, 40) if argument.annotation else ""
    if annotation:
        label = f"{label}: {annotation}"
    if default is not None:
        label = f"{label} = {_py_expr(default, 30)}"
    return _clip(label, 72)


def _py_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.AST | None] = [None] * (
        len(positional) - len(args.defaults)
    ) + list(args.defaults)
    parameters: list[str] = []
    for argument, default in zip(positional, defaults):
        if argument.arg in {"self", "cls"}:
            continue
        parameters.append(_py_arg_label(argument, default))
    if args.vararg:
        parameters.append(f"*{_py_arg_label(args.vararg)}")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults):
        parameters.append(_py_arg_label(argument, default))
    if args.kwarg:
        parameters.append(f"**{_py_arg_label(args.kwarg)}")
    return parameters


def _py_returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if node.returns is not None:
        rendered = _py_expr(node.returns, 60)
        return rendered or None
    found: list[str] = []

    def visit(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Return) and child.value is not None:
                rendered = _py_expr(child.value, 40)
                if rendered and rendered not in found:
                    found.append(rendered)
            visit(child)

    visit(node)
    if not found:
        return "None" if not any(isinstance(c, ast.Return) for c in ast.walk(node)) else None
    if len(found) == 1:
        return found[0]
    return f"{found[0]} | ..." if len(found) > 1 else None


def _py_loop_depth(node: ast.AST, depth: int = 0) -> int:
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
            deepest = max(deepest, _py_loop_depth(child, depth + 1))
        else:
            deepest = max(deepest, _py_loop_depth(child, depth))
    return deepest


def _py_is_recursive(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    name = node.name
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            path = _dotted_path(child.func)
            if path == name or path.endswith(f".{name}"):
                return True
    return False


def _py_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return _complexity_label(_py_loop_depth(node), recursive=_py_is_recursive(node))


def _py_decorators(node: ast.AST) -> list[str]:
    decorators = getattr(node, "decorator_list", None) or []
    return [_py_expr(decorator, 80) for decorator in decorators if decorator is not None]


def _py_contains_call(node: ast.AST | None) -> bool:
    if node is None:
        return False
    return any(isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)) for child in ast.walk(node))


def _py_call_effects(node: ast.AST | None) -> dict[str, bool]:
    """Detect database / HTTP / logging side effects inside an expression."""
    effects = {"db": False, "http": False, "log": False}
    if node is None:
        return effects
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        path = _dotted_path(child.func)
        parts = [part for part in path.split(".") if part]
        if not parts:
            continue
        head = parts[0].lower()
        leaf = parts[-1].lower()
        receiver = next(
            (part.lower() for part in parts[:-1] if part not in {"self", "cls"}),
            None,
        )
        if receiver in _DB_RECEIVER_TOKENS or (
            receiver is not None and leaf in _DB_OPERATION_TOKENS
        ):
            effects["db"] = True
        if head in _HTTP_CLIENT_TOKENS or leaf in _HTTP_CLIENT_TOKENS:
            effects["http"] = True
        if head in _LOG_TOKENS or (receiver in _LOG_TOKENS):
            effects["log"] = True
    return effects


def _py_is_noise_stmt(stmt: ast.stmt) -> bool:
    """Trivial syntax that adds no architectural meaning."""
    if isinstance(
        stmt,
        (ast.Pass, ast.Global, ast.Nonlocal, ast.Delete, ast.Import, ast.ImportFrom),
    ):
        return True
    if isinstance(stmt, ast.Expr):
        value = stmt.value
        if isinstance(value, ast.Constant):
            return True
        if not isinstance(value, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return True
        # Log-only calls (print/logger.info/console.log) carry no
        # architectural meaning - drop them so the canvas stays readable.
        if isinstance(value, ast.Call):
            effects = _py_call_effects(stmt)
            if effects["log"] and not effects["db"] and not effects["http"]:
                return True
    return False


def _py_is_trivial_assignment(stmt: ast.stmt) -> bool:
    """A plain value assignment: prepared data, not a graph-worthy step."""
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return False
    return not _py_contains_call(getattr(stmt, "value", None))


def _py_assignment_targets(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        targets = [stmt.target]
    else:
        targets = []
    names: list[str] = []
    for target in targets:
        rendered = _py_expr(target, 32)
        if rendered and rendered not in names:
            names.append(rendered)
    return names


def _py_emit_assignment_group(
    stmts: list[ast.stmt],
    builder: GraphBuilder,
    depth: int,
    code: str,
    context: dict[str, Any],
) -> str | None:
    """Collapse consecutive plain assignments into one tidy data node."""
    if not stmts:
        return None
    names: list[str] = []
    for stmt in stmts:
        names.extend(name for name in _py_assignment_targets(stmt) if name not in names)
    if not names:
        return None

    start = getattr(stmts[0], "lineno", None)
    end = getattr(stmts[-1], "end_lineno", None) or start
    snippet = "\n".join(
        segment.strip()
        for segment in (_py_segment(code, stmt) for stmt in stmts)
        if segment.strip()
    )
    label = "setup " + ", ".join(names[:4])
    if len(names) > 4:
        label += f" +{len(names) - 4}"
    return builder.add_node(
        node_type=UTILITY,
        label=label,
        code=snippet,
        depth=depth,
        line=start,
        end_line=end,
        kind="assignment",
        semantic_type=UTILITY,
        group=GROUP_UTILITY,
        category="Data Preparation",
        description=(
            "Prepares local values used by the surrounding block "
            f"({', '.join(names[:6])})."
        ),
        parameters=names,
        returns=None,
        complexity="O(1)",
        parent_id=context.get("parent_id"),
        parent_label=context.get("parent_label"),
    )


def _py_statement_semantics(
    stmt: ast.stmt,
    kind: str,
    context: dict[str, Any],
) -> tuple[str, str, str, str]:
    """Return ``(semantic_type, group, category, complexity)`` for a statement."""
    effects = _py_call_effects(stmt)
    name = ""
    if isinstance(stmt, ast.Return):
        return UTILITY, GROUP_CONTROL, "Exit Path", "O(1)"
    if isinstance(stmt, ast.Raise):
        return ERROR_HANDLER, GROUP_ERROR, "Error Handling", "O(1)"
    if isinstance(stmt, ast.Assert):
        return ERROR_HANDLER, GROUP_ERROR, "Guard Clause", "O(1)"
    if isinstance(stmt, ast.Try):
        return ERROR_HANDLER, GROUP_ERROR, "Error Handling", "O(1)"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        name = _py_call_name(stmt.value)
    if effects["db"]:
        return DATABASE, GROUP_DATA, "Data Layer", "O(1)"
    if effects["http"]:
        return SERVICE, GROUP_INTEGRATION, "External Integration", "O(1)"
    if effects["log"]:
        return UTILITY, GROUP_UTILITY, "Observability", "O(1)"
    semantic = _classify_semantic(
        name,
        kind=kind,
        parent_name=context.get("parent_label"),
        parent_semantic=context.get("parent_semantic"),
        has_db_call=effects["db"],
        has_http_call=effects["http"],
    )
    group = _group_for_semantic(semantic, kind=kind)
    return semantic, group, _category_for(semantic, group, kind), "O(1)"


def _py_statement_description(
    stmt: ast.stmt, label: str, kind: str, semantic: str
) -> str:
    if isinstance(stmt, ast.Return):
        value = _py_expr(stmt.value, 80)
        return f"Returns {value} to the caller." if value else "Exits the function."
    if isinstance(stmt, ast.Raise):
        value = _py_expr(stmt.exc, 80)
        return f"Raises {value} to signal a failure." if value else "Raises an exception."
    if isinstance(stmt, ast.Assert):
        return f"Guards the flow with the assertion {_py_expr(stmt.test, 80)}."
    if isinstance(stmt, ast.Assign):
        targets = ", ".join(_py_expr(target, 32) for target in stmt.targets)
        return f"Assigns {_clip(targets, 80)} from {_py_expr(stmt.value, 90)}."
    if isinstance(stmt, ast.AnnAssign):
        target = _py_expr(stmt.target, 40)
        return f"Declares and assigns {target} from {_py_expr(stmt.value, 80)}."
    if isinstance(stmt, ast.AugAssign):
        return f"Updates {_py_expr(stmt.target, 40)} in place with {_py_expr(stmt.value, 70)}."
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return (
            f"Invokes {_py_call_name(stmt.value)}() during the "
            f"{SEMANTIC_META.get(semantic, SEMANTIC_META[UTILITY])['category'].lower()} flow."
        )
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        targets = ", ".join(
            _py_expr(getattr(item, "optional_vars", None), 32) or _py_expr(item.context_expr, 32)
            for item in stmt.items
        )
        return f"Opens a managed context for {_clip(targets, 90)}."
    if isinstance(stmt, ast.Break):
        return "Exits the enclosing loop immediately."
    if isinstance(stmt, ast.Continue):
        return "Skips to the next iteration of the enclosing loop."
    return _describe_symbol(label, kind)


Head = tuple[str, str | None, bool]


def _process_py_block(
    stmts: list[ast.stmt],
    builder: GraphBuilder,
    depth: int,
    heads: list[Head],
    code: str,
    context: dict[str, Any] | None = None,
) -> list[str]:
    """Walk an ordered statement list. Returns open tail node ids.

    Trivial assignments and noise statements are folded away: consecutive
    plain value assignments become one "data preparation" node, imports and
    docstrings disappear entirely, so the canvas shows decisions and side
    effects rather than every variable token.
    """
    current_heads: list[Head] = list(heads)
    context = context or {}
    pending_assignments: list[ast.stmt] = []

    def flush_assignments() -> None:
        nonlocal current_heads
        if not pending_assignments:
            return
        node_id = _py_emit_assignment_group(
            pending_assignments, builder, depth, code, context
        )
        pending_assignments.clear()
        if not node_id:
            return
        for head_id, head_label, head_async in current_heads:
            builder.connect(
                head_id,
                node_id,
                animated=head_async,
                label=head_label,
                kind="branch" if head_label else "sequence",
            )
        current_heads = [(node_id, None, False)]

    for stmt in stmts:
        if _py_is_noise_stmt(stmt):
            continue
        if _py_is_trivial_assignment(stmt):
            pending_assignments.append(stmt)
            continue

        flush_assignments()
        result = _process_py_stmt(stmt, builder, depth, code, context)
        if result is None:
            continue
        entry, tails = result

        for head_id, head_label, head_async in current_heads:
            builder.connect(
                head_id,
                entry,
                animated=head_async or _is_async_stmt(stmt),
                label=head_label,
                kind="branch" if head_label else "sequence",
            )

        current_heads = [(tail, None, False) for tail in tails]

    flush_assignments()
    return [head[0] for head in current_heads]


def _process_py_stmt(
    stmt: ast.stmt,
    builder: GraphBuilder,
    depth: int,
    code: str,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[str]] | None:
    segment = _py_segment(code, stmt)
    label = _py_stmt_label(stmt)
    line = getattr(stmt, "lineno", None)
    end_line = getattr(stmt, "end_lineno", None)
    context = context or {}
    parent_label = context.get("parent_label")
    parent_semantic = context.get("parent_semantic")
    parent_id = context.get("parent_id")

    # ---------------- functions / classes ---------------------------
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        is_class = isinstance(stmt, ast.ClassDef)
        is_async = isinstance(stmt, ast.AsyncFunctionDef)
        kind = "class" if is_class else "function"
        decorators = _py_decorators(stmt)
        doc = _py_docstring(stmt)
        semantic = _classify_semantic(
            stmt.name,
            kind=kind,
            parent_name=parent_label,
            parent_semantic=parent_semantic,
            decorators=decorators,
        )

        if is_class:
            parameters: list[str] = []
            returns: str | None = None
            loop_depth = max(
                (
                    _py_loop_depth(item)
                    for item in stmt.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                default=0,
            )
            complexity = _complexity_label(loop_depth)
        else:
            parameters = _py_parameters(stmt)
            returns = _py_returns(stmt)
            complexity = _py_complexity(stmt)

        group = _group_for_semantic(semantic, kind=kind)
        category = _category_for(semantic, group, kind)
        description = _describe_symbol(
            stmt.name,
            kind,
            doc=doc,
            returns=returns,
            parent=parent_label if not is_class else None,
        )
        is_entry = semantic == ENTRYPOINT and not any(
            (node.get("data") or {}).get("is_entry") for node in builder.nodes
        )

        node_id = builder.add_node(
            node_type=semantic,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind=kind,
            is_async=is_async,
            semantic_type=semantic,
            category=category,
            group=group,
            description=description,
            parameters=parameters,
            returns=returns,
            complexity=complexity,
            parent_id=parent_id,
            parent_label=parent_label,
            decorators=decorators,
            is_entry=is_entry,
        )
        if is_entry:
            builder.mark_entry(node_id)

        _process_py_block(
            list(stmt.body),
            builder,
            depth + 1,
            [(node_id, None, is_async)],
            code,
            {
                "parent_id": node_id,
                "parent_label": stmt.name,
                "parent_semantic": semantic,
                "in_class": is_class,
            },
        )
        return node_id, [node_id]

    # ---------------- branch: if / elif / else ----------------------
    if isinstance(stmt, ast.If):
        node_id = builder.add_node(
            node_type=CONDITION,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind="if",
            semantic_type=UTILITY,
            group=GROUP_CONTROL,
            category="Control Flow",
            description=f"Branches the flow when {_py_expr(stmt.test, 100)}.",
            complexity="O(1)",
            parent_id=parent_id,
            parent_label=parent_label,
        )
        branch_context = {
            "parent_id": node_id,
            "parent_label": parent_label,
            "parent_semantic": parent_semantic,
        }
        has_else = bool(stmt.orelse)
        body_tails = _process_py_block(
            list(stmt.body),
            builder,
            depth + 1,
            [(node_id, "true" if has_else else None, False)],
            code,
            branch_context,
        )
        if has_else:
            else_tails = _process_py_block(
                list(stmt.orelse),
                builder,
                depth + 1,
                [(node_id, "else", False)],
                code,
                branch_context,
            )
            return node_id, list(dict.fromkeys(body_tails + else_tails))
        return node_id, list(dict.fromkeys(body_tails + [node_id]))

    # ---------------- loops ------------------------------------------
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            loop_description = (
                f"Iterates over {_py_expr(stmt.iter, 70)} "
                f"for {_py_expr(stmt.target, 40)}."
            )
        else:
            loop_description = f"Repeats while {_py_expr(stmt.test, 90)}."
        node_id = builder.add_node(
            node_type=LOOP,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind=_py_stmt_kind(stmt),
            is_async=isinstance(stmt, ast.AsyncFor),
            semantic_type=UTILITY,
            group=GROUP_CONTROL,
            category="Control Flow",
            description=loop_description,
            complexity=_complexity_label(_py_loop_depth(stmt)),
            parent_id=parent_id,
            parent_label=parent_label,
        )
        loop_context = {
            "parent_id": node_id,
            "parent_label": parent_label,
            "parent_semantic": parent_semantic,
        }
        body_tails = _process_py_block(
            list(stmt.body),
            builder,
            depth + 1,
            [(node_id, "body", False)],
            code,
            loop_context,
        )
        for tail in body_tails:
            builder.connect(tail, node_id, animated=True, kind="loop-back")

        if stmt.orelse:
            else_tails = _process_py_block(
                list(stmt.orelse),
                builder,
                depth + 1,
                [(node_id, "else", False)],
                code,
                loop_context,
            )
            return node_id, list(dict.fromkeys(else_tails))
        return node_id, [node_id]

    # ---------------- try / except / finally -------------------------
    if isinstance(stmt, ast.Try) or (
        hasattr(ast, "TryStar") and isinstance(stmt, getattr(ast, "TryStar"))
    ):
        node_id = builder.add_node(
            node_type=CONDITION,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind="try",
            semantic_type=ERROR_HANDLER,
            group=GROUP_ERROR,
            category="Error Handling",
            description=(
                "Guards a risky block and routes failures to the recovery paths."
            ),
            complexity="O(1)",
            parent_id=parent_id,
            parent_label=parent_label,
        )
        try_context = {
            "parent_id": node_id,
            "parent_label": parent_label,
            "parent_semantic": parent_semantic,
        }
        body_tails = _process_py_block(
            list(stmt.body),
            builder,
            depth + 1,
            [(node_id, "try", False)],
            code,
            try_context,
        )
        merge_heads: list[Head] = [
            (tail, None, False) for tail in body_tails
        ]
        handler_tails: list[str] = []
        for handler in stmt.handlers:
            handler_label = _py_stmt_label(handler)
            handler_entry = builder.add_node(
                node_type=CONDITION,
                label=handler_label,
                code=_py_segment(code, handler) or segment,
                depth=depth + 1,
                line=getattr(handler, "lineno", None),
                end_line=getattr(handler, "end_lineno", None),
                kind="except",
                semantic_type=ERROR_HANDLER,
                group=GROUP_ERROR,
                category="Error Handling",
                description=(
                    f"Recovers when {_py_expr(handler.type, 80) or 'an exception'} "
                    "is raised."
                ),
                complexity="O(1)",
                parent_id=node_id,
                parent_label=handler_label,
            )
            builder.connect(
                node_id, handler_entry, label="except", kind="branch"
            )
            tails = _process_py_block(
                list(handler.body),
                builder,
                depth + 2,
                [(handler_entry, None, False)],
                code,
                {
                    "parent_id": handler_entry,
                    "parent_label": handler_label,
                    "parent_semantic": ERROR_HANDLER,
                },
            )
            handler_tails.extend(tails)

        if stmt.orelse:
            else_tails = _process_py_block(
                list(stmt.orelse), builder, depth + 1, merge_heads, code, try_context
            )
            merge_heads = [(tail, None, False) for tail in else_tails]

        merge_heads.extend((tail, None, False) for tail in handler_tails)

        if stmt.finalbody:
            final_tails = _process_py_block(
                list(stmt.finalbody), builder, depth + 1, merge_heads, code, try_context
            )
            return node_id, list(dict.fromkeys(final_tails))

        resolved = list(
            dict.fromkeys([head[0] for head in merge_heads])
        )
        return node_id, resolved

    # ---------------- with --------------------------------------------
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        effects = _py_call_effects(stmt)
        semantic, group, category = (
            (DATABASE, GROUP_DATA, "Data Layer")
            if effects["db"]
            else (SERVICE, GROUP_INTEGRATION, "External Integration")
            if effects["http"]
            else (UTILITY, GROUP_UTILITY, "Resource Management")
        )
        node_id = builder.add_node(
            node_type=semantic if semantic in {DATABASE, SERVICE} else ACTION,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind="with",
            is_async=isinstance(stmt, ast.AsyncWith),
            semantic_type=semantic,
            group=group,
            category=category,
            description=_py_statement_description(stmt, label, "with", semantic),
            parameters=[
                _py_expr(getattr(item, "optional_vars", None), 40)
                for item in stmt.items
                if getattr(item, "optional_vars", None) is not None
            ],
            complexity="O(1)",
            parent_id=parent_id,
            parent_label=parent_label,
        )
        body_tails = _process_py_block(
            list(stmt.body),
            builder,
            depth + 1,
            [(node_id, None, False)],
            code,
            {
                "parent_id": node_id,
                "parent_label": parent_label,
                "parent_semantic": semantic,
            },
        )
        return node_id, list(dict.fromkeys(body_tails)) or [node_id]

    # ---------------- simple statements --------------------------------
    kind = _py_stmt_kind(stmt)
    is_async = _contains_await(stmt)
    semantic, group, category, complexity = _py_statement_semantics(
        stmt, kind, context
    )
    description = _py_statement_description(stmt, label, kind, semantic)
    parameters = _py_assignment_targets(stmt)
    returns = _py_expr(getattr(stmt, "value", None), 60) if isinstance(stmt, ast.Return) else None

    if kind == "return":
        node_id = builder.add_node(
            node_type=ACTION,
            label=label,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind="return",
            is_async=is_async,
            semantic_type=UTILITY,
            group=GROUP_CONTROL,
            category="Exit Path",
            description=description,
            returns=returns,
            complexity="O(1)",
            parent_id=parent_id,
            parent_label=parent_label,
        )
        return node_id, []

    if kind in {"break", "continue"}:
        node_id = builder.add_node(
            node_type=ACTION,
            label=kind,
            code=segment,
            depth=depth,
            line=line,
            end_line=end_line,
            kind=kind,
            semantic_type=UTILITY,
            group=GROUP_CONTROL,
            category="Control Flow",
            description=description,
            complexity="O(1)",
            parent_id=parent_id,
            parent_label=parent_label,
        )
        return node_id, []

    node_type = ACTION
    if kind in {"call", "expr", "assignment"} and semantic in {
        DATABASE,
        SERVICE,
        ENTRYPOINT,
    }:
        node_type = semantic
    elif kind in {"raise", "assert"}:
        node_type = ERROR_HANDLER

    node_id = builder.add_node(
        node_type=node_type,
        label=label,
        code=segment,
        depth=depth,
        line=line,
        end_line=end_line,
        kind=kind,
        is_async=is_async,
        semantic_type=semantic,
        category=category,
        group=group,
        description=description,
        parameters=parameters,
        complexity=complexity,
        parent_id=parent_id,
        parent_label=parent_label,
    )
    return node_id, [node_id]


def _parse_python_tree(source: str) -> ast.AST:
    """Parse Python source, normalising every ``ast.parse`` failure.

    ``ast.parse`` raises :class:`SyntaxError` for invalid source and can raise
    :class:`TypeError` / :class:`ValueError` for payloads it cannot even
    tokenize, plus :class:`RecursionError` for pathologically nested input.
    The controller only understands ``SyntaxError`` (recoverable / 400) and
    ``ValueError`` (invalid payload / 400), so everything is translated here
    instead of escaping as an opaque 500.
    """
    try:
        if not isinstance(source, str):
            raise ValueError("Python source must be a string.")
        if "\x00" in source:
            raise ValueError("Python source contains NUL bytes and cannot be parsed.")

        try:
            return ast.parse(source)
        except SyntaxError:
            raise
        except (TypeError, ValueError) as error:
            raise ValueError(f"Python parser could not read the source: {error}") from error
        except (MemoryError, RecursionError) as error:
            raise ValueError(
                "Python source is too deeply nested or too large to analyse."
            ) from error
    except (SyntaxError, ValueError):
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"Python parser failed: {error}") from error


def _build_python_graph(code: str) -> dict[str, Any]:
    """Build a graph from Python source, salvaging it when it does not compile.

    The returned payload always has the same shape. When the source could
    only be recovered, ``partial`` is ``True`` and ``errors`` lists the
    quarantined regions.
    """
    try:
        if not isinstance(code, str):
            raise ValueError("Code payload must be a string.")

        builder = GraphBuilder("python", code)
        errors: list[dict[str, Any]] = []

        try:
            tree = _parse_python_tree(code)
        except SyntaxError as error:
            if not getattr(config, "PARSE_PARTIAL_RECOVERY", True):
                raise SyntaxError(
                    f"Python syntax error at line {error.lineno}: {error.msg}"
                ) from error

            try:
                recovery = recover_python(code)
            except Exception as recovery_error:
                raise SyntaxError(
                    f"Python syntax error at line {error.lineno}: {error.msg}"
                ) from recovery_error
            if not recovery.recovered:
                raise SyntaxError(
                    f"Python syntax error at line {error.lineno}: {error.msg}"
                ) from error

            try:
                tree = _parse_python_tree(recovery.code)
            except SyntaxError as error:  # pragma: no cover - defensive
                raise SyntaxError(
                    f"Python syntax error at line {error.lineno}: {error.msg}"
                ) from error

            errors = [region.to_dict() for region in recovery.regions]

        try:
            module_tails = _process_py_block(list(tree.body), builder, 0, [], code)
        except RecursionError as error:
            raise ValueError(
                "Python source is too deeply nested to analyse."
            ) from error
        except MemoryError as error:
            raise ValueError(
                "Python source is too large to analyse."
            ) from error

        for region in errors:
            start = max(1, int(region.get("start_line") or 1))
            end = max(start, int(region.get("end_line") or start))
            snippet = "\n".join((code.splitlines())[start - 1 : end])
            builder.add_diagnostic(
                label=f"Syntax error · line {start}",
                code=snippet or f"(line {start})",
                line=start,
                end_line=end,
                message=str(region.get("message") or "invalid syntax"),
                kind=str(region.get("kind") or "syntax_error"),
            )

        payload = builder.payload()
        entry_node = next(
            (
                str(node["id"])
                for node in builder.nodes
                if (node.get("data") or {}).get("is_entry")
            ),
            None,
        )
        payload["entry_node"] = entry_node or (
            module_tails[0] if module_tails else (builder.nodes[0]["id"] if builder.nodes else None)
        )
        payload["errors"] = errors
        payload["partial"] = bool(errors)
        return payload
    except (SyntaxError, ValueError):
        raise
    except (MemoryError, RecursionError):
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"Failed to parse Python source: {error}") from error


# ----------------------------------------------------------------------
# JavaScript / TypeScript structural parsing
# ----------------------------------------------------------------------

_STRING_PATTERN = re.compile(
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"
)
_LINE_COMMENT_PATTERN = re.compile(r"//.*$")
_BLOCK_COMMENT_OPEN = re.compile(r"/\*")

_FUNCTION_RE = re.compile(
    r"^(?P<mods>(?:export\s+)?(?:default\s+)?)"
    r"(?P<async>async\s+)?function\s*(?P<name>[A-Za-z_$][\w$]*)?\s*\("
)
_CLASS_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_ARROW_RE = re.compile(
    r"^(?:export\s+)?(?P<decl>const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<async>async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_CONST_FUNCTION_RE = re.compile(
    r"^(?:export\s+)?(?P<decl>const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<async>async\s+)?function\b"
)
_METHOD_RE = re.compile(
    r"^(?P<mods>(?:public|private|protected|static|readonly|abstract|override|async|get|set)\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{?\s*$"
)
_DECLARATION_RE = re.compile(
    r"^(?P<decl>const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_ASSIGNMENT_RE = re.compile(
    r"^(?P<target>[A-Za-z_$][\w$.\[\]'\"\d]*)\s*(?P<op>[+\-*/%]?=)(?!=)"
)
_CALL_RE = re.compile(
    r"^(?P<target>(?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Za-z_$][\w$]*)\s*\("
)
_AWAIT_RE = re.compile(r"\bawait\b")
_PROMISE_RE = re.compile(r"\b(Promise|fetch|axios|setTimeout|setInterval)\b")

_JS_BLOCK_KEYWORDS = ("if", "else if", "else", "for", "while", "switch", "catch", "finally", "try", "do")


def _mask_js_lines(lines: list[str]) -> list[str]:
    """Blank out string literals and comments so braces can be counted."""
    masked: list[str] = []
    in_block_comment = False
    for line in lines:
        result: list[str] = []
        index = 0
        length = len(line)

        while index < length:
            if in_block_comment:
                end = line.find("*/", index)
                if end == -1:
                    index = length
                    continue
                in_block_comment = False
                index = end + 2
                continue

            if line.startswith("//", index):
                break

            if line.startswith("/*", index):
                end = line.find("*/", index + 2)
                if end == -1:
                    in_block_comment = True
                    index = length
                    continue
                index = end + 2
                continue

            char = line[index]
            if char in "\"'`":
                quote = char
                result.append(quote)
                index += 1
                while index < length:
                    if line[index] == "\\":
                        result.append(" ")
                        index += 2
                        continue
                    if line[index] == quote:
                        result.append(quote)
                        index += 1
                        break
                    result.append(" ")
                    index += 1
                continue

            if char == "/" and index + 1 < length and line[index + 1] == "/":
                break

            result.append(char)
            index += 1

        masked.append("".join(result))

    return masked


def _depth_tables(
    masked: list[str],
) -> tuple[list[int], list[int], list[int], list[int], list[int]]:
    depth_before: list[int] = []
    depth_after: list[int] = []
    depth_min: list[int] = []
    opens: list[int] = []
    closes: list[int] = []
    depth = 0
    for line in masked:
        open_count = line.count("{")
        close_count = line.count("}")
        start_depth = depth
        lowest = start_depth
        for char in line:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    depth = 0
            if depth < lowest:
                lowest = depth
        depth_before.append(start_depth)
        depth_after.append(depth)
        depth_min.append(lowest)
        opens.append(open_count)
        closes.append(close_count)
    return depth_before, depth_after, depth_min, opens, closes


def _js_classify(
    stripped: str, next_stripped: str, display: str | None = None
) -> dict[str, Any] | None:
    if not stripped:
        return None
    if stripped[0] in ")}];,":
        return None

    # ``stripped`` has string literals masked for structure detection while
    # ``display`` keeps the original characters for human readable labels.
    text = (display or "").strip() or stripped

    match = _FUNCTION_RE.match(stripped)
    if match:
        name = match.group("name") or "anonymous"
        is_async = bool(match.group("async"))
        return {
            "type": FUNCTION,
            "kind": "function",
            "name": name,
            "label": ("async " if is_async else "") + f"function {name}()",
            "async": is_async,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    match = _CLASS_RE.match(stripped)
    if match:
        return {
            "type": FUNCTION,
            "kind": "class",
            "name": match.group("name"),
            "label": f"class {match.group('name')}",
            "async": False,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    match = _ARROW_RE.match(stripped)
    if match:
        is_async = bool(match.group("async"))
        return {
            "type": FUNCTION,
            "kind": "arrow",
            "name": match.group("name"),
            "label": ("async " if is_async else "") + f"{match.group('name')} = () =>",
            "async": is_async,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    match = _CONST_FUNCTION_RE.match(stripped)
    if match:
        is_async = bool(match.group("async"))
        return {
            "type": FUNCTION,
            "kind": "function",
            "name": match.group("name"),
            "label": ("async " if is_async else "") + f"{match.group('name')} = function",
            "async": is_async,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    first_word = stripped.split("(", 1)[0].split(" ", 1)[0]
    if (
        stripped.startswith(("else if", "else", "if", "switch", "catch"))
        and not stripped.startswith(("elseif", "iff"))
    ):
        kind = "else-if" if stripped.startswith("else if") else first_word
        label = "else if (...)" if stripped.startswith("else if") else (
            "else" if stripped.startswith("else") else f"{first_word} (...)"
        )
        if stripped.startswith("switch"):
            label = "switch (...)"
        return {
            "type": CONDITION,
            "kind": kind if kind in {"if", "else", "else-if", "switch", "catch"} else "if",
            "name": None,
            "label": label,
            "async": False,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    if stripped.startswith(("for", "while", "do")) and not stripped.startswith(
        ("format", "forever")
    ):
        keyword = stripped.split(" ", 1)[0].split("(", 1)[0]
        if keyword in {"for", "while", "do"}:
            return {
                "type": LOOP,
                "kind": keyword,
                "name": None,
                "label": f"{keyword} (...)" if keyword != "do" else "do {}",
                "async": False,
                "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
            }

    if stripped.startswith("try") and stripped.split(" ", 1)[0] == "try":
        return {
            "type": CONDITION,
            "kind": "try",
            "name": None,
            "label": "try",
            "async": False,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    if stripped.startswith("finally") and stripped.split(" ", 1)[0] == "finally":
        return {
            "type": CONDITION,
            "kind": "finally",
            "name": None,
            "label": "finally",
            "async": False,
            "opens": stripped.rstrip().endswith("{") or next_stripped == "{",
        }

    if stripped.startswith("return"):
        return {
            "type": ACTION,
            "kind": "return",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
            "terminal": True,
        }

    if stripped.startswith("throw"):
        return {
            "type": ACTION,
            "kind": "throw",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
            "terminal": True,
        }

    if stripped.startswith("break") and not stripped.startswith("breakfast"):
        return {
            "type": ACTION,
            "kind": "break",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
            "terminal": True,
        }

    if stripped.startswith("continue") and not stripped.startswith("continued"):
        return {
            "type": ACTION,
            "kind": "continue",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
            "terminal": True,
        }

    if stripped.startswith(("import ", "export ", "require(")):
        return {
            "type": ACTION,
            "kind": "import",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
        }

    method_match = _METHOD_RE.match(stripped)
    if method_match:
        method_name = method_match.group("name")
        # Control-flow keywords also look like ``name(...) {``; only treat a
        # call-shaped line as a method when the identifier is not syntax.
        # ``get``/``set``/``static``/``async`` remain valid literal method
        # names even though they are also modifiers/keywords elsewhere.
        if (
            method_name.lower() not in _JS_KEYWORD_METHODS
            or method_name.lower() in {"constructor", "get", "set", "static", "async"}
        ):
            is_async = bool(
                re.search(r"\basync\b", stripped.split("(", 1)[0])
            )
            opens = stripped.rstrip().endswith("{") or next_stripped == "{"
            if opens:
                return {
                    "type": FUNCTION,
                    "kind": "method",
                    "name": method_name,
                    "label": ("async " if is_async else "") + f"{method_name}()",
                    "async": is_async,
                    "opens": True,
                }

    match = _DECLARATION_RE.match(stripped)
    if match:
        return {
            "type": ACTION,
            "kind": "declaration",
            "name": match.group("name"),
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": _AWAIT_RE.search(stripped) is not None,
            "opens": False,
        }

    match = _ASSIGNMENT_RE.match(stripped)
    if match:
        return {
            "type": ACTION,
            "kind": "assignment",
            "name": match.group("target"),
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": _AWAIT_RE.search(stripped) is not None,
            "opens": False,
        }

    match = _CALL_RE.match(stripped)
    if match:
        return {
            "type": ACTION,
            "kind": "call",
            "name": match.group("target"),
            "label": _clip(f"call {match.group('target')}(...)", config.NODE_LABEL_MAX_CHARS),
            "async": _AWAIT_RE.search(stripped) is not None,
            "opens": False,
        }

    if stripped.startswith("console."):
        return {
            "type": ACTION,
            "kind": "log",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": False,
            "opens": False,
        }

    if stripped[0].isalnum() or stripped[0] in "_$#@":
        return {
            "type": ACTION,
            "kind": "expression",
            "label": _clip(text, config.NODE_LABEL_MAX_CHARS),
            "async": _AWAIT_RE.search(stripped) is not None
            or _PROMISE_RE.search(stripped) is not None,
            "opens": False,
        }

    return None


def _find_block_close(
    open_line: int,
    limit: int,
    depth_before: list[int],
    depth_after: list[int],
    depth_min: list[int],
    closes: list[int],
) -> int | None:
    """Return the line index that closes the block opened on ``open_line``.

    The close line is the first line after the opener whose brace depth dips
    back to the opener depth - this correctly resolves ``} else {`` style
    terminators that immediately reopen a sibling branch.
    """
    target = depth_after[open_line]
    for index in range(open_line + 1, limit + 1):
        if closes[index] > 0 and depth_min[index] <= target - 1:
            return index
    return None


def _js_leading_doc(lines: list[str], index: int) -> str:
    """Collect a short ``//`` or JSDoc summary immediately above a symbol."""
    collected: list[str] = []
    probe = index - 1
    while probe >= 0 and len(collected) < 8:
        text = lines[probe].strip()
        if not text:
            probe -= 1
            continue
        if text.startswith("//"):
            collected.insert(0, text.lstrip("/").strip())
            probe -= 1
            continue
        if text.startswith(("/*", "*", "*/")):
            cleaned = text.lstrip("/*").rstrip("*/").strip()
            if cleaned:
                collected.insert(0, cleaned)
            probe -= 1
            continue
        break
    return _first_sentence(" ".join(collected))


def _js_parameters(text: str) -> list[str]:
    """Extract a readable parameter list from a function/method signature."""
    open_index = text.find("(")
    if open_index == -1:
        return []
    depth = 0
    close_index = -1
    for position in range(open_index, len(text)):
        char = text[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_index = position
                break
    if close_index == -1:
        return []
    raw = text[open_index + 1:close_index].strip()
    if not raw:
        return []
    parameters: list[str] = []
    depth = 0
    current = ""
    for char in raw:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            if current.strip():
                parameters.append(_clip(" ".join(current.split()), 60))
            current = ""
            continue
        current += char
    if current.strip():
        parameters.append(_clip(" ".join(current.split()), 60))
    return parameters


def _js_return_value(lines: list[str], start: int, end: int) -> str | None:
    found: list[str] = []
    for line in lines[start:end + 1]:
        match = re.search(r"\breturn\b\s+(.+?);?\s*$", line)
        if match:
            value = _clip(" ".join(match.group(1).split()), 50)
            if value and value not in found:
                found.append(value)
    if not found:
        return "void"
    if len(found) == 1:
        return found[0]
    return f"{found[0]} | ..."


def _js_complexity(lines: list[str], start: int, end: int) -> str:
    loop_count = 0
    for line in lines[start:end + 1]:
        if re.search(r"\b(for|while)\s*\(", line):
            loop_count += 1
    return _complexity_label(min(loop_count, 3))


def _js_call_effects(text: str) -> tuple[bool, bool]:
    """Return ``(touches_database, performs_http_request)`` for a line."""
    lowered = text.lower()
    db = bool(
        re.search(
            r"\b(db|database|cursor|conn|connection|repo|repository|store|"
            r"model|table|prisma|mongoose|sequelize|knex|redis|cache|"
            r"sqlalchemy|queryset)\s*[.]",
            lowered,
        )
        or re.search(r"\.(find|findOne|insertOne|insertMany|updateOne|"
                     r"aggregate|save|deleteOne|query|execute)\s*\(", lowered)
    )
    http = bool(
        re.search(r"\b(fetch|axios|requests|httpx|aiohttp|got|superagent)\b", lowered)
        or re.search(r"\bhttps?://", lowered)
    )
    return db, http


def _js_is_trivial(classified: dict[str, Any], text: str) -> bool:
    """Drop imports, logs, plain constants and bare identifiers."""
    kind = classified.get("kind")
    if kind in {"import", "log"}:
        return True
    if kind in {"declaration", "assignment"}:
        return not re.search(r"\(|\bnew\b|\bawait\b|=>|\bfunction\b", text)
    if kind == "expression":
        return not re.search(r"\(|\bawait\b", text)
    return False


def _js_semantics(
    classified: dict[str, Any],
    text: str,
    context: dict[str, Any],
) -> tuple[str, str, str]:
    """Return ``(semantic_type, group, category)`` for a JS statement."""
    kind = classified.get("kind", "")
    name = str(classified.get("name") or "")
    parent_label = context.get("parent_label")
    parent_semantic = context.get("parent_semantic")

    if kind in {"catch", "finally", "throw"}:
        return ERROR_HANDLER, GROUP_ERROR, "Error Handling"
    if kind == "try":
        return ERROR_HANDLER, GROUP_ERROR, "Error Handling"
    if kind == "return":
        return UTILITY, GROUP_CONTROL, "Exit Path"
    if kind in {"break", "continue", "if", "else", "else-if", "switch", "for", "while", "do"}:
        return UTILITY, GROUP_CONTROL, "Control Flow"

    db, http = _js_call_effects(text)
    if db:
        return DATABASE, GROUP_DATA, "Data Layer"
    if http:
        return SERVICE, GROUP_INTEGRATION, "External Integration"

    semantic = _classify_semantic(
        name or classified.get("label"),
        kind=kind or "function",
        parent_name=parent_label,
        parent_semantic=parent_semantic,
        has_db_call=db,
        has_http_call=http,
    )
    group = _group_for_semantic(semantic, kind=kind)
    return semantic, group, _category_for(semantic, group, kind)


def _js_description(
    classified: dict[str, Any],
    text: str,
    semantic: str,
    doc: str,
    returns: str | None,
    parent_label: str | None,
) -> str:
    kind = classified.get("kind", "")
    name = classified.get("name")
    if doc:
        return doc
    if kind in {"if", "else-if", "switch"}:
        return f"Branches the flow when {_clip(text.rstrip('{ '), 110)}."
    if kind == "else":
        return "Fallback branch when the primary condition is false."
    if kind in {"for", "while", "do"}:
        return f"Repeats while {_clip(text.rstrip('{ '), 110)}."
    if kind == "try":
        return "Guards a risky block and routes failures to recovery handlers."
    if kind == "catch":
        return "Recovers from a thrown error."
    if kind == "finally":
        return "Runs cleanup after the guarded block completes."
    if kind == "throw":
        return f"Raises an error from {_clip(text, 100)}."
    if kind == "return":
        return f"Returns {_clip(text.replace('return', '', 1).strip(' ;'), 100)}."
    if kind == "break":
        return "Exits the enclosing loop immediately."
    if kind == "continue":
        return "Skips to the next iteration of the enclosing loop."
    return _describe_symbol(
        name or classified.get("label"),
        kind or semantic,
        returns=returns,
        parent=parent_label,
    )


def _build_js_graph(code: str) -> dict[str, Any]:
    if not isinstance(code, str):
        raise ValueError("Code payload must be a string.")
    language = detect_language(code)
    if language == "python":
        language = "javascript"

    lines = code.splitlines()
    masked = _mask_js_lines(lines)
    depth_before, depth_after, depth_min, _opens, closes = _depth_tables(masked)
    builder = GraphBuilder(language, code)

    def next_non_empty(index: int, limit: int) -> int | None:
        probe = index
        while probe <= limit:
            if masked[probe].strip():
                return probe
            probe += 1
        return None

    def process_range(
        start: int,
        end: int,
        depth: int,
        heads: list[Head],
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        current_heads: list[Head] = list(heads)
        context = context or {}
        index = start

        while index <= end:
            stripped = masked[index].strip()
            if not stripped:
                index += 1
                continue
            if stripped[0] == "}":
                index += 1
                continue

            probe = next_non_empty(index, end)
            if probe is None:
                break
            index = probe
            stripped = masked[index].strip()
            display = lines[index] if index < len(lines) else stripped
            next_index = next_non_empty(index + 1, end)
            next_stripped = masked[next_index].strip() if next_index is not None else ""

            classified = _js_classify(stripped, next_stripped, display)
            if classified is None:
                index += 1
                continue

            kind = str(classified.get("kind") or "")
            if _js_is_trivial(classified, display):
                index += 1
                continue

            name = classified.get("name")
            is_function_like = kind in {"function", "class", "arrow", "method"}
            semantic, group, category = _js_semantics(classified, display, context)
            doc = _js_leading_doc(lines, index)
            parameters = _js_parameters(display) if is_function_like else []
            initial_returns = None if is_function_like else "void"
            complexity = _complexity_label(
                1 if kind in {"for", "while", "do"} else 0
            )
            description = _js_description(
                classified,
                display,
                semantic,
                doc,
                initial_returns,
                context.get("parent_label"),
            )
            is_entry = semantic == ENTRYPOINT and not any(
                (node.get("data") or {}).get("is_entry") for node in builder.nodes
            )

            if is_function_like:
                public_type = semantic
            elif kind in {"if", "else", "else-if", "switch", "try"}:
                public_type = CONDITION
            elif kind in {"for", "while", "do"}:
                public_type = LOOP
            elif kind == "throw":
                public_type = ERROR_HANDLER
            elif kind in {"return", "break", "continue"}:
                public_type = ACTION
            elif semantic in {DATABASE, SERVICE}:
                public_type = semantic
            else:
                public_type = ACTION

            node_id = builder.add_node(
                node_type=public_type,
                label=classified["label"],
                code=_clip(display, 400),
                depth=depth,
                line=index + 1,
                end_line=index + 1,
                kind=kind,
                is_async=bool(classified.get("async")),
                semantic_type=semantic,
                category=category,
                group=group,
                description=description,
                parameters=parameters,
                returns=initial_returns,
                complexity=complexity,
                parent_id=context.get("parent_id"),
                parent_label=context.get("parent_label"),
                is_entry=is_entry,
            )
            if is_entry:
                builder.mark_entry(node_id)
            node_ref = builder.get_node(node_id)

            for head_id, head_label, head_async in current_heads:
                builder.connect(
                    head_id,
                    node_id,
                    animated=head_async or bool(classified.get("async")),
                    label=head_label,
                    kind="branch" if head_label else "sequence",
                )

            if classified.get("terminal"):
                # return / throw / break / continue end the current flow:
                # nothing after them in the same block is reachable.
                current_heads = []
                index += 1
                continue

            if not classified.get("opens"):
                current_heads = [(node_id, None, False)]
                index += 1
                continue

            open_line = index
            if "{" not in masked[open_line]:
                brace_line = next_non_empty(open_line + 1, end)
                if brace_line is None or not masked[brace_line].strip().endswith("{"):
                    current_heads = [(node_id, None, False)]
                    index += 1
                    continue
                open_line = brace_line

            close_line = _find_block_close(
                open_line, end, depth_before, depth_after, depth_min, closes
            )
            if close_line is None:
                current_heads = [(node_id, None, False)]
                index += 1
                continue

            body_start = open_line + 1
            body_end = close_line - 1
            body_tails: list[str] = []
            if body_end >= body_start:
                child_context = context
                if is_function_like:
                    child_context = {
                        "parent_id": node_id,
                        "parent_label": name or classified.get("label"),
                        "parent_semantic": semantic,
                    }
                body_tails = process_range(
                    body_start, body_end, depth + 1, [(node_id, None, False)],
                    child_context,
                )

            is_loop = classified["type"] == LOOP

            if is_loop:
                for tail in dict.fromkeys(body_tails):
                    builder.connect(tail, node_id, animated=True, kind="loop-back")

            node_ref["end_line"] = close_line + 1
            node_ref["data"]["end_line"] = close_line + 1
            node_ref["code"] = _clip(
                "\n".join(lines[index : close_line + 1]), config.NODE_CODE_MAX_CHARS
            )
            node_ref["data"]["code"] = node_ref["code"]

            if is_function_like:
                if kind == "class":
                    returns = None
                else:
                    returns = _js_return_value(lines, body_start, close_line)
                complexity = _js_complexity(lines, body_start, close_line)
                description = _js_description(
                    classified,
                    display,
                    semantic,
                    doc,
                    returns,
                    context.get("parent_label"),
                )
                for target in (node_ref, node_ref["data"]):
                    target["returns"] = returns
                    target["complexity"] = complexity
                    target["description"] = description
            elif is_loop:
                complexity = _js_complexity(lines, body_start, close_line)
                node_ref["complexity"] = complexity
                node_ref["data"]["complexity"] = complexity

            # A conditional/try block has several outgoing paths. Seed the
            # continuation with the body tails so the statement after the
            # block is reachable from *every* branch, not just the last one.
            if is_loop or is_function_like:
                branch_heads: list[Head] = []
            else:
                branch_heads = [
                    (tail, None, False) for tail in dict.fromkeys(body_tails)
                ]
            branch_found = False
            tail_line = close_line
            guard = 0
            while guard < 12:
                guard += 1
                remainder = masked[tail_line].split("}", 1)[1] if "}" in masked[tail_line] else ""
                remainder = remainder.strip()
                branch_line = None

                if remainder and (
                    remainder.startswith("else")
                    or remainder.startswith("catch")
                    or remainder.startswith("finally")
                ):
                    branch_line = tail_line
                    branch_stripped = remainder
                else:
                    candidate = next_non_empty(tail_line + 1, end)
                    if candidate is not None:
                        candidate_stripped = masked[candidate].strip()
                        if candidate_stripped.startswith("}"):
                            after_brace = candidate_stripped.lstrip("}").strip()
                            if after_brace and (
                                after_brace.startswith("else")
                                or after_brace.startswith("catch")
                                or after_brace.startswith("finally")
                            ):
                                branch_line = candidate
                                branch_stripped = after_brace
                        elif candidate_stripped.startswith("else") or candidate_stripped.startswith(
                            "catch"
                        ) or candidate_stripped.startswith("finally"):
                            branch_line = candidate
                            branch_stripped = candidate_stripped

                if branch_line is None:
                    break

                branch_found = True
                branch_kind = (
                    "finally" if branch_stripped.startswith("finally") else
                    "catch" if branch_stripped.startswith("catch") else
                    "else-if" if branch_stripped.startswith("else if") else
                    "else"
                )
                branch_label = {
                    "finally": "finally",
                    "catch": "catch (...)",
                    "else-if": "else if (...)",
                    "else": "else",
                }[branch_kind]
                branch_semantic = (
                    ERROR_HANDLER if branch_kind in {"catch", "finally"} else UTILITY
                )
                branch_group = (
                    GROUP_ERROR if branch_kind in {"catch", "finally"} else GROUP_CONTROL
                )
                branch_category = (
                    "Error Handling" if branch_kind in {"catch", "finally"} else "Control Flow"
                )
                branch_description = {
                    "finally": "Runs cleanup after the guarded block completes.",
                    "catch": "Recovers from a thrown error.",
                    "else-if": "Evaluates an alternate condition before falling through.",
                    "else": "Fallback branch when the primary condition is false.",
                }[branch_kind]

                branch_node = builder.add_node(
                    node_type=CONDITION,
                    label=branch_label,
                    code=_clip(
                        "\n".join(lines[branch_line : branch_line + 1]), 400
                    ),
                    depth=depth + 1,
                    line=branch_line + 1,
                    end_line=branch_line + 1,
                    kind=branch_kind,
                    is_async=False,
                    semantic_type=branch_semantic,
                    group=branch_group,
                    category=branch_category,
                    description=branch_description,
                    complexity="O(1)",
                    parent_id=node_id,
                    parent_label=branch_label,
                )
                builder.connect(
                    node_id,
                    branch_node,
                    label=branch_kind if branch_kind != "else" else "else",
                    kind="branch",
                )

                branch_open = branch_line
                if not masked[branch_line].strip().rstrip().endswith("{"):
                    probe_brace = next_non_empty(branch_line + 1, end)
                    if probe_brace is None or not masked[probe_brace].strip().endswith("{"):
                        branch_heads.append((branch_node, None, False))
                        tail_line = branch_line
                        index = branch_line + 1
                        continue
                    branch_open = probe_brace

                branch_close = _find_block_close(
                    branch_open, end, depth_before, depth_after, depth_min, closes
                )
                if branch_close is None:
                    branch_heads.append((branch_node, None, False))
                    break

                b_start = branch_open + 1
                b_end = branch_close - 1
                branch_ref = builder.get_node(branch_node)
                branch_ref["end_line"] = branch_close + 1
                branch_ref["data"]["end_line"] = branch_close + 1
                branch_ref["code"] = _clip(
                    "\n".join(lines[branch_line : branch_close + 1]),
                    config.NODE_CODE_MAX_CHARS,
                )
                branch_ref["data"]["code"] = branch_ref["code"]
                if b_end >= b_start:
                    b_tails = process_range(
                        b_start, b_end, depth + 1, [(branch_node, None, False)],
                        context,
                    )
                    for tail in dict.fromkeys(b_tails):
                        branch_heads.append((tail, None, False))
                else:
                    branch_heads.append((branch_node, None, False))

                tail_line = branch_close
                index = branch_close + 1

            if (
                not branch_found
                and not is_loop
                and not is_function_like
                and classified["kind"] in {"if", "else-if", "switch"}
            ):
                # No ``else``/``default`` branch: the false path continues
                # directly from the condition node.
                branch_heads.append((node_id, None, False))

            index = max(index, close_line + 1)
            if is_loop or is_function_like:
                current_heads = [(node_id, None, False)]
            else:
                # An empty list means every path returned/threw; the next
                # statement is intentionally left without an incoming edge.
                current_heads = branch_heads

        return [head[0] for head in current_heads]

    if lines:
        try:
            process_range(0, len(lines) - 1, 0, [])
        except RecursionError as error:
            raise ValueError(
                "JavaScript/TypeScript source is too deeply nested to analyse."
            ) from error
        except MemoryError as error:
            raise ValueError(
                "JavaScript/TypeScript source is too large to analyse."
            ) from error

    errors = []
    for region in recover_javascript(lines):
        start = max(1, region.start_line)
        snippet = lines[start - 1] if start - 1 < len(lines) else ""
        errors.append(region.to_dict())
        builder.add_diagnostic(
            label=f"Unbalanced braces · line {start}",
            code=snippet or f"(line {start})",
            line=start,
            end_line=start,
            message=region.message,
            kind=region.kind,
        )

    payload = builder.payload()
    entry_node = next(
        (
            str(node["id"])
            for node in builder.nodes
            if (node.get("data") or {}).get("is_entry")
        ),
        None,
    )
    payload["entry_node"] = entry_node or (
        builder.nodes[0]["id"] if builder.nodes else None
    )
    payload["errors"] = errors
    payload["partial"] = bool(errors)
    return payload


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def _prune_visual_noise(graph: dict[str, Any], threshold: int = 120) -> dict[str, Any]:
    """Collapse low-value "data preparation" nodes on very large graphs.

    The Python extractor already folds *consecutive* plain assignments into a
    single node; on big modules those folded nodes still outnumber the
    architectural decisions. Once a graph grows past ``threshold`` nodes,
    every ``assignment``-kind node is bypassed (predecessors reconnect to
    successors) so the canvas keeps the meaningful skeleton. Small graphs
    are left untouched. Diagnostic markers are never pruned.
    """
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    if len(nodes) <= threshold:
        return graph

    drop_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        if data.get("diagnostic"):
            continue
        kind = str(node.get("kind") or data.get("kind") or "").lower()
        if kind == "assignment":
            drop_ids.add(str(node.get("id")))

    if not drop_ids:
        return graph

    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("source"), edge.get("target")
        if source is None or target is None:
            continue
        outgoing.setdefault(str(source), []).append(str(target))
        incoming.setdefault(str(target), []).append(str(source))

    kept_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_edge(source: str, target: str, original: dict[str, Any] | None = None) -> None:
        if source == target:
            return
        key = (source, target)
        if key in seen:
            return
        seen.add(key)
        template = original or {}
        kept_edges.append(
            {
                "id": template.get("id") or f"e-prune-{source}-{target}",
                "source": source,
                "target": target,
                "animated": bool(template.get("animated")),
                "kind": template.get("kind") or "sequence",
                **({"label": template["label"]} if template.get("label") else {}),
            }
        )

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in drop_ids or target in drop_ids:
            continue
        add_edge(source, target, edge)

    # Bypass each pruned node: reconnect every predecessor to every successor.
    for removed in drop_ids:
        for pred in incoming.get(removed, []):
            if pred in drop_ids:
                continue
            for succ in outgoing.get(removed, []):
                if succ in drop_ids:
                    continue
                add_edge(pred, succ, None)

    graph["nodes"] = [node for node in nodes if str(node.get("id")) not in drop_ids]
    graph["edges"] = kept_edges
    graph["groups"] = _build_groups(graph["nodes"])
    graph["tiers"] = _build_tiers(graph["nodes"])

    stats = graph.get("stats")
    if isinstance(stats, dict):
        stats["nodes"] = len(graph["nodes"])
        stats["edges"] = len(kept_edges)
        utilities = 0
        for node in graph["nodes"]:
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            if str(node.get("type") or data.get("type") or "") == UTILITY:
                utilities += 1
        stats["utilities"] = utilities
    return graph


def parse_code_to_ast_graph(code_content: str) -> dict[str, Any]:
    """Parse source code into ``{nodes, edges, language, stats}``.

    Raises:
        ValueError: when the payload is empty or larger than the configured limit.
        SyntaxError: when Python source cannot be compiled.
    """
    try:
        if code_content is None:
            raise ValueError("No code supplied to the parser.")

        if not isinstance(code_content, str):
            raise ValueError("Code payload must be a string.")

        if code_content.strip() == "":
            return {
                "nodes": [],
                "edges": [],
                "groups": [],
                "tiers": [],
                "language": "python",
                "entry_node": None,
                "errors": [],
                "partial": False,
                "stats": {
                    "nodes": 0,
                    "edges": 0,
                    "functions": 0,
                    "conditions": 0,
                    "loops": 0,
                    "actions": 0,
                    "entrypoints": 0,
                    "services": 0,
                    "databases": 0,
                    "utilities": 0,
                    "error_handlers": 0,
                    "async_nodes": 0,
                    "diagnostics": 0,
                    "vulnerabilities": 0,
                    "lines": 0,
                    "language": "python",
                },
                "security": {
                    "vulnerable_nodes": 0,
                    "total_findings": 0,
                    "by_severity": {
                        "LOW": 0,
                        "MEDIUM": 0,
                        "HIGH": 0,
                        "CRITICAL": 0,
                    },
                    "findings": [],
                },
            }

        encoded_size = len(code_content.encode("utf-8", errors="ignore"))
        if encoded_size > getattr(config, "PARSE_MAX_BYTES", 1024 * 1024):
            raise ValueError(
                f"Source is {encoded_size} bytes which exceeds the "
                f"{getattr(config, 'PARSE_MAX_BYTES', 1024 * 1024)} byte parse limit."
            )

        language = detect_language(code_content)
        if language in {"python"}:
            return scan_security_graph(_prune_visual_noise(_build_python_graph(code_content)))
        return scan_security_graph(_prune_visual_noise(_build_js_graph(code_content)))
    except (SyntaxError, ValueError):
        raise
    except (MemoryError, RecursionError):
        raise
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"Parser failed unexpectedly: {error}") from error


def parse_files_to_graph(sources: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Parse several ``(filename, content)`` pairs into a single merged graph."""
    merged_nodes: list[dict[str, Any]] = []
    merged_edges: list[dict[str, Any]] = []
    merged_errors: list[dict[str, Any]] = []
    stats = {
        "nodes": 0,
        "edges": 0,
        "functions": 0,
        "conditions": 0,
        "loops": 0,
        "actions": 0,
        "entrypoints": 0,
        "services": 0,
        "databases": 0,
        "utilities": 0,
        "error_handlers": 0,
        "async_nodes": 0,
        "diagnostics": 0,
        "lines": 0,
        "language": "mixed",
        "files": 0,
    }
    offset = 0

    for filename, content in sources:
        graph = parse_code_to_ast_graph(content)
        offset += len(merged_nodes)
        for node in graph["nodes"]:
            node = dict(node)
            node["id"] = f"f{offset + int(node['id'][1:])}"
            node["data"] = dict(node.get("data") or {})
            node["data"]["id"] = node["id"]
            node["data"]["file"] = filename
            node["file"] = filename
            merged_nodes.append(node)
        for edge in graph["edges"]:
            edge = dict(edge)
            edge["id"] = f"f{offset}_{edge['id']}"
            edge["source"] = f"f{offset + int(edge['source'][1:])}"
            edge["target"] = f"f{offset + int(edge['target'][1:])}"
            merged_edges.append(edge)
        for error in graph.get("errors") or []:
            entry = dict(error)
            entry["file"] = filename
            merged_errors.append(entry)

        node_stats = graph["stats"]
        stats["nodes"] += node_stats["nodes"]
        stats["edges"] += node_stats["edges"]
        stats["functions"] += node_stats["functions"]
        stats["conditions"] += node_stats["conditions"]
        stats["loops"] += node_stats["loops"]
        stats["actions"] += node_stats["actions"]
        stats["entrypoints"] += node_stats.get("entrypoints", 0)
        stats["services"] += node_stats.get("services", 0)
        stats["databases"] += node_stats.get("databases", 0)
        stats["utilities"] += node_stats.get("utilities", 0)
        stats["error_handlers"] += node_stats.get("error_handlers", 0)
        stats["async_nodes"] += node_stats["async_nodes"]
        stats["diagnostics"] += node_stats.get("diagnostics", 0)
        stats["lines"] += node_stats["lines"]
        stats["files"] += 1

    merged = {
        "nodes": merged_nodes,
        "edges": merged_edges,
        "groups": _build_groups(merged_nodes),
        "tiers": _build_tiers(merged_nodes),
        "language": stats["language"],
        "errors": merged_errors,
        "partial": bool(merged_errors),
        "stats": stats,
    }
    return scan_security_graph(_prune_visual_noise(merged))


# ----------------------------------------------------------------------
# Auto-Architect - component (architecture) graph extraction
# ----------------------------------------------------------------------
#
# Turns either a structural design prompt ("API Gateway routes requests to
# the Auth Service which writes to the User DB") or a source excerpt into a
# component graph whose nodes carry a classification tag:
#
#   service | database | api | function
#
# The extractor is deterministic: every component is keyed by a loose slug of
# its name (``AuthService`` / ``auth service`` -> ``authservice``), so a second
# run over the same input produces the same ids and the caller can stitch the
# result onto an existing canvas without duplicating anything.

ARCH_SERVICE = "service"
ARCH_DATABASE = "database"
ARCH_API = "api"
ARCH_FUNCTION = "function"
ARCH_TAGS = (ARCH_SERVICE, ARCH_DATABASE, ARCH_API, ARCH_FUNCTION)

ARCH_EDGE_KINDS = ("call", "data_flow", "api_request")

# Semantic family each component tag renders as on the canvas. Architecture
# components are published with the same five semantic roles the AST parser
# emits so both pipelines produce one visual language.
ARCH_TAG_TYPE = {
    ARCH_SERVICE: SERVICE,
    ARCH_DATABASE: DATABASE,
    ARCH_API: ENTRYPOINT,
    ARCH_FUNCTION: UTILITY,
}

_ARCH_TAG_DESCRIPTION = {
    ARCH_DATABASE: "Stores and retrieves the data behind {name}.",
    ARCH_SERVICE: "Implements the business rules and orchestration of {name}.",
    ARCH_API: "Receives requests and routes them into {name}.",
    ARCH_FUNCTION: "Supporting helper used by {name}.",
}

# Role keywords matched against the sub-words of an identifier (or a word of
# free text). The first bucket that matches decides the component tag.
_ARCH_KEYWORDS: dict[str, frozenset[str]] = {
    ARCH_DATABASE: frozenset({
        "db", "database", "databases", "sql", "sqlite", "postgres",
        "postgresql", "mysql", "mariadb", "mongo", "mongodb", "cassandra",
        "redis", "cache", "memcache", "repository", "repo", "dao", "table",
        "tables", "model", "models", "schema", "schemas", "store", "storage",
        "persist", "persistence", "migration", "migrations", "collection",
        "collections", "index", "indexes", "warehouse", "bucket", "blob",
        "ledger", "log", "logs", "snapshot", "snapshots", "archive",
    }),
    ARCH_SERVICE: frozenset({
        "service", "services", "svc", "worker", "workers", "job", "jobs",
        "task", "tasks", "engine", "engines", "manager", "managers", "broker",
        "queue", "queues", "scheduler", "daemon", "processor", "processors",
        "module", "modules", "app", "application", "system", "pipeline",
        "pipelines", "workflow", "workflows", "consumer", "producer",
        "listener", "middleware", "facade", "domain", "core", "provider",
        "providers", "registry", "container", "handler", "handlers",
    }),
    ARCH_API: frozenset({
        "api", "apis", "route", "routes", "router", "endpoint", "endpoints",
        "controller", "controllers", "http", "rest", "graphql", "grpc",
        "webhook", "webhooks", "gateway", "proxy", "client", "request",
        "requests", "response", "responses", "fetch", "axios", "upload",
        "uploads", "download", "downloads", "invoke", "invoker", "rpc", "sdk",
    }),
}

# Keywords that name a component on their own. The remaining keywords only
# count when they close a longer name ("the User Database"), which keeps
# prose verbs that happen to be role words ("routes the requests") from
# turning into nodes.
_ARCH_STANDALONE = frozenset({
    "db", "database", "databases", "sql", "sqlite", "postgres", "postgresql",
    "mysql", "mariadb", "mongo", "mongodb", "cassandra", "redis", "cache",
    "memcache", "repository", "repo", "dao", "schema", "schemas", "storage",
    "ledger", "warehouse", "bucket", "blob", "service", "services", "svc",
    "worker", "workers", "engine", "engines", "queue", "queues", "broker",
    "scheduler", "daemon", "pipeline", "pipelines", "workflow", "workflows",
    "middleware", "facade", "registry", "api", "apis", "gateway", "endpoint",
    "endpoints", "router", "controller", "controllers", "client", "http",
    "rest", "graphql", "grpc", "webhook", "webhooks", "proxy", "sdk", "rpc",
})

# Words that never name a component - articles, prepositions and the verbs
# architecture prose tends to use between two components.
_ARCH_NON_NAME = frozenset({
    "the", "a", "an", "and", "or", "but", "then", "than", "that", "this",
    "these", "those", "to", "of", "in", "on", "at", "by", "for", "with",
    "from", "into", "onto", "over", "under", "via", "per", "as", "is", "are",
    "be", "been", "being", "was", "were", "it", "its", "they", "them",
    "their", "we", "our", "you", "your", "he", "she", "his", "her", "who",
    "which", "what", "when", "where", "why", "how", "if", "else", "not",
    "no", "nor", "so", "because", "before", "after", "during", "while",
    "until", "against", "about", "above", "below", "between", "through",
    "across", "also", "only", "just", "most", "more", "some", "any", "all",
    "each", "both", "such", "there", "here", "new", "old", "other", "same",
    "next", "previous", "main", "few", "several", "multiple", "every",
    "incoming", "outgoing", "relevant", "possible", "additional", "requests",
    "request", "responses", "response", "handling", "writes", "write",
    "written", "reads", "read", "calls", "called", "sends", "send", "sent",
    "returns", "returned", "return", "queries", "query", "uses", "used",
    "use", "stores", "stored", "store", "loads", "loaded", "load", "fetches",
    "fetched", "fetch", "creates", "create", "deletes", "delete", "updates",
    "update", "invokes", "invoke", "triggers", "trigger", "handles",
    "handle", "routes", "route", "connects", "connect", "depends", "depend",
    "receives", "receive", "produces", "produce", "consumes", "consume",
    "streams", "stream", "saves", "save", "gets", "get", "posts", "post",
    "puts", "put", "runs", "run", "starts", "start", "stops", "stop",
    "executes", "execute", "validates", "validate", "enriches", "enrich",
    "checks", "check", "logs", "log", "caches", "cache", "spawns", "spawn",
    "awaits", "await", "retries", "retry", "parses", "parse", "transforms",
    "transform", "aggregates", "aggregate", "notifies", "notify", "signs",
    "sign", "verifies", "verify", "authenticates", "authenticate",
    "authorizes", "authorize", "builds", "build", "deploys", "deploy",
    "serves", "serve", "renders", "render", "collects", "collect", "indexes",
    "index", "schedules", "schedule", "monitors", "monitor", "tracks",
    "track", "holds", "hold", "keeps", "keep", "fills", "fill", "syncs",
    "sync", "merges", "merge", "publishes", "publish", "subscribes",
    "subscribe", "listens", "listen",
})

# Well-known third-party modules mapped straight onto a tag so an import
# line becomes a node without any guessing.
_ARCH_MODULE_TAGS = {
    "sqlalchemy": ARCH_DATABASE, "sqlite3": ARCH_DATABASE,
    "psycopg2": ARCH_DATABASE, "pymongo": ARCH_DATABASE, "redis": ARCH_DATABASE,
    "cassandra": ARCH_DATABASE, "motor": ARCH_DATABASE, "peewee": ARCH_DATABASE,
    "mongoose": ARCH_DATABASE, "knex": ARCH_DATABASE, "sequelize": ARCH_DATABASE,
    "prisma": ARCH_DATABASE, "pg": ARCH_DATABASE, "mysql": ARCH_DATABASE,
    "mysql2": ARCH_DATABASE, "oracledb": ARCH_DATABASE, "tinydb": ARCH_DATABASE,
    "django": ARCH_SERVICE, "flask": ARCH_SERVICE, "fastapi": ARCH_SERVICE,
    "starlette": ARCH_SERVICE, "celery": ARCH_SERVICE, "kafka": ARCH_SERVICE,
    "pika": ARCH_SERVICE, "express": ARCH_SERVICE, "koa": ARCH_SERVICE,
    "hapi": ARCH_SERVICE, "socket.io": ARCH_SERVICE, "amqplib": ARCH_SERVICE,
    "requests": ARCH_API, "httpx": ARCH_API,
    "aiohttp": ARCH_API, "urllib": ARCH_API, "grpc": ARCH_API,
    "boto3": ARCH_API, "axios": ARCH_API, "node-fetch": ARCH_API,
    "got": ARCH_API, "superagent": ARCH_API, "undici": ARCH_API,
}

# Standard library / language modules that carry no architectural meaning.
_IGNORED_IMPORTS = frozenset({
    "__future__", "__main__", "builtins", "typing", "types", "os", "sys",
    "re", "json", "math", "time", "datetime", "calendar", "collections",
    "itertools", "functools", "operator", "pathlib", "abc", "enum",
    "dataclasses", "logging", "unittest", "io", "textwrap", "random",
    "string", "subprocess", "threading", "multiprocessing", "copy", "glob",
    "shutil", "tempfile", "uuid", "hashlib", "base64", "struct", "socket",
    "asyncio", "warnings", "traceback", "inspect", "importlib", "contextlib",
    "csv", "argparse", "secrets", "statistics", "decimal", "fractions",
    "heapq", "bisect", "array", "weakref", "gc", "atexit", "signal", "errno",
    "select", "selectors", "mmap", "zlib", "gzip", "bz2", "lzma", "pickle",
    "copyreg", "shelve", "dbm", "sqlite", "platform", "getpass", "pwd",
    "grp", "stat", "fileinput", "fnmatch", "linecache", "pprint", "reprlib",
    "difflib", "email", "http", "urllib", "xml", "html", "http",
})

# Call receivers / methods that mean "this touches storage".
_DB_RECEIVERS = frozenset({
    "db", "database", "cursor", "conn", "connection", "session", "engine",
    "redis", "cache", "collection", "table", "repo", "repository", "store",
    "sqlite3", "sqlalchemy", "redisclient", "mongo", "mongodb", "knex",
    "sequelize", "prisma", "queryset",
})
_DB_METHODS = frozenset({
    "query", "execute", "executemany", "fetchall", "fetchone", "fetchmany",
    "find", "findone", "find_one", "insert", "insertmany", "insert_one",
    "upsert", "save", "update", "delete", "aggregate", "commit", "count",
})

# Call heads that mean "this issues an HTTP request".
_HTTP_CLIENTS = frozenset({
    "requests", "httpx", "aiohttp", "urllib", "http", "client", "session",
    "axios", "fetch", "rest", "webclient", "apiclient",
})
_HTTP_METHODS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "request",
})
_HTTP_ROUTE_METHODS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options",
})
_STATIC_ROUTE_METHODS = frozenset({"route", "websocket", "webhook"})

# Connector verbs read between two prompt mentions to classify the edge.
_FLOW_WORDS = frozenset({
    "write", "writes", "written", "store", "stores", "stored", "read",
    "reads", "query", "queries", "load", "loads", "load", "save", "saves",
    "persist", "persists", "index", "indexes", "cache", "caches", "fill",
    "fills", "sync", "syncs", "update", "updates", "merge", "merges",
    "keep", "keeps", "hold", "holds", "log", "logs", "archive", "archives",
    "snapshot", "snapshots", "batch", "batches",
})
_API_WORDS = frozenset({
    "request", "requests", "post", "posts", "send", "sends", "fetch",
    "fetches", "route", "routes", "dispatch", "dispatches", "upload",
    "uploads", "download", "downloads", "return", "returns", "respond",
    "responds", "deliver", "delivers",
})
_CALL_WORDS = frozenset({
    "call", "calls", "invoke", "invokes", "trigger", "triggers", "depend",
    "depends", "use", "uses", "handle", "handles", "process", "processes",
    "execute", "executes", "run", "runs", "start", "starts", "await",
    "awaits", "retry", "retries", "validate", "validates", "check",
    "checks", "notify", "notifies", "authenticate", "authenticates",
    "sign", "signs", "verify", "verifies", "parse", "parses", "transform",
    "transforms", "enrich", "enriches", "aggregate", "aggregates",
    "consume", "consumes", "produce", "produces", "stream", "streams",
    "listen", "listens", "subscribe", "subscribes", "publish", "publishes",
    "deploy", "deploys", "serve", "serves", "render", "renders", "track",
    "tracks", "monitor", "monitors", "bill", "bills", "meter", "meters",
})

_SEGMENT_SPLIT = re.compile(
    r"(\s*(?:->|=>|→|⟶)\s*|\s*[.;\n]+\s*|\s*,\s*(?:and\s+then|then)\s*)"
)
_ARROW_SEPARATOR = re.compile(r"->|=>|→|⟶|,\s*(?:and\s+then|then)")
_WORD_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_TEXT_CALL_PATTERN = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_TEXT_URL_PATTERN = re.compile(r"[\"']((?:https?://|/)[^\"'\s]{1,160})[\"']")
_TEXT_ROUTE_PATTERN = re.compile(
    r"\.(get|post|put|patch|delete|head|options|all|route|webhook|websocket)"
    r"\s*\(\s*[\"']([^\"'\s]{1,160})[\"']"
)
_TEXT_HTTP_PATTERN = re.compile(
    r"\b(?:requests|httpx|aiohttp|axios|urllib|client|http)\s*[.]\s*"
    r"(?:get|post|put|patch|delete|request|head)\s*\(|\bfetch\s*\(",
    re.IGNORECASE,
)
_TEXT_DB_PATTERN = re.compile(
    r"\b(db|database|cursor|conn|connection|session|engine|redis|cache|"
    r"collection|repository|repo|table)\s*[.]\s*\w+\s*\(|"
    r"\b(?:mongoose|sqlite3?|knex|sequelize|prisma|sqlalchemy)\b|"
    r"\.\s*(?:find|insert|updateOne|insertOne|aggregate|executemany|fetchall)\s*\(",
    re.IGNORECASE,
)
_TEXT_MODULE_PATTERN = re.compile(
    r"(?:require\s*\(\s*|from\s+|import\s+)[\"']([^\"']+)[\"']"
)
_TEXT_CALL_NOISE = frozenset({
    "if", "for", "while", "switch", "catch", "return", "function", "typeof",
    "new", "await", "super", "this", "import", "export", "class", "do",
    "else", "try", "finally", "delete", "in", "of", "instanceof",
    "constructor", "yield", "void", "throw", "case", "break", "continue",
    "with", "debugger", "ensure", "getattr", "setattr", "print", "len",
    "range", "str", "int", "float", "dict", "list", "set", "tuple", "type",
    "isinstance", "hasattr", "require", "console", "logger", "assert",
    "raise", "lambda", "pass", "async", "static", "super", "define",
    "setTimeout", "setInterval", "Promise", "Object", "Array", "JSON",
    "Math", "Date", "Error", "parseInt", "parseFloat", "isNaN",
})

_PY_DEFINITION_PATTERNS = (
    (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"), ARCH_FUNCTION),
    (re.compile(r"^\s*class\s+(\w+)"), ARCH_SERVICE),
)
_JS_DEFINITION_PATTERNS = (
    (re.compile(r"\b(?:async\s+)?function\s+(\w+)\s*\("), ARCH_FUNCTION),
    (re.compile(r"\bclass\s+(\w+)"), ARCH_SERVICE),
    (
        re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"),
        ARCH_FUNCTION,
    ),
    (re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*function\b"), ARCH_FUNCTION),
)


def arch_slug(name: str) -> str:
    """Loose canonical key for a component name.

    ``AuthService``, ``auth service`` and ``auth_service`` all collapse to
    ``authservice`` so a prompt, a source file and a model response that
    describe the same component are recognised as one node.
    """
    slug = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
    return slug[:64]


def arch_node_id(name: str) -> str:
    """Readable, stable canvas id for a component name."""
    readable = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return f"arc-{readable[:48].strip('-') or 'component'}"


def _identifier_words(name: str) -> list[str]:
    """Split an identifier or phrase into lower-cased sub-words."""
    words: list[str] = []
    for chunk in re.split(r"[^0-9A-Za-z]+", str(name or "")):
        if not chunk:
            continue
        words.extend(
            part.lower()
            for part in re.findall(
                r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk
            )
        )
    return words


def classify_arch_tag(name: str) -> str:
    """Resolve the component tag for a name (defaults to ``function``)."""
    words = set(_identifier_words(name))
    for tag in (ARCH_DATABASE, ARCH_SERVICE, ARCH_API):
        if _ARCH_KEYWORDS[tag] & words:
            return tag
    return ARCH_FUNCTION


def _looks_like_code(text: str) -> bool:
    """Decide whether an input is source code rather than design prose."""
    score = 0
    if re.search(r"^\s*(?:async\s+)?def\s+\w+\s*\(", text, re.M):
        score += 2
    if re.search(r"^\s*class\s+\w+\s*[(:]", text, re.M):
        score += 1
    if re.search(r"\b(?:function|const|let|var)\s+\w+\s*[=(]", text):
        score += 2
    if re.search(r"^\s*(?:import|from|export)\b", text, re.M):
        score += 1
    if "=>" in text:
        score += 1
    if text.count("{") >= 2 and text.count("}") >= 2:
        score += 1
    if re.search(r"^\s*[A-Za-z_$][\w$]*\s*=\s*\S", text, re.M):
        score += 1
    if re.search(r"^\s*@\w[\w.]*\(", text, re.M):
        score += 1
    return score >= 2


# ----------------------------------------------------------------------
# Prompt extraction
# ----------------------------------------------------------------------

def _prompt_mentions(segment: str) -> list[dict[str, Any]]:
    """Find component mentions inside one clause of a design prompt.

    A mention is anchored on a role keyword and extended leftwards over up to
    two name-like neighbours, so ``the Auth Service`` yields ``Auth Service``
    while ``writes to the database`` yields ``database``.
    """
    tokens = [(match.start(), match.end(), match.group(0)) for match in _WORD_TOKEN.finditer(segment)]
    mentions: list[dict[str, Any]] = []
    consumed_until = -1

    for index, (start, end, token) in enumerate(tokens):
        if start < consumed_until:
            continue
        tag = None
        words = set(_identifier_words(token))
        for candidate in (ARCH_DATABASE, ARCH_SERVICE, ARCH_API):
            if _ARCH_KEYWORDS[candidate] & words:
                tag = candidate
                break
        if tag is None:
            continue

        picked = [token]
        left_start = start
        cursor = index
        while cursor > 0 and len(picked) < 3:
            prev_start, prev_end, prev_token = tokens[cursor - 1]
            if prev_start < consumed_until:
                break
            if not re.fullmatch(r"[\s\-]*", segment[prev_end:left_start] or ""):
                break
            if prev_token.lower() in _ARCH_NON_NAME:
                break
            picked.insert(0, prev_token)
            left_start = prev_start
            cursor -= 1

        if len(picked) == 1 and not (words & _ARCH_STANDALONE):
            continue

        name = " ".join(picked)
        mentions.append(
            {
                "name": name,
                "tag": tag,
                "key": arch_slug(name),
                "start": left_start,
                "end": end,
            }
        )
        consumed_until = end

    return _merge_adjacent_mentions(mentions, segment)


def _merge_adjacent_mentions(
    mentions: list[dict[str, Any]], segment: str
) -> list[dict[str, Any]]:
    """Fold ``API`` + ``Gateway`` into ``API Gateway``.

    Two single-word components separated only by whitespace are one noun
    phrase, not two nodes. Anything wider than that (a comma, the word
    ``and``, a connector verb) keeps them apart.
    """
    merged: list[dict[str, Any]] = []
    for mention in mentions:
        if merged:
            previous = merged[-1]
            gap = segment[previous["end"]:mention["start"]]
            if (
                len(previous["name"].split()) == 1
                and len(mention["name"].split()) == 1
                and gap
                and not re.search(r"\w", gap)
            ):
                previous["name"] = f"{previous['name']}{gap}{mention['name']}"
                previous["key"] = arch_slug(previous["name"])
                previous["end"] = mention["end"]
                previous["tag"] = mention["tag"]
                continue
        merged.append(dict(mention))
    return merged


def _connector_kind(between: str, target_tag: str) -> str:
    """Classify an edge from the words between two prompt mentions."""
    words = set(_identifier_words(between))
    if words & _FLOW_WORDS:
        kind = "data_flow"
    elif words & _API_WORDS:
        kind = "api_request"
    elif words & _CALL_WORDS:
        kind = "call"
    elif target_tag == ARCH_DATABASE:
        kind = "data_flow"
    elif target_tag == ARCH_API:
        kind = "api_request"
    else:
        kind = "call"

    if kind == "call" and target_tag == ARCH_API:
        kind = "api_request"
    if kind == "api_request" and target_tag == ARCH_DATABASE:
        kind = "data_flow"
    return kind


def _prompt_records(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract ``(components, connections)`` from structural design prose.

    The text is split on arrows, sentence punctuation and ``then``. An arrow
    keeps the flow going into the next fragment (``A -> B``); a full stop
    ends it, so a new sentence describing a second flow does not get welded
    onto the tail of the previous one.
    """
    components: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    seen: set[str] = set()
    parts = _SEGMENT_SPLIT.split(text)
    previous_key: str | None = None

    for index in range(0, len(parts), 2):
        segment = parts[index]
        if index > 0:
            separator = parts[index - 1] or ""
            if previous_key is not None and not _ARROW_SEPARATOR.search(separator):
                previous_key = None
        if not segment or not segment.strip():
            continue

        mentions = _prompt_mentions(segment)
        if not mentions:
            continue

        if previous_key is not None and previous_key != mentions[0]["key"]:
            connections.append(
                {
                    "source": previous_key,
                    "target": mentions[0]["key"],
                    "kind": _connector_kind("", mentions[0]["tag"]),
                }
            )

        for position, mention in enumerate(mentions):
            if position > 0:
                previous = mentions[position - 1]
                if previous["key"] != mention["key"]:
                    between = segment[previous["end"]:mention["start"]]
                    connections.append(
                        {
                            "source": previous["key"],
                            "target": mention["key"],
                            "kind": _connector_kind(between, mention["tag"]),
                        }
                    )
            if mention["key"] in seen:
                continue
            seen.add(mention["key"])
            components.append(
                {
                    "name": mention["name"],
                    "tag": mention["tag"],
                    "code": _clip(" ".join(segment.split()), 400),
                    "line": None,
                    "end_line": None,
                    "source": "prompt",
                }
            )
        previous_key = mentions[-1]["key"]

    return components, connections


# ----------------------------------------------------------------------
# Source extraction
# ----------------------------------------------------------------------

def _dotted_path(node: ast.AST) -> str:
    """Render an attribute chain (``requests.get``) as a dotted string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_path(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted_path(node.func)
    return ""


def _http_call_label(parts: list[str], call: ast.Call) -> tuple[str, str]:
    """Build ``GET /path`` style label for an outbound HTTP call."""
    leaf = parts[-1].lower()
    method = leaf.upper() if leaf in _HTTP_METHODS else "REQUEST"
    target = None
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            candidate = argument.value.strip()
            if candidate:
                target = candidate
                break
        if isinstance(argument, ast.JoinedStr):
            target = "..."
            break
    label = f"{method} {target}" if target else f"{method} {parts[0]}"
    return label, method


def _architecture_from_python_records(
    code: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract components/calls from compilable Python source via :mod:`ast`."""
    tree = ast.parse(code)
    lines = code.splitlines()
    limit = config.ARCHITECTURE_MAX_NODES

    components: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    keys: set[str] = set()
    aliases: dict[str, str] = {}
    owners: dict[int, str] = {}
    state = {"full": False}

    def source_of(start: int | None, end: int | None) -> str:
        if not start:
            return ""
        first = max(1, start)
        last = min(len(lines), end or start)
        return "\n".join(lines[first - 1:last])

    def add(
        name: str,
        tag: str,
        snippet: str,
        line: int | None,
        end_line: int | None,
    ) -> str | None:
        key = arch_slug(name)
        if not key:
            return None
        if key in keys:
            return key
        if state["full"] or len(components) >= limit:
            state["full"] = True
            return None
        keys.add(key)
        components.append(
            {
                "name": " ".join(str(name).split()),
                "tag": tag,
                "code": _clip(snippet, config.NODE_CODE_MAX_CHARS),
                "line": line,
                "end_line": end_line,
                "source": "code",
            }
        )
        return key

    def handle_decorator(dec: ast.AST, owner_key: str | None, owner_label: str) -> None:
        target = dec.func if isinstance(dec, ast.Call) else dec
        path = _dotted_path(target)
        if not path:
            return
        leaf = path.rsplit(".", 1)[-1].lower()
        if leaf not in _HTTP_ROUTE_METHODS and leaf not in _STATIC_ROUTE_METHODS:
            return
        route_path = None
        if isinstance(dec, ast.Call) and dec.args:
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                route_path = first.value.strip()
        method = leaf.upper() if leaf in _HTTP_ROUTE_METHODS else "ROUTE"
        label = (
            f"{method} {route_path}"
            if route_path
            else f"{method} {owner_label} route"
        )
        key = add(
            label,
            ARCH_API,
            path,
            getattr(dec, "lineno", None),
            getattr(dec, "end_lineno", None),
        )
        if key and owner_key:
            connections.append(
                {
                    "source": key,
                    "target": owner_key,
                    "kind": "api_request",
                    "label": method,
                }
            )

    def walk_body(body: list[ast.stmt], prefix: str = "") -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                has_methods = any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for item in statement.body
                )
                tag = classify_arch_tag(statement.name)
                if tag == ARCH_FUNCTION and has_methods:
                    tag = ARCH_SERVICE
                key = add(
                    statement.name,
                    tag,
                    source_of(statement.lineno, statement.end_lineno),
                    statement.lineno,
                    statement.end_lineno,
                )
                if key:
                    aliases.setdefault(statement.name, key)
                walk_body(statement.body, f"{statement.name}.")
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                label = f"{prefix}{statement.name}"
                key = add(
                    label,
                    ARCH_FUNCTION,
                    source_of(statement.lineno, statement.end_lineno),
                    statement.lineno,
                    statement.end_lineno,
                )
                if key:
                    owners[id(statement)] = key
                    aliases.setdefault(statement.name, key)
                    aliases.setdefault(label, key)
                    for decorator in statement.decorator_list:
                        handle_decorator(decorator, key, label)
                walk_body(statement.body, f"{label}.")
            else:
                for child in ast.iter_child_nodes(statement):
                    if isinstance(child, ast.stmt):
                        walk_body([child], prefix)

    walk_body(tree.body)

    def add_import(root: str, statement: ast.AST) -> None:
        if not root or root in _IGNORED_IMPORTS:
            return
        tag = _ARCH_MODULE_TAGS.get(root)
        if tag is None:
            classified = classify_arch_tag(root)
            if classified == ARCH_FUNCTION:
                return
            tag = classified
        add(
            root,
            tag,
            source_of(getattr(statement, "lineno", None), getattr(statement, "end_lineno", None)),
            getattr(statement, "lineno", None),
            getattr(statement, "end_lineno", None),
        )

    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                add_import(alias.name.split(".", 1)[0], statement)
        elif isinstance(statement, ast.ImportFrom):
            root = (statement.module or "").split(".", 1)[0]
            if root:
                add_import(root, statement)

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def owner_of(node: ast.AST) -> str | None:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return owners.get(id(current))
            current = parents.get(current)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path = _dotted_path(node.func)
        parts = [part for part in path.split(".") if part]
        if not parts:
            continue

        owner = owner_of(node)
        head = parts[0]
        leaf = parts[-1]
        receiver = next((part for part in parts[:-1] if part not in {"self", "cls"}), None)
        is_http = (
            (receiver is not None and receiver in _HTTP_CLIENTS)
            or (len(parts) > 1 and head in _HTTP_CLIENTS)
            or (len(parts) == 1 and head in {"fetch", "axios"})
        )

        if is_http:
            label, method = _http_call_label(parts, node)
            key = add(
                label,
                ARCH_API,
                _clip(path, 160),
                node.lineno,
                getattr(node, "end_lineno", None),
            )
            if key and owner:
                connections.append(
                    {"source": owner, "target": key, "kind": "api_request", "label": method}
                )
            continue

        local = aliases.get(head)
        if local and owner and local != owner:
            connections.append({"source": owner, "target": local, "kind": "call"})
            continue

        if len(parts) == 1:
            continue

        if receiver is None:
            continue

        if receiver in _DB_RECEIVERS or (
            leaf in _DB_METHODS and classify_arch_tag(receiver) == ARCH_FUNCTION
        ):
            key = add(
                receiver,
                ARCH_DATABASE,
                _clip(path, 160),
                node.lineno,
                getattr(node, "end_lineno", None),
            )
            if key and owner:
                connections.append(
                    {"source": owner, "target": key, "kind": "data_flow", "label": _clip(leaf, 32)}
                )

    return components, connections


def _architecture_from_text_records(
    code: str, language: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Regex-based extraction used for JS/TS and for Python that will not parse."""
    lines = code.splitlines()
    limit = config.ARCHITECTURE_MAX_NODES
    patterns = _PY_DEFINITION_PATTERNS if language == "python" else _JS_DEFINITION_PATTERNS

    components: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    keys: set[str] = set()
    definitions: list[dict[str, Any]] = []

    def add(
        name: str,
        tag: str,
        snippet: str,
        line: int | None,
        end_line: int | None,
    ) -> str | None:
        key = arch_slug(name)
        if not key:
            return None
        if key in keys:
            return key
        if len(components) >= limit:
            return None
        keys.add(key)
        components.append(
            {
                "name": " ".join(str(name).split()),
                "tag": tag,
                "code": _clip(snippet, config.NODE_CODE_MAX_CHARS),
                "line": line,
                "end_line": end_line,
                "source": "code",
            }
        )
        return key

    for number, line_text in enumerate(lines, 1):
        for pattern, declared in patterns:
            match = pattern.search(line_text)
            if not match:
                continue
            name = match.group(1)
            if declared == ARCH_FUNCTION:
                tag = ARCH_FUNCTION
            else:
                tag = classify_arch_tag(name)
                if tag == ARCH_FUNCTION:
                    tag = ARCH_SERVICE
            key = add(name, tag, line_text.strip(), number, number)
            if key:
                definitions.append({"key": key, "name": name, "line": number})
            break

    definitions.sort(key=lambda item: item["line"])
    for position, definition in enumerate(definitions):
        if position + 1 < len(definitions):
            definition["end"] = max(definition["line"], definitions[position + 1]["line"] - 1)
        else:
            definition["end"] = max(definition["line"], len(lines))

    aliases = {definition["name"]: definition["key"] for definition in definitions}

    for definition in definitions:
        region = lines[definition["line"] - 1:definition["end"]]
        owner = definition["key"]
        for region_line in region:
            for call in _TEXT_CALL_PATTERN.finditer(region_line):
                callee = call.group(1)
                if callee in _TEXT_CALL_NOISE:
                    continue
                target = aliases.get(callee)
                if target and target != owner:
                    connections.append({"source": owner, "target": target, "kind": "call"})

        joined = "\n".join(region)
        if _TEXT_HTTP_PATTERN.search(joined):
            url_match = _TEXT_URL_PATTERN.search(joined)
            target = url_match.group(1) if url_match else None
            label = f"REQUEST {target}" if target else f"REQUEST {definition['name']}"
            key = add(label, ARCH_API, _clip(joined, 400), definition["line"], definition["end"])
            if key:
                connections.append(
                    {"source": owner, "target": key, "kind": "api_request", "label": "REQUEST"}
                )

        for db_match in _TEXT_DB_PATTERN.finditer(joined):
            receiver = db_match.group(1) or "database"
            key = add(receiver, ARCH_DATABASE, _clip(joined, 400), definition["line"], definition["end"])
            if key:
                connections.append({"source": owner, "target": key, "kind": "data_flow"})

    for number, line_text in enumerate(lines, 1):
        route_match = _TEXT_ROUTE_PATTERN.search(line_text)
        if route_match:
            method = route_match.group(1).upper()
            path = route_match.group(2)
            add(f"{method} {path}", ARCH_API, line_text.strip(), number, number)

        module_match = _TEXT_MODULE_PATTERN.search(line_text)
        if module_match:
            raw = module_match.group(1).strip()
            if raw and not raw.startswith(".") and not raw.startswith("/"):
                root = raw.split("/")[0].split("@")[-1]
                tag = _ARCH_MODULE_TAGS.get(root)
                if tag is None:
                    classified = classify_arch_tag(root)
                    tag = classified if classified != ARCH_FUNCTION else None
                if tag:
                    add(root, tag, line_text.strip(), number, number)

    return components, connections


def extract_architecture_records(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract ``(components, connections)`` from a prompt or source excerpt.

    Code-shaped input is parsed structurally; anything else is treated as
    design prose. A code input that yields nothing falls back to the prose
    path so a half-structured description still produces a graph.
    """
    content = text if isinstance(text, str) else ""
    if not content.strip():
        return [], []

    if _looks_like_code(content):
        language = detect_language(content)
        try:
            if language == "python":
                components, connections = _architecture_from_python_records(content)
            else:
                components, connections = _architecture_from_text_records(content, language)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            components, connections = _architecture_from_text_records(content, language)
        if components:
            return components, connections

    return _prompt_records(content)


# ----------------------------------------------------------------------
# Graph assembly
# ----------------------------------------------------------------------

def _index_architecture_graph(current_graph: Any) -> dict[str, Any]:
    """Index an existing canvas graph for id / slug / pair de-duplication."""
    if isinstance(current_graph, dict):
        raw_nodes = current_graph.get("nodes") or []
        raw_edges = current_graph.get("edges") or []
    elif isinstance(current_graph, (list, tuple)):
        raw_nodes = current_graph
        raw_edges = []
    else:
        raw_nodes, raw_edges = [], []

    ids: set[str] = set()
    key_to_id: dict[str, str] = {}
    pairs: set[tuple[str, str]] = set()
    max_x: float | None = None

    for node in list(raw_nodes)[: config.MAX_GRAPH_NODES]:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        ids.add(node_id)
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        label = data.get("label") or node.get("label") or node_id
        key = arch_slug(label)
        if key:
            key_to_id.setdefault(key, node_id)
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        raw_x = node.get("x") if node.get("x") is not None else position.get("x")
        try:
            coordinate = float(raw_x)
        except (TypeError, ValueError):
            coordinate = None
        if coordinate is not None:
            max_x = coordinate if max_x is None else max(max_x, coordinate)

    for edge in list(raw_edges)[: config.MAX_GRAPH_EDGES]:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            pairs.add((source, target))

    return {"ids": ids, "key_to_id": key_to_id, "pairs": pairs, "max_x": max_x}


def _architecture_node(
    node_id: str,
    name: str,
    tag: str,
    *,
    code: Any,
    line: int | None,
    end_line: int | None,
    language: str,
    source: str,
) -> dict[str, Any]:
    """Build one component node in the same shape as the AST parser nodes."""
    label = _clip(" ".join(str(name).split()), config.NODE_LABEL_MAX_CHARS) or tag
    clean_code = _clip(str(code or "").strip(), config.NODE_CODE_MAX_CHARS)
    semantic_type = ARCH_TAG_TYPE.get(tag, UTILITY)
    preview = _clip(" / ".join(part.strip() for part in clean_code.splitlines()[:2]), 140)
    group_id = _group_for_semantic(semantic_type, kind=tag)
    category = _category_for(semantic_type, group_id, tag)
    description = _ARCH_TAG_DESCRIPTION.get(tag, "Component {name}.").format(
        name=_humanize(label) or label
    )
    complexity = "O(1)"
    parameters: list[str] = []
    returns = None
    is_entry = tag == ARCH_API
    layer = GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY])["order"]
    group_label = GROUP_META.get(group_id, GROUP_META[GROUP_UTILITY])["label"]

    metadata: dict[str, Any] = {
        "id": node_id,
        "type": semantic_type,
        "semantic_type": semantic_type,
        "tag": tag,
        "label": label,
        "code": clean_code,
        "preview": preview,
        "line": line,
        "end_line": end_line,
        "kind": tag,
        "async": False,
        "language": language,
        "status": "idle",
        "source": source,
        "category": category,
        "group": group_id,
        "group_label": group_label,
        "layer": layer,
        "description": description,
        "complexity": complexity,
        "parameters": parameters,
        "returns": returns,
        "parent_id": None,
        "parent_label": None,
        "decorators": [],
        "is_entry": is_entry,
    }

    node: dict[str, Any] = {
        "id": node_id,
        "type": semantic_type,
        "semantic_type": semantic_type,
        "tag": tag,
        "label": label,
        "code": clean_code,
        "x": 0,
        "y": 0,
        "position": {"x": 0, "y": 0},
        "line": line,
        "end_line": end_line,
        "language": language,
        "kind": tag,
        "async": False,
        "category": category,
        "group": group_id,
        "group_label": group_label,
        "complexity": complexity,
        "description": description,
        "parameters": parameters,
        "returns": returns,
        "layer": layer,
        "parent_id": None,
        "parent_label": None,
        "is_entry": is_entry,
        "data": metadata,
    }
    return node


def _unique_arch_id(name: str, used: set[str]) -> str:
    base = arch_node_id(name)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _layout_architecture(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    x_offset: int = 0,
    y_offset: int = 0,
) -> None:
    """Assign nested-column coordinates (depth -> x, column -> y).

    Mirrors :meth:`GraphBuilder.next_position` so an architecture graph and an
    AST graph read the same way on the canvas. Cycles are tolerated: a node is
    relaxed at most once per other node, so a circular dependency cannot spin.
    """
    if not nodes:
        return

    by_id = {str(node["id"]): node for node in nodes}
    adjacency = {node_id: [] for node_id in by_id}
    incoming = {node_id: 0 for node_id in by_id}

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in by_id and target in by_id and source != target:
            adjacency[source].append(target)
            incoming[target] += 1

    roots = [node_id for node_id in by_id if incoming[node_id] == 0] or list(by_id)
    depth = {node_id: 0 for node_id in by_id}
    relaxations = {node_id: 0 for node_id in by_id}
    frontier = list(roots)

    while frontier:
        next_frontier: list[str] = []
        for node_id in frontier:
            for child in adjacency[node_id]:
                if relaxations[child] >= len(by_id):
                    continue
                relaxations[child] += 1
                if depth[child] < depth[node_id] + 1:
                    depth[child] = depth[node_id] + 1
                    next_frontier.append(child)
        frontier = next_frontier

    columns: dict[int, int] = {}
    for node in nodes:
        node_id = str(node["id"])
        rank = depth.get(node_id, 0)
        column = columns.get(rank, 0)
        columns[rank] = column + 1
        x = x_offset + 80 + rank * config.LAYOUT_X_GAP
        y = y_offset + 80 + column * config.LAYOUT_Y_GAP
        node["x"] = x
        node["y"] = y
        node["position"] = {"x": x, "y": y}


def _architecture_stats(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    language: str,
    linked: int = 0,
    truncated: bool = False,
) -> dict[str, Any]:
    counts = {tag: 0 for tag in ARCH_TAGS}
    for node in nodes:
        tag = node.get("tag")
        if tag in counts:
            counts[tag] += 1
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "services": counts[ARCH_SERVICE],
        "databases": counts[ARCH_DATABASE],
        "apis": counts[ARCH_API],
        "functions": counts[ARCH_FUNCTION],
        "linked": linked,
        "truncated": truncated,
        "language": language,
    }


def build_architecture_payload(
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    *,
    language: str = "text",
    current_graph: Any = None,
    source: str = "prompt",
) -> dict[str, Any]:
    """Turn component/connection records into a stitched graph payload.

    Components already present on ``current_graph`` (matched by loose name
    slug) are not re-emitted - their existing ids are reused instead, so edges
    produced here connect the new components to the nodes already on screen.
    Fresh nodes are laid out to the right of the existing graph to avoid
    overlapping it.
    """
    external = _index_architecture_graph(current_graph)
    node_limit = config.ARCHITECTURE_MAX_NODES
    edge_limit = config.ARCHITECTURE_MAX_EDGES
    function_budget = max(40, int(node_limit * 0.6))

    key_to_id: dict[str, str] = dict(external["key_to_id"])
    used_ids: set[str] = set(external["ids"])
    nodes: list[dict[str, Any]] = []
    functions = 0
    truncated = False

    for component in components:
        if not isinstance(component, dict):
            continue
        name = str(component.get("name") or "").strip()
        if not name:
            continue
        key = arch_slug(name)
        if not key or key in key_to_id:
            continue
        tag = component.get("tag")
        if tag not in ARCH_TAGS:
            tag = classify_arch_tag(name)
        if tag == ARCH_FUNCTION:
            if functions >= function_budget:
                continue
            functions += 1
        if len(nodes) >= node_limit:
            truncated = True
            break

        node_id = _unique_arch_id(name, used_ids)
        used_ids.add(node_id)
        nodes.append(
            _architecture_node(
                node_id,
                name,
                tag,
                code=component.get("code") or "",
                line=component.get("line"),
                end_line=component.get("end_line"),
                language=language,
                source=str(component.get("source") or source),
            )
        )
        key_to_id[key] = node_id

    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set(external["pairs"])

    for connection in connections:
        if len(edges) >= edge_limit:
            truncated = True
            break
        if not isinstance(connection, dict):
            continue
        raw_source = str(connection.get("source") or "")
        raw_target = str(connection.get("target") or "")
        source_id = key_to_id.get(arch_slug(raw_source)) or (
            raw_source if raw_source in used_ids else None
        )
        target_id = key_to_id.get(arch_slug(raw_target)) or (
            raw_target if raw_target in used_ids else None
        )
        if not source_id or not target_id or source_id == target_id:
            continue
        pair = (source_id, target_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        kind = connection.get("kind")
        if kind not in ARCH_EDGE_KINDS:
            kind = "call"
        edge: dict[str, Any] = {
            "id": f"arc-{source_id}__{target_id}",
            "source": source_id,
            "target": target_id,
            "animated": True,
            "kind": kind,
        }
        label = connection.get("label")
        if label:
            edge["label"] = _clip(str(label), 32)
        edges.append(edge)

    fresh_ids = {str(node["id"]) for node in nodes}
    fresh_order = [str(node["id"]) for node in nodes]
    external_ids = sorted(
        str(node_id)
        for node_id in external["ids"]
        if str(node_id) not in fresh_ids
    )
    already_bridged = any(
        (edge["source"] in fresh_ids) != (edge["target"] in fresh_ids)
        for edge in edges
    )

    # When a graph is extended with nodes that have no connection to the
    # existing canvas, anchor the first fresh node to the first existing node
    # so the two halves do not read as unrelated islands. The ids are sorted
    # to keep the fallback deterministic across runs.
    if fresh_ids and external_ids and not already_bridged and len(edges) < edge_limit:
        fresh_anchor = fresh_order[0]
        external_anchor = external_ids[0]
        pair = (fresh_anchor, external_anchor)
        if pair not in seen_pairs:
            edges.append(
                {
                    "id": f"arc-{fresh_anchor}__{external_anchor}",
                    "source": fresh_anchor,
                    "target": external_anchor,
                    "animated": True,
                    "kind": "call",
                }
            )
            seen_pairs.add(pair)

    max_x = external["max_x"]
    x_offset = int(max_x + config.NODE_WIDTH + 40 - 80) if max_x is not None else 0
    _layout_architecture(nodes, edges, x_offset=max(x_offset, 0))

    fresh_ids = {str(node["id"]) for node in nodes}
    linked = sum(
        1
        for edge in edges
        if edge["source"] not in fresh_ids or edge["target"] not in fresh_ids
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "language": language,
        "entry_node": nodes[0]["id"] if nodes else None,
        "errors": [],
        "partial": False,
        "source": source,
        "groups": _build_groups(nodes),
        "tiers": _build_tiers(nodes),
        "stats": _architecture_stats(
            nodes,
            edges,
            language=language,
            linked=linked,
            truncated=truncated,
        ),
    }


def extend_architecture_graph(
    base: dict[str, Any],
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    *,
    source: str = "prompt",
) -> dict[str, Any]:
    """Stitch fresh records onto an architecture graph already in hand."""
    if not components and not connections:
        return base
    if not isinstance(base, dict):
        return build_architecture_payload(
            components, connections, source=source
        )

    base_nodes = list(base.get("nodes") or [])
    base_edges = list(base.get("edges") or [])
    language = str(base.get("language") or "text")

    extension = build_architecture_payload(
        components,
        connections,
        language=language,
        current_graph={"nodes": base_nodes, "edges": base_edges},
        source=source,
    )
    if not extension["nodes"] and not extension["edges"]:
        return base

    merged = dict(base)
    merged["nodes"] = base_nodes + extension["nodes"]
    merged["edges"] = base_edges + extension["edges"]
    merged["entry_node"] = merged["nodes"][0]["id"] if merged["nodes"] else None
    merged["groups"] = _build_groups(merged["nodes"])
    merged["tiers"] = _build_tiers(merged["nodes"])
    merged["stats"] = _architecture_stats(
        merged["nodes"],
        merged["edges"],
        language=language,
        linked=extension["stats"]["linked"],
        truncated=bool(base.get("stats", {}).get("truncated"))
        or extension["stats"]["truncated"],
    )
    return merged


def build_architecture_graph(
    text_prompt_or_code: str,
    current_graph: Any = None,
) -> dict[str, Any]:
    """Extract a component graph from a design prompt or a source excerpt.

    Returns ``{nodes, edges, language, stats, source, errors, partial}`` with
    every node tagged ``service`` / ``database`` / ``api`` / ``function``.

    Raises:
        ValueError: when the input is empty or larger than the configured limit.
    """
    text = text_prompt_or_code if isinstance(text_prompt_or_code, str) else ""
    if not text.strip():
        raise ValueError("No prompt or code supplied to the architecture extractor.")

    size = len(text.encode("utf-8", errors="ignore"))
    if size > config.ARCHITECTURE_MAX_CHARS:
        raise ValueError(
            f"Architecture input is {size} bytes which exceeds the "
            f"{config.ARCHITECTURE_MAX_CHARS} byte limit."
        )

    is_code = _looks_like_code(text)
    language = detect_language(text) if is_code else "text"
    components, connections = extract_architecture_records(text)
    source = "code" if is_code else "prompt"

    return build_architecture_payload(
        components,
        connections,
        language=language,
        current_graph=current_graph,
        source=source,
    )


# ----------------------------------------------------------------------
# Service facade
# ----------------------------------------------------------------------

class ASTParserService:
    """Stateless domain service exposing the AST graph extractors."""

    def parse_code(self, code_content: str, language: str | None = None) -> dict[str, Any]:
        """Parse a single source string into ``{nodes, edges, language, stats}``.

        When ``language`` is provided it overrides auto-detection and forces
        the corresponding builder (``python`` vs the JS/TS scanner).
        """
        if not isinstance(code_content, str):
            raise ValueError("Code payload must be a string.")

        normalized = language.strip().lower() if isinstance(language, str) else None
        if normalized in {"python", "python3", "py", "py3"}:
            return scan_security_graph(_prune_visual_noise(_build_python_graph(code_content)))
        if normalized in {
            "javascript", "javascriptreact", "typescript", "typescriptreact",
            "js", "jsx", "ts", "tsx", "node",
        }:
            return scan_security_graph(_prune_visual_noise(_build_js_graph(code_content)))
        return parse_code_to_ast_graph(code_content)

    def parse_files(self, sources: Iterable[tuple[str, str]]) -> dict[str, Any]:
        """Parse several ``(filename, content)`` pairs into a merged graph."""
        return parse_files_to_graph(sources)

    def detect_language(self, code: str) -> str:
        """Detect whether the source looks like Python or JS/TS."""
        return detect_language(code)

    def build_architecture_graph(
        self,
        text_prompt_or_code: str,
        current_graph: Any = None,
    ) -> dict[str, Any]:
        """Extract a component graph (services, databases, APIs) from text.

        ``text_prompt_or_code`` is either structural design prose or a source
        excerpt. ``current_graph`` - an existing ``{nodes, edges}`` snapshot -
        is de-duplicated against so the caller can stitch the result onto the
        canvas that is already on screen.
        """
        return build_architecture_graph(text_prompt_or_code, current_graph=current_graph)


__all__ = [
    "ASTParserService",
    "parse_code_to_ast_graph",
    "parse_files_to_graph",
    "detect_language",
    "GraphBuilder",
    "build_architecture_graph",
    "build_architecture_payload",
    "extend_architecture_graph",
    "extract_architecture_records",
    "classify_arch_tag",
    "arch_slug",
    "arch_node_id",
    "ARCH_TAGS",
    "ARCH_EDGE_KINDS",
    "ARCH_SERVICE",
    "ARCH_DATABASE",
    "ARCH_API",
    "ARCH_FUNCTION",
    "FUNCTION",
    "CONDITION",
    "LOOP",
    "ACTION",
    "VALID_TYPES",
]
