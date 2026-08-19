import random

import pytest
from PIL import Image, ImageDraw

from homework_judge.alignment import (
    AlignmentError,
    Homography,
    Point,
    align_pages,
    warp_student_to_template,
)
from homework_judge.alignment.geometry import homography_from_control_points


def _feature_rich_page() -> Image.Image:
    image = Image.new("RGB", (720, 960), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, 701, 941), outline="black", width=3)
    randomizer = random.Random(20260804)
    for index in range(90):
        x = randomizer.randint(45, 650)
        y = randomizer.randint(45, 890)
        radius = randomizer.randint(3, 12)
        if index % 3 == 0:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="black", width=2)
        elif index % 3 == 1:
            draw.line((x - radius, y, x + radius, y + radius), fill="black", width=2)
            draw.line((x, y - radius, x - radius, y + radius), fill="black", width=2)
        else:
            draw.text((x, y), f"Q{index}-{(index * 37) % 101}", fill="black")
    for row in range(7):
        y = 100 + row * 115
        draw.line((75, y, 640 - row * 9, y + row), fill=(80, 80, 80), width=2)
    return image


def test_orb_alignment_recovers_original_page_coordinates() -> None:
    pytest.importorskip("cv2")
    template = _feature_rich_page()
    expected = Homography.from_rows(
        (
            (0.965, -0.022, 24.0),
            (0.018, 0.972, 17.0),
            (0.000025, -0.000018, 1.0),
        )
    )
    student = template.transform(
        template.size,
        Image.Transform.PERSPECTIVE,
        expected.inverse.pillow_coefficients(),
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )
    ImageDraw.Draw(student).line((180, 470, 480, 530), fill=(20, 20, 120), width=7)

    result = align_pages(template, student, require_reliable=True)

    assert result.quality.method == "orb_homography_ransac"
    assert result.quality.is_reliable
    assert result.quality.inliers >= 8
    for point in (Point(80, 100), Point(350, 450), Point(620, 820)):
        actual = result.template_to_student.map_point(point)
        wanted = expected.map_point(point)
        assert actual.x == pytest.approx(wanted.x, abs=5.0)
        assert actual.y == pytest.approx(wanted.y, abs=5.0)


def test_warp_student_to_template_uses_inverse_sampling_direction() -> None:
    template = _feature_rich_page()
    expected = Homography.from_rows(((1.0, 0.0, 13.0), (0.0, 1.0, 9.0), (0.0, 0.0, 1.0)))
    student = template.transform(
        template.size,
        Image.Transform.PERSPECTIVE,
        expected.inverse.pillow_coefficients(),
        resample=Image.Resampling.NEAREST,
        fillcolor="white",
    )
    result = align_pages(template, student)

    rectified = warp_student_to_template(student, result, resample=Image.Resampling.NEAREST)

    assert rectified.size == template.size
    assert result.template_to_student.map_point(Point(100, 100)).x == pytest.approx(113, abs=3)


def test_blank_pages_return_explicit_low_quality_fallback() -> None:
    template = Image.new("RGB", (200, 300), "white")
    student = Image.new("RGB", (240, 360), "white")

    result = align_pages(template, student)

    assert result.quality.method == "ink_translation_fallback"
    assert not result.quality.is_reliable
    assert result.template_to_student.map_point(Point(100, 150)) == Point(120, 180)
    with pytest.raises(AlignmentError):
        align_pages(template, student, require_reliable=True)


def test_control_point_alignment_accepts_more_than_four_consistent_pairs() -> None:
    expected = Homography.from_rows(
        (
            (0.92, 0.03, 14.0),
            (-0.02, 0.88, 21.0),
            (0.0002, -0.0001, 1.0),
        )
    )
    template_points = (
        Point(0.0, 0.0),
        Point(320.0, 0.0),
        Point(320.0, 240.0),
        Point(0.0, 240.0),
        Point(160.0, 120.0),
        Point(80.0, 180.0),
    )
    student_points = tuple(expected.map_point(point) for point in template_points)

    actual = homography_from_control_points(template_points, student_points)

    for template_point, expected_student in zip(
        template_points,
        student_points,
        strict=True,
    ):
        mapped = actual.map_point(template_point)
        assert mapped.x == pytest.approx(expected_student.x, abs=1e-7)
        assert mapped.y == pytest.approx(expected_student.y, abs=1e-7)


@pytest.mark.parametrize("point_count", (0, 1, 2, 3))
def test_control_point_alignment_rejects_fewer_than_four_pairs(point_count: int) -> None:
    template_points = tuple(
        Point(float(index), float(index * index)) for index in range(point_count)
    )
    student_points = tuple(Point(point.x + 10.0, point.y + 20.0) for point in template_points)

    with pytest.raises(ValueError):
        homography_from_control_points(template_points, student_points)
