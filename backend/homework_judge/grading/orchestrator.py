from __future__ import annotations

from typing import Any

from ..db.database import Database, now_iso
from ..db.repositories.model_runs import (
    finish_model_run_failure,
    finish_model_run_success,
    start_model_run,
)
from ..db.repositories.reviews import save_model_reviews
from ..db.repositories.submissions import (
    get_progress,
    get_submission,
    get_submission_file_record,
    list_processable_submissions,
    set_submission_status,
)
from ..db.repositories.tasks import (
    get_task_summary,
    list_questions,
    set_task_status,
)
from ..errors import AppError, ModelRequestError
from ..files.processor import prepare_model_pages
from ..jobs.manager import JobManager
from ..schemas import Subject
from .client import DashScopeGradingClient
from .output import parse_model_output


class GradingOrchestrator:
    def __init__(
        self,
        database: Database,
        client: DashScopeGradingClient,
        jobs: JobManager,
    ) -> None:
        self.database = database
        self.client = client
        self.jobs = jobs

    async def start(self) -> None:
        await self.jobs.start(self._handle_job)

    async def _validate_task(self, task_id: str) -> dict[str, Any]:
        task = await get_task_summary(self.database, task_id)
        if (
            task["answerConfigStatus"] != "approved"
            or not task["activeAnswerVersion"]
        ):
            raise AppError(
                409,
                "ANSWER_CONFIG_NOT_APPROVED",
                "请先完成答案配置审核并发布",
            )
        if int(task["questionCount"]) == 0:
            raise AppError(409, "QUESTIONS_REQUIRED", "请先录入标准答案和评分点")
        if not self.client.client.status()["configured"]:
            raise AppError(
                409,
                "MODEL_NOT_CONFIGURED",
                "请先在服务端配置 DASHSCOPE_API_KEY",
            )
        return task

    async def enqueue_task(self, task_id: str) -> int:
        await self._validate_task(task_id)
        submissions = await list_processable_submissions(self.database, task_id)
        queued = 0
        for submission in submissions:
            added = await self.jobs.submit(
                ("grading", submission["id"]),
                submission["id"],
            )
            queued += int(added)
        if queued:
            await set_task_status(self.database, task_id, "grading")
        return queued

    async def enqueue_submission(self, submission_id: str) -> bool:
        submission = await get_submission(self.database, submission_id)
        await self._validate_task(str(submission["taskId"]))
        if submission["status"] not in {"failed", "queued"}:
            raise AppError(
                409,
                "SUBMISSION_NOT_RETRYABLE",
                "只有待处理或失败的试卷可以重新批改",
            )
        added = await self.jobs.submit(
            ("grading", submission_id),
            submission_id,
        )
        if added:
            await set_task_status(self.database, str(submission["taskId"]), "grading")
        return added

    async def _handle_job(self, payload: Any) -> None:
        await self.process_one(str(payload))

    async def process_one(self, submission_id: str) -> None:
        submission = await get_submission(self.database, submission_id)
        task = await get_task_summary(self.database, str(submission["taskId"]))
        questions = await list_questions(
            self.database,
            str(submission["taskId"]),
            submission["answerVersionId"],
        )
        await set_submission_status(self.database, submission_id, "processing")
        model_run_id: str | None = None
        run_finished = False
        try:
            file = await get_submission_file_record(self.database, submission_id)
            pages = await prepare_model_pages(
                self.database.settings,
                file["relativePath"],
                file["mimeType"],
            )
            subject = Subject(str(task["subject"]))
            snapshot = self.client.build_request_snapshot(questions, pages, subject)
            model_run_id = await start_model_run(
                self.database,
                submission_id,
                self.client.model_id,
                snapshot,
            )
            response = await self.client.grade(questions, pages, subject)
            try:
                parsed = parse_model_output(
                    response.content,
                    questions,
                    self.database.settings.low_confidence_threshold,
                )
            except AppError as error:
                await finish_model_run_failure(
                    self.database,
                    model_run_id,
                    status="parse_failed",
                    raw_response=response.raw_response,
                    error_message=error.message,
                )
                run_finished = True
                raise
            await finish_model_run_success(
                self.database,
                model_run_id,
                raw_response=response.raw_response,
                parsed_output=parsed.to_dict(),
                usage=response.usage,
            )
            run_finished = True
            await save_model_reviews(
                self.database,
                submission_id,
                model_run_id,
                parsed,
            )
        except Exception as error:
            if isinstance(error, ModelRequestError):
                code = error.code
                raw = error.raw_response
            elif isinstance(error, AppError):
                code = error.code
                raw = None
            else:
                code = "GRADING_FAILED"
                raw = None
            message = str(error) if str(error) else "试卷批改失败"
            if model_run_id and not run_finished:
                await finish_model_run_failure(
                    self.database,
                    model_run_id,
                    status="request_failed",
                    raw_response=raw,
                    error_message=message,
                )
            await set_submission_status(
                self.database,
                submission_id,
                "failed",
                {"code": code, "message": message},
            )
        finally:
            await self._refresh_task_status(str(submission["taskId"]))

    async def _refresh_task_status(self, task_id: str) -> None:
        progress = await get_progress(self.database, task_id)
        if progress["queued"] + progress["processing"] > 0:
            status = "grading"
        elif progress["total"] > 0 and progress["confirmed"] == progress["total"]:
            status = "completed"
        elif progress["reviewPending"] > 0 or progress["confirmed"] > 0:
            status = "reviewing"
        else:
            status = "ready"
        await self.database.execute(
            """
            UPDATE grading_tasks SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now_iso(), task_id),
        )
