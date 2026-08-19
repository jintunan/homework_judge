from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from homework_judge.grading.contracts import (
    BoundingBox,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionType,
    ReviewReason,
)
from homework_judge.grading.fill import (
    FillBlankInput,
    grade_fill_question,
    resolve_fill_blank_evidence,
)
from homework_judge.recognition.client import ModelResponse


class ModelStub:
    settings = SimpleNamespace(dashscope_model="model-stub")

    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    async def chat(self, **kwargs) -> ModelResponse:
        output = self.outputs[self.calls]
        self.calls += 1
        self.requests.append(kwargs)
        return ModelResponse(content=json.dumps(output), raw={}, usage={"totalTokens": 1})


def fill_input(
    blanks: list[dict[str, object]],
    *,
    evidence_text: str = "student answer",
) -> QuestionGradingInput:
    return QuestionGradingInput(
        run_id="run",
        question_id="question",
        question_type=QuestionType.FILL_BLANK,
        max_score=sum((Decimal(str(blank["maxScore"])) for blank in blanks), Decimal(0)),
        question_content="填写物理量",
        standard_answer_snapshot={},
        student_response={},
        evidence_regions=[
            EvidenceRef(
                page_id="page",
                region_id="region-1",
                original_bbox=BoundingBox(x=1, y=2, width=30, height=10),
                recognized_text=evidence_text,
            )
        ],
        grading_config={"blanks": blanks},
    )


@pytest.mark.asyncio
async def test_exact_or_synonym_match_still_calls_model_for_same_blank_key() -> None:
    model = ModelStub(
        [
            {
                "blankKey": "B1",
                "decision": "correct",
                "reason": "与教师同义答案一致",
                "evidenceRegionIds": ["region-1"],
                "confidence": 0.99,
            }
        ]
    )
    result = await grade_fill_question(
        fill_input(
            [
                {
                    "blankKey": "B1",
                    "maxScore": "2",
                    "answerKind": "text",
                    "standardAnswers": ["电场"],
                    "synonyms": ["E场"],
                    "studentAnswer": " E 场。",
                    "evidenceRegionIds": ["region-1"],
                }
            ]
        ),
        model,  # type: ignore[arg-type]
    )
    assert model.calls == 1
    assert result.final_score == Decimal("2.00")


@pytest.mark.asyncio
async def test_every_non_exact_fill_answer_calls_model() -> None:
    model = ModelStub(
        [
            {
                "blankKey": "B1",
                "decision": "correct",
                "reason": "语义一致",
                "evidenceRegionIds": ["region-1"],
                "confidence": 0.99,
            }
        ]
    )
    result = await grade_fill_question(
        fill_input(
            [
                {
                    "blankKey": "B1",
                    "maxScore": "2",
                    "answerKind": "text",
                    "standardAnswers": ["电场强度"],
                    "studentAnswer": "场强",
                    "evidenceRegionIds": ["region-1"],
                }
            ]
        ),
        model,  # type: ignore[arg-type]
    )
    assert model.calls == 1
    assert result.final_score == Decimal("2.00")


@pytest.mark.asyncio
async def test_numeric_model_tool_conflict_requires_review() -> None:
    model = ModelStub(
        [
            {
                "blankKey": "B1",
                "decision": "incorrect",
                "reason": "模型认为不同",
                "evidenceRegionIds": ["region-1"],
                "confidence": 0.99,
            }
        ]
    )
    result = await grade_fill_question(
        fill_input(
            [
                {
                    "blankKey": "B1",
                    "maxScore": "2",
                    "answerKind": "numeric",
                    "standardAnswers": ["1 m"],
                    "studentAnswer": "100 cm",
                    "evidenceRegionIds": ["region-1"],
                }
            ]
        ),
        model,  # type: ignore[arg-type]
    )
    assert result.status is GradingStatus.NEEDS_REVIEW
    assert ReviewReason.MODEL_TOOL_CONFLICT in result.review_reasons
    assert result.final_score == Decimal("0.00")


@pytest.mark.asyncio
async def test_shared_region_is_attached_only_by_explicit_blank_evidence_refs() -> None:
    blanks = [
        {
            "blankKey": "B1",
            "maxScore": "1",
            "answerKind": "text",
            "standardAnswers": ["失去"],
            "studentAnswer": "失去",
            "evidenceRegionIds": ["region-1"],
        },
        {
            "blankKey": "B2",
            "maxScore": "1",
            "answerKind": "text",
            "standardAnswers": ["异种"],
            "studentAnswer": "异种",
            "evidenceRegionIds": [],
        },
        {
            "blankKey": "B3",
            "maxScore": "2",
            "answerKind": "text",
            "standardAnswers": ["吸引"],
            "studentAnswer": "吸引",
            "evidenceRegionIds": [],
        },
    ]

    model = ModelStub(
        [
            {
                "blankKey": key,
                "decision": "correct",
                "reason": "答案正确",
                "evidenceRegionIds": refs,
                "confidence": 0.99,
            }
            for key, refs in (("B1", ["region-1"]), ("B2", []), ("B3", []))
        ]
    )
    result = await grade_fill_question(
        fill_input(blanks, evidence_text="失去\n异种、吸引"),
        model,  # type: ignore[arg-type]
    )

    assert result.final_score == Decimal("4.00")
    assert [
        [evidence.region_id for evidence in decision.evidence_refs] for decision in result.decisions
    ] == [["region-1"], [], []]


@pytest.mark.parametrize(
    "recognized_text",
    [
        "失去 异种 吸引",
        "失去\n异种\n吸引",
        "失去，异种；吸引",
        "失去、异种/吸引",
        "失去：异种｜吸引",
    ],
)
def test_shared_evidence_never_uses_answer_text_substrings(
    recognized_text: str,
) -> None:
    region = EvidenceRef(
        page_id="page",
        region_id="region-1",
        original_bbox=BoundingBox(x=1, y=2, width=30, height=10),
        recognized_text=recognized_text,
    )
    blanks = [
        FillBlankInput(
            blankKey="B1",
            maxScore="1",
            answerKind="text",
            standardAnswers=["失去"],
            studentAnswer="失去",
            evidenceRegionIds=["region-1"],
        ),
        FillBlankInput(
            blankKey="B2",
            maxScore="1",
            answerKind="text",
            standardAnswers=["异种"],
            studentAnswer="异种",
        ),
        FillBlankInput(
            blankKey="B3",
            maxScore="2",
            answerKind="text",
            standardAnswers=["吸引"],
            studentAnswer="吸引",
        ),
    ]

    resolved = resolve_fill_blank_evidence(blanks, [region])

    assert {key: [item.region_id for item in value] for key, value in resolved.items()} == {
        "B1": ["region-1"],
        "B2": [],
        "B3": [],
    }


def test_shared_evidence_does_not_guess_empty_or_unmatched_answers() -> None:
    shared = EvidenceRef(
        page_id="page",
        region_id="shared",
        original_bbox=BoundingBox(x=1, y=2, width=30, height=10),
        recognized_text="失去 异种 吸引",
    )
    independent = EvidenceRef(
        page_id="page",
        region_id="independent",
        original_bbox=BoundingBox(x=40, y=2, width=30, height=10),
        recognized_text="单独答案",
    )
    blanks = [
        FillBlankInput(
            blankKey="B1",
            maxScore="1",
            answerKind="text",
            standardAnswers=["失去"],
            studentAnswer="失去",
            evidenceRegionIds=["shared"],
        ),
        FillBlankInput(
            blankKey="B2",
            maxScore="1",
            answerKind="text",
            standardAnswers=["任意"],
            studentAnswer="",
        ),
        FillBlankInput(
            blankKey="B3",
            maxScore="1",
            answerKind="text",
            standardAnswers=["不存在"],
            studentAnswer="不存在",
        ),
        FillBlankInput(
            blankKey="B4",
            maxScore="1",
            answerKind="text",
            standardAnswers=["单独答案"],
            studentAnswer="单独答案",
            evidenceRegionIds=["independent"],
        ),
    ]

    resolved = resolve_fill_blank_evidence(blanks, [shared, independent])

    assert resolved["B1"] == [shared]
    assert resolved["B2"] == []
    assert resolved["B3"] == []
    assert resolved["B4"] == [independent]


def test_shared_evidence_requires_an_explicitly_referenced_candidate() -> None:
    region = EvidenceRef(
        page_id="page",
        region_id="unclaimed",
        original_bbox=BoundingBox(x=1, y=2, width=30, height=10),
        recognized_text="失去 异种",
    )
    blanks = [
        FillBlankInput(
            blankKey="B1",
            maxScore="1",
            answerKind="text",
            standardAnswers=["失去"],
            studentAnswer="失去",
        ),
        FillBlankInput(
            blankKey="B2",
            maxScore="1",
            answerKind="text",
            standardAnswers=["异种"],
            studentAnswer="异种",
        ),
    ]

    resolved = resolve_fill_blank_evidence(blanks, [region])

    assert resolved == {"B1": [], "B2": []}


def test_shared_evidence_does_not_guess_from_full_width_characters() -> None:
    region = EvidenceRef(
        page_id="page",
        region_id="region-1",
        original_bbox=BoundingBox(x=1, y=2, width=30, height=10),
        recognized_text="１Ａ，２Ｂ",
    )
    blanks = [
        FillBlankInput(
            blankKey="B1",
            maxScore="1",
            answerKind="text",
            standardAnswers=["1A"],
            studentAnswer="1A",
            evidenceRegionIds=["region-1"],
        ),
        FillBlankInput(
            blankKey="B2",
            maxScore="1",
            answerKind="text",
            standardAnswers=["2B"],
            studentAnswer="2B",
        ),
    ]

    resolved = resolve_fill_blank_evidence(blanks, [region])

    assert resolved["B2"] == []


@pytest.mark.asyncio
async def test_model_cannot_change_blank_key() -> None:
    model = ModelStub(
        [
            {
                "blankKey": "B2",
                "decision": "correct",
                "reason": "错误地改了键",
                "evidenceRegionIds": ["region-1"],
                "confidence": 0.99,
            }
        ]
    )
    with pytest.raises(ValueError, match="same blankKey"):
        await grade_fill_question(
            fill_input(
                [
                    {
                        "blankKey": "B1",
                        "maxScore": "1",
                        "answerKind": "text",
                        "standardAnswers": ["任意"],
                        "studentAnswer": "任意",
                        "evidenceRegionIds": ["region-1"],
                    }
                ]
            ),
            model,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_model_score_field_is_rejected() -> None:
    model = ModelStub(
        [
            {
                "blankKey": "B1",
                "decision": "correct",
                "reason": "试图直接给分",
                "evidenceRegionIds": ["region-1"],
                "confidence": 0.99,
                "score": 99,
            }
        ]
    )
    with pytest.raises(ValueError):
        await grade_fill_question(
            fill_input(
                [
                    {
                        "blankKey": "B1",
                        "maxScore": "1",
                        "answerKind": "text",
                        "standardAnswers": ["任意"],
                        "studentAnswer": "任意",
                        "evidenceRegionIds": ["region-1"],
                    }
                ]
            ),
            model,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "scores",
    [
        ["2.75"],
        ["0.40", "1.60"],
        ["1.00", "1.25", "2.75"],
        ["0.10", "0.20", "0.30", "0.40", "1.50"],
    ],
)
@pytest.mark.asyncio
async def test_runtime_blank_scores_are_summed_deterministically(scores: list[str]) -> None:
    blanks = [
        {
            "blankKey": f"B{index}",
            "maxScore": score,
            "answerKind": "text",
            "standardAnswers": [f"answer-{index}"],
            "studentAnswer": f"student-{index}",
            "evidenceRegionIds": ["region-1"],
        }
        for index, score in enumerate(scores, start=1)
    ]
    outputs = [
        {
            "blankKey": f"B{index}",
            "decision": "correct" if index % 2 else "incorrect",
            "reason": "固定模型判定",
            "evidenceRegionIds": ["region-1"],
            "confidence": 0.99,
        }
        for index in range(1, len(scores) + 1)
    ]

    result = await grade_fill_question(
        fill_input(blanks),
        ModelStub(outputs),  # type: ignore[arg-type]
    )

    expected = sum(
        (Decimal(score) for index, score in enumerate(scores, start=1) if index % 2),
        Decimal(0),
    )
    assert result.final_score == expected.quantize(Decimal("0.01"))
    assert [decision.key for decision in result.decisions] == [
        f"B{index}" for index in range(1, len(scores) + 1)
    ]
