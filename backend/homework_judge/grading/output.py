from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..answer_config.parser import JsonCandidateError, extract_json_object
from ..errors import AppError

_NUMBER_KEYS = ("questionNumber", "question_number", "number", "题号")
_ANSWER_KEYS = ("recognizedAnswer", "recognized_answer", "answer", "学生答案")
_SCORE_KEYS = ("suggestedScore", "suggested_score", "score", "得分")
_REASON_KEYS = ("reason", "rationale", "评分理由", "理由")
_CONFIDENCE_KEYS = ("confidence", "置信度")
_ATTENTION_KEYS = ("needsAttention", "needs_attention", "needReview", "需复核")


@dataclass(frozen=True, slots=True)
class GradeQuestionResult:
    question_number: str
    recognized_answer: str
    suggested_score: float
    reason: str
    confidence: float
    needs_attention: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "questionNumber": self.question_number,
            "recognizedAnswer": self.recognized_answer,
            "suggestedScore": self.suggested_score,
            "reason": self.reason,
            "confidence": self.confidence,
            "needsAttention": self.needs_attention,
        }


@dataclass(frozen=True, slots=True)
class GradeOutput:
    questions: list[GradeQuestionResult]
    overall_note: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": [question.to_dict() for question in self.questions],
            "overallNote": self.overall_note,
        }


def _value(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((node[key] for key in keys if key in node), None)


def _number(value: Any, fallback: float = 0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result != result or result in {float("inf"), float("-inf")}:
        return fallback
    return result


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是", "需要"}


def parse_model_output(
    text: str,
    questions: list[dict[str, Any]],
    low_confidence_threshold: float,
) -> GradeOutput:
    try:
        root = extract_json_object(text)
    except JsonCandidateError as error:
        raise AppError(
            422,
            "MODEL_OUTPUT_INVALID_JSON",
            f"模型没有返回合法 JSON：{error}",
        ) from error
    nodes = root.get("questions", root.get("题目"))
    if not isinstance(nodes, list):
        raise AppError(422, "MODEL_OUTPUT_SCHEMA_ERROR", "模型结果缺少 questions 数组")

    configured = {str(question["number"]): question for question in questions}
    parsed_by_number: dict[str, GradeQuestionResult] = {}
    duplicates: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        number = str(_value(node, _NUMBER_KEYS) or "").strip()
        if number not in configured:
            continue
        if number in parsed_by_number:
            duplicates.add(number)
            continue
        question = configured[number]
        max_score = float(question["maxScore"])
        raw_score = _number(_value(node, _SCORE_KEYS))
        score_out_of_range = raw_score < 0 or raw_score > max_score
        score = max(0.0, min(max_score, raw_score))
        confidence = max(0.0, min(1.0, _number(_value(node, _CONFIDENCE_KEYS))))
        reason = str(_value(node, _REASON_KEYS) or "").strip()[:4000]
        if score_out_of_range:
            reason = f"{reason}（模型建议分越界，系统已限制到合法范围）".strip()
        parsed_by_number[number] = GradeQuestionResult(
            question_number=number,
            recognized_answer=str(_value(node, _ANSWER_KEYS) or "").strip()[:4000],
            suggested_score=score,
            reason=reason,
            confidence=confidence,
            needs_attention=(
                _boolean(_value(node, _ATTENTION_KEYS))
                or confidence < low_confidence_threshold
                or score_out_of_range
            ),
        )

    results: list[GradeQuestionResult] = []
    for question in questions:
        number = str(question["number"])
        result = parsed_by_number.get(number)
        if result is None:
            result = GradeQuestionResult(
                question_number=number,
                recognized_answer="",
                suggested_score=0,
                reason="模型结果缺少本题，需教师人工复核",
                confidence=0,
                needs_attention=True,
            )
        elif number in duplicates:
            result = GradeQuestionResult(
                **{
                    **asdict(result),
                    "reason": f"{result.reason}（模型重复返回本题，需教师复核）",
                    "needs_attention": True,
                }
            )
        results.append(result)
    note = root.get("overallNote", root.get("overall_note", root.get("整体说明")))
    return GradeOutput(
        questions=results,
        overall_note=str(note).strip()[:4000] if isinstance(note, str) else None,
    )
