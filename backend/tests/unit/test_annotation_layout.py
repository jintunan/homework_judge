from __future__ import annotations

from homework_judge.artifacts.annotation_layout import (
    AnnotationMarkType,
    build_question_marks,
)
from homework_judge.grading.contracts import BoundingBox


def evidence(region: str = "region") -> list[dict[str, object]]:
    return [
        {
            "page_id": "page",
            "region_id": region,
            "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 100},
        }
    ]


def box_distance(first: BoundingBox, second: BoundingBox) -> float:
    horizontal = max(first.x - (second.x + second.width), second.x - (first.x + first.width), 0)
    vertical = max(first.y - (second.y + second.height), second.y - (first.y + first.height), 0)
    return max(horizontal, vertical)


def test_layout_maps_full_zero_partial_and_review_without_guessing() -> None:
    page_sizes = {"page": (1000, 1400)}

    full = build_question_marks(
        question_result_id="full-result",
        question_id="full",
        status="final",
        final_score=6,
        max_score=6,
        evidence=evidence("full-region"),
        error_locations=[],
        page_sizes=page_sizes,
        occupied={},
    )
    assert [item.mark_type for item in full] == [AnnotationMarkType.CHECK]
    assert "lead_line" not in full[0].model_dump()
    answer_box = BoundingBox.model_validate(evidence()[0]["original_bbox"])
    assert box_distance(full[0].box, answer_box) <= 20

    zero = build_question_marks(
        question_result_id="zero-result",
        question_id="zero",
        status="final",
        final_score=0,
        max_score=6,
        evidence=evidence("zero-region"),
        error_locations=evidence("zero-region"),
        page_sizes=page_sizes,
        occupied={},
    )
    assert [item.mark_type for item in zero] == [AnnotationMarkType.ERROR_CIRCLE]

    partial = build_question_marks(
        question_result_id="partial-result",
        question_id="partial",
        status="final",
        final_score=4,
        max_score=6,
        evidence=evidence("partial-region"),
        error_locations=evidence("partial-region"),
        page_sizes=page_sizes,
        occupied={},
    )
    assert {item.mark_type for item in partial} == {
        AnnotationMarkType.ERROR_CIRCLE,
        AnnotationMarkType.PARTIAL_SCORE,
    }
    assert (
        next(item for item in partial if item.mark_type is AnnotationMarkType.PARTIAL_SCORE).label
        == "4.00/6.00"
    )

    review = build_question_marks(
        question_result_id="review-result",
        question_id="review",
        status="needs_review",
        final_score=0,
        max_score=6,
        evidence=evidence("review-region"),
        error_locations=evidence("review-region"),
        page_sizes=page_sizes,
        occupied={},
    )
    assert review == []

    teacher_reviewed_without_location = build_question_marks(
        question_result_id="teacher-result",
        question_id="teacher-question",
        status="final",
        final_score=2,
        max_score=6,
        evidence=evidence("teacher-region"),
        error_locations=[],
        page_sizes=page_sizes,
        occupied={},
    )
    assert teacher_reviewed_without_location == []


def test_layout_keeps_every_mark_inside_original_page() -> None:
    marks = build_question_marks(
        question_result_id="result",
        question_id="question",
        status="final",
        final_score=1,
        max_score=3,
        evidence=[
            {
                "page_id": "page",
                "region_id": "region",
                "original_bbox": {"x": 960, "y": 1360, "width": 40, "height": 40},
            }
        ],
        error_locations=[
            {
                "page_id": "page",
                "region_id": "region",
                "original_bbox": {"x": 960, "y": 1360, "width": 40, "height": 40},
            }
        ],
        page_sizes={"page": (1000, 1400)},
        occupied={},
    )
    assert marks
    for mark in marks:
        assert 0 <= mark.box.x < 1000
        assert 0 <= mark.box.y < 1400
        assert mark.box.x + mark.box.width <= 1000
        assert mark.box.y + mark.box.height <= 1400


def test_layout_keeps_check_local_at_every_page_edge() -> None:
    anchors = [
        {"x": 0, "y": 500, "width": 120, "height": 50},
        {"x": 880, "y": 500, "width": 120, "height": 50},
        {"x": 400, "y": 0, "width": 200, "height": 50},
        {"x": 400, "y": 1350, "width": 200, "height": 50},
        {"x": 100, "y": 650, "width": 800, "height": 60},
    ]
    for index, anchor_value in enumerate(anchors):
        anchor = BoundingBox.model_validate(anchor_value)
        marks = build_question_marks(
            question_result_id=f"result-{index}",
            question_id=f"question-{index}",
            status="final",
            final_score=1,
            max_score=1,
            evidence=[
                {
                    "page_id": "page",
                    "region_id": f"region-{index}",
                    "original_bbox": anchor_value,
                }
            ],
            error_locations=[],
            page_sizes={"page": (1000, 1400)},
            occupied={},
        )
        assert len(marks) == 1
        mark = marks[0]
        assert mark.mark_type is AnnotationMarkType.CHECK
        assert box_distance(mark.box, anchor) <= 20
        assert mark.box.x + mark.box.width <= 1000
        assert mark.box.y + mark.box.height <= 1400


def test_layout_uses_nearest_local_fallback_when_adjacent_spaces_are_occupied() -> None:
    anchor_value = {"x": 400, "y": 600, "width": 200, "height": 80}
    anchor = BoundingBox.model_validate(anchor_value)
    occupied = {
        "page": [
            BoundingBox(x=600, y=560, width=100, height=160),
            BoundingBox(x=300, y=560, width=100, height=160),
            BoundingBox(x=380, y=500, width=240, height=100),
            BoundingBox(x=380, y=680, width=240, height=100),
        ]
    }
    marks = build_question_marks(
        question_result_id="dense-result",
        question_id="dense-question",
        status="final",
        final_score=1,
        max_score=1,
        evidence=[
            {
                "page_id": "page",
                "region_id": "dense-region",
                "original_bbox": anchor_value,
            }
        ],
        error_locations=[],
        page_sizes={"page": (1000, 1400)},
        occupied=occupied,
    )

    assert len(marks) == 1
    assert box_distance(marks[0].box, anchor) <= 20
    assert marks[0].box.x < 750
