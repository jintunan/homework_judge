from __future__ import annotations

import json
from pathlib import Path

import pytest

from homework_judge.answer_config.parser import (
    JsonCandidateError,
    extract_json_candidate,
    parse_extracted_paper,
)
from homework_judge.schemas import AnswerMode, Subject

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "answer-extraction-overflow.json"
).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        FIXTURE,
        f"```json\n{FIXTURE}\n```",
        f"以下是识别结果：\n{FIXTURE}\n请审核。",
        json.dumps(json.loads(FIXTURE)["questions"], ensure_ascii=False),
        json.dumps({"data": json.loads(FIXTURE)}, ensure_ascii=False),
    ],
)
def test_extracts_supported_json_shapes(content: str) -> None:
    candidate = extract_json_candidate(content)
    assert candidate.question_count == 8


def test_prefers_candidate_with_more_questions() -> None:
    content = (
        '诊断 {"questions":[{"questionText":"坏","maxScore":0}]} '
        + FIXTURE
    )
    assert extract_json_candidate(content).question_count == 8


def test_rejects_text_without_question_json() -> None:
    with pytest.raises(JsonCandidateError):
        extract_json_candidate("没有结构化结果")


def test_never_evaluates_expression() -> None:
    with pytest.raises(JsonCandidateError):
        extract_json_candidate(
            '{"questions": __import__("os").system("echo unsafe")}'
        )


def test_partial_bad_nodes_do_not_fail_whole_paper() -> None:
    content = json.dumps(
        {
            "题目": [
                "not-an-object",
                {"题号": "1", "题干": "", "满分": 2},
                {
                    "题号": "2",
                    "题干": "可用题目",
                    "题型": "计算题",
                    "满分": "6 分",
                    "置信度": "75%",
                },
            ]
        },
        ensure_ascii=False,
    )
    parsed = parse_extracted_paper(
        content,
        answer_mode=AnswerMode.AGENT_SEARCH,
        subject=Subject.HIGH_SCHOOL_PHYSICS,
    )
    assert [question.question_number for question in parsed.questions] == ["2"]
    assert len(parsed.issues) == 2
    assert {issue.code for issue in parsed.issues} == {
        "question_node_invalid",
        "question_text_missing",
    }
