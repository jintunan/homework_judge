from __future__ import annotations

from ..recognition.client import DashScopeClient
from .calculation import grade_calculation_question
from .choice import grade_multiple_choice, grade_single_choice
from .contracts import (
    CalculationEvidenceImagePair,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    QuestionType,
    ReviewReason,
)
from .fill import grade_fill_question


async def grade_question(
    grading_input: QuestionGradingInput,
    client: DashScopeClient,
    *,
    recognition_threshold: float = 0.85,
    model_confidence_threshold: float = 0.95,
    formula_timeout_ms: int = 1500,
    calculation_evidence_images: list[CalculationEvidenceImagePair] | None = None,
) -> QuestionGradingResult:
    result: QuestionGradingResult
    if grading_input.question_type is QuestionType.SINGLE_CHOICE:
        result = grade_single_choice(grading_input, confidence_threshold=recognition_threshold)
    elif grading_input.question_type is QuestionType.MULTIPLE_CHOICE:
        result = grade_multiple_choice(grading_input, confidence_threshold=recognition_threshold)
    elif grading_input.question_type is QuestionType.FILL_BLANK:
        result = await grade_fill_question(
            grading_input,
            client,
            confidence_threshold=model_confidence_threshold,
            formula_timeout_ms=formula_timeout_ms,
        )
    elif grading_input.question_type is QuestionType.CALCULATION:
        result = await grade_calculation_question(
            grading_input,
            client,
            confidence_threshold=model_confidence_threshold,
            evidence_images=calculation_evidence_images,
        )
    else:
        raise ValueError(f"unsupported question type: {grading_input.question_type}")
    if (
        grading_input.recognition_confidence is not None
        and grading_input.recognition_confidence < recognition_threshold
        and ReviewReason.LOW_RECOGNITION_CONFIDENCE not in result.review_reasons
    ):
        result = result.model_copy(
            update={
                "status": GradingStatus.NEEDS_REVIEW,
                "review_reasons": [
                    *result.review_reasons,
                    ReviewReason.LOW_RECOGNITION_CONFIDENCE,
                ],
            }
        )
    return result
