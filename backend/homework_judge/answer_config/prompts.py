from __future__ import annotations

import json

from ..schemas import QuestionType, Subject
from ..subjects import get_subject_profile

ANSWER_EXTRACTION_PROMPT_VERSION = "answer-extraction-v2"
STRUCTURE_REPAIR_PROMPT_VERSION = "answer-structure-repair-v1"
ANSWER_SEARCH_PROMPT_VERSION = "answer-search-v1"
ANSWER_GENERATION_PROMPT_VERSION = "answer-generation-v2"


def build_extraction_system_prompt(subject: Subject, has_reference: bool) -> str:
    profile = get_subject_profile(subject)
    mode_rule = (
        "输入包含试卷和教师参考答案。按题号匹配；无法匹配时保留题目并把答案留空。"
        if has_reference
        else "输入只有试卷。只提取题目结构，不要解题，standardAnswer 和 scoringPoints 必须留空。"
    )
    return "\n".join(
        (
            f"你是{profile.label}试卷答案配置助手。",
            profile.extraction_instructions,
            mode_rule,
            "只输出 JSON，不输出 Markdown。",
            "根对象必须有 questions 数组。",
            "每题输出 questionNumber、questionText、type、maxScore、standardAnswer、"
            "scoringPoints、reason、confidence、needsAttention。",
            f"允许题型：{', '.join(item.value for item in profile.supported_types)}。",
            *profile.scoring_point_rules,
            "reason 只写简短依据，不输出隐藏思维过程。",
        )
    )


def build_extraction_user_prompt(has_reference: bool) -> str:
    return json.dumps(
        {
            "task": "识别试卷并配置参考答案草稿" if has_reference else "只识别试卷题目结构",
            "output": {
                "questions": [
                    {
                        "questionNumber": "题号",
                        "questionText": "完整题干和必要选项",
                        "type": "允许题型之一",
                        "maxScore": "数字",
                        "standardAnswer": "参考答案" if has_reference else "",
                        "scoringPoints": (
                            [{"description": "评分点", "score": "数字"}]
                            if has_reference
                            else []
                        ),
                        "reason": "来源或需复核原因",
                        "confidence": "0 到 1",
                        "needsAttention": "布尔值",
                    }
                ]
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def build_structure_repair_prompt(content: str) -> str:
    return "\n".join(
        (
            "把下面的模型文本修复为一个合法 JSON 对象。",
            "只修复结构和字段名，不补写、推断或求解任何题目。",
            "根对象必须包含 questions 数组；尽量保留每个原始节点和原始值。",
            "只输出 JSON，不输出 Markdown 或说明。",
            content[:60_000],
        )
    )


def build_search_prompt(
    *,
    subject: Subject,
    question_number: str,
    question_text: str,
    question_type: QuestionType,
    max_score: float,
) -> str:
    profile = get_subject_profile(subject)
    return "\n".join(
        (
            f"为下面的{profile.label}题目检索公开答案。",
            "必须先联网搜索。没有与题干直接匹配且带可靠来源的答案时，found 返回 false。",
            "不要用模型自身知识冒充检索结果。",
            "只输出 JSON：found、standardAnswer、scoringPoints、reason、confidence。",
            f"题号：{question_number}",
            f"题型：{question_type.value}",
            f"满分：{max_score}",
            f"题干：{question_text}",
            *profile.scoring_point_rules,
        )
    )


def build_generation_prompts(
    *,
    subject: Subject,
    question_number: str,
    question_text: str,
    question_type: QuestionType,
    max_score: float,
) -> tuple[str, str]:
    profile = get_subject_profile(subject)
    system = "\n".join(
        (
            f"你是{profile.label}答案配置助手。",
            profile.answer_instructions,
            *profile.scoring_point_rules,
            "只输出 JSON：standardAnswer、scoringPoints、reason、confidence。",
            "评分点合计不得超过题目满分。",
            "reason 只给教师可审核的简要依据，不输出隐藏思维过程。",
        )
    )
    user = json.dumps(
        {
            "questionNumber": question_number,
            "questionText": question_text,
            "type": question_type.value,
            "maxScore": max_score,
        },
        ensure_ascii=False,
        indent=2,
    )
    return system, user
