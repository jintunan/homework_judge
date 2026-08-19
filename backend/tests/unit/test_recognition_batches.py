from types import SimpleNamespace
from typing import Any, cast

from homework_judge.config import Settings
from homework_judge.recognition.client import DashScopeClient
from homework_judge.recognition.service import RecognitionService


def pages(total: int) -> list[dict[str, Any]]:
    return [{"page_number": number} for number in range(1, total + 1)]


def numbers(batches: list[list[dict[str, Any]]]) -> list[list[int]]:
    return [[int(page["page_number"]) for page in batch] for batch in batches]


def service() -> RecognitionService:
    settings = cast(
        Settings,
        SimpleNamespace(model_pages_per_batch=4, answer_pages_per_batch=3),
    )
    return RecognitionService(settings, cast(DashScopeClient, None))


def test_answer_batches_use_three_pages_without_overlap() -> None:
    assert numbers(service()._batches("answer", pages(7))) == [
        [1, 2, 3],
        [4, 5, 6],
        [7],
    ]


def test_exam_batches_keep_four_page_setting_without_overlap() -> None:
    assert numbers(service()._batches("exam", pages(7))) == [
        [1, 2, 3, 4],
        [5, 6, 7],
    ]
