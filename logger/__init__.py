"""Centralized logging configuration for the application.

Importing this module initializes logging once:

    from logger import get_logger

    logger = get_logger("my_module")
    logger.info("something happened")
"""

import logging
import logging.handlers
import os
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-5s | %(name)-12s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_initialized = False

# Third-party loggers that are too noisy at INFO level
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "anthropic",
    "langchain",
    "langchain_openai",
    "langchain_anthropic",
    "langgraph",
    "urllib3",
    "asyncio",
    "uvicorn.access",
)


def setup_logging(level: str | None = None) -> None:
    """Configure root logger with console + rotating-file handlers.

    Safe to call multiple times - only initializes once.
    Log level precedence: argument > LOG_LEVEL env var > INFO.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    effective_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, effective_level, logging.INFO))

    fmt = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given name."""
    return logging.getLogger(name)


setup_logging()
