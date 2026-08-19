from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image

from .geometry import Bounds, Homography, Polygon


@dataclass(frozen=True, slots=True)
class PageSize:
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")

    def as_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class AlignmentQuality:
    """Observable alignment metrics; callers decide whether low quality needs review."""

    method: str
    score: float
    matched_features: int
    inliers: int
    inlier_ratio: float
    mean_reprojection_error_px: float | None
    template_feature_coverage: float
    student_feature_coverage: float
    visible_template_ratio: float
    is_reliable: bool
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ratios = (
            self.score,
            self.inlier_ratio,
            self.template_feature_coverage,
            self.student_feature_coverage,
            self.visible_template_ratio,
        )
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in ratios):
            raise ValueError("alignment scores and ratios must be between zero and one")
        if self.matched_features < 0 or self.inliers < 0:
            raise ValueError("feature counts must be non-negative")
        if self.inliers > self.matched_features:
            raise ValueError("inlier count cannot exceed matched feature count")
        if self.mean_reprojection_error_px is not None:
            if (
                not math.isfinite(self.mean_reprojection_error_px)
                or self.mean_reprojection_error_px < 0
            ):
                raise ValueError("reprojection error must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "score": self.score,
            "matched_features": self.matched_features,
            "inliers": self.inliers,
            "inlier_ratio": self.inlier_ratio,
            "mean_reprojection_error_px": self.mean_reprojection_error_px,
            "template_feature_coverage": self.template_feature_coverage,
            "student_feature_coverage": self.student_feature_coverage,
            "visible_template_ratio": self.visible_template_ratio,
            "is_reliable": self.is_reliable,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Bidirectional mapping between a blank template and a student original page."""

    template_to_student: Homography
    student_to_template: Homography
    template_size: PageSize
    student_size: PageSize
    quality: AlignmentQuality

    @classmethod
    def create(
        cls,
        template_to_student: Homography,
        template_size: PageSize,
        student_size: PageSize,
        quality: AlignmentQuality,
    ) -> AlignmentResult:
        return cls(
            template_to_student=template_to_student,
            student_to_template=template_to_student.inverse,
            template_size=template_size,
            student_size=student_size,
            quality=quality,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "coordinate_convention": "pixel_edges_origin_top_left",
            "template_size": self.template_size.as_dict(),
            "student_original_size": self.student_size.as_dict(),
            "template_to_student_original": self.template_to_student.as_rows(),
            "student_original_to_template": self.student_to_template.as_rows(),
            "quality": self.quality.as_dict(),
        }


type HomographyMatrix = Sequence[Sequence[float]]


@dataclass(frozen=True, slots=True)
class FramePageAlignment:
    """One immutable page-alignment revision used by frame-set mapping.

    ``template_to_student`` deliberately also accepts raw matrix rows. Persisted
    revisions are read from JSON, and validation belongs inside the batch mapper
    so a malformed or singular matrix becomes a review blocker instead of an
    exception that aborts the processing job.
    """

    template_page_id: str
    template_page_number: int
    student_page_id: str
    alignment_revision_id: str
    template_size: PageSize
    student_size: PageSize
    template_to_student: Homography | HomographyMatrix
    quality: AlignmentQuality

    def __post_init__(self) -> None:
        if not self.template_page_id:
            raise ValueError("template_page_id must not be empty")
        if self.template_page_number <= 0:
            raise ValueError("template_page_number must be positive")
        if not self.student_page_id:
            raise ValueError("student_page_id must not be empty")
        if not self.alignment_revision_id:
            raise ValueError("alignment_revision_id must not be empty")

    @classmethod
    def from_result(
        cls,
        *,
        template_page_id: str,
        template_page_number: int,
        student_page_id: str,
        alignment_revision_id: str,
        result: AlignmentResult,
    ) -> FramePageAlignment:
        return cls(
            template_page_id=template_page_id,
            template_page_number=template_page_number,
            student_page_id=student_page_id,
            alignment_revision_id=alignment_revision_id,
            template_size=result.template_size,
            student_size=result.student_size,
            template_to_student=result.template_to_student,
            quality=result.quality,
        )

    def resolved_transform(self) -> Homography:
        transform = (
            self.template_to_student
            if isinstance(self.template_to_student, Homography)
            else Homography.from_rows(self.template_to_student)
        )
        # Constructing the inverse is an explicit persisted-matrix integrity
        # check, even when the caller supplied an already-created Homography.
        _ = transform.inverse
        return transform


@dataclass(frozen=True, slots=True)
class QuestionFrameRegion:
    """A confirmed frame fragment in template-page normalized coordinates."""

    frame_set_id: str
    question_id: str
    frame_region_id: str
    region_key: str
    template_page_id: str
    page_number: int
    sort_order: int
    template_normalized_bbox: Bounds

    def __post_init__(self) -> None:
        identifiers = (
            self.frame_set_id,
            self.question_id,
            self.frame_region_id,
            self.region_key,
            self.template_page_id,
        )
        if any(not value for value in identifiers):
            raise ValueError("question frame identifiers must not be empty")
        if self.page_number <= 0:
            raise ValueError("question frame page_number must be positive")
        if self.sort_order < 0:
            raise ValueError("question frame sort_order must be non-negative")
        bounds = self.template_normalized_bbox
        if bounds.area <= 0.0:
            raise ValueError("question frame must have positive area")
        if (
            bounds.left < 0.0
            or bounds.top < 0.0
            or bounds.right > 1.0
            or bounds.bottom > 1.0
        ):
            raise ValueError("question frame must stay inside normalized page bounds")

    def template_pixel_polygon(self, page_size: PageSize) -> Polygon:
        bounds = self.template_normalized_bbox
        return Polygon.rectangle(
            bounds.left * page_size.width,
            bounds.top * page_size.height,
            bounds.right * page_size.width,
            bounds.bottom * page_size.height,
        )


@dataclass(frozen=True, slots=True)
class MappedFrameRegion:
    """One confirmed frame fragment mapped to a student original page."""

    frame_set_id: str
    question_id: str
    frame_region_id: str
    region_key: str
    template_page_id: str
    page_number: int
    sort_order: int
    student_page_id: str
    alignment_revision_id: str
    template_normalized_bbox: Bounds
    template_page_polygon: Polygon
    original_page_polygon: Polygon
    original_page_bbox: Bounds
    visible_original_page_polygon: Polygon | None
    visible_original_page_bbox: Bounds | None
    mapped_area_px: float
    visible_area_px: float
    visible_ratio: float
    out_of_bounds_ratio: float
    max_cross_question_overlap_ratio: float = 0.0
    issues: tuple[str, ...] = ()

    @property
    def status(self) -> Literal["ready", "needs_review"]:
        return "needs_review" if self.issues else "ready"

    def as_dict(self) -> dict[str, Any]:
        normalized = self.template_normalized_bbox
        return {
            "frameSetId": self.frame_set_id,
            "questionId": self.question_id,
            "frameRegionId": self.frame_region_id,
            "regionKey": self.region_key,
            "templatePageId": self.template_page_id,
            "pageNumber": self.page_number,
            "sortOrder": self.sort_order,
            "studentPageId": self.student_page_id,
            "alignmentRevisionId": self.alignment_revision_id,
            "coordinateSpace": "student_original_page_pixels",
            "templateRegion": {
                "coordinateSpace": "template_page_normalized",
                "x": normalized.left,
                "y": normalized.top,
                "width": normalized.width,
                "height": normalized.height,
            },
            "templatePagePolygon": self.template_page_polygon.as_dicts(),
            "templatePageBbox": self.template_page_polygon.bounds.as_dict(),
            "originalPagePolygon": self.original_page_polygon.as_dicts(),
            "originalPageBbox": self.original_page_bbox.as_dict(),
            "visibleOriginalPagePolygon": (
                self.visible_original_page_polygon.as_dicts()
                if self.visible_original_page_polygon is not None
                else None
            ),
            "visibleOriginalPageBbox": (
                self.visible_original_page_bbox.as_dict()
                if self.visible_original_page_bbox is not None
                else None
            ),
            "quality": {
                "mappedAreaPx": self.mapped_area_px,
                "visibleAreaPx": self.visible_area_px,
                "visibleRatio": self.visible_ratio,
                "outOfBoundsRatio": self.out_of_bounds_ratio,
                "maxCrossQuestionOverlapRatio": self.max_cross_question_overlap_ratio,
            },
            "status": self.status,
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class MappingBlocker:
    """A stable, structured reason that answer recognition must not start."""

    code: str
    message: str
    question_id: str | None = None
    frame_region_id: str | None = None
    template_page_id: str | None = None
    student_page_id: str | None = None
    next_action: str = "correct_page_alignment"
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "layer": "mapping",
            "nextAction": self.next_action,
            "details": dict(self.details),
        }
        if self.question_id is not None:
            value["questionId"] = self.question_id
        if self.frame_region_id is not None:
            value["frameRegionId"] = self.frame_region_id
        if self.template_page_id is not None:
            value["templatePageId"] = self.template_page_id
        if self.student_page_id is not None:
            value["studentPageId"] = self.student_page_id
        return value


@dataclass(frozen=True, slots=True)
class FrameSetMappingResult:
    frame_set_id: str | None
    status: Literal["ready", "mapping_needs_review"]
    mappings: tuple[MappedFrameRegion, ...]
    blockers: tuple[MappingBlocker, ...]

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "frameSetId": self.frame_set_id,
            "status": self.status,
            "ready": self.ready,
            "mappings": [mapping.as_dict() for mapping in self.mappings],
            "blockers": [blocker.as_dict() for blocker in self.blockers],
        }


@dataclass(frozen=True, slots=True)
class AnswerRegion:
    """One answer area defined in blank-template coordinates."""

    question_id: str
    region_id: str
    page_number: int
    template_polygon: Polygon

    def __post_init__(self) -> None:
        if not self.question_id:
            raise ValueError("question_id must not be empty")
        if not self.region_id:
            raise ValueError("region_id must not be empty")
        if self.page_number <= 0:
            raise ValueError("page_number must be positive")
        if self.template_polygon.bounds.area <= 0:
            raise ValueError("answer region must have positive area")

    @classmethod
    def rectangle(
        cls,
        question_id: str,
        region_id: str,
        page_number: int,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> AnswerRegion:
        return cls(
            question_id=question_id,
            region_id=region_id,
            page_number=page_number,
            template_polygon=Polygon.rectangle(left, top, right, bottom),
        )


@dataclass(frozen=True, slots=True)
class MappedAnswerRegion:
    """An answer area retaining both template and student-original coordinates."""

    question_id: str
    region_id: str
    page_number: int
    template_polygon: Polygon
    original_page_polygon: Polygon
    original_page_bbox: Bounds
    visible_original_page_bbox: Bounds

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "region_id": self.region_id,
            "page_number": self.page_number,
            "coordinate_space": "student_original_page_pixels",
            "template_polygon": self.template_polygon.as_dicts(),
            "original_page_polygon": self.original_page_polygon.as_dicts(),
            "original_page_bbox": self.original_page_bbox.as_dict(),
            "visible_original_page_bbox": self.visible_original_page_bbox.as_dict(),
        }


@dataclass(slots=True)
class ExtractedAnswerRegion:
    """A rectified recognition crop accompanied by its original-page location."""

    image: Image.Image
    mapping: MappedAnswerRegion
    template_crop_box: tuple[int, int, int, int]

    def metadata(self) -> dict[str, Any]:
        value = self.mapping.as_dict()
        value["template_crop_box"] = list(self.template_crop_box)
        value["crop_size"] = {"width": self.image.width, "height": self.image.height}
        return value
