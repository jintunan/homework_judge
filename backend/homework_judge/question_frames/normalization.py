from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, cast


def normalize_model_question_frame_candidates(
    candidates: Sequence[Mapping[str, object]],
    question_order: Sequence[str],
    *,
    column_tolerance: float = 0.08,
    vertical_gap: float = 0.006,
    minimum_height: float = 0.02,
) -> tuple[list[dict[str, object]], list[str]]:
    """Conservatively complete model frames in an obvious single-column lane.

    The model is good at locating question starts but may stop before the last
    option or reuse part of the following question.  On pages whose question
    starts share one left edge, the next question start is a deterministic
    boundary.  Multi-column and multi-fragment layouts are intentionally left
    untouched for teacher review.
    """

    rank = {question_id: index for index, question_id in enumerate(question_order)}
    output: list[dict[str, object]] = []
    fragment_refs: list[tuple[str, dict[str, object]]] = []
    for candidate in candidates:
        copied = dict(candidate)
        question_id = str(candidate.get("questionId", "")).strip()
        raw_fragments = candidate.get("fragments")
        fragments: list[dict[str, object]] = []
        if isinstance(raw_fragments, Sequence) and not isinstance(
            raw_fragments, str | bytes | bytearray
        ):
            for raw_fragment in raw_fragments:
                if not isinstance(raw_fragment, Mapping):
                    continue
                fragment = dict(raw_fragment)
                fragments.append(fragment)
                fragment_refs.append((question_id, fragment))
        copied["fragments"] = fragments
        output.append(copied)

    by_page: dict[tuple[str, int], list[tuple[str, dict[str, object]]]] = defaultdict(list)
    for question_id, fragment in fragment_refs:
        if str(fragment.get("source", "model")) != "model":
            continue
        try:
            page_id = str(fragment["templatePageId"])
            page_number = int(cast(Any, fragment["pageNumber"]))
            x = float(cast(Any, fragment["x"]))
            y = float(cast(Any, fragment["y"]))
            width = float(cast(Any, fragment["width"]))
            height = float(cast(Any, fragment["height"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (
            not page_id
            or page_number < 1
            or not all(math.isfinite(value) for value in (x, y, width, height))
            or width <= 0
            or height <= 0
            or x < 0
            or y < 0
            or x + width > 1
            or y + height > 1
        ):
            continue
        by_page[(page_id, page_number)].append((question_id, fragment))

    changed: set[str] = set()
    for page_fragments in by_page.values():
        counts = Counter(question_id for question_id, _fragment in page_fragments)
        if len(counts) < 2 or any(count != 1 for count in counts.values()):
            continue
        left_edges = [float(cast(Any, fragment["x"])) for _, fragment in page_fragments]
        if max(left_edges) - min(left_edges) > column_tolerance:
            continue
        ordered = sorted(
            page_fragments,
            key=lambda value: float(cast(Any, value[1]["y"])),
        )
        ranks = [rank.get(question_id) for question_id, _fragment in ordered]
        if any(value is None for value in ranks):
            continue
        numeric_ranks = [cast(int, value) for value in ranks]
        if numeric_ranks != sorted(numeric_ranks) or len(set(numeric_ranks)) != len(numeric_ranks):
            continue

        lane_left = min(left_edges)
        lane_right = max(
            float(cast(Any, fragment["x"]))
            + float(cast(Any, fragment["width"]))
            for _, fragment in ordered
        )
        if lane_right <= lane_left or lane_right > 1:
            continue

        for index, (question_id, fragment) in enumerate(ordered):
            original = (
                float(cast(Any, fragment["x"])),
                float(cast(Any, fragment["width"])),
                float(cast(Any, fragment["height"])),
            )
            y = float(cast(Any, fragment["y"]))
            fragment["x"] = round(lane_left, 6)
            fragment["width"] = round(lane_right - lane_left, 6)
            if index + 1 < len(ordered):
                next_y = float(cast(Any, ordered[index + 1][1]["y"]))
                inferred_bottom = next_y - vertical_gap
                if inferred_bottom - y >= minimum_height:
                    fragment["height"] = round(inferred_bottom - y, 6)
            current = (
                float(cast(Any, fragment["x"])),
                float(cast(Any, fragment["width"])),
                float(cast(Any, fragment["height"])),
            )
            if current != original:
                changed.add(question_id)

    return output, [question_id for question_id in question_order if question_id in changed]
