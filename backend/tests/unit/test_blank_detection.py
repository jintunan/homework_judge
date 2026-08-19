from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.config import Settings
from homework_judge.recognition.blank_detection import (
    BlankDetectionContractError,
    DetectedBlank,
    build_blank_detection_request,
    normalize_blank_detection,
)
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.parser import parse_blank_detection
from homework_judge.recognition.service import RecognitionService


def _frame_set(
    *,
    page_number: int = 2,
    x: float = 0.1,
    y: float = 0.2,
    status: str = "confirmed",
    item_status: str = "confirmed",
) -> dict[str, object]:
    return {
        "id": "frame-set-runtime",
        "status": status,
        "items": [
            {
                "questionId": "question-runtime",
                "status": item_status,
                "fragments": [
                    {
                        "regionKey": "question-runtime:part:1",
                        "templatePageId": f"template-page-{page_number}",
                        "pageNumber": page_number,
                        "x": x,
                        "y": y,
                        "width": 0.8,
                        "height": 0.5,
                        "sortOrder": 0,
                    }
                ],
            }
        ],
    }


def _candidates(
    count: int,
    *,
    fragment_key: str = "question-runtime:part:1",
) -> list[dict[str, object]]:
    return [
        {
            "fragmentKey": fragment_key,
            "candidateType": "answer_blank",
            "bbox": [
                100 + (index % 6) * 140,
                120 + (index // 6) * 200,
                190 + (index % 6) * 140,
                190 + (index // 6) * 200,
            ],
            "isComposite": False,
            "confidence": 0.93,
            "issues": [],
        }
        for index in range(count)
    ]


def _keys(values: Iterable[DetectedBlank]) -> list[str]:
    return [value.blank_key for value in values]


def test_request_requires_the_current_confirmed_frame_and_exact_crops() -> None:
    with pytest.raises(BlankDetectionContractError, match="confirmed"):
        build_blank_detection_request(
            frame_set=_frame_set(status="draft"),
            question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
            frame_images={"question-runtime:part:1": b"full frame"},
        )
    with pytest.raises(BlankDetectionContractError, match="confirmed"):
        build_blank_detection_request(
            frame_set=_frame_set(item_status="pending"),
            question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
            frame_images={"question-runtime:part:1": b"full frame"},
        )
    with pytest.raises(BlankDetectionContractError, match="exactly"):
        build_blank_detection_request(
            frame_set=_frame_set(),
            question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
            frame_images={
                "question-runtime:part:1": b"full frame",
                "unconfirmed-extra-crop": b"must not enter request",
            },
        )

    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    assert request.frame_set_id == "frame-set-runtime"
    assert [item.region_key for item in request.fragments] == ["question-runtime:part:1"]
    assert [item.image for item in request.fragments] == [b"full frame"]


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5, 12])
@pytest.mark.parametrize(
    ("page_number", "frame_x", "frame_y", "stem"),
    [
        (1, 0.02, 0.08, "甲____乙"),
        (7, 0.14, 0.31, "（任意编号）多行、多小问和共享上下文"),
    ],
)
def test_generates_runtime_keys_for_arbitrary_layouts(
    blank_count: int,
    page_number: int,
    frame_x: float,
    frame_y: float,
    stem: str,
) -> None:
    request = build_blank_detection_request(
        frame_set=_frame_set(page_number=page_number, x=frame_x, y=frame_y),
        question={
            "id": "question-runtime",
            "number": "changed-number-is-context-only",
            "type": "fill_blank",
            "stem": stem,
        },
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    result = normalize_blank_detection(
        {"blankCandidates": _candidates(blank_count)},
        request,
    )

    assert _keys(result.blanks) == [f"B{index}" for index in range(1, blank_count + 1)]
    assert result.blocking_issues == ()
    assert all(item.anchor["pageNumber"] == page_number for item in result.blanks)
    assert all(
        item.anchor["templatePageId"] == f"template-page-{page_number}"
        for item in result.blanks
    )


def test_reading_order_not_model_array_order_controls_blank_keys() -> None:
    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    raw = _candidates(3)
    raw[0]["bbox"] = [600, 500, 750, 570]
    raw[1]["bbox"] = [500, 100, 650, 170]
    raw[2]["bbox"] = [100, 100, 250, 170]
    result = normalize_blank_detection({"blankCandidates": raw}, request)

    assert _keys(result.blanks) == ["B1", "B2", "B3"]
    assert [item.model_candidate_index for item in result.blanks] == [2, 1, 0]


def test_printed_labels_diagram_text_and_options_never_create_blank_keys() -> None:
    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    distractors = [
        {**_candidates(1)[0], "candidateType": "printed_option", "printedText": "A"},
        {**_candidates(1)[0], "candidateType": "printed_label", "printedText": "（二）"},
        {**_candidates(1)[0], "candidateType": "diagram_text", "printedText": "O"},
        {**_candidates(1)[0], "candidateType": "decoration", "printedText": "1."},
    ]
    result = normalize_blank_detection(
        {"blankCandidates": [*_candidates(2), *distractors]},
        request,
    )

    assert _keys(result.blanks) == ["B1", "B2"]
    assert result.blocking_issues == ()
    assert result.ignored_candidate_count == len(distractors)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"confidence": 0.2}, "blank_candidate_low_confidence"),
        ({"isComposite": True}, "blank_candidate_composite"),
        ({"bbox": [-10, 100, 120, 180]}, "blank_candidate_out_of_frame"),
        ({"fragmentKey": "unknown-fragment"}, "blank_candidate_fragment_unknown"),
    ],
)
def test_unsafe_candidates_block_confirmation_instead_of_being_silently_confirmed(
    mutation: dict[str, object],
    expected_code: str,
) -> None:
    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    candidate = {**_candidates(1)[0], **mutation}
    result = normalize_blank_detection({"blankCandidates": [candidate]}, request)

    assert expected_code in [issue.code for issue in result.blocking_issues]
    assert result.ready_for_confirmation is False


def test_response_requires_a_candidate_array() -> None:
    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        frame_images={"question-runtime:part:1": b"full frame"},
    )
    result = normalize_blank_detection({"candidates": []}, request)
    assert [issue.code for issue in result.blocking_issues] == ["blank_candidates_missing"]
    assert result.ready_for_confirmation is False


def test_parses_wrapped_blank_detection_payload() -> None:
    assert parse_blank_detection(
        '```json\n{"blankCandidates":[{"candidateType":"answer_blank"}]}\n```'
    ) == {"blankCandidates": [{"candidateType": "answer_blank"}]}


class _BlankDetectionClient:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_content: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        self.system_prompt = system_prompt
        self.user_content = user_content
        return ModelResponse(
            content=(
                '{"blankCandidates":['
                '{"fragmentKey":"question-runtime:part:1","candidateType":"answer_blank",'
                '"bbox":[100,100,250,180],"isComposite":false,"confidence":0.95,"issues":[]},'
                '{"fragmentKey":"question-runtime:part:1","candidateType":"printed_option",'
                '"bbox":[100,300,250,380],"isComposite":false,"confidence":0.99,"issues":[]},'
                '{"fragmentKey":"question-runtime:part:1","candidateType":"answer_blank",'
                '"bbox":[400,300,550,380],"isComposite":false,"confidence":0.9,"issues":[]}'
                ']}'
            ),
            raw={"id": "blank-detection"},
            usage={"totalTokens": 7},
        )


async def test_service_sends_only_full_confirmed_frames_and_surface_context() -> None:
    client = _BlankDetectionClient()
    service = RecognitionService(
        cast(Settings, SimpleNamespace()),
        cast(DashScopeClient, client),
    )
    request = build_blank_detection_request(
        frame_set=_frame_set(),
        question={
            "id": "question-runtime",
            "number": "99",
            "type": "fill_blank",
            "stem": "一个包含选项和图示的任意题面",
            "referenceAnswer": "SECRET_STANDARD_ANSWER",
            "standardAnswers": ["SECRET_STANDARD_ANSWER"],
            "score": 8,
        },
        frame_images={"question-runtime:part:1": b"full confirmed frame bytes"},
    )

    result, raw, usage = await service.detect_blank_anchors(request)

    assert _keys(result.blanks) == ["B1", "B2"]
    assert result.ignored_candidate_count == 1
    assert raw == {"id": "blank-detection"}
    assert usage == {"totalTokens": 7}
    assert "complete" in client.system_prompt.lower()
    prompt_text = " ".join(
        str(item.get("text", "")) for item in client.user_content if item["type"] == "text"
    )
    assert "SECRET_STANDARD_ANSWER" not in prompt_text
    assert '"number"' not in prompt_text
    assert [item["type"] for item in client.user_content] == ["text", "text", "image_url"]
