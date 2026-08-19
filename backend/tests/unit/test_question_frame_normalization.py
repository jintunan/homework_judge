from __future__ import annotations

from typing import Any

from homework_judge.question_frames.normalization import (
    normalize_model_question_frame_candidates,
)


def _fragment(
    question_id: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict[str, Any]:
    return {
        "regionKey": f"{question_id}:frame:1",
        "templatePageId": "page-1",
        "pageNumber": 1,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "sortOrder": 0,
        "source": "model",
        "confidence": 0.8,
        "issues": [],
    }


def test_completes_single_column_width_and_uses_next_question_as_boundary() -> None:
    candidates = [
        {"questionId": "q1", "fragments": [_fragment("q1", x=0.12, y=0.2, width=0.45, height=0.1)]},
        {"questionId": "q2", "fragments": [_fragment("q2", x=0.12, y=0.5, width=0.76, height=0.3)]},
    ]

    normalized, changed = normalize_model_question_frame_candidates(candidates, ["q1", "q2"])
    first = normalized[0]["fragments"][0]

    assert changed == ["q1"]
    assert first["x"] == 0.12
    assert first["width"] == 0.76
    assert first["height"] == 0.294
    assert normalized[1]["fragments"][0]["height"] == 0.3


def test_removes_model_overlap_without_touching_last_question_bottom() -> None:
    candidates = [
        {"questionId": "q7", "fragments": [_fragment("q7", x=0.1, y=0.1, width=0.8, height=0.5)]},
        {"questionId": "q8", "fragments": [_fragment("q8", x=0.1, y=0.43, width=0.8, height=0.28)]},
    ]

    normalized, changed = normalize_model_question_frame_candidates(candidates, ["q7", "q8"])

    assert changed == ["q7"]
    assert normalized[0]["fragments"][0]["height"] == 0.324
    assert normalized[1]["fragments"][0]["height"] == 0.28


def test_skips_multi_column_and_teacher_edited_layouts() -> None:
    left = _fragment("left", x=0.05, y=0.1, width=0.4, height=0.3)
    right = _fragment("right", x=0.55, y=0.1, width=0.4, height=0.3)
    teacher = _fragment("teacher", x=0.05, y=0.55, width=0.4, height=0.2)
    teacher["source"] = "teacher"
    candidates = [
        {"questionId": "left", "fragments": [left]},
        {"questionId": "right", "fragments": [right]},
        {"questionId": "teacher", "fragments": [teacher]},
    ]

    normalized, changed = normalize_model_question_frame_candidates(
        candidates,
        ["left", "right", "teacher"],
    )

    assert changed == []
    assert normalized == candidates
