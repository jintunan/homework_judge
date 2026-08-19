from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from homework_judge.artifacts.error_analysis import (
    ERROR_ANALYSIS_SYSTEM_PROMPT,
    ErrorAnalysisOutput,
    ErrorAnalysisQuestionOutput,
    ErrorCategory,
    analyze_errors,
    build_error_analysis_request,
    error_analysis_user_content,
    validate_error_analysis_output,
)
from homework_judge.db.database import json_dumps
from homework_judge.errors import AppError, ModelError
from homework_judge.recognition.client import ModelResponse

from .test_grading_pipeline import grading_settings


def question_row(
    question_id: str = "question",
    *,
    reason: str = "LEGACY_REASON_MUST_NOT_BE_SENT",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "detected_number": "3",
        "question_type": "calculation",
        "stem": "计算电场强度",
        "recognized_text": "F=kqQ/r²，最后一步把 6×7 算成 40",
        "final_score": "3.00",
        "max_score": "4.00",
        "answer_snapshot_json": json_dumps({"answer": "42", "explanation": "先列式再计算"}),
        "grading_config_snapshot_json": json_dumps(
            {
                "rubricPoints": [
                    {
                        "key": "P1",
                        "criterion": "正确选择并应用物理规律",
                        "score": "3.00",
                        "dependencies": [],
                    },
                    {
                        "key": "FINAL_ANSWER",
                        "criterion": "最终答案正确",
                        "score": "1.00",
                        "dependencies": [],
                    },
                ]
            }
        ),
        "decisions_json": json_dumps(
            [
                {
                    "key": "P1",
                    "status": "satisfied",
                    "score": "3.00",
                    "max_score": "3.00",
                    "reason": reason,
                },
                {
                    "key": "FINAL_ANSWER",
                    "status": "failed",
                    "score": "0.00",
                    "max_score": "1.00",
                    "reason": reason,
                },
            ]
        ),
        "evidence_refs_json": json_dumps(
            [
                {
                    "page_id": "page",
                    "region_id": "region",
                    "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 120},
                    "recognized_text": "F=kqQ/r²，6×7=40",
                }
            ]
        ),
        "tool_observations_json": json_dumps([]),
    }


def valid_output(question_id: str = "question") -> ErrorAnalysisOutput:
    return ErrorAnalysisOutput(
        summary="主要问题是末步运算检查不足，应建立验算习惯。",
        questions=[
            ErrorAnalysisQuestionOutput(
                questionId=question_id,
                errorCategory=ErrorCategory.CALCULATION_CARELESS,
                errorReason="公式和代入均正确，但将 6×7 误算为 40，导致最终数值错误。",
                knowledgeGap="整数乘法结果与最终数值的验算能力",
                masteredParts=["能够正确选择公式并完成数据代入"],
                suggestion="完成计算后用逆运算检查乘法，并核对最终数值与数量级。",
            )
        ],
    )


def test_request_contains_evidence_facts_but_not_legacy_reason(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    Image.new("RGB", (800, 1200), "white").save(tmp_path / "student.jpg", "JPEG")
    request = build_error_analysis_request([question_row()])
    content = error_analysis_user_content(
        settings,
        request,
        {
            "region": {
                "original_image_path": "student.jpg",
                "student_bbox_json": json_dumps(
                    {"x": 100, "y": 200, "width": 300, "height": 120}
                ),
            }
        },
    )
    all_text = " ".join(str(item.get("text", "")) for item in content)
    payload = json.loads(content[0]["text"])

    assert "LEGACY_REASON_MUST_NOT_BE_SENT" not in all_text
    assert payload["questions"][0]["studentResponse"]["recognizedText"].startswith("F=")
    assert payload["questions"][0]["gradingFacts"][1] == {
        "key": "FINAL_ANSWER",
        "status": "failed",
        "score": "0.00",
        "maxScore": "1.00",
        "blockedBy": None,
    }
    assert [item["type"] for item in content] == ["text", "text", "image_url"]
    assert payload["questions"][0]["evidenceStatus"] == "available"
    assert "不得评价学习态度" in ERROR_ANALYSIS_SYSTEM_PROMPT


def test_request_marks_missing_image_evidence_explicitly() -> None:
    row = question_row()
    row["evidence_refs_json"] = json_dumps([])

    request = build_error_analysis_request([row])

    assert request.questions[0].evidenceStatus == "missing"
    assert request.questions[0].evidenceRegionIds == []


@pytest.mark.parametrize(
    "questions,match",
    [
        ([], "do not match"),
        ([valid_output().questions[0], valid_output().questions[0]], "duplicate"),
        (
            [valid_output("unknown").questions[0]],
            "do not match",
        ),
    ],
)
def test_output_requires_exact_question_set(
    questions: list[ErrorAnalysisQuestionOutput],
    match: str,
) -> None:
    request = build_error_analysis_request([question_row()])
    output = ErrorAnalysisOutput(summary="诊断摘要", questions=questions)

    with pytest.raises(ValueError, match=match):
        validate_error_analysis_output(request, output)


def test_output_rejects_internal_grading_keys() -> None:
    request = build_error_analysis_request([question_row()])
    output = valid_output()
    leaked = output.questions[0].model_copy(
        update={"masteredParts": ["已经掌握 P1，但 FINAL_ANSWER 错误"]}
    )

    with pytest.raises(ValueError, match="internal grading key"):
        validate_error_analysis_output(
            request,
            output.model_copy(update={"questions": [leaked]}),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("errorCategory", "unsupported_category"),
        ("errorReason", ""),
        ("knowledgeGap", "知" * 121),
        ("suggestion", "练" * 181),
    ],
)
def test_output_rejects_invalid_category_blank_or_oversized_fields(
    field: str,
    value: str,
) -> None:
    payload = valid_output().model_dump(mode="json")
    payload["questions"][0][field] = value

    with pytest.raises(ValueError):
        ErrorAnalysisOutput.model_validate(payload)


def test_output_accepts_explicit_insufficient_evidence_without_guessing() -> None:
    request = build_error_analysis_request([question_row()])
    uncertain = valid_output().questions[0].model_copy(
        update={
            "errorCategory": ErrorCategory.INSUFFICIENT_EVIDENCE,
            "errorReason": "现有作答只保留了最终数值，缺少计算过程，无法判断是算错还是方法错误。",
            "knowledgeGap": "缺少可用于判断知识掌握情况的中间步骤证据",
            "masteredParts": ["现有作答未能显示已稳定掌握的部分"],
            "suggestion": "订正时保留公式、代入和运算三步，便于定位具体偏差。",
        }
    )

    result = validate_error_analysis_output(
        request,
        valid_output().model_copy(update={"questions": [uncertain]}),
    )

    assert result.questions[0].errorCategory is ErrorCategory.INSUFFICIENT_EVIDENCE


class AnalysisClient:
    settings = SimpleNamespace(dashscope_model="analysis-stub")

    def __init__(self, content: str | None = None, failure: bool = False) -> None:
        self.content = content or valid_output().model_dump_json()
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs: object) -> ModelResponse:
        self.calls.append(kwargs)
        if self.failure:
            raise ModelError("MODEL_TIMEOUT", "timeout")
        return ModelResponse(content=self.content, raw={}, usage={"totalTokens": 10})


@pytest.mark.asyncio
async def test_analyze_errors_returns_validated_diagnosis(tmp_path: Path) -> None:
    client = AnalysisClient()
    request = build_error_analysis_request([question_row()])

    result = await analyze_errors(
        client,  # type: ignore[arg-type]
        grading_settings(tmp_path),
        request,
        {},
    )

    assert result.questions[0].errorCategory is ErrorCategory.CALCULATION_CARELESS
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_analyze_errors_wraps_model_and_schema_failures(tmp_path: Path) -> None:
    request = build_error_analysis_request([question_row()])
    settings = grading_settings(tmp_path)
    with pytest.raises(AppError) as model_error:
        await analyze_errors(AnalysisClient(failure=True), settings, request, {})  # type: ignore[arg-type]
    assert model_error.value.code == "ERROR_ANALYSIS_MODEL_FAILED"

    with pytest.raises(AppError) as schema_error:
        await analyze_errors(AnalysisClient(content='{"questions":[]}'), settings, request, {})  # type: ignore[arg-type]
    assert schema_error.value.code == "ERROR_ANALYSIS_INVALID_OUTPUT"


class UnconfiguredAnalysisClient:
    async def chat(self, **_kwargs: object) -> ModelResponse:
        raise ModelError("MODEL_NOT_CONFIGURED", "not configured")


@pytest.mark.asyncio
async def test_analyze_errors_reports_unconfigured_model_explicitly(tmp_path: Path) -> None:
    request = build_error_analysis_request([question_row()])

    with pytest.raises(AppError) as captured:
        await analyze_errors(
            UnconfiguredAnalysisClient(),  # type: ignore[arg-type]
            grading_settings(tmp_path),
            request,
            {},
        )

    assert captured.value.code == "ERROR_ANALYSIS_MODEL_NOT_CONFIGURED"
    assert "未配置" in captured.value.message
