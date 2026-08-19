import pytest

from homework_judge.alignment import Homography, Point, Polygon
from homework_judge.alignment.geometry import (
    Bounds,
    clip_polygon_to_bounds,
    homography_from_control_points,
    polygon_area,
    polygon_intersection_area,
    polygon_intersection_ratio,
    polygon_out_of_bounds_ratio,
    polygon_visible_ratio,
)


def test_homography_round_trip_preserves_points() -> None:
    transform = Homography.from_rows(
        (
            (1.08, 0.04, 37.0),
            (-0.02, 0.96, 21.0),
            (0.00012, -0.00008, 1.0),
        )
    )
    source = Point(420.5, 731.25)

    mapped = transform.map_point(source)
    restored = transform.inverse.map_point(mapped)

    assert restored.x == pytest.approx(source.x, abs=1e-7)
    assert restored.y == pytest.approx(source.y, abs=1e-7)


def test_polygon_bounds_follow_projective_mapping() -> None:
    transform = Homography.from_rows(((2.0, 0.0, 10.0), (0.0, 3.0, 20.0), (0.0, 0.0, 1.0)))
    polygon = Polygon.rectangle(5.0, 7.0, 15.0, 17.0)

    mapped = transform.map_polygon(polygon)

    assert mapped.bounds.left == pytest.approx(20.0)
    assert mapped.bounds.top == pytest.approx(41.0)
    assert mapped.bounds.right == pytest.approx(40.0)
    assert mapped.bounds.bottom == pytest.approx(71.0)


def test_then_composes_transforms_in_reading_order() -> None:
    translate = Homography.from_rows(((1.0, 0.0, 4.0), (0.0, 1.0, 7.0), (0.0, 0.0, 1.0)))
    scale = Homography.scale(2.0, 3.0)

    combined = translate.then(scale)

    assert combined.map_point(Point(1.0, 2.0)) == Point(10.0, 27.0)


@pytest.mark.parametrize(
    "rows",
    [
        ((1.0, 2.0, 3.0), (2.0, 4.0, 6.0), (0.0, 0.0, 1.0)),
        ((1.0, 0.0, float("nan")), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ],
    ids=("singular", "non-finite"),
)
def test_homography_rejects_non_invertible_or_non_finite_matrices(
    rows: tuple[tuple[float, float, float], ...],
) -> None:
    with pytest.raises(ValueError):
        Homography.from_rows(rows)


@pytest.mark.parametrize(
    ("invalid_points", "message"),
    [
        (
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 0.0)),
            "distinct",
        ),
        (
            ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (150.0, 0.0)),
            "degenerate",
        ),
        (
            ((0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)),
            "boundary order",
        ),
        (
            ((0.0, 0.0), (100.0, 0.0), (40.0, 40.0), (0.0, 100.0)),
            "convex",
        ),
    ],
    ids=("duplicate", "collinear", "self-crossing", "concave"),
)
def test_four_point_homography_rejects_degenerate_or_misordered_source_points(
    invalid_points: tuple[tuple[float, float], ...],
    message: str,
) -> None:
    destination = ((20.0, 30.0), (240.0, 20.0), (220.0, 180.0), (30.0, 200.0))

    with pytest.raises(ValueError, match=message):
        homography_from_control_points(invalid_points, destination)


def test_four_point_homography_rejects_mismatched_winding() -> None:
    template = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    mirrored_student_order = (
        (20.0, 30.0),
        (30.0, 200.0),
        (220.0, 180.0),
        (240.0, 20.0),
    )

    with pytest.raises(ValueError, match="same winding"):
        homography_from_control_points(template, mirrored_student_order)


def test_control_point_homography_maps_template_to_student_not_reverse() -> None:
    template = (
        Point(0.0, 0.0),
        Point(200.0, 0.0),
        Point(200.0, 120.0),
        Point(0.0, 120.0),
    )
    student = (
        Point(20.0, 30.0),
        Point(250.0, 10.0),
        Point(220.0, 190.0),
        Point(35.0, 205.0),
    )

    template_to_student = homography_from_control_points(template, student)

    for template_point, student_point in zip(template, student, strict=True):
        mapped = template_to_student.map_point(template_point)
        restored = template_to_student.inverse.map_point(student_point)
        assert mapped.x == pytest.approx(student_point.x, abs=1e-8)
        assert mapped.y == pytest.approx(student_point.y, abs=1e-8)
        assert restored.x == pytest.approx(template_point.x, abs=1e-8)
        assert restored.y == pytest.approx(template_point.y, abs=1e-8)

    assert template_to_student.map_point(template[0]) != template[0]


def test_control_point_homography_is_stable_when_coordinate_origin_is_far_away() -> None:
    template = (
        Point(1_000_000_000.0, 1_000_000_000.0),
        Point(1_000_002_000.0, 1_000_000_000.0),
        Point(1_000_002_000.0, 1_000_003_000.0),
        Point(1_000_000_000.0, 1_000_003_000.0),
    )
    student = (
        Point(20.0, 30.0),
        Point(2050.0, 10.0),
        Point(2020.0, 3100.0),
        Point(35.0, 3005.0),
    )

    transform = homography_from_control_points(template, student)

    for source, expected in zip(template, student, strict=True):
        mapped = transform.map_point(source)
        assert mapped.x == pytest.approx(expected.x, abs=1e-6)
        assert mapped.y == pytest.approx(expected.y, abs=1e-6)


@pytest.mark.parametrize(
    ("transform", "expected_area"),
    [
        (Homography.identity(), 200.0),
        (Homography.from_rows(((1.0, 0.0, 17.0), (0.0, 1.0, -9.0), (0.0, 0.0, 1.0))), 200.0),
        (Homography.scale(2.0, 3.0), 1200.0),
    ],
    ids=("identity", "translation", "scale"),
)
def test_mapped_polygon_area_follows_affine_transform(
    transform: Homography,
    expected_area: float,
) -> None:
    source = Polygon.rectangle(5.0, 10.0, 25.0, 20.0)

    mapped = transform.map_polygon(source)

    assert polygon_area(mapped) == pytest.approx(expected_area)
    assert mapped.area == pytest.approx(expected_area)


def test_projective_mapping_produces_finite_positive_polygon_area() -> None:
    transform = homography_from_control_points(
        ((0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)),
        ((15.0, 20.0), (240.0, 8.0), (210.0, 155.0), (30.0, 170.0)),
    )

    mapped = transform.map_polygon(Polygon.rectangle(40.0, 20.0, 160.0, 80.0))

    assert mapped.area > 0.0
    assert mapped.area == pytest.approx(polygon_area(mapped.points))


@pytest.mark.parametrize(
    ("polygon", "expected_clipped_area", "expected_visible", "expected_outside"),
    [
        (Polygon.rectangle(10.0, 10.0, 30.0, 30.0), 400.0, 1.0, 0.0),
        (Polygon.rectangle(-10.0, 10.0, 10.0, 30.0), 200.0, 0.5, 0.5),
        (Polygon.rectangle(-30.0, 10.0, -10.0, 30.0), 0.0, 0.0, 1.0),
        (
            Polygon.rectangle(-10.0, -10.0, 110.0, 110.0),
            10_000.0,
            10_000.0 / 14_400.0,
            4_400.0 / 14_400.0,
        ),
    ],
    ids=("inside", "half-clipped", "fully-outside", "surrounds-page"),
)
def test_polygon_clipping_and_out_of_bounds_ratios(
    polygon: Polygon,
    expected_clipped_area: float,
    expected_visible: float,
    expected_outside: float,
) -> None:
    page = Bounds(0.0, 0.0, 100.0, 100.0)

    clipped = clip_polygon_to_bounds(polygon, page)

    assert (0.0 if clipped is None else clipped.area) == pytest.approx(expected_clipped_area)
    assert polygon_visible_ratio(polygon, page) == pytest.approx(expected_visible)
    assert polygon_out_of_bounds_ratio(polygon, page) == pytest.approx(expected_outside)
    if clipped is not None:
        assert all(0.0 <= point.x <= 100.0 for point in clipped.points)
        assert all(0.0 <= point.y <= 100.0 for point in clipped.points)


@pytest.mark.parametrize(
    ("left", "right", "expected_area", "expected_ratio"),
    [
        (
            Polygon.rectangle(0.0, 0.0, 10.0, 10.0),
            Polygon.rectangle(20.0, 0.0, 30.0, 10.0),
            0.0,
            0.0,
        ),
        (
            Polygon.rectangle(0.0, 0.0, 10.0, 10.0),
            Polygon.rectangle(0.0, 0.0, 10.0, 10.0),
            100.0,
            1.0,
        ),
        (
            Polygon.rectangle(0.0, 0.0, 10.0, 10.0),
            Polygon.rectangle(5.0, 0.0, 15.0, 10.0),
            50.0,
            0.5,
        ),
        (
            Polygon.rectangle(0.0, 0.0, 20.0, 20.0),
            Polygon.rectangle(5.0, 5.0, 10.0, 10.0),
            25.0,
            1.0,
        ),
        (
            Polygon.rectangle(0.0, 0.0, 10.0, 10.0),
            Polygon.rectangle(10.0, 0.0, 20.0, 10.0),
            0.0,
            0.0,
        ),
    ],
    ids=("disjoint", "identical", "half-overlap", "contained", "edge-touch"),
)
def test_polygon_intersection_uses_smaller_area_denominator(
    left: Polygon,
    right: Polygon,
    expected_area: float,
    expected_ratio: float,
) -> None:
    assert polygon_intersection_area(left, right) == pytest.approx(expected_area)
    assert polygon_intersection_area(right, left) == pytest.approx(expected_area)
    assert polygon_intersection_ratio(left, right) == pytest.approx(expected_ratio)
    assert polygon_intersection_ratio(right, left) == pytest.approx(expected_ratio)
