from __future__ import annotations

from typing import Any

from ..matching.numbers import normalize_question_number

QUESTION_TYPES = {
    "single_choice",
    "multiple_choice",
    "fill_blank",
    "calculation",
    "short_answer",
    "unknown",
}
TYPE_ALIASES = {
    "choice": "single_choice",
    "选择题": "single_choice",
    "单选题": "single_choice",
    "多选题": "multiple_choice",
    "multiple": "multiple_choice",
    "填空题": "fill_blank",
    "计算题": "calculation",
    "解答题": "short_answer",
    "简答题": "short_answer",
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _pages(value: Any, allowed: set[int]) -> list[int]:
    raw = value if isinstance(value, list) else [value]
    pages: list[int] = []
    for item in raw:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page in allowed and page not in pages:
            pages.append(page)
    return pages


def _answer_regions(value: Any, allowed: set[int]) -> list[dict[str, float | int]]:
    """Normalize model-provided answer boxes to page-relative [0, 1] rectangles.

    Vision models commonly return either ``x/y/width/height`` or ``bbox``
    coordinates, and bbox coordinates are often expressed on a 0..1000 grid.
    Keeping a single normalized representation makes the regions independent of
    the template render resolution.
    """
    raw_regions = value if isinstance(value, list) else []
    regions: list[dict[str, float | int]] = []
    for raw in raw_regions:
        if not isinstance(raw, dict):
            continue
        page_value = raw.get("pageNumber", raw.get("page", raw.get("sourcePage")))
        if page_value is None:
            continue
        try:
            page = int(page_value)
        except (TypeError, ValueError):
            continue
        if page not in allowed:
            continue

        coordinates: tuple[float, float, float, float] | None = None
        bbox = raw.get("bbox", raw.get("bbox2d", raw.get("bbox_2d")))
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = (float(item) for item in bbox)
                coordinates = (x1, y1, x2 - x1, y2 - y1)
            except (TypeError, ValueError):
                coordinates = None
        if coordinates is None:
            try:
                coordinates = (
                    float(raw["x"]),
                    float(raw["y"]),
                    float(raw["width"]),
                    float(raw["height"]),
                )
            except (KeyError, TypeError, ValueError):
                continue

        x, y, width, height = coordinates
        largest = max(abs(x), abs(y), abs(width), abs(height), abs(x + width), abs(y + height))
        if largest > 1.0:
            # Qwen and several other vision models use a normalized 0..1000 grid.
            scale = 1000.0
            x, y, width, height = x / scale, y / scale, width / scale, height / scale
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1.000001
            or y + height > 1.000001
        ):
            continue
        x1, y1 = x, y
        x2, y2 = x + width, y + height
        if x2 - x1 < 0.002 or y2 - y1 < 0.002:
            continue
        region = {
            "page_number": page,
            "x": round(x1, 6),
            "y": round(y1, 6),
            "width": round(x2 - x1, 6),
            "height": round(y2 - y1, 6),
        }
        if region not in regions:
            regions.append(region)
    return regions


def normalize_answer_regions(
    value: Any,
    allowed_pages: set[int],
) -> list[dict[str, float | int]]:
    return _answer_regions(value, allowed_pages)


def normalize_question_regions(
    value: Any,
    allowed_pages: set[int],
) -> list[dict[str, Any]]:
    raw_regions = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for raw in raw_regions:
        geometry = _answer_regions([raw], allowed_pages)
        if not geometry:
            continue
        region = geometry[0]
        issues = (
            [str(item).strip() for item in raw.get("issues", []) if str(item).strip()]
            if isinstance(raw, dict) and isinstance(raw.get("issues"), list)
            else []
        )
        output.append(
            {
                **region,
                "confidence": _confidence(raw.get("confidence", 0.8))
                if isinstance(raw, dict)
                else 0.8,
                "issues": list(dict.fromkeys(issues)),
            }
        )
    return merge_question_regions_by_page(output)


def merge_question_regions_by_page(
    regions: list[dict[str, Any]],
    *,
    padding: float = 0.0,
) -> list[dict[str, Any]]:
    """Normalize, order, and deduplicate fragments without changing their geometry.

    The historical name remains for recognition-boundary callers. A question may
    legitimately have multiple independent fragments on one page, so joining them
    into a bounding hull would silently include unrelated content between them.
    ``padding`` is retained only for call-site compatibility and is intentionally
    ignored.
    """
    del padding
    output: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[int, float, float, float, float]] = set()
    for index, region in enumerate(regions):
        try:
            page_number = int(region["page_number"])
            x = float(region["x"])
            y = float(region["y"])
            width = float(region["width"])
            height = float(region["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            page_number <= 0
            or x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1.000001
            or y + height > 1.000001
        ):
            continue
        key = (
            page_number,
            round(x, 6),
            round(y, 6),
            round(width, 6),
            round(height, 6),
        )
        if key in seen:
            continue
        seen.add(key)
        issues = (
            [str(issue).strip() for issue in region.get("issues", []) if str(issue).strip()]
            if isinstance(region.get("issues"), list)
            else []
        )
        output.append(
            (
                index,
                {
                    **region,
                    "page_number": page_number,
                    "x": key[1],
                    "y": key[2],
                    "width": key[3],
                    "height": key[4],
                    "confidence": _confidence(region.get("confidence", 0.8)),
                    "issues": list(dict.fromkeys(issues)),
                },
            )
        )
    output.sort(
        key=lambda item: (
            int(item[1]["page_number"]),
            float(item[1]["y"]),
            float(item[1]["x"]),
            item[0],
        )
    )
    return [region for _index, region in output]


def normalize_question(
    node: dict[str, Any],
    index: int,
    allowed_pages: set[int],
) -> dict[str, Any]:
    number = _text(node.get("number", node.get("questionNumber")))
    stem = _text(node.get("stem", node.get("questionText")))
    issues = [str(value) for value in node.get("issues", []) if str(value).strip()]
    if not number:
        issues.append("missing_number")
    if not stem:
        issues.append("missing_stem")
    raw_type = _text(node.get("type")).lower()
    question_type = TYPE_ALIASES.get(raw_type, raw_type)
    if question_type not in QUESTION_TYPES:
        question_type = "unknown"
        issues.append("unknown_type")
    score: float | None
    try:
        score = float(node["score"]) if node.get("score") not in {None, ""} else None
        if score is not None and score <= 0:
            raise ValueError
    except (TypeError, ValueError):
        score = None
        issues.append("invalid_score")
    options: list[dict[str, str]] = []
    for option in node.get("options", []) if isinstance(node.get("options"), list) else []:
        if isinstance(option, dict):
            label = _text(option.get("label"))
            text = _text(option.get("text"))
        else:
            label, text = "", _text(option)
        if label or text:
            options.append({"label": label, "text": text})
    pages = _pages(node.get("sourcePages"), allowed_pages)
    if not pages:
        pages = sorted(allowed_pages)[:1]
        issues.append("source_page_inferred")
    answer_regions = _answer_regions(node.get("answerRegions"), allowed_pages)
    question_regions = normalize_question_regions(node.get("questionRegions"), allowed_pages)
    return {
        "sort_order": index,
        "detected_number": number,
        "normalized_number": normalize_question_number(number),
        "stem": stem,
        "options": options,
        "question_type": question_type,
        "score": score,
        "source_pages": pages,
        "answer_regions": answer_regions,
        "question_regions": question_regions,
        "confidence": _confidence(node.get("confidence")),
        "issues": list(dict.fromkeys(issues)),
    }


def normalize_answer(
    node: dict[str, Any],
    index: int,
    allowed_pages: set[int],
) -> dict[str, Any]:
    number = _text(node.get("numberHint", node.get("number", node.get("questionNumber"))))
    answer = _text(node.get("answer", node.get("standardAnswer")))
    issues = [str(value) for value in node.get("issues", []) if str(value).strip()]
    if not number:
        issues.append("missing_number")
    if not answer:
        issues.append("missing_answer")
    pages = _pages(node.get("sourcePages"), allowed_pages)
    if not pages:
        pages = sorted(allowed_pages)[:1]
        issues.append("source_page_inferred")
    return {
        "sort_order": index,
        "number_hint": number,
        "normalized_number": normalize_question_number(number),
        "stem_hint": _text(node.get("stemHint", node.get("questionText"))),
        "answer": answer,
        "explanation": _text(node.get("explanation", node.get("reason"))),
        "source_pages": pages,
        "confidence": _confidence(node.get("confidence")),
        "issues": list(dict.fromkeys(issues)),
    }
