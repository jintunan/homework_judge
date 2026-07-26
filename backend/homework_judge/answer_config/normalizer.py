from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from ..schemas import (
    AnswerMode,
    NormalizedQuestion,
    ParsedPaper,
    ParseIssue,
    QuestionType,
    ScoringPoint,
    Subject,
)
from ..subjects import get_subject_profile

_NUMBER_KEYS = ("questionNumber", "question_number", "number", "questionNo", "no", "题号", "序号")
_TEXT_KEYS = ("questionText", "question_text", "text", "stem", "content", "题干", "题目")
_TYPE_KEYS = ("type", "questionType", "question_type", "题型", "类型")
_MAX_SCORE_KEYS = ("maxScore", "max_score", "totalScore", "points", "score", "满分", "分值")
_ANSWER_KEYS = (
    "standardAnswer",
    "standard_answer",
    "referenceAnswer",
    "answer",
    "参考答案",
    "标准答案",
    "答案",
)
_POINTS_KEYS = (
    "scoringPoints",
    "scoring_points",
    "scorePoints",
    "rubric",
    "评分点",
    "得分点",
)
_REASON_KEYS = ("reason", "rationale", "explanation", "依据", "理由", "说明")
_CONFIDENCE_KEYS = ("confidence", "置信度")
_ATTENTION_KEYS = (
    "needsAttention",
    "needs_attention",
    "needReview",
    "requiresReview",
    "需关注",
    "需要复核",
)
_POINT_DESCRIPTION_KEYS = (
    "description",
    "desc",
    "criterion",
    "point",
    "评分点",
    "描述",
)
_POINT_SCORE_KEYS = ("score", "points", "value", "分值", "得分")

_TYPE_ALIASES: dict[str, QuestionType] = {
    "choice": QuestionType.CHOICE,
    "single_choice": QuestionType.CHOICE,
    "multiple_choice": QuestionType.CHOICE,
    "选择题": QuestionType.CHOICE,
    "单选题": QuestionType.CHOICE,
    "多选题": QuestionType.CHOICE,
    "fill_blank": QuestionType.FILL_BLANK,
    "fillblank": QuestionType.FILL_BLANK,
    "blank": QuestionType.FILL_BLANK,
    "填空题": QuestionType.FILL_BLANK,
    "short_answer": QuestionType.SHORT_ANSWER,
    "shortanswer": QuestionType.SHORT_ANSWER,
    "简答题": QuestionType.SHORT_ANSWER,
    "解答题": QuestionType.SHORT_ANSWER,
    "calculation": QuestionType.CALCULATION,
    "calculate": QuestionType.CALCULATION,
    "计算题": QuestionType.CALCULATION,
}
_DECIMAL_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def _value(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in node:
            return node[key]
    return None


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float, Decimal)):
        return str(value).strip()[:limit]
    return ""


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            return None
        return result if result.is_finite() else None
    if isinstance(value, str):
        match = _DECIMAL_PATTERN.search(value.replace(",", ""))
        if match is None:
            return None
        try:
            result = Decimal(match.group())
        except InvalidOperation:
            return None
        return result if result.is_finite() else None
    return None


def _confidence(value: Any) -> tuple[float, bool]:
    if isinstance(value, str) and "%" in value:
        decimal = _decimal(value)
        if decimal is not None:
            decimal /= Decimal(100)
    else:
        decimal = _decimal(value)
    if decimal is None:
        return 0.0, value not in (None, "")
    clamped = min(Decimal(1), max(Decimal(0), decimal))
    return float(clamped), clamped != decimal


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
            "是",
            "需要",
            "需复核",
        }
    return False


def _issue(
    index: int,
    field: str | None,
    code: str,
    message: str,
    *,
    severity: Literal["attention", "blocking", "skipped"] = "attention",
    original: Any = None,
    normalized: Any = None,
    requires_correction: bool = False,
) -> ParseIssue:
    path: list[str | int] = ["questions", index]
    if field is not None:
        path.append(field)
    return ParseIssue(
        path=path,
        code=code,
        message=message,
        severity=severity,
        original_value=original,
        normalized_value=normalized,
        requires_correction=requires_correction,
    )


def _question_type(
    value: Any,
    *,
    index: int,
    subject: Subject,
) -> tuple[QuestionType, list[ParseIssue]]:
    profile = get_subject_profile(subject)
    raw = _text(value, 80).lower().replace("-", "_").replace(" ", "_")
    question_type = _TYPE_ALIASES.get(raw)
    if question_type is not None and question_type in profile.supported_types:
        return question_type, []
    code = "question_type_missing" if not raw else "question_type_unsupported"
    return profile.fallback_type, [
        _issue(
            index,
            "type",
            code,
            "题型缺失或不在当前科目的支持范围内，已转为待教师复核题型",
            original=value,
            normalized=profile.fallback_type.value,
        )
    ]


def _raw_points(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value[:30]
    if isinstance(value, dict):
        return [{"description": key, "score": score} for key, score in value.items()][:30]
    return []


def _normalize_reference_points(
    value: Any,
    *,
    index: int,
    max_score: Decimal,
) -> tuple[list[ScoringPoint], list[ParseIssue]]:
    issues: list[ParseIssue] = []
    points: list[ScoringPoint] = []
    raw = _raw_points(value)
    if isinstance(value, list) and len(value) > 30:
        issues.append(
            _issue(
                index,
                "scoringPoints",
                "scoring_points_truncated",
                "评分点超过 30 项，超出部分已忽略",
                original=len(value),
                normalized=30,
            )
        )
    for point_index, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(
                _issue(
                    index,
                    f"scoringPoints.{point_index}",
                    "scoring_point_invalid",
                    "评分点不是对象，已忽略",
                    severity="skipped",
                    original=item,
                )
            )
            continue
        description = _text(_value(item, _POINT_DESCRIPTION_KEYS), 300)
        score = _decimal(_value(item, _POINT_SCORE_KEYS))
        if not description or score is None or score <= 0 or score > 100:
            issues.append(
                _issue(
                    index,
                    f"scoringPoints.{point_index}",
                    "scoring_point_invalid",
                    "评分点描述或分值无效，已忽略",
                    severity="skipped",
                    original=item,
                )
            )
            continue
        points.append(ScoringPoint(description=description, score=score))

    total = sum((point.score for point in points), Decimal(0))
    if total <= max_score or total <= 0:
        return points, issues

    factor = max_score / total
    normalized: list[ScoringPoint] = []
    allocated = Decimal(0)
    cent = Decimal("0.01")
    for point_index, point in enumerate(points):
        if point_index == len(points) - 1:
            score = max_score - allocated
        else:
            score = (point.score * factor).quantize(cent, rounding=ROUND_HALF_UP)
            score = min(score, max_score - allocated)
        score = max(Decimal(0), score)
        normalized.append(ScoringPoint(description=point.description, score=score))
        allocated += score
    issues.append(
        _issue(
            index,
            "scoringPoints",
            "scoring_points_scaled",
            "评分点合计超过题目满分，已按比例归一化，需教师确认",
            original=[point.model_dump(by_alias=True) for point in points],
            normalized=[point.model_dump(by_alias=True) for point in normalized],
        )
    )
    return normalized, issues


def normalize_paper(
    nodes: list[Any],
    *,
    answer_mode: AnswerMode,
    subject: Subject,
    overall_note: str | None = None,
    candidate_shape: Literal["object", "array"] = "object",
    repaired: bool = False,
) -> ParsedPaper:
    questions: list[NormalizedQuestion] = []
    paper_issues: list[ParseIssue] = []
    seen_numbers: dict[str, int] = {}

    for index, raw_node in enumerate(nodes[:100]):
        if not isinstance(raw_node, dict):
            paper_issues.append(
                _issue(
                    index,
                    None,
                    "question_node_invalid",
                    "题目节点不是对象，已跳过",
                    severity="skipped",
                    original=raw_node,
                )
            )
            continue

        text = _text(_value(raw_node, _TEXT_KEYS), 8000)
        if not text:
            paper_issues.append(
                _issue(
                    index,
                    "questionText",
                    "question_text_missing",
                    "题干缺失，当前节点无法形成可审核题目",
                    severity="skipped",
                    original=raw_node,
                )
            )
            continue

        max_score = _decimal(_value(raw_node, _MAX_SCORE_KEYS))
        if max_score is None or max_score <= 0 or max_score > 100:
            paper_issues.append(
                _issue(
                    index,
                    "maxScore",
                    "max_score_invalid",
                    "题目满分无效，当前节点已跳过",
                    severity="skipped",
                    original=_value(raw_node, _MAX_SCORE_KEYS),
                )
            )
            continue

        issues: list[ParseIssue] = []
        number = _text(_value(raw_node, _NUMBER_KEYS), 30)
        requires_correction = False
        if not number:
            number = f"待核{index + 1}"
            requires_correction = True
            issues.append(
                _issue(
                    index,
                    "questionNumber",
                    "question_number_missing",
                    "题号缺失，已生成临时题号，教师必须修正",
                    normalized=number,
                    requires_correction=True,
                )
            )
        duplicate_count = seen_numbers.get(number, 0)
        if duplicate_count:
            original_number = number
            number = f"{number}-待核{duplicate_count + 1}"
            requires_correction = True
            issues.append(
                _issue(
                    index,
                    "questionNumber",
                    "question_number_duplicate",
                    "题号重复，已生成临时唯一题号，教师必须修正",
                    original=original_number,
                    normalized=number,
                    requires_correction=True,
                )
            )
        seen_numbers[_text(_value(raw_node, _NUMBER_KEYS), 30) or number] = duplicate_count + 1

        question_type, type_issues = _question_type(
            _value(raw_node, _TYPE_KEYS),
            index=index,
            subject=subject,
        )
        issues.extend(type_issues)
        confidence, confidence_changed = _confidence(_value(raw_node, _CONFIDENCE_KEYS))
        if confidence_changed:
            issues.append(
                _issue(
                    index,
                    "confidence",
                    "confidence_normalized",
                    "置信度无效或越界，已转换到 0–1 范围",
                    original=_value(raw_node, _CONFIDENCE_KEYS),
                    normalized=confidence,
                )
            )

        answer = _text(_value(raw_node, _ANSWER_KEYS), 8000)
        reason = _text(_value(raw_node, _REASON_KEYS), 4000)
        if answer_mode is AnswerMode.AGENT_SEARCH:
            points: list[ScoringPoint] = []
            if answer or _raw_points(_value(raw_node, _POINTS_KEYS)):
                issues.append(
                    _issue(
                        index,
                        "standardAnswer",
                        "premature_answer_discarded",
                        "无参考答案模式下已清除模型提前生成的答案和评分点，将按检索优先流程重新配置",
                    )
                )
            answer = ""
        else:
            points, point_issues = _normalize_reference_points(
                _value(raw_node, _POINTS_KEYS),
                index=index,
                max_score=max_score,
            )
            issues.extend(point_issues)
            if not answer:
                issues.append(
                    _issue(
                        index,
                        "standardAnswer",
                        "reference_answer_missing",
                        "参考答案未能匹配到本题，需教师补充",
                    )
                )

        needs_attention = (
            _boolean(_value(raw_node, _ATTENTION_KEYS))
            or requires_correction
            or bool(issues)
            or not answer
        )
        questions.append(
            NormalizedQuestion(
                question_number=number,
                question_text=text,
                type=question_type,
                max_score=max_score,
                standard_answer=answer,
                scoring_points=points,
                reason=reason,
                confidence=confidence,
                needs_attention=needs_attention,
                requires_correction=requires_correction,
                issues=issues,
                source_index=index,
            )
        )

    if len(nodes) > 100:
        paper_issues.append(
            ParseIssue(
                path=["questions"],
                code="question_count_truncated",
                message="题目超过 100 道，超出部分已忽略",
                severity="skipped",
                original_value=len(nodes),
                normalized_value=100,
            )
        )
    if not questions:
        paper_issues.append(
            ParseIssue(
                path=["questions"],
                code="no_usable_questions",
                message="模型响应中没有可形成审核草稿的题目",
                severity="blocking",
                requires_correction=True,
            )
        )
    return ParsedPaper(
        questions=questions,
        issues=paper_issues,
        overall_note=overall_note,
        candidate_shape=candidate_shape,
        repaired=repaired,
    )
