from __future__ import annotations

import asyncio
from typing import Any

from ..db.database import Database, now_iso
from ..db.repositories.answer_config import (
    create_answer_version,
    get_answer_config_progress,
    get_answer_version,
    get_latest_answer_version,
    list_answer_drafts,
    replace_drafts,
)
from ..db.repositories.answer_runs import list_answer_runs
from ..db.repositories.tasks import get_task_summary
from ..errors import AppError, ModelRequestError, require_found
from ..jobs.manager import JobManager
from ..schemas import AnswerMode
from .extractor import VisionQuestionExtractor
from .resolver import AnswerResolver


class AnswerConfigOrchestrator:
    def __init__(
        self,
        database: Database,
        extractor: VisionQuestionExtractor,
        resolver: AnswerResolver,
        jobs: JobManager,
    ) -> None:
        self.database = database
        self.extractor = extractor
        self.resolver = resolver
        self.jobs = jobs

    async def start(self) -> None:
        await self.jobs.start(self._handle_job)

    async def start_task(self, task_id: str) -> dict[str, Any]:
        task = require_found(
            await self.database.fetch_one(
                """
                SELECT id, answer_mode, reference_answer_file_id,
                       answer_config_status
                FROM grading_tasks WHERE id = ?
                """,
                (task_id,),
            ),
            "批改任务不存在",
        )
        if (
            task["answer_mode"] == AnswerMode.REFERENCE_UPLOAD.value
            and not task["reference_answer_file_id"]
        ):
            raise AppError(
                422,
                "REFERENCE_ANSWER_REQUIRED",
                "参考答案模式必须上传参考答案文件",
            )
        latest = await get_latest_answer_version(self.database, task_id)
        running = task["answer_config_status"] in {
            "queued",
            "extracting",
            "searching",
            "generating",
        }
        if running and latest and latest["status"] in {"draft", "review_pending"}:
            await self.jobs.submit(
                ("answer_config", task_id, latest["id"]),
                ("task", task_id, latest["id"]),
            )
            return latest
        version = await create_answer_version(
            self.database,
            task_id,
            str(task["answer_mode"]),
        )
        await self.jobs.submit(
            ("answer_config", task_id, version["id"]),
            ("task", task_id, version["id"]),
        )
        return version

    async def _handle_job(self, payload: Any) -> None:
        kind, first, second = payload
        if kind == "task":
            await self.process_task(str(first), str(second))
        elif second == "regenerate":
            await self.resolver.regenerate(str(first))
        else:
            await self.resolver.resolve(str(first))

    async def enqueue_draft(self, draft_id: str, action: str) -> bool:
        if action not in {"research", "regenerate"}:
            raise ValueError("unsupported draft action")
        return await self.jobs.submit(
            ("answer_draft", draft_id, action),
            ("draft", draft_id, action),
        )

    async def process_task(self, task_id: str, version_id: str) -> None:
        await self.database.execute(
            """
            UPDATE grading_tasks
            SET answer_config_status = 'extracting', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), task_id),
        )
        try:
            _run_id, paper, has_reference = await self.extractor.extract(
                task_id,
                version_id,
            )
            source_type = "reference_extracted" if has_reference else None
            drafts = await replace_drafts(
                self.database,
                version_id,
                paper,
                source_type,
            )
            if not has_reference:
                await self.database.execute(
                    """
                    UPDATE grading_tasks
                    SET answer_config_status = 'searching', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), task_id),
                )
                semaphore = asyncio.Semaphore(
                    self.database.settings.answer_config_concurrency
                )

                async def resolve_one(draft_id: str) -> None:
                    async with semaphore:
                        await self.resolver.resolve(draft_id)

                await asyncio.gather(
                    *(resolve_one(str(draft["id"])) for draft in drafts),
                    return_exceptions=True,
                )
            await self.database.execute(
                """
                UPDATE grading_tasks
                SET answer_config_status = 'review_pending', updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), task_id),
            )
        except (AppError, ModelRequestError) as error:
            await self.database.execute(
                """
                UPDATE grading_tasks
                SET answer_config_status = 'failed', updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), task_id),
            )
            await self.database.execute(
                """
                UPDATE answer_config_versions
                SET status = 'draft'
                WHERE id = ? AND status != 'superseded'
                """,
                (version_id,),
            )
            raise error

    async def get_detail(self, task_id: str) -> dict[str, Any]:
        task = await get_task_summary(self.database, task_id)
        version = await get_latest_answer_version(self.database, task_id)
        if version is None:
            return {
                "task": task,
                "version": None,
                "drafts": [],
                "progress": {
                    "total": 0,
                    "pending": 0,
                    "processing": 0,
                    "webSearched": 0,
                    "modelGenerated": 0,
                    "needsAttention": 0,
                    "approved": 0,
                    "rejected": 0,
                    "failed": 0,
                },
                "runs": [],
            }
        return {
            "task": task,
            "version": await get_answer_version(self.database, str(version["id"])),
            "drafts": await list_answer_drafts(self.database, str(version["id"])),
            "progress": await get_answer_config_progress(
                self.database,
                str(version["id"]),
            ),
            "runs": await list_answer_runs(self.database, str(version["id"])),
        }
