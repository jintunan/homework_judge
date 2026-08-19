from __future__ import annotations

import math
from typing import Any

from homework_judge.question_frames import validate_question_frame_set

TEMPLATE_PAGES = {"template-page-1": 1, "template-page-2": 2}


def fragment(
    region_key: str,
    *,
    template_page_id: str = "template-page-1",
    page_number: int = 1,
    x: float = 0.1,
    y: float = 0.1,
    width: float = 0.3,
    height: float = 0.2,
    sort_order: int = 0,
) -> dict[str, Any]:
    return {
        "regionKey": region_key,
        "templatePageId": template_page_id,
        "pageNumber": page_number,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "sortOrder": sort_order,
    }


def item(
    question_id: str,
    question_number: str,
    fragments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "questionId": question_id,
        "questionNumber": question_number,
        "fragments": fragments,
    }


def test_accepts_valid_cross_page_fragments() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-8",
                "8",
                [
                    fragment("Q8-P1", sort_order=0),
                    fragment(
                        "Q8-P2",
                        template_page_id="template-page-2",
                        page_number=2,
                        y=0.05,
                        sort_order=1,
                    ),
                ],
            )
        ],
        TEMPLATE_PAGES,
    )

    assert issues == []


def test_rejects_unknown_page_and_page_number_mismatch_with_fragment_context() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-11",
                "11",
                [
                    fragment("Q11-UNKNOWN", template_page_id="missing-page"),
                    fragment("Q11-MISMATCH", page_number=2, sort_order=1),
                ],
            )
        ],
        TEMPLATE_PAGES,
    )

    assert [(issue.code, issue.question_number, issue.region_key) for issue in issues] == [
        ("frame_page_unknown", "11", "Q11-UNKNOWN"),
        ("frame_page_mismatch", "11", "Q11-MISMATCH"),
    ]
    assert issues[0].as_dict()["questionNumber"] == "11"
    assert issues[0].as_dict()["regionKey"] == "Q11-UNKNOWN"


def test_rejects_non_integer_or_non_positive_page_numbers() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-1",
                "1",
                [
                    fragment("Q1-BOOL", page_number=True),
                    fragment("Q1-ZERO", page_number=0, sort_order=1),
                ],
            )
        ],
        TEMPLATE_PAGES,
    )

    assert [issue.code for issue in issues] == [
        "frame_page_number_invalid",
        "frame_page_number_invalid",
    ]


def test_rejects_nan_infinity_and_non_numeric_coordinates() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-2",
                "2",
                [
                    fragment("Q2-NAN", x=math.nan),
                    fragment("Q2-INF", y=math.inf, sort_order=1),
                    {**fragment("Q2-TEXT", sort_order=2), "width": "wide"},
                ],
            )
        ],
        TEMPLATE_PAGES,
    )

    assert [(issue.code, issue.region_key) for issue in issues] == [
        ("frame_coordinate_not_finite", "Q2-NAN"),
        ("frame_coordinate_not_finite", "Q2-INF"),
        ("frame_coordinate_invalid", "Q2-TEXT"),
    ]


def test_rejects_zero_negative_area_and_out_of_bounds_boxes() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-3",
                "3",
                [
                    fragment("Q3-ZERO", width=0),
                    fragment("Q3-NEGATIVE", height=-0.1, sort_order=1),
                    fragment("Q3-LEFT", x=-0.01, sort_order=2),
                    fragment("Q3-RIGHT", x=0.8, width=0.3, sort_order=3),
                ],
            )
        ],
        TEMPLATE_PAGES,
    )

    assert [(issue.code, issue.region_key) for issue in issues] == [
        ("frame_area_non_positive", "Q3-ZERO"),
        ("frame_area_non_positive", "Q3-NEGATIVE"),
        ("frame_out_of_bounds", "Q3-LEFT"),
        ("frame_out_of_bounds", "Q3-RIGHT"),
    ]


def test_rejects_duplicate_region_keys_and_sort_orders() -> None:
    issues = validate_question_frame_set(
        [
            item(
                "question-4",
                "4",
                [
                    fragment("SHARED", sort_order=0),
                    fragment("Q4-P2", y=0.4, sort_order=0),
                ],
            ),
            item("question-5", "5", [fragment("SHARED", y=0.7)]),
        ],
        TEMPLATE_PAGES,
    )

    assert {(issue.code, issue.question_number, issue.region_key) for issue in issues} == {
        ("frame_sort_order_duplicate", "4", "Q4-P2"),
        ("frame_region_key_duplicate", "5", "SHARED"),
    }
    duplicate = next(issue for issue in issues if issue.code == "frame_region_key_duplicate")
    assert duplicate.related_question_number == "4"
    assert duplicate.related_region_key == "SHARED"


def test_reports_severe_cross_question_overlap_once_with_both_regions() -> None:
    issues = validate_question_frame_set(
        [
            item("question-6", "6", [fragment("Q6-P1", x=0.1, width=0.5)]),
            item("question-7", "7", [fragment("Q7-P1", x=0.35, width=0.5)]),
        ],
        TEMPLATE_PAGES,
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "frame_cross_question_overlap"
    assert (issue.question_number, issue.region_key) == ("6", "Q6-P1")
    assert (issue.related_question_number, issue.related_region_key) == ("7", "Q7-P1")
    assert issue.details["overlapRatio"] == 0.5


def test_allows_small_edge_tolerance_and_equal_coordinates_on_different_pages() -> None:
    issues = validate_question_frame_set(
        [
            item("question-8", "8", [fragment("Q8-P1", x=0.1, width=0.5)]),
            item("question-9", "9", [fragment("Q9-P1", x=0.599, width=0.3)]),
            item(
                "question-10",
                "10",
                [
                    fragment(
                        "Q10-P2",
                        template_page_id="template-page-2",
                        page_number=2,
                    )
                ],
            ),
        ],
        TEMPLATE_PAGES,
    )

    assert issues == []


def test_rejects_missing_keys_and_invalid_sort_order_without_raising() -> None:
    raw = fragment("", sort_order=-1)
    raw["templatePageId"] = ""
    issues = validate_question_frame_set(
        [item("question-12", "12", [raw])],
        TEMPLATE_PAGES,
    )

    assert {(issue.code, issue.question_number) for issue in issues} == {
        ("frame_region_key_missing", "12"),
        ("frame_sort_order_invalid", "12"),
        ("frame_page_id_missing", "12"),
    }
