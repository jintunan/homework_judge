from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.config import Settings
from homework_judge.recognition.boundary import (
    RecognitionDraft,
    apply_boundary_decisions,
    context_for_boundary,
)
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.service import RecognitionService


def _page(number: int) -> dict[str, object]:
    return {"page_number": number}


def _question(draft_id: str, batch: int, page: int, stem: str) -> RecognitionDraft:
    return RecognitionDraft(
        draft_id=draft_id,
        role="exam",
        batch_index=batch,
        sort_order=batch - 1,
        item={
            "sort_order": batch - 1,
            "detected_number": "12",
            "normalized_number": "12",
            "stem": stem,
            "options": [],
            "question_type": "calculation",
            "score": 7.0,
            "source_pages": [page],
            "answer_regions": [],
            "question_regions": [],
            "confidence": 0.9,
            "issues": [],
        },
    )


def _answer(draft_id: str, batch: int, page: int, explanation: str) -> RecognitionDraft:
    return RecognitionDraft(
        draft_id=draft_id,
        role="answer",
        batch_index=batch,
        sort_order=batch - 1,
        item={
            "sort_order": batch - 1,
            "number_hint": "12",
            "normalized_number": "12",
            "stem_hint": "跨页解析",
            "answer": "2.0",
            "explanation": explanation,
            "source_pages": [page],
            "confidence": 0.9,
            "issues": [],
        },
    )


def test_applies_exam_merge_across_boundary() -> None:
    drafts = [_question("left", 1, 4, "题干前半"), _question("right", 2, 5, "题干后半")]
    context = context_for_boundary("exam", 1, _page(4), _page(5), drafts)
    merged, summaries = apply_boundary_decisions(
        context,
        drafts,
        [
            {
                "relation": "merge",
                "draftIds": ["left", "right"],
                "confidence": 0.96,
                "issues": [],
                "mergedItem": {
                    "number": "12",
                    "stem": "题干前半\n题干后半",
                    "options": [],
                    "type": "calculation",
                    "score": 7,
                    "sourcePages": [4, 5],
                    "confidence": 0.96,
                    "issues": [],
                },
            }
        ],
        0.85,
    )
    assert len(merged) == 1
    assert merged[0].item["source_pages"] == [4, 5]
    assert summaries[0]["relation"] == "merge"


def test_applies_answer_explanation_merge_across_boundary() -> None:
    drafts = [_answer("left", 1, 4, "解析第一步"), _answer("right", 2, 5, "解析第二步")]
    context = context_for_boundary("answer", 1, _page(4), _page(5), drafts)
    merged, summaries = apply_boundary_decisions(
        context,
        drafts,
        [
            {
                "relation": "merge",
                "draftIds": ["left", "right"],
                "confidence": 0.97,
                "issues": [],
                "mergedItem": {
                    "numberHint": "12",
                    "stemHint": "跨页解析",
                    "answer": "2.0",
                    "explanation": "解析第一步\n解析第二步",
                    "sourcePages": [4, 5],
                    "confidence": 0.97,
                    "issues": [],
                },
            }
        ],
        0.85,
    )
    assert len(merged) == 1
    assert merged[0].item["source_pages"] == [4, 5]
    assert merged[0].item["explanation"] == "解析第一步\n解析第二步"
    assert summaries[0]["relation"] == "merge"


def test_low_confidence_merge_is_kept_for_review() -> None:
    drafts = [_question("left", 1, 4, "A"), _question("right", 2, 5, "B")]
    context = context_for_boundary("exam", 1, _page(4), _page(5), drafts)
    merged, summaries = apply_boundary_decisions(
        context,
        drafts,
        [
            {
                "relation": "merge",
                "draftIds": ["left", "right"],
                "confidence": 0.5,
                "mergedItem": {"number": "12", "stem": "AB"},
            }
        ],
        0.85,
    )
    assert len(merged) == 2
    assert all("boundary_merge_needs_review" in item.item["issues"] for item in merged)
    assert summaries[0]["reason"] == "boundary_merge_low_confidence"


def test_same_side_merge_is_rejected() -> None:
    drafts = [
        _question("left-a", 1, 4, "A"),
        _question("left-b", 1, 4, "B"),
        _question("right", 2, 5, "C"),
    ]
    context = context_for_boundary("exam", 1, _page(4), _page(5), drafts)
    merged, summaries = apply_boundary_decisions(
        context,
        drafts,
        [
            {
                "relation": "merge",
                "draftIds": ["left-a", "left-b"],
                "confidence": 1,
                "mergedItem": {},
            }
        ],
        0.85,
    )
    assert len(merged) == 3
    assert summaries[0]["reason"] == "boundary_merge_requires_both_sides"


class _FakeClient:
    def __init__(self, contents: list[dict[str, Any]]) -> None:
        self.contents = contents

    async def chat(self, **_kwargs: Any) -> ModelResponse:
        value = self.contents.pop(0)
        return ModelResponse(
            content=json.dumps(value, ensure_ascii=False),
            raw={"fixture": value},
            usage={"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
        )


@pytest.mark.asyncio
async def test_service_surfaces_partial_parse_issues_on_review_item(tmp_path: Path) -> None:
    (tmp_path / "page-1.jpg").write_bytes(b"image")
    client = _FakeClient(
        [
            {
                "questions": [
                    {
                        "number": "1",
                        "stem": "有效题目",
                        "type": "single_choice",
                        "options": ["A", "B"],
                        "score": 2,
                        "sourcePages": [1],
                        "confidence": 0.9,
                        "issues": [],
                    },
                    "bad-node",
                ]
            }
        ]
    )
    settings = cast(
        Settings,
        SimpleNamespace(
            model_pages_per_batch=2,
            answer_pages_per_batch=2,
            data_dir=tmp_path,
            boundary_merge_min_confidence=0.85,
        ),
    )

    questions, records, _usage = await RecognitionService(
        settings, cast(DashScopeClient, client)
    ).recognize("exam", [{"page_number": 1, "image_path": "page-1.jpg"}])

    assert len(questions) == 1
    assert any("node_not_object" in issue for issue in questions[0]["issues"])
    assert records[0]["parseIssues"][0]["code"] == "node_not_object"


@pytest.mark.asyncio
async def test_service_runs_non_overlapping_batches_then_boundary_merge(
    tmp_path: Path,
) -> None:
    for number in range(1, 4):
        (tmp_path / f"page-{number}.jpg").write_bytes(b"image")
    pages = [{"page_number": number, "image_path": f"page-{number}.jpg"} for number in range(1, 4)]
    client = _FakeClient(
        [
            {
                "questions": [
                    {
                        "number": "12",
                        "stem": "跨页题前半",
                        "type": "calculation",
                        "score": 7,
                        "sourcePages": [2],
                        "confidence": 0.9,
                        "issues": ["cross_page_incomplete"],
                    }
                ]
            },
            {
                "questions": [
                    {
                        "number": "12",
                        "stem": "跨页题后半",
                        "type": "calculation",
                        "score": 7,
                        "sourcePages": [3],
                        "confidence": 0.9,
                        "issues": ["cross_page_incomplete"],
                    }
                ]
            },
            {
                "decisions": [
                    {
                        "relation": "merge",
                        "draftIds": ["exam-1-0", "exam-2-1"],
                        "mergedItem": {
                            "number": "12",
                            "stem": "跨页题前半\n跨页题后半",
                            "options": [],
                            "type": "calculation",
                            "score": 7,
                            "sourcePages": [2, 3],
                            "confidence": 0.96,
                            "issues": [],
                        },
                        "confidence": 0.96,
                        "issues": [],
                    }
                ]
            },
        ]
    )
    settings = cast(
        Settings,
        SimpleNamespace(
            model_pages_per_batch=2,
            answer_pages_per_batch=2,
            data_dir=tmp_path,
            boundary_merge_min_confidence=0.85,
        ),
    )
    service = RecognitionService(settings, cast(DashScopeClient, client))

    questions, records, usage = await service.recognize("exam", pages)

    assert len(questions) == 1
    assert questions[0]["source_pages"] == [2, 3]
    assert [record["phase"] for record in records] == [
        "main_batch",
        "main_batch",
        "boundary_merge",
    ]
    assert usage["totalTokens"] == 6
