from __future__ import annotations

import copy
import re
import unicodedata
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz

from .normalizer import merge_question_regions_by_page

_SUBQUESTION = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[）)]")
_LEADING_SCORE = re.compile(r"^\s*[（(]?\s*\d+(?:\.\d+)?\s*分\s*[）)]?\s*")


def _canonical_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = _LEADING_SCORE.sub("", text)
    text = re.sub(r"\\(?:text|mathrm|operatorname)\s*\{([^}]*)\}", r"\1", text)
    replacements = {
        r"\times": "×",
        r"\cdot": "·",
        r"\infty": "∞",
        r"\to": "→",
        r"\frac": "",
        r"\,": "",
        "$": "",
        "{": "",
        "}": "",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = text.replace("⁻", "-").replace("−", "-")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff×·∞→+-]+", "", text)


def _similarity(left: str, right: str) -> float:
    first, second = _canonical_text(left), _canonical_text(right)
    if not first or not second:
        return 0.0
    return fuzz.WRatio(first, second) / 100


def _question_text(item: dict[str, Any]) -> str:
    """Canonicalize a stem without counting its optional printed number."""
    stem = str(item.get("stem", ""))
    number = str(item.get("normalized_number", "")).strip()
    if number:
        stem = re.sub(
            rf"^\s*(?:第\s*)?{re.escape(number)}\s*(?:题)?\s*[.\u3001:：)）]?\s*",
            "",
            stem,
            count=1,
        )
    return _canonical_text(stem)


def _question_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    first, second = _question_text(left), _question_text(right)
    if not first or not second:
        return 0.0
    return fuzz.WRatio(first, second) / 100


def _same_source_page(left: dict[str, Any], right: dict[str, Any]) -> bool:
    first = {int(value) for value in left.get("source_pages", [])}
    second = {int(value) for value in right.get("source_pages", [])}
    return bool(first & second)


def _cross_batch_partial_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Recognize a short/complete pair emitted from overlapping page batches."""
    left_batch = left.get("_recognition_batch_index")
    right_batch = right.get("_recognition_batch_index")
    if left_batch is None or right_batch is None or left_batch == right_batch:
        return False
    if not _same_source_page(left, right):
        return False
    first, second = _question_text(left), _question_text(right)
    if min(len(first), len(second)) < 40:
        return False
    return fuzz.partial_ratio(first, second) >= 92


def _pages_related(left: dict[str, Any], right: dict[str, Any]) -> bool:
    first = {int(value) for value in left.get("source_pages", [])}
    second = {int(value) for value in right.get("source_pages", [])}
    if not first or not second:
        return True
    return bool(first & second) or min(abs(a - b) for a in first for b in second) <= 1


def _merge_metadata(base: dict[str, Any], item: dict[str, Any]) -> None:
    base["source_pages"] = sorted(
        {int(value) for value in base.get("source_pages", [])}
        | {int(value) for value in item.get("source_pages", [])}
    )
    base["issues"] = list(dict.fromkeys([*base.get("issues", []), *item.get("issues", [])]))
    base["confidence"] = max(float(base.get("confidence", 0)), float(item.get("confidence", 0)))
    base["answer_regions"] = list(
        {
            (
                int(region.get("page_number", 0)),
                float(region.get("x", 0)),
                float(region.get("y", 0)),
                float(region.get("width", 0)),
                float(region.get("height", 0)),
            ): copy.deepcopy(region)
            for region in [
                *base.get("answer_regions", []),
                *item.get("answer_regions", []),
            ]
            if isinstance(region, dict)
        }.values()
    )
    base["question_regions"] = merge_question_regions_by_page(
        list(
            {
                (
                    int(region.get("page_number", 0)),
                    float(region.get("x", 0)),
                    float(region.get("y", 0)),
                    float(region.get("width", 0)),
                    float(region.get("height", 0)),
                ): copy.deepcopy(region)
                for region in [
                    *base.get("question_regions", []),
                    *item.get("question_regions", []),
                ]
                if isinstance(region, dict)
            }.values()
        )
    )
    if base.get("score") is None and item.get("score") is not None:
        base["score"] = item["score"]
    if len(item.get("options", [])) > len(base.get("options", [])):
        base["options"] = copy.deepcopy(item["options"])
    if base.get("question_type") == "unknown" and item.get("question_type") != "unknown":
        base["question_type"] = item["question_type"]


def _merge_duplicate_question(base: dict[str, Any], item: dict[str, Any]) -> None:
    _merge_metadata(base, item)
    base_stem = str(base.get("stem", ""))
    item_stem = str(item.get("stem", ""))
    if len(_canonical_text(item_stem)) > len(_canonical_text(base_stem)):
        base["stem"] = item_stem


def _append_subquestion(base: dict[str, Any], item: dict[str, Any]) -> None:
    _merge_metadata(base, item)
    stem = str(item.get("stem", "")).strip()
    if not stem:
        return
    canonical_stem = _canonical_text(stem)
    if canonical_stem and canonical_stem not in _canonical_text(str(base.get("stem", ""))):
        base["stem"] = f"{str(base.get('stem', '')).rstrip()}\n{stem}".strip()


def consolidate_questions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge cross-batch duplicates and split subquestions conservatively."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unnumbered: list[dict[str, Any]] = []
    order: list[str] = []
    for original in items:
        item = copy.deepcopy(original)
        number = str(item.get("normalized_number", ""))
        if not number:
            unnumbered.append(item)
            continue
        if number not in grouped:
            order.append(number)
        grouped[number].append(item)

    output: list[dict[str, Any]] = []
    for number in order:
        deduplicated: list[dict[str, Any]] = []
        for item in grouped[number]:
            duplicate = next(
                (
                    existing
                    for existing in deduplicated
                    if _pages_related(existing, item)
                    and (
                        _question_similarity(existing, item) >= 0.88
                        or _cross_batch_partial_duplicate(existing, item)
                    )
                ),
                None,
            )
            if duplicate is None:
                deduplicated.append(item)
            else:
                _merge_duplicate_question(duplicate, item)

        anchors = [
            item for item in deduplicated if not _SUBQUESTION.match(str(item.get("stem", "")))
        ]
        continuations = [
            item for item in deduplicated if _SUBQUESTION.match(str(item.get("stem", "")))
        ]
        if (
            len(anchors) == 1
            and continuations
            and all(_pages_related(anchors[0], item) for item in continuations)
        ):
            base = anchors[0]
            continuations.sort(
                key=lambda item: int(_SUBQUESTION.match(str(item.get("stem", ""))).group(1))  # type: ignore[union-attr]
            )
            for item in continuations:
                _append_subquestion(base, item)
            output.append(base)
        else:
            if len(deduplicated) > 1:
                for item in deduplicated:
                    item["issues"] = list(
                        dict.fromkeys([*item.get("issues", []), "duplicate_question_number"])
                    )
            output.extend(deduplicated)

    output.extend(unnumbered)
    output.sort(key=lambda item: int(item.get("sort_order", 0)))
    for index, item in enumerate(output):
        item.pop("_recognition_batch_index", None)
        item.pop("_draft_id", None)
        item["sort_order"] = index
    return output


def _append_distinct(base: str, addition: str) -> str:
    first, second = base.strip(), addition.strip()
    if not second:
        return first
    if not first:
        return second
    canonical_first, canonical_second = _canonical_text(first), _canonical_text(second)
    if canonical_second in canonical_first:
        return first
    if canonical_first in canonical_second:
        return second
    return f"{first}\n{second}"


def consolidate_answers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce one answer entry per top-level number and rescue visible solution text."""
    grouped: dict[str, dict[str, Any]] = {}
    unnumbered: list[dict[str, Any]] = []
    order: list[str] = []
    for original in items:
        item = copy.deepcopy(original)
        if not str(item.get("answer", "")).strip() and str(item.get("explanation", "")).strip():
            item["answer"] = str(item["explanation"]).strip()
            item["issues"] = [
                issue
                for issue in item.get("issues", [])
                if str(issue) not in {"missing_answer", "答案缺失"}
            ]
            item["issues"].append("answer_recovered_from_explanation")
            item["confidence"] = max(0.5, float(item.get("confidence", 0)))
        number = str(item.get("normalized_number", ""))
        if not number:
            unnumbered.append(item)
            continue
        if number not in grouped:
            grouped[number] = item
            order.append(number)
            continue
        base = grouped[number]
        base["answer"] = _append_distinct(str(base.get("answer", "")), str(item.get("answer", "")))
        base["explanation"] = _append_distinct(
            str(base.get("explanation", "")), str(item.get("explanation", ""))
        )
        if not base.get("stem_hint") and item.get("stem_hint"):
            base["stem_hint"] = item["stem_hint"]
        _merge_metadata(base, item)

    output = [grouped[number] for number in order] + unnumbered
    output.sort(key=lambda item: int(item.get("sort_order", 0)))
    for index, item in enumerate(output):
        item.pop("_recognition_batch_index", None)
        item.pop("_draft_id", None)
        item["sort_order"] = index
    return output
