from __future__ import annotations

import logging
import uuid

from ..db.database import Database, now_iso
from ..errors import AppError
from ..observability import bind_log_context, log_event
from .grading_pipeline import GradingPipeline
from .student_pipeline import StudentPipeline

LOGGER = logging.getLogger("homework_judge.student_workflow")


class AutoGradingCoordinator:
    """Start grading exactly once for each current student processing revision."""

    def __init__(self, database: Database, grading: GradingPipeline) -> None:
        self.database = database
        self.grading = grading

    async def run_for_current_revision(self, submission_id: str) -> str | None:
        submission = self.database.fetchone(
            """SELECT id,current_processing_revision_id,question_region_status
               FROM student_submissions WHERE id=?""",
            (submission_id,),
        )
        if not submission or not submission.get("current_processing_revision_id"):
            return None
        revision_id = str(submission["current_processing_revision_id"])
        revision = self.database.fetchone(
            """SELECT * FROM student_processing_revisions
               WHERE id=? AND submission_id=? AND is_current=1""",
            (revision_id, submission_id),
        )
        timestamp = now_iso()
        attempt_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO student_auto_grading_attempts(
                     id,submission_id,processing_revision_id,status,created_at,updated_at
                   ) VALUES(?,?,?,'pending',?,?)""",
                (attempt_id, submission_id, revision_id, timestamp, timestamp),
            )
            attempt = connection.execute(
                """SELECT * FROM student_auto_grading_attempts
                   WHERE processing_revision_id=?""",
                (revision_id,),
            ).fetchone()
        if not attempt:
            return None
        attempt_id = str(attempt["id"])
        if attempt["status"] in {"running", "completed", "needs_review"}:
            return str(attempt["grading_run_id"]) if attempt["grading_run_id"] else None

        # Alignment/mapping failures remain hard gates. Recognition uncertainty is
        # deliberately not a gate and is carried into per-question review reasons.
        if (
            not revision
            or revision["status"] not in {"ready", "recognition_needs_review"}
            or submission.get("question_region_status") != "ready"
        ):
            self._update_attempt(
                attempt_id,
                "blocked",
                error_code="AUTO_GRADING_INPUT_NOT_READY",
                error_message="配准或题框映射尚未达到可批改条件",
            )
            return None

        with bind_log_context(
            submission_id=submission_id,
            processing_revision_id=revision_id,
        ):
            try:
                run_id = self.grading.create_run(
                    submission_id,
                    processing_revision_id=revision_id,
                    trigger_source="automatic",
                )
            except AppError as error:
                self._update_attempt(
                    attempt_id,
                    "blocked",
                    error_code=error.code,
                    error_message=error.message,
                )
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "auto_grading_blocked",
                    error_code=error.code,
                )
                return None

            self._update_attempt(attempt_id, "running", grading_run_id=run_id)
            log_event(
                LOGGER,
                logging.INFO,
                "auto_grading_started",
                trigger_source="automatic",
            )
            await self.grading.run(run_id)
            run = self.database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
            run_status = str(run["status"]) if run else "failed"
            status = (
                "completed"
                if run_status == "completed"
                else "needs_review"
                if run_status == "needs_review"
                else "failed"
            )
            self._update_attempt(
                attempt_id,
                status,
                grading_run_id=run_id,
                error_code=str(run["error_code"]) if run and run.get("error_code") else None,
                error_message=(
                    str(run["error_message"])
                    if run and run.get("error_message")
                    else None
                ),
            )
            log_event(
                LOGGER,
                logging.INFO if status != "failed" else logging.ERROR,
                "auto_grading_finished",
                status=status,
            )
            return run_id

    def _update_attempt(
        self,
        attempt_id: str,
        status: str,
        *,
        grading_run_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.database.execute(
            """UPDATE student_auto_grading_attempts
               SET status=?,grading_run_id=COALESCE(?,grading_run_id),
                   error_code=?,error_message=?,updated_at=? WHERE id=?""",
            (status, grading_run_id, error_code, error_message, now_iso(), attempt_id),
        )


class StudentSubmissionWorkflow:
    """Compose processing and automatic grading into one observable background job."""

    def __init__(
        self,
        student: StudentPipeline,
        auto_grading: AutoGradingCoordinator,
    ) -> None:
        self.student = student
        self.auto_grading = auto_grading

    async def process(self, submission_id: str) -> None:
        with bind_log_context(submission_id=submission_id):
            log_event(LOGGER, logging.INFO, "student_processing_started")
            await self.student.run(submission_id)
            await self.auto_grading.run_for_current_revision(submission_id)
            log_event(LOGGER, logging.INFO, "student_workflow_finished")

    async def resume_recognition(self, submission_id: str) -> None:
        with bind_log_context(submission_id=submission_id):
            log_event(LOGGER, logging.INFO, "student_recognition_resumed")
            await self.student.resume_current_recognition(submission_id)
            await self.auto_grading.run_for_current_revision(submission_id)
