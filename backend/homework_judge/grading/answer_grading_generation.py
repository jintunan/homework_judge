from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import ModelError
from ..recognition.client import DashScopeClient, ModelResponse
from .blank_initialization import allocate_blank_scores, count_stem_blank_markers
from .calculation import normalize_calculation_rubric
from .dependencies import RubricPoint
from .normalization import decimal_string, normalize_options, parse_decimal, quantize_score

ANSWER_GRADING_PROMPT_VERSION = "answer-grading-regeneration-v1"
SUPPORTED_TYPES = {"single_choice", "multiple_choice", "fill_blank", "calculation"}

SYSTEM_PROMPT = """你是教师的当前题答案与批改设置草案助手。只处理提供的这一题。
结合题目原图、OCR 题干、选项和已上传参考答案，返回严格 JSON：
{"questionType":"fill_blank","standardAnswer":"...","explanation":"...","maxScore":"5.00",
 "answerOptions":[],"blanks":[{"answerKind":"text","standardAnswers":["..."],"synonyms":[]}],
 "rubricPoints":[{"pointKey":"P1","criterion":"...","score":"1.00","sortOrder":0,"dependencies":[]}],
 "warnings":[]}
规则：questionType 必须与输入一致；答案和解析不能为空。选择题用 answerOptions 返回选项标签。
填空题必须逐个观察题目原图中所有实际作答位置，不得只依赖 OCR 横线数量；每个空单独返回，
顺序按题面阅读顺序。计算题返回可观察的过程评分点，系统会按正式政策规范化并补充 FINAL_ANSWER。
不要评价学生，不要返回额外字段。"""


class GeneratedBlank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answerKind: Literal["text", "numeric", "formula"] = "text"
    standardAnswers: list[str] = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("standardAnswers", "synonyms")
    @classmethod
    def clean_values(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned and values:
            raise ValueError("answer values cannot be empty")
        return cleaned


class GeneratedRubricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pointKey: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    score: Decimal = Field(gt=0)
    sortOrder: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)


class GeneratedDraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questionType: Literal["single_choice", "multiple_choice", "fill_blank", "calculation"]
    standardAnswer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    maxScore: Decimal = Field(gt=0)
    answerOptions: list[str] = Field(default_factory=list)
    blanks: list[GeneratedBlank] = Field(default_factory=list)
    rubricPoints: list[GeneratedRubricPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("standardAnswer", "explanation")
    @classmethod
    def require_meaningful_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("generated answer and explanation cannot be blank")
        return cleaned


def _image_content(label: str, image: bytes) -> list[dict[str, Any]]:
    encoded = base64.b64encode(image).decode("ascii")
    return [
        {"type": "text", "text": label},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
    ]


def generation_user_content(
    context: dict[str, Any], images: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(context, ensure_ascii=False)}
    ]
    for value in images:
        content.extend(_image_content(str(value["label"]), value["image"]))
    return content


def normalize_generated_draft(
    output: GeneratedDraftOutput,
    *,
    expected_type: str,
    expected_score: Decimal | str | int,
    option_labels: list[str],
    stem: str,
) -> dict[str, Any]:
    if output.questionType != expected_type:
        raise ValueError("generated question type does not match current question")
    total = quantize_score(expected_score)
    if quantize_score(output.maxScore) != total:
        raise ValueError("generated max score does not match current question")
    standard_answer = output.standardAnswer.strip()
    explanation = output.explanation.strip()
    warnings = [value.strip() for value in output.warnings if value.strip()]
    answer_options: list[str] = []
    blanks: list[dict[str, Any]] = []
    rubric_points: list[dict[str, Any]] = []
    if expected_type in {"single_choice", "multiple_choice"}:
        normalized = normalize_options(output.answerOptions or standard_answer)
        answer_options = list(normalized.options)
        if normalized.issues or not answer_options:
            raise ValueError("generated choice answer is invalid")
        if expected_type == "single_choice" and len(answer_options) != 1:
            raise ValueError("single-choice answer must contain exactly one option")
        if expected_type == "multiple_choice" and len(answer_options) < 2:
            raise ValueError("multiple-choice answer must contain at least two options")
        available = {value.strip().upper() for value in option_labels if value.strip()}
        if available and not set(answer_options).issubset(available):
            raise ValueError("generated answer references an unknown option")
        standard_answer = "".join(answer_options)
        if output.blanks or output.rubricPoints:
            raise ValueError("choice draft contains unrelated grading fields")
    elif expected_type == "fill_blank":
        if not output.blanks:
            raise ValueError("fill-blank draft has no blanks")
        scores = allocate_blank_scores(total, len(output.blanks))
        if not scores or any(score <= 0 for score in scores):
            raise ValueError("max score is too small to allocate positive blank scores")
        for index, (blank, score) in enumerate(zip(output.blanks, scores, strict=True)):
            if not blank.standardAnswers:
                raise ValueError("a generated blank has no standard answer")
            if set(blank.standardAnswers) & set(blank.synonyms):
                raise ValueError("standard answers and synonyms overlap")
            blanks.append(
                {
                    "blankKey": f"B{index + 1}",
                    "sortOrder": index,
                    "maxScore": decimal_string(score),
                    "answerKind": blank.answerKind,
                    "standardAnswers": blank.standardAnswers,
                    "synonyms": blank.synonyms,
                    "anchor": None,
                }
            )
        marker_count = count_stem_blank_markers(stem)
        if marker_count and marker_count != len(blanks):
            warnings.append(
                f"OCR 题干识别到 {marker_count} 个空，题目原图与答案草稿判断为 "
                f"{len(blanks)} 个空，请教师重点核对。"
            )
        standard_answer = "；".join(blank["standardAnswers"][0] for blank in blanks)
        if output.answerOptions or output.rubricPoints:
            raise ValueError("fill-blank draft contains unrelated grading fields")
    else:
        proposed = [
            RubricPoint(
                key=item.pointKey,
                criterion=item.criterion.strip(),
                score=parse_decimal(item.score),
                order=item.sortOrder,
                dependencies=item.dependencies,
            )
            for item in output.rubricPoints
            if item.pointKey != "FINAL_ANSWER"
        ]
        normalized_points = normalize_calculation_rubric(proposed, total)
        rubric_points = [
            {
                "pointKey": item.key,
                "criterion": item.criterion,
                "score": decimal_string(item.score),
                "sortOrder": item.order,
                "dependencies": item.dependencies,
            }
            for item in normalized_points
        ]
        if output.answerOptions or output.blanks:
            raise ValueError("calculation draft contains unrelated grading fields")
    return {
        "questionType": expected_type,
        "standardAnswer": standard_answer,
        "explanation": explanation,
        "maxScore": decimal_string(total),
        "answerOptions": answer_options,
        "blanks": blanks,
        "rubricPoints": rubric_points,
        "warnings": list(dict.fromkeys(warnings)),
    }


async def generate_answer_grading_draft(
    client: DashScopeClient,
    *,
    context: dict[str, Any],
    images: list[dict[str, Any]],
) -> tuple[dict[str, Any], ModelResponse]:
    response = await client.chat(
        system_prompt=SYSTEM_PROMPT,
        user_content=generation_user_content(context, images),
    )
    try:
        output = GeneratedDraftOutput.model_validate_json(response.content)
        draft = normalize_generated_draft(
            output,
            expected_type=str(context["questionType"]),
            expected_score=context["maxScore"],
            option_labels=[str(item.get("label", "")) for item in context.get("options", [])],
            stem=str(context.get("stem", "")),
        )
    except (ValueError, TypeError) as error:
        failure = ModelError(
            "ANSWER_GRADING_DRAFT_INVALID",
            "模型生成的答案或批改设置不符合当前题要求，请重试",
        )
        failure.__dict__["raw_response"] = response.raw
        failure.__dict__["model_usage"] = response.usage
        raise failure from error
    return draft, response
