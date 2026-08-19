from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..grading.contracts import BoundingBox


class AnnotationMarkType(StrEnum):
    CHECK = "check"
    ERROR_CIRCLE = "error_circle"
    PARTIAL_SCORE = "partial_score"


class AnnotationMark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mark_type: AnnotationMarkType
    page_id: str = Field(min_length=1)
    question_result_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    box: BoundingBox
    target_box: BoundingBox | None = None
    label: str = ""
    color: str

    @model_validator(mode="after")
    def circle_requires_target(self) -> AnnotationMark:
        if self.mark_type is AnnotationMarkType.ERROR_CIRCLE and self.target_box is None:
            raise ValueError("error circles require a target box")
        return self


def _intersects(first: BoundingBox, second: BoundingBox, padding: float = 0) -> bool:
    return not (
        first.x + first.width + padding <= second.x
        or second.x + second.width + padding <= first.x
        or first.y + first.height + padding <= second.y
        or second.y + second.height + padding <= first.y
    )


def _clamp(box: BoundingBox, width: int, height: int) -> BoundingBox:
    x = min(max(0.0, box.x), max(0.0, width - 1.0))
    y = min(max(0.0, box.y), max(0.0, height - 1.0))
    return BoundingBox(
        x=x,
        y=y,
        width=max(1.0, min(box.width, width - x)),
        height=max(1.0, min(box.height, height - y)),
    )


def _candidate_boxes(anchor: BoundingBox, size: float, gap: float) -> list[BoundingBox]:
    centered_y = anchor.y + (anchor.height - size) / 2
    centered_x = anchor.x + (anchor.width - size) / 2
    return [
        BoundingBox(
            x=anchor.x + anchor.width + gap,
            y=max(0.0, centered_y),
            width=size,
            height=size,
        ),
        BoundingBox(
            x=max(0.0, anchor.x - size - gap),
            y=max(0.0, centered_y),
            width=size,
            height=size,
        ),
        BoundingBox(
            x=max(0.0, centered_x),
            y=max(0.0, anchor.y - size - gap),
            width=size,
            height=size,
        ),
        BoundingBox(
            x=max(0.0, centered_x),
            y=anchor.y + anchor.height + gap,
            width=size,
            height=size,
        ),
    ]


def _overlap_area(first: BoundingBox, second: BoundingBox) -> float:
    width = max(0.0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
    height = max(
        0.0,
        min(first.y + first.height, second.y + second.height) - max(first.y, second.y),
    )
    return width * height


def _badge_box(
    anchor: BoundingBox,
    width: int,
    height: int,
    occupied: list[BoundingBox],
    protected: list[BoundingBox],
) -> BoundingBox:
    size = max(42.0, min(84.0, min(width, height) * 0.055))
    gap = max(8.0, size * 0.15)
    local_candidates: list[BoundingBox] = []
    for scale in (1.0, 0.85, 0.7):
        scaled_size = max(28.0, size * scale)
        for raw in _candidate_boxes(anchor, scaled_size, gap):
            candidate = _clamp(raw, width, height)
            if candidate.width < scaled_size * 0.95 or candidate.height < scaled_size * 0.95:
                continue
            local_candidates.append(candidate)
            if not any(_intersects(candidate, item, 4) for item in [*occupied, *protected]):
                return candidate

    # Extremely dense pages may have no completely clear adjacent position. Keep
    # the mark local and choose the candidate with the least overlap instead of
    # moving it to a remote page margin and drawing a leader line.
    if local_candidates:
        return min(
            local_candidates,
            key=lambda candidate: sum(
                _overlap_area(candidate, item) for item in [*occupied, *protected]
            ),
        )
    return _clamp(
        BoundingBox(
            x=anchor.x + anchor.width - min(size, anchor.width),
            y=anchor.y,
            width=min(size, max(28.0, anchor.width)),
            height=min(size, max(28.0, anchor.height)),
        ),
        width,
        height,
    )


def build_question_marks(
    *,
    question_result_id: str,
    question_id: str,
    status: str,
    final_score: float,
    max_score: float,
    evidence: list[dict[str, object]],
    error_locations: list[dict[str, object]],
    page_sizes: dict[str, tuple[int, int]],
    occupied: dict[str, list[BoundingBox]],
) -> list[AnnotationMark]:
    if status == "needs_review" or not evidence:
        return []
    anchor_ref = evidence[0]
    page_id = str(anchor_ref["page_id"])
    if page_id not in page_sizes:
        return []
    page_width, page_height = page_sizes[page_id]
    anchor = BoundingBox.model_validate(anchor_ref["original_bbox"])
    page_occupied = occupied.setdefault(page_id, [])
    protected = [
        BoundingBox.model_validate(item["original_bbox"])
        for item in evidence
        if item.get("page_id") == page_id and item.get("original_bbox")
    ]
    marks: list[AnnotationMark] = []
    if final_score >= max_score:
        box = _badge_box(anchor, page_width, page_height, page_occupied, protected)
        marks.append(
            AnnotationMark(
                mark_type=AnnotationMarkType.CHECK,
                page_id=page_id,
                question_result_id=question_result_id,
                question_id=question_id,
                box=box,
                label="正确",
                color="#10B981",
            )
        )
        page_occupied.append(box)
        return marks

    if not error_locations:
        return []
    error_ref = error_locations[0]
    error_page_id = str(error_ref["page_id"])
    if error_page_id not in page_sizes:
        return []
    error_width, error_height = page_sizes[error_page_id]
    target = BoundingBox.model_validate(error_ref["original_bbox"])
    pad = max(6.0, min(target.width, target.height) * 0.08)
    circle = _clamp(
        BoundingBox(
            x=max(0.0, target.x - pad),
            y=max(0.0, target.y - pad),
            width=target.width + pad * 2,
            height=target.height + pad * 2,
        ),
        error_width,
        error_height,
    )
    marks.append(
        AnnotationMark(
            mark_type=AnnotationMarkType.ERROR_CIRCLE,
            page_id=error_page_id,
            question_result_id=question_result_id,
            question_id=question_id,
            box=circle,
            target_box=target,
            label="错误位置",
            color="#DC2626",
        )
    )
    occupied.setdefault(error_page_id, []).append(circle)
    if final_score > 0:
        box = _badge_box(
            anchor,
            page_width,
            page_height,
            page_occupied,
            protected,
        )
        marks.append(
            AnnotationMark(
                mark_type=AnnotationMarkType.PARTIAL_SCORE,
                page_id=page_id,
                question_result_id=question_result_id,
                question_id=question_id,
                box=box,
                label=f"{final_score:.2f}/{max_score:.2f}",
                color="#F59E0B",
            )
        )
        page_occupied.append(box)
    return marks
