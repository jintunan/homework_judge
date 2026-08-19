from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast


class BlankDetectionContractError(ValueError):
    """The detector was invoked without an exact, confirmed question frame."""


@dataclass(frozen=True, slots=True)
class BlankDetectionFragment:
    region_key: str
    template_page_id: str
    page_number: int
    x: float
    y: float
    width: float
    height: float
    sort_order: int
    image: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class BlankDetectionRequest:
    frame_set_id: str
    question_id: str
    question_surface: dict[str, object]
    fragments: tuple[BlankDetectionFragment, ...]

    def prompt_context(self) -> dict[str, object]:
        """Return model context without answers, synonyms, scores or grading state."""

        return {
            "frameSetId": self.frame_set_id,
            "questionId": self.question_id,
            "questionSurface": dict(self.question_surface),
            "fragments": [
                {
                    "fragmentKey": item.region_key,
                    "templatePageId": item.template_page_id,
                    "pageNumber": item.page_number,
                    "sortOrder": item.sort_order,
                    "coordinateGrid": [0, 0, 1000, 1000],
                }
                for item in self.fragments
            ],
        }


@dataclass(frozen=True, slots=True)
class BlankDetectionIssue:
    code: str
    message: str
    candidate_index: int | None = None
    fragment_key: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }
        if self.candidate_index is not None:
            value["candidateIndex"] = self.candidate_index
        if self.fragment_key is not None:
            value["fragmentKey"] = self.fragment_key
        return value


@dataclass(frozen=True, slots=True)
class DetectedBlank:
    blank_key: str
    sort_order: int
    anchor: dict[str, object]
    model_candidate_index: int

    def as_dict(self) -> dict[str, object]:
        return {
            "blankKey": self.blank_key,
            "sortOrder": self.sort_order,
            "anchor": dict(self.anchor),
            "modelCandidateIndex": self.model_candidate_index,
        }


@dataclass(frozen=True, slots=True)
class BlankDetectionResult:
    blanks: tuple[DetectedBlank, ...]
    blocking_issues: tuple[BlankDetectionIssue, ...]
    ignored_candidate_count: int

    @property
    def ready_for_confirmation(self) -> bool:
        return bool(self.blanks) and not self.blocking_issues

    def as_dict(self) -> dict[str, object]:
        return {
            "blanks": [item.as_dict() for item in self.blanks],
            "blockingIssues": [item.as_dict() for item in self.blocking_issues],
            "ignoredCandidateCount": self.ignored_candidate_count,
            "readyForConfirmation": self.ready_for_confirmation,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    model_index: int
    fragment: BlankDetectionFragment
    left: float
    top: float
    right: float
    bottom: float
    confidence: float
    issues: tuple[str, ...]


_SURFACE_KEYS = ("type", "stem", "options", "subquestions", "layoutHints")
_IGNORED_CANDIDATE_TYPES = {
    "printed_option",
    "printed_label",
    "diagram_text",
    "decoration",
    "question_number",
    "other_printed_text",
}


def build_blank_detection_request(
    *,
    frame_set: Mapping[str, object],
    question: Mapping[str, object],
    frame_images: Mapping[str, bytes],
) -> BlankDetectionRequest:
    """Bind detector input to every fragment of one confirmed frame-set item.

    The exact-key check is deliberate: callers cannot accidentally add an
    answer-only crop or omit a cross-page fragment. The model always sees the
    complete, teacher-confirmed visual question frame.
    """

    if str(frame_set.get("status", "")) != "confirmed":
        raise BlankDetectionContractError("blank detection requires a confirmed frame set")
    frame_set_id = _required_text(frame_set.get("id"), "frame set id")
    question_id = _required_text(question.get("id"), "question id")
    raw_items = frame_set.get("items")
    if not _sequence(raw_items):
        raise BlankDetectionContractError("confirmed frame set has no items")
    matching_items = [
        item
        for item in cast(Sequence[object], raw_items)
        if isinstance(item, Mapping) and str(item.get("questionId", "")) == question_id
    ]
    if len(matching_items) != 1:
        raise BlankDetectionContractError(
            "confirmed frame set must contain exactly one item for the question"
        )
    item = cast(Mapping[str, object], matching_items[0])
    if str(item.get("status", "")) != "confirmed":
        raise BlankDetectionContractError("blank detection requires a confirmed frame item")

    raw_fragments = item.get("fragments")
    if not _sequence(raw_fragments) or not raw_fragments:
        raise BlankDetectionContractError("confirmed frame item has no fragments")
    fragments_without_images: list[tuple[str, str, int, float, float, float, float, int]] = []
    seen_keys: set[str] = set()
    seen_orders: set[int] = set()
    for raw in cast(Sequence[object], raw_fragments):
        if not isinstance(raw, Mapping):
            raise BlankDetectionContractError("confirmed frame fragment must be an object")
        region_key = _required_text(raw.get("regionKey"), "frame fragment regionKey")
        template_page_id = _required_text(
            raw.get("templatePageId"), "frame fragment templatePageId"
        )
        page_number = _positive_integer(raw.get("pageNumber"), "frame fragment pageNumber")
        sort_order = _non_negative_integer(raw.get("sortOrder"), "frame fragment sortOrder")
        x = _finite_number(raw.get("x"), "frame fragment x")
        y = _finite_number(raw.get("y"), "frame fragment y")
        width = _finite_number(raw.get("width"), "frame fragment width")
        height = _finite_number(raw.get("height"), "frame fragment height")
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1 or y + height > 1:
            raise BlankDetectionContractError("confirmed frame fragment is outside page bounds")
        if region_key in seen_keys or sort_order in seen_orders:
            raise BlankDetectionContractError(
                "confirmed frame fragments require unique regionKey and sortOrder"
            )
        seen_keys.add(region_key)
        seen_orders.add(sort_order)
        fragments_without_images.append(
            (region_key, template_page_id, page_number, x, y, width, height, sort_order)
        )

    if set(frame_images) != seen_keys:
        raise BlankDetectionContractError(
            "frame images must contain exactly the confirmed question-frame fragments"
        )
    fragments: list[BlankDetectionFragment] = []
    for values in fragments_without_images:
        image = frame_images[values[0]]
        if not isinstance(image, bytes) or not image:
            raise BlankDetectionContractError("each confirmed frame fragment needs image bytes")
        fragments.append(BlankDetectionFragment(*values, image=image))
    fragments.sort(key=lambda value: value.sort_order)

    question_surface = {key: question[key] for key in _SURFACE_KEYS if key in question}
    return BlankDetectionRequest(
        frame_set_id=frame_set_id,
        question_id=question_id,
        question_surface=question_surface,
        fragments=tuple(fragments),
    )


def normalize_blank_detection(
    payload: Mapping[str, object],
    request: BlankDetectionRequest,
    *,
    confidence_threshold: float = 0.65,
) -> BlankDetectionResult:
    """Validate model candidates and assign B1..Bn in deterministic reading order."""

    if not math.isfinite(confidence_threshold) or not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be finite and between zero and one")
    raw_candidates = payload.get("blankCandidates")
    if not _sequence(raw_candidates):
        return BlankDetectionResult(
            (),
            (
                BlankDetectionIssue(
                    "blank_candidates_missing",
                    "Model response must contain a blankCandidates array",
                ),
            ),
            0,
        )

    fragment_by_key = {item.region_key: item for item in request.fragments}
    candidates: list[_Candidate] = []
    issues: list[BlankDetectionIssue] = []
    ignored = 0
    for index, raw in enumerate(cast(Sequence[object], raw_candidates)):
        if not isinstance(raw, Mapping):
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_invalid",
                    "Blank candidate must be an object",
                    candidate_index=index,
                )
            )
            continue
        candidate_type = str(raw.get("candidateType", "")).strip()
        if candidate_type in _IGNORED_CANDIDATE_TYPES:
            ignored += 1
            continue
        if candidate_type != "answer_blank":
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_type_unknown",
                    "Blank candidate type is not recognized",
                    candidate_index=index,
                    details={"candidateType": candidate_type},
                )
            )
            continue

        fragment_key = str(raw.get("fragmentKey", "")).strip()
        fragment = fragment_by_key.get(fragment_key)
        if fragment is None:
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_fragment_unknown",
                    "Blank candidate does not belong to a confirmed frame fragment",
                    candidate_index=index,
                    fragment_key=fragment_key or None,
                )
            )
            continue
        bbox = _bbox(raw.get("bbox"))
        if bbox is None:
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_out_of_frame",
                    "Blank candidate bbox must have positive area inside the fragment grid",
                    candidate_index=index,
                    fragment_key=fragment_key,
                )
            )
            continue
        confidence = _optional_confidence(raw.get("confidence"))
        if confidence is None:
            confidence = 0.0
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_confidence_invalid",
                    "Blank candidate confidence must be finite and between zero and one",
                    candidate_index=index,
                    fragment_key=fragment_key,
                )
            )
        elif confidence < confidence_threshold:
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_low_confidence",
                    "Blank candidate needs teacher review because confidence is low",
                    candidate_index=index,
                    fragment_key=fragment_key,
                    details={
                        "confidence": confidence,
                        "threshold": confidence_threshold,
                    },
                )
            )
        if raw.get("isComposite") is True:
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_composite",
                    "A composite answer area cannot be confirmed as multiple independent blanks",
                    candidate_index=index,
                    fragment_key=fragment_key,
                )
            )
        model_issues = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in cast(Sequence[object], raw.get("issues", ()))
                if str(value).strip()
            )
        ) if _sequence(raw.get("issues")) else ()
        if model_issues:
            issues.append(
                BlankDetectionIssue(
                    "blank_candidate_model_issue",
                    "Model reported uncertainty for the blank candidate",
                    candidate_index=index,
                    fragment_key=fragment_key,
                    details={"issues": list(model_issues)},
                )
            )
        left, top, right, bottom = bbox
        candidates.append(
            _Candidate(
                model_index=index,
                fragment=fragment,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=confidence,
                issues=model_issues,
            )
        )

    candidates.sort(
        key=lambda value: (
            value.fragment.sort_order,
            value.top,
            value.left,
            value.bottom,
            value.right,
        )
    )
    issues.extend(_overlap_issues(candidates))
    blanks = tuple(
        _detected_blank(index, candidate) for index, candidate in enumerate(candidates, 1)
    )
    if not blanks:
        issues.append(
            BlankDetectionIssue(
                "blank_candidates_empty",
                "No independent answer blank was found inside the confirmed question frame",
            )
        )
    return BlankDetectionResult(blanks, tuple(issues), ignored)


def _detected_blank(sort_number: int, candidate: _Candidate) -> DetectedBlank:
    fragment = candidate.fragment
    grid = 1000.0
    x = fragment.x + fragment.width * candidate.left / grid
    y = fragment.y + fragment.height * candidate.top / grid
    width = fragment.width * (candidate.right - candidate.left) / grid
    height = fragment.height * (candidate.bottom - candidate.top) / grid
    return DetectedBlank(
        blank_key=f"B{sort_number}",
        sort_order=sort_number - 1,
        anchor={
            "templatePageId": fragment.template_page_id,
            "pageNumber": fragment.page_number,
            "coordinateSpace": "template_page_normalized",
            "box": {"x": x, "y": y, "width": width, "height": height},
            "source": "model",
            "confidence": candidate.confidence,
            "issues": list(candidate.issues),
        },
        model_candidate_index=candidate.model_index,
    )


def _overlap_issues(candidates: Sequence[_Candidate]) -> list[BlankDetectionIssue]:
    issues: list[BlankDetectionIssue] = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if left.fragment.region_key != right.fragment.region_key:
                continue
            overlap_width = min(left.right, right.right) - max(left.left, right.left)
            overlap_height = min(left.bottom, right.bottom) - max(left.top, right.top)
            if overlap_width <= 0 or overlap_height <= 0:
                continue
            overlap = overlap_width * overlap_height
            smaller = min(
                (left.right - left.left) * (left.bottom - left.top),
                (right.right - right.left) * (right.bottom - right.top),
            )
            if overlap / smaller <= 0.1:
                continue
            issues.append(
                BlankDetectionIssue(
                    "blank_candidates_overlap",
                    "Independent blank candidates overlap too much",
                    candidate_index=right.model_index,
                    fragment_key=right.fragment.region_key,
                    details={"relatedCandidateIndex": left.model_index},
                )
            )
    return issues


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not _sequence(value) or len(cast(Sequence[object], value)) != 4:
        return None
    parsed: list[float] = []
    for raw in cast(Sequence[object], value):
        if isinstance(raw, bool | str | bytes | bytearray):
            return None
        try:
            number = float(cast(Any, raw))
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number):
            return None
        parsed.append(number)
    left, top, right, bottom = parsed
    if left < 0 or top < 0 or right > 1000 or bottom > 1000:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _optional_confidence(value: object) -> float | None:
    if isinstance(value, bool | str | bytes | bytearray):
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _required_text(value: object, name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise BlankDetectionContractError(f"{name} is required")
    return text


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlankDetectionContractError(f"{name} must be a positive integer")
    return value


def _non_negative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlankDetectionContractError(f"{name} must be a non-negative integer")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool | str | bytes | bytearray):
        raise BlankDetectionContractError(f"{name} must be finite")
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as error:
        raise BlankDetectionContractError(f"{name} must be finite") from error
    if not math.isfinite(number):
        raise BlankDetectionContractError(f"{name} must be finite")
    return number


def _sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)
