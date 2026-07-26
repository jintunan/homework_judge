from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="ignore",
    )


class Subject(StrEnum):
    MIDDLE_SCHOOL_MATH = "middle_school_math"
    HIGH_SCHOOL_PHYSICS = "high_school_physics"


class QuestionType(StrEnum):
    CHOICE = "choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    CALCULATION = "calculation"


class AnswerMode(StrEnum):
    REFERENCE_UPLOAD = "reference_upload"
    AGENT_SEARCH = "agent_search"


class AnswerConfigStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    SEARCHING = "searching"
    GENERATING = "generating"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    FAILED = "failed"


class AnswerVersionStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ModelRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARSE_FAILED = "parse_failed"
    REQUEST_FAILED = "request_failed"


class ScoringPoint(CamelModel):
    description: str = Field(min_length=1, max_length=300)
    score: Decimal = Field(ge=0, le=100)

    @field_serializer("score")
    def serialize_score(self, value: Decimal) -> float:
        return float(value)


class ParseIssue(CamelModel):
    path: list[str | int]
    code: str
    message: str
    severity: Literal["attention", "blocking", "skipped"]
    original_value: Any = None
    normalized_value: Any = None
    requires_correction: bool = False


class NormalizedQuestion(CamelModel):
    question_number: str
    question_text: str
    type: QuestionType
    max_score: Decimal
    standard_answer: str = ""
    scoring_points: list[ScoringPoint] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0, ge=0, le=1)
    needs_attention: bool = False
    requires_correction: bool = False
    issues: list[ParseIssue] = Field(default_factory=list)
    source_index: int

    @field_serializer("max_score")
    def serialize_max_score(self, value: Decimal) -> float:
        return float(value)


class ParsedPaper(CamelModel):
    questions: list[NormalizedQuestion]
    issues: list[ParseIssue] = Field(default_factory=list)
    overall_note: str | None = None
    candidate_shape: Literal["object", "array"] = "object"
    repaired: bool = False


class TaskInput(CamelModel):
    name: str = Field(min_length=2, max_length=80)
    class_name: str = Field(min_length=1, max_length=80)
    paper_name: str = Field(min_length=2, max_length=120)
    subject: Subject = Subject.MIDDLE_SCHOOL_MATH
    answer_mode: AnswerMode = AnswerMode.AGENT_SEARCH


class TaskUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    class_name: str | None = Field(default=None, min_length=1, max_length=80)
    paper_name: str | None = Field(default=None, min_length=2, max_length=120)
    subject: Subject | None = None
    answer_mode: AnswerMode | None = None


class QuestionInput(CamelModel):
    id: str | None = None
    number: str = Field(min_length=1, max_length=30)
    type: QuestionType
    max_score: Decimal = Field(gt=0, le=100)
    standard_answer: str = Field(min_length=1, max_length=8000)
    scoring_points: list[ScoringPoint] = Field(default_factory=list, max_length=30)
    sort_order: int = Field(default=0, ge=0)

    @field_serializer("max_score")
    def serialize_max_score(self, value: Decimal) -> float:
        return float(value)


class QuestionsBatch(CamelModel):
    questions: list[QuestionInput] = Field(min_length=1, max_length=100)


class AnswerDraftUpdate(CamelModel):
    number: str = Field(min_length=1, max_length=30)
    type: QuestionType
    max_score: Decimal = Field(gt=0, le=100)
    standard_answer: str = Field(min_length=1, max_length=8000)
    scoring_points: list[ScoringPoint] = Field(max_length=30)

    @field_serializer("max_score")
    def serialize_max_score(self, value: Decimal) -> float:
        return float(value)


class AnswerDraftAction(CamelModel):
    reason: str = Field(default="", max_length=2000)


class StudentNameUpdate(CamelModel):
    student_name: str = Field(min_length=1, max_length=60)


class ReviewUpdate(CamelModel):
    final_answer: str = Field(default="", max_length=4000)
    final_score: Decimal = Field(ge=0, le=100)
    teacher_comment: str = Field(default="", max_length=2000)
    review_status: Literal["pending", "needs_attention", "reviewed"]

    @field_serializer("final_score")
    def serialize_final_score(self, value: Decimal) -> float:
        return float(value)
