from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..recognition.client import DashScopeClient, ModelResponse
from .contracts import (
    CalculationEvidenceImagePair,
    DecisionRecord,
    DecisionStatus,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    ReviewReason,
    ToolObservation,
)
from .dependencies import RubricPoint, propagate_dependencies, validate_rubric
from .normalization import parse_decimal, quantize_score
from .prompts import (
    CALCULATION_JUDGE_PROMPT_VERSION,
    CALCULATION_JUDGE_SYSTEM_PROMPT,
    RUBRIC_SYSTEM_PROMPT,
    calculation_judge_user_content,
    rubric_user_content,
)

FINAL_ANSWER_POINT_KEY = "FINAL_ANSWER"
FINAL_ANSWER_WEIGHT = Decimal("0.20")
CALCULATION_SCORING_POLICY_VERSION = "evidence-aware-alternative-methods-v3"
_SCORE_UNIT = Decimal("0.01")
MAX_MODEL_REASON_LENGTH = 300


def calculation_scoring_policy_payload() -> dict[str, object]:
    """Return the explicit policy supplied to every calculation judge call."""

    return {
        "omittedSteps": {
            "downstreamCorrectUseIsEvidence": True,
            "nonCriticalWithClearEvidence": "satisfied",
            "criticalWithCompleteEvidence": "satisfied",
            "criticalWithPartialEvidence": "partial",
            "noEvidenceOnCompleteImage": "failed",
            "uncertainDueToImageOrHandwriting": "unable",
            "explicitError": "failed",
        },
        "alternativeMethods": {
            "standardSolutionIsExclusive": False,
            "mapEquivalentRolesToRubricPoints": True,
            "differentMethodAloneRequiresReview": False,
            "unmappablePossiblyCorrectMethodRequiresReview": True,
        },
        "deductions": {
            "deductSameIssueOnce": True,
            "preserveLaterIndependentEvidence": True,
        },
    }


def final_answer_point_score(max_score: Decimal | str | int) -> Decimal:
    """Return the auditable, currency-style 20% final-answer allocation."""

    total = quantize_score(max_score)
    score = quantize_score(total * FINAL_ANSWER_WEIGHT)
    if score <= 0 or score >= total:
        raise ValueError("max_score is too small to split into process and final-answer credit")
    return score


def partial_credit_score(point_score: Decimal | str | int) -> Decimal:
    """Return 50% credit rounded half-up to the persisted score precision."""

    return quantize_score(parse_decimal(point_score) / Decimal(2))


def _allocate_process_scores(
    points: list[RubricPoint],
    target_score: Decimal,
) -> list[Decimal]:
    """Scale model-proposed process weights to exact positive cent values."""

    if not points:
        raise ValueError("calculation rubric must contain at least one process point")
    target_units = int(target_score / _SCORE_UNIT)
    if target_units < len(points):
        raise ValueError("max_score is too small for the proposed rubric point count")

    remaining_units = target_units - len(points)
    weight_total = sum((point.score for point in points), Decimal(0))
    exact_extras = [Decimal(remaining_units) * point.score / weight_total for point in points]
    extra_units = [
        int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact_extras
    ]
    unassigned = remaining_units - sum(extra_units)
    priority = sorted(
        range(len(points)),
        key=lambda index: (exact_extras[index] - extra_units[index], -index),
        reverse=True,
    )
    for index in priority[:unassigned]:
        extra_units[index] += 1
    return [Decimal(1 + extra) * _SCORE_UNIT for extra in extra_units]


def normalize_calculation_rubric(
    points: list[RubricPoint],
    max_score: Decimal | str | int,
) -> list[RubricPoint]:
    """Guarantee an independent 20% final-answer point in generated drafts.

    The model chooses the relative process-point weights.  This function reserves
    the final-answer share and deterministically rescales those weights so model
    rounding cannot violate the frozen rubric total.
    """

    total = quantize_score(max_score)
    final_score = final_answer_point_score(total)
    ordered = sorted(enumerate(points), key=lambda item: (item[1].order, item[0]))
    if len({point.key for _index, point in ordered}) != len(ordered):
        raise ValueError("rubric point keys must be unique")
    process_points = [point for _index, point in ordered if point.key != FINAL_ANSWER_POINT_KEY]
    process_scores = _allocate_process_scores(process_points, total - final_score)
    normalized: list[RubricPoint] = []
    for order, (point, score) in enumerate(zip(process_points, process_scores, strict=True)):
        normalized.append(
            point.model_copy(
                update={
                    "score": score,
                    "order": order,
                    "dependencies": list(
                        dict.fromkeys(
                            dependency
                            for dependency in point.dependencies
                            if dependency != FINAL_ANSWER_POINT_KEY
                        )
                    ),
                }
            )
        )
    normalized.append(
        RubricPoint(
            key=FINAL_ANSWER_POINT_KEY,
            criterion="最终答案正确（接受数值、公式、单位和等价表达）",
            score=final_score,
            order=len(normalized),
            dependencies=[],
        )
    )
    validate_calculation_rubric_policy(normalized, total)
    return normalized


def validate_calculation_rubric_policy(
    points: list[RubricPoint],
    max_score: Decimal | str | int,
) -> None:
    """Validate the scoring policy teachers freeze for calculation questions."""

    raw_total = parse_decimal(max_score)
    total = quantize_score(raw_total)
    if raw_total != total:
        raise ValueError("calculation max_score must use at most two decimal places")
    if any(point.score != quantize_score(point.score) for point in points):
        raise ValueError("calculation rubric scores must use at most two decimal places")
    validate_rubric(points, total)
    final_points = [point for point in points if point.key == FINAL_ANSWER_POINT_KEY]
    if len(final_points) != 1:
        raise ValueError("calculation rubric requires exactly one FINAL_ANSWER point")
    final_point = final_points[0]
    if quantize_score(final_point.score) != final_answer_point_score(total):
        raise ValueError("FINAL_ANSWER score must be 20% of max_score")
    if final_point.dependencies:
        raise ValueError("FINAL_ANSWER must not depend on process points")
    if len(points) < 2:
        raise ValueError("calculation rubric must reserve process credit")
    if any(
        FINAL_ANSWER_POINT_KEY in point.dependencies
        for point in points
        if point.key != FINAL_ANSWER_POINT_KEY
    ):
        raise ValueError("process points must not depend on FINAL_ANSWER")
    order_by_key = {point.key: point.order for point in points}
    if any(
        order_by_key[dependency] >= point.order
        for point in points
        for dependency in point.dependencies
    ):
        raise ValueError("rubric dependencies must reference earlier sort orders")


class RubricDraftPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pointKey: str = Field(min_length=1, max_length=64)
    criterion: str = Field(min_length=1)
    score: Decimal = Field(gt=0)
    sortOrder: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)


class RubricDraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[RubricDraftPoint] = Field(min_length=1)


async def generate_rubric_draft(
    client: DashScopeClient,
    *,
    question: str,
    standard_answer: str,
    explanation: str,
    max_score: Decimal,
) -> tuple[list[RubricPoint], ModelResponse]:
    response = await client.chat(
        system_prompt=RUBRIC_SYSTEM_PROMPT,
        user_content=rubric_user_content(
            question=question,
            standard_answer=standard_answer,
            explanation=explanation,
            max_score=str(max_score),
        ),
    )
    parsed = RubricDraftOutput.model_validate_json(response.content)
    proposed_points = [
        RubricPoint(
            key=item.pointKey,
            criterion=item.criterion,
            score=item.score,
            order=item.sortOrder,
            dependencies=item.dependencies,
        )
        for item in parsed.points
    ]
    points = normalize_calculation_rubric(proposed_points, max_score)
    return points, response


class CalculationPointOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pointKey: str = Field(min_length=1)
    status: Literal["satisfied", "partial", "failed", "unable"]
    reason: str = Field(min_length=1)
    evidenceRegionIds: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """Keep verbose model explanations from invalidating the whole question."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be empty")
        if len(normalized) <= MAX_MODEL_REASON_LENGTH:
            return normalized
        return normalized[: MAX_MODEL_REASON_LENGTH - 1].rstrip() + "…"


class CalculationJudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[CalculationPointOutput] = Field(min_length=1)
    uncoveredMethod: bool = False


class CalculationModelOutputError(ValueError):
    """Carry a rejected model response into the persisted review diagnostics."""

    def __init__(
        self,
        *,
        raw_model_content: str,
        validation_errors: list[dict[str, object]],
        usage: dict[str, int],
    ) -> None:
        super().__init__("calculation model output failed schema validation")
        self.raw_model_content = raw_model_content
        self.validation_errors = validation_errors
        self.usage = usage


def _validation_error_details(error: ValidationError) -> list[dict[str, object]]:
    return [
        {
            "type": str(item.get("type", "validation_error")),
            "path": ".".join(str(part) for part in item.get("loc", ())),
            "message": str(item.get("msg", "invalid model output")),
        }
        for item in error.errors(include_url=False)
    ]


def _rubric_from_input(grading_input: QuestionGradingInput) -> list[RubricPoint]:
    return [
        RubricPoint.model_validate(item)
        for item in grading_input.grading_config.get("rubricPoints", [])
    ]


def calculation_evidence_incomplete_result(
    grading_input: QuestionGradingInput,
    *,
    detail: str = "paired calculation evidence is incomplete",
) -> QuestionGradingResult:
    """Return a review-only placeholder without asserting any point is wrong."""

    rubric = _rubric_from_input(grading_input)
    validate_rubric(rubric, grading_input.max_score)
    decisions = [
        DecisionRecord(
            key=point.key,
            status=DecisionStatus.UNABLE,
            score=Decimal(0),
            max_score=quantize_score(point.score),
            reason="证据图像配对不完整，无法可靠判断该评分点",
        )
        for point in rubric
    ]
    return QuestionGradingResult(
        status=GradingStatus.NEEDS_REVIEW,
        # Zero is a schema-compatible placeholder only.  NEEDS_REVIEW and the
        # unable decisions prevent it from becoming a deterministic wrong score.
        raw_score=Decimal(0),
        final_score=Decimal(0),
        max_score=quantize_score(grading_input.max_score),
        decisions=decisions,
        evidence_refs=grading_input.evidence_regions,
        error_locations=[],
        tool_observations=[
            ToolObservation(
                tool="calculation_evidence_gate",
                status="blocked",
                detail=detail,
                payload={
                    "recognitionEvidenceComplete": (
                        grading_input.recognition_evidence_complete
                    ),
                    "evidenceRegionIds": [
                        item.region_id for item in grading_input.evidence_regions
                    ],
                },
                tool_version="paired-evidence-v1",
            )
        ],
        review_reasons=[ReviewReason.MISSING_EVIDENCE],
    )


def _complete_pair_map(
    grading_input: QuestionGradingInput,
    evidence_images: list[CalculationEvidenceImagePair] | None,
) -> dict[str, CalculationEvidenceImagePair] | None:
    if not grading_input.recognition_evidence_complete or not grading_input.evidence_regions:
        return None
    if evidence_images is None:
        return None
    pair_map = {item.region_id: item for item in evidence_images}
    evidence_map = {item.region_id: item for item in grading_input.evidence_regions}
    if len(pair_map) != len(evidence_images) or len(evidence_map) != len(
        grading_input.evidence_regions
    ):
        return None
    if set(pair_map) != set(evidence_map):
        return None
    for region_id, evidence in evidence_map.items():
        pair = pair_map[region_id]
        if not pair.template_image or not pair.student_image:
            return None
        if evidence.evidence_kind is not None and pair.evidence_kind != evidence.evidence_kind:
            return None
    return pair_map


async def grade_calculation_question(
    grading_input: QuestionGradingInput,
    client: DashScopeClient,
    *,
    confidence_threshold: float = 0.95,
    evidence_images: list[CalculationEvidenceImagePair] | None = None,
) -> QuestionGradingResult:
    if not grading_input.rubric_version_id:
        raise ValueError("calculation question requires a frozen rubric version")
    rubric = _rubric_from_input(grading_input)
    order = validate_rubric(rubric, grading_input.max_score)
    evidence_map = {item.region_id: item for item in grading_input.evidence_regions}
    pair_map = _complete_pair_map(grading_input, evidence_images)
    if pair_map is None:
        return calculation_evidence_incomplete_result(grading_input)
    response = await client.chat(
        system_prompt=CALCULATION_JUDGE_SYSTEM_PROMPT,
        user_content=calculation_judge_user_content(
            {
                "question": grading_input.question_content,
                "standardAnswer": grading_input.standard_answer_snapshot.get("answer", ""),
                "standardExplanation": grading_input.standard_answer_snapshot.get(
                    "explanation", ""
                ),
                "studentResponse": grading_input.student_response,
                "availableEvidence": [
                    {
                        "regionId": item.region_id,
                        "recognizedText": item.recognized_text,
                        "evidenceKind": pair_map[item.region_id].evidence_kind,
                        "isBlank": (
                            pair_map[item.region_id].evidence_kind
                            == "blank_search_window"
                        ),
                    }
                    for item in grading_input.evidence_regions
                ],
                "rubricVersionId": grading_input.rubric_version_id,
                "scoringPolicyVersion": CALCULATION_SCORING_POLICY_VERSION,
                "scoringPolicy": calculation_scoring_policy_payload(),
                "rubricPoints": [item.model_dump(mode="json") for item in rubric],
            },
            [pair_map[item.region_id] for item in grading_input.evidence_regions],
        ),
    )
    try:
        parsed = CalculationJudgeOutput.model_validate_json(response.content)
    except ValidationError as error:
        raise CalculationModelOutputError(
            raw_model_content=response.content,
            validation_errors=_validation_error_details(error),
            usage=response.usage,
        ) from error
    outputs = {item.pointKey: item for item in parsed.points}
    if len(outputs) != len(parsed.points) or set(outputs) != set(order):
        raise ValueError("model must return every frozen rubric point exactly once")

    direct: list[DecisionRecord] = []
    review_reasons: list[ReviewReason] = []
    for point in rubric:
        output = outputs[point.key]
        unknown = set(output.evidenceRegionIds) - set(evidence_map)
        if unknown:
            raise ValueError("model referenced evidence outside the current response")
        evidence = [evidence_map[key] for key in output.evidenceRegionIds]
        if output.status == "satisfied":
            status = DecisionStatus.SATISFIED
            score = point.score
            if not evidence:
                review_reasons.append(ReviewReason.MISSING_EVIDENCE)
        elif output.status == "partial":
            status = DecisionStatus.PARTIAL
            score = partial_credit_score(point.score)
            if not evidence:
                review_reasons.append(ReviewReason.MISSING_EVIDENCE)
        elif output.status == "failed":
            status = DecisionStatus.FAILED
            score = Decimal(0)
        else:
            status = DecisionStatus.UNABLE
            score = Decimal(0)
            review_reasons.append(ReviewReason.MODEL_UNABLE_TO_JUDGE)
        if output.confidence < confidence_threshold:
            review_reasons.append(ReviewReason.LOW_RECOGNITION_CONFIDENCE)
        direct.append(
            DecisionRecord(
                key=point.key,
                status=status,
                score=quantize_score(score),
                max_score=quantize_score(point.score),
                reason=output.reason,
                evidence_refs=evidence,
            )
        )
    if parsed.uncoveredMethod:
        review_reasons.append(ReviewReason.RUBRIC_UNCOVERED_METHOD)
    final = propagate_dependencies(rubric, direct)
    raw_score = sum((item.score for item in final), Decimal(0))
    failed = next(
        (
            item
            for item in final
            if item.status in {DecisionStatus.FAILED, DecisionStatus.PARTIAL}
            and item.evidence_refs
        ),
        None,
    )
    unique_reasons = list(dict.fromkeys(review_reasons))
    return QuestionGradingResult(
        status=GradingStatus.NEEDS_REVIEW if unique_reasons else GradingStatus.GRADED,
        raw_score=raw_score,
        final_score=quantize_score(raw_score),
        max_score=quantize_score(grading_input.max_score),
        decisions=final,
        evidence_refs=grading_input.evidence_regions,
        error_locations=failed.evidence_refs[:1] if failed else [],
        tool_observations=[
            ToolObservation(
                tool="calculation_point_model",
                status="judged",
                payload={
                    "usage": response.usage,
                    "promptVersion": CALCULATION_JUDGE_PROMPT_VERSION,
                    "scoringPolicyVersion": CALCULATION_SCORING_POLICY_VERSION,
                    "rubricVersionId": grading_input.rubric_version_id,
                    "directPoints": [item.model_dump(mode="json") for item in parsed.points],
                },
                tool_version=client.settings.dashscope_model,
            )
        ],
        review_reasons=unique_reasons,
    )
