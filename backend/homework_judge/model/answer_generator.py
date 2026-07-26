from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..answer_config.normalizer import normalize_paper
from ..answer_config.parser import JsonCandidateError, extract_json_object
from ..answer_config.prompts import (
    ANSWER_GENERATION_PROMPT_VERSION,
    build_generation_prompts,
)
from ..errors import ModelRequestError
from ..schemas import AnswerMode, QuestionType, ScoringPoint, Subject
from .dashscope import DashScopeClient


@dataclass(frozen=True, slots=True)
class AnswerInput:
    subject: Subject
    question_number: str
    question_text: str
    question_type: QuestionType
    max_score: Decimal


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    standard_answer: str
    scoring_points: list[ScoringPoint]
    reason: str
    confidence: float
    raw_response: Any
    usage: dict[str, int] | None
    normalizations: list[dict[str, Any]]


class DashScopeAnswerGenerator:
    def __init__(self, client: DashScopeClient) -> None:
        self.client = client
        self.model_id = client.model_id

    def build_request_snapshot(self, answer_input: AnswerInput) -> dict[str, Any]:
        return self.client.snapshot(
            prompt_version=ANSWER_GENERATION_PROMPT_VERSION,
            purpose="answer_generation",
            details={
                "subject": answer_input.subject.value,
                "questionNumber": answer_input.question_number,
                "questionText": answer_input.question_text,
                "questionType": answer_input.question_type.value,
                "maxScore": float(answer_input.max_score),
                "responseFormat": "json_object",
                "thinking": False,
            },
        )

    async def generate(self, answer_input: AnswerInput) -> GeneratedAnswer:
        system, user = build_generation_prompts(
            subject=answer_input.subject,
            question_number=answer_input.question_number,
            question_text=answer_input.question_text,
            question_type=answer_input.question_type,
            max_score=float(answer_input.max_score),
        )
        response = await self.client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        try:
            output = extract_json_object(response.content)
        except JsonCandidateError as error:
            raise ModelRequestError(
                "ANSWER_GENERATION_INVALID",
                "模型生成的答案结构无法解析",
                response.raw_response,
            ) from error

        paper = normalize_paper(
            [
                {
                    **output,
                    "questionNumber": answer_input.question_number,
                    "questionText": answer_input.question_text,
                    "type": answer_input.question_type.value,
                    "maxScore": str(answer_input.max_score),
                }
            ],
            answer_mode=AnswerMode.REFERENCE_UPLOAD,
            subject=answer_input.subject,
        )
        if not paper.questions or not paper.questions[0].standard_answer:
            raise ModelRequestError(
                "ANSWER_GENERATION_INVALID",
                "模型没有生成可审核的标准答案",
                response.raw_response,
            )
        question = paper.questions[0]
        return GeneratedAnswer(
            standard_answer=question.standard_answer,
            scoring_points=question.scoring_points,
            reason=question.reason,
            confidence=question.confidence,
            raw_response=response.raw_response,
            usage=response.usage,
            normalizations=[
                issue.model_dump(by_alias=True)
                for issue in question.issues
                if issue.code in {"scoring_points_scaled", "scoring_point_invalid"}
            ],
        )
