import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from memory_mcp.core import PrincipalContext, RecallQuery, TurnEnvelope
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.logging import (
    configure_logging,
    content_logging_enabled,
    log_content_event,
    log_event,
    stable_reference,
)
from tests.support.fakes import (
    FakeCandidateExtractor,
    TestScenarioPolicy,
    candidate_proposal,
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


def test_content_logging_is_explicit_and_traces_capture_and_recall(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "content.log"
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    safe_expression = "以后项目周报默认用表格"
    secret = "credential-must-not-appear"

    try:
        configure_logging(level="INFO", log_file=log_path, content=False)
        log_content_event("content.disabled", content=safe_expression)
        assert content_logging_enabled() is False
        assert safe_expression not in log_path.read_text(encoding="utf-8")

        configure_logging(level="INFO", log_file=log_path, content=True)
        assert content_logging_enabled() is True
        service = create_memory_service(
            InMemoryMemoryRepository(),
            [TestScenarioPolicy()],
            candidate_extractor=FakeCandidateExtractor(
                (
                    candidate_proposal(
                        safe_expression,
                        save_rationale="明确且持续有效的工作偏好",
                    ),
                )
            ),
        )
        principal = PrincipalContext("owner-a")
        service.capture_turn(
            principal,
            TurnEnvelope(
                scenario="project-work",
                conversation_id="run-a",
                source_turn_id="run-a-1",
                content=f"密码是 {secret}。{safe_expression}。",
                observed_at=datetime(2026, 7, 31, 10, tzinfo=UTC),
            ),
        )
        service.recall_memory(
            principal,
            RecallQuery(
                scenario="project-work",
                query=f"密码是 {secret}。项目周报 表格",
                subject="weekly-report",
            ),
        )

        output = log_path.read_text(encoding="utf-8")
        for event in (
            "logging.content.enabled",
            "memory.capture.input",
            "memory.capture.candidates",
            "memory.capture.admission",
            "memory.capture.persisted",
            "memory.recall.input",
            "memory.recall.ranked",
            "memory.recall.output",
        ):
            assert f'event="{event}"' in output
        assert safe_expression in output
        assert "项目周报默认使用表格" in output
        assert secret not in output
        assert "[REDACTED:credential]" in output
    finally:
        configure_logging(level="INFO", log_file=None, content=False)
        _restore_logging(root_logger, original_handlers, original_level)


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
