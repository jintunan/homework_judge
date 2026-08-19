from __future__ import annotations

from typing import Any

from ..config import Settings
from ..db.database import Database
from ..errors import AppError
from ..files.storage import stage_submission_deletion
from ..jobs.manager import JobManager


class StudentSubmissionDeletionService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        jobs: JobManager,
    ) -> None:
        self.settings = settings
        self.database = database
        self.jobs = jobs

    async def delete(self, submission_id: str, *, actor: str) -> dict[str, Any]:
        submission = self.database.fetchone(
            "SELECT id,task_id FROM student_submissions WHERE id=?",
            (submission_id,),
        )
        if submission is None:
            raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
        revision_rows = self.database.fetchall(
            "SELECT id FROM student_processing_revisions WHERE submission_id=?",
            (submission_id,),
        )
        grading_rows = self.database.fetchall(
            "SELECT id FROM grading_runs WHERE submission_id=?",
            (submission_id,),
        )
        grading_run_ids = [str(row["id"]) for row in grading_rows]
        job_keys = [
            f"student:{submission_id}",
            f"student:{submission_id}:new-flow",
            *(f"student:{submission_id}:processing:{row['id']}" for row in revision_rows),
            *(f"grading:{run_id}" for run_id in grading_run_ids),
            *(f"grading-artifacts:{run_id}" for run_id in grading_run_ids),
        ]
        cancelled = await self.jobs.cancel(job_keys)
        staged = stage_submission_deletion(
            self.settings,
            str(submission["task_id"]),
            submission_id,
            grading_run_ids,
        )
        try:
            with self.database.transaction() as connection:
                self.database.audit(
                    connection,
                    str(submission["task_id"]),
                    "student_submission_deleted",
                    actor,
                    {
                        "submissionId": submission_id,
                        "gradingRunIds": grading_run_ids,
                    },
                )
                deleted = connection.execute(
                    "DELETE FROM student_submissions WHERE id=?",
                    (submission_id,),
                )
                if deleted.rowcount != 1:
                    raise AppError(
                        409,
                        "STUDENT_SUBMISSION_DELETE_CONFLICT",
                        "学生答卷已发生变化，请刷新后重试",
                    )
        except Exception:
            try:
                staged.rollback()
            except OSError as error:
                raise AppError(
                    500,
                    "STUDENT_SUBMISSION_FILE_ROLLBACK_FAILED",
                    "删除未完成，且答卷文件恢复失败，请检查数据目录",
                    {"reason": str(error)},
                ) from error
            raise
        cleanup_pending = staged.commit()
        return {
            "submissionId": submission_id,
            "taskId": submission["task_id"],
            "deleted": True,
            "cancelledJobs": cancelled,
            "cleanupPending": cleanup_pending,
        }
