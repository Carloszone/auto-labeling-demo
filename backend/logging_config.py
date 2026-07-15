"""Console and rotating-file logging for the HTTP demo."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from backend.settings import Settings


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(settings: Settings) -> None:
    """Configure one console handler and one persistent rotating file."""

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT)
    root = logging.getLogger()
    root.setLevel(level)
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = settings.log_path.resolve()
    if any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == str(resolved)
        for handler in root.handlers
    ):
        return
    handler = RotatingFileHandler(
        resolved,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
