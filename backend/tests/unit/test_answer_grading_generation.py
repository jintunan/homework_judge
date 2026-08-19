from decimal import Decimal

import pytest

from homework_judge.grading.answer_grading_generation import (
    GeneratedBlank,
    GeneratedDraftOutput,
    GeneratedRubricPoint,
    normalize_generated_draft,
)


def test_fill_draft_uses_visual_three_blank_count_and_exact_scores() -> None:
    output = GeneratedDraftOutput(
        questionType="fill_blank",
        standardAnswer="电荷转移；守；CD",
        explanation="摩擦起电是电荷转移，电荷守恒；图示选 CD。",
        maxScore=Decimal("5"),
        blanks=[
            GeneratedBlank(standardAnswers=["电荷转移"]),
            GeneratedBlank(standardAnswers=["守"]),
            GeneratedBlank(standardAnswers=["CD"]),
        ],
    )

    draft = normalize_generated_draft(
        output,
        expected_type="fill_blank",
        expected_score="5.00",
        option_labels=[],
        stem="摩擦起电的过程是______，遵守______定律；正确选项______。",
    )

    assert [item["maxScore"] for item in draft["blanks"]] == ["1.67", "1.67", "1.66"]
    assert [item["standardAnswers"] for item in draft["blanks"]] == [
        ["电荷转移"],
        ["守"],
        ["CD"],
    ]


def test_fill_draft_warns_when_visual_count_differs_from_ocr_markers() -> None:
    output = GeneratedDraftOutput(
        questionType="fill_blank",
        standardAnswer="甲；乙；丙",
        explanation="逐空解析",
        maxScore=Decimal("3"),
        blanks=[
            GeneratedBlank(standardAnswers=["甲"]),
            GeneratedBlank(standardAnswers=["乙"]),
            GeneratedBlank(standardAnswers=["丙"]),
        ],
    )
    draft = normalize_generated_draft(
        output,
        expected_type="fill_blank",
        expected_score="3",
        option_labels=[],
        stem="第一空______，第二处______。",
    )
    assert "OCR 题干识别到 2 个空" in draft["warnings"][0]


def test_choice_draft_rejects_unknown_option() -> None:
    output = GeneratedDraftOutput(
        questionType="single_choice",
        standardAnswer="D",
        explanation="解析",
        maxScore=Decimal("2"),
        answerOptions=["D"],
    )
    with pytest.raises(ValueError, match="unknown option"):
        normalize_generated_draft(
            output,
            expected_type="single_choice",
            expected_score="2",
            option_labels=["A", "B", "C"],
            stem="题干",
        )


def test_calculation_draft_is_normalized_to_current_policy() -> None:
    output = GeneratedDraftOutput(
        questionType="calculation",
        standardAnswer="2 m/s",
        explanation="先列式再计算",
        maxScore=Decimal("10"),
        rubricPoints=[
            GeneratedRubricPoint(
                pointKey="P1",
                criterion="列出关键公式",
                score=Decimal("7"),
                sortOrder=0,
            ),
            GeneratedRubricPoint(
                pointKey="P2",
                criterion="正确代入计算",
                score=Decimal("3"),
                sortOrder=1,
                dependencies=["P1"],
            ),
        ],
    )
    draft = normalize_generated_draft(
        output,
        expected_type="calculation",
        expected_score="10",
        option_labels=[],
        stem="计算速度",
    )
    assert sum(Decimal(item["score"]) for item in draft["rubricPoints"]) == Decimal("10")
    assert draft["rubricPoints"][-1]["pointKey"] == "FINAL_ANSWER"
    assert draft["rubricPoints"][-1]["score"] == "2.00"
