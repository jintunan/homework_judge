"""Page alignment and answer-region coordinate mapping."""

from .engine import AlignmentError, PageInput, align_pages, warp_student_to_template
from .geometry import Bounds, Homography, Point, Polygon
from .models import (
    AlignmentQuality,
    AlignmentResult,
    AnswerRegion,
    ExtractedAnswerRegion,
    MappedAnswerRegion,
    PageSize,
)
from .regions import (
    extract_answer_regions,
    load_question_regions,
    map_answer_regions,
    parse_question_regions,
)

__all__ = [
    "AlignmentError",
    "AlignmentQuality",
    "AlignmentResult",
    "AnswerRegion",
    "Bounds",
    "ExtractedAnswerRegion",
    "Homography",
    "MappedAnswerRegion",
    "PageInput",
    "PageSize",
    "Point",
    "Polygon",
    "align_pages",
    "extract_answer_regions",
    "load_question_regions",
    "map_answer_regions",
    "parse_question_regions",
    "warp_student_to_template",
]
