import pytest
from pydantic import ValidationError

from homework_judge.grading.calculation import RubricDraftOutput
from homework_judge.grading.prompts import (
    CALCULATION_JUDGE_SYSTEM_PROMPT,
    FILL_JUDGE_SYSTEM_PROMPT,
    RUBRIC_SYSTEM_PROMPT,
    rubric_user_content,
)


def test_rubric_prompt_contains_required_input_and_forbids_student_scoring() -> None:
    content = rubric_user_content(
        question="Q", standard_answer="A", explanation="E", max_score="6.00"
    )
    assert "Q" in content[0]["text"]
    assert "6.00" in content[0]["text"]
    assert "不得评价任何学生" in RUBRIC_SYSTEM_PROMPT


def test_rubric_output_rejects_extra_total_score() -> None:
    with pytest.raises(ValidationError):
        RubricDraftOutput.model_validate(
            {
                "points": [
                    {
                        "pointKey": "P1",
                        "criterion": "step",
                        "score": "1",
                        "sortOrder": 0,
                        "dependencies": [],
                    }
                ],
                "totalScore": "1",
            }
        )


def test_fill_judge_contract_is_keyed_and_forbids_model_scores() -> None:
    assert '"blankKey":"B1"' in FILL_JUDGE_SYSTEM_PROMPT
    assert "不得返回分数" in FILL_JUDGE_SYSTEM_PROMPT


def test_calculation_prompts_accept_omitted_steps_and_alternative_methods() -> None:
    assert "没有单独写出”不等于“没有证据" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "非关键步骤" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "关键步骤" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "页面完整清晰" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "图片不完整、字迹不清等输入质量问题" in (
        CALCULATION_JUDGE_SYSTEM_PROMPT
    )
    assert "其他解法" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "禁止重复扣分" in CALCULATION_JUDGE_SYSTEM_PROMPT
    assert "uncoveredMethod 不是“使用了不同方法”的标记" in (
        CALCULATION_JUDGE_SYSTEM_PROMPT
    )
    assert "后续公式中正确使用的中间结论" in RUBRIC_SYSTEM_PROMPT
    assert "其他正确解法中发挥相同作用" in RUBRIC_SYSTEM_PROMPT
