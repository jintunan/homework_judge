from __future__ import annotations

import base64
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from homework_judge.grading.calculation import grade_calculation_question
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
from homework_judge.recognition.client import ModelResponse


class CalculationModelStub:
    settings = SimpleNamespace(dashscope_model="calculation-stub")

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs: object) -> ModelResponse:
        self.calls.append(kwargs)
        return ModelResponse(
            content=json.dumps(
                {
                    "points": [
                        {
                            "pointKey": "P1",
                            "status": "failed",
                            "reason": "首个公式错误",
                            "evidenceRegionIds": ["r1"],
                            "confidence": 0.99,
                        },
                        {
                            "pointKey": "P2",
                            "status": "satisfied",
                            "reason": "形式正确",
                            "evidenceRegionIds": ["r2"],
                            "confidence": 0.99,
                        },
                        {
                            "pointKey": "P3",
                            "status": "satisfied",
                            "reason": "形式正确",
                            "evidenceRegionIds": ["r2"],
                            "confidence": 0.99,
                        },
                        {
                            "pointKey": "P4",
                            "status": "satisfied",
                            "reason": "独立结论正确",
                            "evidenceRegionIds": ["r2"],
                            "confidence": 0.99,
                        },
                    ],
                    "uncoveredMethod": False,
                }
            ),
            raw={},
            usage={"totalTokens": 10},
        )


def calculation_input() -> QuestionGradingInput:
    evidence = [
        EvidenceRef(
            page_id="page",
            region_id=region_id,
            original_bbox=BoundingBox(x=index * 20, y=10, width=15, height=10),
            recognized_text=region_id,
            evidence_kind="located_region",
        )
        for index, region_id in enumerate(("r1", "r2"), start=1)
    ]
    return QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("4"),
        question_content="计算",
        standard_answer_snapshot={"answer": "42", "explanation": "先列式再计算"},
        student_response={"recognizedText": "steps"},
        evidence_regions=evidence,
        rubric_version_id="rubric-v1",
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "one", "score": "1", "order": 0},
                {
                    "key": "P2",
                    "criterion": "two",
                    "score": "1",
                    "order": 1,
                    "dependencies": ["P1"],
                },
                {
                    "key": "P3",
                    "criterion": "three",
                    "score": "1",
                    "order": 2,
                    "dependencies": ["P2"],
                },
                {"key": "P4", "criterion": "four", "score": "1", "order": 3},
            ]
        },
    )


def calculation_pairs() -> list[CalculationEvidenceImagePair]:
    return [
        CalculationEvidenceImagePair(
            region_id=region_id,
            evidence_kind="located_region",
            template_image=f"template-{region_id}".encode(),
            student_image=f"student-{region_id}".encode(),
        )
        for region_id in ("r1", "r2")
    ]


@pytest.mark.asyncio
async def test_calculation_soft_dependency_keeps_directly_supported_scores() -> None:
    model = CalculationModelStub()
    result = await grade_calculation_question(
        calculation_input(),
        model,  # type: ignore[arg-type]
        evidence_images=calculation_pairs(),
    )
    decisions = {item.key: item for item in result.decisions}
    assert decisions["P1"].score == 0
    assert decisions["P2"].status is DecisionStatus.SATISFIED
    assert decisions["P2"].score == Decimal("1.00")
    assert decisions["P3"].status is DecisionStatus.SATISFIED
    assert decisions["P3"].score == Decimal("1.00")
    assert decisions["P4"].score == Decimal("1.00")
    assert result.final_score == Decimal("3.00")
    assert result.error_locations[0].region_id == "r1"
    content = model.calls[0]["user_content"]
    assert isinstance(content, list)
    payload = json.loads(content[0]["text"])
    assert payload["standardAnswer"] == "42"
    assert payload["standardExplanation"] == "先列式再计算"
    assert payload["scoringPolicyVersion"] == "evidence-aware-alternative-methods-v3"
    assert payload["scoringPolicy"] == {
        "omittedSteps": {
            "downstreamCorrectUseIsEvidence": True,
            "nonCriticalWithClearEvidence": "satisfied",
            "criticalWithCompleteEvidence": "satisfied",
            "criticalWithPartialEvidence": "partial",
            "noEvidenceOnCompleteImage": "failed",
            "uncertainDueToImageOrHandwriting": "unable",
            "explicitError": "failed",
        },
        "alternativeMethods": {
            "standardSolutionIsExclusive": False,
            "mapEquivalentRolesToRubricPoints": True,
            "differentMethodAloneRequiresReview": False,
            "unmappablePossiblyCorrectMethodRequiresReview": True,
        },
        "deductions": {
            "deductSameIssueOnce": True,
            "preserveLaterIndependentEvidence": True,
        },
    }
    assert payload["availableEvidence"] == [
        {
            "regionId": "r1",
            "recognizedText": "r1",
            "evidenceKind": "located_region",
            "isBlank": False,
        },
        {
            "regionId": "r2",
            "recognizedText": "r2",
            "evidenceKind": "located_region",
            "isBlank": False,
        },
    ]
    assert [item["type"] for item in content] == [
        "text",
        "text",
        "image_url",
        "text",
        "image_url",
        "text",
        "image_url",
        "text",
        "image_url",
    ]
    assert json.loads(content[1]["text"])["evidenceId"] == "r1"
    assert json.loads(content[5]["text"])["evidenceId"] == "r2"
    system_prompt = model.calls[0]["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "后续书写就是该关系的直接可见证据" in system_prompt
    assert "将其他解法中作用等价的正确内容" in system_prompt


@pytest.mark.asyncio
async def test_calculation_truncates_verbose_model_reason_without_losing_score() -> None:
    verbose_reason = "判定依据" * 100

    class VerboseReasonModel:
        settings = SimpleNamespace(dashscope_model="verbose-reason-stub")

        async def chat(self, **_kwargs: object) -> ModelResponse:
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": point_key,
                                "status": "satisfied",
                                "reason": verbose_reason,
                                "evidenceRegionIds": ["r1"],
                                "confidence": 0.99,
                            }
                            for point_key in ("P1", "P2", "P3", "P4")
                        ],
                        "uncoveredMethod": False,
                    },
                    ensure_ascii=False,
                ),
                raw={},
                usage={"totalTokens": 500},
            )

    result = await grade_calculation_question(
        calculation_input(),
        VerboseReasonModel(),  # type: ignore[arg-type]
        evidence_images=calculation_pairs(),
    )

    assert result.status is GradingStatus.GRADED
    assert result.final_score == Decimal("4.00")
    assert {len(item.reason) for item in result.decisions} == {300}
    assert all(item.reason.endswith("…") for item in result.decisions)
    direct_points = result.tool_observations[0].payload["directPoints"]
    assert all(len(item["reason"]) == 300 for item in direct_points)


@pytest.mark.asyncio
async def test_calculation_rejects_evidence_id_outside_current_response() -> None:
    class ForeignEvidenceModel:
        settings = SimpleNamespace(dashscope_model="foreign-evidence-stub")

        async def chat(self, **_kwargs: object) -> ModelResponse:
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": point_key,
                                "status": "satisfied",
                                "reason": "伪造引用",
                                "evidenceRegionIds": [
                                    "foreign-response-region" if point_key == "P1" else "r2"
                                ],
                                "confidence": 0.99,
                            }
                            for point_key in ("P1", "P2", "P3", "P4")
                        ],
                        "uncoveredMethod": False,
                    }
                ),
                raw={},
                usage={},
            )

    with pytest.raises(
        ValueError,
        match="model referenced evidence outside the current response",
    ):
        await grade_calculation_question(
            calculation_input(),
            ForeignEvidenceModel(),  # type: ignore[arg-type]
            evidence_images=calculation_pairs(),
        )


@pytest.mark.asyncio
async def test_calculation_binds_positive_and_blank_pairs_in_evidence_order() -> None:
    class MixedEvidenceModel:
        settings = SimpleNamespace(dashscope_model="mixed-evidence-stub")

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def chat(self, **kwargs: object) -> ModelResponse:
            self.calls.append(kwargs)
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": "P1",
                                "status": "satisfied",
                                "reason": "正证据包含作答",
                                "evidenceRegionIds": ["positive-region"],
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
        run_id="mixed-run",
        question_id="mixed-question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("1"),
        question_content="计算",
        standard_answer_snapshot={},
        student_response={"recognizedText": "x=1"},
        evidence_regions=[
            EvidenceRef(
                page_id="positive-page",
                region_id="positive-region",
                original_bbox=BoundingBox(x=1, y=1, width=10, height=10),
                recognized_text="x=1",
                evidence_kind="located_region",
            ),
            EvidenceRef(
                page_id="blank-page",
                region_id="blank-window",
                original_bbox=BoundingBox(x=2, y=20, width=10, height=10),
                recognized_text="",
                evidence_kind="blank_search_window",
            ),
        ],
        rubric_version_id="mixed-rubric",
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "过程", "score": "1", "order": 0}
            ]
        },
    )
    positive_pair = CalculationEvidenceImagePair(
        region_id="positive-region",
        evidence_kind="located_region",
        template_image=b"positive-template-bytes",
        student_image=b"positive-student-bytes",
    )
    blank_pair = CalculationEvidenceImagePair(
        region_id="blank-window",
        evidence_kind="blank_search_window",
        template_image=b"blank-template-bytes",
        student_image=b"blank-student-bytes",
    )
    model = MixedEvidenceModel()

    result = await grade_calculation_question(
        grading_input,
        model,  # type: ignore[arg-type]
        # Deliberately reverse the runtime list. The prompt must follow persisted
        # evidence order, never caller/map iteration order.
        evidence_images=[blank_pair, positive_pair],
    )

    assert result.status is GradingStatus.GRADED
    assert len(model.calls) == 1
    content = model.calls[0]["user_content"]
    assert isinstance(content, list)
    assert json.loads(content[0]["text"])["availableEvidence"] == [
        {
            "regionId": "positive-region",
            "recognizedText": "x=1",
            "evidenceKind": "located_region",
            "isBlank": False,
        },
        {
            "regionId": "blank-window",
            "recognizedText": "",
            "evidenceKind": "blank_search_window",
            "isBlank": True,
        },
    ]
    assert [json.loads(content[index]["text"])["evidenceId"] for index in (1, 5)] == [
        "positive-region",
        "blank-window",
    ]
    assert [
        base64.b64decode(content[index]["image_url"]["url"].rsplit(",", 1)[1])
        for index in (2, 4, 6, 8)
    ] == [
        b"positive-template-bytes",
        b"positive-student-bytes",
        b"blank-template-bytes",
        b"blank-student-bytes",
    ]
    assert json.loads(content[1]["text"]) == {
        "evidenceId": "positive-region",
        "evidenceKind": "located_region",
        "recognizedText": "x=1",
        "isBlank": False,
        "nextImage": "blank_template_crop",
    }
    assert json.loads(content[5]["text"]) == {
        "evidenceId": "blank-window",
        "evidenceKind": "blank_search_window",
        "recognizedText": "",
        "isBlank": True,
        "nextImage": "blank_template_crop",
    }
    assert positive_pair.model_dump(mode="json") == {
        "region_id": "positive-region",
        "evidence_kind": "located_region",
    }
    serialized_input = grading_input.model_dump_json()
    serialized_result = result.model_dump_json()
    for secret in (
        "positive-template-bytes",
        "positive-student-bytes",
        "blank-template-bytes",
        "blank-student-bytes",
    ):
        assert secret not in serialized_input
        assert secret not in serialized_result


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_kind", ["located_region", "blank_search_window"])
async def test_calculation_missing_pair_never_calls_model_and_marks_every_point_unable(
    missing_kind: str,
) -> None:
    class RejectingModel:
        async def chat(self, **_kwargs: object) -> None:
            raise AssertionError("incomplete evidence must not call the model")

    grading_input = calculation_input()
    grading_input = grading_input.model_copy(
        update={
            "evidence_regions": [
                grading_input.evidence_regions[0],
                grading_input.evidence_regions[1].model_copy(
                    update={
                        "evidence_kind": missing_kind,
                        "recognized_text": (
                            "" if missing_kind == "blank_search_window" else "r2"
                        ),
                    }
                ),
            ]
        }
    )
    result = await grade_calculation_question(
        grading_input,
        RejectingModel(),  # type: ignore[arg-type]
        evidence_images=calculation_pairs()[:1],
    )

    assert result.status is GradingStatus.NEEDS_REVIEW
    assert result.review_reasons == [ReviewReason.MISSING_EVIDENCE]
    assert result.final_score == 0
    assert result.error_locations == []
    assert len(result.decisions) == 4
    assert {decision.status for decision in result.decisions} == {DecisionStatus.UNABLE}
    assert all(decision.evidence_refs == [] for decision in result.decisions)


@pytest.mark.asyncio
async def test_reliable_blank_pair_is_sent_to_the_normal_calculation_model() -> None:
    class BlankModel:
        settings = SimpleNamespace(dashscope_model="blank-stub")

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def chat(self, **kwargs: object) -> ModelResponse:
            self.calls.append(kwargs)
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": "P1",
                                "status": "failed",
                                "reason": "检查窗口内没有作答",
                                "evidenceRegionIds": ["blank-window"],
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
        question_id="blank-question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("1"),
        question_content="计算",
        standard_answer_snapshot={},
        student_response={"recognizedText": "", "isBlank": True},
        evidence_regions=[
            EvidenceRef(
                page_id="page",
                region_id="blank-window",
                original_bbox=BoundingBox(x=1, y=1, width=10, height=10),
                recognized_text="",
                evidence_kind="blank_search_window",
            )
        ],
        rubric_version_id="rubric",
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "有过程", "score": "1", "order": 0}
            ]
        },
    )
    pair = CalculationEvidenceImagePair(
        region_id="blank-window",
        evidence_kind="blank_search_window",
        template_image=b"template",
        student_image=b"student",
    )
    model = BlankModel()

    result = await grade_calculation_question(
        grading_input,
        model,  # type: ignore[arg-type]
        evidence_images=[pair],
    )

    assert len(model.calls) == 1
    content = model.calls[0]["user_content"]
    assert isinstance(content, list)
    payload = json.loads(content[0]["text"])
    assert payload["availableEvidence"][0]["isBlank"] is True
    assert result.decisions[0].status is DecisionStatus.FAILED


@pytest.mark.asyncio
async def test_calculation_without_any_evidence_never_calls_model() -> None:
    class RejectingModel:
        async def chat(self, **_kwargs: object) -> None:
            raise AssertionError("empty evidence must not call the model")

    grading_input = calculation_input().model_copy(update={"evidence_regions": []})

    result = await grade_calculation_question(
        grading_input,
        RejectingModel(),  # type: ignore[arg-type]
        evidence_images=[],
    )

    assert result.status is GradingStatus.NEEDS_REVIEW
    assert {decision.status for decision in result.decisions} == {DecisionStatus.UNABLE}
    assert result.error_locations == []
