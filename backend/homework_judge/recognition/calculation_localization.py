from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import pairwise
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class CalculationLocalizationIssue:
    """A stable, serializable reason why localization needs review."""

    code: str
    path: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class CalculationPageBinding:
    """One reliable template/student page pair captured for this processing run."""

    page_number: int
    student_page_number: int
    template_page_id: str
    student_page_id: str
    alignment_revision_id: str
    is_reliable: bool = True
    alignment_confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class CalculationSearchFragment:
    """A full-width, bounded page fragment visible to the locator model."""

    fragment_key: str
    template_page_id: str
    student_page_id: str
    alignment_revision_id: str
    page_number: int
    x: float
    y: float
    width: float
    height: float
    sort_order: int
    student_page_number: int | None = None
    alignment_confidence: float = 1.0
    template_image: bytes | None = field(default=None, repr=False)
    student_image: bytes | None = field(default=None, repr=False)

    def with_images(
        self,
        *,
        template_image: bytes,
        student_image: bytes,
    ) -> CalculationSearchFragment:
        """Return a runtime copy carrying ephemeral paired crop bytes."""

        return replace(
            self,
            template_image=bytes(template_image),
            student_image=bytes(student_image),
        )

    def snapshot(self) -> dict[str, object]:
        """Return persistence-safe metadata without either image payload."""

        return {
            "fragmentKey": self.fragment_key,
            "templatePageId": self.template_page_id,
            "studentPageId": self.student_page_id,
            "alignmentRevisionId": self.alignment_revision_id,
            "pageNumber": self.page_number,
            "studentPageNumber": self.student_page_number,
            "alignmentConfidence": self.alignment_confidence,
            "coordinateSpace": "template_page_normalized",
            "box": {
                "x": self.x,
                "y": self.y,
                "width": self.width,
                "height": self.height,
            },
            "sortOrder": self.sort_order,
        }


@dataclass(frozen=True, slots=True)
class CalculationSearchPlan:
    frame_set_id: str
    question_id: str
    next_question_id: str | None
    submission_last_page_number: int | None
    fragments: tuple[CalculationSearchFragment, ...]
    issues: tuple[CalculationLocalizationIssue, ...]

    @property
    def evidence_complete(self) -> bool:
        return bool(self.fragments) and not self.issues

    def snapshot(self) -> dict[str, object]:
        return {
            "frameSetId": self.frame_set_id,
            "questionId": self.question_id,
            "nextQuestionId": self.next_question_id,
            "submissionLastPageNumber": self.submission_last_page_number,
            "evidenceComplete": self.evidence_complete,
            "fragments": [fragment.snapshot() for fragment in self.fragments],
            "issues": [issue.as_dict() for issue in self.issues],
        }


class CalculationWindowStatus(StrEnum):
    LOCATED = "located"
    BLANK = "blank"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LocalizedCalculationRegion:
    fragment_key: str
    template_page_id: str
    student_page_id: str
    alignment_revision_id: str
    page_number: int
    student_page_number: int
    model_bbox: tuple[float, float, float, float]
    template_bbox: tuple[float, float, float, float]
    confidence: float
    issues: tuple[str, ...]
    model_candidate_index: int
    batch_index: int
    attempt_id: str

    @property
    def x(self) -> float:
        return self.template_bbox[0]

    @property
    def y(self) -> float:
        return self.template_bbox[1]

    @property
    def width(self) -> float:
        return self.template_bbox[2]

    @property
    def height(self) -> float:
        return self.template_bbox[3]

    def as_dict(self) -> dict[str, object]:
        return {
            "fragmentKey": self.fragment_key,
            "templatePageId": self.template_page_id,
            "studentPageId": self.student_page_id,
            "alignmentRevisionId": self.alignment_revision_id,
            "pageNumber": self.page_number,
            "studentPageNumber": self.student_page_number,
            "modelBBox": list(self.model_bbox),
            "templateBBox": {
                "x": self.x,
                "y": self.y,
                "width": self.width,
                "height": self.height,
            },
            "confidence": self.confidence,
            "issues": list(self.issues),
            "modelCandidateIndex": self.model_candidate_index,
            "batchIndex": self.batch_index,
            "attemptId": self.attempt_id,
        }

    def snapshot(self) -> dict[str, object]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class CalculationWindowResult:
    fragment_key: str
    status: CalculationWindowStatus
    confidence: float
    issues: tuple[str, ...]
    regions: tuple[LocalizedCalculationRegion, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "fragmentKey": self.fragment_key,
            "status": self.status.value,
            "confidence": self.confidence,
            "issues": list(self.issues),
            "regions": [region.as_dict() for region in self.regions],
        }

    def snapshot(self) -> dict[str, object]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class CalculationLocalizationBatchResult:
    batch_index: int
    attempt_id: str
    windows: tuple[CalculationWindowResult, ...]
    regions: tuple[LocalizedCalculationRegion, ...]
    confidence: float
    issues: tuple[CalculationLocalizationIssue, ...]
    evidence_complete: bool
    reliable_blank: bool
    model_id: str
    prompt_version: str

    @property
    def status(self) -> str:
        if self.reliable_blank:
            return "blank"
        if self.evidence_complete and self.regions and not self.issues:
            return "located"
        return "needs_review"

    def as_dict(self) -> dict[str, object]:
        return {
            "batchIndex": self.batch_index,
            "attemptId": self.attempt_id,
            "status": self.status,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "confidence": self.confidence,
            "evidenceComplete": self.evidence_complete,
            "reliableBlank": self.reliable_blank,
            "windows": [window.as_dict() for window in self.windows],
            "regions": [region.as_dict() for region in self.regions],
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def snapshot(self) -> dict[str, object]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class CalculationRegionTranscription:
    fragment_key: str
    model_candidate_index: int
    transcription: str
    confidence: float
    issues: tuple[str, ...]

    @property
    def region_key(self) -> tuple[str, int]:
        return self.fragment_key, self.model_candidate_index

    def as_dict(self) -> dict[str, object]:
        return {
            "fragmentKey": self.fragment_key,
            "modelCandidateIndex": self.model_candidate_index,
            "transcription": self.transcription,
            "confidence": self.confidence,
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class CalculationRecognitionBatchResult:
    localization: CalculationLocalizationBatchResult
    transcriptions: tuple[CalculationRegionTranscription, ...]
    localization_contract_valid: bool
    transcription_contract_valid: bool
    issues: tuple[CalculationLocalizationIssue, ...]

    @property
    def transcription_by_region(self) -> dict[tuple[str, int], CalculationRegionTranscription]:
        return {item.region_key: item for item in self.transcriptions}

    def as_dict(self) -> dict[str, object]:
        return {
            **self.localization.as_dict(),
            "localizationContractValid": self.localization_contract_valid,
            "transcriptionContractValid": self.transcription_contract_valid,
            "transcriptions": [item.as_dict() for item in self.transcriptions],
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class CalculationLocalizationResult:
    windows: tuple[CalculationWindowResult, ...]
    regions: tuple[LocalizedCalculationRegion, ...]
    batches: tuple[CalculationLocalizationBatchResult, ...]
    confidence: float
    issues: tuple[CalculationLocalizationIssue, ...]
    evidence_complete: bool
    reliable_blank: bool

    @property
    def status(self) -> str:
        if self.reliable_blank:
            return "blank"
        if self.evidence_complete and self.regions and not self.issues:
            return "located"
        return "needs_review"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "evidenceComplete": self.evidence_complete,
            "reliableBlank": self.reliable_blank,
            "windows": [window.as_dict() for window in self.windows],
            "regions": [region.as_dict() for region in self.regions],
            "batches": [batch.as_dict() for batch in self.batches],
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def snapshot(self) -> dict[str, object]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class _FrameRegion:
    page_number: int
    template_page_id: str
    x: float
    y: float
    width: float
    height: float
    source_index: int

    @property
    def bottom(self) -> float:
        return self.y + self.height


def build_calculation_search_plan(
    *,
    frame_set_id: str,
    question_id: str,
    questions: Sequence[Mapping[str, object]],
    page_bindings: Sequence[CalculationPageBinding],
    uploaded_student_page_numbers: Sequence[int],
) -> CalculationSearchPlan:
    """Build the half-open window from this teacher frame to the next one.

    The function is deliberately pure. It neither repairs confirmed frames nor
    shortens an unaligned upload tail into an apparently complete plan.
    """

    issues: list[CalculationLocalizationIssue] = []
    ordered = _ordered_questions(questions, issues)
    current_index = next(
        (
            index
            for index, (_, question) in enumerate(ordered)
            if _text(question.get("id")) == question_id
        ),
        None,
    )
    if current_index is None:
        return CalculationSearchPlan(
            frame_set_id,
            question_id,
            None,
            _last_uploaded_page(uploaded_student_page_numbers, issues),
            (),
            tuple(
                [
                    *issues,
                    _plan_issue(
                        "calculation_question_not_found",
                        "$.questionId",
                        "The calculation question is absent from the confirmed question order.",
                    ),
                ]
            ),
        )

    current = ordered[current_index][1]
    if _truthy_duplicate(current.get("is_duplicate", current.get("isDuplicate", False))):
        issues.append(
            _plan_issue(
                "calculation_question_duplicate",
                "$.questions",
                "A duplicate question cannot define an independent calculation window.",
            )
        )
    next_question = ordered[current_index + 1][1] if current_index + 1 < len(ordered) else None
    next_question_id = _text(next_question.get("id")) if next_question is not None else None

    current_regions = _frame_regions(current, "current", issues)
    next_regions = _frame_regions(next_question, "next", issues) if next_question else []
    if not current_regions:
        issues.append(
            _plan_issue(
                "calculation_current_frame_missing",
                "$.questions.current.frameRegions",
                "The current question has no valid confirmed frame anchor.",
            )
        )
    if next_question is not None and not next_regions:
        issues.append(
            _plan_issue(
                "calculation_next_frame_missing",
                "$.questions.next.frameRegions",
                "The next question has no valid confirmed frame boundary.",
            )
        )

    uploaded_last = _last_uploaded_page(uploaded_student_page_numbers, issues)
    bindings_by_template, bindings_by_student = _binding_maps(page_bindings, issues)
    if not current_regions:
        return CalculationSearchPlan(
            frame_set_id,
            question_id,
            next_question_id,
            uploaded_last,
            (),
            tuple(_unique_issues(issues)),
        )

    start = min(current_regions, key=_frame_order)
    end_page: int | None = None
    end_y: float | None = None
    if next_question is not None:
        if next_regions:
            boundary = min(next_regions, key=_frame_order)
            end_page = boundary.page_number
            end_y = boundary.y
    else:
        if uploaded_last is None:
            issues.append(
                _plan_issue(
                    "calculation_submission_pages_missing",
                    "$.uploadedStudentPageNumbers",
                    "The last question cannot extend without uploaded student pages.",
                )
            )
        else:
            tail_binding = bindings_by_student.get(uploaded_last)
            if tail_binding is None:
                issues.append(
                    _plan_issue(
                        "calculation_submission_tail_unaligned",
                        "$.uploadedStudentPageNumbers",
                        "The actual uploaded tail page has no unambiguous template pairing.",
                        {"studentPageNumber": uploaded_last},
                    )
                )
                eligible = [
                    binding
                    for binding in bindings_by_student.values()
                    if binding.student_page_number <= uploaded_last
                ]
                tail_binding = max(
                    eligible,
                    key=lambda item: item.student_page_number,
                    default=None,
                )
            if tail_binding is not None:
                end_page = tail_binding.page_number
                end_y = 1.0

    if end_page is None or end_y is None:
        return CalculationSearchPlan(
            frame_set_id,
            question_id,
            next_question_id,
            uploaded_last,
            (),
            tuple(_unique_issues(issues)),
        )
    if (end_page, end_y) <= (start.page_number, start.y):
        issues.append(
            _plan_issue(
                "calculation_anchor_order_invalid",
                "$.questions",
                "The exclusive next-question boundary must follow the current anchor.",
                {
                    "start": [start.page_number, start.y],
                    "end": [end_page, end_y],
                },
            )
        )
        return CalculationSearchPlan(
            frame_set_id,
            question_id,
            next_question_id,
            uploaded_last,
            (),
            tuple(_unique_issues(issues)),
        )

    _validate_frame_page_bindings(current_regions, bindings_by_template, "current", issues)
    _validate_frame_page_bindings(next_regions, bindings_by_template, "next", issues)
    _validate_upload_coverage(
        start,
        end_page,
        uploaded_student_page_numbers,
        bindings_by_template,
        bindings_by_student,
        issues,
    )
    fragments: list[CalculationSearchFragment] = []
    for page_number in range(start.page_number, end_page + 1):
        top = start.y if page_number == start.page_number else 0.0
        bottom = end_y if page_number == end_page else 1.0
        if bottom <= top:
            continue
        binding = bindings_by_template.get(page_number)
        path = f"$.pageBindings[templatePage={page_number}]"
        if binding is None:
            issues.append(
                _plan_issue(
                    "calculation_page_binding_missing",
                    path,
                    "A page inside the calculation window has no unambiguous pairing.",
                    {"pageNumber": page_number},
                )
            )
            continue
        if not binding.is_reliable:
            issues.append(
                _plan_issue(
                    "calculation_alignment_unreliable",
                    path,
                    "A page inside the calculation window lacks reliable alignment.",
                    {"pageNumber": page_number},
                )
            )
            continue
        if not all(
            (
                binding.template_page_id.strip(),
                binding.student_page_id.strip(),
                binding.alignment_revision_id.strip(),
            )
        ):
            issues.append(
                _plan_issue(
                    "calculation_page_binding_invalid",
                    path,
                    "Page pairing IDs and alignment revision ID must be present.",
                    {"pageNumber": page_number},
                )
            )
            continue
        fragments.append(
            CalculationSearchFragment(
                fragment_key=f"{question_id}:calculation-window:{page_number}",
                template_page_id=binding.template_page_id,
                student_page_id=binding.student_page_id,
                alignment_revision_id=binding.alignment_revision_id,
                page_number=page_number,
                student_page_number=binding.student_page_number,
                x=0.0,
                y=top,
                width=1.0,
                height=bottom - top,
                sort_order=len(fragments),
                alignment_confidence=binding.alignment_confidence,
            )
        )

    _validate_current_frames_inside_window(current_regions, start, end_page, end_y, issues)
    if not fragments:
        issues.append(
            _plan_issue(
                "calculation_search_window_empty",
                "$.fragments",
                "The calculation search window contains no usable page fragment.",
            )
        )
    return CalculationSearchPlan(
        frame_set_id=frame_set_id,
        question_id=question_id,
        next_question_id=next_question_id,
        submission_last_page_number=uploaded_last,
        fragments=tuple(fragments),
        issues=tuple(_unique_issues(issues)),
    )


def normalize_calculation_localization_batch(
    nodes: Sequence[Mapping[str, object]],
    fragments: Sequence[CalculationSearchFragment],
    *,
    batch_index: int,
    attempt_id: str,
    model_id: str,
    prompt_version: str,
    parse_issues: Sequence[Mapping[str, object]] = (),
    min_confidence: float = 0.75,
) -> CalculationLocalizationBatchResult:
    """Validate one exact-key model batch and project valid local boxes."""

    _validate_batch_metadata(batch_index, attempt_id, model_id, prompt_version, min_confidence)
    issues = [_parsed_issue(issue) for issue in parse_issues]
    evidence_complete = not issues
    fragment_by_key: dict[str, CalculationSearchFragment] = {}
    for index, fragment in enumerate(fragments):
        if fragment.fragment_key in fragment_by_key:
            evidence_complete = False
            issues.append(
                _result_issue(
                    "calculation_expected_fragment_duplicate",
                    f"$.fragments[{index}].fragmentKey",
                    "Expected fragment keys must be unique within a batch.",
                    {"fragmentKey": fragment.fragment_key},
                )
            )
            continue
        fragment_by_key[fragment.fragment_key] = fragment

    raw_by_key: dict[str, tuple[int, Mapping[str, object]]] = {}
    invalid_expected_keys: set[str] = set()
    for index, raw_window in enumerate(nodes):
        path = f"$.windows[{index}]"
        keys = set(raw_window)
        expected_window_fields = {"fragmentKey", "status", "confidence", "issues", "regions"}
        fragment_key_value = raw_window.get("fragmentKey")
        fragment_key = fragment_key_value.strip() if isinstance(fragment_key_value, str) else ""
        if keys != expected_window_fields:
            evidence_complete = False
            issues.append(
                _result_issue(
                    "calculation_window_fields_invalid",
                    path,
                    "Window objects must contain exactly the contracted fields.",
                    {
                        "missing": sorted(expected_window_fields - keys),
                        "extra": sorted(keys - expected_window_fields),
                    },
                )
            )
            if fragment_key in fragment_by_key:
                invalid_expected_keys.add(fragment_key)
            continue
        if not fragment_key:
            evidence_complete = False
            issues.append(
                _result_issue(
                    "calculation_fragment_key_invalid",
                    f"{path}.fragmentKey",
                    "fragmentKey must be a non-empty string.",
                )
            )
            continue
        if fragment_key not in fragment_by_key:
            evidence_complete = False
            issues.append(
                _result_issue(
                    "calculation_fragment_key_unknown",
                    f"{path}.fragmentKey",
                    "The model returned a fragment key that was not supplied.",
                    {"fragmentKey": fragment_key},
                )
            )
            continue
        if fragment_key in raw_by_key:
            evidence_complete = False
            invalid_expected_keys.add(fragment_key)
            issues.append(
                _result_issue(
                    "calculation_fragment_key_duplicate",
                    f"{path}.fragmentKey",
                    "Each supplied fragment key must be returned exactly once.",
                    {"fragmentKey": fragment_key},
                )
            )
            continue
        raw_by_key[fragment_key] = (index, raw_window)

    windows: list[CalculationWindowResult] = []
    for fragment in fragments:
        fragment_key = fragment.fragment_key
        raw_entry = raw_by_key.get(fragment_key)
        if raw_entry is None or fragment_key in invalid_expected_keys:
            evidence_complete = False
            if raw_entry is None:
                issues.append(
                    _result_issue(
                        "calculation_fragment_key_missing",
                        "$.windows",
                        "A supplied fragment key is missing from the model response.",
                        {"fragmentKey": fragment_key},
                    )
                )
            windows.append(
                CalculationWindowResult(
                    fragment_key,
                    CalculationWindowStatus.UNCERTAIN,
                    0.0,
                    ("invalid_window_contract",),
                    (),
                )
            )
            continue
        model_window_index, raw_window = raw_entry
        window, window_complete, window_issues = _normalize_window(
            raw_window,
            fragment,
            model_window_index=model_window_index,
            batch_index=batch_index,
            attempt_id=attempt_id,
            min_confidence=min_confidence,
        )
        windows.append(window)
        evidence_complete = evidence_complete and window_complete
        issues.extend(window_issues)

    regions, duplicate_issues = _deduplicate_regions(
        [region for window in windows for region in window.regions]
    )
    issues.extend(duplicate_issues)
    windows = _windows_with_regions(windows, regions)
    ordered_issues = tuple(_unique_issues(issues))
    confidence = _aggregate_confidence(windows, regions)
    reliable_blank = (
        bool(windows)
        and evidence_complete
        and not ordered_issues
        and all(window.status is CalculationWindowStatus.BLANK for window in windows)
        and all(window.confidence >= min_confidence for window in windows)
    )
    return CalculationLocalizationBatchResult(
        batch_index=batch_index,
        attempt_id=attempt_id,
        windows=tuple(windows),
        regions=tuple(regions),
        confidence=confidence,
        issues=ordered_issues,
        evidence_complete=evidence_complete,
        reliable_blank=reliable_blank,
        model_id=model_id,
        prompt_version=prompt_version,
    )


def normalize_calculation_recognition_batch(
    nodes: Sequence[Mapping[str, object]],
    fragments: Sequence[CalculationSearchFragment],
    *,
    batch_index: int,
    attempt_id: str,
    model_id: str,
    prompt_version: str,
    parse_issues: Sequence[Mapping[str, object]] = (),
    min_confidence: float = 0.75,
) -> CalculationRecognitionBatchResult:
    """Validate combined localization/transcription while keeping fallbacks separable."""

    location_nodes: list[dict[str, object]] = []
    location_parse_issues: list[Mapping[str, object]] = list(parse_issues)
    raw_regions_by_key: dict[tuple[str, int], Mapping[str, object]] = {}
    expected_window_fields = {"fragmentKey", "status", "confidence", "issues", "regions"}
    location_region_fields = {"bbox", "confidence", "issues"}
    combined_region_fields = {
        *location_region_fields,
        "transcription",
        "transcriptionConfidence",
        "transcriptionIssues",
    }

    for window_index, raw_window in enumerate(nodes):
        path = f"$.windows[{window_index}]"
        if set(raw_window) != expected_window_fields:
            location_parse_issues.append(
                _result_issue(
                    "calculation_recognition_window_fields_invalid",
                    path,
                    "Combined windows must contain exactly the contracted fields.",
                    {
                        "missing": sorted(expected_window_fields - set(raw_window)),
                        "extra": sorted(set(raw_window) - expected_window_fields),
                    },
                ).as_dict()
            )
        projected: dict[str, object] = {
            key: raw_window[key]
            for key in expected_window_fields - {"regions"}
            if key in raw_window
        }
        raw_regions = raw_window.get("regions")
        if _is_sequence(raw_regions):
            projected_regions: list[object] = []
            fragment_key = _text(raw_window.get("fragmentKey"))
            for candidate_index, raw_region in enumerate(cast(Sequence[object], raw_regions)):
                if isinstance(raw_region, Mapping):
                    projected_regions.append(
                        {
                            key: raw_region[key]
                            for key in location_region_fields
                            if key in raw_region
                        }
                    )
                    if fragment_key:
                        raw_regions_by_key[(fragment_key, candidate_index)] = raw_region
                else:
                    projected_regions.append(raw_region)
            projected["regions"] = projected_regions
        elif "regions" in raw_window:
            projected["regions"] = raw_regions
        location_nodes.append(projected)

    localization = normalize_calculation_localization_batch(
        location_nodes,
        fragments,
        batch_index=batch_index,
        attempt_id=attempt_id,
        model_id=model_id,
        prompt_version=prompt_version,
        parse_issues=location_parse_issues,
        min_confidence=min_confidence,
    )
    localization_contract_valid = localization.evidence_complete
    transcription_issues: list[CalculationLocalizationIssue] = []
    transcriptions: list[CalculationRegionTranscription] = []

    for region in localization.regions:
        path = (
            f"$.windows[{region.fragment_key}].regions"
            f"[{region.model_candidate_index}]"
        )
        raw_region = raw_regions_by_key.get(
            (region.fragment_key, region.model_candidate_index)
        )
        if raw_region is None:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_mapping_missing",
                    path,
                    "A localized region has no matching combined transcription.",
                )
            )
            continue
        if set(raw_region) != combined_region_fields:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_fields_invalid",
                    path,
                    "Combined regions must contain the exact location and transcription fields.",
                    {
                        "missing": sorted(combined_region_fields - set(raw_region)),
                        "extra": sorted(set(raw_region) - combined_region_fields),
                    },
                )
            )
            continue
        transcription = _text(raw_region.get("transcription"))
        if not transcription:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_missing",
                    f"{path}.transcription",
                    "A located region must include a non-empty transcription.",
                )
            )
            continue
        confidence = _confidence(raw_region.get("transcriptionConfidence"))
        if confidence is None:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_confidence_invalid",
                    f"{path}.transcriptionConfidence",
                    "Transcription confidence must be finite and between zero and one.",
                )
            )
            continue
        model_issues = _model_issues(raw_region.get("transcriptionIssues"))
        if model_issues is None:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_issues_invalid",
                    f"{path}.transcriptionIssues",
                    "Transcription issues must be an array of non-empty strings.",
                )
            )
            continue
        if confidence < min_confidence:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_low_confidence",
                    f"{path}.transcriptionConfidence",
                    "Transcription confidence is below the review threshold.",
                    {"confidence": confidence, "threshold": min_confidence},
                )
            )
        if model_issues:
            transcription_issues.append(
                _result_issue(
                    "calculation_transcription_model_issue",
                    f"{path}.transcriptionIssues",
                    "The model reported uncertainty for this transcription.",
                    {"issues": list(model_issues)},
                )
            )
        transcriptions.append(
            CalculationRegionTranscription(
                fragment_key=region.fragment_key,
                model_candidate_index=region.model_candidate_index,
                transcription=transcription,
                confidence=confidence,
                issues=model_issues,
            )
        )

    transcription_contract_valid = (
        localization_contract_valid
        and len(transcriptions) == len(localization.regions)
        and not any(
            issue.code
            in {
                "calculation_transcription_mapping_missing",
                "calculation_transcription_fields_invalid",
                "calculation_transcription_missing",
                "calculation_transcription_confidence_invalid",
                "calculation_transcription_issues_invalid",
            }
            for issue in transcription_issues
        )
    )
    return CalculationRecognitionBatchResult(
        localization=localization,
        transcriptions=tuple(transcriptions),
        localization_contract_valid=localization_contract_valid,
        transcription_contract_valid=transcription_contract_valid,
        issues=tuple(_unique_issues([*localization.issues, *transcription_issues])),
    )


def failed_calculation_localization_batch(
    fragments: Sequence[CalculationSearchFragment],
    *,
    batch_index: int,
    attempt_id: str,
    model_id: str,
    prompt_version: str,
    issue_code: str,
    issue_message: str,
) -> CalculationLocalizationBatchResult:
    """Create a uniform incomplete result after one bounded model call fails."""

    _validate_batch_metadata(batch_index, attempt_id, model_id, prompt_version, 0.75)
    if not issue_code.strip() or not issue_message.strip():
        raise ValueError("failed localization issue code and message must not be empty")
    windows = tuple(
        CalculationWindowResult(
            fragment.fragment_key,
            CalculationWindowStatus.UNCERTAIN,
            0.0,
            (issue_code.strip(),),
            (),
        )
        for fragment in fragments
    )
    return CalculationLocalizationBatchResult(
        batch_index=batch_index,
        attempt_id=attempt_id,
        windows=windows,
        regions=(),
        confidence=0.0,
        issues=(
            _result_issue(
                issue_code.strip(),
                "$",
                issue_message.strip(),
                {"batchIndex": batch_index, "attemptId": attempt_id},
            ),
        ),
        evidence_complete=False,
        reliable_blank=False,
        model_id=model_id,
        prompt_version=prompt_version,
    )


def aggregate_calculation_localization_batches(
    fragments: Sequence[CalculationSearchFragment],
    batches: Sequence[CalculationLocalizationBatchResult],
    *,
    plan_issues: Sequence[CalculationLocalizationIssue | Mapping[str, object]] = (),
    min_confidence: float = 0.75,
) -> CalculationLocalizationResult:
    """Aggregate non-overlapping batches while auditing exact fragment coverage."""

    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be finite and between zero and one")
    issues = [
        issue if isinstance(issue, CalculationLocalizationIssue) else _parsed_issue(issue)
        for issue in plan_issues
    ]
    evidence_complete = not issues
    expected_order: list[str] = []
    fragment_by_key: dict[str, CalculationSearchFragment] = {}
    for index, fragment in enumerate(fragments):
        if fragment.fragment_key in fragment_by_key:
            evidence_complete = False
            issues.append(
                _result_issue(
                    "calculation_expected_fragment_duplicate",
                    f"$.fragments[{index}].fragmentKey",
                    "Expected aggregate fragment keys must be unique.",
                    {"fragmentKey": fragment.fragment_key},
                )
            )
            continue
        fragment_by_key[fragment.fragment_key] = fragment
        expected_order.append(fragment.fragment_key)

    observed: dict[str, list[CalculationWindowResult]] = {}
    all_regions: list[LocalizedCalculationRegion] = []
    for batch_position, batch in enumerate(batches):
        evidence_complete = evidence_complete and batch.evidence_complete
        issues.extend(batch.issues)
        all_regions.extend(batch.regions)
        for window in batch.windows:
            if window.fragment_key not in fragment_by_key:
                evidence_complete = False
                issues.append(
                    _result_issue(
                        "calculation_fragment_key_unknown",
                        f"$.batches[{batch_position}].windows",
                        "A batch returned a fragment outside the aggregate request.",
                        {"fragmentKey": window.fragment_key},
                    )
                )
                continue
            observed.setdefault(window.fragment_key, []).append(window)

    windows: list[CalculationWindowResult] = []
    for fragment_key in expected_order:
        matches = observed.get(fragment_key, [])
        if len(matches) != 1:
            evidence_complete = False
            code = (
                "calculation_fragment_batch_missing"
                if not matches
                else "calculation_fragment_batch_duplicate"
            )
            issues.append(
                _result_issue(
                    code,
                    "$.batches",
                    "Each expected fragment must belong to exactly one completed batch.",
                    {"fragmentKey": fragment_key, "count": len(matches)},
                )
            )
            windows.append(
                CalculationWindowResult(
                    fragment_key,
                    CalculationWindowStatus.UNCERTAIN,
                    0.0,
                    (code,),
                    (),
                )
            )
        else:
            windows.append(matches[0])

    regions, duplicate_issues = _deduplicate_regions(all_regions)
    issues.extend(duplicate_issues)
    windows = _windows_with_regions(windows, regions)
    ordered_issues = tuple(_unique_issues(issues))
    confidence = _aggregate_confidence(windows, regions)
    reliable_blank = (
        bool(windows)
        and bool(batches)
        and evidence_complete
        and not ordered_issues
        and all(batch.reliable_blank for batch in batches)
        and all(window.status is CalculationWindowStatus.BLANK for window in windows)
        and all(window.confidence >= min_confidence for window in windows)
    )
    return CalculationLocalizationResult(
        windows=tuple(windows),
        regions=tuple(regions),
        batches=tuple(batches),
        confidence=confidence,
        issues=ordered_issues,
        evidence_complete=evidence_complete,
        reliable_blank=reliable_blank,
    )


def _normalize_window(
    raw: Mapping[str, object],
    fragment: CalculationSearchFragment,
    *,
    model_window_index: int,
    batch_index: int,
    attempt_id: str,
    min_confidence: float,
) -> tuple[CalculationWindowResult, bool, list[CalculationLocalizationIssue]]:
    path = f"$.windows[{model_window_index}]"
    issues: list[CalculationLocalizationIssue] = []
    complete = True
    status_value = raw.get("status")
    try:
        status = CalculationWindowStatus(status_value) if isinstance(status_value, str) else None
    except (ValueError, TypeError):
        status = None
    if status is None:
        status = CalculationWindowStatus.UNCERTAIN
        complete = False
        issues.append(
            _result_issue(
                "calculation_window_status_invalid",
                f"{path}.status",
                "Window status must be located, blank, or uncertain.",
            )
        )
    confidence = _confidence(raw.get("confidence"))
    if confidence is None:
        confidence = 0.0
        complete = False
        issues.append(
            _result_issue(
                "calculation_window_confidence_invalid",
                f"{path}.confidence",
                "Window confidence must be a finite number between zero and one.",
            )
        )
    elif confidence < min_confidence:
        issues.append(
            _result_issue(
                "calculation_window_low_confidence",
                f"{path}.confidence",
                "Window localization confidence is below the review threshold.",
                {"confidence": confidence, "threshold": min_confidence},
            )
        )
    model_issues = _model_issues(raw.get("issues"))
    if model_issues is None:
        model_issues = ()
        complete = False
        issues.append(
            _result_issue(
                "calculation_window_issues_invalid",
                f"{path}.issues",
                "Window issues must be an array of non-empty strings.",
            )
        )
    elif model_issues:
        issues.append(
            _result_issue(
                "calculation_window_model_issue",
                f"{path}.issues",
                "The locator reported uncertainty for this search window.",
                {"issues": list(model_issues)},
            )
        )

    raw_regions = raw.get("regions")
    regions: list[LocalizedCalculationRegion] = []
    if not _is_sequence(raw_regions):
        complete = False
        issues.append(
            _result_issue(
                "calculation_window_regions_invalid",
                f"{path}.regions",
                "Window regions must be an array.",
            )
        )
    else:
        for candidate_index, raw_region in enumerate(cast(Sequence[object], raw_regions)):
            region, region_complete, region_issues = _normalize_region(
                raw_region,
                fragment,
                path=f"{path}.regions[{candidate_index}]",
                model_candidate_index=candidate_index,
                batch_index=batch_index,
                attempt_id=attempt_id,
                min_confidence=min_confidence,
            )
            complete = complete and region_complete
            issues.extend(region_issues)
            if region is not None:
                regions.append(region)

    raw_region_count = len(cast(Sequence[object], raw_regions)) if _is_sequence(raw_regions) else 0
    if status is CalculationWindowStatus.LOCATED and (raw_region_count == 0 or not regions):
        complete = False
        issues.append(
            _result_issue(
                "calculation_located_regions_missing",
                f"{path}.regions",
                "located status requires at least one valid region.",
            )
        )
        status = CalculationWindowStatus.UNCERTAIN
    elif status is CalculationWindowStatus.BLANK and raw_region_count != 0:
        complete = False
        issues.append(
            _result_issue(
                "calculation_blank_regions_present",
                f"{path}.regions",
                "blank status cannot contain regions.",
            )
        )
        regions = []
        status = CalculationWindowStatus.UNCERTAIN
    if status is CalculationWindowStatus.UNCERTAIN and not model_issues:
        complete = False
        issues.append(
            _result_issue(
                "calculation_uncertain_issue_missing",
                f"{path}.issues",
                "uncertain status requires at least one model issue.",
            )
        )
    elif status is CalculationWindowStatus.UNCERTAIN:
        issues.append(
            _result_issue(
                "calculation_window_uncertain",
                f"{path}.status",
                "The model could not determine whether this window contains student work.",
            )
        )
    return (
        CalculationWindowResult(
            fragment_key=fragment.fragment_key,
            status=status,
            confidence=confidence,
            issues=model_issues,
            regions=tuple(regions),
        ),
        complete,
        issues,
    )


def _normalize_region(
    raw: object,
    fragment: CalculationSearchFragment,
    *,
    path: str,
    model_candidate_index: int,
    batch_index: int,
    attempt_id: str,
    min_confidence: float,
) -> tuple[LocalizedCalculationRegion | None, bool, list[CalculationLocalizationIssue]]:
    if not isinstance(raw, Mapping):
        return (
            None,
            False,
            [
                _result_issue(
                    "calculation_region_not_object",
                    path,
                    "Each localized region must be an object.",
                )
            ],
        )
    expected_fields = {"bbox", "confidence", "issues"}
    keys = set(raw)
    if keys != expected_fields:
        return (
            None,
            False,
            [
                _result_issue(
                    "calculation_region_fields_invalid",
                    path,
                    "Region objects must contain exactly bbox, confidence, and issues.",
                    {
                        "missing": sorted(expected_fields - keys),
                        "extra": sorted(keys - expected_fields),
                    },
                )
            ],
        )
    bbox = _model_bbox(raw.get("bbox"))
    if bbox is None:
        return (
            None,
            False,
            [
                _result_issue(
                    "calculation_region_bbox_invalid",
                    f"{path}.bbox",
                    "Region bbox must have finite positive area inside the 0..1000 grid.",
                )
            ],
        )
    confidence = _confidence(raw.get("confidence"))
    region_issues: list[CalculationLocalizationIssue] = []
    complete = True
    if confidence is None:
        confidence = 0.0
        complete = False
        region_issues.append(
            _result_issue(
                "calculation_region_confidence_invalid",
                f"{path}.confidence",
                "Region confidence must be a finite number between zero and one.",
            )
        )
    elif confidence < min_confidence:
        region_issues.append(
            _result_issue(
                "calculation_region_low_confidence",
                f"{path}.confidence",
                "Region confidence is below the review threshold.",
                {"confidence": confidence, "threshold": min_confidence},
            )
        )
    model_issues = _model_issues(raw.get("issues"))
    if model_issues is None:
        model_issues = ()
        complete = False
        region_issues.append(
            _result_issue(
                "calculation_region_issues_invalid",
                f"{path}.issues",
                "Region issues must be an array of non-empty strings.",
            )
        )
    elif model_issues:
        region_issues.append(
            _result_issue(
                "calculation_region_model_issue",
                f"{path}.issues",
                "The locator reported uncertainty for this region.",
                {"issues": list(model_issues)},
            )
        )
    left, top, right, bottom = bbox
    template_bbox = (
        fragment.x + fragment.width * left / 1000.0,
        fragment.y + fragment.height * top / 1000.0,
        fragment.width * (right - left) / 1000.0,
        fragment.height * (bottom - top) / 1000.0,
    )
    return (
        LocalizedCalculationRegion(
            fragment_key=fragment.fragment_key,
            template_page_id=fragment.template_page_id,
            student_page_id=fragment.student_page_id,
            alignment_revision_id=fragment.alignment_revision_id,
            page_number=fragment.page_number,
            student_page_number=fragment.student_page_number or fragment.page_number,
            model_bbox=bbox,
            template_bbox=template_bbox,
            confidence=confidence,
            issues=model_issues,
            model_candidate_index=model_candidate_index,
            batch_index=batch_index,
            attempt_id=attempt_id,
        ),
        complete,
        region_issues,
    )


def _deduplicate_regions(
    regions: Sequence[LocalizedCalculationRegion],
) -> tuple[list[LocalizedCalculationRegion], list[CalculationLocalizationIssue]]:
    ordered = sorted(regions, key=_region_order)
    kept: list[LocalizedCalculationRegion] = []
    issues: list[CalculationLocalizationIssue] = []
    for candidate in ordered:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if existing.fragment_key == candidate.fragment_key
                and _iou(existing.template_bbox, candidate.template_bbox) >= 0.85
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(candidate)
            continue
        existing = kept[duplicate_index]
        winner, discarded = (
            (candidate, existing)
            if candidate.confidence > existing.confidence
            else (existing, candidate)
        )
        kept[duplicate_index] = winner
        issues.append(
            _result_issue(
                "calculation_region_duplicate",
                "$.windows.regions",
                "Highly overlapping candidates were deduplicated without expanding their hull.",
                {
                    "keptFragmentKey": winner.fragment_key,
                    "keptCandidateIndex": winner.model_candidate_index,
                    "discardedFragmentKey": discarded.fragment_key,
                    "discardedCandidateIndex": discarded.model_candidate_index,
                },
            )
        )
    kept.sort(key=_region_order)
    return kept, issues


def _windows_with_regions(
    windows: Sequence[CalculationWindowResult],
    regions: Sequence[LocalizedCalculationRegion],
) -> list[CalculationWindowResult]:
    by_key: dict[str, list[LocalizedCalculationRegion]] = {}
    for region in regions:
        by_key.setdefault(region.fragment_key, []).append(region)
    return [
        replace(window, regions=tuple(by_key.get(window.fragment_key, ())))
        for window in windows
    ]


def _ordered_questions(
    questions: Sequence[Mapping[str, object]],
    issues: list[CalculationLocalizationIssue],
) -> list[tuple[int, Mapping[str, object]]]:
    values: list[tuple[int, int, Mapping[str, object]]] = []
    seen_orders: dict[int, int] = {}
    for index, question in enumerate(questions):
        if _truthy_duplicate(question.get("is_duplicate", question.get("isDuplicate", False))):
            continue
        sort_order = _integer(question.get("sort_order", question.get("sortOrder")))
        if sort_order is None:
            issues.append(
                _plan_issue(
                    "calculation_question_order_invalid",
                    f"$.questions[{index}].sortOrder",
                    "Confirmed questions require an integer sort order.",
                )
            )
            continue
        if sort_order in seen_orders:
            issues.append(
                _plan_issue(
                    "calculation_question_order_ambiguous",
                    f"$.questions[{index}].sortOrder",
                    "Two confirmed questions share the same sort order.",
                    {"sortOrder": sort_order, "firstIndex": seen_orders[sort_order]},
                )
            )
        else:
            seen_orders[sort_order] = index
        values.append((sort_order, index, question))
    values.sort(key=lambda item: (item[0], item[1]))
    return [(index, question) for _, index, question in values]


def _frame_regions(
    question: Mapping[str, object] | None,
    role: str,
    issues: list[CalculationLocalizationIssue],
) -> list[_FrameRegion]:
    if question is None:
        return []
    raw_regions = question.get("frame_regions", question.get("frameRegions"))
    if not _is_sequence(raw_regions):
        return []
    regions: list[_FrameRegion] = []
    for index, raw in enumerate(cast(Sequence[object], raw_regions)):
        path = f"$.questions.{role}.frameRegions[{index}]"
        if not isinstance(raw, Mapping):
            issues.append(
                _plan_issue(
                    "calculation_frame_region_invalid",
                    path,
                    "Confirmed frame regions must be objects.",
                )
            )
            continue
        page_number = _positive_integer(raw.get("page_number", raw.get("pageNumber")))
        template_page_id = _text(raw.get("template_page_id", raw.get("templatePageId")))
        x = _finite(raw.get("x"))
        y = _finite(raw.get("y"))
        width = _finite(raw.get("width"))
        height = _finite(raw.get("height"))
        if (
            page_number is None
            or not template_page_id
            or x is None
            or y is None
            or width is None
            or height is None
            or x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1
            or y + height > 1
        ):
            issues.append(
                _plan_issue(
                    "calculation_frame_region_invalid",
                    path,
                    "Confirmed frame geometry must have positive area inside a normalized page.",
                )
            )
            continue
        regions.append(
            _FrameRegion(page_number, template_page_id, x, y, width, height, index)
        )
    return regions


def _binding_maps(
    bindings: Sequence[CalculationPageBinding],
    issues: list[CalculationLocalizationIssue],
) -> tuple[dict[int, CalculationPageBinding], dict[int, CalculationPageBinding]]:
    by_template: dict[int, CalculationPageBinding] = {}
    by_student: dict[int, CalculationPageBinding] = {}
    duplicate_template: set[int] = set()
    duplicate_student: set[int] = set()
    for index, binding in enumerate(bindings):
        if (
            binding.page_number <= 0
            or binding.student_page_number <= 0
            or not math.isfinite(binding.alignment_confidence)
            or not 0.0 <= binding.alignment_confidence <= 1.0
        ):
            issues.append(
                _plan_issue(
                    "calculation_page_binding_invalid",
                    f"$.pageBindings[{index}]",
                    "Page numbers and alignment confidence must be valid.",
                )
            )
            continue
        if binding.page_number in by_template:
            duplicate_template.add(binding.page_number)
        else:
            by_template[binding.page_number] = binding
        if binding.student_page_number in by_student:
            duplicate_student.add(binding.student_page_number)
        else:
            by_student[binding.student_page_number] = binding
    for page_number in sorted(duplicate_template):
        by_template.pop(page_number, None)
        issues.append(
            _plan_issue(
                "calculation_page_binding_duplicate",
                "$.pageBindings",
                "A template page has multiple student-page pairings.",
                {"pageNumber": page_number},
            )
        )
    for page_number in sorted(duplicate_student):
        by_student.pop(page_number, None)
        issues.append(
            _plan_issue(
                "calculation_student_page_binding_duplicate",
                "$.pageBindings",
                "A student page has multiple template-page pairings.",
                {"studentPageNumber": page_number},
            )
        )
    ordered_bindings = sorted(by_student.values(), key=lambda item: item.student_page_number)
    for left, right in pairwise(ordered_bindings):
        if right.page_number > left.page_number:
            continue
        issues.append(
            _plan_issue(
                "calculation_page_order_ambiguous",
                "$.pageBindings",
                "Student upload order must map to strictly increasing template pages.",
                {
                    "leftStudentPageNumber": left.student_page_number,
                    "leftTemplatePageNumber": left.page_number,
                    "rightStudentPageNumber": right.student_page_number,
                    "rightTemplatePageNumber": right.page_number,
                },
            )
        )
    return by_template, by_student


def _validate_upload_coverage(
    start: _FrameRegion,
    end_page: int,
    uploaded_pages: Sequence[int],
    bindings_by_template: Mapping[int, CalculationPageBinding],
    bindings_by_student: Mapping[int, CalculationPageBinding],
    issues: list[CalculationLocalizationIssue],
) -> None:
    uploaded = {page for page in uploaded_pages if _positive_integer(page) is not None}
    for page_number in range(start.page_number, end_page + 1):
        binding = bindings_by_template.get(page_number)
        if binding is not None and binding.student_page_number not in uploaded:
            issues.append(
                _plan_issue(
                    "calculation_submission_page_missing",
                    "$.uploadedStudentPageNumbers",
                    "A paired page in the search interval is absent from this upload.",
                    {
                        "pageNumber": page_number,
                        "studentPageNumber": binding.student_page_number,
                    },
                )
            )
    start_binding = bindings_by_template.get(start.page_number)
    end_binding = bindings_by_template.get(end_page)
    if start_binding is None or end_binding is None or not uploaded:
        return
    student_start = start_binding.student_page_number
    student_end = end_binding.student_page_number
    if student_end < student_start:
        issues.append(
            _plan_issue(
                "calculation_page_binding_order_invalid",
                "$.pageBindings",
                "Student page pairing order contradicts template page order.",
                {"start": student_start, "end": student_end},
            )
        )
        return
    relevant = sorted(page for page in uploaded if student_start <= page <= student_end)
    for page in relevant:
        binding = bindings_by_student.get(page)
        if binding is None:
            issues.append(
                _plan_issue(
                    "calculation_student_page_unaligned",
                    "$.pageBindings",
                    "An uploaded student page inside the search interval is not paired.",
                    {"studentPageNumber": page},
                )
            )
        elif not binding.is_reliable:
            issues.append(
                _plan_issue(
                    "calculation_alignment_unreliable",
                    "$.pageBindings",
                    "An uploaded student page inside the search interval is not reliably aligned.",
                    {"studentPageNumber": page},
                )
            )
    for left, right in pairwise(relevant):
        if right != left + 1:
            issues.append(
                _plan_issue(
                    "calculation_submission_page_gap",
                    "$.uploadedStudentPageNumbers",
                    "Uploaded student page numbers are discontinuous inside the answer tail.",
                    {"left": left, "right": right},
                )
            )


def _validate_frame_page_bindings(
    regions: Sequence[_FrameRegion],
    bindings_by_template: Mapping[int, CalculationPageBinding],
    role: str,
    issues: list[CalculationLocalizationIssue],
) -> None:
    for region in regions:
        binding = bindings_by_template.get(region.page_number)
        if binding is None:
            continue
        if binding.template_page_id != region.template_page_id:
            issues.append(
                _plan_issue(
                    "calculation_frame_page_binding_mismatch",
                    f"$.questions.{role}.frameRegions[{region.source_index}]",
                    "The confirmed frame and page pairing refer to different template pages.",
                    {
                        "pageNumber": region.page_number,
                        "frameTemplatePageId": region.template_page_id,
                        "bindingTemplatePageId": binding.template_page_id,
                    },
                )
            )


def _validate_current_frames_inside_window(
    regions: Sequence[_FrameRegion],
    start: _FrameRegion,
    end_page: int,
    end_y: float,
    issues: list[CalculationLocalizationIssue],
) -> None:
    for region in regions:
        starts_inside = (region.page_number, region.y) >= (start.page_number, start.y)
        ends_inside = region.page_number < end_page or (
            region.page_number == end_page and region.bottom <= end_y
        )
        if starts_inside and ends_inside:
            continue
        issues.append(
            _plan_issue(
                "calculation_current_frame_outside_search_window",
                f"$.questions.current.frameRegions[{region.source_index}]",
                "The vertical half-open window cannot contain every confirmed current frame.",
                {
                    "pageNumber": region.page_number,
                    "top": region.y,
                    "bottom": region.bottom,
                    "endPageNumber": end_page,
                    "endTop": end_y,
                },
            )
        )


def _last_uploaded_page(
    values: Sequence[int], issues: list[CalculationLocalizationIssue]
) -> int | None:
    pages: list[int] = []
    for index, value in enumerate(values):
        page = _positive_integer(value)
        if page is None:
            issues.append(
                _plan_issue(
                    "calculation_submission_page_number_invalid",
                    f"$.uploadedStudentPageNumbers[{index}]",
                    "Uploaded page numbers must be positive integers.",
                )
            )
        else:
            pages.append(page)
    if len(pages) != len(set(pages)):
        issues.append(
            _plan_issue(
                "calculation_submission_page_duplicate",
                "$.uploadedStudentPageNumbers",
                "Uploaded student page numbers must be unique.",
            )
        )
    return max(pages, default=None)


def _model_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not _is_sequence(value) or len(cast(Sequence[object], value)) != 4:
        return None
    numbers = [_finite(raw) for raw in cast(Sequence[object], value)]
    if any(number is None for number in numbers):
        return None
    left, top, right, bottom = cast(tuple[float, float, float, float], tuple(numbers))
    if left < 0 or top < 0 or right > 1000 or bottom > 1000:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _confidence(value: object) -> float | None:
    number = _finite(value)
    return number if number is not None and 0 <= number <= 1 else None


def _model_issues(value: object) -> tuple[str, ...] | None:
    if not _is_sequence(value):
        return None
    output: list[str] = []
    for raw in cast(Sequence[object], value):
        if not isinstance(raw, str) or not raw.strip():
            return None
        if raw.strip() not in output:
            output.append(raw.strip())
    return tuple(output)


def _aggregate_confidence(
    windows: Sequence[CalculationWindowResult],
    regions: Sequence[LocalizedCalculationRegion],
) -> float:
    values = [window.confidence for window in windows]
    values.extend(region.confidence for region in regions)
    return min(values, default=0.0)


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_x, left_y, left_width, left_height = left
    right_x, right_y, right_width, right_height = right
    overlap_width = min(left_x + left_width, right_x + right_width) - max(left_x, right_x)
    overlap_height = min(left_y + left_height, right_y + right_height) - max(left_y, right_y)
    if overlap_width <= 0 or overlap_height <= 0:
        return 0.0
    overlap = overlap_width * overlap_height
    union = left_width * left_height + right_width * right_height - overlap
    return overlap / union if union > 0 else 0.0


def _frame_order(region: _FrameRegion) -> tuple[int, float, float, int]:
    return region.page_number, region.y, region.x, region.source_index


def _region_order(
    region: LocalizedCalculationRegion,
) -> tuple[int, float, float, float, float, int, int]:
    return (
        region.page_number,
        region.y,
        region.x,
        region.height,
        region.width,
        region.batch_index,
        region.model_candidate_index,
    )


def _validate_batch_metadata(
    batch_index: int,
    attempt_id: str,
    model_id: str,
    prompt_version: str,
    min_confidence: float,
) -> None:
    if isinstance(batch_index, bool) or not isinstance(batch_index, int) or batch_index <= 0:
        raise ValueError("batch_index must be a positive integer")
    if not attempt_id.strip() or not model_id.strip() or not prompt_version.strip():
        raise ValueError("attempt, model, and prompt identifiers must not be empty")
    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be finite and between zero and one")


def _parsed_issue(raw: Mapping[str, object]) -> CalculationLocalizationIssue:
    code = _text(raw.get("code")) or "calculation_localization_parse_error"
    path = _text(raw.get("path")) or "$"
    message = _text(raw.get("message")) or "The localization response could not be parsed."
    details = raw.get("details")
    return CalculationLocalizationIssue(
        code=code,
        path=path,
        message=message,
        details=dict(details) if isinstance(details, Mapping) else {},
    )


def _plan_issue(
    code: str,
    path: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> CalculationLocalizationIssue:
    return CalculationLocalizationIssue(code, path, message, details or {})


def _result_issue(
    code: str,
    path: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> CalculationLocalizationIssue:
    return CalculationLocalizationIssue(code, path, message, details or {})


def _unique_issues(
    issues: Sequence[CalculationLocalizationIssue],
) -> list[CalculationLocalizationIssue]:
    output: list[CalculationLocalizationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = issue.code, issue.path, repr(sorted(issue.details.items()))
        if key not in seen:
            seen.add(key)
            output.append(issue)
    return output


def _finite(value: object) -> float | None:
    if isinstance(value, bool | str | bytes | bytearray) or value is None:
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _positive_integer(value: object) -> int | None:
    number = _integer(value)
    return number if number is not None and number > 0 else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _truthy_duplicate(value: object) -> bool:
    return value is True or value == 1


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


__all__ = [
    "CalculationLocalizationBatchResult",
    "CalculationLocalizationIssue",
    "CalculationLocalizationResult",
    "CalculationPageBinding",
    "CalculationRecognitionBatchResult",
    "CalculationRegionTranscription",
    "CalculationSearchFragment",
    "CalculationSearchPlan",
    "CalculationWindowResult",
    "CalculationWindowStatus",
    "LocalizedCalculationRegion",
    "aggregate_calculation_localization_batches",
    "build_calculation_search_plan",
    "failed_calculation_localization_batch",
    "normalize_calculation_localization_batch",
    "normalize_calculation_recognition_batch",
]
