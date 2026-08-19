from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class Point:
    """A point in continuous image coordinates (origin at the top-left)."""

    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("point coordinates must be finite")

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


type PointInput = Point | Sequence[float]


@dataclass(frozen=True, slots=True)
class Bounds:
    """Axis-aligned bounds using exclusive right and bottom edges."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bounds must be finite")
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("bounds right/bottom must not precede left/top")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return self.width * self.height

    def padded(self, padding: float) -> Bounds:
        if padding < 0:
            raise ValueError("padding must be non-negative")
        return Bounds(
            self.left - padding,
            self.top - padding,
            self.right + padding,
            self.bottom + padding,
        )

    def clipped(self, width: int, height: int) -> Bounds:
        if width <= 0 or height <= 0:
            raise ValueError("clip dimensions must be positive")
        left = min(float(width), max(0.0, self.left))
        top = min(float(height), max(0.0, self.top))
        right = min(float(width), max(left, self.right))
        bottom = min(float(height), max(top, self.bottom))
        return Bounds(left, top, right, bottom)

    def to_pixel_box(self) -> tuple[int, int, int, int]:
        """Expand continuous bounds to a PIL-compatible integer crop box."""

        return (
            math.floor(self.left),
            math.floor(self.top),
            math.ceil(self.right),
            math.ceil(self.bottom),
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class Polygon:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("a region polygon needs at least three points")

    @classmethod
    def from_points(cls, points: Iterable[Point | Sequence[float]]) -> Polygon:
        parsed: list[Point] = []
        for point in points:
            if isinstance(point, Point):
                parsed.append(point)
            else:
                if len(point) != 2:
                    raise ValueError("polygon points must contain x and y")
                parsed.append(Point(float(point[0]), float(point[1])))
        return cls(tuple(parsed))

    @classmethod
    def rectangle(cls, left: float, top: float, right: float, bottom: float) -> Polygon:
        bounds = Bounds(float(left), float(top), float(right), float(bottom))
        if bounds.width <= 0 or bounds.height <= 0:
            raise ValueError("rectangle regions must have positive width and height")
        return cls(
            (
                Point(bounds.left, bounds.top),
                Point(bounds.right, bounds.top),
                Point(bounds.right, bounds.bottom),
                Point(bounds.left, bounds.bottom),
            )
        )

    @property
    def bounds(self) -> Bounds:
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        return Bounds(min(xs), min(ys), max(xs), max(ys))

    @property
    def signed_area(self) -> float:
        return polygon_signed_area(self)

    @property
    def area(self) -> float:
        return polygon_area(self)

    def as_dicts(self) -> list[dict[str, float]]:
        return [point.as_dict() for point in self.points]


@dataclass(frozen=True, slots=True)
class Homography:
    """A projective transform represented by a row-major 3x3 matrix."""

    values: tuple[float, float, float, float, float, float, float, float, float]

    def __post_init__(self) -> None:
        if len(self.values) != 9 or not all(math.isfinite(value) for value in self.values):
            raise ValueError("homography must contain nine finite values")
        if not _matrix_is_invertible(self.rows):
            raise ValueError("homography must be invertible")

    @classmethod
    def identity(cls) -> Homography:
        return cls((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[float]]) -> Homography:
        if len(rows) != 3 or any(len(row) != 3 for row in rows):
            raise ValueError("homography rows must form a 3x3 matrix")
        values = tuple(float(value) for row in rows for value in row)
        scale = values[8]
        if abs(scale) > _EPSILON:
            values = tuple(value / scale for value in values)
        return cls(values)  # type: ignore[arg-type]

    @classmethod
    def scale(cls, x_scale: float, y_scale: float) -> Homography:
        if x_scale <= 0 or y_scale <= 0:
            raise ValueError("scale values must be positive")
        return cls((x_scale, 0.0, 0.0, 0.0, y_scale, 0.0, 0.0, 0.0, 1.0))

    @property
    def determinant(self) -> float:
        a, b, c, d, e, f, g, h, i = self.values
        return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)

    def map_point(self, point: Point) -> Point:
        a, b, c, d, e, f, g, h, i = self.values
        denominator = g * point.x + h * point.y + i
        if abs(denominator) <= _EPSILON:
            raise ValueError("point maps to infinity under homography")
        return Point(
            (a * point.x + b * point.y + c) / denominator,
            (d * point.x + e * point.y + f) / denominator,
        )

    def map_polygon(self, polygon: Polygon) -> Polygon:
        return Polygon(tuple(self.map_point(point) for point in polygon.points))

    def then(self, following: Homography) -> Homography:
        """Return a transform that applies this transform, then ``following``."""

        return Homography.from_rows(_multiply_matrices(following.rows, self.rows))

    @property
    def inverse(self) -> Homography:
        a, b, c, d, e, f, g, h, i = self.values
        determinant = self.determinant
        rows = (
            (
                (e * i - f * h) / determinant,
                (c * h - b * i) / determinant,
                (b * f - c * e) / determinant,
            ),
            (
                (f * g - d * i) / determinant,
                (a * i - c * g) / determinant,
                (c * d - a * f) / determinant,
            ),
            (
                (d * h - e * g) / determinant,
                (b * g - a * h) / determinant,
                (a * e - b * d) / determinant,
            ),
        )
        return Homography.from_rows(rows)

    @property
    def rows(self) -> tuple[tuple[float, float, float], ...]:
        values = self.values
        return (
            (values[0], values[1], values[2]),
            (values[3], values[4], values[5]),
            (values[6], values[7], values[8]),
        )

    def as_rows(self) -> list[list[float]]:
        return [list(row) for row in self.rows]

    def pillow_coefficients(self) -> tuple[float, float, float, float, float, float, float, float]:
        """Return output-to-input coefficients accepted by Pillow's perspective transform."""

        a, b, c, d, e, f, g, h, i = self.values
        if abs(i) <= _EPSILON:
            raise ValueError("Pillow transform requires a finite normalization coefficient")
        return (a / i, b / i, c / i, d / i, e / i, f / i, g / i, h / i)


def homography_from_control_points(
    template_points: Sequence[PointInput],
    student_points: Sequence[PointInput],
) -> Homography:
    """Solve a template-to-student homography from four or more point pairs.

    The first four points must trace the boundary of a strictly convex
    quadrilateral. Additional pairs may be interior control points and are fit
    with normalized least squares. Clockwise and counter-clockwise input are
    both accepted, but the boundary pairs must use the same winding.
    """

    if len(template_points) != len(student_points) or len(template_points) < 4:
        raise ValueError("template and student control points need at least four pairs")
    template_boundary, template_winding = _validated_control_quadrilateral(
        template_points[:4],
        label="template",
    )
    student_boundary, student_winding = _validated_control_quadrilateral(
        student_points[:4],
        label="student",
    )
    if template_winding != student_winding:
        raise ValueError("template and student control points must use the same winding")
    template = tuple(_point_from_input(value) for value in template_points)
    student = tuple(_point_from_input(value) for value in student_points)
    _validate_distinct_control_points(template, "template")
    _validate_distinct_control_points(student, "student")

    normalized_template, template_normalizer = _normalize_control_points(template)
    normalized_student, student_normalizer = _normalize_control_points(student)

    rows: list[list[float]] = []
    values: list[float] = []
    for source, target in zip(normalized_template, normalized_student, strict=True):
        x, y = source.as_tuple()
        u, v = target.as_tuple()
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        values.append(u)
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        values.append(v)

    solved = (
        _solve_linear_system(rows, values)
        if len(rows) == 8
        else _solve_least_squares(rows, values)
    )
    normalized_transform = Homography(
        (
            solved[0],
            solved[1],
            solved[2],
            solved[3],
            solved[4],
            solved[5],
            solved[6],
            solved[7],
            1.0,
        )
    )
    transform = Homography.from_rows(
        _multiply_matrices(
            student_normalizer.inverse.rows,
            _multiply_matrices(normalized_transform.rows, template_normalizer.rows),
        )
    )
    coordinate_scale = _geometry_scale(student)
    tolerance = coordinate_scale * (1e-8 if len(template) == 4 else 1e-6)
    for source, expected in zip(template, student, strict=True):
        actual = transform.map_point(source)
        if math.hypot(actual.x - expected.x, actual.y - expected.y) > tolerance:
            raise ValueError("control points produce an unstable homography")
    return transform


def _validate_distinct_control_points(points: Sequence[Point], label: str) -> None:
    coordinate_scale = _geometry_scale(points)
    tolerance = coordinate_scale * 1e-10
    for index, point in enumerate(points):
        if any(
            math.hypot(point.x - other.x, point.y - other.y) <= tolerance
            for other in points[index + 1 :]
        ):
            raise ValueError(f"{label} control points must be distinct")


def polygon_signed_area(polygon: Polygon | Sequence[Point]) -> float:
    """Return shoelace area with winding sign preserved."""

    points = _polygon_points(polygon)
    if len(points) < 3:
        return 0.0
    return (
        sum(
            point.x * points[(index + 1) % len(points)].y
            - points[(index + 1) % len(points)].x * point.y
            for index, point in enumerate(points)
        )
        / 2.0
    )


def polygon_area(polygon: Polygon | Sequence[Point]) -> float:
    """Return the non-negative shoelace area of a polygon."""

    return abs(polygon_signed_area(polygon))


def clip_polygon_to_bounds(polygon: Polygon, bounds: Bounds) -> Polygon | None:
    """Clip a polygon to page bounds, returning ``None`` when no area remains."""

    if bounds.area <= _EPSILON or polygon.area <= _EPSILON:
        return None
    points = list(polygon.points)
    points = _clip_points_to_axis(points, axis=0, threshold=bounds.left, keep_greater=True)
    points = _clip_points_to_axis(points, axis=0, threshold=bounds.right, keep_greater=False)
    points = _clip_points_to_axis(points, axis=1, threshold=bounds.top, keep_greater=True)
    points = _clip_points_to_axis(points, axis=1, threshold=bounds.bottom, keep_greater=False)
    points = _deduplicate_adjacent_points(points)
    if len(points) < 3 or polygon_area(points) <= _EPSILON:
        return None
    return Polygon(tuple(points))


def polygon_visible_ratio(polygon: Polygon, bounds: Bounds) -> float:
    """Return mapped polygon area remaining inside page bounds."""

    area = polygon.area
    if area <= _EPSILON:
        return 0.0
    clipped = clip_polygon_to_bounds(polygon, bounds)
    if clipped is None:
        return 0.0
    return _clamp_ratio(clipped.area / area)


def polygon_out_of_bounds_ratio(polygon: Polygon, bounds: Bounds) -> float:
    """Return the fraction of mapped polygon area outside page bounds."""

    return _clamp_ratio(1.0 - polygon_visible_ratio(polygon, bounds))


def polygon_intersection_area(left: Polygon, right: Polygon) -> float:
    """Return intersection area for two convex mapped frame polygons."""

    if left.area <= _EPSILON or right.area <= _EPSILON:
        return 0.0
    if not _is_convex_polygon(left.points) or not _is_convex_polygon(right.points):
        raise ValueError("polygon intersection requires convex polygons")

    clipped = list(left.points)
    clip_points = right.points
    orientation = 1.0 if polygon_signed_area(clip_points) > 0.0 else -1.0
    for index, edge_start in enumerate(clip_points):
        edge_end = clip_points[(index + 1) % len(clip_points)]
        clipped = _clip_points_to_directed_edge(
            clipped,
            edge_start=edge_start,
            edge_end=edge_end,
            orientation=orientation,
        )
        if not clipped:
            return 0.0
    return polygon_area(_deduplicate_adjacent_points(clipped))


def polygon_intersection_ratio(left: Polygon, right: Polygon) -> float:
    """Return intersection area divided by the smaller polygon area."""

    denominator = min(left.area, right.area)
    if denominator <= _EPSILON:
        return 0.0
    return _clamp_ratio(polygon_intersection_area(left, right) / denominator)


def _validated_control_quadrilateral(
    values: Sequence[PointInput],
    *,
    label: str,
) -> tuple[tuple[Point, Point, Point, Point], int]:
    if len(values) != 4:
        raise ValueError(f"{label} control points must contain exactly four points")
    parsed = tuple(_point_from_input(value) for value in values)
    points = (parsed[0], parsed[1], parsed[2], parsed[3])
    coordinate_scale = _geometry_scale(points)
    distance_tolerance = coordinate_scale * 1e-10
    for index, point in enumerate(points):
        if any(
            math.hypot(point.x - other.x, point.y - other.y) <= distance_tolerance
            for other in points[index + 1 :]
        ):
            raise ValueError(f"{label} control points must be distinct")

    area_tolerance = coordinate_scale * coordinate_scale * 1e-10
    if _segments_properly_intersect(
        points[0], points[1], points[2], points[3], area_tolerance
    ) or _segments_properly_intersect(
        points[1], points[2], points[3], points[0], area_tolerance
    ):
        raise ValueError(f"{label} control points must follow boundary order")

    signed_area = polygon_signed_area(points)
    if abs(signed_area) <= area_tolerance:
        raise ValueError(f"{label} control points form a degenerate quadrilateral")

    turns = [
        _cross(points[index], points[(index + 1) % 4], points[(index + 2) % 4])
        for index in range(4)
    ]
    if any(abs(turn) <= area_tolerance for turn in turns):
        raise ValueError(f"{label} control points form a degenerate quadrilateral")
    if not (all(turn > 0.0 for turn in turns) or all(turn < 0.0 for turn in turns)):
        raise ValueError(f"{label} control points must form a convex quadrilateral")
    return points, 1 if signed_area > 0.0 else -1


def _point_from_input(value: PointInput) -> Point:
    if isinstance(value, Point):
        return value
    if len(value) != 2:
        raise ValueError("control points must contain x and y")
    return Point(float(value[0]), float(value[1]))


def _normalize_control_points(
    points: Sequence[Point],
) -> tuple[tuple[Point, ...], Homography]:
    center_x = sum(point.x for point in points) / len(points)
    center_y = sum(point.y for point in points) / len(points)
    mean_distance = sum(
        math.hypot(point.x - center_x, point.y - center_y) for point in points
    ) / len(points)
    if mean_distance <= _EPSILON:
        raise ValueError("control points produce a degenerate normalization")
    scale = math.sqrt(2.0) / mean_distance
    normalizer = Homography(
        (
            scale,
            0.0,
            -scale * center_x,
            0.0,
            scale,
            -scale * center_y,
            0.0,
            0.0,
            1.0,
        )
    )
    return tuple(normalizer.map_point(point) for point in points), normalizer


def _solve_linear_system(rows: Sequence[Sequence[float]], values: Sequence[float]) -> list[float]:
    size = len(rows)
    if size == 0 or len(values) != size or any(len(row) != size for row in rows):
        raise ValueError("homography control-point system must be square")

    augmented: list[list[float]] = []
    for row, expected in zip(rows, values, strict=True):
        if not all(math.isfinite(value) for value in [*row, expected]):
            raise ValueError("homography control-point system must be finite")
        scale = max(abs(value) for value in row)
        if scale <= _EPSILON:
            raise ValueError("control points produce a singular homography")
        augmented.append([*(value / scale for value in row), expected / scale])

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        pivot = augmented[pivot_row][column]
        if abs(pivot) <= _EPSILON:
            raise ValueError("control points produce a singular homography")
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]

        pivot = augmented[column][column]
        for index in range(column, size + 1):
            augmented[column][index] /= pivot
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            if abs(factor) <= _EPSILON:
                continue
            for index in range(column, size + 1):
                augmented[row_index][index] -= factor * augmented[column][index]

    solution = [augmented[index][size] for index in range(size)]
    for row, expected in zip(rows, values, strict=True):
        actual = sum(coefficient * value for coefficient, value in zip(row, solution, strict=True))
        scale = max(1.0, abs(expected), abs(actual))
        if abs(actual - expected) > scale * 1e-8:
            raise ValueError("control points produce an unstable homography")
    return solution


def _solve_least_squares(
    rows: Sequence[Sequence[float]],
    values: Sequence[float],
) -> list[float]:
    """Solve an overdetermined system through its normalized normal equations."""

    if not rows or len(values) != len(rows):
        raise ValueError("homography control-point system is incomplete")
    column_count = len(rows[0])
    if len(rows) < column_count or any(len(row) != column_count for row in rows):
        raise ValueError("homography control-point system is underdetermined")
    if any(
        not all(math.isfinite(value) for value in [*row, expected])
        for row, expected in zip(rows, values, strict=True)
    ):
        raise ValueError("homography control-point system must be finite")
    normal_rows = [
        [
            sum(row[left] * row[right] for row in rows)
            for right in range(column_count)
        ]
        for left in range(column_count)
    ]
    normal_values = [
        sum(row[column] * expected for row, expected in zip(rows, values, strict=True))
        for column in range(column_count)
    ]
    return _solve_linear_system(normal_rows, normal_values)


def _matrix_is_invertible(rows: Sequence[Sequence[float]]) -> bool:
    size = len(rows)
    if size == 0 or any(len(row) != size for row in rows):
        return False
    work: list[list[float]] = []
    for row in rows:
        scale = max(abs(value) for value in row)
        if scale <= _EPSILON:
            return False
        work.append([value / scale for value in row])

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot_row][column]) <= _EPSILON:
            return False
        work[column], work[pivot_row] = work[pivot_row], work[column]
        pivot = work[column][column]
        for row_index in range(column + 1, size):
            factor = work[row_index][column] / pivot
            for index in range(column, size):
                work[row_index][index] -= factor * work[column][index]
    return True


def _polygon_points(polygon: Polygon | Sequence[Point]) -> tuple[Point, ...]:
    return polygon.points if isinstance(polygon, Polygon) else tuple(polygon)


def _clip_points_to_axis(
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
        difference = end_value - start_value
        if abs(difference) <= _EPSILON:
            return end
        portion = (threshold - start_value) / difference
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


def _clip_points_to_directed_edge(
    points: list[Point],
    *,
    edge_start: Point,
    edge_end: Point,
    orientation: float,
) -> list[Point]:
    if not points:
        return []

    def inside(point: Point) -> bool:
        return orientation * _cross(edge_start, edge_end, point) >= -_EPSILON

    def intersection(start: Point, end: Point) -> Point:
        segment_x = end.x - start.x
        segment_y = end.y - start.y
        edge_x = edge_end.x - edge_start.x
        edge_y = edge_end.y - edge_start.y
        denominator = _cross_vectors(segment_x, segment_y, edge_x, edge_y)
        if abs(denominator) <= _EPSILON:
            return end
        offset_x = edge_start.x - start.x
        offset_y = edge_start.y - start.y
        portion = _cross_vectors(offset_x, offset_y, edge_x, edge_y) / denominator
        return Point(start.x + portion * segment_x, start.y + portion * segment_y)

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


def _deduplicate_adjacent_points(points: Sequence[Point]) -> list[Point]:
    output: list[Point] = []
    for point in points:
        if not output or not _points_close(output[-1], point):
            output.append(point)
    if len(output) > 1 and _points_close(output[0], output[-1]):
        output.pop()
    return output


def _points_close(left: Point, right: Point) -> bool:
    scale = max(1.0, abs(left.x), abs(left.y), abs(right.x), abs(right.y))
    return math.hypot(left.x - right.x, left.y - right.y) <= scale * 1e-10


def _geometry_scale(points: Sequence[Point]) -> float:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return max(1.0, max(xs) - min(xs), max(ys) - min(ys))


def _is_convex_polygon(points: Sequence[Point]) -> bool:
    if len(points) < 3 or polygon_area(points) <= _EPSILON:
        return False
    coordinate_scale = _geometry_scale(points)
    tolerance = coordinate_scale * coordinate_scale * 1e-10
    turns = [
        _cross(
            points[index],
            points[(index + 1) % len(points)],
            points[(index + 2) % len(points)],
        )
        for index in range(len(points))
    ]
    significant = [turn for turn in turns if abs(turn) > tolerance]
    return bool(significant) and (
        all(turn > 0.0 for turn in significant) or all(turn < 0.0 for turn in significant)
    )


def _segments_properly_intersect(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
    tolerance: float,
) -> bool:
    first_side_start = _cross(first_start, first_end, second_start)
    first_side_end = _cross(first_start, first_end, second_end)
    second_side_start = _cross(second_start, second_end, first_start)
    second_side_end = _cross(second_start, second_end, first_end)
    return (
        first_side_start * first_side_end < -(tolerance * tolerance)
        and second_side_start * second_side_end < -(tolerance * tolerance)
    )


def _cross(origin: Point, first: Point, second: Point) -> float:
    return _cross_vectors(
        first.x - origin.x,
        first.y - origin.y,
        second.x - origin.x,
        second.y - origin.y,
    )


def _cross_vectors(first_x: float, first_y: float, second_x: float, second_y: float) -> float:
    return first_x * second_y - first_y * second_x


def _clamp_ratio(value: float) -> float:
    return min(1.0, max(0.0, value))


def _multiply_matrices(
    left: Sequence[Sequence[float]],
    right: Sequence[Sequence[float]],
) -> tuple[tuple[float, float, float], ...]:
    def value(row: int, column: int) -> float:
        return sum(
            (left[row][index] * right[index][column] for index in range(3)),
            start=0.0,
        )

    return (
        (value(0, 0), value(0, 1), value(0, 2)),
        (value(1, 0), value(1, 1), value(1, 2)),
        (value(2, 0), value(2, 1), value(2, 2)),
    )
