from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..db.database import Database
from ..db.repositories.answer_config import (
    get_answer_draft,
    get_draft_task_context,
    mark_draft_failed,
    set_draft_resolution,
)
from ..db.repositories.answer_runs import (
    finish_answer_run_failure,
    finish_answer_run_success,
    save_search_sources,
    start_answer_run,
)
from ..errors import ModelRequestError
from ..model.answer_generator import AnswerInput, DashScopeAnswerGenerator
from ..model.dashscope_search import DashScopeNativeSearchClient
from ..schemas import QuestionType, Subject


class AnswerResolver:
    def __init__(
        self,
        database: Database,
        search_client: DashScopeNativeSearchClient,
        generator: DashScopeAnswerGenerator,
    ) -> None:
        self.database = database
        self.search_client = search_client
        self.generator = generator

    async def _context_and_input(
        self,
        draft_id: str,
    ) -> tuple[dict[str, Any], AnswerInput]:
        draft = await get_answer_draft(self.database, draft_id)
        context = await get_draft_task_context(self.database, draft_id)
        return (
            context,
            AnswerInput(
                subject=Subject(str(context["subject"])),
                question_number=str(draft["effectiveNumber"]),
                question_text=str(draft["questionText"]),
                question_type=QuestionType(str(draft["effectiveType"])),
                max_score=Decimal(str(draft["effectiveMaxScore"])),
            ),
        )

    async def resolve(self, draft_id: str) -> dict[str, Any]:
        context, answer_input = await self._context_and_input(draft_id)
        search_run_id = await start_answer_run(
            self.database,
            task_id=str(context["taskId"]),
            version_id=str(context["versionId"]),
            draft_question_id=draft_id,
            kind="web_search",
            provider="阿里云百炼",
            model=self.search_client.model_id,
            request_snapshot=self.search_client.build_request_snapshot(answer_input),
        )
        try:
            searched = await self.search_client.search(answer_input)
            await finish_answer_run_success(
                self.database,
                search_run_id,
                raw_response=searched.raw_response,
                parsed_output={
                    "found": searched.found,
                    "standardAnswer": searched.standard_answer,
                    "scoringPoints": [
                        point.model_dump(by_alias=True, mode="json")
                        for point in searched.scoring_points
                    ],
                    "reason": searched.reason,
                    "confidence": searched.confidence,
                },
                usage=searched.usage,
            )
            await save_search_sources(
                self.database,
                search_run_id,
                draft_id,
                searched.sources,
            )
            if searched.found:
                return await set_draft_resolution(
                    self.database,
                    draft_id,
                    standard_answer=searched.standard_answer,
                    scoring_points=searched.scoring_points,
                    reason=searched.reason,
                    source_type="web_searched",
                    confidence=searched.confidence,
                    needs_attention=(
                        searched.confidence
                        < self.database.settings.low_confidence_threshold
                    ),
                    latest_run_id=search_run_id,
                )
        except ModelRequestError as error:
            await finish_answer_run_failure(
                self.database,
                search_run_id,
                status="request_failed",
                error_code=error.code,
                error_message=error.message,
                raw_response=error.raw_response,
            )

        return await self._generate(draft_id, context, answer_input)

    async def regenerate(self, draft_id: str) -> dict[str, Any]:
        context, answer_input = await self._context_and_input(draft_id)
        return await self._generate(draft_id, context, answer_input)

    async def _generate(
        self,
        draft_id: str,
        context: dict[str, Any],
        answer_input: AnswerInput,
    ) -> dict[str, Any]:
        generation_run_id = await start_answer_run(
            self.database,
            task_id=str(context["taskId"]),
            version_id=str(context["versionId"]),
            draft_question_id=draft_id,
            kind="model_generation",
            provider="阿里云百炼",
            model=self.generator.model_id,
            request_snapshot=self.generator.build_request_snapshot(answer_input),
        )
        try:
            generated = await self.generator.generate(answer_input)
            parsed_output = {
                "standardAnswer": generated.standard_answer,
                "scoringPoints": [
                    point.model_dump(by_alias=True, mode="json")
                    for point in generated.scoring_points
                ],
                "reason": generated.reason,
                "confidence": generated.confidence,
                "normalizations": generated.normalizations,
            }
            await finish_answer_run_success(
                self.database,
                generation_run_id,
                raw_response=generated.raw_response,
                parsed_output=parsed_output,
                usage=generated.usage,
            )
            return await set_draft_resolution(
                self.database,
                draft_id,
                standard_answer=generated.standard_answer,
                scoring_points=generated.scoring_points,
                reason=generated.reason,
                source_type="model_generated",
                confidence=generated.confidence,
                needs_attention=True,
                latest_run_id=generation_run_id,
                normalizations=generated.normalizations,
            )
        except ModelRequestError as error:
            await finish_answer_run_failure(
                self.database,
                generation_run_id,
                status="request_failed",
                error_code=error.code,
                error_message=error.message,
                raw_response=error.raw_response,
            )
            return await mark_draft_failed(
                self.database,
                draft_id,
                f"检索未命中，模型生成失败：{error.message}",
                generation_run_id,
            )
