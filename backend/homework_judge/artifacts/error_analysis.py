from __future__ import annotations

import base64
import json
import re
from enum import StrEnum
from io import BytesIO
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..config import Settings
from ..db.database import json_loads
from ..errors import AppError, ModelError
from ..files.storage import resolve_data_path
from ..grading.audit import latest_teacher_review_detail
from ..recognition.client import DashScopeClient

ERROR_ANALYSIS_PROMPT_VERSION = "error-analysis-v1-independent-diagnosis"


class ErrorCategory(StrEnum):
    CALCULATION_CARELESS = "calculation_careless"
    KNOWLEDGE_GAP = "knowledge_gap"
    METHOD_GAP = "method_gap"
    MISREAD_QUESTION = "misread_question"
    EXPRESSION_ISSUE = "expression_issue"
    INCOMPLETE_ANSWER = "incomplete_answer"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


ERROR_CATEGORY_LABELS: dict[ErrorCategory, str] = {
    ErrorCategory.CALCULATION_CARELESS: "计算不认真",
    ErrorCategory.KNOWLEDGE_GAP: "知识未掌握到位",
    ErrorCategory.METHOD_GAP: "解题方法未掌握",
    ErrorCategory.MISREAD_QUESTION: "审题错误",
    ErrorCategory.EXPRESSION_ISSUE: "表达或书写问题",
    ErrorCategory.INCOMPLETE_ANSWER: "漏答或步骤不完整",
    ErrorCategory.INSUFFICIENT_EVIDENCE: "证据不足，无法可靠归因",
}

ERROR_ANALYSIS_SYSTEM_PROMPT = f"""你是面向学生的错题诊断教师。
提示版本：{ERROR_ANALYSIS_PROMPT_VERSION}。
你会收到已经由批改与教师复核最终确定的题目事实。你只解释错误，不得修改正误和分数。

请独立比较题目要求、标准答案、学生实际作答、作答图片和结构化评分事实，判断学生为什么出错。
输入中的 gradingFacts 只提供评分点状态、得分和依赖，不包含旧批改理由；
你不能复述内部字段或猜测旧结论。

返回严格 JSON 对象：
{{"summary":"整份试卷的共性问题和优先建议",
 "questions":[{{"questionId":"原样返回题目ID",
 "errorCategory":"calculation_careless|knowledge_gap|method_gap|misread_question|expression_issue|incomplete_answer|insufficient_evidence",
 "errorReason":"结合学生实际作答指出首个关键偏差",
 "knowledgeGap":"需要补齐的具体知识或能力",
 "masteredParts":["学生已经掌握的人可读内容"],
 "suggestion":"与该错误直接对应的可执行建议"}}]}}

要求：
1. questions 必须与输入题目一一对应，不得缺少、重复或新增题目。
2. calculation_careless 只用于方法或公式基本正确，
   但算术、符号、抄写、单位换算或验算出现局部偏差的情况；
   不得把概念错误、方法错误或证据不足笼统归为粗心，更不得评价学习态度。
3. 如果证据不足以区分粗心、知识或方法问题，必须选择 insufficient_evidence，并明确缺少什么证据。
4. knowledgeGap 必须是具体知识、适用条件或能力描述，不能复制题干。
5. masteredParts 必须写成学生能看懂的内容，不得输出 P1、P2、B1、FINAL_ANSWER、answer 等内部评分键；
   如果没有足够证据确认已掌握内容，写明“现有作答未能显示已稳定掌握的部分”。
6. suggestion 必须针对本题原因，给出可以执行的复习、检查或练习方法，不能使用所有题都相同的套话。
7. 不得输出完整标准答案、完整解题过程、评分点键、分数调整意见或额外字段。
8. errorReason 最多 300 个汉字，knowledgeGap 最多 120 个汉字，每个 masteredParts 最多 100 个汉字，
   suggestion 最多 180 个汉字，summary 最多 300 个汉字。"""


class ErrorAnalysisQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionId: str = Field(min_length=1)
    questionNumber: str = Field(min_length=1)
    questionType: str = Field(min_length=1)
    question: str
    standardAnswer: dict[str, Any]
    studentResponse: dict[str, Any]
    finalScore: str
    maxScore: str
    gradingFacts: list[dict[str, Any]]
    rubricFacts: list[dict[str, Any]]
    teacherReviewFacts: list[str]
    evidenceRegionIds: list[str]
    evidenceStatus: Literal["available", "missing"]


class ErrorAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promptVersion: str = ERROR_ANALYSIS_PROMPT_VERSION
    questions: list[ErrorAnalysisQuestionInput] = Field(min_length=1)


class ErrorAnalysisQuestionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionId: str = Field(min_length=1)
    errorCategory: ErrorCategory
    errorReason: str = Field(min_length=1, max_length=300)
    knowledgeGap: str = Field(min_length=1, max_length=120)
    masteredParts: list[str] = Field(min_length=1, max_length=10)
    suggestion: str = Field(min_length=1, max_length=180)

    @model_validator(mode="after")
    def validate_student_facing_text(self) -> ErrorAnalysisQuestionOutput:
        fields = [self.errorReason, self.knowledgeGap, self.suggestion, *self.masteredParts]
        if any(not item.strip() for item in fields):
            raise ValueError("analysis fields must not be blank")
        if any(len(item) > 100 for item in self.masteredParts):
            raise ValueError("mastered parts must be concise")
        forbidden = ("完整答案", "完整解题过程", "标准解答如下")
        if any(token in "".join(fields) for token in forbidden):
            raise ValueError("analysis must not expand a complete answer")
        return self


class ErrorAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=300)
    questions: list[ErrorAnalysisQuestionOutput]


def _grading_facts(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": str(item.get("key", "")),
            "status": str(item.get("status", "")),
            "score": str(item.get("score", "0")),
            "maxScore": str(item.get("max_score", item.get("maxScore", "0"))),
            "blockedBy": item.get("blocked_by", item.get("blockedBy")),
        }
        for item in decisions
    ]


def _rubric_facts(config: dict[str, Any]) -> list[dict[str, Any]]:
    points = config.get("rubricPoints", [])
    if isinstance(points, list) and points:
        return [
            {
                "key": str(item.get("key", "")),
                "criterion": str(item.get("criterion", "")),
                "score": str(item.get("score", "0")),
                "dependencies": item.get("dependencies", []),
            }
            for item in points
            if isinstance(item, dict)
        ]
    blanks = config.get("blanks", [])
    if isinstance(blanks, list):
        return [
            {
                "key": str(item.get("blankKey", "")),
                "answerKind": str(item.get("answerKind", "")),
                "maxScore": str(item.get("maxScore", "0")),
            }
            for item in blanks
            if isinstance(item, dict)
        ]
    return []


def _student_response(
    row: dict[str, Any],
    config: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "recognizedText": str(row.get("recognized_text") or ""),
        "evidenceTexts": [
            {
                "regionId": str(item.get("region_id", "")),
                "recognizedText": str(item.get("recognized_text", "")),
            }
            for item in evidence
            if item.get("region_id")
        ],
    }
    blanks = config.get("blanks", [])
    if isinstance(blanks, list) and blanks:
        value["blankAnswers"] = [
            {
                "blankKey": str(item.get("blankKey", "")),
                "studentAnswer": str(item.get("studentAnswer", "")),
                "isBlank": bool(item.get("isBlank", False)),
            }
            for item in blanks
            if isinstance(item, dict)
        ]
    return value


def build_error_analysis_request(question_rows: list[dict[str, Any]]) -> ErrorAnalysisRequest:
    questions: list[ErrorAnalysisQuestionInput] = []
    for row in question_rows:
        if float(row["final_score"] or 0) >= float(row["max_score"]):
            continue
        decisions = json_loads(row.get("decisions_json"), [])
        evidence = json_loads(row.get("evidence_refs_json"), [])
        config = json_loads(row.get("grading_config_snapshot_json"), {})
        standard_answer = json_loads(row.get("answer_snapshot_json"), {})
        observations = json_loads(row.get("tool_observations_json"), [])
        teacher_detail = latest_teacher_review_detail(observations)
        evidence_ids = list(
            dict.fromkeys(
                str(item.get("region_id"))
                for item in evidence
                if isinstance(item, dict) and item.get("region_id")
            )
        )
        questions.append(
            ErrorAnalysisQuestionInput(
                questionId=str(row["question_id"]),
                questionNumber=str(row["detected_number"]),
                questionType=str(row["question_type"]),
                question=str(row.get("stem") or ""),
                standardAnswer=standard_answer if isinstance(standard_answer, dict) else {},
                studentResponse=_student_response(row, config, evidence),
                finalScore=str(row["final_score"] or "0.00"),
                maxScore=str(row["max_score"]),
                gradingFacts=_grading_facts(decisions),
                rubricFacts=_rubric_facts(config),
                teacherReviewFacts=[teacher_detail] if teacher_detail else [],
                evidenceRegionIds=evidence_ids,
                evidenceStatus="available" if evidence_ids else "missing",
            )
        )
    if not questions:
        raise ValueError("error analysis request requires at least one incorrect question")
    return ErrorAnalysisRequest(questions=questions)


def _crop_region_jpeg(
    settings: Settings,
    region: dict[str, Any],
) -> bytes | None:
    try:
        source = resolve_data_path(settings, str(region["original_image_path"]))
        box = json_loads(region.get("student_bbox_json"), {})
        with Image.open(source) as image:
            left = max(0, int(float(box["x"])))
            top = max(0, int(float(box["y"])))
            right = min(image.width, int(float(box["x"]) + float(box["width"])))
            bottom = min(image.height, int(float(box["y"]) + float(box["height"])))
            if right <= left or bottom <= top:
                return None
            crop = image.crop((left, top, right, bottom)).convert("RGB")
            output = BytesIO()
            crop.save(output, "JPEG", quality=90)
            return output.getvalue()
    except (KeyError, OSError, TypeError, ValueError):
        return None


def error_analysis_user_content(
    settings: Settings,
    request: ErrorAnalysisRequest,
    region_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
        }
    ]
    for question in request.questions:
        for region_id in question.evidenceRegionIds:
            image = _crop_region_jpeg(settings, region_rows.get(region_id, {}))
            if image is None:
                continue
            content.extend(
                [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "questionId": question.questionId,
                                "evidenceRegionId": region_id,
                                "nextImage": "student_answer_crop",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(image).decode("ascii")
                        },
                    },
                ]
            )
    return content


def _contains_internal_key(text: str, keys: set[str]) -> str | None:
    for key in keys:
        if not key or len(key) > 80:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", text):
            return key
    return None


def validate_error_analysis_output(
    request: ErrorAnalysisRequest,
    output: ErrorAnalysisOutput,
) -> ErrorAnalysisOutput:
    expected_ids = [item.questionId for item in request.questions]
    actual_ids = [item.questionId for item in output.questions]
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("analysis contains duplicate question ids")
    if set(actual_ids) != set(expected_ids):
        raise ValueError("analysis question ids do not match the request")
    inputs = {item.questionId: item for item in request.questions}
    for item in output.questions:
        internal_keys = {
            str(fact.get("key", ""))
            for fact in [
                *inputs[item.questionId].gradingFacts,
                *inputs[item.questionId].rubricFacts,
            ]
            if fact.get("key")
        }
        visible = " ".join(
            [item.errorReason, item.knowledgeGap, item.suggestion, *item.masteredParts]
        )
        leaked = _contains_internal_key(visible, internal_keys)
        if leaked:
            raise ValueError(f"analysis exposes internal grading key: {leaked}")
    order = {question_id: index for index, question_id in enumerate(expected_ids)}
    return output.model_copy(
        update={"questions": sorted(output.questions, key=lambda item: order[item.questionId])}
    )


async def analyze_errors(
    client: DashScopeClient,
    settings: Settings,
    request: ErrorAnalysisRequest,
    region_rows: dict[str, dict[str, Any]],
) -> ErrorAnalysisOutput:
    try:
        response = await client.chat(
            system_prompt=ERROR_ANALYSIS_SYSTEM_PROMPT,
            user_content=error_analysis_user_content(settings, request, region_rows),
        )
    except ModelError as error:
        if error.code == "MODEL_NOT_CONFIGURED":
            raise AppError(
                503,
                "ERROR_ANALYSIS_MODEL_NOT_CONFIGURED",
                "AI 错题诊断模型未配置，无法生成错题报告",
            ) from error
        raise AppError(
            502,
            "ERROR_ANALYSIS_MODEL_FAILED",
            "AI 错题诊断失败，请稍后重试",
            {"cause": error.code},
        ) from error
    try:
        parsed = ErrorAnalysisOutput.model_validate_json(response.content)
        return validate_error_analysis_output(request, parsed)
    except (ValidationError, ValueError) as error:
        raise AppError(
            502,
            "ERROR_ANALYSIS_INVALID_OUTPUT",
            "AI 错题诊断返回内容不完整，请重试",
            {"reason": str(error)[:500]},
        ) from error
