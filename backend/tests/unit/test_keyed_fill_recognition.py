from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.config import Settings
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.parser import parse_keyed_fill_response
from homework_judge.recognition.service import RecognitionService


def _answer(key: str, evidence: str = "frame-part-1") -> dict[str, object]:
    return {
        "blankKey": key,
        "recognizedText": f"student-{key}",
        "isBlank": False,
        "confidence": 0.93,
        "issues": [],
        "evidenceRefs": [evidence],
    }


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_parser_binds_runtime_keys_even_when_model_order_is_reversed(count: int) -> None:
    keys = [f"B{index}" for index in range(1, count + 1)]
    parsed = parse_keyed_fill_response(
        json.dumps({"answers": [_answer(key) for key in reversed(keys)]}),
        expected_keys=keys,
        allowed_evidence_refs={"frame-part-1"},
    )

    assert parsed.issues == []
    assert [item["blankKey"] for item in parsed.nodes] == keys


@pytest.mark.parametrize(
    ("answers", "expected_code"),
    [
        ([_answer("B1"), _answer("B2")], "blank_key_missing"),
        ([_answer("B1"), _answer("B2"), _answer("B3"), _answer("B4")], "blank_key_extra"),
        ([_answer("B1"), _answer("B2"), _answer("B2")], "blank_key_duplicate"),
        (
            [
                _answer("B1"),
                {**_answer("B2"), "isBlank": True, "recognizedText": "not empty"},
                _answer("B3"),
            ],
            "blank_text_inconsistent",
        ),
        (
            [_answer("B1"), _answer("B2", "unknown-evidence"), _answer("B3")],
            "evidence_ref_unknown",
        ),
        (
            [_answer("B1"), {**_answer("B2"), "score": 1}, _answer("B3")],
            "forbidden_grading_field",
        ),
    ],
)
def test_parser_fails_closed_for_any_key_or_field_mismatch(
    answers: list[dict[str, object]],
    expected_code: str,
) -> None:
    parsed = parse_keyed_fill_response(
        json.dumps({"answers": answers}),
        expected_keys=["B1", "B2", "B3"],
        allowed_evidence_refs={"frame-part-1"},
    )
    assert expected_code in {str(issue["code"]) for issue in parsed.issues}


class _SequenceClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, list[dict[str, Any]]]] = []

    async def chat(
        self,
        *,
        system_prompt: str,
        user_content: list[dict[str, Any]],
    ) -> ModelResponse:
        self.requests.append((system_prompt, user_content))
        value = self.responses.pop(0)
        return ModelResponse(
            content=json.dumps(value),
            raw={"response": value},
            usage={"promptTokens": 2, "completionTokens": 1, "totalTokens": 3},
        )


def _regions() -> list[dict[str, object]]:
    return [
        {
            "evidence_id": "frame-part-1",
            "page_number": 4,
            "template_image": b"complete confirmed template frame",
            "student_image": b"complete aligned student frame",
        }
    ]


def _blanks() -> list[dict[str, object]]:
    return [
        {
            "blankKey": key,
            "anchor": {
                "templatePageId": "page-4",
                "pageNumber": 4,
                "coordinateSpace": "template_page_normalized",
                "box": {"x": 0.1 * index, "y": 0.2, "width": 0.08, "height": 0.04},
            },
            "standardAnswers": ["SECRET_STANDARD_ANSWER"],
            "synonyms": ["SECRET_SYNONYM"],
            "maxScore": "99",
        }
        for index, key in enumerate(("B1", "B2", "B3"), 1)
    ]


@pytest.mark.asyncio
async def test_service_retries_one_structural_failure_then_returns_keyed_answers() -> None:
    client = _SequenceClient(
        [
            {"answers": [_answer("B1"), _answer("B2")]},
            {"answers": [_answer("B3"), _answer("B1"), _answer("B2")]},
        ]
    )
    service = RecognitionService(
        cast(Settings, SimpleNamespace()),
        cast(DashScopeClient, client),
    )

    outcome, raw, usage = await service.recognize_keyed_fill_response(
        {
            "id": "question-runtime",
            "type": "fill_blank",
            "stem": "任意题干",
            "referenceAnswer": "SECRET_STANDARD_ANSWER",
        },
        _blanks(),
        _regions(),
        frame_set_id="frame-v4",
        blank_config_version_id="config-v7",
    )

    assert outcome["status"] == "recognized"
    assert [item["blankKey"] for item in outcome["answers"]] == ["B1", "B2", "B3"]
    assert outcome["attemptCount"] == 2
    assert len(raw) == 2
    assert usage["totalTokens"] == 6
    assert len(client.requests) == 2
    serialized_requests = json.dumps(client.requests, ensure_ascii=False)
    assert "SECRET_STANDARD_ANSWER" not in serialized_requests
    assert "SECRET_SYNONYM" not in serialized_requests
    assert '"maxScore"' not in serialized_requests
    assert "frame-v4" in serialized_requests
    assert "config-v7" in serialized_requests


@pytest.mark.asyncio
async def test_second_structural_failure_returns_review_outcome_without_guessing() -> None:
    client = _SequenceClient(
        [
            {"answers": [_answer("B1"), _answer("B2")]},
            {"answers": [_answer("B1"), _answer("B2"), _answer("B4")]},
        ]
    )
    service = RecognitionService(
        cast(Settings, SimpleNamespace()),
        cast(DashScopeClient, client),
    )

    outcome, _raw, _usage = await service.recognize_keyed_fill_response(
        {"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        _blanks(),
        _regions(),
        frame_set_id="frame-v4",
        blank_config_version_id="config-v7",
    )

    assert outcome["status"] == "recognition_needs_review"
    assert outcome["answers"] == []
    assert outcome["attemptCount"] == 2
    assert {issue["code"] for issue in outcome["issues"]} >= {
        "blank_key_missing",
        "blank_key_extra",
    }


@pytest.mark.asyncio
async def test_missing_anchors_share_the_complete_question_frame_by_blank_key() -> None:
    client = _SequenceClient(
        [
            {
                "answers": [
                    _answer("B1", "frame-part-1"),
                    _answer("B2", "frame-part-1"),
                    _answer("B3", "frame-part-1"),
                ]
            }
        ]
    )
    service = RecognitionService(
        cast(Settings, SimpleNamespace()),
        cast(DashScopeClient, client),
    )
    blanks = [{**item, "anchor": None} for item in _blanks()]

    outcome, _raw, _usage = await service.recognize_keyed_fill_response(
        {"id": "question-runtime", "type": "fill_blank", "stem": "任意题干"},
        blanks,
        _regions(),
        frame_set_id="frame-v4",
        blank_config_version_id="config-v7",
    )

    assert outcome["status"] == "recognized"
    assert [item["blankKey"] for item in outcome["answers"]] == ["B1", "B2", "B3"]
    assert all(
        item["evidenceRefs"] == ["frame-part-1"] for item in outcome["answers"]
    )
    serialized_request = json.dumps(client.requests, ensure_ascii=False)
    assert serialized_request.count("frame-part-1") >= 1
