from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from ..schemas import AnswerMode, ParsedPaper, Subject

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_QUESTION_KEYS = (
    "questions",
    "questionList",
    "question_list",
    "items",
    "题目",
    "试题",
    "题目列表",
)
_WRAPPER_KEYS = ("data", "result", "output", "response", "试卷", "结果")


@dataclass(frozen=True, slots=True)
class JsonCandidate:
    value: Any
    shape: Literal["object", "array"]
    source: Literal["complete", "fence", "embedded"]
    question_count: int


class JsonCandidateError(ValueError):
    pass


def extract_json_object(content: str) -> dict[str, Any]:
    trimmed = content.strip().lstrip("\ufeff")
    if not trimmed:
        raise JsonCandidateError("模型响应为空")
    texts = [trimmed, *(match.group(1).strip() for match in _FENCE_PATTERN.finditer(trimmed))]
    for text in texts:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for index, character in enumerate(trimmed):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(trimmed, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise JsonCandidateError("模型响应中没有合法 JSON 对象")


def _question_nodes(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    for key in _QUESTION_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, list):
            return candidate
    for key in _WRAPPER_KEYS:
        nested = value.get(key)
        found = _question_nodes(nested)
        if found is not None:
            return found
    return None


def _candidate(
    value: Any,
    source: Literal["complete", "fence", "embedded"],
) -> JsonCandidate | None:
    nodes = _question_nodes(value)
    if nodes is None:
        return None
    return JsonCandidate(
        value=value,
        shape="array" if isinstance(value, list) else "object",
        source=source,
        question_count=len(nodes),
    )


def _decode_complete(text: str, source: Literal["complete", "fence"]) -> JsonCandidate | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _candidate(value, source)


def extract_json_candidate(content: str) -> JsonCandidate:
    trimmed = content.strip().lstrip("\ufeff")
    if not trimmed:
        raise JsonCandidateError("模型响应为空")

    candidates: list[JsonCandidate] = []
    complete = _decode_complete(trimmed, "complete")
    if complete is not None:
        candidates.append(complete)

    for match in _FENCE_PATTERN.finditer(trimmed):
        fenced = _decode_complete(match.group(1).strip(), "fence")
        if fenced is not None:
            candidates.append(fenced)

    decoder = json.JSONDecoder()
    for index, character in enumerate(trimmed):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(trimmed, index)
        except json.JSONDecodeError:
            continue
        embedded = _candidate(value, "embedded")
        if embedded is not None:
            candidates.append(embedded)

    if not candidates:
        raise JsonCandidateError("模型响应中没有包含题目数组的合法 JSON")

    source_priority = {"complete": 3, "fence": 2, "embedded": 1}
    return max(
        candidates,
        key=lambda item: (item.question_count, source_priority[item.source]),
    )


def unwrap_question_nodes(candidate: JsonCandidate) -> tuple[list[Any], str | None]:
    value = candidate.value
    if isinstance(value, list):
        return value, None
    current: Any = value
    overall_note: str | None = None
    visited: set[int] = set()
    while isinstance(current, dict) and id(current) not in visited:
        visited.add(id(current))
        note = current.get("overallNote", current.get("overall_note", current.get("整体说明")))
        if isinstance(note, str) and note.strip() and overall_note is None:
            overall_note = note.strip()[:4000]
        for key in _QUESTION_KEYS:
            nodes = current.get(key)
            if isinstance(nodes, list):
                return nodes, overall_note
        nested = next(
            (current[key] for key in _WRAPPER_KEYS if key in current),
            None,
        )
        if nested is None:
            break
        current = nested
    raise JsonCandidateError("JSON 中没有可读取的题目数组")


def parse_extracted_paper(
    content: str,
    *,
    answer_mode: AnswerMode,
    subject: Subject,
    repaired: bool = False,
) -> ParsedPaper:
    from .normalizer import normalize_paper

    candidate = extract_json_candidate(content)
    nodes, overall_note = unwrap_question_nodes(candidate)
    return normalize_paper(
        nodes,
        answer_mode=answer_mode,
        subject=subject,
        overall_note=overall_note,
        candidate_shape=candidate.shape,
        repaired=repaired,
    )
