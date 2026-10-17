"""Rotating log file in %APPDATA%\\Orion\\logs."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

LOG_NAME = "background-agent.log"


def setup(cfg: dict, console: bool = False) -> logging.Logger:
    logger = logging.getLogger("orion_bg")
    if getattr(logger, "_orion_configured", False):
        return logger
    level = getattr(logging, str(cfg["logging"]["level"]).upper(), logging.INFO)
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")
    handler = RotatingFileHandler(
        paths.log_dir() / LOG_NAME,
        maxBytes=int(cfg["logging"]["max_bytes"]),
        backupCount=int(cfg["logging"]["backup_count"]),
        encoding="utf-8",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    # pythonw has no stderr; only add a console handler when one exists.
    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    logger._orion_configured = True  # type: ignore[attr-defined]
    return logger
