from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from homework_judge.artifacts.error_analysis import (
    ErrorAnalysisOutput,
    ErrorAnalysisQuestionOutput,
    ErrorCategory,
)
from homework_judge.artifacts.error_report import (
    build_error_report_data,
    render_error_report,
)
from homework_judge.db.database import json_dumps

from .test_grading_pipeline import grading_settings


def diagnosis(question_id: str = "question") -> ErrorAnalysisOutput:
    return ErrorAnalysisOutput(
        summary="本次主要问题是电场公式的适用条件不清，应先复习条件再练习。",
        questions=[
            ErrorAnalysisQuestionOutput(
                questionId=question_id,
                errorCategory=ErrorCategory.KNOWLEDGE_GAP,
                errorReason="学生直接套用了点电荷公式，没有判断带电体能否视为点电荷。",
                knowledgeGap="点电荷模型的适用条件和库仑定律的使用范围",
                masteredParts=["能够识别题目给出的电荷量和距离"],
                suggestion="先列出点电荷模型成立的条件，再做两道判断模型是否适用的同类题。",
            )
        ],
    )


def calculation_diagnosis(question_id: str = "question") -> ErrorAnalysisOutput:
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


def test_error_report_uses_ai_diagnosis_and_real_evidence(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    page = tmp_path / "student.jpg"
    Image.new("RGB", (800, 1200), "white").save(page, "JPEG")
    run = {
        "id": "run",
        "result_revision": 2,
        "student_name": "小明",
        "total_score": "1.00",
        "max_score": "4.00",
    }
    question_rows = [
        {
            "question_id": "question",
            "detected_number": "3",
            "question_type": "calculation",
            "stem": "THIS_STEM_MUST_NOT_BECOME_THE_KNOWLEDGE_POINT",
            "final_score": "1.00",
            "max_score": "4.00",
            "decisions_json": json_dumps(
                [
                    {
                        "key": "P1",
                        "status": "failed",
                        "reason": "LEGACY_REASON_MUST_NOT_APPEAR",
                        "score": "0",
                    },
                    {"key": "P4", "status": "satisfied", "score": "1"},
                ]
            ),
            "evidence_refs_json": json_dumps([]),
            "error_locations_json": json_dumps(
                [
                    {
                        "page_id": "page",
                        "region_id": "region",
                        "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 120},
                    }
                ]
            ),
        }
    ]
    data = build_error_report_data(run, question_rows, diagnosis())

    assert data.questions[0].errorCategory == "知识未掌握到位"
    assert "点电荷" in data.questions[0].errorReason
    assert data.questions[0].knowledgeGap == "点电荷模型的适用条件和库仑定律的使用范围"
    assert data.questions[0].masteredParts == ["能够识别题目给出的电荷量和距离"]
    serialized = data.model_dump_json()
    assert "LEGACY_REASON_MUST_NOT_APPEAR" not in serialized
    assert "THIS_STEM_MUST_NOT_BECOME_THE_KNOWLEDGE_POINT" not in serialized
    assert "P1" not in serialized
    assert "P4" not in serialized

    artifact = render_error_report(
        settings=settings,
        data=data,
        region_rows={
            "region": {
                "original_image_path": "student.jpg",
                "student_bbox_json": json_dumps(
                    {"x": 100, "y": 200, "width": 300, "height": 120}
                ),
            }
        },
        output_dir=tmp_path / "report",
    )
    document = pdfium.PdfDocument(artifact.pdf_path)
    try:
        assert len(document) >= 1
        pdf_text = "".join(
            document[index].get_textpage().get_text_bounded()
            for index in range(len(document))
        )
    finally:
        document.close()
    assert data.questions[0].errorReason in pdf_text
    assert data.questions[0].knowledgeGap in pdf_text
    assert data.questions[0].suggestion in pdf_text
    assert "LEGACY_REASON_MUST_NOT_APPEAR" not in pdf_text
    assert "P1" not in pdf_text
    assert artifact.crop_paths[0].is_file()
    assert artifact.data_path.is_file()
    assert artifact.preview["questions"][0]["knowledgeGap"] == data.questions[0].knowledgeGap


def test_calculation_careless_feedback_keeps_correct_method_and_specific_advice() -> None:
    run = {
        "id": "run",
        "result_revision": 2,
        "student_name": "小明",
        "total_score": "3.00",
        "max_score": "4.00",
    }
    row = {
        "question_id": "question",
        "detected_number": "3",
        "question_type": "calculation",
        "final_score": "3.00",
        "max_score": "4.00",
        "evidence_refs_json": json_dumps([]),
        "error_locations_json": json_dumps([]),
    }

    data = build_error_report_data(run, [row], calculation_diagnosis())

    feedback = data.questions[0]
    assert feedback.errorCategory == "计算不认真"
    assert "6×7" in feedback.errorReason
    assert "正确选择公式" in feedback.masteredParts[0]
    assert "逆运算" in feedback.suggestion
    assert feedback.suggestion != diagnosis().questions[0].suggestion


def test_error_report_keeps_teacher_reviewed_question_without_location() -> None:
    run = {
        "id": "run",
        "result_revision": 3,
        "student_name": "小明",
        "total_score": "0.00",
        "max_score": "2.00",
    }
    question_rows = [
        {
            "question_id": "question",
            "detected_number": "13",
            "question_type": "calculation",
            "stem": "计算题",
            "final_score": "0.00",
            "max_score": "2.00",
            "decisions_json": json_dumps([]),
            "evidence_refs_json": json_dumps([]),
            "error_locations_json": json_dumps([]),
        }
    ]

    data = build_error_report_data(run, question_rows, diagnosis())

    assert len(data.questions) == 1
    assert data.questions[0].evidenceRegionId is None
    assert data.questions[0].errorReason == diagnosis().questions[0].errorReason


def test_full_score_report_skips_ai_analysis() -> None:
    run = {
        "id": "run",
        "result_revision": 1,
        "student_name": "小明",
        "total_score": "2.00",
        "max_score": "2.00",
    }
    rows = [
        {
            "question_id": "question",
            "detected_number": "1",
            "question_type": "single_choice",
            "final_score": "2.00",
            "max_score": "2.00",
        }
    ]

    data = build_error_report_data(run, rows, None)

    assert data.questions == []
    assert "全部答对" in data.summary
