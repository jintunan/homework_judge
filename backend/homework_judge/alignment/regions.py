from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from PIL import Image

from .engine import PageInput, warp_student_to_template
from .geometry import (
    Bounds,
    Homography,
    Point,
    Polygon,
    clip_polygon_to_bounds,
    polygon_intersection_ratio,
    polygon_out_of_bounds_ratio,
    polygon_visible_ratio,
)
from .models import (
    AlignmentResult,
    AnswerRegion,
    ExtractedAnswerRegion,
    FramePageAlignment,
    FrameSetMappingResult,
    MappedAnswerRegion,
    MappedFrameRegion,
    MappingBlocker,
    QuestionFrameRegion,
)

_REGION_KEYS = ("answer_regions", "answerRegions", "answer_region", "answerRegion", "regions")
_PAGE_KEYS = ("page_number", "pageNumber", "source_page", "sourcePage", "page")


def load_question_regions(
    path: str | Path,
    *,
    default_page_number: int | None = None,
) -> list[AnswerRegion]:
    """Load region definitions from a questions JSON object."""

    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, Mapping):
        raise ValueError("questions JSON must contain an object keyed by question id")
    return parse_question_regions(payload, default_page_number=default_page_number)


def parse_question_regions(
    questions: Mapping[str, Any],
    *,
    default_page_number: int | None = None,
) -> list[AnswerRegion]:
    """Parse zero or more pixel-coordinate answer regions for every question.

    Supported region geometry forms are ``polygon``/``points``, explicit
    ``left/top/right/bottom``, explicit ``x/y/width/height``, and a four-item
    ``bbox``. Sequence bboxes default to ``xyxy`` and may declare
    ``bbox_format: \"xywh\"``. Questions without region metadata are skipped,
    which keeps this compatible with existing questions.json files.
    """

    parsed: list[AnswerRegion] = []
    for raw_question_id, question in questions.items():
        question_id = str(raw_question_id)
        if not isinstance(question, Mapping):
            continue
        raw_regions = _first_value(question, _REGION_KEYS)
        if raw_regions is None:
            continue
        question_page = _page_number(question, default_page_number)
        entries = _region_entries(raw_regions)
        for index, (suggested_id, raw_region) in enumerate(entries, 1):
            region_id = suggested_id or f"{question_id}:region:{index}"
            page_number = question_page
            if isinstance(raw_region, Mapping):
                region_id = str(
                    raw_region.get("region_id")
                    or raw_region.get("regionId")
                    or raw_region.get("id")
                    or region_id
                )
                page_number = _page_number(raw_region, question_page)
            if page_number is None:
                raise ValueError(f"{question_id}/{region_id} is missing a page number")
            parsed.append(
                AnswerRegion(
                    question_id=question_id,
                    region_id=region_id,
                    page_number=page_number,
                    template_polygon=_parse_polygon(raw_region),
                )
            )
    return parsed


def map_answer_regions(
    regions: Sequence[AnswerRegion],
    alignment: AlignmentResult,
) -> list[MappedAnswerRegion]:
    """Map template regions onto a student page without discarding original coordinates."""

    mapped: list[MappedAnswerRegion] = []
    for region in regions:
        original_polygon = alignment.template_to_student.map_polygon(region.template_polygon)
        original_bbox = original_polygon.bounds
        visible_bbox = original_bbox.clipped(
            alignment.student_size.width,
            alignment.student_size.height,
        )
        mapped.append(
            MappedAnswerRegion(
                question_id=region.question_id,
                region_id=region.region_id,
                page_number=region.page_number,
                template_polygon=region.template_polygon,
                original_page_polygon=original_polygon,
                original_page_bbox=original_bbox,
                visible_original_page_bbox=visible_bbox,
            )
        )
    return mapped


def map_confirmed_frame_set(
    frame_set: Mapping[str, object],
    page_alignments: Mapping[str, FramePageAlignment],
    *,
    min_alignment_score: float,
    min_polygon_area_px: float,
    min_visible_ratio: float,
    max_out_of_bounds_ratio: float,
    max_cross_question_overlap_ratio: float,
) -> FrameSetMappingResult:
    """Map every fragment in one frozen frame set, failing closed on quality issues.

    The function is intentionally pure: it does not persist rows or call a
    recognition model. Valid preview mappings are retained even when another
    fragment blocks the batch, but the batch status prevents downstream use.
    """

    _validate_mapping_thresholds(
        min_alignment_score=min_alignment_score,
        min_polygon_area_px=min_polygon_area_px,
        min_visible_ratio=min_visible_ratio,
        max_out_of_bounds_ratio=max_out_of_bounds_ratio,
        max_cross_question_overlap_ratio=max_cross_question_overlap_ratio,
    )
    frame_set_id = _nonempty_text_or_none(frame_set.get("id"))
    if frame_set_id is None:
        return _mapping_failure(
            None,
            MappingBlocker(
                code="frame_set_invalid",
                message="Question frame set is missing a stable id",
                next_action="confirm_question_frames",
            ),
        )
    if frame_set.get("status") != "confirmed":
        return _mapping_failure(
            frame_set_id,
            MappingBlocker(
                code="frame_set_not_confirmed",
                message="Student mapping requires a confirmed question frame set",
                next_action="confirm_question_frames",
                details={"status": str(frame_set.get("status", ""))},
            ),
        )

    raw_items = frame_set.get("items")
    if not _is_sequence(raw_items) or not raw_items:
        return _mapping_failure(
            frame_set_id,
            MappingBlocker(
                code="frame_set_has_no_items",
                message="Confirmed question frame set contains no frame items",
                next_action="confirm_question_frames",
            ),
        )

    fragments: list[QuestionFrameRegion] = []
    blockers: list[MappingBlocker] = []
    seen_frame_region_ids: set[str] = set()
    for raw_item in cast(Sequence[object], raw_items):
        if not isinstance(raw_item, Mapping):
            blockers.append(
                MappingBlocker(
                    code="frame_item_invalid",
                    message="Confirmed frame item must be an object",
                    next_action="confirm_question_frames",
                )
            )
            continue
        question_id = _nonempty_text_or_none(raw_item.get("questionId"))
        if question_id is None:
            blockers.append(
                MappingBlocker(
                    code="frame_item_invalid",
                    message="Confirmed frame item is missing questionId",
                    next_action="confirm_question_frames",
                )
            )
            continue
        if raw_item.get("status") != "confirmed":
            blockers.append(
                MappingBlocker(
                    code="frame_item_not_confirmed",
                    message="Student mapping requires every frame item to be confirmed",
                    question_id=question_id,
                    next_action="confirm_question_frames",
                    details={"status": str(raw_item.get("status", ""))},
                )
            )
            continue
        raw_fragments = raw_item.get("fragments")
        if not _is_sequence(raw_fragments) or not raw_fragments:
            blockers.append(
                MappingBlocker(
                    code="frame_item_has_no_fragments",
                    message="Confirmed frame item contains no fragments",
                    question_id=question_id,
                    next_action="confirm_question_frames",
                )
            )
            continue

        item_fragments: list[QuestionFrameRegion] = []
        for raw_fragment in cast(Sequence[object], raw_fragments):
            if not isinstance(raw_fragment, Mapping):
                blockers.append(
                    MappingBlocker(
                        code="frame_fragment_invalid",
                        message="Confirmed frame fragment must be an object",
                        question_id=question_id,
                        next_action="confirm_question_frames",
                    )
                )
                continue
            try:
                fragment = _parse_confirmed_frame_region(
                    frame_set_id,
                    question_id,
                    cast(Mapping[str, object], raw_fragment),
                )
            except (KeyError, TypeError, ValueError) as error:
                blockers.append(
                    MappingBlocker(
                        code="frame_fragment_invalid",
                        message="Confirmed frame fragment has invalid geometry or metadata",
                        question_id=question_id,
                        frame_region_id=_nonempty_text_or_none(raw_fragment.get("id")),
                        template_page_id=_nonempty_text_or_none(
                            raw_fragment.get("templatePageId")
                        ),
                        next_action="confirm_question_frames",
                        details={"reason": str(error)},
                    )
                )
                continue
            if fragment.frame_region_id in seen_frame_region_ids:
                blockers.append(
                    _region_blocker(
                        fragment,
                        code="frame_region_id_duplicate",
                        message="Confirmed frame set contains a duplicate frame region id",
                        next_action="confirm_question_frames",
                    )
                )
                continue
            seen_frame_region_ids.add(fragment.frame_region_id)
            item_fragments.append(fragment)
        item_fragments.sort(key=lambda item: (item.sort_order, item.frame_region_id))
        fragments.extend(item_fragments)

    mappings: list[MappedFrameRegion] = []
    resolved_transforms: dict[str, Homography] = {}
    invalid_transforms: dict[str, str] = {}
    for fragment in fragments:
        page_alignment = page_alignments.get(fragment.template_page_id)
        if page_alignment is None:
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_page_missing",
                    message="No student page alignment exists for the template page",
                    details={"pageNumber": fragment.page_number},
                )
            )
            continue
        if (
            page_alignment.template_page_id != fragment.template_page_id
            or page_alignment.template_page_number != fragment.page_number
        ):
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_page_mismatch",
                    message="Frame fragment and alignment revision refer to different pages",
                    student_page_id=page_alignment.student_page_id,
                    details={
                        "alignmentTemplatePageId": page_alignment.template_page_id,
                        "alignmentPageNumber": page_alignment.template_page_number,
                    },
                )
            )
            continue

        transform = resolved_transforms.get(fragment.template_page_id)
        transform_error = invalid_transforms.get(fragment.template_page_id)
        if transform is None and transform_error is None:
            try:
                transform = page_alignment.resolved_transform()
            except (OverflowError, TypeError, ValueError) as error:
                transform_error = str(error)
                invalid_transforms[fragment.template_page_id] = transform_error
            else:
                resolved_transforms[fragment.template_page_id] = transform
        if transform is None:
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_transform_not_invertible",
                    message="Page alignment transform is invalid or not invertible",
                    student_page_id=page_alignment.student_page_id,
                    details={"reason": transform_error or "invalid transform"},
                )
            )
            continue

        mapping_issues: list[str] = []
        if (
            not page_alignment.quality.is_reliable
            or page_alignment.quality.score < min_alignment_score
        ):
            code = "mapping_alignment_low_quality"
            mapping_issues.append(code)
            blockers.append(
                _region_blocker(
                    fragment,
                    code=code,
                    message="Page alignment quality is below the mapping threshold",
                    student_page_id=page_alignment.student_page_id,
                    details={
                        "score": page_alignment.quality.score,
                        "minimumScore": min_alignment_score,
                        "isReliable": page_alignment.quality.is_reliable,
                        "warnings": list(page_alignment.quality.warnings),
                    },
                )
            )

        template_polygon = fragment.template_pixel_polygon(page_alignment.template_size)
        if not _transform_is_finite_across_polygon(transform, template_polygon):
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_transform_invalid",
                    message="Page transform crosses infinity inside the frame polygon",
                    student_page_id=page_alignment.student_page_id,
                )
            )
            continue
        try:
            original_polygon = transform.map_polygon(template_polygon)
        except (OverflowError, ValueError) as error:
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_transform_invalid",
                    message="Page transform cannot map the complete frame polygon",
                    student_page_id=page_alignment.student_page_id,
                    details={"reason": str(error)},
                )
            )
            continue

        mapped_area = original_polygon.area
        page_bounds = Bounds(
            0.0,
            0.0,
            float(page_alignment.student_size.width),
            float(page_alignment.student_size.height),
        )
        try:
            visible_polygon = clip_polygon_to_bounds(original_polygon, page_bounds)
            visible_ratio = polygon_visible_ratio(original_polygon, page_bounds)
            out_of_bounds_ratio = polygon_out_of_bounds_ratio(original_polygon, page_bounds)
        except (OverflowError, ValueError) as error:
            blockers.append(
                _region_blocker(
                    fragment,
                    code="mapping_polygon_degenerate",
                    message="Mapped frame polygon cannot be measured safely",
                    student_page_id=page_alignment.student_page_id,
                    details={"reason": str(error)},
                )
            )
            continue
        visible_area = visible_polygon.area if visible_polygon is not None else 0.0

        if not math.isfinite(mapped_area) or mapped_area < min_polygon_area_px:
            code = "mapping_polygon_degenerate"
            mapping_issues.append(code)
            blockers.append(
                _region_blocker(
                    fragment,
                    code=code,
                    message="Mapped frame polygon area is below the usable threshold",
                    student_page_id=page_alignment.student_page_id,
                    details={
                        "mappedAreaPx": mapped_area,
                        "minimumAreaPx": min_polygon_area_px,
                    },
                )
            )
        if visible_ratio < min_visible_ratio:
            code = "mapping_severe_clipping"
            mapping_issues.append(code)
            blockers.append(
                _region_blocker(
                    fragment,
                    code=code,
                    message="Too little of the mapped frame remains visible on the student page",
                    student_page_id=page_alignment.student_page_id,
                    details={
                        "visibleRatio": visible_ratio,
                        "minimumVisibleRatio": min_visible_ratio,
                    },
                )
            )
        if out_of_bounds_ratio > max_out_of_bounds_ratio:
            code = "mapping_out_of_bounds"
            mapping_issues.append(code)
            blockers.append(
                _region_blocker(
                    fragment,
                    code=code,
                    message="Mapped frame extends too far outside the student page",
                    student_page_id=page_alignment.student_page_id,
                    details={
                        "outOfBoundsRatio": out_of_bounds_ratio,
                        "maximumOutOfBoundsRatio": max_out_of_bounds_ratio,
                    },
                )
            )

        mappings.append(
            MappedFrameRegion(
                frame_set_id=fragment.frame_set_id,
                question_id=fragment.question_id,
                frame_region_id=fragment.frame_region_id,
                region_key=fragment.region_key,
                template_page_id=fragment.template_page_id,
                page_number=fragment.page_number,
                sort_order=fragment.sort_order,
                student_page_id=page_alignment.student_page_id,
                alignment_revision_id=page_alignment.alignment_revision_id,
                template_normalized_bbox=fragment.template_normalized_bbox,
                template_page_polygon=template_polygon,
                original_page_polygon=original_polygon,
                original_page_bbox=original_polygon.bounds,
                visible_original_page_polygon=visible_polygon,
                visible_original_page_bbox=(
                    visible_polygon.bounds if visible_polygon is not None else None
                ),
                mapped_area_px=mapped_area,
                visible_area_px=visible_area,
                visible_ratio=visible_ratio,
                out_of_bounds_ratio=out_of_bounds_ratio,
                issues=tuple(dict.fromkeys(mapping_issues)),
            )
        )

    mappings, overlap_blockers = _validate_cross_question_overlaps(
        mappings,
        max_cross_question_overlap_ratio=max_cross_question_overlap_ratio,
    )
    blockers.extend(overlap_blockers)
    return FrameSetMappingResult(
        frame_set_id=frame_set_id,
        status="mapping_needs_review" if blockers else "ready",
        mappings=tuple(mappings),
        blockers=tuple(blockers),
    )


def _parse_confirmed_frame_region(
    frame_set_id: str,
    question_id: str,
    value: Mapping[str, object],
) -> QuestionFrameRegion:
    if value.get("coordinateSpace") != "template_page_normalized":
        raise ValueError("coordinateSpace must be template_page_normalized")
    frame_region_id = _required_mapping_text(value.get("id"), "frame fragment id")
    region_key = _required_mapping_text(value.get("regionKey"), "frame fragment regionKey")
    template_page_id = _required_mapping_text(
        value.get("templatePageId"), "frame fragment templatePageId"
    )
    page_number = _required_mapping_integer(
        value.get("pageNumber"), "frame fragment pageNumber", minimum=1
    )
    sort_order = _required_mapping_integer(
        value.get("sortOrder"), "frame fragment sortOrder", minimum=0
    )
    x = _required_mapping_number(value.get("x"), "frame fragment x")
    y = _required_mapping_number(value.get("y"), "frame fragment y")
    width = _required_mapping_number(value.get("width"), "frame fragment width")
    height = _required_mapping_number(value.get("height"), "frame fragment height")
    return QuestionFrameRegion(
        frame_set_id=frame_set_id,
        question_id=question_id,
        frame_region_id=frame_region_id,
        region_key=region_key,
        template_page_id=template_page_id,
        page_number=page_number,
        sort_order=sort_order,
        template_normalized_bbox=Bounds(x, y, x + width, y + height),
    )


def _validate_cross_question_overlaps(
    mappings: list[MappedFrameRegion],
    *,
    max_cross_question_overlap_ratio: float,
) -> tuple[list[MappedFrameRegion], list[MappingBlocker]]:
    maximums = [0.0] * len(mappings)
    issue_codes = [list(mapping.issues) for mapping in mappings]
    blockers: list[MappingBlocker] = []
    for left_index, left in enumerate(mappings):
        for right_index in range(left_index + 1, len(mappings)):
            right = mappings[right_index]
            if (
                left.student_page_id != right.student_page_id
                or left.question_id == right.question_id
            ):
                continue
            try:
                overlap_ratio = polygon_intersection_ratio(
                    left.original_page_polygon,
                    right.original_page_polygon,
                )
            except ValueError as error:
                code = "mapping_overlap_check_failed"
                issue_codes[left_index].append(code)
                issue_codes[right_index].append(code)
                blockers.append(
                    MappingBlocker(
                        code=code,
                        message=(
                            "Mapped frame polygons cannot be checked for cross-question overlap"
                        ),
                        question_id=left.question_id,
                        frame_region_id=left.frame_region_id,
                        template_page_id=left.template_page_id,
                        student_page_id=left.student_page_id,
                        details={
                            "otherQuestionId": right.question_id,
                            "otherFrameRegionId": right.frame_region_id,
                            "reason": str(error),
                        },
                    )
                )
                continue
            maximums[left_index] = max(maximums[left_index], overlap_ratio)
            maximums[right_index] = max(maximums[right_index], overlap_ratio)
            if overlap_ratio <= max_cross_question_overlap_ratio:
                continue
            code = "mapping_cross_question_overlap"
            issue_codes[left_index].append(code)
            issue_codes[right_index].append(code)
            blockers.append(
                MappingBlocker(
                    code=code,
                    message=(
                        "Mapped frame overlaps a different question above the allowed threshold"
                    ),
                    question_id=left.question_id,
                    frame_region_id=left.frame_region_id,
                    template_page_id=left.template_page_id,
                    student_page_id=left.student_page_id,
                    details={
                        "otherQuestionId": right.question_id,
                        "otherFrameRegionId": right.frame_region_id,
                        "overlapRatio": overlap_ratio,
                        "maximumOverlapRatio": max_cross_question_overlap_ratio,
                    },
                )
            )

    updated = [
        replace(
            mapping,
            max_cross_question_overlap_ratio=maximums[index],
            issues=tuple(dict.fromkeys(issue_codes[index])),
        )
        for index, mapping in enumerate(mappings)
    ]
    return updated, blockers


def _transform_is_finite_across_polygon(
    transform: Homography,
    polygon: Polygon,
) -> bool:
    g, h, i = transform.values[6:]
    denominators = [g * point.x + h * point.y + i for point in polygon.points]
    scale = max(1.0, *(abs(value) for value in denominators))
    tolerance = scale * 1e-12
    if any(not math.isfinite(value) or abs(value) <= tolerance for value in denominators):
        return False
    return all(value > 0.0 for value in denominators) or all(
        value < 0.0 for value in denominators
    )


def _region_blocker(
    region: QuestionFrameRegion,
    *,
    code: str,
    message: str,
    student_page_id: str | None = None,
    next_action: str = "correct_page_alignment",
    details: Mapping[str, object] | None = None,
) -> MappingBlocker:
    return MappingBlocker(
        code=code,
        message=message,
        question_id=region.question_id,
        frame_region_id=region.frame_region_id,
        template_page_id=region.template_page_id,
        student_page_id=student_page_id,
        next_action=next_action,
        details=dict(details or {}),
    )


def _mapping_failure(
    frame_set_id: str | None,
    blocker: MappingBlocker,
) -> FrameSetMappingResult:
    return FrameSetMappingResult(
        frame_set_id=frame_set_id,
        status="mapping_needs_review",
        mappings=(),
        blockers=(blocker,),
    )


def _validate_mapping_thresholds(
    *,
    min_alignment_score: float,
    min_polygon_area_px: float,
    min_visible_ratio: float,
    max_out_of_bounds_ratio: float,
    max_cross_question_overlap_ratio: float,
) -> None:
    ratios = {
        "min_alignment_score": min_alignment_score,
        "min_visible_ratio": min_visible_ratio,
        "max_out_of_bounds_ratio": max_out_of_bounds_ratio,
        "max_cross_question_overlap_ratio": max_cross_question_overlap_ratio,
    }
    for name, value in ratios.items():
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be finite and between zero and one")
    if not math.isfinite(min_polygon_area_px) or min_polygon_area_px <= 0.0:
        raise ValueError("min_polygon_area_px must be finite and positive")


def _nonempty_text_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_mapping_text(value: object, label: str) -> str:
    parsed = _nonempty_text_or_none(value)
    if parsed is None:
        raise ValueError(f"{label} must be a non-empty string")
    return parsed


def _required_mapping_integer(value: object, label: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{label} must be an integer greater than or equal to {minimum}")
    return value


def _required_mapping_number(value: object, label: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def extract_answer_regions(
    student_page: PageInput,
    regions: Sequence[AnswerRegion],
    alignment: AlignmentResult,
    *,
    padding: int = 0,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> list[ExtractedAnswerRegion]:
    """Create template-rectified recognition crops plus original-page metadata."""

    if padding < 0:
        raise ValueError("padding must be non-negative")
    rectified = warp_student_to_template(student_page, alignment, resample=resample)
    mappings = map_answer_regions(regions, alignment)
    extracted: list[ExtractedAnswerRegion] = []
    for region, mapping in zip(regions, mappings, strict=True):
        template_bounds = region.template_polygon.bounds.padded(float(padding)).clipped(
            alignment.template_size.width,
            alignment.template_size.height,
        )
        crop_box = template_bounds.to_pixel_box()
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            raise ValueError(f"region {region.region_id} is outside the template page")
        extracted.append(
            ExtractedAnswerRegion(
                image=rectified.crop(crop_box),
                mapping=mapping,
                template_crop_box=crop_box,
            )
        )
    return extracted


def _first_value(source: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _page_number(source: Mapping[str, Any], fallback: int | None) -> int | None:
    value = _first_value(source, _PAGE_KEYS)
    if value is None:
        return fallback
    try:
        page_number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid page number: {value!r}") from error
    if page_number <= 0:
        raise ValueError("page numbers are one-based and must be positive")
    return page_number


def _region_entries(value: Any) -> list[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        if _looks_like_geometry(value):
            return [(None, value)]
        return [(str(region_id), region) for region_id, region in value.items()]
    if _is_sequence(value):
        if len(value) == 4 and all(_is_number(item) for item in value):
            return [(None, value)]
        return [(None, region) for region in value]
    raise ValueError("answer regions must be a region, list, or object")


def _parse_polygon(value: Any) -> Polygon:
    if _is_sequence(value):
        return _bbox_polygon(value, "xyxy")
    if not isinstance(value, Mapping):
        raise ValueError("region geometry must be an object or four-item bbox")

    points = value.get("polygon", value.get("points"))
    if points is not None:
        if not _is_sequence(points):
            raise ValueError("polygon points must be a list")
        parsed_points: list[Point] = []
        for point in points:
            if isinstance(point, Mapping):
                parsed_points.append(Point(float(point["x"]), float(point["y"])))
            elif _is_sequence(point) and len(point) == 2:
                parsed_points.append(Point(float(point[0]), float(point[1])))
            else:
                raise ValueError("each polygon point must contain x and y")
        return Polygon(tuple(parsed_points))

    if all(key in value for key in ("left", "top", "right", "bottom")):
        return Polygon.rectangle(
            float(value["left"]),
            float(value["top"]),
            float(value["right"]),
            float(value["bottom"]),
        )
    if all(key in value for key in ("x1", "y1", "x2", "y2")):
        return Polygon.rectangle(
            float(value["x1"]),
            float(value["y1"]),
            float(value["x2"]),
            float(value["y2"]),
        )
    if all(key in value for key in ("x", "y", "width", "height")):
        left = float(value["x"])
        top = float(value["y"])
        return Polygon.rectangle(
            left,
            top,
            left + float(value["width"]),
            top + float(value["height"]),
        )

    bbox = value.get("bbox", value.get("rect"))
    if isinstance(bbox, Mapping):
        return _parse_polygon(bbox)
    if _is_sequence(bbox):
        bbox_format = str(value.get("bbox_format", value.get("bboxFormat", "xyxy"))).lower()
        return _bbox_polygon(bbox, bbox_format)
    raise ValueError("region is missing polygon or rectangle coordinates")


def _bbox_polygon(value: Sequence[Any], bbox_format: str) -> Polygon:
    if len(value) != 4 or not all(_is_number(item) for item in value):
        raise ValueError("bbox must contain four numeric values")
    first, second, third, fourth = (float(item) for item in value)
    if bbox_format == "xyxy":
        return Polygon.rectangle(first, second, third, fourth)
    if bbox_format == "xywh":
        return Polygon.rectangle(first, second, first + third, second + fourth)
    raise ValueError("bbox_format must be 'xyxy' or 'xywh'")


def _looks_like_geometry(value: Mapping[str, Any]) -> bool:
    geometry_keys = {
        "polygon",
        "points",
        "bbox",
        "rect",
        "left",
        "top",
        "right",
        "bottom",
        "x1",
        "y1",
        "x2",
        "y2",
        "x",
        "y",
        "width",
        "height",
    }
    return any(key in value for key in geometry_keys)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
