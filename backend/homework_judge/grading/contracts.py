from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"
    CALCULATION = "calculation"


class GradingStatus(StrEnum):
    GRADED = "graded"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class DecisionStatus(StrEnum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    UNABLE = "unable"
    SATISFIED = "satisfied"
    FAILED = "failed"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"


class ReviewReason(StrEnum):
    LOW_RECOGNITION_CONFIDENCE = "LOW_RECOGNITION_CONFIDENCE"
    MODEL_UNABLE_TO_JUDGE = "MODEL_UNABLE_TO_JUDGE"
    MODEL_TOOL_CONFLICT = "MODEL_TOOL_CONFLICT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    UNCERTAIN_ERROR_LOCATION = "UNCERTAIN_ERROR_LOCATION"
    RUBRIC_UNCOVERED_METHOD = "RUBRIC_UNCOVERED_METHOD"
    DEPENDENCY_CONTRADICTION = "DEPENDENCY_CONTRADICTION"
    SCORE_INCONSISTENCY = "SCORE_INCONSISTENCY"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    original_bbox: BoundingBox
    cropped_image_path: str | None = None
    recognized_text: str = ""
    char_or_step_range: tuple[int, int] | None = None
    # Calculation evidence is located in template coordinates and projected
    # through one immutable alignment revision.  These fields are optional so
    # grading snapshots written before calculation localization remain valid.
    template_page_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    template_bbox: BoundingBox | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    alignment_revision_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    evidence_kind: Literal[
        "legacy",
        "located_region",
        "answer_region",
        "blank_search_window",
    ] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_range(self) -> EvidenceRef:
        if self.char_or_step_range is not None:
            start, end = self.char_or_step_range
            if start < 0 or end <= start:
                raise ValueError("char_or_step_range must be an increasing non-negative range")
        return self


class CalculationEvidenceImagePair(BaseModel):
    """Ephemeral paired pixels for one persisted calculation evidence id.

    Image bytes are deliberately excluded from serialization.  They are loaded
    from immutable source pages immediately before the model call and must
    never enter grading hashes, snapshots, or database JSON.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    region_id: str = Field(min_length=1)
    evidence_kind: Literal[
        "legacy",
        "located_region",
        "answer_region",
        "blank_search_window",
    ] = "legacy"
    template_image: bytes = Field(min_length=1, exclude=True)
    student_image: bytes = Field(min_length=1, exclude=True)


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    status: str = Field(min_length=1)
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    tool_version: str = ""


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    status: DecisionStatus
    score: Decimal = Field(ge=0)
    max_score: Decimal = Field(ge=0)
    reason: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    blocked_by: str | None = None

    @model_validator(mode="after")
    def validate_score(self) -> DecisionRecord:
        if self.score > self.max_score:
            raise ValueError("decision score cannot exceed max_score")
        if (
            self.status
            in {
                DecisionStatus.INCORRECT,
                DecisionStatus.FAILED,
                DecisionStatus.BLOCKED_BY_DEPENDENCY,
            }
            and self.score != 0
        ):
            raise ValueError("failed decisions must have zero score")
        if self.status is DecisionStatus.BLOCKED_BY_DEPENDENCY and not self.blocked_by:
            raise ValueError("blocked decisions must reference a blocking point")
        return self


class QuestionGradingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    question_type: QuestionType
    max_score: Decimal = Field(gt=0)
    question_content: str
    standard_answer_snapshot: dict[str, Any]
    student_response: dict[str, Any]
    evidence_regions: list[EvidenceRef] = Field(default_factory=list)
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    grading_config: dict[str, Any] = Field(default_factory=dict)
    rubric_version_id: str | None = None
    frame_set_id: str | None = None
    blank_config_version_id: str | None = None
    processing_revision_id: str | None = None
    # Recognition uncertainty no longer blocks grading. It travels with the
    # immutable input so the resulting question is forced into teacher review.
    recognition_requires_review: bool = False
    recognition_issue_codes: list[str] = Field(default_factory=list)
    # Legacy responses predate localization completeness and therefore retain
    # their previous optimistic default.  The v1 localization path sets this
    # explicitly and the runtime image loader may only downgrade it.
    recognition_evidence_complete: bool = True


class QuestionGradingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GradingStatus
    raw_score: Decimal = Field(ge=0)
    final_score: Decimal = Field(ge=0)
    max_score: Decimal = Field(ge=0)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    error_locations: list[EvidenceRef] = Field(default_factory=list)
    tool_observations: list[ToolObservation] = Field(default_factory=list)
    review_reasons: list[ReviewReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> QuestionGradingResult:
        if self.raw_score > self.max_score or self.final_score > self.max_score:
            raise ValueError("question score cannot exceed max_score")
        if self.status is GradingStatus.NEEDS_REVIEW and not self.review_reasons:
            raise ValueError("needs_review results must include a reason")
        if self.status is GradingStatus.GRADED and self.review_reasons:
            raise ValueError("graded results cannot include open review reasons")
        return self
