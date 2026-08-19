from __future__ import annotations

import copy
import math

import pytest

from homework_judge.recognition.calculation_localization import (
    CalculationPageBinding,
    CalculationSearchFragment,
    aggregate_calculation_localization_batches,
    build_calculation_search_plan,
    failed_calculation_localization_batch,
    normalize_calculation_localization_batch,
    normalize_calculation_recognition_batch,
)


def _region(page: int, top: float, height: float = 0.2) -> dict[str, object]:
    return {
        "template_page_id": f"template-{page}",
        "page_number": page,
        "x": 0.1,
        "y": top,
        "width": 0.8,
        "height": height,
    }


def _question(
    question_id: str,
    order: int,
    regions: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": question_id,
        "sort_order": order,
        "is_duplicate": False,
        "frame_regions": regions,
    }


def _binding(template_page: int, student_page: int | None = None) -> CalculationPageBinding:
    actual_student_page = student_page or template_page
    return CalculationPageBinding(
        page_number=template_page,
        student_page_number=actual_student_page,
        template_page_id=f"template-{template_page}",
        student_page_id=f"student-{actual_student_page}",
        alignment_revision_id=f"alignment-{actual_student_page}",
    )


def _fragment(
    key: str = "q1:calculation-window:1",
    *,
    page: int = 1,
    y: float = 0.25,
    height: float = 0.5,
    sort_order: int = 0,
) -> CalculationSearchFragment:
    return CalculationSearchFragment(
        fragment_key=key,
        template_page_id=f"template-{page}",
        student_page_id=f"student-{page}",
        alignment_revision_id=f"alignment-{page}",
        page_number=page,
        student_page_number=page,
        x=0.0,
        y=y,
        width=1.0,
        height=height,
        sort_order=sort_order,
    )


def _located_node(
    key: str,
    bbox: list[float] | None = None,
    *,
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "fragmentKey": key,
        "status": "located",
        "confidence": confidence,
        "issues": [],
        "regions": [
            {
                "bbox": bbox or [100, 200, 600, 700],
                "confidence": confidence,
                "issues": [],
            }
        ],
    }


def _recognized_node(
    key: str,
    *,
    transcription: str = "x = 42",
    transcription_confidence: float = 0.94,
) -> dict[str, object]:
    node = _located_node(key)
    region = node["regions"][0]  # type: ignore[index]
    assert isinstance(region, dict)
    region.update(
        {
            "transcription": transcription,
            "transcriptionConfidence": transcription_confidence,
            "transcriptionIssues": [],
        }
    )
    return node


def _normalize(
    nodes: list[dict[str, object]],
    fragments: list[CalculationSearchFragment],
):
    return normalize_calculation_localization_batch(
        nodes,
        fragments,
        batch_index=1,
        attempt_id="attempt-1",
        model_id="vision-model",
        prompt_version="locator-v1",
    )


def _normalize_recognition(nodes: list[dict[str, object]]):
    return normalize_calculation_recognition_batch(
        nodes,
        [_fragment()],
        batch_index=1,
        attempt_id="attempt-1",
        model_id="vision-model",
        prompt_version="recognition-v1",
    )


def test_combined_calculation_result_maps_transcription_to_localized_region() -> None:
    result = _normalize_recognition([_recognized_node("q1:calculation-window:1")])

    assert result.localization_contract_valid is True
    assert result.transcription_contract_valid is True
    assert len(result.localization.regions) == 1
    assert len(result.transcriptions) == 1
    transcription = result.transcriptions[0]
    assert transcription.fragment_key == "q1:calculation-window:1"
    assert transcription.model_candidate_index == 0
    assert transcription.transcription == "x = 42"
    assert transcription.confidence == pytest.approx(0.94)


def test_combined_calculation_result_reuses_location_when_transcription_is_missing() -> None:
    node = _recognized_node("q1:calculation-window:1")
    region = node["regions"][0]  # type: ignore[index]
    assert isinstance(region, dict)
    region.pop("transcription")

    result = _normalize_recognition([node])

    assert result.localization_contract_valid is True
    assert result.transcription_contract_valid is False
    assert len(result.localization.regions) == 1
    assert result.transcriptions == ()
    assert "calculation_transcription_fields_invalid" in {
        issue.code for issue in result.issues
    }


def test_combined_calculation_result_rejects_invalid_location_before_transcription() -> None:
    node = _recognized_node("q1:calculation-window:1")
    region = node["regions"][0]  # type: ignore[index]
    assert isinstance(region, dict)
    region["bbox"] = [100, 200, 100, 700]

    result = _normalize_recognition([node])

    assert result.localization_contract_valid is False
    assert result.transcription_contract_valid is False
    assert result.localization.regions == ()


def test_combined_calculation_low_confidence_transcription_stays_structurally_valid() -> None:
    result = _normalize_recognition(
        [
            _recognized_node(
                "q1:calculation-window:1",
                transcription_confidence=0.3,
            )
        ]
    )

    assert result.localization_contract_valid is True
    assert result.transcription_contract_valid is True
    assert "calculation_transcription_low_confidence" in {
        issue.code for issue in result.issues
    }


def test_builds_same_page_half_open_full_width_window_without_mutating_input() -> None:
    questions = [
        _question("q1", 10, [_region(1, 0.2)]),
        _question("q2", 20, [_region(1, 0.7)]),
    ]
    original = copy.deepcopy(questions)

    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=questions,
        page_bindings=[_binding(1)],
        uploaded_student_page_numbers=[1],
    )

    assert plan.evidence_complete is True
    assert plan.next_question_id == "q2"
    assert len(plan.fragments) == 1
    fragment = plan.fragments[0]
    assert (fragment.x, fragment.y, fragment.width, fragment.height) == pytest.approx(
        (0.0, 0.2, 1.0, 0.5)
    )
    assert questions == original


def test_builds_cross_page_window_and_omits_zero_height_terminal_fragment() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question("q1", 1, [_region(1, 0.6)]),
            _question("q2", 2, [_region(3, 0.0)]),
        ],
        page_bindings=[_binding(1), _binding(2), _binding(3)],
        uploaded_student_page_numbers=[1, 2, 3],
    )

    assert plan.evidence_complete is True
    assert [(item.page_number, item.y, item.height) for item in plan.fragments] == [
        (1, 0.6, 0.4),
        (2, 0.0, 1.0),
    ]


def test_last_question_reaches_actual_uploaded_tail() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[_question("q1", 1, [_region(2, 0.4)])],
        page_bindings=[_binding(2), _binding(3), _binding(4)],
        uploaded_student_page_numbers=[2, 3, 4],
    )

    assert plan.evidence_complete is True
    assert plan.submission_last_page_number == 4
    assert [(item.page_number, item.y, item.height) for item in plan.fragments] == [
        (2, 0.4, 0.6),
        (3, 0.0, 1.0),
        (4, 0.0, 1.0),
    ]


def test_unaligned_actual_tail_is_explicitly_incomplete_instead_of_silently_shortened() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[_question("q1", 1, [_region(1, 0.4)])],
        page_bindings=[_binding(1), _binding(2)],
        uploaded_student_page_numbers=[1, 2, 3],
    )

    assert plan.evidence_complete is False
    assert "calculation_submission_tail_unaligned" in {issue.code for issue in plan.issues}


def test_rejects_non_monotonic_student_to_template_page_order() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[_question("q1", 1, [_region(2, 0.2)])],
        page_bindings=[_binding(3, 1), _binding(2, 2)],
        uploaded_student_page_numbers=[1, 2],
    )

    assert plan.evidence_complete is False
    assert "calculation_page_order_ambiguous" in {issue.code for issue in plan.issues}


def test_rejects_current_frame_that_crosses_next_question_boundary() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question("q1", 1, [_region(1, 0.2, 0.6)]),
            _question("q2", 2, [_region(1, 0.7)]),
        ],
        page_bindings=[_binding(1)],
        uploaded_student_page_numbers=[1],
    )

    assert plan.evidence_complete is False
    assert "calculation_current_frame_outside_search_window" in {
        issue.code for issue in plan.issues
    }


def test_rejects_reversed_anchor_order_without_guessing_a_window() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question("q1", 1, [_region(1, 0.8, 0.1)]),
            _question("q2", 2, [_region(1, 0.2, 0.1)]),
        ],
        page_bindings=[_binding(1)],
        uploaded_student_page_numbers=[1],
    )

    assert plan.evidence_complete is False
    assert plan.fragments == ()
    assert [issue.code for issue in plan.issues] == [
        "calculation_anchor_order_invalid"
    ]


@pytest.mark.parametrize(
    ("bindings", "uploaded_pages", "expected_codes", "expected_fragment_pages"),
    [
        (
            [_binding(1), _binding(2), _binding(3)],
            [1, 3],
            {
                "calculation_submission_page_missing",
                "calculation_submission_page_gap",
            },
            [1, 2, 3],
        ),
        (
            [_binding(1), _binding(3)],
            [1, 2, 3],
            {
                "calculation_student_page_unaligned",
                "calculation_page_binding_missing",
            },
            [1, 3],
        ),
    ],
)
def test_missing_middle_upload_or_alignment_is_explicitly_incomplete(
    bindings: list[CalculationPageBinding],
    uploaded_pages: list[int],
    expected_codes: set[str],
    expected_fragment_pages: list[int],
) -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question("q1", 1, [_region(1, 0.6)]),
            _question("q2", 2, [_region(3, 0.4)]),
        ],
        page_bindings=bindings,
        uploaded_student_page_numbers=uploaded_pages,
    )

    assert plan.evidence_complete is False
    assert expected_codes <= {issue.code for issue in plan.issues}
    assert [fragment.page_number for fragment in plan.fragments] == (
        expected_fragment_pages
    )


def test_all_current_question_fragments_fit_inside_one_cross_page_window() -> None:
    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question(
                "q1",
                1,
                [
                    _region(1, 0.6, 0.2),
                    _region(2, 0.05, 0.25),
                ],
            ),
            _question("q2", 2, [_region(2, 0.8, 0.1)]),
        ],
        page_bindings=[_binding(1), _binding(2)],
        uploaded_student_page_numbers=[1, 2],
    )

    assert plan.evidence_complete is True
    assert plan.issues == ()
    assert [(item.page_number, item.y, item.height) for item in plan.fragments] == [
        (1, 0.6, 0.4),
        (2, 0.0, 0.8),
    ]


def test_multi_column_layout_that_cannot_fit_vertical_window_has_stable_issue() -> None:
    left_column = _region(1, 0.2, 0.2)
    left_column["x"] = 0.05
    left_column["width"] = 0.4
    right_column = _region(1, 0.65, 0.2)
    right_column["x"] = 0.55
    right_column["width"] = 0.4
    next_anchor = _region(1, 0.55, 0.1)
    next_anchor["x"] = 0.05
    next_anchor["width"] = 0.4

    plan = build_calculation_search_plan(
        frame_set_id="frames-v1",
        question_id="q1",
        questions=[
            _question("q1", 1, [left_column, right_column]),
            _question("q2", 2, [next_anchor]),
        ],
        page_bindings=[_binding(1)],
        uploaded_student_page_numbers=[1],
    )

    assert plan.evidence_complete is False
    issue = next(
        issue
        for issue in plan.issues
        if issue.code == "calculation_current_frame_outside_search_window"
    )
    assert issue.path == "$.questions.current.frameRegions[1]"
    assert issue.details == {
        "pageNumber": 1,
        "top": 0.65,
        "bottom": 0.8500000000000001,
        "endPageNumber": 1,
        "endTop": 0.55,
    }


def test_fragment_images_are_immutable_runtime_data_and_absent_from_snapshot() -> None:
    fragment = _fragment()
    with_images = fragment.with_images(template_image=b"template", student_image=b"student")

    assert fragment.template_image is None
    assert with_images.template_image == b"template"
    assert with_images.student_image == b"student"
    assert "template_image" not in with_images.snapshot()
    assert "student_image" not in with_images.snapshot()


def test_normalizes_and_projects_valid_locator_regions() -> None:
    fragment = _fragment()
    result = _normalize([_located_node(fragment.fragment_key)], [fragment])

    assert result.status == "located"
    assert result.evidence_complete is True
    assert result.confidence == 0.9
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.model_bbox == (100.0, 200.0, 600.0, 700.0)
    assert region.template_bbox == (0.1, 0.35, 0.5, 0.25)
    assert region.model_candidate_index == 0
    assert region.batch_index == 1
    assert region.attempt_id == "attempt-1"


def test_orders_shuffled_same_page_and_cross_page_candidates_by_reading_position() -> None:
    page_one = _fragment("p1", page=1, y=0.2, height=0.5)
    page_two = _fragment("p2", page=2, y=0.1, height=0.8, sort_order=1)
    result = _normalize(
        [
            {
                "fragmentKey": "p2",
                "status": "located",
                "confidence": 0.99,
                "issues": [],
                "regions": [
                    {"bbox": [900, 0, 1000, 100], "confidence": 0.99, "issues": []}
                ],
            },
            {
                "fragmentKey": "p1",
                "status": "located",
                "confidence": 0.99,
                "issues": [],
                "regions": [
                    {"bbox": [600, 500, 800, 700], "confidence": 0.99, "issues": []},
                    {"bbox": [600, 100, 800, 300], "confidence": 0.99, "issues": []},
                    {"bbox": [100, 100, 300, 300], "confidence": 0.99, "issues": []},
                ],
            },
        ],
        [page_one, page_two],
    )

    assert result.evidence_complete is True
    assert [
        (region.page_number, region.y, region.x) for region in result.regions
    ] == pytest.approx(
        [
            (1, 0.25, 0.1),
            (1, 0.25, 0.6),
            (1, 0.45, 0.6),
            (2, 0.1, 0.9),
        ]
    )
    assert [region.model_candidate_index for region in result.regions] == [2, 1, 0, 0]


def test_projects_model_grid_through_fragment_offset_and_scale() -> None:
    fragment = CalculationSearchFragment(
        fragment_key="scaled-fragment",
        template_page_id="template-4",
        student_page_id="student-4",
        alignment_revision_id="alignment-4",
        page_number=4,
        student_page_number=7,
        x=0.125,
        y=0.2,
        width=0.5,
        height=0.4,
        sort_order=0,
    )
    result = _normalize(
        [_located_node("scaled-fragment", [200, 250, 800, 750])],
        [fragment],
    )

    assert result.evidence_complete is True
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.model_bbox == (200.0, 250.0, 800.0, 750.0)
    assert region.template_bbox == pytest.approx((0.225, 0.3, 0.3, 0.2))
    assert region.page_number == 4
    assert region.student_page_number == 7
    assert region.as_dict()["templateBBox"] == pytest.approx(
        {"x": 0.225, "y": 0.3, "width": 0.3, "height": 0.2}
    )


def test_all_exact_high_confidence_blank_windows_form_reliable_blank() -> None:
    fragments = [_fragment("p1"), _fragment("p2", page=2, sort_order=1)]
    result = _normalize(
        [
            {
                "fragmentKey": fragment.fragment_key,
                "status": "blank",
                "confidence": 0.95,
                "issues": [],
                "regions": [],
            }
            for fragment in fragments
        ],
        fragments,
    )

    assert result.status == "blank"
    assert result.reliable_blank is True
    assert result.regions == ()


def test_unknown_missing_and_duplicate_keys_are_structurally_incomplete() -> None:
    fragments = [_fragment("p1"), _fragment("p2", page=2, sort_order=1)]
    result = _normalize(
        [
            _located_node("p1"),
            _located_node("p1"),
            _located_node("unknown"),
        ],
        fragments,
    )

    assert result.status == "needs_review"
    assert result.evidence_complete is False
    codes = {issue.code for issue in result.issues}
    assert "calculation_fragment_key_duplicate" in codes
    assert "calculation_fragment_key_unknown" in codes
    assert "calculation_fragment_key_missing" in codes


def test_rejects_extra_fields_nonfinite_and_zero_area_bboxes() -> None:
    fragment = _fragment()
    extra = _located_node(fragment.fragment_key)
    extra["isBlank"] = False
    extra_result = _normalize([extra], [fragment])
    assert extra_result.evidence_complete is False
    assert "calculation_window_fields_invalid" in {issue.code for issue in extra_result.issues}

    for bbox in ([0, 0, 0, 10], [0, 0, 1001, 10], [0, 0, math.nan, 10]):
        result = _normalize([_located_node(fragment.fragment_key, bbox)], [fragment])
        assert result.evidence_complete is False
        assert "calculation_region_bbox_invalid" in {issue.code for issue in result.issues}


def test_deduplicates_high_iou_candidates_without_expanding_hull() -> None:
    fragment = _fragment()
    node = _located_node(fragment.fragment_key, [100, 100, 500, 500], confidence=0.8)
    regions = node["regions"]
    assert isinstance(regions, list)
    regions.append(
        {"bbox": [105, 105, 505, 505], "confidence": 0.95, "issues": []}
    )

    result = _normalize([node], [fragment])

    assert len(result.regions) == 1
    assert result.regions[0].confidence == 0.95
    assert result.regions[0].model_bbox == (105.0, 105.0, 505.0, 505.0)
    assert result.evidence_complete is True
    assert result.status == "needs_review"
    assert "calculation_region_duplicate" in {issue.code for issue in result.issues}


def test_failed_batch_helper_keeps_fragment_coverage_but_forces_incomplete_aggregate() -> None:
    first = _fragment("p1")
    second = _fragment("p2", page=2, sort_order=1)
    success = normalize_calculation_localization_batch(
        [_located_node("p1")],
        [first],
        batch_index=1,
        attempt_id="attempt-1",
        model_id="vision-model",
        prompt_version="locator-v1",
    )
    failed = failed_calculation_localization_batch(
        [second],
        batch_index=2,
        attempt_id="attempt-2",
        model_id="vision-model",
        prompt_version="locator-v1",
        issue_code="calculation_localization_batch_failed",
        issue_message="model timeout",
    )

    result = aggregate_calculation_localization_batches([first, second], [success, failed])

    assert result.evidence_complete is False
    assert result.status == "needs_review"
    assert [window.fragment_key for window in result.windows] == ["p1", "p2"]
    assert [region.fragment_key for region in result.regions] == ["p1"]
    assert "calculation_localization_batch_failed" in {issue.code for issue in result.issues}
