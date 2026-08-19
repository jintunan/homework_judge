from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from pydantic import BaseModel, ConfigDict, Field, model_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import Settings
from ..db.database import json_dumps, json_loads
from ..files.storage import resolve_data_path
from .error_analysis import ERROR_CATEGORY_LABELS, ErrorAnalysisOutput
from .fonts import reportlab_font


class ErrorQuestionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionId: str
    questionNumber: str
    questionType: str
    score: str
    maxScore: str
    errorCategory: str = Field(min_length=1, max_length=40)
    errorReason: str = Field(min_length=1, max_length=300)
    knowledgeGap: str = Field(min_length=1, max_length=120)
    masteredParts: list[str] = Field(default_factory=list, max_length=10)
    suggestion: str = Field(min_length=1, max_length=180)
    evidenceRegionId: str | None = None

    @model_validator(mode="after")
    def concise_feedback(self) -> ErrorQuestionFeedback:
        forbidden = ("完整答案", "完整解题过程", "标准解答如下")
        combined = f"{self.errorReason}{self.knowledgeGap}{self.suggestion}"
        if any(item in combined for item in forbidden):
            raise ValueError("feedback must not expand a complete answer")
        return self


class ErrorReportData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gradingRunId: str
    resultRevision: int = Field(ge=0)
    studentName: str
    totalScore: str
    maxScore: str
    summary: str = Field(min_length=1, max_length=300)
    questions: list[ErrorQuestionFeedback]


@dataclass(frozen=True, slots=True)
class ErrorReportArtifact:
    pdf_path: Path
    data_path: Path
    crop_paths: tuple[Path, ...]
    content_hash: str
    preview: dict[str, object]


def build_error_report_data(
    run: dict[str, Any],
    question_rows: list[dict[str, Any]],
    analysis: ErrorAnalysisOutput | None,
) -> ErrorReportData:
    incorrect_rows = [
        row
        for row in question_rows
        if float(row["final_score"] or 0) < float(row["max_score"])
    ]
    if not incorrect_rows:
        return ErrorReportData(
            gradingRunId=str(run["id"]),
            resultRevision=int(run["result_revision"]),
            studentName=str(run.get("student_name") or "学生"),
            totalScore=str(run["total_score"] or "0.00"),
            maxScore=str(run["max_score"] or "0.00"),
            summary="本次作业全部答对。继续保持审题和规范书写习惯。",
            questions=[],
        )
    if analysis is None:
        raise ValueError("non-full-score report requires validated AI analysis")
    analysis_by_id = {item.questionId: item for item in analysis.questions}
    questions: list[ErrorQuestionFeedback] = []
    for row in incorrect_rows:
        diagnosis = analysis_by_id.get(str(row["question_id"]))
        if diagnosis is None:
            raise ValueError("validated analysis is missing an incorrect question")
        error_locations = json_loads(row["error_locations_json"], [])
        evidence = json_loads(row["evidence_refs_json"], [])
        crop_sources = [*error_locations, *evidence]
        evidence_region_id = (
            str(crop_sources[0].get("region_id")) if crop_sources else None
        )
        questions.append(
            ErrorQuestionFeedback(
                questionId=str(row["question_id"]),
                questionNumber=str(row["detected_number"]),
                questionType=str(row["question_type"]),
                score=str(row["final_score"]),
                maxScore=str(row["max_score"]),
                errorCategory=ERROR_CATEGORY_LABELS[diagnosis.errorCategory],
                errorReason=diagnosis.errorReason,
                knowledgeGap=diagnosis.knowledgeGap,
                masteredParts=diagnosis.masteredParts,
                suggestion=diagnosis.suggestion,
                evidenceRegionId=evidence_region_id,
            )
        )
    return ErrorReportData(
        gradingRunId=str(run["id"]),
        resultRevision=int(run["result_revision"]),
        studentName=str(run.get("student_name") or "学生"),
        totalScore=str(run["total_score"] or "0.00"),
        maxScore=str(run["max_score"] or "0.00"),
        summary=analysis.summary,
        questions=questions,
    )


def _crop_evidence(
    settings: Settings,
    region_id: str,
    region_rows: dict[str, dict[str, Any]],
    output: Path,
) -> Path | None:
    region = region_rows.get(region_id)
    if not region:
        return None
    source = resolve_data_path(settings, str(region["original_image_path"]))
    box = json_loads(region["student_bbox_json"], {})
    with PILImage.open(source) as image:
        left = max(0, int(float(box["x"])))
        top = max(0, int(float(box["y"])))
        right = min(image.width, int(float(box["x"]) + float(box["width"])))
        bottom = min(image.height, int(float(box["y"]) + float(box["height"])))
        if right <= left or bottom <= top:
            return None
        crop = image.crop((left, top, right, bottom)).convert("RGB")
        output.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output, "JPEG", quality=92)
    return output


def render_error_report(
    *,
    settings: Settings,
    data: ErrorReportData,
    region_rows: dict[str, dict[str, Any]],
    output_dir: Path,
) -> ErrorReportArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "error-report.json"
    data_path.write_text(json_dumps(data.model_dump(mode="json")), encoding="utf-8")
    crop_paths: list[Path] = []
    crop_by_question: dict[str, Path] = {}
    for item in data.questions:
        if not item.evidenceRegionId:
            continue
        crop = _crop_evidence(
            settings,
            item.evidenceRegionId,
            region_rows,
            output_dir / "crops" / f"question-{item.questionId}.jpg",
        )
        if crop:
            crop_paths.append(crop)
            crop_by_question[item.questionId] = crop

    font = reportlab_font()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ChineseTitle",
        parent=styles["Title"],
        fontName=font,
        fontSize=20,
        leading=28,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#173F35"),
    )
    body = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=10.5,
        leading=17,
        wordWrap="CJK",
        textColor=colors.HexColor("#27332F"),
    )
    heading = ParagraphStyle(
        "ChineseHeading",
        parent=body,
        fontSize=13,
        leading=20,
        textColor=colors.HexColor("#8F2F27"),
        spaceAfter=4,
    )
    pdf_path = output_dir / "error-analysis.pdf"
    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="作业错题分析",
    )
    story: list[Any] = [
        Paragraph("作业错题分析", title),
        Spacer(1, 5 * mm),
        Table(
            [
                ["学生", data.studentName, "成绩", f"{data.totalScore}/{data.maxScore}"],
                ["分析", Paragraph(data.summary, body), "错题数", str(len(data.questions))],
            ],
            colWidths=[18 * mm, 55 * mm, 18 * mm, 62 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F1EC")),
                    ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8F1EC")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BCD0C6")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        Spacer(1, 6 * mm),
    ]
    if not data.questions:
        story.append(Paragraph("本次没有需要订正的题目。", body))
    for item in data.questions:
        section: list[Any] = [
            Paragraph(f"第 {item.questionNumber} 题　{item.score}/{item.maxScore}", heading),
            Paragraph(f"错误类型：{item.errorCategory}", body),
            Paragraph(f"错误原因：{item.errorReason}", body),
            Paragraph(f"知识薄弱点：{item.knowledgeGap}", body),
        ]
        if item.masteredParts:
            section.append(Paragraph(f"已经掌握：{'、'.join(item.masteredParts)}", body))
        section.append(Paragraph(f"改进建议：{item.suggestion}", body))
        crop = crop_by_question.get(item.questionId)
        if crop:
            image = Image(str(crop), width=70 * mm, height=32 * mm, kind="proportional")
            section.extend([Spacer(1, 2 * mm), image])
        section.append(Spacer(1, 5 * mm))
        story.append(KeepTogether(section))
    document.build(story)
    digest = hashlib.sha256()
    for path in [data_path, *crop_paths, pdf_path]:
        digest.update(path.read_bytes())
    return ErrorReportArtifact(
        pdf_path=pdf_path,
        data_path=data_path,
        crop_paths=tuple(crop_paths),
        content_hash=digest.hexdigest(),
        preview=data.model_dump(mode="json"),
    )
