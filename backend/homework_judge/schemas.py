from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

QuestionType = Literal[
    "single_choice",
    "multiple_choice",
    "fill_blank",
    "calculation",
    "short_answer",
    "unknown",
]

CoordinateSpace = Literal["pixel", "normalized"]
AlignmentStatus = Literal["pending", "aligned", "low_quality", "failed"]
StudentResponseStatus = Literal["pending", "recognized", "needs_review", "failed"]
QuestionRegionStatus = Literal["pending", "processing", "ready", "needs_review", "failed"]
GradingQuestionType = Literal["single_choice", "multiple_choice", "fill_blank", "calculation"]
BlankAnswerKind = Literal["text", "numeric", "formula"]
QuestionFrameSetStatus = Literal["draft", "confirmed", "superseded"]
QuestionFrameItemStatus = Literal["pending", "confirmed"]
QuestionFrameSource = Literal["model", "teacher", "legacy"]
BlankConfigVersionStatus = Literal[
    "pending", "auto_confirmed", "teacher_confirmed", "stale"
]
ProcessingRevisionStatus = Literal[
    "aligning",
    "mapping_needs_review",
    "recognizing",
    "recognition_needs_review",
    "ready",
    "failed",
]
IssueLayer = Literal[
    "question_frame", "blank_config", "alignment", "recognition", "grading"
]
HomographyMatrix = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


class Option(BaseModel):
    label: str = ""
    text: str = ""


class QuestionOverride(BaseModel):
    number: str
    stem: str
    options: list[Option] = Field(default_factory=list)
    type: QuestionType
    score: float | None = None


class MatchUpdate(BaseModel):
    answerEntryId: str | None = None
    answer: str | None = None
    explanation: str | None = None


class BoundingBox(BaseModel):
    """Axis-aligned box in the coordinate space declared by its owning region."""

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class TemplateAnswerRegion(BaseModel):
    """One page-relative answer area detected on the blank exam template."""

    pageNumber: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fits_page(self) -> TemplateAnswerRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("template answer region must stay within page bounds")
        return self


class TemplateQuestionRegion(TemplateAnswerRegion):
    """Full question block on a blank template: stem, options, figures and answer space."""

    confidence: float = Field(default=1, ge=0, le=1)
    issues: list[str] = Field(default_factory=list)


class QuestionFrameFragment(BaseModel):
    """One normalized, page-owned fragment in a task-level question-frame set."""

    regionKey: str = Field(min_length=1, max_length=128)
    templatePageId: str = Field(min_length=1)
    pageNumber: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    sortOrder: int = Field(ge=0)
    source: QuestionFrameSource = "model"
    confidence: float | None = Field(default=None, ge=0, le=1)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fits_template_page(self) -> QuestionFrameFragment:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("question frame fragment must stay within page bounds")
        return self


class QuestionFrameItem(BaseModel):
    questionId: str = Field(min_length=1)
    status: QuestionFrameItemStatus = "pending"
    revision: int = Field(ge=0)
    fragments: list[QuestionFrameFragment] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    carriedFromItemId: str | None = None
    confirmedAt: str | None = None
    confirmedBy: str | None = None


class QuestionFrameSet(BaseModel):
    id: str = Field(min_length=1)
    taskId: str = Field(min_length=1)
    versionNumber: int = Field(gt=0)
    status: QuestionFrameSetStatus
    revision: int = Field(ge=0)
    baseFrameSetId: str | None = None
    source: QuestionFrameSource
    contentHash: str = Field(min_length=1)
    items: list[QuestionFrameItem] = Field(default_factory=list)
    createdAt: str
    createdBy: str
    updatedAt: str
    confirmedAt: str | None = None
    confirmedBy: str | None = None


class QuestionFrameItemUpdate(BaseModel):
    expectedRevision: int = Field(ge=0)
    regions: list[QuestionFrameFragment] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_regions(self) -> QuestionFrameItemUpdate:
        keys = [item.regionKey for item in self.regions]
        orders = [item.sortOrder for item in self.regions]
        if len(set(keys)) != len(keys):
            raise ValueError("question frame region keys must be unique")
        if len(set(orders)) != len(orders):
            raise ValueError("question frame sort orders must be unique")
        return self


class SingleQuestionRerecognitionRequest(QuestionFrameItemUpdate):
    """Save the supplied frame draft, then recognize exactly this question."""


class ExpectedRevisionRequest(BaseModel):
    expectedRevision: int = Field(ge=0)


class LayeredIssue(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    layer: IssueLayer
    questionId: str | None = None
    regionKey: str | None = None
    nextAction: str | None = None


class StudentProcessingGate(BaseModel):
    ready: bool
    frameSetId: str | None = None
    frameSetVersion: int | None = Field(default=None, gt=0)
    missingQuestionIds: list[str] = Field(default_factory=list)
    unconfirmedQuestionIds: list[str] = Field(default_factory=list)
    issues: list[LayeredIssue] = Field(default_factory=list)


class BlankAnchor(BaseModel):
    templatePageId: str = Field(min_length=1)
    pageNumber: int = Field(ge=1)
    coordinateSpace: Literal["template_page_normalized"] = "template_page_normalized"
    box: BoundingBox
    source: Literal["model", "teacher", "legacy"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalized_box_fits_page(self) -> BlankAnchor:
        if self.box.x + self.box.width > 1 or self.box.y + self.box.height > 1:
            raise ValueError("blank anchor must stay within template page bounds")
        return self


class BlankConfigReadiness(BaseModel):
    status: BlankConfigVersionStatus
    frameSetId: str
    stemBlankCount: int = Field(ge=0)
    anchorCount: int = Field(ge=0)
    standardAnswerCount: int = Field(ge=0)
    expectedKeys: list[str] = Field(default_factory=list)
    blockingIssues: list[LayeredIssue] = Field(default_factory=list)
    advisoryIssues: list[LayeredIssue] = Field(default_factory=list)


class ControlPointPair(BaseModel):
    template: PixelPoint
    student: PixelPoint


class AlignmentOverrideUpdate(BaseModel):
    expectedAlignmentRevision: int = Field(ge=0)
    templatePageId: str | None = Field(default=None, min_length=1)
    controlPoints: list[ControlPointPair] = Field(default_factory=list)
    clearOverride: bool = False

    @model_validator(mode="after")
    def validate_override_action(self) -> AlignmentOverrideUpdate:
        if self.clearOverride:
            if self.templatePageId is not None or self.controlPoints:
                raise ValueError("clearOverride cannot include templatePageId or controlPoints")
            return self
        if self.templatePageId is None or len(self.controlPoints) < 4:
            raise ValueError("alignment override requires a template page and four control pairs")
        return self


class StudentBlankResponseValue(BaseModel):
    blankKey: str = Field(pattern=r"^B[1-9][0-9]*$")
    recognizedText: str = ""
    isBlank: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["recognized", "needs_review"]
    issues: list[str] = Field(default_factory=list)
    evidenceRefs: list[str] = Field(default_factory=list)
    frameSetId: str = Field(min_length=1)
    blankConfigVersionId: str = Field(min_length=1)
    processingRevisionId: str = Field(min_length=1)

    @model_validator(mode="after")
    def blank_text_is_consistent(self) -> StudentBlankResponseValue:
        if self.isBlank and self.recognizedText.strip():
            raise ValueError("blank response cannot contain recognized text")
        return self


class PixelPoint(BaseModel):
    x: float
    y: float


class StudentPageAlignment(BaseModel):
    """Mapping from an original student page into one template page."""

    templatePageId: str = Field(min_length=1)
    transform: HomographyMatrix
    quality: float = Field(ge=0, le=1)
    method: str = ""
    status: AlignmentStatus = "aligned"


class StudentPageCreate(BaseModel):
    pageNumber: int = Field(ge=1)
    originalImagePath: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sha256: str = Field(min_length=1)
    alignment: StudentPageAlignment | None = None


class StudentResponseRegion(BaseModel):
    """Paired template and original-student-page coordinates for one answer area."""

    templatePageId: str = Field(min_length=1)
    studentPageId: str = Field(min_length=1)
    coordinateSpace: CoordinateSpace = "pixel"
    templateBox: BoundingBox
    studentBox: BoundingBox
    croppedImagePath: str | None = None

    @model_validator(mode="after")
    def normalized_boxes_fit_page(self) -> StudentResponseRegion:
        if self.coordinateSpace != "normalized":
            return self
        for name, box in (("templateBox", self.templateBox), ("studentBox", self.studentBox)):
            if box.x + box.width > 1 or box.y + box.height > 1:
                raise ValueError(f"{name} must stay within normalized page bounds")
        return self


class StudentResponseCreate(BaseModel):
    questionId: str | None = None
    questionNumber: str = Field(min_length=1)
    recognizedText: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    recognitionModelId: str | None = None
    status: StudentResponseStatus = "recognized"
    regions: list[StudentResponseRegion] = Field(min_length=1)


class ApiErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class BlankDefinitionInput(BaseModel):
    blankKey: str = Field(min_length=1, max_length=64)
    sortOrder: int = Field(ge=0)
    maxScore: Decimal = Field(gt=0)
    answerKind: BlankAnswerKind = "text"
    standardAnswers: list[str] = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    region: BoundingBox | None = None
    anchor: BlankAnchor | None = None

    @model_validator(mode="after")
    def normalize_answers(self) -> BlankDefinitionInput:
        standards = [item.strip() for item in self.standardAnswers if item.strip()]
        if not standards:
            raise ValueError("at least one non-empty standard answer is required")
        self.standardAnswers = list(dict.fromkeys(standards))
        self.synonyms = list(dict.fromkeys(item.strip() for item in self.synonyms if item.strip()))
        return self


class QuestionGradingConfigUpdate(BaseModel):
    questionType: GradingQuestionType
    maxScore: Decimal = Field(gt=0)
    blanks: list[BlankDefinitionInput] = Field(default_factory=list)
    frameSetId: str | None = None
    expectedConfigVersion: int | None = Field(default=None, ge=0)
    confirm: bool = False

    @model_validator(mode="after")
    def validate_blanks(self) -> QuestionGradingConfigUpdate:
        if self.questionType == "fill_blank":
            if not self.blanks:
                raise ValueError("fill_blank questions require blank definitions")
            if len({item.blankKey for item in self.blanks}) != len(self.blanks):
                raise ValueError("blank keys must be unique")
            if len({item.sortOrder for item in self.blanks}) != len(self.blanks):
                raise ValueError("blank sort orders must be unique")
            if sum((item.maxScore for item in self.blanks), Decimal(0)) != self.maxScore:
                raise ValueError("blank scores must add up to maxScore")
        elif self.blanks:
            raise ValueError("only fill_blank questions may define blanks")
        return self


class RubricPointInput(BaseModel):
    pointKey: str = Field(min_length=1, max_length=64)
    criterion: str = Field(min_length=1)
    score: Decimal = Field(gt=0)
    sortOrder: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)


class RubricVersionUpdate(BaseModel):
    maxScore: Decimal = Field(gt=0)
    points: list[RubricPointInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_points(self) -> RubricVersionUpdate:
        if len({item.pointKey for item in self.points}) != len(self.points):
            raise ValueError("rubric point keys must be unique")
        if len({item.sortOrder for item in self.points}) != len(self.points):
            raise ValueError("rubric point sort orders must be unique")
        return self


class BlankDecisionOverride(BaseModel):
    blankKey: str = Field(min_length=1, max_length=64)
    status: Literal["correct", "incorrect"]


class GradingBlankCorrection(BaseModel):
    """Teacher correction addressed by the immutable blank key and captured versions."""

    teacherReason: str = Field(min_length=1, max_length=1000)
    expectedGradingRevision: int = Field(ge=0)
    frameSetId: str = Field(min_length=1)
    blankConfigVersionId: str = Field(min_length=1)
    processingRevisionId: str = Field(min_length=1)
    recognizedText: str | None = Field(default=None, max_length=4000)
    finalStatus: Literal["correct", "incorrect"] | None = None

    @model_validator(mode="after")
    def validate_single_correction(self) -> GradingBlankCorrection:
        if (self.recognizedText is None) == (self.finalStatus is None):
            raise ValueError("provide exactly one of recognizedText or finalStatus")
        return self


class PointDecisionOverride(BaseModel):
    pointKey: str = Field(min_length=1, max_length=64)
    directStatus: Literal["satisfied", "partial", "failed"]


class GradingReviewResolution(BaseModel):
    action: Literal["confirm", "override"]
    teacherReason: str = Field(min_length=1, max_length=1000)
    recognizedText: str | None = None
    blankDecisions: list[BlankDecisionOverride] = Field(default_factory=list)
    pointDecisions: list[PointDecisionOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_override(self) -> GradingReviewResolution:
        if self.action == "confirm" and (
            self.recognizedText is not None or self.blankDecisions or self.pointDecisions
        ):
            raise ValueError("confirm cannot include grading overrides")
        if self.action == "override" and not (
            self.recognizedText is not None or self.blankDecisions or self.pointDecisions
        ):
            raise ValueError("override requires at least one changed field")
        if len({item.blankKey for item in self.blankDecisions}) != len(self.blankDecisions):
            raise ValueError("blank override keys must be unique")
        if len({item.pointKey for item in self.pointDecisions}) != len(self.pointDecisions):
            raise ValueError("point override keys must be unique")
        return self


class ErrorLocationInput(BaseModel):
    pageId: str = Field(min_length=1)
    regionId: str = Field(min_length=1)
    box: BoundingBox
    recognizedText: str = ""


class ErrorLocationUpdate(BaseModel):
    errorLocations: list[ErrorLocationInput] = Field(max_length=20)
    teacherReason: str = Field(min_length=1, max_length=1000)
