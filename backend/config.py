"""AxiomTrace backend configuration.

Central place for Flask settings, SQLite binding, sandbox limits and
AI provider credentials. Every value can be overridden through environment
variables so the desktop shell (electron/main.js) can tune the runtime
without editing source code.

Design rules enforced here:

* Every tunable has a safe default - the app boots with zero configuration.
* Sandbox limits are deliberately tight (2.0 s wall clock, 3 M bytecode
  instructions, 512 MB address space) so a runaway node can never wedge
  the API server.
* Memory-hygiene knobs (log retention, trace caps) are declared next to the
  feature they bound so they are easy to audit.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


def _env(key: str, default: str | None = None) -> str | None:
    """Read an environment variable with an optional default."""
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    """Shared configuration for every AxiomTrace runtime."""

    APP_NAME = "AxiomTrace"
    APP_VERSION = "1.1.0"
    DEBUG = False
    TESTING = False

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY = _env("AXIOM_SECRET_KEY") or _env("SECRET_KEY") or secrets.token_hex(32)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = _env_int("AXIOM_MAX_CONTENT_LENGTH", 8 * 1024 * 1024)

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------
    HOST = _env("AXIOM_HOST", "127.0.0.1") or "127.0.0.1"
    PORT = _env_int("AXIOM_PORT", 5000)
    CORS_ORIGINS = [
        origin.strip()
        for origin in (
            _env(
                "AXIOM_CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,"
                "http://localhost:3001,http://127.0.0.1:3001,"
                "http://localhost:3002,http://127.0.0.1:3002,"
                "http://localhost:3005,http://127.0.0.1:3005,"
                "http://localhost:8080,http://127.0.0.1:8080,"
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8000,http://127.0.0.1:8000",
            )
            or ""
        ).split(",")
        if origin.strip()
    ]
    # Any localhost port is a legitimate origin for the dev UI; pin the
    # exact list above for production and use the regex as a safety net.
    CORS_ORIGIN_REGEX = _env(
        "AXIOM_CORS_REGEX",
        r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$",
    )

    # ------------------------------------------------------------------
    # Persistence - sqlite:///axiom_trace.db (resolved under backend/)
    # ------------------------------------------------------------------
    DATABASE_FILENAME = _env("AXIOM_DB_FILENAME", "axiom_trace.db") or "axiom_trace.db"
    DATABASE_PATH = Path(_env("AXIOM_DATABASE_PATH") or (BASE_DIR / DATABASE_FILENAME))
    SQLALCHEMY_DATABASE_URI = (
        _env("AXIOM_DATABASE_URI") or f"sqlite:///{DATABASE_PATH}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False, "timeout": 20},
    }
    DATABASE_ECHO = _env_bool("AXIOM_DB_ECHO", False)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL = (_env("AXIOM_LOG_LEVEL", "INFO") or "INFO").upper()
    LOG_DIR = Path(_env("AXIOM_LOG_DIR") or (BASE_DIR / "logs"))
    LOG_FILENAME = _env("AXIOM_LOG_FILENAME", "axiom_backend.log") or "axiom_backend.log"
    LOG_FILE = LOG_DIR / LOG_FILENAME
    LOG_MAX_BYTES = _env_int("AXIOM_LOG_MAX_BYTES", 2 * 1024 * 1024)
    LOG_BACKUP_COUNT = _env_int("AXIOM_LOG_BACKUP_COUNT", 3)

    # ------------------------------------------------------------------
    # Sandbox execution limits
    # ------------------------------------------------------------------
    # Hard wall-clock ceiling for a single node run. The spec target is a
    # strict 2.0 s budget - long enough for real work, short enough that a
    # runaway loop is reported (not merely delayed).
    SANDBOX_TIMEOUT_SECONDS = _env_float("AXIOM_SANDBOX_TIMEOUT", 2.0)
    # Per-case ceiling during a mutation sweep. Kept lower than the single
    # run budget so a 18-case corpus still finishes inside one HTTP call.
    MUTATION_TIMEOUT_SECONDS = _env_float("AXIOM_MUTATION_TIMEOUT", 1.0)
    # Total wall-clock ceiling for one full mutation sweep.
    MUTATION_TOTAL_BUDGET_SECONDS = _env_float("AXIOM_MUTATION_BUDGET", 30.0)
    SANDBOX_MAX_OUTPUT_CHARS = _env_int("AXIOM_SANDBOX_MAX_OUTPUT", 20000)
    SANDBOX_MAX_TRACEBACK_LINES = _env_int("AXIOM_SANDBOX_MAX_TRACE", 40)
    # Bytecode instruction ceiling enforced inside the sandbox. Stops
    # ``while True: pass`` long before the wall clock trips.
    SANDBOX_MAX_INSTRUCTIONS = _env_int("AXIOM_SANDBOX_MAX_INSTRUCTIONS", 3_000_000)
    # Address-space ceiling (MiB) applied to the isolated worker process.
    # 0 disables the rlimit (e.g. on platforms without ``resource``).
    SANDBOX_MEMORY_LIMIT_MB = _env_int("AXIOM_SANDBOX_MEMORY_MB", 512)
    # ``process`` (fork/spawn worker, hard kill on timeout),
    # ``thread`` (in-process watchdog) or ``auto`` (process, fall back to
    # thread when the platform refuses to fork).
    SANDBOX_ISOLATION = (_env("AXIOM_SANDBOX_ISOLATION", "auto") or "auto").lower()

    # Edge-case corpus used by mutation stress testing. The list mixes
    # "empty" values, type confusions, non-finite floats, malformed JSON
    # payloads, unicode and bulk data so a node is probed against the
    # inputs that actually break naive code.
    MUTATION_EDGE_INPUTS = [
        None,                       # missing value
        "",                         # empty string
        0,                          # zero
        [],                         # empty list
        {},                         # empty dict
        -1,                         # negative
        999999999,                  # large int
        True,                       # boolean (truthy)
        False,                      # boolean (falsy)
        float("nan"),               # not-a-number float
        float("inf"),               # infinite float
        "not-a-number",             # non-numeric string
        "{malformed json",          # malformed JSON payload
        '{"truncated": ',           # truncated JSON payload
        [1, 2, 3],                  # populated list
        {"key": "value"},           # populated dict
        "unicode-\u00e9\u4f60\u597d",  # multi-byte string
        10 ** 30,                   # very large int
    ]
    MUTATION_MAX_STACK_TRACES = 12
    MUTATION_MAX_ITERATIONS = 5

    # ------------------------------------------------------------------
    # Parser limits
    # ------------------------------------------------------------------
    PARSE_MAX_BYTES = _env_int("AXIOM_PARSE_MAX_BYTES", 2 * 1024 * 1024)
    NODE_LABEL_MAX_CHARS = _env_int("AXIOM_NODE_LABEL_MAX", 72)
    NODE_CODE_MAX_CHARS = _env_int("AXIOM_NODE_CODE_MAX", 4000)
    # When true a Python syntax error does not abort the request: the
    # parser salvages a partial graph and reports the error alongside it.
    PARSE_PARTIAL_RECOVERY = _env_bool("AXIOM_PARSE_PARTIAL_RECOVERY", True)

    # ------------------------------------------------------------------
    # AI agent (OpenAI compatible - also serves DeepSeek)
    # ------------------------------------------------------------------
    AI_API_KEY = (
        _env("DEEPSEEK_API_KEY")
        or _env("AXIOM_AI_API_KEY")
        or _env("OPENAI_API_KEY")
    )
    AI_BASE_URL = (
        _env("DEEPSEEK_BASE_URL")
        or _env("AXIOM_AI_BASE_URL")
        or _env("OPENAI_BASE_URL")
        or "https://api.deepseek.com/v1"
    )
    AI_MODEL = (
        _env("DEEPSEEK_MODEL")
        or _env("AXIOM_AI_MODEL")
        or _env("OPENAI_MODEL")
        or "deepseek-chat"
    )
    DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY") or AI_API_KEY
    DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL") or "deepseek-chat"
    AI_TIMEOUT_SECONDS = _env_float("AXIOM_AI_TIMEOUT", 30.0)
    AI_MAX_TOKENS = _env_int("AXIOM_AI_MAX_TOKENS", 1400)
    # Heuristic used to turn a character count into a token estimate.
    AI_CHARS_PER_TOKEN = _env_int("AXIOM_AI_CHARS_PER_TOKEN", 4)
    # Hard ceiling for a generated context dump. Keeps a 500-node lasso
    # selection from producing a prompt that no model will accept.
    AI_CONTEXT_MAX_CHARS = _env_int("AXIOM_AI_CONTEXT_MAX_CHARS", 60000)

    # ------------------------------------------------------------------
    # Auto-Architect - prompt/code -> component (architecture) graph
    # ------------------------------------------------------------------
    # Ceiling for a single architecture request. The input is either a design
    # prompt or a source excerpt, both of which are far smaller than the full
    # parse limit, so this stays tight.
    ARCHITECTURE_MAX_CHARS = _env_int("AXIOM_ARCH_MAX_CHARS", 100_000)
    # Component / connection caps applied when the extracted graph is built.
    # The function-shaped nodes get a share of the budget so a 400-function
    # file cannot crowd every service, database and API node out of the view.
    ARCHITECTURE_MAX_NODES = _env_int("AXIOM_ARCH_MAX_NODES", 400)
    ARCHITECTURE_MAX_EDGES = _env_int("AXIOM_ARCH_MAX_EDGES", 800)
    # Ask the configured model to refine a heuristic architecture extraction.
    # Automatically skipped when no API key is present, so the endpoint keeps
    # working offline.
    AI_ARCHITECTURE_ENRICHMENT = _env_bool("AXIOM_AI_ARCH_ENRICHMENT", True)

    # ------------------------------------------------------------------
    # Memory hygiene
    # ------------------------------------------------------------------
    # Maximum number of execution-log rows kept in the local database.
    # Older rows are pruned on write so the scratch DB never grows without
    # bound.
    EXECUTION_LOG_RETENTION = _env_int("AXIOM_LOG_RETENTION", 500)
    # Maximum number of nodes persisted in a single graph snapshot.
    MAX_GRAPH_NODES = _env_int("AXIOM_MAX_GRAPH_NODES", 5000)
    MAX_GRAPH_EDGES = _env_int("AXIOM_MAX_GRAPH_EDGES", 10000)

    # Visual graph defaults (also used when a document has no saved layout)
    NODE_WIDTH = 300
    NODE_HEIGHT = 118
    LAYOUT_X_GAP = 380
    LAYOUT_Y_GAP = 170


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = True
    DATABASE_PATH = Path(":memory:")
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    # Tests must never fork a worker process - the in-process watchdog is
    # deterministic and keeps the suite hermetic.
    SANDBOX_ISOLATION = "thread"
    SANDBOX_TIMEOUT_SECONDS = 2.0
    MUTATION_TIMEOUT_SECONDS = 1.0


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None) -> type[BaseConfig]:
    """Resolve a configuration class from AXIOM_ENV (defaults to development)."""
    env_name = (name or _env("AXIOM_ENV", "development") or "development").lower()
    config_cls = CONFIGS.get(env_name)
    if config_cls is None:
        raise KeyError(
            f"Unknown AXIOM_ENV '{env_name}'. Expected one of: {', '.join(CONFIGS)}"
        )
    return config_cls


config = get_config()

__all__ = [
    "BASE_DIR",
    "PROJECT_ROOT",
    "BaseConfig",
    "DevelopmentConfig",
    "ProductionConfig",
    "TestingConfig",
    "CONFIGS",
    "get_config",
    "config",
]
