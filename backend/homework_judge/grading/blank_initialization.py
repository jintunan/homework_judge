from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, Literal

InitializationSource = Literal["saved", "derived", "none"]

_BLANK_MARKER_RE = re.compile(
    r"(?<!\\)(?:_{3,}|＿{2,}|﹍{2,})"
    r"|\\underline\s*\{\s*(?:\\hspace\s*\{[^{}]*\}|\s*)\s*\}"
)
_NUMBERED_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\(\s*\d+\s*\)|（\s*\d+\s*）|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.、])"
)
_STRONG_SEPARATOR_RE = re.compile(r"(?:\r?\n)+|[;；]+")
_MATH_SPAN_RE = re.compile(r"\$[^$]+\$|\\\([^)]*\\\)|\\\[[^]]*\\\]")
_UNIT_RE = re.compile(r"^(?:m|s|kg|g|N|C|V|A|K|mol|Pa|J|W|Hz|Ω|℃)(?:[/·⋅*^²³A-Za-z0-9-]*)$")
_NUMERIC_TOKEN_RE = re.compile(r"^[+\-−]?(?:\d|[.]\d)")
_BOUNDARY_OPERATOR_RE = re.compile(r"(?:[+×÷=/*^]|(?<![eE])[\-−])$")
_LEADING_OPERATOR_RE = re.compile(r"^[+×÷=/*^]")


@dataclass(frozen=True, slots=True)
class BlankInitializationInput:
    stem: str
    reference_answer: str
    max_score: Decimal | str | float | int | None
    answer_regions: list[dict[str, Any]]
    blank_scores: Sequence[Decimal | str | float | int | None] | None = None


@dataclass(frozen=True, slots=True)
class BlankCountSignals:
    stem_marker_count: int
    independent_region_count: int
    structured_answer_count: int | None
    selected_count: int


@dataclass(frozen=True, slots=True)
class InitializationWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class BlankDraft:
    blankKey: str
    sortOrder: int
    maxScore: str
    answerKind: Literal["text", "numeric", "formula"]
    standardAnswers: list[str]
    synonyms: list[str]
    region: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    parts: list[str] | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BlankInitializationResult:
    blanks: list[BlankDraft]
    signals: BlankCountSignals
    warnings: list[InitializationWarning]
    source: InitializationSource = "derived"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "signals": {
                "stemMarkerCount": self.signals.stem_marker_count,
                "independentRegionCount": self.signals.independent_region_count,
                "structuredAnswerCount": self.signals.structured_answer_count,
                "selectedCount": self.signals.selected_count,
            },
            "warnings": [asdict(item) for item in self.warnings],
            "blanks": [asdict(item) for item in self.blanks],
        }


@dataclass(frozen=True, slots=True)
class BlankInitializationReadiness:
    auto_confirmable: bool
    blocking_reasons: list[InitializationWarning]
    advisory_reasons: list[InitializationWarning]


OPTIONAL_ANCHOR_ISSUE_CODES = frozenset(
    {
        "answer_region_count_conflict",
        "blank_anchor_low_confidence",
        "blank_anchor_model_issue",
        "composite_region_shared",
        "missing_blank_anchor",
    }
)
ADVISORY_ISSUE_CODES = OPTIONAL_ANCHOR_ISSUE_CODES | {"blank_score_auto_allocated"}


def count_stem_blank_markers(stem: str) -> int:
    return len(_BLANK_MARKER_RE.findall(stem or ""))


def _clean_parts(parts: list[str]) -> list[str]:
    return [part.strip(" \t,，、:：") for part in parts if part.strip(" \t,，、:：")]


def _numbered_groups(answer: str) -> list[str]:
    matches = list(_NUMBERED_LABEL_RE.finditer(answer))
    if not matches:
        return []
    groups: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        value = answer[match.end() : end].strip()
        if value:
            groups.append(value)
    return groups


def _strong_groups(answer: str) -> list[str]:
    numbered = _numbered_groups(answer)
    if len(numbered) > 1:
        return numbered
    separated = _clean_parts(_STRONG_SEPARATOR_RE.split(answer))
    return separated if len(separated) > 1 else []


def infer_blank_count(value: BlankInitializationInput) -> BlankCountSignals:
    marker_count = count_stem_blank_markers(value.stem)
    region_count = len(_sorted_regions(value.answer_regions))
    strong_groups = _strong_groups(value.reference_answer)
    structured_count = len(strong_groups) if strong_groups else None
    if marker_count > 0:
        selected = marker_count
    elif structured_count and structured_count > 1:
        selected = structured_count
    elif region_count > 1:
        selected = region_count
    else:
        selected = 1
    return BlankCountSignals(marker_count, region_count, structured_count, selected)


def _balanced(value: str) -> bool:
    pairs = (("(", ")"), ("（", "）"), ("[", "]"), ("{", "}"))
    return all(value.count(left) == value.count(right) for left, right in pairs)


def _numeric_unit_was_split(parts: list[str]) -> bool:
    return any(
        _NUMERIC_TOKEN_RE.match(left) and _UNIT_RE.fullmatch(right)
        for left, right in pairwise(parts)
    )


def _unsafe_parts(parts: list[str]) -> bool:
    if any(not _balanced(part) for part in parts):
        return True
    if any(
        _BOUNDARY_OPERATOR_RE.search(part) or _LEADING_OPERATOR_RE.search(part) for part in parts
    ):
        return True
    return _numeric_unit_was_split(parts)


def _protected_whitespace_parts(answer: str) -> list[str] | None:
    if "\\" in answer and not _MATH_SPAN_RE.search(answer):
        return None
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"MATHSPAN{len(protected)}TOKEN"
        protected[key] = match.group(0).strip()
        return key

    value = _MATH_SPAN_RE.sub(replace, answer)
    value = _NUMBERED_LABEL_RE.sub(" ", value)
    parts = _clean_parts(re.split(r"\s+", value.strip()))
    return [protected.get(part, part) for part in parts]


def split_reference_answer(answer: str, expected_count: int) -> SplitOutcome:
    value = answer.strip()
    if expected_count < 1:
        return SplitOutcome(None, "blank_count_invalid")
    if not value:
        return SplitOutcome(None, "reference_answer_missing")
    if expected_count == 1:
        if len(_strong_groups(value)) > 1:
            return SplitOutcome(None, "answer_split_ambiguous")
        return SplitOutcome([value])

    numbered = _numbered_groups(value)
    candidates: list[list[str]] = []
    if numbered:
        candidates.append(numbered)
        expanded: list[str] = []
        for group in numbered:
            strong = _clean_parts(_STRONG_SEPARATOR_RE.split(group))
            if len(strong) > 1:
                expanded.extend(strong)
                continue
            whitespace = _protected_whitespace_parts(group)
            expanded.extend(whitespace or [group])
        candidates.append(expanded)
    strong = _clean_parts(_STRONG_SEPARATOR_RE.split(value))
    if len(strong) > 1:
        candidates.append(strong)
    whitespace = _protected_whitespace_parts(value)
    if whitespace:
        candidates.append(whitespace)

    for candidate in candidates:
        parts = _clean_parts(candidate)
        if len(parts) == expected_count and not _unsafe_parts(parts):
            return SplitOutcome(parts)
    return SplitOutcome(None, "answer_split_ambiguous")


def allocate_blank_scores(
    max_score: Decimal | str | float | int | None,
    blank_count: int,
) -> list[Decimal]:
    if blank_count < 1:
        return []
    try:
        score = Decimal(str(max_score))
    except (InvalidOperation, TypeError, ValueError):
        return [Decimal("0.00")] * blank_count
    if not score.is_finite() or score <= 0:
        return [Decimal("0.00")] * blank_count
    quantum = Decimal("0.01")
    base = (score / Decimal(blank_count)).quantize(quantum, rounding=ROUND_HALF_UP)
    values = [base] * (blank_count - 1)
    remainder = (score - sum(values, Decimal(0))).quantize(quantum)
    if remainder <= 0:
        total_units = int((score / quantum).to_integral_value(rounding=ROUND_HALF_UP))
        if total_units < blank_count:
            return [Decimal("0.00")] * blank_count
        base_units, extra_units = divmod(total_units, blank_count)
        return [
            Decimal(base_units + (1 if index < extra_units else 0)) * quantum
            for index in range(blank_count)
        ]
    values.append(remainder)
    return values


_REGION_WARNING_MESSAGES = {
    "anchor_outside_question_frame": "空位锚点超出模板页范围，请重新定位。",
    "blank_anchor_low_confidence": "空位定位置信度较低，将使用完整题框辅助识别。",
    "blank_anchor_model_issue": "空位定位包含模型提示，将使用完整题框辅助识别。",
    "composite_region_shared": "多个空共享视觉区域，将使用完整题框按空位键识别。",
    "missing_blank_anchor": "至少一个空没有单独定位，将使用完整题框作为共享识别范围。",
    "answer_region_count_conflict": "空位定位数量与空位数量不同，将使用完整题框补充上下文。",
}


def _warning(code: str, message: str | None = None) -> InitializationWarning:
    return InitializationWarning(code, message or _REGION_WARNING_MESSAGES[code])


def _dedupe_warnings(warnings: list[InitializationWarning]) -> list[InitializationWarning]:
    by_code: dict[str, InitializationWarning] = {}
    for warning in warnings:
        by_code.setdefault(warning.code, warning)
    return list(by_code.values())


def _analyze_regions(
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[InitializationWarning]]:
    valid: list[dict[str, Any]] = []
    warnings: list[InitializationWarning] = []
    fingerprints: set[tuple[int, float, float, float, float]] = set()
    for raw in regions:
        if not isinstance(raw, dict):
            warnings.append(_warning("missing_blank_anchor"))
            continue
        raw_box = raw.get("box")
        box: dict[str, Any] = raw_box if isinstance(raw_box, dict) else raw
        try:
            template_page_id = str(
                raw.get("template_page_id", raw.get("templatePageId")) or ""
            ).strip()
            page_value = raw.get("page_number", raw.get("pageNumber"))
            if page_value is None:
                raise ValueError
            page_number = int(page_value)
            coordinate_space = str(
                raw.get("coordinate_space", raw.get("coordinateSpace")) or ""
            ).strip()
            source = str(raw.get("source") or "").strip()
            x = float(box["x"])
            y = float(box["y"])
            width = float(box["width"])
            height = float(box["height"])
        except (KeyError, TypeError, ValueError):
            warnings.append(_warning("missing_blank_anchor"))
            continue
        if (
            not template_page_id
            or coordinate_space != "template_page_normalized"
            or source not in {"model", "teacher", "legacy"}
        ):
            warnings.append(_warning("missing_blank_anchor"))
            continue
        raw_issues = raw.get("issues", [])
        if isinstance(raw_issues, list) and any(str(issue).strip() for issue in raw_issues):
            warnings.append(_warning("blank_anchor_model_issue"))
        confidence: float | None = None
        if raw.get("confidence") is not None:
            try:
                confidence = float(raw["confidence"])
            except (TypeError, ValueError):
                confidence = None
        if confidence is not None and not 0.65 <= confidence <= 1:
            warnings.append(_warning("blank_anchor_low_confidence"))
        if (
            page_number < 1
            or not all(math.isfinite(value) for value in (x, y, width, height))
            or x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1
            or y + height > 1
        ):
            warnings.append(_warning("anchor_outside_question_frame"))
            continue
        fingerprint = (page_number, x, y, width, height)
        if fingerprint in fingerprints:
            warnings.append(_warning("composite_region_shared"))
            continue
        fingerprints.add(fingerprint)
        normalized: dict[str, Any] = {
            "template_page_id": template_page_id,
            "page_number": page_number,
            "coordinate_space": coordinate_space,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "source": source,
            "issues": [
                str(issue).strip()
                for issue in raw_issues
                if str(issue).strip()
            ]
            if isinstance(raw_issues, list)
            else [],
        }
        if confidence is not None:
            normalized["confidence"] = confidence
        valid.append(normalized)
    ordered = sorted(valid, key=lambda item: (item["page_number"], item["y"], item["x"]))
    return ordered, _dedupe_warnings(warnings)


def _sorted_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _analyze_regions(regions)[0]


def assign_blank_regions(
    regions: list[dict[str, Any]],
    blank_count: int,
) -> tuple[list[dict[str, Any] | None], list[InitializationWarning]]:
    ordered, warnings = _analyze_regions(regions)
    if len(ordered) == blank_count:
        return list(ordered), warnings
    if len(ordered) == 1 and blank_count > 1:
        warnings.append(_warning("composite_region_shared"))
    warnings.extend(
        [
            _warning("answer_region_count_conflict"),
            _warning("missing_blank_anchor"),
        ]
    )
    return [None] * blank_count, _dedupe_warnings(warnings)


def _explicit_score_strings(
    scores: Sequence[Decimal | str | float | int | None] | None,
    blank_count: int,
    max_score: Decimal | str | float | int | None,
) -> tuple[list[str], list[InitializationWarning]]:
    values = [""] * blank_count
    warnings: list[InitializationWarning] = []
    if scores is None:
        allocated = allocate_blank_scores(max_score, blank_count)
        if allocated and all(score > 0 for score in allocated):
            return (
                [format(score, "f") for score in allocated],
                [
                    InitializationWarning(
                        "blank_score_auto_allocated",
                        "未提供逐空分值，已按题目总分确定性等额分配；教师可修改。",
                    )
                ],
            )
    if scores is None or len(scores) < blank_count:
        warnings.append(
            InitializationWarning(
                "blank_score_missing",
                "每个空都必须提供有明确来源的正分值，不能按题目总分自动均分。",
            )
        )
    if scores is not None and len(scores) > blank_count:
        warnings.append(
            InitializationWarning(
                "blank_score_total_conflict",
                "逐空分值数量多于空位数量，请逐空检查。",
            )
        )
    for index, raw_score in enumerate((scores or [])[:blank_count]):
        try:
            score = Decimal(str(raw_score))
        except (InvalidOperation, TypeError, ValueError):
            score = Decimal(0)
        if not score.is_finite() or score <= 0:
            warnings.append(
                InitializationWarning(
                    "blank_score_invalid",
                    "每个空都必须提供正的有限 Decimal 分值。",
                )
            )
            continue
        values[index] = format(score, "f")
    return values, _dedupe_warnings(warnings)


def initialize_fill_blanks(value: BlankInitializationInput) -> BlankInitializationResult:
    signals = infer_blank_count(value)
    warnings: list[InitializationWarning] = []
    split = split_reference_answer(value.reference_answer, signals.selected_count)
    if split.parts is None:
        warnings.append(
            InitializationWarning(
                split.reason or "answer_split_ambiguous",
                "答案分配需要检查：参考答案无法安全分配到 "
                f"{signals.selected_count} 个空，请逐空检查。",
            )
        )
    else:
        signals = BlankCountSignals(
            signals.stem_marker_count,
            signals.independent_region_count,
            len(split.parts),
            signals.selected_count,
        )
    scores, score_warnings = _explicit_score_strings(
        value.blank_scores, signals.selected_count, value.max_score
    )
    warnings.extend(score_warnings)
    regions, region_warnings = assign_blank_regions(
        value.answer_regions, signals.selected_count
    )
    warnings.extend(region_warnings)

    parts = split.parts or []
    blanks = [
        BlankDraft(
            blankKey=f"B{index + 1}",
            sortOrder=index,
            maxScore=scores[index],
            answerKind="text",
            standardAnswers=[parts[index]] if index < len(parts) else [],
            synonyms=[],
            region=regions[index],
        )
        for index in range(signals.selected_count)
    ]
    return BlankInitializationResult(blanks, signals, _dedupe_warnings(warnings))


def assess_blank_initialization(
    result: BlankInitializationResult,
    max_score: Decimal | str | float | int | None,
) -> BlankInitializationReadiness:
    reasons = list(result.warnings)
    expected_count = result.signals.selected_count
    if expected_count < 1 or not result.blanks:
        reasons.append(
            InitializationWarning("blank_definitions_missing", "没有可用于正式批改的空位。")
        )
    if len(result.blanks) != expected_count:
        reasons.append(
            InitializationWarning(
                "blank_count_conflict",
                "空位定义数量与题面空位数量不一致。",
            )
        )

    expected_keys = [f"B{index + 1}" for index in range(expected_count)]
    actual_keys = [blank.blankKey for blank in result.blanks]
    if len(set(actual_keys)) != len(actual_keys):
        reasons.append(
            InitializationWarning("duplicate_blank_key", "逐空键重复，请使用唯一的 B1...Bn。")
        )
    if actual_keys != expected_keys:
        reasons.append(
            InitializationWarning(
                "blank_key_conflict",
                "逐空键必须完整、连续，并严格对应 B1...Bn。",
            )
        )
    expected_orders = list(range(expected_count))
    if [blank.sortOrder for blank in result.blanks] != expected_orders:
        reasons.append(
            InitializationWarning(
                "blank_key_conflict",
                "逐空顺序必须从 0 连续排列并与 B1...Bn 对应。",
            )
        )

    populated_answer_count = sum(
        1
        for blank in result.blanks
        if any(str(answer).strip() for answer in blank.standardAnswers)
    )
    if populated_answer_count < expected_count:
        reasons.append(
            InitializationWarning(
                "missing_standard_answer",
                "至少一个空缺少标准答案，请逐空检查并保存。",
            )
        )
    if populated_answer_count > expected_count:
        reasons.append(
            InitializationWarning(
                "extra_standard_answer",
                "逐空标准答案数量多于题面空位数量。",
            )
        )
    structured_answer_count = result.signals.structured_answer_count
    if structured_answer_count is not None and structured_answer_count < expected_count:
        reasons.append(
            InitializationWarning(
                "missing_standard_answer",
                "参考答案中可安全分配的逐空答案不足。",
            )
        )
    if structured_answer_count is not None and structured_answer_count > expected_count:
        reasons.append(
            InitializationWarning(
                "extra_standard_answer",
                "参考答案中可安全分配的逐空答案过多。",
            )
        )

    answer_synonym_conflict = False
    for blank in result.blanks:
        standards = [str(value).strip() for value in blank.standardAnswers]
        synonyms = [str(value).strip() for value in blank.synonyms]
        if (
            any(not value for value in standards + synonyms)
            or len(set(standards)) != len(standards)
            or len(set(synonyms)) != len(synonyms)
            or set(standards) & set(synonyms)
        ):
            answer_synonym_conflict = True
    if answer_synonym_conflict:
        reasons.append(
            InitializationWarning(
                "answer_synonym_conflict",
                "每个空的标准答案和同义词必须非空、各自唯一且彼此不重叠。",
            )
        )

    raw_regions = [blank.region for blank in result.blanks if blank.region is not None]
    valid_regions, region_warnings = _analyze_regions(raw_regions)
    reasons.extend(region_warnings)
    if len(raw_regions) < expected_count or len(valid_regions) < expected_count:
        reasons.append(_warning("missing_blank_anchor"))
    if len(valid_regions) != expected_count:
        reasons.append(_warning("answer_region_count_conflict"))

    try:
        question_score = Decimal(str(max_score))
    except (InvalidOperation, TypeError, ValueError):
        question_score = Decimal(0)
    if not question_score.is_finite() or question_score <= 0:
        reasons.append(
            InitializationWarning("question_score_invalid", "本题满分无效，请先修正题目分值。")
        )

    blank_scores: list[Decimal] = []
    missing_score = False
    invalid_score = False
    for blank in result.blanks[:expected_count]:
        if not str(blank.maxScore).strip():
            missing_score = True
            continue
        try:
            score = Decimal(blank.maxScore)
        except (InvalidOperation, TypeError, ValueError):
            score = Decimal(0)
        if not score.is_finite() or score <= 0:
            invalid_score = True
            continue
        blank_scores.append(score)
    if len(result.blanks) < expected_count:
        missing_score = True
    if len(result.blanks) > expected_count:
        reasons.append(
            InitializationWarning(
                "blank_score_total_conflict",
                "逐空分值数量多于题面空位数量。",
            )
        )
    if missing_score:
        reasons.append(
            InitializationWarning(
                "blank_score_missing",
                "每个空都必须提供有明确来源的正分值，不能按总分自动均分。",
            )
        )
    if invalid_score:
        reasons.append(
            InitializationWarning(
                "blank_score_invalid",
                "至少一个空的分值不是正的有限 Decimal。",
            )
        )
    if (
        question_score.is_finite()
        and question_score > 0
        and len(blank_scores) == expected_count
        and sum(blank_scores, Decimal(0)) != question_score
    ):
        reasons.append(
            InitializationWarning(
                "blank_score_total_conflict",
                "各空分值之和不等于本题满分，请检查后保存。",
            )
        )
    deduped = _dedupe_warnings(reasons)
    advisories = [reason for reason in deduped if reason.code in ADVISORY_ISSUE_CODES]
    blockers = [
        reason for reason in deduped if reason.code not in ADVISORY_ISSUE_CODES
    ]
    return BlankInitializationReadiness(not blockers, blockers, advisories)
