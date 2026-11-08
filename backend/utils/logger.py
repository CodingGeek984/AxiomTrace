"""Application-wide logging for the AxiomTrace backend.

Produces formatted console output plus a size-rotated file log under
``backend/logs/`` so desktop users can inspect runtime issues without
attaching a terminal.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import config

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configurator_lock = False
_configured_names: set[str] = set()


def _build_console_handler() -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def _build_file_handler() -> Optional[logging.Handler]:
    try:
        log_dir = config.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / config.LOG_FILENAME,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        return handler
    except (OSError, PermissionError):
        return None


def configure_root_logging() -> logging.Logger:
    """Idempotently configure the ``axiom`` logger tree."""
    global _configurator_lock
    root = logging.getLogger("axiom")
    if _configurator_lock:
        return root

    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.propagate = False

    if not root.handlers:
        root.addHandler(_build_console_handler())
        file_handler = _build_file_handler()
        if file_handler is not None:
            root.addHandler(file_handler)

    _configurator_lock = True
    return root


def get_logger(name: str = "axiom") -> logging.Logger:
    """Return a namespaced logger under the ``axiom`` tree."""
    configure_root_logging()
    clean = str(name or "axiom")
    if clean == "axiom" or clean.startswith("axiom."):
        logger_name = clean
    else:
        logger_name = f"axiom.{clean}"
    if logger_name not in _configured_names:
        _configured_names.add(logger_name)
    return logging.getLogger(logger_name)


__all__ = ["get_logger", "configure_root_logging"]
