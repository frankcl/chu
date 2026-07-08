"""Tests for agent/log.py — centralized logging configuration."""

import logging


def test_get_logger_returns_correct_name():
    from agent.log import get_logger
    logger = get_logger("my_module")
    assert logger.name == "my_module"


def test_get_logger_is_standard_logger():
    from agent.log import get_logger
    logger = get_logger("test_logger")
    assert isinstance(logger, logging.Logger)


def test_setup_logging_is_idempotent():
    """Calling setup_logging() twice must not add duplicate handlers."""
    import agent.log as log_module

    # Reset the module-level flag so we can test fresh
    original = log_module._initialized
    log_module._initialized = False
    root_before = len(logging.getLogger().handlers)

    log_module.setup_logging()
    handlers_after_first = len(logging.getLogger().handlers)

    log_module.setup_logging()  # second call — must be a no-op
    handlers_after_second = len(logging.getLogger().handlers)

    assert handlers_after_first == handlers_after_second, (
        "setup_logging() added extra handlers on second call"
    )

    # Restore original state
    log_module._initialized = original


def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    """setup_logging() should create logs/app.log relative to cwd."""
    import agent.log as log_module

    # Reset so setup runs fresh, and redirect log dir to tmp_path
    original_init = log_module._initialized
    log_module._initialized = False

    # Patch Path("logs") to write inside tmp_path
    monkeypatch.chdir(tmp_path)

    log_module.setup_logging()

    assert (tmp_path / "logs" / "app.log").exists()

    # Restore
    log_module._initialized = original_init


def test_noisy_loggers_are_suppressed():
    """Third-party loggers should be set to WARNING or higher after setup."""
    from agent.log import _NOISY_LOGGERS
    for name in _NOISY_LOGGERS:
        logger = logging.getLogger(name)
        assert logger.level >= logging.WARNING, (
            f"Expected {name} logger to be WARNING+, got level {logger.level}"
        )
