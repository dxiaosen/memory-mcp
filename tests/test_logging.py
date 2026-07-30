import logging
from pathlib import Path
from uuid import uuid4

from memory_mcp.logging import (
    configure_logging,
    log_event,
    stable_reference,
)


def test_logging_writes_structured_event_and_redacts_sensitive_fields() -> None:
    log_directory = Path(".memory-mcp/test-logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"{uuid4().hex}.log"
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level

    try:
        configure_logging(level="DEBUG", log_file=log_path)
        log_event(
            logging.getLogger("memory_mcp.test"),
            logging.INFO,
            "test.completed",
            count=2,
            query="private question",
        )

        output = log_path.read_text(encoding="utf-8")
        assert 'event="test.completed"' in output
        assert "count=2" in output
        assert 'query="[REDACTED]"' in output
        assert "private question" not in output
    finally:
        _restore_logging(root_logger, original_handlers, original_level)
        log_path.unlink(missing_ok=True)


def test_logging_configuration_is_idempotent() -> None:
    log_directory = Path(".memory-mcp/test-logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"{uuid4().hex}.log"
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level

    try:
        configure_logging(level="INFO", log_file=log_path)
        configure_logging(level="INFO", log_file=log_path)
        log_event(
            logging.getLogger("memory_mcp.test"),
            logging.INFO,
            "single.event",
        )

        output = log_path.read_text(encoding="utf-8")
        assert output.count('event="single.event"') == 1
    finally:
        _restore_logging(root_logger, original_handlers, original_level)
        log_path.unlink(missing_ok=True)


def test_stable_reference_is_deterministic_without_exposing_identifier() -> None:
    first = stable_reference("analyst-a")
    second = stable_reference("analyst-a")

    assert first == second
    assert len(first) == 12
    assert "analyst-a" not in first


def _restore_logging(
    root_logger: logging.Logger,
    original_handlers: list[logging.Handler],
    original_level: int,
) -> None:
    for handler in tuple(root_logger.handlers):
        if handler not in original_handlers:
            root_logger.removeHandler(handler)
            handler.close()
    root_logger.handlers[:] = original_handlers
    root_logger.setLevel(original_level)
