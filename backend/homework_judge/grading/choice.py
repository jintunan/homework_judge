from __future__ import annotations

from decimal import Decimal

from .contracts import (
    DecisionRecord,
    DecisionStatus,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    ReviewReason,
    ToolObservation,
)
from .normalization import normalize_options, quantize_score


def _review_reasons(
    grading_input: QuestionGradingInput,
    issues: tuple[str, ...],
    confidence_threshold: float,
) -> list[ReviewReason]:
    reasons: list[ReviewReason] = []
    if issues or (
        grading_input.recognition_confidence is not None
        and grading_input.recognition_confidence < confidence_threshold
    ):
        reasons.append(ReviewReason.LOW_RECOGNITION_CONFIDENCE)
    return reasons


def grade_single_choice(
    grading_input: QuestionGradingInput,
    *,
    confidence_threshold: float = 0.85,
) -> QuestionGradingResult:
    selected = normalize_options(str(grading_input.student_response.get("answer", "")))
    correct = normalize_options(grading_input.standard_answer_snapshot.get("options", []))
    reasons = _review_reasons(grading_input, selected.issues + correct.issues, confidence_threshold)
    is_correct = len(selected.options) == 1 and selected.options == correct.options
    score = grading_input.max_score if is_correct else Decimal(0)
    decision = DecisionRecord(
        key="answer",
        status=DecisionStatus.CORRECT if is_correct else DecisionStatus.INCORRECT,
        score=quantize_score(score),
        max_score=quantize_score(grading_input.max_score),
        reason="选项完全一致" if is_correct else "选项与标准答案不一致",
        evidence_refs=grading_input.evidence_regions,
    )
    return QuestionGradingResult(
        status=GradingStatus.NEEDS_REVIEW if reasons else GradingStatus.GRADED,
        raw_score=score,
        final_score=quantize_score(score),
        max_score=quantize_score(grading_input.max_score),
        decisions=[decision],
        evidence_refs=grading_input.evidence_regions,
        error_locations=[] if is_correct else grading_input.evidence_regions[:1],
        tool_observations=[
            ToolObservation(
                tool="single_choice_rule",
                status="matched" if is_correct else "not_matched",
                payload={
                    "selectedOptions": list(selected.options),
                    "correctOptions": list(correct.options),
                    "issues": list(selected.issues + correct.issues),
                },
                tool_version="1",
            )
        ],
        review_reasons=reasons,
    )


def grade_multiple_choice(
    grading_input: QuestionGradingInput,
    *,
    confidence_threshold: float = 0.85,
) -> QuestionGradingResult:
    selected = normalize_options(str(grading_input.student_response.get("answer", "")))
    correct = normalize_options(grading_input.standard_answer_snapshot.get("options", []))
    reasons = _review_reasons(grading_input, selected.issues + correct.issues, confidence_threshold)
    selected_set = set(selected.options)
    correct_set = set(correct.options)
    wrong_options = selected_set - correct_set
    correct_selected = selected_set & correct_set

    if not correct_set:
        reasons.append(ReviewReason.SCORE_INCONSISTENCY)
        raw_score = Decimal(0)
        reason = "标准答案没有有效选项"
    elif wrong_options or not selected_set:
        raw_score = Decimal(0)
        reason = "包含错误选项" if wrong_options else "未选择任何选项"
    elif selected_set == correct_set:
        raw_score = grading_input.max_score
        reason = "选项集合完全一致"
    else:
        raw_score = (
            grading_input.max_score * Decimal(len(correct_selected)) / Decimal(len(correct_set))
        )
        reason = "少选且未错选，按选对数量比例得分"

    final_score = quantize_score(raw_score)
    if final_score == grading_input.max_score:
        decision_status = DecisionStatus.CORRECT
    elif final_score > 0:
        decision_status = DecisionStatus.PARTIAL
    else:
        decision_status = DecisionStatus.INCORRECT
    decision = DecisionRecord(
        key="answer",
        status=decision_status,
        score=final_score,
        max_score=quantize_score(grading_input.max_score),
        reason=reason,
        evidence_refs=grading_input.evidence_regions,
    )
    return QuestionGradingResult(
        status=GradingStatus.NEEDS_REVIEW if reasons else GradingStatus.GRADED,
        raw_score=raw_score,
        final_score=final_score,
        max_score=quantize_score(grading_input.max_score),
        decisions=[decision],
        evidence_refs=grading_input.evidence_regions,
        error_locations=(
            [] if final_score == grading_input.max_score else grading_input.evidence_regions[:1]
        ),
        tool_observations=[
            ToolObservation(
                tool="multiple_choice_rule",
                status="scored",
                payload={
                    "selectedOptions": list(selected.options),
                    "correctOptions": list(correct.options),
                    "correctSelectedCount": len(correct_selected),
                    "correctOptionCount": len(correct_set),
                    "wrongOptions": sorted(wrong_options),
                    "rawRatio": (
                        f"{len(correct_selected)}/{len(correct_set)}"
                        if correct_set and not wrong_options
                        else "0"
                    ),
                    "unroundedScore": str(raw_score),
                    "finalScore": str(final_score),
                },
                tool_version="1",
            )
        ],
        review_reasons=list(dict.fromkeys(reasons)),
    )
