from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image, ImageDraw

from homework_judge.alignment import (
    AlignmentQuality,
    AlignmentResult,
    Homography,
    PageSize,
)
from homework_judge.alignment.overrides import AlignmentOverrideService
from homework_judge.config import Settings
from homework_judge.db.database import Database, json_dumps, json_loads, now_iso
from homework_judge.errors import AppError
from homework_judge.jobs.student_pipeline import StudentPipeline
from homework_judge.recognition.calculation_localization import (
    CalculationSearchFragment,
    normalize_calculation_localization_batch,
    normalize_calculation_recognition_batch,
)
from homework_judge.recognition.prompts import (
    CALCULATION_LOCALIZATION_PROMPT_VERSION,
    CALCULATION_RECOGNITION_PROMPT_VERSION,
    KEYED_FILL_RESPONSE_PROMPT_VERSION,
)
from homework_judge.recognition.service import RecognitionService


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite",
        port=8787,
        dashscope_api_key="test",
        dashscope_base_url="https://example.invalid/v1",
        dashscope_model="test-vl",
        model_timeout_ms=1000,
        model_retry_count=0,
        model_concurrency=1,
        model_pages_per_batch=4,
        answer_pages_per_batch=3,
        max_upload_mb=30,
        max_document_pages=30,
        auto_match_threshold=0.82,
        auto_match_margin=0.08,
        teacher_name="test",
        soffice_path="",
    )


class FakeRecognition:
    def __init__(self) -> None:
        self.question_region_calls = 0
        self.template_region_calls = 0
        self.student_response_calls = 0
        self.calculation_location_calls = 0
        self.calculation_fragments: list[CalculationSearchFragment] = []

    async def recognize_question_regions(
        self,
        _page: dict[str, Any],
        _questions: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]:
        self.question_region_calls += 1
        raise AssertionError("student processing must not detect question frames")

    async def recognize_template_regions(
        self,
        _page: dict[str, Any],
        _questions: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]:
        self.template_region_calls += 1
        raise AssertionError("student processing must not backfill template regions")

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        assert question["number"] == "1"
        assert len(regions) == 1
        assert regions[0]["template_image"].startswith(b"\xff\xd8")
        assert regions[0]["student_image"].startswith(b"\xff\xd8")
        return (
            {
                "transcription": "A",
                "is_blank": False,
                "confidence": 0.97,
                "issues": [],
            },
            {"id": "fake"},
            {"totalTokens": 5},
        )

    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        assert question["type"] == "calculation"
        assert frame_set_id
        assert all(fragment.template_image for fragment in fragments)
        assert all(fragment.student_image for fragment in fragments)
        nodes = [
            {
                "fragmentKey": fragment.fragment_key,
                "status": "located",
                "confidence": 0.98,
                "issues": [],
                "regions": [
                    {
                        "bbox": [350, 560, 550, 680],
                        "confidence": 0.97,
                        "issues": [],
                    }
                ],
            }
            for fragment in fragments
        ]
        return (
            normalize_calculation_localization_batch(
                nodes,
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"locator-{batch_index}"},
            {"totalTokens": 3},
        )


class FailingRecognition(FakeRecognition):
    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        raise RuntimeError("model unavailable")


class RecognitionConcurrencyProbePipeline(StudentPipeline):
    def __init__(
        self,
        settings: Settings,
        database: Database,
        recognition: RecognitionService,
        *,
        fail_number: str | None = None,
        staggered: bool = True,
    ) -> None:
        super().__init__(settings, database, recognition)
        self.fail_number = fail_number
        self.staggered = staggered
        self.active_questions = 0
        self.peak_questions = 0
        self.started: list[str] = []
        self.completed: list[str] = []

    async def _recognize_question_response(
        self,
        question: dict[str, Any],
        questions: list[dict[str, Any]],
        alignments: dict[int, Any],
        blank_configs: dict[str, dict[str, Any]],
        *,
        uploaded_student_page_numbers: list[int],
        allow_non_calculation: bool,
    ) -> dict[str, Any] | None:
        del questions, alignments, blank_configs
        del uploaded_student_page_numbers, allow_non_calculation
        number = str(question["number"])
        self.started.append(number)
        self.active_questions += 1
        self.peak_questions = max(self.peak_questions, self.active_questions)
        try:
            await asyncio.sleep((7 - int(number)) * 0.002 if self.staggered else 0.01)
            if number == self.fail_number:
                raise RuntimeError(f"question {number} failed")
            return {
                "question_id": question["id"],
                "question_number": number,
            }
        finally:
            self.active_questions -= 1
            self.completed.append(number)


class HighConfidenceCalculationRecognition(FakeRecognition):
    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        assert question["type"] == "calculation"
        assert regions
        return (
            {
                "transcription": "x = 42",
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
            },
            {"id": "high-confidence-transcription"},
            {"totalTokens": 5},
        )

    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "located",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [
                            {
                                "bbox": [200, 300, 800, 700],
                                "confidence": 0.99,
                                "issues": [],
                            }
                        ],
                    }
                    for fragment in fragments
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"high-confidence-locator-{batch_index}"},
            {"totalTokens": 3},
        )


class CombinedCalculationRecognition(HighConfidenceCalculationRecognition):
    def __init__(self, mode: str = "success") -> None:
        super().__init__()
        self.mode = mode
        self.calculation_recognition_calls = 0

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        del question
        self.student_response_calls += 1
        segments = [
            {
                "region_index": index,
                "transcription": f"legacy-{index}",
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
            }
            for index, _region in enumerate(regions, 1)
        ]
        return (
            {
                "transcription": "\n".join(
                    str(segment["transcription"]) for segment in segments
                ),
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
                "segments": segments,
            },
            {"id": "combined-fallback-transcription"},
            {"totalTokens": 5},
        )

    async def recognize_calculation_batch(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        del question, frame_set_id
        self.calculation_recognition_calls += 1
        effective_mode = self.mode
        if self.mode == "mixed_batches":
            effective_mode = (
                "success"
                if batch_index == 1
                else "missing_transcription"
                if batch_index == 2
                else "invalid_location"
            )
        if effective_mode == "request_failure":
            raise RuntimeError("combined model unavailable")
        nodes: list[dict[str, Any]] = []
        for fragment in fragments:
            if effective_mode == "blank":
                nodes.append(
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "blank",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [],
                    }
                )
                continue
            region: dict[str, Any] = {
                "bbox": [200, 300, 800, 700],
                "confidence": 0.99,
                "issues": [],
                "transcription": "x = 42",
                "transcriptionConfidence": 0.99,
                "transcriptionIssues": [],
            }
            if effective_mode == "low_confidence":
                region["transcriptionConfidence"] = 0.5
            if effective_mode == "missing_transcription":
                region.pop("transcription")
            if effective_mode == "invalid_location":
                region["bbox"] = [200, 300, 200, 700]
            nodes.append(
                {
                    "fragmentKey": fragment.fragment_key,
                    "status": "located",
                    "confidence": 0.99,
                    "issues": [],
                    "regions": [region],
                }
            )
        return (
            normalize_calculation_recognition_batch(
                nodes,
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_RECOGNITION_PROMPT_VERSION,
            ),
            {"id": f"combined-{batch_index}"},
            {"totalTokens": 7},
        )


class InsideFrameCalculationRecognition(HighConfidenceCalculationRecognition):
    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "located",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [
                            {
                                "bbox": [200, 50, 600, 150],
                                "confidence": 0.99,
                                "issues": [],
                            }
                        ],
                    }
                    for fragment in fragments
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"inside-frame-locator-{batch_index}"},
            {"totalTokens": 3},
        )


class LowConfidenceTranscriptionRecognition(HighConfidenceCalculationRecognition):
    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        assert question["type"] == "calculation"
        assert regions
        return (
            {
                "transcription": "x = 42?",
                "is_blank": False,
                "confidence": 0.50,
                "issues": [],
            },
            {"id": "low-confidence-transcription"},
            {"totalTokens": 5},
        )


class MultiPageCalculationRecognition(FakeRecognition):
    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        assert question["type"] == "calculation"
        assert frame_set_id == "frame-set"
        assert all(fragment.template_image for fragment in fragments)
        assert all(fragment.student_image for fragment in fragments)
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "located",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [
                            {
                                "bbox": [100, 100, 300, 300],
                                "confidence": 0.99,
                                "issues": [],
                            },
                            {
                                "bbox": [600, 600, 800, 800],
                                "confidence": 0.98,
                                "issues": [],
                            },
                        ],
                    }
                    for fragment in fragments
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"multi-page-locator-{batch_index}"},
            {"totalTokens": len(fragments)},
        )

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        assert question["type"] == "calculation"
        assert all(region["template_image"].startswith(b"\xff\xd8") for region in regions)
        assert all(region["student_image"].startswith(b"\xff\xd8") for region in regions)
        segments = [
            {
                "region_index": index,
                "transcription": f"step-{index}",
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
            }
            for index in range(1, len(regions) + 1)
        ]
        return (
            {
                "transcription": "\n".join(
                    str(segment["transcription"]) for segment in segments
                ),
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
                "segments": segments,
            },
            {"id": "multi-page-transcription"},
            {"totalTokens": len(regions)},
        )


class BlankCalculationRecognition(FakeRecognition):
    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "blank",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [],
                    }
                    for fragment in fragments
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"blank-locator-{batch_index}"},
            {"totalTokens": 2},
        )


class LowConfidenceBlankCalculationRecognition(FakeRecognition):
    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragment.fragment_key,
                        "status": "blank",
                        "confidence": 0.70,
                        "issues": [],
                        "regions": [],
                    }
                    for fragment in fragments
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"low-confidence-blank-locator-{batch_index}"},
            {"totalTokens": 2},
        )


class PartiallyFailingCalculationRecognition(FakeRecognition):
    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        assert len(fragments) == 1
        if batch_index == 2:
            raise RuntimeError("second localization batch unavailable")
        return (
            normalize_calculation_localization_batch(
                [
                    {
                        "fragmentKey": fragments[0].fragment_key,
                        "status": "blank",
                        "confidence": 0.99,
                        "issues": [],
                        "regions": [],
                    }
                ],
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"partial-locator-{batch_index}"},
            {"totalTokens": 2},
        )


class MixedCalculationRecognition(FakeRecognition):
    def __init__(self, *, second_status: str = "uncertain") -> None:
        super().__init__()
        self.second_status = second_status

    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: tuple[CalculationSearchFragment, ...],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        self.calculation_location_calls += 1
        self.calculation_fragments.extend(fragments)
        assert len(fragments) == 2
        nodes = [
            {
                "fragmentKey": fragments[0].fragment_key,
                "status": "located",
                "confidence": 0.98,
                "issues": [],
                "regions": [
                    {
                        "bbox": [100, 100, 300, 300],
                        "confidence": 0.97,
                        "issues": [],
                    }
                ],
            },
            {
                "fragmentKey": fragments[1].fragment_key,
                "status": self.second_status,
                "confidence": 0.99 if self.second_status == "blank" else 0.80,
                "issues": [] if self.second_status == "blank" else ["ambiguous_marks"],
                "regions": [],
            },
        ]
        return (
            normalize_calculation_localization_batch(
                nodes,
                fragments,
                batch_index=batch_index,
                attempt_id=attempt_id,
                model_id="test-vl",
                prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            ),
            {"id": f"mixed-locator-{batch_index}"},
            {"totalTokens": 4},
        )


class BlockingRecognition(FakeRecognition):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        self.student_response_calls += 1
        self.started.set()
        await self.release.wait()
        assert question["number"] == "1"
        assert len(regions) == 1
        return (
            {
                "transcription": "stale-A",
                "is_blank": False,
                "confidence": 0.99,
                "issues": [],
            },
            {"id": "stale"},
            {"totalTokens": 5},
        )


class KeyedFillRecognition(FakeRecognition):
    def __init__(self, answer_count: int, *, mode: str = "ok") -> None:
        super().__init__()
        self.answer_count = answer_count
        self.mode = mode
        self.keyed_calls = 0
        self.captured: list[dict[str, Any]] = []

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        raise AssertionError("fill recognition must not use positional student-response segments")

    async def recognize_keyed_fill_response(
        self,
        question: dict[str, Any],
        blanks: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        *,
        frame_set_id: str,
        blank_config_version_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
        self.keyed_calls += 1
        assert [item["blankKey"] for item in blanks] == [
            f"B{index}" for index in range(1, self.answer_count + 1)
        ]
        request_text = repr((question, blanks))
        assert "SECRET_STANDARD" not in request_text
        assert "SECRET_SYNONYM" not in request_text
        assert "maxScore" not in request_text
        assert len(regions) == 1
        evidence_id = str(regions[0]["evidence_id"])
        self.captured.append(
            {
                "frameSetId": frame_set_id,
                "blankConfigVersionId": blank_config_version_id,
                "evidenceId": evidence_id,
                "regions": regions,
            }
        )
        answers = [
            {
                "blankKey": f"B{index}",
                "recognizedText": f"student-{index}",
                "isBlank": False,
                "confidence": 0.4 if self.mode == "low_confidence" and index == 2 else 0.96,
                "issues": [],
                "evidenceRefs": [evidence_id],
            }
            for index in range(self.answer_count, 0, -1)
        ]
        if self.mode == "missing_key":
            answers = [item for item in answers if item["blankKey"] != f"B{self.answer_count}"]
        elif self.mode == "extra_key":
            answers.append(
                {
                    "blankKey": f"B{self.answer_count + 1}",
                    "recognizedText": "extra",
                    "isBlank": False,
                    "confidence": 0.99,
                    "issues": [],
                    "evidenceRefs": [evidence_id],
                }
            )
        elif self.mode == "duplicate_key":
            answers.append(dict(answers[0]))
        elif self.mode == "service_review":
            return (
                {
                    "status": "recognition_needs_review",
                    "answers": [],
                    "issues": [{"code": "blank_key_missing"}],
                    "attemptCount": 2,
                },
                [{"id": "first"}, {"id": "retry"}],
                {"totalTokens": 8},
            )
        return (
            {
                "status": "recognized",
                "answers": answers,
                "issues": [],
                "attemptCount": 1,
            },
            [{"id": "keyed"}],
            {"totalTokens": 5},
        )


class BlockingKeyedFillRecognition(KeyedFillRecognition):
    def __init__(self, answer_count: int) -> None:
        super().__init__(answer_count)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def recognize_keyed_fill_response(
        self,
        question: dict[str, Any],
        blanks: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        *,
        frame_set_id: str,
        blank_config_version_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
        self.started.set()
        await self.release.wait()
        return await super().recognize_keyed_fill_response(
            question,
            blanks,
            regions,
            frame_set_id=frame_set_id,
            blank_config_version_id=blank_config_version_id,
        )


def test_fill_recognition_has_no_region_order_or_tight_box_selector() -> None:
    assert not hasattr(StudentPipeline, "_specific_fill_regions")


def _exam_page(path: Path, *, student: bool) -> None:
    image = Image.new("RGB", (400, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 380, 480), outline="black", width=3)
    for y in range(70, 430, 45):
        draw.line((45, y, 355, y), fill="black", width=2)
    draw.text((50, 40), "1. Question (   )", fill="black")
    if student:
        draw.text((170, 315), "A", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG" if path.suffix == ".png" else "JPEG")


def _calculation_alignment_map(
    tmp_path: Path,
    page_numbers: range | tuple[int, ...],
    *,
    prefix: str,
) -> dict[int, tuple[dict[str, Any], dict[str, Any], AlignmentResult]]:
    quality = AlignmentQuality(
        method="test",
        score=1.0,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments: dict[int, tuple[dict[str, Any], dict[str, Any], AlignmentResult]] = {}
    for page_number in page_numbers:
        template_path = tmp_path / f"{prefix}-template-{page_number}.jpg"
        student_path = tmp_path / f"{prefix}-student-{page_number}.jpg"
        _exam_page(template_path, student=False)
        _exam_page(student_path, student=True)
        alignments[page_number] = (
            {
                "id": f"{prefix}-template-page-{page_number}",
                "page_number": page_number,
                "image_path": template_path.name,
                "width": 400,
                "height": 500,
            },
            {
                "id": f"{prefix}-student-page-{page_number}",
                "page_number": page_number,
                "original_image_path": student_path.name,
                "width": 400,
                "height": 500,
                "alignment_revision_id": f"{prefix}-alignment-{page_number}",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(400, 500),
                PageSize(400, 500),
                quality,
            ),
        )
    return alignments


async def _direct_calculation_response(
    tmp_path: Path,
    recognition: CombinedCalculationRecognition,
) -> dict[str, Any]:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    alignments = _calculation_alignment_map(tmp_path, (1,), prefix=recognition.mode)
    question = {
        "id": "calculation-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Calculate",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "current-anchor",
                "template_page_id": f"{recognition.mode}-template-page-1",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.2,
                "sort_order": 0,
            }
        ],
    }
    return await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, recognition),
    )._recognize_calculation_response(question, [question], alignments, [1])


async def _direct_multi_batch_calculation_response(
    tmp_path: Path,
    recognition: CombinedCalculationRecognition,
) -> dict[str, Any]:
    settings = replace(settings_for(tmp_path), answer_pages_per_batch=2)
    settings.ensure_directories()
    alignments = _calculation_alignment_map(
        tmp_path,
        range(1, 6),
        prefix=recognition.mode,
    )
    current = {
        "id": "calculation-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Calculate",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "current-anchor",
                "template_page_id": f"{recognition.mode}-template-page-1",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.2,
                "sort_order": 0,
            }
        ],
    }
    next_question = {
        "id": "next-question",
        "sort_order": 1,
        "number": "2",
        "stem": "Next",
        "type": "single_choice",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "next-anchor",
                "template_page_id": f"{recognition.mode}-template-page-5",
                "page_number": 5,
                "x": 0.1,
                "y": 0.6,
                "width": 0.8,
                "height": 0.2,
                "sort_order": 0,
            }
        ],
    }
    return await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, recognition),
    )._recognize_calculation_response(
        current,
        [current, next_question],
        alignments,
        [1, 2, 3, 4, 5],
    )


def _confirmed_frame_set(
    connection: Any,
    *,
    task_id: str,
    frames: list[dict[str, Any]],
    timestamp: str,
) -> str:
    frame_set_id = f"{task_id}-confirmed-frame-set"
    connection.execute(
        """INSERT INTO question_frame_sets(
             id,task_id,version_number,status,revision,source,content_hash,created_by,
             created_at,updated_at,confirmed_at,confirmed_by
           ) VALUES(?,?,1,'confirmed',1,'teacher','test-hash','teacher',?,?,?,'teacher')""",
        (frame_set_id, task_id, timestamp, timestamp, timestamp),
    )
    connection.execute(
        "UPDATE tasks SET current_question_frame_set_id=? WHERE id=?",
        (frame_set_id, task_id),
    )
    for frame in frames:
        question_id = str(frame["question_id"])
        item_id = f"{frame_set_id}:{question_id}:item"
        connection.execute(
            """INSERT INTO question_frame_items(
                 id,frame_set_id,question_id,status,revision,issues_json,confirmed_at,
                 confirmed_by,created_at,updated_at
               ) VALUES(?,?,?,'confirmed',1,'[]',?,'teacher',?,?)""",
            (item_id, frame_set_id, question_id, timestamp, timestamp, timestamp),
        )
        for sort_order, region in enumerate(frame["regions"]):
            region_id = f"{frame_set_id}:{question_id}:region:{sort_order}"
            connection.execute(
                """INSERT INTO question_frame_regions(
                     id,frame_item_id,region_key,template_page_id,page_number,
                     coordinate_space,x,y,width,height,sort_order,source,confidence,
                     issues_json,raw_region_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'template_page_normalized',?,?,?,?,?,'teacher',1,
                     '[]',?,?,?)""",
                (
                    region_id,
                    item_id,
                    f"{question_id}:part:{sort_order + 1}",
                    region["template_page_id"],
                    region["page_number"],
                    region["x"],
                    region["y"],
                    region["width"],
                    region["height"],
                    sort_order,
                    json_dumps(region),
                    timestamp,
                    timestamp,
                ),
            )
    return frame_set_id


def _single_page_case(tmp_path: Path) -> tuple[Settings, Database, str]:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.migrate()
    timestamp = now_iso()
    _exam_page(tmp_path / "template.jpg", student=False)
    _exam_page(
        tmp_path / "uploads" / "task" / "students" / "submission" / "page.png",
        student=True,
    )

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Exam','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 1,'sha',1,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam',1,'template.jpg',400,500,'page-sha')"""
        )
        run_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES(?,'task','exam_recognition','succeeded','done',?)""",
            (run_id, timestamp),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 options_json,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status,answer_regions_json,question_regions_json
               ) VALUES('question','task',?,0,'1','1','Question','[]','single_choice',4,'[1]',
                 1,'[]','confirmed',?,?)""",
            (
                run_id,
                json_dumps(
                    [
                        {
                            "page_number": 1,
                            "x": 0.3,
                            "y": 0.58,
                            "width": 0.4,
                            "height": 0.12,
                        }
                    ]
                ),
                "[]",
            ),
        )
        frame_set_id = _confirmed_frame_set(
            connection,
            task_id="task",
            frames=[
                {
                    "question_id": "question",
                    "regions": [
                        {
                            "template_page_id": "template-page",
                            "page_number": 1,
                            "x": 0.1,
                            "y": 0.08,
                            "width": 0.8,
                            "height": 0.62,
                        }
                    ],
                }
            ],
            timestamp=timestamp,
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,original_name,mime_type,size_bytes,sha256,relative_path,
                 status,created_at,updated_at
               ) VALUES('submission','task','Student','page.png','image/png',1,'student-sha',
                 'uploads/task/students/submission/page.png','uploaded',?,?)""",
            (timestamp, timestamp),
        )
    return settings, database, frame_set_id


def _blank_config_versions(database: Database, frame_set_id: str) -> tuple[str, str]:
    timestamp = now_iso()
    first_id = "blank-config-v1"
    second_id = "blank-config-v2"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('question','single_choice','4',1,?)""",
            (timestamp,),
        )
        for version_number, version_id in enumerate((first_id, second_id), 1):
            connection.execute(
                """INSERT INTO question_blank_config_versions(
                     id,question_id,version_number,frame_set_id,status,source,signals_json,
                     blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                     confirmed_at,confirmed_by
                   ) VALUES(?,'question',?,?,'teacher_confirmed','teacher','{}','[]','[]',?,
                     'teacher',?,?,?,'teacher')""",
                (
                    version_id,
                    version_number,
                    frame_set_id,
                    f"config-hash-{version_number}",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            """UPDATE question_grading_configs
               SET current_blank_config_version_id=? WHERE question_id='question'""",
            (first_id,),
        )
    return first_id, second_id


def _fill_blank_case(
    tmp_path: Path,
    blank_count: int,
    *,
    config_status: str | None = "teacher_confirmed",
    config_frame_set_id: str | None = None,
    reference_answer: str | None = None,
) -> tuple[Settings, Database, str, str | None]:
    settings, database, frame_set_id = _single_page_case(tmp_path)
    timestamp = now_iso()
    version_id = f"fill-config-{blank_count}-{config_status}" if config_status else None
    with database.transaction() as connection:
        if config_frame_set_id:
            connection.execute(
                """INSERT INTO question_frame_sets(
                     id,task_id,version_number,status,revision,source,content_hash,created_by,
                     created_at,updated_at
                   ) VALUES(?, 'task', 2, 'draft', 0, 'teacher', 'other-frame-hash',
                     'teacher', ?, ?)""",
                (config_frame_set_id, timestamp, timestamp),
            )
        connection.execute(
            """UPDATE questions SET question_type='fill_blank',score=?,stem=?,
                      answer_regions_json=? WHERE id='question'""",
            (
                str(blank_count),
                "Runtime fill question " + " ".join("____" for _ in range(blank_count)),
                # This deliberately points outside the confirmed frame. The fill
                # path must ignore it and use the complete confirmed frame only.
                json_dumps(
                    [
                        {
                            "page_number": 99,
                            "x": 0.0,
                            "y": 0.0,
                            "width": 0.01,
                            "height": 0.01,
                        }
                    ]
                ),
            ),
        )
        if reference_answer is not None:
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,teacher_answer,updated_at
                   ) VALUES('fill-match','task','question','manual','confirmed',?,?)""",
                (reference_answer, timestamp),
            )
        if version_id:
            confirmed_at = (
                timestamp
                if config_status in {"auto_confirmed", "teacher_confirmed"}
                else None
            )
            connection.execute(
                """INSERT INTO question_blank_config_versions(
                     id,question_id,version_number,frame_set_id,status,source,signals_json,
                     blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                     confirmed_at,confirmed_by
                   ) VALUES(?,'question',1,?,?,'teacher','{}','[]','[]','fill-hash',
                     'teacher',?,?,?,'teacher')""",
                (
                    version_id,
                    config_frame_set_id or frame_set_id,
                    config_status,
                    timestamp,
                    timestamp,
                    confirmed_at,
                ),
            )
            connection.execute(
                """INSERT INTO question_grading_configs(
                     question_id,question_type,max_score,config_version,
                     current_blank_config_version_id,updated_at
                   ) VALUES('question','fill_blank',?,1,?,?)""",
                (str(blank_count), version_id, timestamp),
            )
            for index in range(1, blank_count + 1):
                x = 0.15 + (index - 1) * 0.1
                connection.execute(
                    """INSERT INTO question_blank_definition_versions(
                         id,blank_config_version_id,blank_key,sort_order,max_score,answer_kind,
                         standard_answers_json,synonyms_json,template_page_id,page_number,
                         coordinate_space,x,y,width,height,anchor_source,anchor_confidence,
                         anchor_issues_json,anchor_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'text',?,?,?,?,'template_page_normalized',
                         ?,?,?,?,'teacher',1,'[]',?,?,?)""",
                    (
                        f"{version_id}:definition:{index}",
                        version_id,
                        f"B{index}",
                        index - 1,
                        "1",
                        json_dumps([f"SECRET_STANDARD_{index}"]),
                        json_dumps([f"SECRET_SYNONYM_{index}"]),
                        "template-page",
                        1,
                        x,
                        0.2,
                        0.08,
                        0.04,
                        json_dumps({"fragmentKey": f"runtime-fragment-{index}"}),
                        timestamp,
                        timestamp,
                    ),
                )
    return settings, database, frame_set_id, version_id


@pytest.mark.asyncio
async def test_student_processing_auto_confirms_safe_missing_fill_config(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id, _version_id = _fill_blank_case(
        tmp_path,
        3,
        config_status=None,
        reference_answer="alpha; beta; gamma",
    )
    recognition = KeyedFillRecognition(3)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    assert recognition.keyed_calls == 1
    version = database.fetchone(
        "SELECT id,frame_set_id,status,source FROM question_blank_config_versions"
    )
    assert version is not None
    assert version["frame_set_id"] == frame_set_id
    assert version["status"] == "auto_confirmed"
    assert version["source"] == "model"
    scores = database.fetchall(
        """SELECT max_score FROM question_blank_definition_versions
           WHERE blank_config_version_id=? ORDER BY sort_order""",
        (version["id"],),
    )
    assert [row["max_score"] for row in scores] == ["1.00", "1.00", "1.00"]
    audit = database.fetchone(
        """SELECT payload_json FROM audit_events
           WHERE event_type='fill_blank_config_auto_confirmed'
           ORDER BY created_at DESC LIMIT 1"""
    )
    assert audit is not None
    assert json_loads(audit["payload_json"], {})["modelCalls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
async def test_fill_pipeline_persists_arbitrary_keyed_blank_count_from_full_frame(
    tmp_path: Path,
    blank_count: int,
) -> None:
    settings, database, frame_set_id, version_id = _fill_blank_case(tmp_path, blank_count)
    assert version_id is not None
    recognition = KeyedFillRecognition(blank_count)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    assert recognition.keyed_calls == 1
    assert recognition.student_response_calls == 0
    assert recognition.captured[0]["frameSetId"] == frame_set_id
    assert recognition.captured[0]["blankConfigVersionId"] == version_id
    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["frame_set_id"] == frame_set_id
    assert response["blank_config_version_id"] == version_id
    assert response["status"] == "recognized"
    assert response["recognized_text"].startswith("B1=student-1")
    raw = json_loads(response["raw_recognition_json"], {})
    assert raw["notForFillScoring"] is True
    assert raw["summaryOnly"] is True
    evidence = database.fetchall(
        """SELECT id FROM student_response_regions
           WHERE student_response_id=? ORDER BY sort_order""",
        (response["id"],),
    )
    assert evidence == [{"id": recognition.captured[0]["evidenceId"]}]
    blank_rows = database.fetchall(
        """SELECT b.*,d.sort_order FROM student_blank_responses b
           JOIN question_blank_definition_versions d ON d.id=b.blank_definition_id
           WHERE b.student_response_id=? ORDER BY d.sort_order""",
        (response["id"],),
    )
    assert [row["blank_key"] for row in blank_rows] == [
        f"B{index}" for index in range(1, blank_count + 1)
    ]
    for index, row in enumerate(blank_rows, 1):
        assert row["recognized_text"] == f"student-{index}"
        assert row["status"] == "recognized"
        assert row["recognition_model_id"] == "test-vl"
        assert row["prompt_version"] == KEYED_FILL_RESPONSE_PROMPT_VERSION
        assert row["frame_set_id"] == frame_set_id
        assert row["blank_config_version_id"] == version_id
        assert row["processing_revision_id"] == response["processing_revision_id"]
        assert json_loads(row["evidence_refs_json"], []) == [evidence[0]["id"]]
    processing = database.fetchone(
        "SELECT status FROM student_processing_revisions WHERE id=?",
        (response["processing_revision_id"],),
    )
    assert processing == {"status": "ready"}
    assert database.fetchall("PRAGMA foreign_key_check") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("config_status", [None, "pending"])
async def test_fill_without_current_confirmed_config_needs_review_without_model(
    tmp_path: Path,
    config_status: str | None,
) -> None:
    settings, database, _frame_set_id, _version_id = _fill_blank_case(
        tmp_path,
        3,
        config_status=config_status,
    )
    recognition = KeyedFillRecognition(3)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    assert recognition.keyed_calls == 0
    assert recognition.student_response_calls == 0
    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["status"] == "needs_review"
    assert response["blank_config_version_id"] is None
    assert response["recognition_model_id"] is None
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_blank_responses"
    ) == {"count": 0}
    raw = json_loads(response["raw_recognition_json"], {})
    assert raw["issues"][0]["code"] == (
        "fill_blank_config_missing"
        if config_status is None
        else "fill_blank_config_not_confirmed"
    )
    processing = database.fetchone(
        "SELECT status FROM student_processing_revisions WHERE id=?",
        (response["processing_revision_id"],),
    )
    assert processing == {"status": "recognition_needs_review"}


@pytest.mark.asyncio
async def test_fill_config_from_another_confirmed_frame_set_is_never_sent_to_model(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id, version_id = _fill_blank_case(
        tmp_path,
        2,
        config_frame_set_id="different-confirmed-frame-set",
    )
    assert version_id is not None
    recognition = KeyedFillRecognition(2)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    assert recognition.keyed_calls == 0
    response = database.fetchone(
        "SELECT blank_config_version_id,recognition_model_id,raw_recognition_json "
        "FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["blank_config_version_id"] is None
    assert response["recognition_model_id"] is None
    assert json_loads(response["raw_recognition_json"], {})["issues"] == [
        {"code": "fill_blank_config_frame_mismatch"}
    ]
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_blank_responses"
    ) == {"count": 0}
    assert database.fetchone(
        "SELECT frame_set_id FROM student_processing_revisions "
        "WHERE id=(SELECT processing_revision_id FROM student_responses "
        "WHERE submission_id='submission')"
    ) == {"frame_set_id": frame_set_id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["missing_key", "extra_key", "duplicate_key", "service_review"],
)
async def test_fill_key_set_mismatch_fails_closed_without_partial_blank_rows(
    tmp_path: Path,
    mode: str,
) -> None:
    settings, database, _frame_set_id, version_id = _fill_blank_case(tmp_path, 3)
    recognition = KeyedFillRecognition(3, mode=mode)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["status"] == "needs_review"
    assert response["blank_config_version_id"] == version_id
    assert response["recognized_text"] == ""
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_blank_responses"
    ) == {"count": 0}
    raw = json_loads(response["raw_recognition_json"], {})
    assert any(issue.get("code") == "fill_response_key_mismatch" for issue in raw["issues"])


@pytest.mark.asyncio
async def test_fill_low_confidence_is_persisted_per_key_and_routes_to_review(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id, _version_id = _fill_blank_case(tmp_path, 3)
    recognition = KeyedFillRecognition(3, mode="low_confidence")

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["status"] == "needs_review"
    assert response["confidence"] == pytest.approx(0.4)
    blank_rows = database.fetchall(
        """SELECT blank_key,status,confidence FROM student_blank_responses
           WHERE student_response_id=? ORDER BY blank_key""",
        (response["id"],),
    )
    assert [row["status"] for row in blank_rows] == [
        "recognized",
        "needs_review",
        "recognized",
    ]
    assert blank_rows[1]["blank_key"] == "B2"
    assert blank_rows[1]["confidence"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_fill_rerun_preserves_versioned_blank_response_generations(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id, version_id = _fill_blank_case(tmp_path, 2)
    recognition = KeyedFillRecognition(2)
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, recognition))

    await pipeline.run("submission")
    await pipeline.run("submission")

    responses = database.fetchall(
        """SELECT id,processing_revision_id,frame_set_id,blank_config_version_id
           FROM student_responses WHERE submission_id='submission'
           ORDER BY created_at,id"""
    )
    assert len(responses) == 2
    assert len({row["processing_revision_id"] for row in responses}) == 2
    assert {row["frame_set_id"] for row in responses} == {frame_set_id}
    assert {row["blank_config_version_id"] for row in responses} == {version_id}
    blank_rows = database.fetchall(
        """SELECT student_response_id,processing_revision_id,blank_key
           FROM student_blank_responses ORDER BY student_response_id,blank_key"""
    )
    assert len(blank_rows) == 4
    assert {row["student_response_id"] for row in blank_rows} == {
        row["id"] for row in responses
    }
    assert {row["blank_key"] for row in blank_rows} == {"B1", "B2"}
    assert recognition.keyed_calls == 2
    assert database.fetchall("PRAGMA foreign_key_check") == []


@pytest.mark.asyncio
async def test_teacher_alignment_revision_resumes_keyed_recognition_without_realigning(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id, version_id = _fill_blank_case(tmp_path, 2)
    initial_recognition = KeyedFillRecognition(2)
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, initial_recognition),
    ).run("submission")
    student_page = database.fetchone(
        "SELECT id FROM student_pages WHERE submission_id='submission'"
    )
    assert student_page is not None
    override = AlignmentOverrideService(database).apply(
        "submission",
        str(student_page["id"]),
        expected_revision=1,
        template_page_id="template-page",
        control_points=[
            {"template": {"x": 0, "y": 0}, "student": {"x": 0, "y": 0}},
            {"template": {"x": 400, "y": 0}, "student": {"x": 400, "y": 0}},
            {"template": {"x": 400, "y": 500}, "student": {"x": 400, "y": 500}},
            {"template": {"x": 0, "y": 500}, "student": {"x": 0, "y": 500}},
        ],
        clear_override=False,
        actor="teacher",
    )
    teacher_revision_id = str(override["processingRevisionId"])
    database.execute(
        """UPDATE student_page_alignment_revisions SET quality=0.60,status='aligned'
           WHERE processing_revision_id=?""",
        (teacher_revision_id,),
    )
    resumed_recognition = KeyedFillRecognition(2)

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, resumed_recognition),
    ).resume_current_recognition("submission")

    assert resumed_recognition.keyed_calls == 1
    revisions = database.fetchall(
        """SELECT id,revision_number,source,status,is_current
           FROM student_processing_revisions WHERE submission_id='submission'
           ORDER BY revision_number"""
    )
    assert revisions[1] == {
        "id": teacher_revision_id,
        "revision_number": 2,
        "source": "teacher",
        "status": "ready",
        "is_current": 1,
    }
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_page_alignment_revisions"
    ) == {"count": 2}
    current_responses = database.fetchall(
        """SELECT id,frame_set_id,blank_config_version_id,processing_revision_id
           FROM student_responses WHERE processing_revision_id=?""",
        (teacher_revision_id,),
    )
    assert len(current_responses) == 1
    assert current_responses[0]["frame_set_id"] == frame_set_id
    assert current_responses[0]["blank_config_version_id"] == version_id
    current_blanks = database.fetchall(
        """SELECT blank_key,processing_revision_id FROM student_blank_responses
           WHERE student_response_id=? ORDER BY blank_key""",
        (current_responses[0]["id"],),
    )
    assert current_blanks == [
        {"blank_key": "B1", "processing_revision_id": teacher_revision_id},
        {"blank_key": "B2", "processing_revision_id": teacher_revision_id},
    ]
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_question_regions"
    ) == {"count": 2}
    assert database.fetchall("PRAGMA foreign_key_check") == []


@pytest.mark.asyncio
async def test_late_fill_recognition_cannot_commit_after_config_pointer_changes(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id, _version_id = _fill_blank_case(tmp_path, 2)
    recognition = BlockingKeyedFillRecognition(2)
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, recognition))

    processing_task = asyncio.create_task(pipeline.run("submission"))
    await asyncio.wait_for(recognition.started.wait(), timeout=5)
    database.execute(
        """UPDATE question_grading_configs SET current_blank_config_version_id=NULL
           WHERE question_id='question'"""
    )
    recognition.release.set()
    await processing_task

    revision = database.fetchone(
        """SELECT status,is_current,issues_json FROM student_processing_revisions
           WHERE submission_id='submission'"""
    )
    assert revision is not None
    assert revision["status"] == "failed"
    assert revision["is_current"] == 0
    assert any(
        "blank_config_changed" in issue.get("details", {}).get("reason", "")
        for issue in json_loads(revision["issues_json"], [])
    )
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_responses") == {
        "count": 0
    }
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_blank_responses"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_missing_confirmed_frame_set_stops_before_render_or_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Exam','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,original_name,mime_type,size_bytes,sha256,relative_path,
                 status,created_at,updated_at
               ) VALUES('blocked','task','page.png','image/png',1,'sha','missing.png',
                 'uploaded',?,?)""",
            (timestamp, timestamp),
        )

    render_calls = 0

    async def fail_if_rendered(
        _pipeline: StudentPipeline,
        _submission: dict[str, Any],
    ) -> list[dict[str, Any]]:
        nonlocal render_calls
        render_calls += 1
        raise AssertionError("rendering must start after the frame gate")

    monkeypatch.setattr(StudentPipeline, "_render_staged_pages", fail_if_rendered)
    recognition = FakeRecognition()
    await StudentPipeline(settings, database, cast(RecognitionService, recognition)).run("blocked")

    blocked = database.fetchone("SELECT * FROM student_submissions WHERE id='blocked'")
    assert blocked is not None
    assert blocked["status"] == "failed"
    assert blocked["error_code"] == "QUESTION_FRAMES_NOT_CONFIRMED"
    assert blocked["question_region_error_code"] == "QUESTION_FRAMES_NOT_CONFIRMED"
    assert render_calls == 0
    assert recognition.question_region_calls == 0
    assert recognition.template_region_calls == 0
    assert recognition.student_response_calls == 0


@pytest.mark.asyncio
async def test_pipeline_keeps_original_page_coordinates_and_transcribes_region(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id = _single_page_case(tmp_path)

    recognition = FakeRecognition()
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, recognition))
    await pipeline.run("submission")

    submission = database.fetchone("SELECT * FROM student_submissions WHERE id='submission'")
    assert submission is not None
    assert submission["status"] == "ready"
    page = database.fetchone("SELECT * FROM student_pages WHERE submission_id='submission'")
    assert page is not None
    assert page["original_image_path"].startswith("pages/task/student-submission-")
    assert json_loads(page["alignment_transform_json"], None) is not None
    response = database.fetchone("SELECT * FROM student_responses WHERE submission_id='submission'")
    assert response is not None
    assert response["recognized_text"] == "A"
    assert response["confidence"] == pytest.approx(0.97)
    region = database.fetchone(
        "SELECT * FROM student_response_regions WHERE student_response_id=?",
        (response["id"],),
    )
    assert region is not None
    assert region["cropped_image_path"] is None
    template_box = json_loads(region["template_bbox_json"], {})
    student_box = json_loads(region["student_bbox_json"], {})
    assert template_box == {"x": 40.0, "y": 40.0, "width": 320.0, "height": 310.0}
    assert student_box["x"] == pytest.approx(40, abs=5)
    assert student_box["y"] == pytest.approx(40, abs=5)
    question_region = database.fetchone(
        "SELECT * FROM student_question_regions WHERE submission_id='submission'"
    )
    assert question_region is not None
    assert question_region["student_page_id"] == page["id"]
    assert question_region["frame_set_id"] == frame_set_id
    assert question_region["frame_region_id"] == f"{frame_set_id}:question:region:0"
    assert question_region["processing_revision_id"]
    assert question_region["alignment_revision_id"]
    assert json_loads(question_region["template_region_json"], {})["width"] == 0.8
    polygon = json_loads(question_region["student_polygon_json"], [])
    assert len(polygon) == 4
    assert polygon[0]["x"] == pytest.approx(40, abs=5)
    assert polygon[0]["y"] == pytest.approx(40, abs=5)
    assert submission["question_region_status"] == "ready"
    assert recognition.question_region_calls == 0
    assert recognition.template_region_calls == 0
    original_page_id = page["id"]
    original_response_id = response["id"]
    original_revision_id = response["processing_revision_id"]
    assert original_revision_id == submission["current_processing_revision_id"]
    original_revision = database.fetchone(
        "SELECT * FROM student_processing_revisions WHERE id=?",
        (original_revision_id,),
    )
    assert original_revision is not None
    assert original_revision["frame_set_id"] == frame_set_id
    assert original_revision["status"] == "ready"
    assert original_revision["is_current"] == 1
    assert original_revision["input_hash"]
    alignment = database.fetchone(
        """SELECT * FROM student_page_alignment_revisions
           WHERE processing_revision_id=? AND student_page_id=? AND is_current=1""",
        (original_revision_id, original_page_id),
    )
    assert alignment is not None
    assert question_region["alignment_revision_id"] == alignment["id"]

    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
               ) VALUES('grading-v1','submission','task','completed','done','grade-input',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_question_results(
                 id,grading_run_id,question_id,student_response_id,input_hash,question_type,
                 status,max_score,created_at,updated_at
               ) VALUES('grade-result-v1','grading-v1','question',?,'question-input',
                 'single_choice','final','4',?,?)""",
            (original_response_id, timestamp, timestamp),
        )

    rerun_recognition = FakeRecognition()
    rerun = StudentPipeline(
        settings,
        database,
        cast(RecognitionService, rerun_recognition),
    )
    await rerun.run("submission")

    pages = database.fetchall(
        "SELECT id,original_image_path,sha256 FROM student_pages WHERE submission_id='submission'"
    )
    assert pages == [
        {
            "id": original_page_id,
            "original_image_path": page["original_image_path"],
            "sha256": page["sha256"],
        }
    ]
    revisions = database.fetchall(
        """SELECT id,revision_number,frame_set_id,status,input_hash,is_current
           FROM student_processing_revisions WHERE submission_id='submission'
           ORDER BY revision_number"""
    )
    assert len(revisions) == 2
    assert revisions[0] == {
        "id": original_revision_id,
        "revision_number": 1,
        "frame_set_id": frame_set_id,
        "status": "ready",
        "input_hash": original_revision["input_hash"],
        "is_current": 0,
    }
    assert revisions[1]["revision_number"] == 2
    assert revisions[1]["frame_set_id"] == frame_set_id
    assert revisions[1]["status"] == "ready"
    assert revisions[1]["input_hash"] == original_revision["input_hash"]
    assert revisions[1]["is_current"] == 1
    current_revision_id = revisions[1]["id"]
    response_generations = database.fetchall(
        """SELECT id,processing_revision_id,frame_set_id FROM student_responses
           WHERE submission_id='submission' ORDER BY created_at,id"""
    )
    assert len(response_generations) == 2
    assert {row["processing_revision_id"] for row in response_generations} == {
        original_revision_id,
        current_revision_id,
    }
    assert {row["frame_set_id"] for row in response_generations} == {frame_set_id}
    assert database.fetchone(
        "SELECT student_response_id FROM grading_question_results WHERE id='grade-result-v1'"
    ) == {"student_response_id": original_response_id}
    assert database.fetchall("PRAGMA foreign_key_check") == []
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_response_regions"
    ) == {"count": 2}
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_question_regions WHERE submission_id='submission'"
    ) == {"count": 2}
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_page_alignment_revisions"
    ) == {"count": 2}
    current_submission = database.fetchone(
        "SELECT current_processing_revision_id FROM student_submissions WHERE id='submission'"
    )
    assert current_submission == {"current_processing_revision_id": current_revision_id}
    assert rerun_recognition.question_region_calls == 0
    assert rerun_recognition.template_region_calls == 0

    database.execute("UPDATE student_submissions SET status='uploaded' WHERE id='submission'")
    failing_recognition = FailingRecognition()
    failing = StudentPipeline(settings, database, cast(RecognitionService, failing_recognition))
    await failing.run("submission")
    failed = database.fetchone("SELECT * FROM student_submissions WHERE id='submission'")
    assert failed is not None
    assert failed["status"] == "failed"
    assert database.fetchall(
        "SELECT id FROM student_pages WHERE submission_id='submission'"
    ) == [{"id": original_page_id}]
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_responses") == {"count": 2}
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_response_regions") == {
        "count": 2
    }
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_question_regions") == {
        "count": 2
    }
    failed_revision = database.fetchone(
        """SELECT status,is_current,issues_json FROM student_processing_revisions
           WHERE id=(SELECT current_processing_revision_id FROM student_submissions
                     WHERE id='submission')"""
    )
    assert failed_revision is not None
    assert failed_revision["status"] == "failed"
    assert failed_revision["is_current"] == 1
    assert json_loads(failed_revision["issues_json"], [])
    assert failing_recognition.question_region_calls == 0
    assert failing_recognition.template_region_calls == 0


@pytest.mark.asyncio
async def test_student_question_recognition_runs_at_most_three_and_preserves_order(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for(tmp_path), student_recognition_concurrency=3)
    pipeline = RecognitionConcurrencyProbePipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, FakeRecognition()),
    )
    questions = [
        {"id": f"question-{number}", "number": str(number), "type": "single_choice"}
        for number in range(1, 7)
    ]

    responses = await pipeline._recognize_responses(
        questions,
        {},
        {},
        uploaded_student_page_numbers=[],
        allow_non_calculation=True,
    )

    assert pipeline.peak_questions == 3
    assert pipeline.completed != [str(number) for number in range(1, 7)]
    assert [response["question_number"] for response in responses] == [
        str(number) for number in range(1, 7)
    ]


@pytest.mark.asyncio
async def test_student_question_recognition_drains_workers_before_raising(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for(tmp_path), student_recognition_concurrency=3)
    pipeline = RecognitionConcurrencyProbePipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, FakeRecognition()),
        fail_number="2",
    )
    questions = [
        {"id": f"question-{number}", "number": str(number), "type": "single_choice"}
        for number in range(1, 7)
    ]

    with pytest.raises(RuntimeError, match="question 2 failed"):
        await pipeline._recognize_responses(
            questions,
            {},
            {},
            uploaded_student_page_numbers=[],
            allow_non_calculation=True,
        )

    assert pipeline.active_questions == 0
    assert sorted(pipeline.started) == [str(number) for number in range(1, 7)]
    assert sorted(pipeline.completed) == [str(number) for number in range(1, 7)]


@pytest.mark.asyncio
async def test_three_question_parallelism_is_faster_than_sixty_percent_of_serial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "homework_judge.jobs.student_pipeline.log_event",
        lambda *_args, **_kwargs: None,
    )
    questions = [
        {"id": f"question-{number}", "number": str(number), "type": "single_choice"}
        for number in range(1, 16)
    ]

    async def measure(concurrency: int, run: int) -> tuple[float, int]:
        run_path = tmp_path / f"run-{concurrency}-{run}"
        run_path.mkdir()
        settings = replace(
            settings_for(run_path),
            student_recognition_concurrency=concurrency,
        )
        pipeline = RecognitionConcurrencyProbePipeline(
            settings,
            Database(settings.database_path),
            cast(RecognitionService, FakeRecognition()),
            staggered=False,
        )
        started = time.perf_counter()
        await pipeline._recognize_responses(
            questions,
            {},
            {},
            uploaded_student_page_numbers=[],
            allow_non_calculation=True,
        )
        return time.perf_counter() - started, pipeline.peak_questions

    for run in range(3):
        serial_elapsed, serial_peak = await measure(1, run)
        parallel_elapsed, parallel_peak = await measure(3, run)
        assert serial_peak == 1
        assert parallel_peak == 3
        assert parallel_elapsed <= serial_elapsed * 0.6


@pytest.mark.asyncio
async def test_calculation_combined_success_skips_legacy_calls(tmp_path: Path) -> None:
    recognition = CombinedCalculationRecognition()

    response = await _direct_calculation_response(tmp_path, recognition)

    assert response["status"] == "recognized"
    assert response["recognized_text"] == "x = 42"
    assert recognition.calculation_recognition_calls == 1
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 0
    raw = response["raw_recognition"]
    assert raw["recognitionPath"] == "single_pass"
    assert raw["localization"]["recognitionPath"] == "single_pass"
    assert raw["localization"]["batches"][0]["recognitionPath"] == "single_pass"
    assert raw["localization"]["requestCounts"]["total"] == 1
    assert raw["usage"]["totalTokens"] == 7


@pytest.mark.asyncio
async def test_three_single_batch_calculations_save_three_model_requests(
    tmp_path: Path,
) -> None:
    optimized_requests = 0
    for index in range(3):
        case_path = tmp_path / f"calculation-{index}"
        case_path.mkdir()
        response = await _direct_calculation_response(
            case_path,
            CombinedCalculationRecognition(),
        )
        optimized_requests += int(
            response["raw_recognition"]["localization"]["requestCounts"]["total"]
        )

    legacy_two_step_requests = 3 * 2
    assert optimized_requests == 3
    assert legacy_two_step_requests - optimized_requests == 3


@pytest.mark.asyncio
async def test_calculation_combined_missing_text_reuses_location(tmp_path: Path) -> None:
    recognition = CombinedCalculationRecognition("missing_transcription")

    response = await _direct_calculation_response(tmp_path, recognition)

    assert response["status"] == "recognized"
    assert response["recognized_text"] == "legacy-1"
    assert recognition.calculation_recognition_calls == 1
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 1
    assert response["raw_recognition"]["recognitionPath"] == "transcription_fallback"
    assert response["raw_recognition"]["localization"]["requestCounts"]["total"] == 2
    assert response["raw_recognition"]["usage"]["totalTokens"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["request_failure", "invalid_location"])
async def test_calculation_combined_location_failure_uses_full_fallback(
    tmp_path: Path,
    mode: str,
) -> None:
    recognition = CombinedCalculationRecognition(mode)

    response = await _direct_calculation_response(tmp_path, recognition)

    assert response["status"] == "recognized"
    assert recognition.calculation_recognition_calls == 1
    assert recognition.calculation_location_calls == 1
    assert recognition.student_response_calls == 1
    assert response["raw_recognition"]["recognitionPath"] == "full_fallback"
    batch = response["raw_recognition"]["localization"]["batches"][0]
    assert batch["legacyLocalization"] is not None
    assert response["raw_recognition"]["localization"]["requestCounts"]["total"] == 3


@pytest.mark.asyncio
async def test_calculation_combined_reliable_blank_skips_all_legacy_calls(
    tmp_path: Path,
) -> None:
    recognition = CombinedCalculationRecognition("blank")

    response = await _direct_calculation_response(tmp_path, recognition)

    assert response["status"] == "recognized"
    assert response["recognized_text"] == ""
    assert recognition.calculation_recognition_calls == 1
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 0
    assert response["raw_recognition"]["recognitionPath"] == "reliable_blank"
    assert response["raw_recognition"]["isBlank"] is True
    assert response["raw_recognition"]["localization"]["requestCounts"]["total"] == 1


@pytest.mark.asyncio
async def test_calculation_combined_low_confidence_routes_to_review_without_fallback(
    tmp_path: Path,
) -> None:
    recognition = CombinedCalculationRecognition("low_confidence")

    response = await _direct_calculation_response(tmp_path, recognition)

    assert response["status"] == "needs_review"
    assert response["confidence"] == pytest.approx(0.5)
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 0
    assert response["raw_recognition"]["recognitionPath"] == "single_pass"
    assert response["raw_recognition"]["localization"]["requestCounts"]["total"] == 1


@pytest.mark.asyncio
async def test_calculation_combined_multi_batch_falls_back_only_unresolved_regions(
    tmp_path: Path,
) -> None:
    recognition = CombinedCalculationRecognition("mixed_batches")

    response = await _direct_multi_batch_calculation_response(tmp_path, recognition)

    assert response["status"] == "recognized"
    assert recognition.calculation_recognition_calls == 3
    assert recognition.calculation_location_calls == 1
    assert recognition.student_response_calls == 1
    raw = response["raw_recognition"]
    assert raw["recognitionPath"] == "full_fallback"
    assert raw["localization"]["requestCounts"] == {
        "fast": 3,
        "legacyLocalization": 1,
        "legacyTranscription": 1,
        "total": 5,
    }
    assert [
        batch["recognitionPath"] for batch in raw["localization"]["batches"]
    ] == ["single_pass", "transcription_fallback", "full_fallback"]
    assert len(raw["segments"]) == 5
    assert [segment["region_index"] for segment in raw["segments"]] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_calculation_uses_teacher_frame_as_anchor_and_locates_work_below_it(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            """UPDATE questions SET question_type='calculation',answer_regions_json=?
               WHERE id='question'""",
            (
                json_dumps(
                    [
                        {
                            # A deliberately unusable detector crop proves that
                            # calculation work expands to the confirmed full frame.
                            "page_number": 99,
                            "x": 0.0,
                            "y": 0.0,
                            "width": 0.01,
                            "height": 0.01,
                        }
                    ]
                ),
            ),
        )
        connection.execute(
            """UPDATE question_frame_regions SET height=0.20
               WHERE frame_item_id LIKE '%:question:item'"""
        )

    recognition = FakeRecognition()
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["recognized_text"] == "A"
    region = database.fetchone(
        "SELECT * FROM student_response_regions WHERE student_response_id=?",
        (response["id"],),
    )
    assert region is not None
    template_box = json_loads(region["template_bbox_json"], {})
    assert template_box == {"x": 136.0, "y": 293.0, "width": 88.0, "height": 64.0}
    assert template_box["y"] > 140.0  # The teacher frame ends at y=0.28*500.
    raw = json_loads(response["raw_recognition_json"], {})
    localization = raw["localization"]
    assert localization["schemaVersion"] == 1
    assert localization["evidenceComplete"] is True
    assert localization["plan"]["fragments"][0]["box"] == {
        "x": 0.0,
        "y": 0.08,
        "width": 1.0,
        "height": 0.92,
    }
    assert {item["evidenceId"] for item in localization["evidence"]} == {region["id"]}
    assert localization["evidence"][0]["evidenceKind"] == "located_region"
    assert recognition.student_response_calls == 1

    assert recognition.calculation_location_calls == 1


@pytest.mark.asyncio
async def test_calculation_merges_successful_multi_page_batches_until_next_anchor(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for(tmp_path), answer_pages_per_batch=2)
    settings.ensure_directories()
    recognition = MultiPageCalculationRecognition()
    alignments = _calculation_alignment_map(
        tmp_path,
        range(1, 7),
        prefix="multi-success",
    )
    current = {
        "id": "calculation-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Calculate",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "current-anchor",
                "template_page_id": "multi-success-template-page-1",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.2,
                "sort_order": 0,
            }
        ],
    }
    next_question = {
        "id": "next-question",
        "sort_order": 1,
        "number": "2",
        "stem": "Next",
        "type": "single_choice",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "next-anchor",
                "template_page_id": "multi-success-template-page-5",
                "page_number": 5,
                "x": 0.1,
                "y": 0.6,
                "width": 0.8,
                "height": 0.2,
                "sort_order": 0,
            }
        ],
    }

    response = await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, recognition),
    )._recognize_calculation_response(
        current,
        [current, next_question],
        alignments,
        [1, 2, 3, 4, 5, 6],
    )

    assert response["status"] == "recognized"
    assert recognition.calculation_location_calls == 3
    assert recognition.student_response_calls == 1
    assert [fragment.page_number for fragment in recognition.calculation_fragments] == [
        1,
        2,
        3,
        4,
        5,
    ]
    raw = response["raw_recognition"]
    localization = raw["localization"]
    assert localization["evidenceComplete"] is True
    assert localization["plan"]["nextQuestionId"] == "next-question"
    assert localization["plan"]["submissionLastPageNumber"] == 6
    assert [item["pageNumber"] for item in localization["plan"]["fragments"]] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert localization["plan"]["fragments"][0]["box"] == {
        "x": 0.0,
        "y": 0.1,
        "width": 1.0,
        "height": 0.9,
    }
    assert localization["plan"]["fragments"][-1]["box"] == {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 0.6,
    }
    batches = localization["batches"]
    assert [batch["batchIndex"] for batch in batches] == [1, 2, 3]
    assert [len(batch["fragmentKeys"]) for batch in batches] == [2, 2, 1]
    attempt_ids = [batch["attemptId"] for batch in batches]
    assert len(set(attempt_ids)) == len(attempt_ids)
    assert all(attempt_ids)

    evidence = localization["evidence"]
    assert len(evidence) == 10
    assert len(response["regions"]) == 10
    assert evidence[0]["fragmentKey"].endswith(":1")
    assert evidence[-1]["fragmentKey"].endswith(":5")
    assert {item["templatePageId"] for item in evidence} == {
        f"multi-success-template-page-{page_number}" for page_number in range(1, 6)
    }
    assert all(item["templatePageId"] != "multi-success-template-page-6" for item in evidence)
    trace_keys = {
        (
            item["fragmentKey"],
            item["batchIndex"],
            item["attemptId"],
            item["modelCandidateIndex"],
        )
        for item in evidence
    }
    assert len(trace_keys) == len(evidence)
    for item in evidence:
        batch = batches[item["batchIndex"] - 1]
        assert item["attemptId"] == batch["attemptId"]
        assert item["fragmentKey"] in batch["fragmentKeys"]
        assert item["modelCandidateIndex"] in {0, 1}
    assert [segment["region_index"] for segment in raw["segments"]] == list(range(1, 11))


@pytest.mark.asyncio
async def test_calculation_accepts_student_work_inside_confirmed_teacher_frame(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE questions SET question_type='calculation' WHERE id='question'"
        )
        connection.execute(
            """UPDATE question_frame_regions SET height=0.20
               WHERE frame_item_id LIKE '%:question:item'"""
        )
    recognition = InsideFrameCalculationRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["status"] == "recognized"
    region = database.fetchone(
        "SELECT * FROM student_response_regions WHERE student_response_id=?",
        (response["id"],),
    )
    assert region is not None
    box = json_loads(region["template_bbox_json"], {})
    teacher_top = 0.08 * 500
    teacher_bottom = (0.08 + 0.20) * 500
    assert box["y"] >= teacher_top
    assert box["y"] + box["height"] <= teacher_bottom
    localization = json_loads(response["raw_recognition_json"], {})["localization"]
    assert localization["evidenceComplete"] is True
    assert localization["evidence"][0]["modelBbox"] == [200.0, 50.0, 600.0, 150.0]
    assert localization["evidence"][0]["templateBboxPx"] == box
    assert recognition.student_response_calls == 1


@pytest.mark.asyncio
async def test_calculation_low_confidence_transcription_forces_review(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    database.execute(
        "UPDATE questions SET question_type='calculation' WHERE id='question'"
    )
    recognition = LowConfidenceTranscriptionRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["recognized_text"] == "x = 42?"
    assert response["confidence"] == pytest.approx(0.50)
    assert response["status"] == "needs_review"
    raw = json_loads(response["raw_recognition_json"], {})
    assert raw["localization"]["evidenceComplete"] is True
    assert raw["localization"]["confidence"] == pytest.approx(0.50)
    assert raw["localization"]["recognitionReviewThreshold"] == pytest.approx(
        settings.grading_recognition_review_threshold
    )
    assert response["confidence"] < settings.grading_recognition_review_threshold
    assert recognition.calculation_location_calls == 1
    assert recognition.student_response_calls == 1


@pytest.mark.asyncio
async def test_calculation_reprocessing_preserves_old_localization_and_confirmed_frame(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id = _single_page_case(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE questions SET question_type='calculation' WHERE id='question'"
        )
        connection.execute(
            """UPDATE question_frame_regions SET height=0.20
               WHERE frame_item_id LIKE '%:question:item'"""
        )
    first_recognition = FakeRecognition()
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, first_recognition),
    ).run("submission")

    old_response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert old_response is not None
    old_regions = database.fetchall(
        """SELECT * FROM student_response_regions WHERE student_response_id=?
           ORDER BY sort_order""",
        (old_response["id"],),
    )
    old_frame = database.fetchone(
        """SELECT r.* FROM question_frame_regions r
           JOIN question_frame_items i ON i.id=r.frame_item_id
           WHERE i.frame_set_id=? AND i.question_id='question'""",
        (frame_set_id,),
    )
    old_frame_set = database.fetchone(
        "SELECT * FROM question_frame_sets WHERE id=?",
        (frame_set_id,),
    )
    assert old_regions and old_frame is not None and old_frame_set is not None
    old_raw = json_loads(old_response["raw_recognition_json"], {})
    old_evidence_ids = {
        item["evidenceId"] for item in old_raw["localization"]["evidence"]
    }
    old_alignment_ids = {
        item["alignmentRevisionId"] for item in old_raw["localization"]["evidence"]
    }

    second_recognition = InsideFrameCalculationRecognition()
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, second_recognition),
    ).run("submission")

    generations = database.fetchall(
        """SELECT r.*,p.revision_number FROM student_responses r
           JOIN student_processing_revisions p ON p.id=r.processing_revision_id
           WHERE r.submission_id='submission' ORDER BY p.revision_number"""
    )
    assert [row["revision_number"] for row in generations] == [1, 2]
    assert generations[0]["id"] == old_response["id"]
    assert generations[0]["raw_recognition_json"] == old_response["raw_recognition_json"]
    assert generations[0]["frame_set_id"] == frame_set_id
    assert generations[1]["frame_set_id"] == frame_set_id
    assert generations[0]["processing_revision_id"] != generations[1][
        "processing_revision_id"
    ]
    assert database.fetchone(
        "SELECT * FROM student_responses WHERE id=?",
        (old_response["id"],),
    ) == old_response
    assert database.fetchall(
        """SELECT * FROM student_response_regions WHERE student_response_id=?
           ORDER BY sort_order""",
        (old_response["id"],),
    ) == old_regions
    assert database.fetchone(
        """SELECT r.* FROM question_frame_regions r
           JOIN question_frame_items i ON i.id=r.frame_item_id
           WHERE i.frame_set_id=? AND i.question_id='question'""",
        (frame_set_id,),
    ) == old_frame
    assert database.fetchone(
        "SELECT * FROM question_frame_sets WHERE id=?",
        (frame_set_id,),
    ) == old_frame_set

    new_response = generations[1]
    new_raw = json_loads(new_response["raw_recognition_json"], {})
    new_evidence_ids = {
        item["evidenceId"] for item in new_raw["localization"]["evidence"]
    }
    new_alignment_ids = {
        item["alignmentRevisionId"] for item in new_raw["localization"]["evidence"]
    }
    assert old_evidence_ids.isdisjoint(new_evidence_ids)
    assert old_alignment_ids.isdisjoint(new_alignment_ids)
    assert old_raw["localization"]["evidence"] != new_raw["localization"]["evidence"]
    assert database.fetchone(
        "SELECT current_processing_revision_id FROM student_submissions WHERE id='submission'"
    ) == {"current_processing_revision_id": new_response["processing_revision_id"]}
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_response_regions"
    ) == {"count": len(old_evidence_ids) + len(new_evidence_ids)}

    for row in generations:
        serialized = str(row["raw_recognition_json"])
        assert "template_image" not in serialized
        assert "student_image" not in serialized
        assert "templateImage" not in serialized
        assert "studentImage" not in serialized
        assert "/9j/" not in serialized
        persisted = json_loads(serialized, {})
        assert all(
            "template_image" not in fragment and "student_image" not in fragment
            for fragment in persisted["localization"]["plan"]["fragments"]
        )
    assert all(
        row["cropped_image_path"] is None
        for row in database.fetchall("SELECT * FROM student_response_regions")
    )


@pytest.mark.asyncio
async def test_calculation_reliable_blank_persists_checked_window_without_transcription(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    database.execute(
        "UPDATE questions SET question_type='calculation' WHERE id='question'"
    )
    recognition = BlankCalculationRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["recognized_text"] == ""
    assert response["status"] == "recognized"
    raw = json_loads(response["raw_recognition_json"], {})
    assert raw["isBlank"] is True
    assert raw["issues"] == []
    assert raw["segments"] == [
        {
            "region_index": 1,
            "transcription": "",
            "is_blank": True,
            "confidence": 0.99,
            "issues": [],
        }
    ]
    localization = raw["localization"]
    assert localization["reliableBlank"] is True
    assert localization["evidenceComplete"] is True
    assert [item["evidenceKind"] for item in localization["evidence"]] == [
        "blank_search_window"
    ]
    regions = database.fetchall(
        "SELECT * FROM student_response_regions WHERE student_response_id=?",
        (response["id"],),
    )
    assert [region["id"] for region in regions] == [
        localization["evidence"][0]["evidenceId"]
    ]
    assert recognition.student_response_calls == 0
    assert recognition.calculation_location_calls == 1


@pytest.mark.asyncio
async def test_calculation_low_confidence_blank_keeps_negative_evidence_for_review(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    database.execute(
        "UPDATE questions SET question_type='calculation' WHERE id='question'"
    )
    recognition = LowConfidenceBlankCalculationRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        "SELECT * FROM student_responses WHERE submission_id='submission'"
    )
    assert response is not None
    assert response["recognized_text"] == ""
    assert response["status"] == "needs_review"
    raw = json_loads(response["raw_recognition_json"], {})
    assert raw["isBlank"] is False
    assert "calculation_window_low_confidence" in raw["issues"]
    assert raw["segments"] == [
        {
            "region_index": 1,
            "transcription": "",
            "is_blank": False,
            "confidence": 0.70,
            "issues": ["localization_blank_unreliable"],
        }
    ]
    localization = raw["localization"]
    assert localization["evidenceComplete"] is True
    assert localization["reliableBlank"] is False
    assert [item["evidenceKind"] for item in localization["evidence"]] == [
        "blank_search_window"
    ]
    regions = database.fetchall(
        "SELECT id FROM student_response_regions WHERE student_response_id=?",
        (response["id"],),
    )
    assert regions == [{"id": localization["evidence"][0]["evidenceId"]}]
    assert recognition.student_response_calls == 0
    assert recognition.calculation_location_calls == 1


@pytest.mark.asyncio
async def test_calculation_borderline_alignment_routes_positive_and_blank_to_review(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    template_path = tmp_path / "borderline-template.jpg"
    student_path = tmp_path / "borderline-student.jpg"
    _exam_page(template_path, student=False)
    _exam_page(student_path, student=True)
    quality = AlignmentQuality(
        method="test",
        score=0.60,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments = {
        1: (
            {
                "id": "borderline-template-page",
                "page_number": 1,
                "image_path": template_path.name,
                "width": 400,
                "height": 500,
            },
            {
                "id": "borderline-student-page",
                "page_number": 1,
                "original_image_path": student_path.name,
                "width": 400,
                "height": 500,
                "alignment_revision_id": "borderline-alignment",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(400, 500),
                PageSize(400, 500),
                quality,
            ),
        )
    }
    question = {
        "id": "borderline-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Question",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "anchor",
                "template_page_id": "borderline-template-page",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.1,
                "sort_order": 0,
            }
        ],
    }

    positive_recognition = HighConfidenceCalculationRecognition()
    positive = await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, positive_recognition),
    )._recognize_calculation_response(question, [question], alignments, [1])

    assert positive["status"] == "needs_review"
    assert positive["recognized_text"] == "x = 42"
    assert positive["confidence"] == pytest.approx(0.60)
    positive_raw = positive["raw_recognition"]
    assert positive_raw["isBlank"] is False
    assert "calculation_alignment_low_confidence" in positive_raw["issues"]
    positive_localization = positive_raw["localization"]
    assert positive_localization["evidenceComplete"] is True
    assert positive_localization["reliableBlank"] is False
    assert positive_localization["alignmentConfidence"] == pytest.approx(0.60)
    assert positive_localization["recognitionReviewThreshold"] == pytest.approx(
        settings.grading_recognition_review_threshold
    )
    alignment_issue = next(
        issue
        for issue in positive_localization["issues"]
        if issue["code"] == "calculation_alignment_low_confidence"
    )
    assert alignment_issue["details"] == {
        "confidence": 0.60,
        "threshold": settings.grading_recognition_review_threshold,
    }
    assert positive_recognition.calculation_location_calls == 1
    assert positive_recognition.student_response_calls == 1

    blank_recognition = BlankCalculationRecognition()
    blank = await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, blank_recognition),
    )._recognize_calculation_response(question, [question], alignments, [1])
    blank_raw = blank["raw_recognition"]
    assert blank["status"] == "needs_review"
    assert blank["confidence"] == pytest.approx(0.60)
    assert blank_raw["isBlank"] is False
    assert blank_raw["localization"]["reliableBlank"] is False
    assert "calculation_alignment_low_confidence" in blank_raw["issues"]
    assert blank_recognition.calculation_location_calls == 1
    assert blank_recognition.student_response_calls == 0


@pytest.mark.asyncio
async def test_calculation_alignment_below_mapping_threshold_skips_locator(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for(tmp_path), mapping_min_alignment_score=0.75)
    settings.ensure_directories()
    template_path = tmp_path / "below-mapping-template.jpg"
    student_path = tmp_path / "below-mapping-student.jpg"
    _exam_page(template_path, student=False)
    _exam_page(student_path, student=True)
    quality = AlignmentQuality(
        method="test",
        score=0.60,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments = {
        1: (
            {
                "id": "below-mapping-template-page",
                "page_number": 1,
                "image_path": template_path.name,
                "width": 400,
                "height": 500,
            },
            {
                "id": "below-mapping-student-page",
                "page_number": 1,
                "original_image_path": student_path.name,
                "width": 400,
                "height": 500,
                "alignment_revision_id": "below-mapping-alignment",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(400, 500),
                PageSize(400, 500),
                quality,
            ),
        )
    }
    question = {
        "id": "below-mapping-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Question",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "anchor",
                "template_page_id": "below-mapping-template-page",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.1,
                "sort_order": 0,
            }
        ],
    }
    recognition = HighConfidenceCalculationRecognition()

    response = await StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, recognition),
    )._recognize_calculation_response(question, [question], alignments, [1])

    assert response["status"] == "needs_review"
    assert response["regions"] == []
    raw = response["raw_recognition"]
    assert raw["isBlank"] is False
    localization = raw["localization"]
    assert localization["evidenceComplete"] is False
    assert localization["evidence"] == []
    assert localization["plan"]["evidenceComplete"] is False
    assert localization["plan"]["fragments"] == []
    assert "calculation_alignment_unreliable" in {
        issue["code"] for issue in localization["plan"]["issues"]
    }
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 0


@pytest.mark.asyncio
async def test_calculation_mixed_located_and_uncertain_window_is_incomplete(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    database = Database(settings.database_path)
    recognition = MixedCalculationRecognition()
    pipeline = StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    )
    quality = AlignmentQuality(
        method="test",
        score=1.0,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments: dict[int, tuple[dict[str, Any], dict[str, Any], AlignmentResult]] = {}
    for page_number in (1, 2):
        template_path = tmp_path / f"template-{page_number}.jpg"
        student_path = tmp_path / f"student-{page_number}.jpg"
        _exam_page(template_path, student=False)
        _exam_page(student_path, student=True)
        alignments[page_number] = (
            {
                "id": f"template-page-{page_number}",
                "page_number": page_number,
                "image_path": template_path.name,
                "width": 400,
                "height": 500,
            },
            {
                "id": f"student-page-{page_number}",
                "page_number": page_number,
                "original_image_path": student_path.name,
                "width": 400,
                "height": 500,
                "alignment_revision_id": f"alignment-{page_number}",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(400, 500),
                PageSize(400, 500),
                quality,
            ),
        )
    question = {
        "id": "calculation-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Question",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "anchor",
                "template_page_id": "template-page-1",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.1,
                "sort_order": 0,
            }
        ],
    }

    response = await pipeline._recognize_calculation_response(
        question,
        [question],
        alignments,
        [1, 2],
    )

    assert response["status"] == "needs_review"
    assert len(response["regions"]) == 1
    localization = response["raw_recognition"]["localization"]
    assert localization["evidenceComplete"] is False
    assert [item["status"] for item in localization["batches"][0]["windows"]] == [
        "located",
        "uncertain",
    ]
    assert recognition.student_response_calls == 1

    located_blank_recognition = MixedCalculationRecognition(second_status="blank")
    located_blank = await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, located_blank_recognition),
    )._recognize_calculation_response(
        question,
        [question],
        alignments,
        [1, 2],
    )

    assert located_blank["status"] == "recognized"
    assert located_blank["raw_recognition"]["isBlank"] is False
    assert [
        segment["is_blank"] for segment in located_blank["raw_recognition"]["segments"]
    ] == [False, True]
    assert [
        item["evidenceKind"]
        for item in located_blank["raw_recognition"]["localization"]["evidence"]
    ] == ["located_region", "blank_search_window"]


@pytest.mark.asyncio
async def test_calculation_batch_failure_preserves_successful_negative_evidence(
    tmp_path: Path,
) -> None:
    settings = replace(settings_for(tmp_path), answer_pages_per_batch=1)
    settings.ensure_directories()
    database = Database(settings.database_path)
    recognition = PartiallyFailingCalculationRecognition()
    pipeline = StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    )
    quality = AlignmentQuality(
        method="test",
        score=1.0,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments: dict[int, tuple[dict[str, Any], dict[str, Any], AlignmentResult]] = {}
    for page_number in (1, 2):
        template_path = tmp_path / f"partial-template-{page_number}.jpg"
        student_path = tmp_path / f"partial-student-{page_number}.jpg"
        _exam_page(template_path, student=False)
        _exam_page(student_path, student=True)
        alignments[page_number] = (
            {
                "id": f"partial-template-page-{page_number}",
                "page_number": page_number,
                "image_path": template_path.name,
                "width": 400,
                "height": 500,
            },
            {
                "id": f"partial-student-page-{page_number}",
                "page_number": page_number,
                "original_image_path": student_path.name,
                "width": 400,
                "height": 500,
                "alignment_revision_id": f"partial-alignment-{page_number}",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(400, 500),
                PageSize(400, 500),
                quality,
            ),
        )
    question = {
        "id": "partial-calculation-question",
        "sort_order": 0,
        "number": "1",
        "stem": "Question",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "anchor",
                "template_page_id": "partial-template-page-1",
                "page_number": 1,
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.1,
                "sort_order": 0,
            }
        ],
    }

    response = await pipeline._recognize_calculation_response(
        question,
        [question],
        alignments,
        [1, 2],
    )

    assert response["status"] == "needs_review"
    assert response["recognized_text"] == ""
    assert len(response["regions"]) == 1
    localization = response["raw_recognition"]["localization"]
    assert localization["evidenceComplete"] is False
    assert localization["reliableBlank"] is False
    assert [batch["status"] for batch in localization["batches"]] == [
        "blank",
        "needs_review",
    ]
    assert localization["batches"][1]["error"]["code"] == (
        "CALCULATION_LOCALIZATION_FAILED"
    )
    assert [item["evidenceKind"] for item in localization["evidence"]] == [
        "blank_search_window"
    ]
    assert localization["evidence"][0]["evidenceId"] == response["regions"][0]["id"]
    assert response["raw_recognition"]["isBlank"] is False
    assert response["raw_recognition"]["segments"][0]["is_blank"] is False
    assert recognition.student_response_calls == 0
    assert recognition.calculation_location_calls == 2


def test_calculation_half_open_boundary_is_excluded_from_locator_and_evidence_crops(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    pipeline = StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, FakeRecognition()),
    )
    template_path = tmp_path / "boundary-template.png"
    student_path = tmp_path / "boundary-student.png"
    for path in (template_path, student_path):
        image = Image.new("RGB", (100, 100), (0, 255, 0))
        ImageDraw.Draw(image).rectangle((0, 50, 99, 99), fill=(255, 0, 0))
        image.save(path, "PNG")
    quality = AlignmentQuality(
        method="test",
        score=1.0,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=1.0,
        is_reliable=True,
    )
    alignments = {
        1: (
            {
                "id": "boundary-template-page",
                "page_number": 1,
                "image_path": template_path.name,
                "width": 100,
                "height": 100,
            },
            {
                "id": "boundary-student-page",
                "page_number": 1,
                "original_image_path": student_path.name,
                "width": 100,
                "height": 100,
                "alignment_revision_id": "boundary-alignment",
            },
            AlignmentResult.create(
                Homography.identity(),
                PageSize(100, 100),
                PageSize(100, 100),
                quality,
            ),
        )
    }
    fragment = CalculationSearchFragment(
        fragment_key="question:calculation-window:1",
        template_page_id="boundary-template-page",
        student_page_id="boundary-student-page",
        alignment_revision_id="boundary-alignment",
        page_number=1,
        student_page_number=1,
        x=0.0,
        y=0.2,
        width=1.0,
        height=0.3,
        sort_order=0,
    )

    runtime_fragment = pipeline._calculation_fragment_with_images(fragment, alignments)
    assert runtime_fragment.template_image is not None
    assert runtime_fragment.student_image is not None
    for encoded in (runtime_fragment.template_image, runtime_fragment.student_image):
        with Image.open(BytesIO(encoded)) as opened:
            cropped = opened.convert("RGB")
            assert cropped.size == (100, 30)
            red, green, _blue = cropped.getpixel((50, 29))
            assert green > red + 100

    localized = normalize_calculation_localization_batch(
        [
            {
                "fragmentKey": fragment.fragment_key,
                "status": "located",
                "confidence": 0.99,
                "issues": [],
                "regions": [
                    {"bbox": [0, 0, 1000, 1000], "confidence": 0.99, "issues": []}
                ],
            }
        ],
        [fragment],
        batch_index=1,
        attempt_id="boundary-attempt",
        model_id="test-vl",
        prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
    )
    prepared, saved, snapshot = pipeline._prepare_calculation_evidence(
        {"id": "question"},
        fragment,
        alignments,
        evidence_kind="located_region",
        candidate=localized.regions[0],
        batch_index=1,
        attempt_id="boundary-attempt",
        confidence=0.99,
        issues=[],
        sort_order=0,
    )

    assert saved["template_box"] == {
        "x": 0.0,
        "y": 20.0,
        "width": 100.0,
        "height": 30.0,
    }
    assert snapshot["templateBboxPx"] == saved["template_box"]
    for key in ("template_image", "student_image"):
        with Image.open(BytesIO(prepared[key])) as opened:
            cropped = opened.convert("RGB")
            assert cropped.size == (100, 30)
            red, green, _blue = cropped.getpixel((50, 29))
            assert green > red + 100


@pytest.mark.asyncio
async def test_calculation_rejects_locally_clipped_search_and_candidate_evidence(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    recognition = FakeRecognition()
    pipeline = StudentPipeline(
        settings,
        Database(settings.database_path),
        cast(RecognitionService, recognition),
    )
    template_path = tmp_path / "clipped-template.png"
    student_path = tmp_path / "clipped-student.png"
    Image.new("RGB", (100, 100), "white").save(template_path, "PNG")
    Image.new("RGB", (100, 100), "white").save(student_path, "PNG")
    quality = AlignmentQuality(
        method="test",
        score=0.90,
        matched_features=20,
        inliers=20,
        inlier_ratio=1.0,
        mean_reprojection_error_px=0.0,
        template_feature_coverage=1.0,
        student_feature_coverage=1.0,
        visible_template_ratio=0.70,
        is_reliable=True,
    )
    alignment = AlignmentResult.create(
        Homography.from_rows(
            ((1.0, 0.0, 0.0), (0.0, 1.0, 30.0), (0.0, 0.0, 1.0))
        ),
        PageSize(100, 100),
        PageSize(100, 100),
        quality,
    )
    alignments = {
        1: (
            {
                "id": "clipped-template-page",
                "page_number": 1,
                "image_path": template_path.name,
                "width": 100,
                "height": 100,
            },
            {
                "id": "clipped-student-page",
                "page_number": 1,
                "original_image_path": student_path.name,
                "width": 100,
                "height": 100,
                "alignment_revision_id": "clipped-alignment",
            },
            alignment,
        )
    }
    bottom_fragment = CalculationSearchFragment(
        fragment_key="question:calculation-window:1",
        template_page_id="clipped-template-page",
        student_page_id="clipped-student-page",
        alignment_revision_id="clipped-alignment",
        page_number=1,
        student_page_number=1,
        x=0.0,
        y=0.7,
        width=1.0,
        height=0.3,
        sort_order=0,
    )

    with pytest.raises(AppError) as search_error:
        pipeline._calculation_fragment_with_images(bottom_fragment, alignments)
    assert search_error.value.code == "CALCULATION_SEARCH_FRAGMENT_CLIPPED"
    assert search_error.value.details["visibleRatio"] == pytest.approx(0.0)

    question = {
        "id": "question",
        "sort_order": 0,
        "number": "1",
        "stem": "Question",
        "type": "calculation",
        "frame_set_id": "frame-set",
        "frame_regions": [
            {
                "id": "anchor",
                "template_page_id": "clipped-template-page",
                "page_number": 1,
                "x": 0.1,
                "y": 0.7,
                "width": 0.8,
                "height": 0.1,
                "sort_order": 0,
            }
        ],
    }
    response = await pipeline._recognize_calculation_response(
        question,
        [question],
        alignments,
        [1],
    )
    localization = response["raw_recognition"]["localization"]
    assert response["status"] == "needs_review"
    assert localization["evidenceComplete"] is False
    assert localization["batches"][0]["error"]["code"] == (
        "CALCULATION_SEARCH_FRAGMENT_CLIPPED"
    )
    assert recognition.calculation_location_calls == 0

    mostly_visible_fragment = CalculationSearchFragment(
        fragment_key="question:calculation-window:1",
        template_page_id="clipped-template-page",
        student_page_id="clipped-student-page",
        alignment_revision_id="clipped-alignment",
        page_number=1,
        student_page_number=1,
        x=0.0,
        y=0.0,
        width=1.0,
        height=0.8,
        sort_order=0,
    )
    runtime_fragment = pipeline._calculation_fragment_with_images(
        mostly_visible_fragment,
        alignments,
    )
    assert runtime_fragment.student_image is not None
    localized_candidate = normalize_calculation_localization_batch(
        [
            {
                "fragmentKey": mostly_visible_fragment.fragment_key,
                "status": "located",
                "confidence": 0.99,
                "issues": [],
                "regions": [
                    {
                        "bbox": [0, 800, 1000, 1000],
                        "confidence": 0.99,
                        "issues": [],
                    }
                ],
            }
        ],
        [mostly_visible_fragment],
        batch_index=1,
        attempt_id="clipped-candidate-attempt",
        model_id="test-vl",
        prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
    )
    with pytest.raises(AppError) as evidence_error:
        pipeline._prepare_calculation_evidence(
            {"id": "question"},
            mostly_visible_fragment,
            alignments,
            evidence_kind="located_region",
            candidate=localized_candidate.regions[0],
            batch_index=1,
            attempt_id="clipped-candidate-attempt",
            confidence=0.99,
            issues=[],
            sort_order=0,
        )
    assert evidence_error.value.code == "CALCULATION_EVIDENCE_REGION_CLIPPED"
    assert evidence_error.value.details["visibleRatio"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_late_processing_revision_is_abandoned_without_overwriting_current(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id = _single_page_case(tmp_path)
    blocking = BlockingRecognition()
    stale_pipeline = StudentPipeline(
        settings,
        database,
        cast(RecognitionService, blocking),
    )
    stale_task = asyncio.create_task(stale_pipeline.run("submission"))
    await asyncio.wait_for(blocking.started.wait(), timeout=5)

    current_recognition = FakeRecognition()
    current_pipeline = StudentPipeline(
        settings,
        database,
        cast(RecognitionService, current_recognition),
    )
    try:
        await current_pipeline.run("submission")
    finally:
        blocking.release.set()
    await stale_task

    revisions = database.fetchall(
        """SELECT id,revision_number,frame_set_id,status,is_current,issues_json
           FROM student_processing_revisions WHERE submission_id='submission'
           ORDER BY revision_number"""
    )
    assert len(revisions) == 2
    assert revisions[0]["revision_number"] == 1
    assert revisions[0]["frame_set_id"] == frame_set_id
    assert revisions[0]["status"] == "failed"
    assert revisions[0]["is_current"] == 0
    stale_issues = json_loads(revisions[0]["issues_json"], [])
    assert any(issue.get("code") == "processing_revision_abandoned" for issue in stale_issues)
    assert revisions[1]["revision_number"] == 2
    assert revisions[1]["status"] == "ready"
    assert revisions[1]["is_current"] == 1
    current_revision_id = revisions[1]["id"]

    submission = database.fetchone(
        """SELECT status,error_code,current_processing_revision_id
           FROM student_submissions WHERE id='submission'"""
    )
    assert submission == {
        "status": "ready",
        "error_code": None,
        "current_processing_revision_id": current_revision_id,
    }
    assert database.fetchall(
        "SELECT id FROM student_pages WHERE submission_id='submission'"
    ) and database.fetchone(
        "SELECT COUNT(*) AS count FROM student_pages WHERE submission_id='submission'"
    ) == {"count": 1}
    assert database.fetchall(
        """SELECT processing_revision_id,recognized_text FROM student_responses
           WHERE submission_id='submission'"""
    ) == [{"processing_revision_id": current_revision_id, "recognized_text": "A"}]
    assert database.fetchall(
        """SELECT processing_revision_id FROM student_question_regions
           WHERE submission_id='submission'"""
    ) == [{"processing_revision_id": current_revision_id}]
    assert database.fetchall(
        "SELECT processing_revision_id FROM student_page_alignment_revisions"
    ) == [{"processing_revision_id": current_revision_id}]
    assert blocking.question_region_calls == 0
    assert blocking.template_region_calls == 0
    assert current_recognition.question_region_calls == 0
    assert current_recognition.template_region_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["frame_set", "blank_config"])
async def test_commit_abandons_revision_when_captured_dependency_changes(
    tmp_path: Path,
    dependency: str,
) -> None:
    settings, database, frame_set_id = _single_page_case(tmp_path)
    second_config_id: str | None = None
    if dependency == "blank_config":
        _first_config_id, second_config_id = _blank_config_versions(database, frame_set_id)

    blocking = BlockingRecognition()
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, blocking))
    processing_task = asyncio.create_task(pipeline.run("submission"))
    await asyncio.wait_for(blocking.started.wait(), timeout=5)
    if dependency == "frame_set":
        database.execute(
            "UPDATE tasks SET current_question_frame_set_id=NULL WHERE id='task'"
        )
    else:
        database.execute(
            """UPDATE question_grading_configs SET current_blank_config_version_id=?
               WHERE question_id='question'""",
            (second_config_id,),
        )
    blocking.release.set()
    await processing_task

    revision = database.fetchone(
        """SELECT status,is_current,issues_json FROM student_processing_revisions
           WHERE submission_id='submission'"""
    )
    assert revision is not None
    assert revision["status"] == "failed"
    assert revision["is_current"] == 0
    issues = json_loads(revision["issues_json"], [])
    abandoned = next(issue for issue in issues if issue["code"] == "processing_revision_abandoned")
    assert dependency in abandoned["details"]["reason"]
    submission = database.fetchone(
        """SELECT status,current_processing_revision_id,error_code
           FROM student_submissions WHERE id='submission'"""
    )
    assert submission == {
        "status": "uploaded",
        "current_processing_revision_id": None,
        "error_code": "STUDENT_PROCESSING_SUPERSEDED",
    }
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_pages") == {"count": 0}
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_responses") == {"count": 0}
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_question_regions") == {
        "count": 0
    }
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_page_alignment_revisions"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_commit_abandons_when_alignment_revision_appears_during_recognition(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, FakeRecognition()),
    ).run("submission")
    original_response = database.fetchone(
        "SELECT id,processing_revision_id FROM student_responses WHERE submission_id='submission'"
    )
    page = database.fetchone(
        "SELECT id FROM student_pages WHERE submission_id='submission'"
    )
    assert original_response is not None
    assert page is not None

    blocking = BlockingRecognition()
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, blocking))
    processing_task = asyncio.create_task(pipeline.run("submission"))
    await asyncio.wait_for(blocking.started.wait(), timeout=5)
    submission = database.fetchone(
        "SELECT current_processing_revision_id FROM student_submissions WHERE id='submission'"
    )
    assert submission is not None
    current_revision_id = str(submission["current_processing_revision_id"])
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO student_page_alignment_revisions(
                 id,processing_revision_id,student_page_id,revision_number,template_page_id,
                 transform_json,quality,method,status,control_points_json,metrics_json,source,
                 is_current,issues_json,created_by,created_at,updated_at
               ) VALUES('unexpected-alignment',?,?,1,'template-page',
                 '[[1,0,0],[0,1,0],[0,0,1]]',1,'teacher','aligned','[]','{}','teacher',
                 1,'[]','teacher',?,?)""",
            (current_revision_id, page["id"], timestamp, timestamp),
        )
    blocking.release.set()
    await processing_task

    revision = database.fetchone(
        "SELECT status,is_current,issues_json FROM student_processing_revisions WHERE id=?",
        (current_revision_id,),
    )
    assert revision is not None
    assert revision["status"] == "failed"
    assert revision["is_current"] == 0
    issues = json_loads(revision["issues_json"], [])
    assert any(
        "alignment_revision_changed" in issue.get("details", {}).get("reason", "")
        for issue in issues
    )
    assert database.fetchall(
        "SELECT id,processing_revision_id FROM student_responses WHERE submission_id='submission'"
    ) == [original_response]
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_question_regions"
    ) == {"count": 1}
    assert database.fetchone("SELECT COUNT(*) AS count FROM student_pages") == {"count": 1}


@pytest.mark.asyncio
async def test_partial_submission_matches_one_template_page_and_skips_missing_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.migrate()
    timestamp = now_iso()
    _exam_page(tmp_path / "template-1.jpg", student=False)
    _exam_page(tmp_path / "template-2.jpg", student=False)
    upload_path = tmp_path / "uploads" / "task" / "students" / "partial" / "page.png"
    _exam_page(upload_path, student=True)

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Exam','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 1,'sha',2,'exam.pdf',?)""",
            (timestamp,),
        )
        for page_number in (1, 2):
            connection.execute(
                """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
                   VALUES(?,?,?,?,400,500,?)""",
                (
                    f"template-page-{page_number}",
                    "exam",
                    page_number,
                    f"template-{page_number}.jpg",
                    f"sha-{page_number}",
                ),
            )
        run_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES(?,'task','exam_recognition','succeeded','done',?)""",
            (run_id, timestamp),
        )
        for page_number in (1, 2):
            regions = json_dumps(
                [{"page_number": page_number, "x": 0.2, "y": 0.3, "width": 0.5, "height": 0.2}]
            )
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                     options_json,question_type,source_pages_json,confidence,issues_json,
                     confirmation_status,answer_regions_json,question_regions_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,1,'[]','confirmed',?,?)""",
                (
                    f"question-{page_number}",
                    "task",
                    run_id,
                    page_number - 1,
                    str(page_number),
                    str(page_number),
                    f"Question {page_number}",
                    "[]",
                    "single_choice",
                    json_dumps([page_number]),
                    regions,
                    "[]",
                ),
            )
        _confirmed_frame_set(
            connection,
            task_id="task",
            frames=[
                {
                    "question_id": f"question-{page_number}",
                    "regions": [
                        {
                            "template_page_id": f"template-page-{page_number}",
                            "page_number": page_number,
                            "x": 0.1,
                            "y": 0.1,
                            "width": 0.8,
                            "height": 0.6,
                        }
                    ],
                }
                for page_number in (1, 2)
            ],
            timestamp=timestamp,
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,original_name,mime_type,size_bytes,sha256,relative_path,
                 status,created_at,updated_at
               ) VALUES('partial','task','page-01.png','image/png',1,'student-sha',
                 'uploads/task/students/partial/page.png','uploaded',?,?)""",
            (timestamp, timestamp),
        )

    aligned_templates: list[str] = []

    def fake_align(template: str | Path, _student: str | Path) -> AlignmentResult:
        aligned_templates.append(Path(template).name)
        # The filename page hint must win even when visual quality alone is ambiguous.
        score = 0.8 if Path(template).name == "template-1.jpg" else 0.95
        quality = AlignmentQuality(
            method="test",
            score=score,
            matched_features=10,
            inliers=10,
            inlier_ratio=1,
            mean_reprojection_error_px=1,
            template_feature_coverage=0.5,
            student_feature_coverage=0.5,
            visible_template_ratio=1,
            is_reliable=score > 0.5,
        )
        return AlignmentResult.create(
            Homography.identity(), PageSize(400, 500), PageSize(400, 500), quality
        )

    monkeypatch.setattr("homework_judge.jobs.student_pipeline.align_pages", fake_align)
    recognition = FakeRecognition()
    pipeline = StudentPipeline(settings, database, cast(RecognitionService, recognition))
    await pipeline.run("partial")

    submission = database.fetchone("SELECT * FROM student_submissions WHERE id='partial'")
    assert submission is not None
    assert submission["status"] == "ready"
    assert submission["page_count"] == 1
    assert submission["question_region_status"] == "needs_review"
    assert submission["question_region_error_code"] == "STUDENT_PAGES_PARTIAL"
    assert "第 2 页" in submission["question_region_error_message"]
    page = database.fetchone("SELECT * FROM student_pages WHERE submission_id='partial'")
    assert page is not None
    assert page["template_page_id"] == "template-page-1"
    assert aligned_templates == ["template-1.jpg"]
    responses = database.fetchall(
        "SELECT question_id FROM student_responses WHERE submission_id='partial'"
    )
    assert responses == []
    processing = database.fetchone(
        """SELECT status,issues_json FROM student_processing_revisions
           WHERE id=?""",
        (submission["current_processing_revision_id"],),
    )
    assert processing is not None
    assert processing["status"] == "mapping_needs_review"
    assert any(
        issue.get("code") == "question_mapping_not_ready"
        for issue in json_loads(processing["issues_json"], [])
    )
    assert recognition.student_response_calls == 0
    assert recognition.question_region_calls == 0
    assert recognition.template_region_calls == 0
