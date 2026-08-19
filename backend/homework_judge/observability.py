from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import Settings

_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "homework_judge_log_context",
    default=None,
)

# Only identifiers and non-sensitive operational metadata are accepted. In particular,
# answer text, model prompts, uploaded filenames, and local file paths must not enter logs.
_CONTEXT_KEYS = {
    "request_id",
    "job_id",
    "task_id",
    "submission_id",
    "processing_revision_id",
    "grading_run_id",
    "question_id",
}
_EVENT_KEYS = {
    "method",
    "route",
    "status_code",
    "duration_ms",
    "stage",
    "status",
    "page_count",
    "question_count",
    "current",
    "total",
    "reason_codes",
    "attempt",
    "error_code",
    "trigger_source",
    "question_id",
    "question_number",
    "question_type",
    "recognition_path",
    "batch_count",
    "fast_request_count",
    "fallback_request_count",
    "error_type",
}


class JsonLogFormatter(logging.Formatter):
    """Render one compact JSON object per line for machine-readable local logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_CONTEXT.get() or {})
        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            payload.update(
                {
                    key: value
                    for key, value in event_fields.items()
                    if key in _EVENT_KEYS and value is not None
                }
            )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure the application logger once at service startup."""

    logger = logging.getLogger("homework_judge")
    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = JsonLogFormatter()
    if settings.log_to_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    if settings.log_to_file and settings.log_file_path is not None:
        log_path = Path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Keep diagnostics observable even if both configured destinations are disabled.
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


@contextmanager
def bind_log_context(**values: object) -> Iterator[None]:
    """Temporarily attach safe correlation identifiers to all nested log records."""

    merged = dict(_CONTEXT.get() or {})
    merged.update(
        {
            key: str(value)
            for key, value in values.items()
            if key in _CONTEXT_KEYS and value is not None
        }
    )
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    """Emit an allow-listed operational event without leaking answer content."""

    safe_fields = {key: value for key, value in fields.items() if key in _EVENT_KEYS}
    logger.log(level, event, extra={"event_fields": safe_fields})
