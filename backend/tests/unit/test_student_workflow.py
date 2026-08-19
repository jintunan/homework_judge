from __future__ import annotations

from pathlib import Path

import pytest

from homework_judge.db.database import Database, now_iso
from homework_judge.jobs.student_workflow import AutoGradingCoordinator


class StubGradingPipeline:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.create_count = 0

    def create_run(
        self,
        submission_id: str,
        *,
        processing_revision_id: str | None = None,
        trigger_source: str = "manual",
    ) -> str:
        self.create_count += 1
        timestamp = now_iso()
        self.database.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,processing_revision_id,trigger_source,
                 status,stage,input_hash,created_at,updated_at
               ) VALUES('auto-run',?,'task',?,?,'queued','queued','hash',?,?)""",
            (submission_id, processing_revision_id, trigger_source, timestamp, timestamp),
        )
        return "auto-run"

    async def run(self, run_id: str) -> None:
        self.database.execute(
            """UPDATE grading_runs SET status='needs_review',stage='needs_review',
               updated_at=? WHERE id=?""",
            (now_iso(), run_id),
        )


def seed_current_revision(database: Database, *, status: str) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Task','completed',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,question_region_status,created_at,updated_at
               ) VALUES('submission','task','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,status,input_hash,is_current,
                 source,created_at,updated_at
               ) VALUES('revision','submission',1,?,'input',1,'system',?,?)""",
            (status, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='revision'
               WHERE id='submission'"""
        )


@pytest.mark.asyncio
async def test_auto_grading_runs_once_for_low_confidence_recognition(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "workflow.sqlite")
    database.migrate()
    seed_current_revision(database, status="recognition_needs_review")
    grading = StubGradingPipeline(database)
    coordinator = AutoGradingCoordinator(database, grading)  # type: ignore[arg-type]

    first_run = await coordinator.run_for_current_revision("submission")
    second_run = await coordinator.run_for_current_revision("submission")

    assert first_run == "auto-run"
    assert second_run == "auto-run"
    assert grading.create_count == 1
    attempt = database.fetchone(
        "SELECT * FROM student_auto_grading_attempts WHERE processing_revision_id='revision'"
    )
    assert attempt is not None
    assert attempt["status"] == "needs_review"
    assert attempt["grading_run_id"] == "auto-run"


@pytest.mark.asyncio
async def test_auto_grading_keeps_mapping_review_as_a_hard_gate(tmp_path: Path) -> None:
    database = Database(tmp_path / "blocked.sqlite")
    database.migrate()
    seed_current_revision(database, status="mapping_needs_review")
    grading = StubGradingPipeline(database)
    coordinator = AutoGradingCoordinator(database, grading)  # type: ignore[arg-type]

    run_id = await coordinator.run_for_current_revision("submission")

    assert run_id is None
    assert grading.create_count == 0
    attempt = database.fetchone(
        "SELECT * FROM student_auto_grading_attempts WHERE processing_revision_id='revision'"
    )
    assert attempt is not None
    assert attempt["status"] == "blocked"
    assert attempt["error_code"] == "AUTO_GRADING_INPUT_NOT_READY"
