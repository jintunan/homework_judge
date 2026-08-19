from __future__ import annotations

import math
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageChops, ImageOps

from .geometry import Homography, Point, Polygon
from .models import AlignmentQuality, AlignmentResult, PageSize

type PageInput = Image.Image | str | Path


class AlignmentError(RuntimeError):
    """Raised when a reliable page transform was requested but could not be obtained."""


def align_pages(
    template_page: PageInput,
    student_page: PageInput,
    *,
    max_feature_dimension: int = 1400,
    max_features: int = 5000,
    ratio_test: float = 0.78,
    ransac_threshold_px: float = 4.0,
    require_reliable: bool = False,
) -> AlignmentResult:
    """Align a blank template page to a student page.

    The returned homography always maps template coordinates to coordinates on the
    unwarped, original student page. OpenCV is used when available. A deterministic
    scale/translation ink-overlap fallback keeps the coordinate API usable in a
    Pillow-only installation and reports its limited confidence in ``quality``.
    """

    if max_feature_dimension < 128:
        raise ValueError("max_feature_dimension must be at least 128")
    if max_features < 32:
        raise ValueError("max_features must be at least 32")
    if not 0.0 < ratio_test < 1.0:
        raise ValueError("ratio_test must be between zero and one")
    if ransac_threshold_px <= 0:
        raise ValueError("ransac_threshold_px must be positive")

    template = _load_page(template_page)
    student = _load_page(student_page)
    result, feature_warning = _feature_alignment(
        template,
        student,
        max_dimension=max_feature_dimension,
        max_features=max_features,
        ratio_test=ratio_test,
        ransac_threshold_px=ransac_threshold_px,
    )
    if result is None:
        result = _ink_translation_alignment(template, student, feature_warning)
    if require_reliable and not result.quality.is_reliable:
        details = "; ".join(result.quality.warnings) or "quality thresholds were not met"
        raise AlignmentError(f"page alignment is not reliable: {details}")
    return result


def warp_student_to_template(
    student_page: PageInput,
    alignment: AlignmentResult,
    *,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> Image.Image:
    """Rectify a student page into the template coordinate system."""

    student = _load_page(student_page)
    if student.size != (alignment.student_size.width, alignment.student_size.height):
        raise ValueError("student page size does not match the alignment result")
    return student.transform(
        (alignment.template_size.width, alignment.template_size.height),
        Image.Transform.PERSPECTIVE,
        alignment.template_to_student.pillow_coefficients(),
        resample=resample,
        fillcolor=_white_fill(student.mode),
    )


def _load_page(page: PageInput) -> Image.Image:
    if isinstance(page, Image.Image):
        return ImageOps.exif_transpose(page).convert("RGB")
    with Image.open(page) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def _feature_alignment(
    template: Image.Image,
    student: Image.Image,
    *,
    max_dimension: int,
    max_features: int,
    ratio_test: float,
    ransac_threshold_px: float,
) -> tuple[AlignmentResult | None, str]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, "OpenCV is unavailable; used scale/translation fallback"

    template_work, template_scale = _feature_image(template, max_dimension)
    student_work, student_scale = _feature_image(student, max_dimension)
    detector = cast(Any, cv2).ORB_create(
        nfeatures=max_features,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=15,
        fastThreshold=7,
    )
    template_keypoints, template_descriptors = detector.detectAndCompute(
        np.asarray(template_work), None
    )
    student_keypoints, student_descriptors = detector.detectAndCompute(
        np.asarray(student_work), None
    )
    if template_descriptors is None or student_descriptors is None:
        return None, "not enough visual features; used scale/translation fallback"
    if len(template_keypoints) < 8 or len(student_keypoints) < 8:
        return None, "not enough visual features; used scale/translation fallback"

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    forward = _ratio_matches(
        matcher.knnMatch(template_descriptors, student_descriptors, k=2), ratio_test
    )
    reverse = _ratio_matches(
        matcher.knnMatch(student_descriptors, template_descriptors, k=2), ratio_test
    )
    reverse_pairs = {(match.trainIdx, match.queryIdx) for match in reverse}
    mutual = [match for match in forward if (match.queryIdx, match.trainIdx) in reverse_pairs]
    matches = mutual if len(mutual) >= 8 else forward
    matches.sort(key=lambda match: match.distance)
    if len(matches) < 8:
        return None, "not enough consistent feature matches; used scale/translation fallback"

    template_points = np.asarray(
        [template_keypoints[match.queryIdx].pt for match in matches],
        dtype=np.float32,
    ).reshape(-1, 1, 2)
    student_points = np.asarray(
        [student_keypoints[match.trainIdx].pt for match in matches],
        dtype=np.float32,
    ).reshape(-1, 1, 2)
    try:
        work_matrix, inlier_mask = cv2.findHomography(
            template_points,
            student_points,
            cv2.RANSAC,
            ransac_threshold_px,
        )
    except cv2.error:
        return None, "homography estimation failed; used scale/translation fallback"
    if work_matrix is None or inlier_mask is None:
        return None, "homography estimation failed; used scale/translation fallback"

    original_matrix = (
        np.diag((1.0 / student_scale[0], 1.0 / student_scale[1], 1.0))
        @ work_matrix
        @ np.diag((template_scale[0], template_scale[1], 1.0))
    )
    try:
        transform = Homography.from_rows(original_matrix.tolist())
    except ValueError:
        return None, "estimated homography was singular; used scale/translation fallback"

    mask_values = inlier_mask.reshape(-1).astype(bool)
    inlier_indices = [index for index, included in enumerate(mask_values) if bool(included)]
    inliers = len(inlier_indices)
    inlier_ratio = inliers / len(matches)
    template_original_points = [
        Point(
            float(template_points[index, 0, 0]) / template_scale[0],
            float(template_points[index, 0, 1]) / template_scale[1],
        )
        for index in inlier_indices
    ]
    student_original_points = [
        Point(
            float(student_points[index, 0, 0]) / student_scale[0],
            float(student_points[index, 0, 1]) / student_scale[1],
        )
        for index in inlier_indices
    ]
    try:
        errors = [
            math.hypot(mapped.x - target.x, mapped.y - target.y)
            for source, target in zip(
                template_original_points, student_original_points, strict=True
            )
            for mapped in (transform.map_point(source),)
        ]
    except ValueError:
        return None, "estimated homography was unstable; used scale/translation fallback"
    mean_error = sum(errors) / len(errors) if errors else None
    template_coverage = _feature_coverage(
        cv2,
        np,
        template_points,
        inlier_indices,
        template_work.width,
        template_work.height,
    )
    student_coverage = _feature_coverage(
        cv2,
        np,
        student_points,
        inlier_indices,
        student_work.width,
        student_work.height,
    )
    visible_ratio = _visible_template_ratio(
        transform,
        template.width,
        template.height,
        student.width,
        student.height,
    )
    scored_error = mean_error if mean_error is not None else 1000.0
    error_score = math.exp(-scored_error / 8.0)
    coverage_score = min(1.0, min(template_coverage, student_coverage) / 0.12)
    score = _clamp_ratio(
        0.40 * inlier_ratio + 0.25 * error_score + 0.20 * coverage_score + 0.15 * visible_ratio
    )
    warnings: list[str] = []
    if inliers < 8:
        warnings.append("fewer than 8 geometric inliers")
    if inlier_ratio < 0.35:
        warnings.append("low feature inlier ratio")
    if mean_error is None or mean_error > 10.0:
        warnings.append("high reprojection error")
    if min(template_coverage, student_coverage) < 0.025:
        warnings.append("matched features cover too little of the page")
    if visible_ratio < 0.60:
        warnings.append("most of the mapped template lies outside the student page")
    reliable = (
        inliers >= 8
        and inlier_ratio >= 0.35
        and mean_error is not None
        and mean_error <= 10.0
        and min(template_coverage, student_coverage) >= 0.025
        and visible_ratio >= 0.60
        and score >= 0.55
    )
    quality = AlignmentQuality(
        method="orb_homography_ransac",
        score=score,
        matched_features=len(matches),
        inliers=inliers,
        inlier_ratio=inlier_ratio,
        mean_reprojection_error_px=mean_error,
        template_feature_coverage=template_coverage,
        student_feature_coverage=student_coverage,
        visible_template_ratio=visible_ratio,
        is_reliable=reliable,
        warnings=tuple(warnings),
    )
    return (
        AlignmentResult.create(
            transform,
            PageSize(template.width, template.height),
            PageSize(student.width, student.height),
            quality,
        ),
        "",
    )


def _ratio_matches(groups: Any, ratio: float) -> list[Any]:
    matches: list[Any] = []
    for group in groups:
        if len(group) >= 2 and group[0].distance < ratio * group[1].distance:
            matches.append(group[0])
    return matches


def _feature_image(
    image: Image.Image,
    max_dimension: int,
) -> tuple[Image.Image, tuple[float, float]]:
    gray = ImageOps.grayscale(image)
    scale = min(1.0, max_dimension / max(gray.size))
    if scale < 1.0:
        resized = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.Resampling.LANCZOS,
        )
    else:
        resized = gray
    return resized, (resized.width / image.width, resized.height / image.height)


def _feature_coverage(
    cv2: Any,
    np: Any,
    points: Any,
    indices: list[int],
    width: int,
    height: int,
) -> float:
    if len(indices) < 3:
        return 0.0
    selected = np.float32([points[index, 0] for index in indices]).reshape(-1, 1, 2)
    hull = cv2.convexHull(selected)
    return _clamp_ratio(float(cv2.contourArea(hull)) / (width * height))


def _ink_translation_alignment(
    template: Image.Image,
    student: Image.Image,
    feature_warning: str,
) -> AlignmentResult:
    work_width, work_height = _fallback_size(template.width, template.height)
    template_mask = _ink_mask(template, (work_width, work_height))
    student_mask = _ink_mask(student, (work_width, work_height))
    template_total = _white_pixel_count(template_mask)
    student_total = _white_pixel_count(student_mask)
    max_dx = max(2, min(20, round(work_width * 0.04)))
    max_dy = max(2, min(20, round(work_height * 0.04)))
    best_dx = 0
    best_dy = 0
    best_overlap = _ink_overlap(template_mask, student_mask, 0, 0)
    coarse_step = 2 if max(max_dx, max_dy) >= 8 else 1
    for dy in range(-max_dy, max_dy + 1, coarse_step):
        for dx in range(-max_dx, max_dx + 1, coarse_step):
            overlap = _ink_overlap(template_mask, student_mask, dx, dy)
            if _is_better_offset(overlap, dx, dy, best_overlap, best_dx, best_dy):
                best_dx, best_dy, best_overlap = dx, dy, overlap
    if coarse_step > 1:
        for dy in range(best_dy - 2, best_dy + 3):
            for dx in range(best_dx - 2, best_dx + 3):
                if abs(dx) <= max_dx and abs(dy) <= max_dy:
                    overlap = _ink_overlap(template_mask, student_mask, dx, dy)
                    if _is_better_offset(overlap, dx, dy, best_overlap, best_dx, best_dy):
                        best_dx, best_dy, best_overlap = dx, dy, overlap

    x_scale = student.width / template.width
    y_scale = student.height / template.height
    x_offset = best_dx * student.width / work_width
    y_offset = best_dy * student.height / work_height
    transform = Homography.from_rows(
        ((x_scale, 0.0, x_offset), (0.0, y_scale, y_offset), (0.0, 0.0, 1.0))
    )
    visible_ratio = _visible_template_ratio(
        transform,
        template.width,
        template.height,
        student.width,
        student.height,
    )
    enough_ink = template_total >= max(32, work_width * work_height // 2000)
    reliable = enough_ink and best_overlap >= 0.55 and visible_ratio >= 0.80
    warnings = [feature_warning, "fallback estimates only scale and translation"]
    if not enough_ink:
        warnings.append("template has too little ink to verify alignment")
    if best_overlap < 0.55:
        warnings.append("low printed-ink overlap")
    if student_total == 0:
        warnings.append("student page has no detectable ink")
    quality = AlignmentQuality(
        method="ink_translation_fallback",
        score=_clamp_ratio(best_overlap * visible_ratio),
        matched_features=0,
        inliers=0,
        inlier_ratio=0.0,
        mean_reprojection_error_px=None,
        template_feature_coverage=0.0,
        student_feature_coverage=0.0,
        visible_template_ratio=visible_ratio,
        is_reliable=reliable,
        warnings=tuple(warning for warning in warnings if warning),
    )
    return AlignmentResult.create(
        transform,
        PageSize(template.width, template.height),
        PageSize(student.width, student.height),
        quality,
    )


def _fallback_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, 384.0 / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _ink_mask(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    gray = ImageOps.grayscale(image).resize(size, Image.Resampling.LANCZOS)
    return gray.point(lambda value: 255 if value < 180 else 0, mode="L")


def _white_pixel_count(image: Image.Image) -> int:
    return image.histogram()[255]


def _ink_overlap(template: Image.Image, student: Image.Image, dx: int, dy: int) -> float:
    width, height = template.size
    template_left = max(0, -dx)
    template_top = max(0, -dy)
    template_right = min(width, width - dx)
    template_bottom = min(height, height - dy)
    if template_right <= template_left or template_bottom <= template_top:
        return 0.0
    student_left = template_left + dx
    student_top = template_top + dy
    box = (template_left, template_top, template_right, template_bottom)
    student_box = (
        student_left,
        student_top,
        student_left + (template_right - template_left),
        student_top + (template_bottom - template_top),
    )
    template_crop = template.crop(box)
    student_crop = student.crop(student_box)
    intersection = _white_pixel_count(ImageChops.multiply(template_crop, student_crop))
    denominator = max(
        1,
        min(_white_pixel_count(template_crop), _white_pixel_count(student_crop)),
    )
    return intersection / denominator


def _is_better_offset(
    overlap: float,
    dx: int,
    dy: int,
    best_overlap: float,
    best_dx: int,
    best_dy: int,
) -> bool:
    if overlap > best_overlap + 1e-12:
        return True
    if abs(overlap - best_overlap) > 1e-12:
        return False
    return (abs(dx) + abs(dy), abs(dy), abs(dx)) < (
        abs(best_dx) + abs(best_dy),
        abs(best_dy),
        abs(best_dx),
    )


def _visible_template_ratio(
    transform: Homography,
    template_width: int,
    template_height: int,
    student_width: int,
    student_height: int,
) -> float:
    try:
        mapped = transform.map_polygon(
            Polygon.rectangle(0.0, 0.0, float(template_width), float(template_height))
        )
    except ValueError:
        return 0.0
    area = _polygon_area(list(mapped.points))
    if area <= 1e-9:
        return 0.0
    clipped = list(mapped.points)
    clipped = _clip_polygon(clipped, axis=0, threshold=0.0, keep_greater=True)
    clipped = _clip_polygon(clipped, axis=0, threshold=float(student_width), keep_greater=False)
    clipped = _clip_polygon(clipped, axis=1, threshold=0.0, keep_greater=True)
    clipped = _clip_polygon(clipped, axis=1, threshold=float(student_height), keep_greater=False)
    return _clamp_ratio(_polygon_area(clipped) / area)


def _clip_polygon(
    points: list[Point],
    *,
    axis: int,
    threshold: float,
    keep_greater: bool,
) -> list[Point]:
    if not points:
        return []

    def coordinate(point: Point) -> float:
        return point.x if axis == 0 else point.y

    def inside(point: Point) -> bool:
        value = coordinate(point)
        return value >= threshold if keep_greater else value <= threshold

    def intersection(start: Point, end: Point) -> Point:
        start_value = coordinate(start)
        end_value = coordinate(end)
        portion = (threshold - start_value) / (end_value - start_value)
        return Point(
            start.x + portion * (end.x - start.x),
            start.y + portion * (end.y - start.y),
        )

    output: list[Point] = []
    start = points[-1]
    for end in points:
        start_inside = inside(start)
        end_inside = inside(end)
        if end_inside:
            if not start_inside:
                output.append(intersection(start, end))
            output.append(end)
        elif start_inside:
            output.append(intersection(start, end))
        start = end
    return output


def _polygon_area(points: list[Point]) -> float:
    if len(points) < 3:
        return 0.0
    signed = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    return abs(signed) / 2.0


def _white_fill(mode: str) -> int | tuple[int, ...]:
    bands = Image.getmodebands(mode)
    return 255 if bands == 1 else tuple(255 for _ in range(bands))


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, value))
