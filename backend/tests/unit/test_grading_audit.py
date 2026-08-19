from decimal import Decimal

from homework_judge.grading.audit import (
    TEACHER_REVIEW_TOOL,
    TEACHER_REVIEW_VERSION,
    audit_exam,
    audit_question,
    has_teacher_review,
    latest_teacher_review_detail,
)
from homework_judge.grading.contracts import (
    DecisionRecord,
    DecisionStatus,
    GradingStatus,
    QuestionGradingResult,
    ReviewReason,
)


def result(*, score: str = "1", decision_score: str = "1") -> QuestionGradingResult:
    return QuestionGradingResult(
        status=GradingStatus.GRADED,
        raw_score=Decimal(score),
        final_score=Decimal(score),
        max_score=Decimal("2"),
        decisions=[
            DecisionRecord(
                key="P1",
                status=DecisionStatus.SATISFIED,
                score=Decimal(decision_score),
                max_score=Decimal("2"),
            )
        ],
    )


def test_question_audit_reports_score_and_evidence_problems() -> None:
    issues = audit_question(result(score="2", decision_score="1"))
    reasons = {issue.reason for issue in issues}
    assert ReviewReason.SCORE_INCONSISTENCY in reasons
    assert ReviewReason.MISSING_EVIDENCE in reasons


def test_exam_audit_requires_complete_results_and_no_open_review() -> None:
    issues = audit_exam(
        [result()],
        expected_question_count=2,
        expected_max_score=Decimal("2"),
        open_review_count=1,
    )
    assert len([item for item in issues if item.reason is ReviewReason.SCORE_INCONSISTENCY]) >= 2


def test_teacher_review_helpers_only_accept_versioned_teacher_records() -> None:
    values = [
        {
            "tool": "model",
            "status": "confirmed",
            "detail": "不是教师记录",
            "payload": {"reviewItemId": "wrong-tool"},
            "tool_version": TEACHER_REVIEW_VERSION,
        },
        {
            "tool": TEACHER_REVIEW_TOOL,
            "status": "confirmed",
            "detail": "旧版本",
            "payload": {"reviewItemId": "old"},
            "tool_version": "v0",
        },
        {
            "tool": TEACHER_REVIEW_TOOL,
            "status": "confirmed",
            "detail": "教师已核对原卷",
            "payload": {"reviewItemId": "review"},
            "tool_version": TEACHER_REVIEW_VERSION,
        },
    ]

    assert has_teacher_review(values)
    assert latest_teacher_review_detail(values) == "教师已核对原卷"
    assert not has_teacher_review(values[:2])
