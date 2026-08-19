from decimal import Decimal

from homework_judge.grading.choice import grade_multiple_choice, grade_single_choice
from homework_judge.grading.contracts import (
    BoundingBox,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionType,
    ReviewReason,
)


def grading_input(
    question_type: QuestionType,
    answer: str,
    correct: list[str],
    *,
    max_score: str = "6",
    confidence: float = 0.99,
) -> QuestionGradingInput:
    return QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=question_type,
        max_score=Decimal(max_score),
        question_content="题目",
        standard_answer_snapshot={"options": correct},
        student_response={"answer": answer},
        evidence_regions=[
            EvidenceRef(
                page_id="page",
                region_id="region",
                original_bbox=BoundingBox(x=10, y=20, width=30, height=40),
            )
        ],
        recognition_confidence=confidence,
    )


def test_single_choice_requires_exactly_one_matching_option() -> None:
    correct = grade_single_choice(grading_input(QuestionType.SINGLE_CHOICE, "A", ["A"]))
    assert correct.final_score == Decimal("6.00")
    for answer in ("B", "AB", ""):
        incorrect = grade_single_choice(grading_input(QuestionType.SINGLE_CHOICE, answer, ["A"]))
        assert incorrect.final_score == Decimal("0.00")


def test_multiple_choice_scoring_examples() -> None:
    expected = {"ACD": "6.00", "AC": "4.00", "A": "2.00", "AB": "0.00", "": "0.00"}
    for answer, score in expected.items():
        result = grade_multiple_choice(
            grading_input(QuestionType.MULTIPLE_CHOICE, answer, ["A", "C", "D"])
        )
        assert result.final_score == Decimal(score)


def test_multiple_choice_saves_raw_ratio_and_unrounded_score() -> None:
    result = grade_multiple_choice(
        grading_input(QuestionType.MULTIPLE_CHOICE, "A", ["A", "B", "C"], max_score="2")
    )
    assert result.raw_score == Decimal(2) / Decimal(3)
    assert result.final_score == Decimal("0.67")
    assert result.tool_observations[0].payload["rawRatio"] == "1/3"


def test_low_confidence_choice_requires_review() -> None:
    result = grade_single_choice(
        grading_input(QuestionType.SINGLE_CHOICE, "A", ["A"], confidence=0.2)
    )
    assert result.status is GradingStatus.NEEDS_REVIEW
    assert result.review_reasons == [ReviewReason.LOW_RECOGNITION_CONFIDENCE]
