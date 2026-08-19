from __future__ import annotations

import json
import logging

from homework_judge.observability import JsonLogFormatter, bind_log_context


def test_json_log_keeps_correlation_ids_and_drops_answer_content() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="homework_judge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="grading_progress",
        args=(),
        exc_info=None,
    )
    record.event_fields = {  # type: ignore[attr-defined]
        "current": 2,
        "total": 10,
        "answer_text": "不得进入日志的学生答案",
        "local_path": "C:/private/student.jpg",
    }

    with bind_log_context(submission_id="submission-1", grading_run_id="run-1"):
        value = json.loads(formatter.format(record))

    assert value["event"] == "grading_progress"
    assert value["submission_id"] == "submission-1"
    assert value["grading_run_id"] == "run-1"
    assert value["current"] == 2
    assert value["total"] == 10
    assert "answer_text" not in value
    assert "local_path" not in value
