from __future__ import annotations

import json
from typing import Any

from ..schemas import Subject
from ..subjects import get_subject_profile

GRADING_PROMPT_VERSION = "dual-subject-mvp-python-v1"

_TYPE_LABELS = {
    "choice": "选择题",
    "fill_blank": "填空题",
    "short_answer": "简单简答题",
    "calculation": "计算题",
}


def build_system_prompt(subject: Subject) -> str:
    profile = get_subject_profile(subject)
    subject_rule = (
        "选择题和填空题按答案判定；计算题严格按教师评分点检查公式、代入、结果和单位。"
        if subject is Subject.HIGH_SCHOOL_PHYSICS
        else "选择题和填空题按答案判定；简单简答题只按教师评分点给分。"
    )
    return "\n".join(
        (
            f"你是{profile.label}试卷批改助手。",
            "你会看到一名学生固定模板试卷的全部页面，以及教师确认过的答案和评分点。",
            "只识别和评分明确列出的题号，不补造题目，不改变每题满分。",
            subject_rule,
            "看不清、题号无法对应或评分没有把握时，将 needsAttention 设为 true 并降低 confidence。",
            "suggestedScore 必须是 0 到本题满分之间的数字。",
            "reason 简洁说明命中或缺失哪些公开评分点，不输出隐藏思维过程。",
            "教师将复核全部结果；模型建议不是最终成绩。",
            "只输出一个合法 JSON 对象，不使用 Markdown。",
        )
    )


def build_user_prompt(questions: list[dict[str, Any]]) -> str:
    definitions = [
        {
            "questionNumber": question["number"],
            "type": _TYPE_LABELS[str(question["type"])],
            "maxScore": question["maxScore"],
            "standardAnswer": question["standardAnswer"],
            "scoringPoints": question["scoringPoints"],
        }
        for question in questions
    ]
    output = {
        "questions": [
            {
                "questionNumber": "必须与配置完全一致的题号",
                "recognizedAnswer": "从试卷中识别的学生答案，看不清可为空",
                "suggestedScore": "0 到本题满分之间的数字",
                "reason": "简短且可供教师检查的评分理由",
                "confidence": "0 到 1",
                "needsAttention": "布尔值",
            }
        ],
        "overallNote": "可选的整卷识别备注",
    }
    return "\n\n".join(
        (
            "请按教师答案配置识别并评分试卷。图片按页面顺序提供。",
            f"严格按此 JSON 结构输出：\n{json.dumps(output, ensure_ascii=False, indent=2)}",
            f"教师答案配置：\n{json.dumps(definitions, ensure_ascii=False, indent=2)}",
        )
    )
