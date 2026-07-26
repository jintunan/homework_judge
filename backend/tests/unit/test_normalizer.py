from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from homework_judge.answer_config.parser import parse_extracted_paper
from homework_judge.schemas import AnswerMode, Subject

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "answer-extraction-overflow.json"
).read_text(encoding="utf-8")


def test_agent_search_discards_premature_answers_without_losing_questions() -> None:
    parsed = parse_extracted_paper(
        FIXTURE,
        answer_mode=AnswerMode.AGENT_SEARCH,
        subject=Subject.HIGH_SCHOOL_PHYSICS,
    )
    assert len(parsed.questions) == 8
    assert all(question.standard_answer == "" for question in parsed.questions)
    assert all(question.scoring_points == [] for question in parsed.questions)
    assert all(question.needs_attention for question in parsed.questions)
    assert all(
        any(issue.code == "premature_answer_discarded" for issue in question.issues)
        for question in parsed.questions
    )


def test_reference_overflow_is_scaled_exactly_to_max_score() -> None:
    parsed = parse_extracted_paper(
        FIXTURE,
        answer_mode=AnswerMode.REFERENCE_UPLOAD,
        subject=Subject.HIGH_SCHOOL_PHYSICS,
    )
    assert len(parsed.questions) == 8
    for question in parsed.questions[4:]:
        total = sum((point.score for point in question.scoring_points), Decimal(0))
        assert total == question.max_score
        assert any(issue.code == "scoring_points_scaled" for issue in question.issues)
        assert question.needs_attention


def test_invalid_points_are_isolated_and_duplicate_numbers_require_correction() -> None:
    nodes = """
    {
      "questions": [
        {
          "questionNumber": "1",
          "questionText": "第一题",
          "type": "choice",
          "maxScore": 4,
          "standardAnswer": "A",
          "scoringPoints": [
            {"description": "正确", "score": 4},
            "坏评分点"
          ],
          "confidence": 0.9
        },
        {
          "questionNumber": "1",
          "questionText": "第二题",
          "type": "unknown",
          "maxScore": 5,
          "standardAnswer": "示例",
          "scoringPoints": [{"description": "", "score": -1}],
          "confidence": 2
        }
      ]
    }
    """
    parsed = parse_extracted_paper(
        nodes,
        answer_mode=AnswerMode.REFERENCE_UPLOAD,
        subject=Subject.HIGH_SCHOOL_PHYSICS,
    )
    assert len(parsed.questions) == 2
    assert parsed.questions[1].question_number == "1-待核2"
    assert parsed.questions[1].requires_correction
    assert parsed.questions[1].confidence == 1
    assert parsed.questions[1].scoring_points == []
