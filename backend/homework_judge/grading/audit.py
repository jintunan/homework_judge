from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .contracts import (
    DecisionStatus,
    QuestionGradingResult,
    ReviewReason,
    ToolObservation,
)
from .normalization import quantize_score

TEACHER_REVIEW_TOOL = "teacher_review"
TEACHER_REVIEW_VERSION = "teacher-authority-v1"
TEACHER_REVIEW_STATUSES = frozenset({"confirmed", "overridden"})


@dataclass(frozen=True, slots=True)
class AuditIssue:
    reason: ReviewReason
    detail: str


def audit_question(result: QuestionGradingResult) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    if not result.evidence_refs:
        issues.append(AuditIssue(ReviewReason.MISSING_EVIDENCE, "题目批改结果缺少可追溯证据"))
    if result.final_score < 0 or result.final_score > result.max_score:
        issues.append(AuditIssue(ReviewReason.SCORE_INCONSISTENCY, "题目得分越界"))
    if result.decisions:
        decision_sum = quantize_score(
            sum((decision.score for decision in result.decisions), Decimal(0))
        )
        if decision_sum != quantize_score(result.final_score):
            issues.append(
                AuditIssue(ReviewReason.SCORE_INCONSISTENCY, "分项得分之和与题目得分不一致")
            )
    for decision in result.decisions:
        if not decision.evidence_refs:
            issues.append(
                AuditIssue(ReviewReason.MISSING_EVIDENCE, f"{decision.key} 得分但缺少证据")
            )
        if decision.status is DecisionStatus.BLOCKED_BY_DEPENDENCY and not decision.blocked_by:
            issues.append(
                AuditIssue(
                    ReviewReason.DEPENDENCY_CONTRADICTION,
                    f"{decision.key} 被依赖阻断但未记录来源",
                )
            )
    if result.final_score < result.max_score and not result.error_locations:
        issues.append(
            AuditIssue(
                ReviewReason.UNCERTAIN_ERROR_LOCATION,
                "非满分题缺少可靠的首个失分位置",
            )
        )
    for reason in result.review_reasons:
        if not any(issue.reason is reason for issue in issues):
            issues.append(AuditIssue(reason, "批改器要求教师复核"))
    return issues


def teacher_review_observations(
    values: list[ToolObservation | dict[str, Any]],
) -> list[ToolObservation]:
    records: list[ToolObservation] = []
    for value in values:
        try:
            observation = (
                value
                if isinstance(value, ToolObservation)
                else ToolObservation.model_validate(value)
            )
        except (TypeError, ValueError):
            continue
        if (
            observation.tool == TEACHER_REVIEW_TOOL
            and observation.tool_version == TEACHER_REVIEW_VERSION
            and observation.status in TEACHER_REVIEW_STATUSES
            and str(observation.payload.get("reviewItemId", "")).strip()
        ):
            records.append(observation)
    return records


def has_teacher_review(values: list[ToolObservation | dict[str, Any]]) -> bool:
    return bool(teacher_review_observations(values))


def latest_teacher_review_detail(
    values: list[ToolObservation | dict[str, Any]],
) -> str | None:
    records = teacher_review_observations(values)
    if not records:
        return None
    detail = records[-1].detail.strip()
    return detail or None


def audit_exam(
    results: list[QuestionGradingResult],
    *,
    expected_question_count: int,
    expected_max_score: Decimal,
    open_review_count: int,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    if len(results) != expected_question_count:
        issues.append(AuditIssue(ReviewReason.SCORE_INCONSISTENCY, "题目结果数量不完整"))
    max_score = quantize_score(sum((result.max_score for result in results), Decimal(0)))
    if max_score != quantize_score(expected_max_score):
        issues.append(AuditIssue(ReviewReason.SCORE_INCONSISTENCY, "整卷满分不一致"))
    if open_review_count:
        issues.append(AuditIssue(ReviewReason.SCORE_INCONSISTENCY, "仍有未处理复核项"))
    for result in results:
        issues.extend(audit_question(result))
    return issues
