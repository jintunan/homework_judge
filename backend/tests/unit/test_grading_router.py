import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

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
from homework_judge.grading.router import grade_question
from homework_judge.recognition.client import ModelResponse


def test_router_exports_single_controlled_entrypoint() -> None:
    assert callable(grade_question)


@pytest.mark.asyncio
async def test_router_short_circuits_incomplete_calculation_evidence() -> None:
    class RejectingModel:
        async def chat(self, **_kwargs: object) -> None:
            raise AssertionError("incomplete evidence must not call the model")

    grading_input = QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("2"),
        question_content="计算",
        standard_answer_snapshot={},
        student_response={"recognizedText": "x=1"},
        evidence_regions=[
            EvidenceRef(
                page_id="page",
                region_id="evidence",
                original_bbox=BoundingBox(x=1, y=1, width=10, height=10),
                recognized_text="x=1",
                evidence_kind="located_region",
            )
        ],
        rubric_version_id="rubric",
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "式子", "score": "2", "order": 0}
            ]
        },
        recognition_evidence_complete=False,
    )
    paired = [
        CalculationEvidenceImagePair(
            region_id="evidence",
            evidence_kind="located_region",
            template_image=b"template",
            student_image=b"student",
        )
    ]

    result = await grade_question(
        grading_input,
        RejectingModel(),  # type: ignore[arg-type]
        calculation_evidence_images=paired,
    )

    assert result.status is GradingStatus.NEEDS_REVIEW
    assert result.review_reasons == [ReviewReason.MISSING_EVIDENCE]
    assert [decision.status for decision in result.decisions] == [DecisionStatus.UNABLE]
    assert result.error_locations == []


@pytest.mark.asyncio
async def test_router_runs_complete_low_confidence_calculation_as_review_suggestion() -> None:
    class SatisfiedModel:
        settings = SimpleNamespace(dashscope_model="satisfied-stub")

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, **_kwargs: object) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": "P1",
                                "status": "satisfied",
                                "reason": "过程可见",
                                "evidenceRegionIds": ["evidence"],
                                "confidence": 0.99,
                            }
                        ],
                        "uncoveredMethod": False,
                    }
                ),
                raw={},
                usage={},
            )

    grading_input = QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("2"),
        question_content="计算",
        standard_answer_snapshot={},
        student_response={"recognizedText": "x=1"},
        evidence_regions=[
            EvidenceRef(
                page_id="page",
                region_id="evidence",
                original_bbox=BoundingBox(x=1, y=1, width=10, height=10),
                recognized_text="x=1",
                evidence_kind="located_region",
            )
        ],
        recognition_confidence=0.4,
        rubric_version_id="rubric",
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "式子", "score": "2", "order": 0}
            ]
        },
    )
    pair = CalculationEvidenceImagePair(
        region_id="evidence",
        evidence_kind="located_region",
        template_image=b"template",
        student_image=b"student",
    )
    model = SatisfiedModel()

    result = await grade_question(
        grading_input,
        model,  # type: ignore[arg-type]
        calculation_evidence_images=[pair],
    )

    assert model.calls == 1
    assert result.status is GradingStatus.NEEDS_REVIEW
    assert result.decisions[0].status is DecisionStatus.SATISFIED
    assert ReviewReason.LOW_RECOGNITION_CONFIDENCE in result.review_reasons
