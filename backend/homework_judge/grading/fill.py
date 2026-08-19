from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..recognition.client import DashScopeClient
from .contracts import (
    DecisionRecord,
    DecisionStatus,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    ReviewReason,
    ToolObservation,
)
from .formula import verify_formula_equivalence
from .normalization import matches_exact_or_synonym, quantize_score
from .numeric import VerificationResult, VerificationStatus, verify_numeric_equivalence
from .prompts import (
    FILL_JUDGE_PROMPT_VERSION,
    FILL_JUDGE_SYSTEM_PROMPT,
    fill_judge_user_content,
)


class SemanticDecision(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNABLE = "unable"


class FillSemanticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blankKey: str = Field(pattern=r"^B[1-9][0-9]*$")
    decision: SemanticDecision
    reason: str = Field(min_length=1, max_length=300)
    evidenceRegionIds: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class FillBlankInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blankKey: str = Field(min_length=1)
    maxScore: Decimal = Field(gt=0)
    answerKind: str = Field(pattern="^(text|numeric|formula)$")
    standardAnswers: list[str] = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    studentAnswer: str = ""
    isBlank: bool = False
    recognitionConfidence: float | None = Field(default=None, ge=0, le=1)
    evidenceRegionIds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_answers(self) -> FillBlankInput:
        if not any(answer.strip() for answer in self.standardAnswers):
            raise ValueError("blank requires a non-empty standard answer")
        if self.isBlank and self.studentAnswer.strip():
            raise ValueError("isBlank=true cannot contain a student answer")
        return self


def _evidence_by_id(regions: list[EvidenceRef]) -> dict[str, EvidenceRef]:
    return {region.region_id: region for region in regions}


def resolve_fill_blank_evidence(
    blanks: list[FillBlankInput],
    evidence_regions: list[EvidenceRef],
) -> dict[str, list[EvidenceRef]]:
    evidence_map = _evidence_by_id(evidence_regions)
    return {
        blank.blankKey: [
            evidence_map[region_id]
            for region_id in blank.evidenceRegionIds
            if region_id in evidence_map
        ]
        for blank in blanks
    }


def hydrate_fill_result_evidence(
    result: QuestionGradingResult,
    blanks: list[FillBlankInput],
) -> QuestionGradingResult:
    evidence_by_blank = resolve_fill_blank_evidence(blanks, result.evidence_refs)
    changed = False
    decisions: list[DecisionRecord] = []
    for decision in result.decisions:
        shared = evidence_by_blank.get(decision.key, [])
        if decision.evidence_refs or not shared:
            decisions.append(decision)
            continue
        changed = True
        decisions.append(decision.model_copy(update={"evidence_refs": shared}))
    return result.model_copy(update={"decisions": decisions}) if changed else result


def _verification(blank: FillBlankInput, timeout_ms: int) -> VerificationResult | None:
    standard = blank.standardAnswers[0]
    if blank.answerKind == "numeric":
        return verify_numeric_equivalence(blank.studentAnswer, standard)
    if blank.answerKind == "formula":
        return verify_formula_equivalence(blank.studentAnswer, standard, timeout_ms=timeout_ms)
    return None


async def _judge_semantics(
    client: DashScopeClient,
    grading_input: QuestionGradingInput,
    blank: FillBlankInput,
    verification: VerificationResult | None,
    exact_match: bool,
) -> tuple[FillSemanticOutput, ToolObservation]:
    response = await client.chat(
        system_prompt=FILL_JUDGE_SYSTEM_PROMPT,
        user_content=fill_judge_user_content(
            {
                "question": grading_input.question_content,
                "blankKey": blank.blankKey,
                "answerKind": blank.answerKind,
                "standardAnswers": blank.standardAnswers,
                "teacherSynonyms": blank.synonyms,
                "studentAnswer": blank.studentAnswer,
                    "availableEvidenceRegionIds": blank.evidenceRegionIds,
                    "toolEvidence": {
                        "exactOrTeacherSynonymMatch": exact_match,
                        "specializedVerifier": (
                            {
                                "status": verification.status,
                                "detail": verification.detail,
                                "normalizedStudent": verification.normalized_student,
                                "normalizedStandard": verification.normalized_standard,
                            }
                            if verification
                            else None
                        ),
                    },
            }
        ),
    )
    parsed = FillSemanticOutput.model_validate_json(response.content)
    if parsed.blankKey != blank.blankKey:
        raise ValueError("model must return the same blankKey")
    unknown_evidence = set(parsed.evidenceRegionIds) - set(blank.evidenceRegionIds)
    if unknown_evidence:
        raise ValueError("model referenced evidence outside the current blank")
    return parsed, ToolObservation(
        tool="fill_semantic_model",
        status=parsed.decision,
        detail=parsed.reason,
        payload={
            "blankKey": blank.blankKey,
            "confidence": parsed.confidence,
            "evidenceRegionIds": parsed.evidenceRegionIds,
            "usage": response.usage,
            "promptVersion": FILL_JUDGE_PROMPT_VERSION,
        },
        tool_version=client.settings.dashscope_model,
    )


async def grade_fill_question(
    grading_input: QuestionGradingInput,
    client: DashScopeClient,
    *,
    confidence_threshold: float = 0.95,
    formula_timeout_ms: int = 1500,
) -> QuestionGradingResult:
    blank_inputs = [
        FillBlankInput.model_validate(item)
        for item in grading_input.grading_config.get("blanks", [])
    ]
    if not blank_inputs:
        raise ValueError("fill_blank question requires configured blanks")
    evidence_by_blank = resolve_fill_blank_evidence(blank_inputs, grading_input.evidence_regions)
    decisions: list[DecisionRecord] = []
    observations: list[ToolObservation] = []
    review_reasons: list[ReviewReason] = []
    error_locations: list[EvidenceRef] = []

    for blank in blank_inputs:
        evidence = evidence_by_blank.get(blank.blankKey, [])
        effective_blank = blank.model_copy(
            update={"evidenceRegionIds": [item.region_id for item in evidence]}
        )
        exact = matches_exact_or_synonym(blank.studentAnswer, blank.standardAnswers, blank.synonyms)
        observations.append(
            ToolObservation(
                tool="fill_exact_match",
                status="matched" if exact else "not_matched",
                payload={"blankKey": blank.blankKey},
                tool_version="1",
            )
        )
        if blank.isBlank:
            decisions.append(
                DecisionRecord(
                    key=blank.blankKey,
                    status=DecisionStatus.INCORRECT,
                    score=Decimal(0),
                    max_score=quantize_score(blank.maxScore),
                    reason="逐空识别确认该空未作答",
                    evidence_refs=evidence,
                )
            )
            observations.append(
                ToolObservation(
                    tool="blank_presence",
                    status="blank",
                    payload={"blankKey": blank.blankKey},
                    tool_version="1",
                )
            )
            continue

        verifier = _verification(effective_blank, formula_timeout_ms)
        if verifier:
            observations.append(
                ToolObservation(
                    tool=f"{blank.answerKind}_verifier",
                    status=verifier.status,
                    detail=verifier.detail,
                    payload={"blankKey": blank.blankKey},
                    tool_version="1",
                )
            )
        model, model_observation = await _judge_semantics(
            client, grading_input, effective_blank, verifier, exact
        )
        observations.append(model_observation)

        evidence_map = _evidence_by_id(evidence)
        decision_evidence = [evidence_map[key] for key in model.evidenceRegionIds]

        needs_review = (
            model.decision is SemanticDecision.UNABLE or model.confidence < confidence_threshold
        )
        conflict = exact and model.decision is SemanticDecision.INCORRECT
        if verifier and verifier.status is not VerificationStatus.UNABLE:
            conflict = conflict or (
                verifier.status is VerificationStatus.EQUIVALENT
                and model.decision is SemanticDecision.INCORRECT
            ) or (
                verifier.status is VerificationStatus.NOT_EQUIVALENT
                and model.decision is SemanticDecision.CORRECT
            )
        if conflict:
            review_reasons.append(ReviewReason.MODEL_TOOL_CONFLICT)
            needs_review = True
        if model.decision is SemanticDecision.UNABLE:
            review_reasons.append(ReviewReason.MODEL_UNABLE_TO_JUDGE)
        if model.confidence < confidence_threshold:
            review_reasons.append(ReviewReason.LOW_RECOGNITION_CONFIDENCE)

        if needs_review:
            status = DecisionStatus.UNABLE
            score = Decimal(0)
            reason = model.reason
        elif model.decision is SemanticDecision.CORRECT:
            status = DecisionStatus.CORRECT
            score = blank.maxScore
            reason = model.reason
        else:
            status = DecisionStatus.INCORRECT
            score = Decimal(0)
            reason = model.reason
            if evidence:
                error_locations.append(decision_evidence[0] if decision_evidence else evidence[0])
        decisions.append(
            DecisionRecord(
                key=blank.blankKey,
                status=status,
                score=quantize_score(score),
                max_score=quantize_score(blank.maxScore),
                reason=reason,
                evidence_refs=decision_evidence,
            )
        )

    raw_score = sum((decision.score for decision in decisions), Decimal(0))
    unique_reasons = list(dict.fromkeys(review_reasons))
    return QuestionGradingResult(
        status=GradingStatus.NEEDS_REVIEW if unique_reasons else GradingStatus.GRADED,
        raw_score=raw_score,
        final_score=quantize_score(raw_score),
        max_score=quantize_score(grading_input.max_score),
        decisions=decisions,
        evidence_refs=grading_input.evidence_regions,
        error_locations=error_locations,
        tool_observations=observations,
        review_reasons=unique_reasons,
    )
