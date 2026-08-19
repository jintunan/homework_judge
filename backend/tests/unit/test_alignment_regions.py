import pytest
from PIL import Image, ImageDraw

from homework_judge.alignment import (
    AlignmentQuality,
    AlignmentResult,
    AnswerRegion,
    Homography,
    PageSize,
    Polygon,
    extract_answer_regions,
    map_answer_regions,
    parse_question_regions,
)
from homework_judge.alignment.models import FramePageAlignment, FrameSetMappingResult
from homework_judge.alignment.regions import map_confirmed_frame_set
from homework_judge.config import Settings


def alignment(transform: Homography) -> AlignmentResult:
    return AlignmentResult.create(
        transform,
        PageSize(200, 150),
        PageSize(240, 180),
        AlignmentQuality(
            method="test",
            score=1.0,
            matched_features=20,
            inliers=20,
            inlier_ratio=1.0,
            mean_reprojection_error_px=0.0,
            template_feature_coverage=0.5,
            student_feature_coverage=0.5,
            visible_template_ratio=1.0,
            is_reliable=True,
        ),
    )


def test_parser_supports_multiple_regions_and_common_rectangle_forms() -> None:
    questions = {
        "q1": {
            "page_number": 2,
            "answer_regions": [
                {"id": "choice", "left": 10, "top": 20, "right": 30, "bottom": 40},
                {"id": "work", "bbox": [40, 50, 25, 30], "bbox_format": "xywh"},
            ],
        },
        "q2": {
            "regions": {
                "first_blank": {
                    "page": 3,
                    "points": [[1, 2], [11, 2], [11, 8], [1, 8]],
                }
            }
        },
        "q3": {"number": "3", "type": "calculation"},
    }

    regions = parse_question_regions(questions)

    assert [(item.question_id, item.region_id, item.page_number) for item in regions] == [
        ("q1", "choice", 2),
        ("q1", "work", 2),
        ("q2", "first_blank", 3),
    ]
    assert regions[1].template_polygon.bounds.right == 65
    assert regions[1].template_polygon.bounds.bottom == 80


def test_existing_questions_without_region_metadata_are_accepted() -> None:
    assert parse_question_regions({"q1": {"number": "1", "type": "single_choice"}}) == []


def test_multi_region_mapping_retains_unclipped_original_page_coordinates() -> None:
    result = alignment(Homography.from_rows(((1.0, 0.0, 30.0), (0.0, 1.0, 15.0), (0.0, 0.0, 1.0))))
    regions = [
        AnswerRegion.rectangle("q1", "q1-a", 1, 10, 20, 50, 40),
        AnswerRegion.rectangle("q1", "q1-b", 1, 190, 140, 210, 160),
    ]

    mapped = map_answer_regions(regions, result)

    assert mapped[0].original_page_bbox.as_dict() == {
        "left": 40.0,
        "top": 35.0,
        "right": 80.0,
        "bottom": 55.0,
        "width": 40.0,
        "height": 20.0,
    }
    assert mapped[1].original_page_bbox.right == 240.0
    assert mapped[1].original_page_bbox.bottom == 175.0
    assert mapped[1].visible_original_page_bbox.right == 240.0
    metadata = mapped[0].as_dict()
    assert metadata["coordinate_space"] == "student_original_page_pixels"
    assert len(metadata["original_page_polygon"]) == 4


def test_extraction_rectifies_crop_but_keeps_original_coordinates() -> None:
    result = alignment(Homography.from_rows(((1.0, 0.0, 30.0), (0.0, 1.0, 15.0), (0.0, 0.0, 1.0))))
    student = Image.new("RGB", (240, 180), "white")
    ImageDraw.Draw(student).rectangle((50, 45, 89, 64), fill="red")
    region = AnswerRegion.rectangle("q1", "q1-a", 1, 20, 30, 60, 50)

    extracted = extract_answer_regions(student, [region], result)

    assert len(extracted) == 1
    assert extracted[0].image.size == (40, 20)
    assert extracted[0].image.getpixel((20, 10))[0] > 200
    assert extracted[0].image.getpixel((20, 10))[1] < 40
    assert extracted[0].mapping.original_page_bbox.left == 50.0
    assert extracted[0].mapping.original_page_bbox.top == 45.0
    assert extracted[0].metadata()["template_crop_box"] == [20, 30, 60, 50]


def frame_fragment(
    frame_region_id: str,
    *,
    region_key: str,
    template_page_id: str = "template-page-1",
    page_number: int = 1,
    x: float = 0.1,
    y: float = 0.1,
    width: float = 0.2,
    height: float = 0.2,
    sort_order: int = 0,
) -> dict[str, object]:
    return {
        "id": frame_region_id,
        "regionKey": region_key,
        "templatePageId": template_page_id,
        "pageNumber": page_number,
        "coordinateSpace": "template_page_normalized",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "sortOrder": sort_order,
    }


def confirmed_frame_set(
    *items: tuple[str, list[dict[str, object]]],
) -> dict[str, object]:
    return {
        "id": "frame-set-1",
        "status": "confirmed",
        "items": [
            {
                "id": f"frame-item-{index}",
                "questionId": question_id,
                "status": "confirmed",
                "fragments": fragments,
            }
            for index, (question_id, fragments) in enumerate(items, 1)
        ],
    }


def frame_alignment(
    template_page_id: str = "template-page-1",
    *,
    page_number: int = 1,
    student_page_id: str = "student-page-1",
    transform: Homography | tuple[tuple[float, float, float], ...] | None = None,
    score: float = 1.0,
    reliable: bool = True,
    template_size: PageSize = PageSize(100, 100),
    student_size: PageSize = PageSize(100, 100),
) -> FramePageAlignment:
    return FramePageAlignment(
        template_page_id=template_page_id,
        template_page_number=page_number,
        student_page_id=student_page_id,
        alignment_revision_id=f"alignment-{student_page_id}",
        template_size=template_size,
        student_size=student_size,
        template_to_student=transform or Homography.identity(),
        quality=AlignmentQuality(
            method="test",
            score=score,
            matched_features=20,
            inliers=20,
            inlier_ratio=1.0,
            mean_reprojection_error_px=0.0,
            template_feature_coverage=0.5,
            student_feature_coverage=0.5,
            visible_template_ratio=1.0,
            is_reliable=reliable,
            warnings=() if reliable else ("test low quality",),
        ),
    )


def map_frame_set(
    frame_set: dict[str, object],
    page_alignments: dict[str, FramePageAlignment],
    *,
    min_alignment_score: float = 0.55,
    min_polygon_area_px: float = 16.0,
    min_visible_ratio: float = 0.8,
    max_out_of_bounds_ratio: float = 0.2,
    max_cross_question_overlap_ratio: float = 0.1,
):
    return map_confirmed_frame_set(
        frame_set,
        page_alignments,
        min_alignment_score=min_alignment_score,
        min_polygon_area_px=min_polygon_area_px,
        min_visible_ratio=min_visible_ratio,
        max_out_of_bounds_ratio=max_out_of_bounds_ratio,
        max_cross_question_overlap_ratio=max_cross_question_overlap_ratio,
    )


def blocker_codes(result: FrameSetMappingResult) -> set[str]:
    return {blocker.code for blocker in result.blockers}


def test_confirmed_frame_set_maps_every_fragment_across_pages_with_provenance() -> None:
    frame_set = confirmed_frame_set(
        (
            "question-alpha",
            [
                frame_fragment("frame-region-a2", region_key="part-2", sort_order=1),
                frame_fragment("frame-region-a1", region_key="part-1", sort_order=0),
            ],
        ),
        (
            "question-beta",
            [
                frame_fragment(
                    "frame-region-b1",
                    region_key="whole",
                    template_page_id="template-page-2",
                    page_number=2,
                    x=0.5,
                    y=0.5,
                    width=0.25,
                    height=0.25,
                )
            ],
        ),
    )
    page_alignments = {
        "template-page-1": frame_alignment(
            transform=Homography.from_rows(
                ((1.0, 0.0, 10.0), (0.0, 1.0, 5.0), (0.0, 0.0, 1.0))
            ),
            student_size=PageSize(120, 110),
        ),
        "template-page-2": frame_alignment(
            "template-page-2",
            page_number=2,
            student_page_id="student-page-2",
            transform=Homography.scale(2.0, 2.0),
            student_size=PageSize(200, 200),
        ),
    }

    result = map_frame_set(frame_set, page_alignments)

    assert result.status == "ready"
    assert result.blockers == ()
    assert [mapping.frame_region_id for mapping in result.mappings] == [
        "frame-region-a1",
        "frame-region-a2",
        "frame-region-b1",
    ]
    first = result.mappings[0]
    assert first.frame_set_id == "frame-set-1"
    assert first.alignment_revision_id == "alignment-student-page-1"
    assert first.original_page_polygon.as_dicts() == [
        {"x": 20.0, "y": 15.0},
        {"x": 40.0, "y": 15.0},
        {"x": 40.0, "y": 35.0},
        {"x": 20.0, "y": 35.0},
    ]
    assert first.original_page_bbox.as_dict() == {
        "left": 20.0,
        "top": 15.0,
        "right": 40.0,
        "bottom": 35.0,
        "width": 20.0,
        "height": 20.0,
    }
    assert first.visible_ratio == pytest.approx(1.0)
    assert first.out_of_bounds_ratio == pytest.approx(0.0)
    assert first.max_cross_question_overlap_ratio == pytest.approx(0.0)
    serialized = first.as_dict()
    assert serialized["frameRegionId"] == "frame-region-a1"
    assert serialized["coordinateSpace"] == "student_original_page_pixels"
    assert serialized["templatePageBbox"] == pytest.approx(
        {
            "left": 10.0,
            "top": 10.0,
            "right": 30.0,
            "bottom": 30.0,
            "width": 20.0,
            "height": 20.0,
        }
    )
    assert serialized["quality"]["mappedAreaPx"] == pytest.approx(400.0)


@pytest.mark.parametrize(
    "transform",
    [
        Homography.identity(),
        Homography.from_rows(
            ((1.0, 0.0, 12.0), (0.0, 1.0, 7.0), (0.0, 0.0, 1.0))
        ),
        Homography.scale(2.0, 1.5),
        Homography.from_rows(
            ((1.0, 0.05, 5.0), (0.02, 1.0, 3.0), (0.001, 0.0005, 1.0))
        ),
    ],
    ids=("identity", "translation", "scale", "perspective"),
)
def test_batch_mapping_supports_normal_geometric_transform_families(
    transform: Homography,
) -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    page = frame_alignment(transform=transform, student_size=PageSize(300, 300))

    result = map_frame_set(frame_set, {page.template_page_id: page})

    expected = transform.map_polygon(Polygon.rectangle(10.0, 10.0, 30.0, 30.0))
    assert result.status == "ready"
    for actual_point, expected_point in zip(
        result.mappings[0].original_page_polygon.points,
        expected.points,
        strict=True,
    ):
        assert actual_point.as_tuple() == pytest.approx(expected_point.as_tuple())


def test_mapping_missing_page_returns_structured_blocker() -> None:
    frame_set = confirmed_frame_set(
        (
            "question-alpha",
            [
                frame_fragment(
                    "frame-region-a1",
                    region_key="whole",
                    template_page_id="missing-template-page",
                )
            ],
        )
    )

    result = map_frame_set(frame_set, {})

    assert result.status == "mapping_needs_review"
    assert result.mappings == ()
    assert blocker_codes(result) == {"mapping_page_missing"}
    assert result.blockers[0].as_dict()["frameRegionId"] == "frame-region-a1"


def test_mapping_non_invertible_matrix_returns_structured_blocker() -> None:
    singular = ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    page = frame_alignment(transform=singular)

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert result.mappings == ()
    assert blocker_codes(result) == {"mapping_transform_not_invertible"}


def test_mapping_transform_crossing_infinity_returns_structured_blocker() -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    crosses_frame = Homography.from_rows(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.05, 0.0, -1.0))
    )
    page = frame_alignment(transform=crosses_frame)

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert result.mappings == ()
    assert blocker_codes(result) == {"mapping_transform_invalid"}


def test_mapping_low_alignment_quality_is_blocking_but_keeps_preview_geometry() -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    page = frame_alignment(score=0.3, reliable=False)

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert len(result.mappings) == 1
    assert result.mappings[0].status == "needs_review"
    assert result.mappings[0].issues == ("mapping_alignment_low_quality",)
    assert blocker_codes(result) == {"mapping_alignment_low_quality"}


def test_mapping_degenerate_polygon_is_blocking() -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    page = frame_alignment(transform=Homography.scale(0.01, 0.01))

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert result.mappings[0].mapped_area_px == pytest.approx(0.04)
    assert blocker_codes(result) == {"mapping_polygon_degenerate"}


@pytest.mark.parametrize(
    ("min_visible_ratio", "max_out_of_bounds_ratio", "expected_code"),
    [
        (0.8, 1.0, "mapping_severe_clipping"),
        (0.0, 0.2, "mapping_out_of_bounds"),
    ],
)
def test_mapping_clipping_and_out_of_bounds_are_independently_blocking(
    min_visible_ratio: float,
    max_out_of_bounds_ratio: float,
    expected_code: str,
) -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    page = frame_alignment(
        transform=Homography.from_rows(
            ((1.0, 0.0, -20.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        )
    )

    result = map_frame_set(
        frame_set,
        {page.template_page_id: page},
        min_visible_ratio=min_visible_ratio,
        max_out_of_bounds_ratio=max_out_of_bounds_ratio,
    )

    assert result.status == "mapping_needs_review"
    assert result.mappings[0].visible_ratio == pytest.approx(0.5)
    assert result.mappings[0].out_of_bounds_ratio == pytest.approx(0.5)
    assert blocker_codes(result) == {expected_code}


def test_cross_question_overlap_blocks_both_regions_but_same_question_overlap_does_not() -> None:
    frame_set = confirmed_frame_set(
        (
            "question-alpha",
            [
                frame_fragment(
                    "frame-region-a1",
                    region_key="part-1",
                    x=0.1,
                    y=0.1,
                    width=0.4,
                    height=0.4,
                ),
                frame_fragment(
                    "frame-region-a2",
                    region_key="part-2",
                    x=0.2,
                    y=0.2,
                    width=0.2,
                    height=0.2,
                    sort_order=1,
                ),
            ],
        ),
        (
            "question-beta",
            [
                frame_fragment(
                    "frame-region-b1",
                    region_key="whole",
                    x=0.3,
                    y=0.1,
                    width=0.4,
                    height=0.4,
                )
            ],
        ),
    )
    page = frame_alignment()

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert blocker_codes(result) == {"mapping_cross_question_overlap"}
    by_id = {mapping.frame_region_id: mapping for mapping in result.mappings}
    assert by_id["frame-region-a1"].max_cross_question_overlap_ratio == pytest.approx(0.5)
    assert by_id["frame-region-b1"].max_cross_question_overlap_ratio == pytest.approx(0.5)
    assert by_id["frame-region-a2"].max_cross_question_overlap_ratio == pytest.approx(0.5)
    assert "mapping_cross_question_overlap" in by_id["frame-region-a1"].issues
    assert "mapping_cross_question_overlap" in by_id["frame-region-b1"].issues


def test_unconfirmed_frame_set_is_rejected_before_mapping() -> None:
    frame_set = confirmed_frame_set(
        ("question-alpha", [frame_fragment("frame-region-a1", region_key="whole")])
    )
    frame_set["status"] = "draft"
    page = frame_alignment()

    result = map_frame_set(frame_set, {page.template_page_id: page})

    assert result.status == "mapping_needs_review"
    assert result.mappings == ()
    assert blocker_codes(result) == {"frame_set_not_confirmed"}


def test_mapping_thresholds_are_loaded_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAPPING_MIN_ALIGNMENT_SCORE", "0.61")
    monkeypatch.setenv("MAPPING_MIN_POLYGON_AREA_PX", "12.5")
    monkeypatch.setenv("MAPPING_MIN_VISIBLE_RATIO", "0.82")
    monkeypatch.setenv("MAPPING_MAX_OUT_OF_BOUNDS_RATIO", "0.18")
    monkeypatch.setenv("MAPPING_MAX_CROSS_QUESTION_OVERLAP_RATIO", "0.07")

    settings = Settings.load()

    assert settings.mapping_min_alignment_score == pytest.approx(0.61)
    assert settings.mapping_min_polygon_area_px == pytest.approx(12.5)
    assert settings.mapping_min_visible_ratio == pytest.approx(0.82)
    assert settings.mapping_max_out_of_bounds_ratio == pytest.approx(0.18)
    assert settings.mapping_max_cross_question_overlap_ratio == pytest.approx(0.07)
