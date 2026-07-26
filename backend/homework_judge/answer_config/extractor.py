from __future__ import annotations

from typing import Any

from ..db.database import Database
from ..db.repositories.answer_runs import (
    finish_answer_run_failure,
    finish_answer_run_success,
    start_answer_run,
)
from ..errors import AppError, ModelRequestError, require_found
from ..files.processor import PreparedPage, prepare_model_pages
from ..model.dashscope import DashScopeClient
from ..schemas import AnswerMode, ParsedPaper, Subject
from .parser import JsonCandidateError, parse_extracted_paper
from .prompts import (
    ANSWER_EXTRACTION_PROMPT_VERSION,
    STRUCTURE_REPAIR_PROMPT_VERSION,
    build_extraction_system_prompt,
    build_extraction_user_prompt,
    build_structure_repair_prompt,
)


def _page_snapshot(pages: list[PreparedPage]) -> list[dict[str, Any]]:
    return [
        {
            "pageNumber": page.page_number,
            "mimeType": page.mime_type,
            "byteLength": page.byte_length,
        }
        for page in pages
    ]


class VisionQuestionExtractor:
    def __init__(self, database: Database, client: DashScopeClient) -> None:
        self.database = database
        self.client = client

    async def _repair(
        self,
        *,
        task_id: str,
        version_id: str,
        subject: Subject,
        answer_mode: AnswerMode,
        original_run_id: str,
        content: str,
    ) -> tuple[str, ParsedPaper]:
        run_id = await start_answer_run(
            self.database,
            task_id=task_id,
            version_id=version_id,
            kind="structure_repair",
            provider="阿里云百炼",
            model=self.client.model_id,
            request_snapshot=self.client.snapshot(
                prompt_version=STRUCTURE_REPAIR_PROMPT_VERSION,
                purpose="answer_structure_repair",
                details={
                    "originalRunId": original_run_id,
                    "subject": subject.value,
                    "answerMode": answer_mode.value,
                    "inputCharacters": len(content),
                    "imagesIncluded": False,
                },
            ),
        )
        try:
            response = await self.client.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "你只修复 JSON 结构，不补写、推断或求解题目。",
                    },
                    {"role": "user", "content": build_structure_repair_prompt(content)},
                ],
                temperature=0,
            )
            parsed = parse_extracted_paper(
                response.content,
                answer_mode=answer_mode,
                subject=subject,
                repaired=True,
            )
            if not parsed.questions:
                raise JsonCandidateError("修复结果仍没有可用题目")
            await finish_answer_run_success(
                self.database,
                run_id,
                raw_response=response.raw_response,
                parsed_output=parsed.model_dump(by_alias=True, mode="json"),
                usage=response.usage,
            )
            return run_id, parsed
        except JsonCandidateError as error:
            await finish_answer_run_failure(
                self.database,
                run_id,
                status="parse_failed",
                error_code="ANSWER_STRUCTURE_REPAIR_INVALID",
                error_message=str(error),
            )
            raise AppError(
                502,
                "ANSWER_EXTRACTION_INVALID",
                "模型返回的试卷结构无法解析，结构修复后仍无可用题目",
                {"runId": run_id, "originalRunId": original_run_id},
            ) from error
        except ModelRequestError as error:
            await finish_answer_run_failure(
                self.database,
                run_id,
                status="request_failed",
                error_code=error.code,
                error_message=error.message,
                raw_response=error.raw_response,
            )
            raise AppError(
                502,
                "ANSWER_STRUCTURE_REPAIR_FAILED",
                "模型试卷结构修复请求失败",
                {"runId": run_id, "originalRunId": original_run_id},
            ) from error

    async def extract(
        self,
        task_id: str,
        version_id: str,
    ) -> tuple[str, ParsedPaper, bool]:
        task = require_found(
            await self.database.fetch_one(
                """
                SELECT subject, answer_mode, template_file_id,
                       reference_answer_file_id
                FROM grading_tasks WHERE id = ?
                """,
                (task_id,),
            ),
            "批改任务不存在",
        )
        template = require_found(
            await self.database.fetch_one(
                """
                SELECT relative_path, mime_type FROM stored_files
                WHERE id = ?
                """,
                (task["template_file_id"],),
            ),
            "固定模板文件不存在",
        )
        reference = (
            await self.database.fetch_one(
                """
                SELECT relative_path, mime_type FROM stored_files
                WHERE id = ?
                """,
                (task["reference_answer_file_id"],),
            )
            if task["reference_answer_file_id"]
            else None
        )
        subject = Subject(str(task["subject"]))
        answer_mode = AnswerMode(str(task["answer_mode"]))
        template_pages = await prepare_model_pages(
            self.database.settings,
            str(template["relative_path"]),
            str(template["mime_type"]),
        )
        reference_pages = (
            await prepare_model_pages(
                self.database.settings,
                str(reference["relative_path"]),
                str(reference["mime_type"]),
            )
            if reference
            else []
        )
        snapshot = self.client.snapshot(
            prompt_version=ANSWER_EXTRACTION_PROMPT_VERSION,
            purpose="answer_extraction",
            details={
                "subject": subject.value,
                "answerMode": answer_mode.value,
                "templatePages": _page_snapshot(template_pages),
                "referencePages": _page_snapshot(reference_pages),
                "responseFormat": "json_object",
                "thinking": False,
            },
        )
        run_id = await start_answer_run(
            self.database,
            task_id=task_id,
            version_id=version_id,
            kind="reference_extraction" if reference else "exam_extraction",
            provider="阿里云百炼",
            model=self.client.model_id,
            request_snapshot=snapshot,
        )
        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": build_extraction_user_prompt(bool(reference))},
            {"type": "text", "text": "以下是固定模板试卷："},
            *[
                {"type": "image_url", "image_url": {"url": page.data_url}}
                for page in template_pages
            ],
        ]
        if reference:
            content_blocks.extend(
                [
                    {"type": "text", "text": "以下是教师上传的参考答案："},
                    *[
                        {"type": "image_url", "image_url": {"url": page.data_url}}
                        for page in reference_pages
                    ],
                ]
            )
        try:
            response = await self.client.chat(
                messages=[
                    {
                        "role": "system",
                        "content": build_extraction_system_prompt(subject, bool(reference)),
                    },
                    {"role": "user", "content": content_blocks},
                ]
            )
        except ModelRequestError as error:
            await finish_answer_run_failure(
                self.database,
                run_id,
                status="request_failed",
                error_code=error.code,
                error_message=error.message,
                raw_response=error.raw_response,
            )
            raise

        try:
            parsed = parse_extracted_paper(
                response.content,
                answer_mode=answer_mode,
                subject=subject,
            )
            if not parsed.questions:
                raise JsonCandidateError("识别结果没有可用题目")
        except JsonCandidateError as error:
            await finish_answer_run_failure(
                self.database,
                run_id,
                status="parse_failed",
                error_code="ANSWER_EXTRACTION_INVALID",
                error_message=str(error),
                raw_response=response.raw_response,
            )
            repair_run_id, repaired = await self._repair(
                task_id=task_id,
                version_id=version_id,
                subject=subject,
                answer_mode=answer_mode,
                original_run_id=run_id,
                content=response.content,
            )
            return repair_run_id, repaired, bool(reference)

        await finish_answer_run_success(
            self.database,
            run_id,
            raw_response=response.raw_response,
            parsed_output=parsed.model_dump(by_alias=True, mode="json"),
            usage=response.usage,
        )
        return run_id, parsed, bool(reference)
