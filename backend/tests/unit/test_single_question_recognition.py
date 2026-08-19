from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.config import Settings
from homework_judge.errors import AppError
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.prompts import SINGLE_QUESTION_PROMPT_VERSION
from homework_judge.recognition.service import RecognitionService


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
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
            content=self.content,
            raw={"id": "single-question-response"},
            usage={"promptTokens": 4, "completionTokens": 5, "totalTokens": 9},
        )


def service(client: FakeClient) -> RecognitionService:
    settings = cast(Settings, SimpleNamespace())
    return RecognitionService(settings, cast(DashScopeClient, client))


def fragments() -> list[dict[str, object]]:
    return [
        {"region_key": "part-two", "page_number": 12, "sort_order": 1, "image": b"b"},
        {"region_key": "part-one", "page_number": 11, "sort_order": 0, "image": b"a"},
    ]


@pytest.mark.asyncio
async def test_recognizes_one_cross_page_question_in_fragment_order() -> None:
    client = FakeClient(
        '{"questions":[{"number":"12","stem":"第一页题干；下一页续文",'
        '"options":[],"type":"calculation","score":10,"sourcePages":[11,12],'
        '"confidence":0.96,"issues":[]}]}'
    )

    value, raw, usage = await service(client).recognize_single_question(
        {"number": "12", "stem": "第一页题干", "type": "calculation"},
        fragments(),
    )

    assert value["detected_number"] == "12"
    assert value["stem"] == "第一页题干；下一页续文"
    assert value["source_pages"] == [11, 12]
    assert raw == {"id": "single-question-response"}
    assert usage["totalTokens"] == 9
    labels = [
        item["text"]
        for item in client.user_content
        if item["type"] == "text" and str(item["text"]).startswith("Fragment")
    ]
    assert labels == [
        "Fragment 1; key part-one; page 11; sortOrder 0:",
        "Fragment 2; key part-two; page 12; sortOrder 1:",
    ]
    assert "唯一一道" in client.system_prompt
    assert RecognitionService.prompt_version("single_question") == SINGLE_QUESTION_PROMPT_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "count"),
    [
        ('{"questions":[]}', 0),
        (
            '{"questions":[{"number":"12","stem":"a"},'
            '{"number":"13","stem":"b"}]}',
            2,
        ),
        ("not-json", 0),
    ],
)
async def test_rejects_zero_multiple_or_invalid_questions(content: str, count: int) -> None:
    with pytest.raises(AppError) as captured:
        await service(FakeClient(content)).recognize_single_question(
            {"number": "12"}, fragments()
        )

    assert captured.value.code == "SINGLE_QUESTION_RESULT_COUNT_INVALID"
    assert captured.value.details["questionCount"] == count


@pytest.mark.asyncio
async def test_rejects_incomplete_question_and_limits_source_pages() -> None:
    incomplete = FakeClient(
        '{"questions":[{"number":"12","stem":"","sourcePages":[11,99]}]}'
    )
    with pytest.raises(AppError) as captured:
        await service(incomplete).recognize_single_question({"number": "12"}, fragments())
    assert captured.value.code == "SINGLE_QUESTION_RESULT_INCOMPLETE"

    complete = FakeClient(
        '{"questions":[{"number":"12","stem":"完整","sourcePages":[99],'
        '"type":"unknown","confidence":0.8}]}'
    )
    value, _raw, _usage = await service(complete).recognize_single_question(
        {"number": "12"}, fragments()
    )
    assert value["source_pages"] == [11]
    assert "source_page_inferred" in value["issues"]
