from __future__ import annotations

from dataclasses import dataclass

from .schemas import QuestionType, Subject


@dataclass(frozen=True, slots=True)
class SubjectProfile:
    label: str
    supported_types: tuple[QuestionType, ...]
    fallback_type: QuestionType
    extraction_instructions: str
    answer_instructions: str
    scoring_point_rules: tuple[str, ...]


SUBJECT_PROFILES: dict[Subject, SubjectProfile] = {
    Subject.MIDDLE_SCHOOL_MATH: SubjectProfile(
        label="初中数学",
        supported_types=(
            QuestionType.CHOICE,
            QuestionType.FILL_BLANK,
            QuestionType.SHORT_ANSWER,
        ),
        fallback_type=QuestionType.SHORT_ANSWER,
        extraction_instructions="识别选择题、填空题和简单简答题，保留数学符号与必要选项。",
        answer_instructions="答案必须符合初中数学课程范围。",
        scoring_point_rules=("简单简答题按关键步骤拆分评分点。",),
    ),
    Subject.HIGH_SCHOOL_PHYSICS: SubjectProfile(
        label="高中物理",
        supported_types=(
            QuestionType.CHOICE,
            QuestionType.FILL_BLANK,
            QuestionType.CALCULATION,
        ),
        fallback_type=QuestionType.CALCULATION,
        extraction_instructions="识别选择题、填空题和计算题，保留公式、单位、图示说明和小问。",
        answer_instructions="答案必须符合高中物理课程范围。",
        scoring_point_rules=("计算题按关键公式、代入、结果和单位拆分评分点。",),
    ),
}


def get_subject_profile(subject: Subject) -> SubjectProfile:
    return SUBJECT_PROFILES[subject]


def is_question_type_allowed(subject: Subject, question_type: QuestionType) -> bool:
    return question_type in get_subject_profile(subject).supported_types
