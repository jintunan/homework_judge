from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class FrameValidationIssue:
    """One deterministic blocker tied to a question-frame fragment."""

    code: str
    message: str
    question_id: str
    question_number: str
    region_key: str
    related_question_id: str | None = None
    related_question_number: str | None = None
    related_region_key: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "questionId": self.question_id,
            "questionNumber": self.question_number,
            "regionKey": self.region_key,
            "details": dict(self.details),
        }
        if self.related_question_id is not None:
            value["relatedQuestionId"] = self.related_question_id
        if self.related_question_number is not None:
            value["relatedQuestionNumber"] = self.related_question_number
        if self.related_region_key is not None:
            value["relatedRegionKey"] = self.related_region_key
        return value


@dataclass(frozen=True, slots=True)
class _FragmentContext:
    question_index: int
    question_id: str
    question_number: str
    region_key: str


@dataclass(frozen=True, slots=True)
class _ValidatedFragment:
    context: _FragmentContext
    template_page_id: str
    sort_order: int
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


def validate_frame_fragment(
    fragment: Mapping[str, object],
    *,
    question_id: str,
    question_number: str,
    template_pages: Mapping[str, int],
) -> list[FrameValidationIssue]:
    """Validate one normalized template fragment without using model confidence."""

    context = _FragmentContext(
        question_index=0,
        question_id=str(question_id),
        question_number=str(question_number),
        region_key=_text(_value(fragment, "regionKey", "region_key")),
    )
    issues, _validated = _validate_fragment(fragment, context, template_pages)
    return issues


def validate_question_frame_set(
    items: Sequence[Mapping[str, object]],
    template_pages: Mapping[str, int],
    *,
    overlap_ratio_threshold: float = 0.05,
    edge_tolerance: float = 0.002,
) -> list[FrameValidationIssue]:
    """Validate all fragments and report deterministic cross-question blockers.

    ``template_pages`` maps stable template-page IDs to their one-based page
    numbers. Overlap is measured as intersection area divided by the smaller
    fragment area. Thin boundary contacts within ``edge_tolerance`` are allowed.
    """

    if not math.isfinite(overlap_ratio_threshold) or not 0 <= overlap_ratio_threshold <= 1:
        raise ValueError("overlap_ratio_threshold must be finite and between zero and one")
    if not math.isfinite(edge_tolerance) or edge_tolerance < 0:
        raise ValueError("edge_tolerance must be finite and non-negative")
    if any(
        not page_id or isinstance(page_number, bool) or not isinstance(page_number, int)
        or page_number <= 0
        for page_id, page_number in template_pages.items()
    ):
        raise ValueError("template_pages must map non-empty IDs to positive page numbers")

    issues: list[FrameValidationIssue] = []
    validated: list[_ValidatedFragment] = []
    region_keys: dict[str, _FragmentContext] = {}

    for question_index, item in enumerate(items):
        question_id = _text(_value(item, "questionId", "question_id"))
        question_number = _text(_value(item, "questionNumber", "question_number", "number"))
        raw_fragments = item.get("fragments", ())
        fragments = (
            raw_fragments
            if isinstance(raw_fragments, Sequence)
            and not isinstance(raw_fragments, str | bytes | bytearray)
            else ()
        )
        sort_orders: dict[int, _FragmentContext] = {}
        for raw_fragment in fragments:
            if not isinstance(raw_fragment, Mapping):
                issues.append(
                    FrameValidationIssue(
                        code="frame_fragment_invalid",
                        message="Question-frame fragment must be an object",
                        question_id=question_id,
                        question_number=question_number,
                        region_key="",
                    )
                )
                continue
            fragment = raw_fragment
            context = _FragmentContext(
                question_index=question_index,
                question_id=question_id,
                question_number=question_number,
                region_key=_text(_value(fragment, "regionKey", "region_key")),
            )
            fragment_issues, parsed = _validate_fragment(fragment, context, template_pages)
            issues.extend(fragment_issues)

            if context.region_key:
                previous = region_keys.get(context.region_key)
                if previous is None:
                    region_keys[context.region_key] = context
                else:
                    issues.append(
                        _related_issue(
                            "frame_region_key_duplicate",
                            "regionKey must be unique within a question-frame set",
                            context,
                            previous,
                            {"regionKey": context.region_key},
                        )
                    )

            sort_order = _integer(_value(fragment, "sortOrder", "sort_order"))
            if sort_order is not None and sort_order >= 0:
                previous = sort_orders.get(sort_order)
                if previous is None:
                    sort_orders[sort_order] = context
                else:
                    issues.append(
                        _related_issue(
                            "frame_sort_order_duplicate",
                            "Fragment sortOrder must be unique within one question",
                            context,
                            previous,
                            {"sortOrder": sort_order},
                        )
                    )

            if parsed is not None:
                validated.append(parsed)

    issues.extend(
        _cross_question_overlap_issues(
            validated,
            overlap_ratio_threshold=overlap_ratio_threshold,
            edge_tolerance=edge_tolerance,
        )
    )
    return issues


def _validate_fragment(
    fragment: Mapping[str, object],
    context: _FragmentContext,
    template_pages: Mapping[str, int],
) -> tuple[list[FrameValidationIssue], _ValidatedFragment | None]:
    issues: list[FrameValidationIssue] = []
    if not context.region_key:
        issues.append(
            _issue(
                "frame_region_key_missing",
                "Question-frame fragment requires a non-empty regionKey",
                context,
            )
        )

    sort_order = _integer(_value(fragment, "sortOrder", "sort_order"))
    if sort_order is None or sort_order < 0:
        issues.append(
            _issue(
                "frame_sort_order_invalid",
                "Fragment sortOrder must be a non-negative integer",
                context,
            )
        )

    template_page_id = _text(_value(fragment, "templatePageId", "template_page_id"))
    if not template_page_id:
        issues.append(
            _issue(
                "frame_page_id_missing",
                "Question-frame fragment requires a templatePageId",
                context,
            )
        )

    page_number = _integer(_value(fragment, "pageNumber", "page_number"))
    page_valid = page_number is not None and page_number > 0
    if not page_valid:
        issues.append(
            _issue(
                "frame_page_number_invalid",
                "Fragment pageNumber must be a positive integer",
                context,
            )
        )

    known_page_number = template_pages.get(template_page_id) if template_page_id else None
    if template_page_id and known_page_number is None:
        issues.append(
            _issue(
                "frame_page_unknown",
                "templatePageId does not belong to the template",
                context,
                {"templatePageId": template_page_id},
            )
        )
    elif known_page_number is not None and page_valid and page_number != known_page_number:
        issues.append(
            _issue(
                "frame_page_mismatch",
                "pageNumber does not match templatePageId",
                context,
                {
                    "templatePageId": template_page_id,
                    "pageNumber": page_number,
                    "expectedPageNumber": known_page_number,
                },
            )
        )

    coordinates, coordinate_issue = _coordinates(fragment, context)
    if coordinate_issue is not None:
        issues.append(coordinate_issue)
        return issues, None
    assert coordinates is not None
    x, y, width, height = coordinates
    if width <= 0 or height <= 0:
        issues.append(
            _issue(
                "frame_area_non_positive",
                "Question-frame fragment must have positive width and height",
                context,
                {"width": width, "height": height},
            )
        )
        return issues, None
    if x < 0 or y < 0 or x + width > 1 or y + height > 1:
        issues.append(
            _issue(
                "frame_out_of_bounds",
                "Question-frame fragment must stay within normalized page bounds",
                context,
                {"x": x, "y": y, "width": width, "height": height},
            )
        )
        return issues, None

    can_compare = (
        bool(context.region_key)
        and sort_order is not None
        and sort_order >= 0
        and known_page_number is not None
        and page_valid
        and page_number == known_page_number
    )
    if not can_compare:
        return issues, None
    assert sort_order is not None
    return (
        issues,
        _ValidatedFragment(
            context=context,
            template_page_id=template_page_id,
            sort_order=sort_order,
            x=x,
            y=y,
            width=width,
            height=height,
        ),
    )


def _coordinates(
    fragment: Mapping[str, object],
    context: _FragmentContext,
) -> tuple[tuple[float, float, float, float] | None, FrameValidationIssue | None]:
    values: list[float] = []
    invalid_fields: list[str] = []
    non_finite_fields: list[str] = []
    for field_name in ("x", "y", "width", "height"):
        value = fragment.get(field_name)
        parsed = _number(value)
        if parsed is None:
            invalid_fields.append(field_name)
            continue
        if not math.isfinite(parsed):
            non_finite_fields.append(field_name)
        values.append(parsed)
    if invalid_fields:
        return (
            None,
            _issue(
                "frame_coordinate_invalid",
                "Fragment coordinates must be numeric",
                context,
                {"fields": invalid_fields},
            ),
        )
    if non_finite_fields:
        return (
            None,
            _issue(
                "frame_coordinate_not_finite",
                "Fragment coordinates must be finite",
                context,
                {"fields": non_finite_fields},
            ),
        )
    if len(values) != 4:
        return (
            None,
            _issue(
                "frame_coordinate_invalid",
                "Fragment coordinates must include x, y, width and height",
                context,
            ),
        )
    return (values[0], values[1], values[2], values[3]), None


def _cross_question_overlap_issues(
    fragments: Sequence[_ValidatedFragment],
    *,
    overlap_ratio_threshold: float,
    edge_tolerance: float,
) -> list[FrameValidationIssue]:
    issues: list[FrameValidationIssue] = []
    for index, left in enumerate(fragments):
        for right in fragments[index + 1 :]:
            if left.context.question_index == right.context.question_index:
                continue
            if left.template_page_id != right.template_page_id:
                continue
            overlap_width = min(left.right, right.right) - max(left.x, right.x)
            overlap_height = min(left.bottom, right.bottom) - max(left.y, right.y)
            if overlap_width <= edge_tolerance or overlap_height <= edge_tolerance:
                continue
            overlap_area = overlap_width * overlap_height
            smaller_area = min(left.width * left.height, right.width * right.height)
            overlap_ratio = overlap_area / smaller_area
            if overlap_ratio <= overlap_ratio_threshold:
                continue
            issues.append(
                _related_issue(
                    "frame_cross_question_overlap",
                    (
                        f"第 {left.context.question_number} 题与第 "
                        f"{right.context.question_number} 题的题框重叠 "
                        f"{overlap_ratio:.0%}，请调整两题边界后再确认"
                    ),
                    left.context,
                    right.context,
                    {
                        "templatePageId": left.template_page_id,
                        "overlapArea": round(overlap_area, 6),
                        "overlapRatio": round(overlap_ratio, 6),
                        "threshold": overlap_ratio_threshold,
                    },
                )
            )
    return issues


def _issue(
    code: str,
    message: str,
    context: _FragmentContext,
    details: dict[str, Any] | None = None,
) -> FrameValidationIssue:
    return FrameValidationIssue(
        code=code,
        message=message,
        question_id=context.question_id,
        question_number=context.question_number,
        region_key=context.region_key,
        details=details or {},
    )


def _related_issue(
    code: str,
    message: str,
    context: _FragmentContext,
    related: _FragmentContext,
    details: dict[str, Any] | None = None,
) -> FrameValidationIssue:
    return FrameValidationIssue(
        code=code,
        message=message,
        question_id=context.question_id,
        question_number=context.question_number,
        region_key=context.region_key,
        related_question_id=related.question_id,
        related_question_number=related.question_number,
        related_region_key=related.region_key,
        details=details or {},
    )


def _value(source: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _text(value: object | None) -> str:
    return str(value).strip() if value is not None else ""


def _integer(value: object | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _number(value: object | None) -> float | None:
    if value is None or isinstance(value, bool | str | bytes | bytearray):
        return None
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
