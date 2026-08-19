from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedPayload:
    nodes: list[dict[str, Any]]
    issues: list[dict[str, Any]]


_BLANK_KEY_PATTERN = re.compile(r"^B[1-9][0-9]*$")
_FORBIDDEN_GRADING_FIELDS = {
    "acceptedanswer",
    "acceptedanswers",
    "correct",
    "correctness",
    "decision",
    "grade",
    "maxscore",
    "points",
    "score",
    "standardanswer",
    "standardanswers",
    "synonym",
    "synonyms",
}


def _issue(code: str, path: str, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message}


def _json_value(text: str) -> tuple[Any, str]:
    last_error = ""
    for candidate in _candidate_strings(text):
        try:
            return json.loads(candidate), ""
        except json.JSONDecodeError as error:
            last_error = str(error)
    return None, last_error


def parse_keyed_fill_response(
    text: str,
    *,
    expected_keys: Sequence[str],
    allowed_evidence_refs: set[str],
) -> ParsedPayload:
    """Strictly validate an answer-free, key-addressed fill transcription.

    This parser deliberately does not repair, infer, renumber, or bind answers by
    array position. Any structural mismatch is returned as an issue so callers
    can retry once and then route the whole question to review.
    """

    expected = list(expected_keys)
    issues: list[dict[str, Any]] = []
    if len(set(expected)) != len(expected) or any(
        not isinstance(key, str) or not _BLANK_KEY_PATTERN.fullmatch(key)
        for key in expected
    ):
        return ParsedPayload(
            [],
            [
                _issue(
                    "expected_key_invalid",
                    "$.answers",
                    "调用方给出的逐空键必须是唯一的 B1...Bn。",
                )
            ],
        )

    parsed, last_error = _json_value(text)
    if not isinstance(parsed, dict):
        return ParsedPayload(
            [],
            [_issue("invalid_json", "$", last_error or "根节点必须是 JSON 对象。")],
        )
    raw_answers = parsed.get("answers")
    if not isinstance(raw_answers, list):
        return ParsedPayload(
            [],
            [_issue("missing_array", "$.answers", "缺少 answers 数组。")],
        )

    normalized_by_key: dict[str, dict[str, Any]] = {}
    observed_keys: list[str] = []
    for index, raw_answer in enumerate(raw_answers):
        path = f"$.answers[{index}]"
        if not isinstance(raw_answer, dict):
            issues.append(_issue("node_not_object", path, "逐空结果必须是对象。"))
            continue

        for field in raw_answer:
            normalized_field = re.sub(r"[^a-z0-9]", "", str(field).lower())
            if normalized_field in _FORBIDDEN_GRADING_FIELDS:
                issues.append(
                    _issue(
                        "forbidden_grading_field",
                        f"{path}.{field}",
                        "识别结果不得包含标准答案、分数或判分字段。",
                    )
                )

        blank_key = raw_answer.get("blankKey")
        if not isinstance(blank_key, str) or not _BLANK_KEY_PATTERN.fullmatch(blank_key):
            issues.append(
                _issue("blank_key_invalid", f"{path}.blankKey", "blankKey 格式非法。")
            )
            continue
        observed_keys.append(blank_key)
        if blank_key in normalized_by_key:
            issues.append(
                _issue(
                    "blank_key_duplicate",
                    f"{path}.blankKey",
                    f"逐空键 {blank_key} 重复。",
                )
            )

        recognized_text = raw_answer.get("recognizedText")
        if not isinstance(recognized_text, str):
            issues.append(
                _issue(
                    "recognized_text_invalid",
                    f"{path}.recognizedText",
                    "recognizedText 必须是字符串。",
                )
            )
            recognized_text = ""

        is_blank = raw_answer.get("isBlank")
        if not isinstance(is_blank, bool):
            issues.append(
                _issue("is_blank_invalid", f"{path}.isBlank", "isBlank 必须是布尔值。")
            )
            is_blank = False
        if is_blank and recognized_text.strip():
            issues.append(
                _issue(
                    "blank_text_inconsistent",
                    f"{path}.recognizedText",
                    "isBlank=true 时 recognizedText 必须为空。",
                )
            )

        confidence = raw_answer.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            issues.append(
                _issue(
                    "confidence_invalid",
                    f"{path}.confidence",
                    "confidence 必须是 0 到 1 的有限数值。",
                )
            )
            confidence = 0.0

        raw_issues = raw_answer.get("issues")
        if not isinstance(raw_issues, list) or any(
            not isinstance(item, str) for item in raw_issues
        ):
            issues.append(
                _issue("issues_invalid", f"{path}.issues", "issues 必须是字符串数组。")
            )
            answer_issues: list[str] = []
        else:
            answer_issues = [item.strip() for item in raw_issues if item.strip()]

        raw_evidence_refs = raw_answer.get("evidenceRefs")
        if not isinstance(raw_evidence_refs, list) or any(
            not isinstance(item, str) for item in raw_evidence_refs
        ):
            issues.append(
                _issue(
                    "evidence_refs_invalid",
                    f"{path}.evidenceRefs",
                    "evidenceRefs 必须是字符串数组。",
                )
            )
            evidence_refs: list[str] = []
        else:
            evidence_refs = list(dict.fromkeys(raw_evidence_refs))
            unknown_refs = [
                item for item in evidence_refs if item not in allowed_evidence_refs
            ]
            if unknown_refs:
                issues.append(
                    _issue(
                        "evidence_ref_unknown",
                        f"{path}.evidenceRefs",
                        f"引用了未提供的证据：{unknown_refs}",
                    )
                )

        if blank_key not in normalized_by_key:
            normalized_by_key[blank_key] = {
                "blankKey": blank_key,
                "recognizedText": "" if is_blank else recognized_text.strip(),
                "isBlank": is_blank,
                "confidence": float(confidence),
                "issues": answer_issues,
                "evidenceRefs": evidence_refs,
            }

    observed = set(observed_keys)
    expected_set = set(expected)
    missing = [key for key in expected if key not in observed]
    extra = sorted(observed - expected_set)
    if missing:
        issues.append(
            _issue("blank_key_missing", "$.answers", f"缺少逐空键：{missing}")
        )
    if extra:
        issues.append(_issue("blank_key_extra", "$.answers", f"出现额外逐空键：{extra}"))

    nodes = [normalized_by_key[key] for key in expected if key in normalized_by_key]
    return ParsedPayload(nodes, issues)


def _candidate_strings(text: str) -> list[str]:
    candidates = [text.strip()]
    if "```" in text:
        for block in text.split("```")[1::2]:
            cleaned = block.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            candidates.append(cleaned)
    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts[:20]:
        opening = text[start]
        closing = "}" if opening == "{" else "]"
        end = text.rfind(closing)
        if end > start:
            candidates.append(text[start : end + 1])
    return list(dict.fromkeys(value for value in candidates if value))


def parse_model_payload(text: str, role: str) -> ParsedPayload:
    parsed: Any = None
    last_error = ""
    for candidate in _candidate_strings(text):
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as error:
            last_error = str(error)
    if parsed is None:
        return ParsedPayload(
            [],
            [{"code": "invalid_json", "path": "$", "message": last_error or "无法解析 JSON"}],
        )
    key = "questions" if role == "exam" else "answers"
    if isinstance(parsed, dict):
        raw_nodes = parsed.get(key)
        if raw_nodes is None and isinstance(parsed.get("items"), list):
            raw_nodes = parsed["items"]
    elif isinstance(parsed, list):
        raw_nodes = parsed
    else:
        raw_nodes = None
    if not isinstance(raw_nodes, list):
        return ParsedPayload(
            [],
            [{"code": "missing_array", "path": f"$.{key}", "message": f"缺少 {key} 数组"}],
        )
    nodes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, node in enumerate(raw_nodes):
        if isinstance(node, dict):
            nodes.append(node)
        else:
            issues.append(
                {
                    "code": "node_not_object",
                    "path": f"$.{key}[{index}]",
                    "message": "条目不是对象，已跳过",
                }
            )
    return ParsedPayload(nodes, issues)


def parse_calculation_localization(text: str) -> ParsedPayload:
    """Parse only a complete, unwrapped calculation-localization JSON object.

    Unlike the legacy recognition parsers, this entry point intentionally does
    not extract JSON from Markdown or surrounding prose. The downstream pure
    contract validator handles exact window and region schemas.
    """

    try:
        parsed = json.loads(
            text.strip(),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_localization_invalid_json",
                    "$",
                    str(error) or "Localization output must be one complete JSON object.",
                )
            ],
        )
    if not isinstance(parsed, dict):
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_localization_root_invalid",
                    "$",
                    "Localization output root must be a JSON object.",
                )
            ],
        )
    expected_fields = {"windows"}
    if set(parsed) != expected_fields:
        return ParsedPayload(
            [],
            [
                {
                    **_issue(
                        "calculation_localization_root_fields_invalid",
                        "$",
                        "Localization root must contain exactly the windows field.",
                    ),
                    "details": {
                        "missing": sorted(expected_fields - set(parsed)),
                        "extra": sorted(set(parsed) - expected_fields),
                    },
                }
            ],
        )
    raw_windows = parsed["windows"]
    if not isinstance(raw_windows, list):
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_localization_windows_invalid",
                    "$.windows",
                    "windows must be a JSON array.",
                )
            ],
        )
    nodes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, window in enumerate(raw_windows):
        if not isinstance(window, dict):
            issues.append(
                _issue(
                    "calculation_localization_window_not_object",
                    f"$.windows[{index}]",
                    "Every localization window must be a JSON object.",
                )
            )
            continue
        nodes.append(window)
    return ParsedPayload(nodes, issues)


def parse_calculation_recognition(text: str) -> ParsedPayload:
    """Parse one strict combined calculation localization/transcription object."""

    try:
        parsed = json.loads(
            text.strip(),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_recognition_invalid_json",
                    "$",
                    str(error) or "Recognition output must be one complete JSON object.",
                )
            ],
        )
    if not isinstance(parsed, dict):
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_recognition_root_invalid",
                    "$",
                    "Recognition output root must be a JSON object.",
                )
            ],
        )
    expected_fields = {"windows"}
    if set(parsed) != expected_fields:
        return ParsedPayload(
            [],
            [
                {
                    **_issue(
                        "calculation_recognition_root_fields_invalid",
                        "$",
                        "Recognition root must contain exactly the windows field.",
                    ),
                    "details": {
                        "missing": sorted(expected_fields - set(parsed)),
                        "extra": sorted(set(parsed) - expected_fields),
                    },
                }
            ],
        )
    raw_windows = parsed["windows"]
    if not isinstance(raw_windows, list):
        return ParsedPayload(
            [],
            [
                _issue(
                    "calculation_recognition_windows_invalid",
                    "$.windows",
                    "windows must be a JSON array.",
                )
            ],
        )
    nodes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, window in enumerate(raw_windows):
        if not isinstance(window, dict):
            issues.append(
                _issue(
                    "calculation_recognition_window_not_object",
                    f"$.windows[{index}]",
                    "Every combined recognition window must be a JSON object.",
                )
            )
            continue
        nodes.append(window)
    return ParsedPayload(nodes, issues)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def parse_boundary_payload(text: str) -> ParsedPayload:
    parsed: Any = None
    last_error = ""
    for candidate in _candidate_strings(text):
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as error:
            last_error = str(error)
    if not isinstance(parsed, dict):
        return ParsedPayload(
            [],
            [{"code": "invalid_json", "path": "$", "message": last_error or "无法解析 JSON"}],
        )
    raw = parsed.get("decisions")
    if not isinstance(raw, list):
        return ParsedPayload(
            [],
            [{"code": "missing_array", "path": "$.decisions", "message": "缺少 decisions 数组"}],
        )
    nodes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, node in enumerate(raw):
        if not isinstance(node, dict):
            issues.append(
                {
                    "code": "node_not_object",
                    "path": f"$.decisions[{index}]",
                    "message": "决策不是对象",
                }
            )
            continue
        relation = str(node.get("relation", ""))
        if relation not in {"merge", "separate", "uncertain"}:
            issues.append(
                {
                    "code": "invalid_relation",
                    "path": f"$.decisions[{index}].relation",
                    "message": "非法边界关系",
                }
            )
            continue
        nodes.append(node)
    return ParsedPayload(nodes, issues)


def parse_student_response(text: str) -> dict[str, Any] | None:
    """Parse the compact one-question transcription response."""
    for candidate in _candidate_strings(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        response = parsed.get("response", parsed)
        if isinstance(response, dict):
            return response
    return None


def parse_blank_detection(text: str) -> dict[str, Any] | None:
    """Parse a complete-frame blank-candidate response without repairing it."""

    for candidate in _candidate_strings(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        raw_candidates = parsed.get("blankCandidates")
        if isinstance(raw_candidates, list):
            return parsed
    return None


def parse_template_regions(text: str) -> list[dict[str, Any]] | None:
    """Parse answer-region detections from a blank template page."""
    for candidate in _candidate_strings(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        regions = parsed.get("regions")
        if isinstance(regions, list):
            return [item for item in regions if isinstance(item, dict)]
    return None
