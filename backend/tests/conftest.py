"""Pytest configuration for the AxiomTrace backend.

The suite runs entirely offline:

* the database lives in memory,
* the sandbox falls back to the in-process watchdog (``AXIOM_ENV=testing``),
* the AI layer always uses the deterministic heuristic scanner.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("AXIOM_ENV", "testing")
os.environ.setdefault("AXIOM_LOG_LEVEL", "CRITICAL")


@pytest.fixture(scope="session")
def app():
    """The configured Flask application used by the HTTP level tests."""
    from app import create_app

    application = create_app("testing")
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    """A Flask test client bound to the in-memory database."""
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def sandbox():
    """The sandbox service facade."""
    from services import SandboxExecutionService

    return SandboxExecutionService()


@pytest.fixture()
def parser():
    """The AST parser facade."""
    from services import ASTParserService

    return ASTParserService()
