"""Rolling, size-capped log file for the `qb_mcp` loggers.

Logs request types and errors only — never qbXML response bodies, which may
hold sensitive fields. Default location is the user's local app-data folder
(`%LOCALAPPDATA%\\qb-mcp\\logs` on Windows); override with `QB_MCP_LOG_DIR`.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "qb-mcp.log"
MAX_BYTES = 1_000_000
BACKUPS = 3


def default_log_dir() -> Path:
    if env := os.environ.get("QB_MCP_LOG_DIR"):
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "qb-mcp" / "logs"


def configure_logging(
    log_dir: Path | None = None, max_bytes: int = MAX_BYTES, backups: int = BACKUPS
) -> RotatingFileHandler:
    """Attach a rotating file handler to the `qb_mcp` logger; returns it."""
    log_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / LOG_NAME, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("qb_mcp")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler
