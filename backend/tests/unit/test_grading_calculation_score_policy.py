from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from homework_judge.api.grading import _question_value, _review_value
from homework_judge.db.database import json_dumps
from homework_judge.grading.calculation import (
    grade_calculation_question,
    partial_credit_score,
    validate_calculation_rubric_policy,
)
from homework_judge.grading.contracts import (
    BoundingBox,
    CalculationEvidenceImagePair,
    DecisionStatus,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionType,
    ReviewReason,
)
from homework_judge.grading.dependencies import RubricPoint
from homework_judge.grading.prompts import RUBRIC_SYSTEM_PROMPT
from homework_judge.recognition.client import ModelResponse

RUBRIC_POINTS = [
    {
        "key": "P1",
        "criterion": "列出正确公式",
        "score": "3.00",
        "order": 0,
        "dependencies": [],
    },
    {
        "key": "P2",
        "criterion": "正确代入已知量",
        "score": "2.00",
        "order": 1,
        "dependencies": ["P1"],
    },
    {
        "key": "P3",
        "criterion": "完成关键运算过程",
        "score": "3.00",
        "order": 2,
        "dependencies": ["P2"],
    },
    {
        "key": "FINAL_ANSWER",
        "criterion": "最终答案",
        "score": "2.00",
        "order": 3,
        "dependencies": [],
    },
]


class PointScenarioModel:
    settings = SimpleNamespace(dashscope_model="calculation-score-policy-stub")

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs: object) -> ModelResponse:
        self.calls.append(kwargs)
        return ModelResponse(
            content=json.dumps(
                {
                    "points": [
                        {
                            "pointKey": point["key"],
                            "status": self.statuses[str(point["key"])],
                            "reason": f"direct observation for {point['key']}",
                            "evidenceRegionIds": ["evidence-1"],
                            "confidence": 0.99,
                        }
                        for point in RUBRIC_POINTS
                    ],
                    "uncoveredMethod": False,
                }
            ),
            raw={},
            usage={"totalTokens": 10},
        )


def _grading_input() -> QuestionGradingInput:
    return QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("10.00"),
        question_content="求未知量并写出过程",
        standard_answer_snapshot={"answer": "42"},
        student_response={"recognizedText": "student work"},
        evidence_regions=[
            EvidenceRef(
                page_id="page-1",
                region_id="evidence-1",
                original_bbox=BoundingBox(x=10, y=20, width=100, height=80),
                recognized_text="student work",
                evidence_kind="located_region",
            )
        ],
        rubric_version_id="rubric-v1",
        grading_config={"rubricPoints": RUBRIC_POINTS},
    )


def _evidence_pairs() -> list[CalculationEvidenceImagePair]:
    return [
        CalculationEvidenceImagePair(
            region_id="evidence-1",
            evidence_kind="located_region",
            template_image=b"blank-template",
            student_image=b"student-work",
        )
    ]


async def _grade(statuses: dict[str, str]):
    model = PointScenarioModel(statuses)
    result = await grade_calculation_question(
        _grading_input(),
        model,  # type: ignore[arg-type]
        evidence_images=_evidence_pairs(),
    )
    assert len(model.calls) == 1
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "expected_score"),
    [
        (
            {
                "P1": "failed",
                "P2": "failed",
                "P3": "failed",
                "FINAL_ANSWER": "failed",
            },
            Decimal("0.00"),
        ),
        (
            {
                "P1": "partial",
                "P2": "partial",
                "P3": "partial",
                "FINAL_ANSWER": "partial",
            },
            Decimal("5.00"),
        ),
        (
            {
                "P1": "satisfied",
                "P2": "satisfied",
                "P3": "satisfied",
                "FINAL_ANSWER": "satisfied",
            },
            Decimal("10.00"),
        ),
    ],
    ids=["zero-percent", "fifty-percent", "one-hundred-percent"],
)
async def test_calculation_supports_zero_half_and_full_scores(
    statuses: dict[str, str],
    expected_score: Decimal,
) -> None:
    result = await _grade(statuses)

    assert result.status is GradingStatus.GRADED
    assert result.raw_score == expected_score
    assert result.final_score == expected_score
    if expected_score == Decimal("5.00"):
        assert {decision.status for decision in result.decisions} == {
            DecisionStatus.PARTIAL
        }
        assert all(
            decision.score == decision.max_score / 2 for decision in result.decisions
        )


@pytest.mark.asyncio
async def test_final_answer_is_independent_and_exactly_twenty_percent() -> None:
    result = await _grade(
        {
            "P1": "failed",
            "P2": "failed",
            "P3": "failed",
            "FINAL_ANSWER": "satisfied",
        }
    )
    decisions = {decision.key: decision for decision in result.decisions}

    assert RUBRIC_POINTS[-1]["dependencies"] == []
    assert decisions["FINAL_ANSWER"].status is DecisionStatus.SATISFIED
    assert decisions["FINAL_ANSWER"].score == Decimal("2.00")
    assert decisions["FINAL_ANSWER"].max_score / result.max_score == Decimal("0.20")
    assert result.final_score == Decimal("2.00")


@pytest.mark.asyncio
async def test_correct_final_answer_relaxes_dependencies_without_inventing_process_credit(
) -> None:
    result = await _grade(
        {
            "P1": "failed",
            "P2": "satisfied",
            "P3": "satisfied",
            "FINAL_ANSWER": "satisfied",
        }
    )
    decisions = {decision.key: decision for decision in result.decisions}

    # A correct final answer permits directly observed later work to survive a
    # broken dependency chain, but it cannot turn the unobserved P1 into credit.
    assert decisions["P1"].status is DecisionStatus.FAILED
    assert decisions["P1"].score == Decimal("0.00")
    assert decisions["P2"].status is DecisionStatus.SATISFIED
    assert decisions["P2"].score == Decimal("2.00")
    assert decisions["P3"].status is DecisionStatus.SATISFIED
    assert decisions["P3"].score == Decimal("3.00")
    assert decisions["FINAL_ANSWER"].score == Decimal("2.00")
    assert result.final_score == Decimal("7.00")


@pytest.mark.asyncio
async def test_correct_process_keeps_credit_when_final_answer_is_wrong() -> None:
    result = await _grade(
        {
            "P1": "satisfied",
            "P2": "satisfied",
            "P3": "satisfied",
            "FINAL_ANSWER": "failed",
        }
    )
    decisions = {decision.key: decision for decision in result.decisions}

    assert decisions["P1"].score == Decimal("3.00")
    assert decisions["P2"].score == Decimal("2.00")
    assert decisions["P3"].score == Decimal("3.00")
    assert decisions["FINAL_ANSWER"].status is DecisionStatus.FAILED
    assert decisions["FINAL_ANSWER"].score == Decimal("0.00")
    assert result.final_score == Decimal("8.00")


def test_partial_credit_rounds_half_up_to_score_precision() -> None:
    assert partial_credit_score("2.66") == Decimal("1.33")
    assert partial_credit_score("2.67") == Decimal("1.34")


def test_calculation_rubric_rejects_scores_with_more_than_two_decimal_places() -> None:
    points = [
        RubricPoint(
            key="P1",
            criterion="process",
            score=Decimal("8.001"),
            order=0,
            dependencies=[],
        ),
        RubricPoint(
            key="FINAL_ANSWER",
            criterion="最终答案",
            score=Decimal("1.999"),
            order=1,
            dependencies=[],
        ),
    ]

    with pytest.raises(ValueError, match="two decimal places"):
        validate_calculation_rubric_policy(points, Decimal("10.00"))


def test_rubric_prompt_requires_independent_twenty_percent_final_answer_point() -> None:
    normalized = RUBRIC_SYSTEM_PROMPT.replace("％", "%").replace(" ", "")
    final_answer_rule = next(
        (line for line in normalized.splitlines() if "最终答案" in line),
        "",
    )

    assert final_answer_rule
    assert "20%" in final_answer_rule
    assert "dependencies" in final_answer_rule
    assert "为空" in final_answer_rule or "=[]" in final_answer_rule


@pytest.mark.asyncio
async def test_missing_image_placeholder_remains_zero_in_storage_but_null_in_api() -> None:
    class RejectingModel:
        settings = SimpleNamespace(dashscope_model="must-not-run")

        async def chat(self, **_kwargs: object) -> ModelResponse:
            raise AssertionError("missing paired images must stop before the model")

    result = await grade_calculation_question(
        _grading_input(),
        RejectingModel(),  # type: ignore[arg-type]
        evidence_images=None,
    )

    assert result.status is GradingStatus.NEEDS_REVIEW
    assert result.raw_score == Decimal("0.00")
    assert result.final_score == Decimal("0.00")
    assert result.review_reasons == [ReviewReason.MISSING_EVIDENCE]
    assert {decision.status for decision in result.decisions} == {DecisionStatus.UNABLE}

    question_row: dict[str, Any] = {
        "id": "result-1",
        "grading_run_id": "run-1",
        "question_id": "question-1",
        "detected_number": "1",
        "question_type": "calculation",
        "status": "needs_review",
        "raw_score": "0.00",
        "final_score": "0.00",
        "max_score": "10.00",
        "review_reasons_json": json_dumps(["MISSING_EVIDENCE"]),
        "error_locations_json": "[]",
        "attempt_count": 0,
        "result_revision": 1,
        "error_code": None,
        "error_message": None,
    }
    question_payload = _question_value(question_row, detail=False)
    assert question_payload["rawScore"] is None
    assert question_payload["finalScore"] is None

    review_row = {
        **question_row,
        "id": "review-1",
        "grading_question_result_id": "result-1",
        "reason": "MISSING_EVIDENCE",
        "status": "open",
        "question_result_status": "needs_review",
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:00+00:00",
    }
    review_payload = _review_value(review_row, detail=False)
    assert review_payload["score"] is None
