from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from .similarity import order_similarity, stem_similarity


def _record(
    task_id: str,
    question: dict[str, Any],
    answer: dict[str, Any] | None,
    method: str,
    number_score: float,
    stem_score: float,
    order_score: float,
    status: str,
    reasons: list[str],
) -> dict[str, Any]:
    total = round(number_score * 0.55 + stem_score * 0.35 + order_score * 0.10, 4)
    if method == "number_exact":
        total = max(total, 0.90)
    return {
        "id": uuid.uuid4().hex,
        "task_id": task_id,
        "question_id": question["id"],
        "answer_entry_id": answer["id"] if answer else None,
        "method": method,
        "number_score": number_score,
        "stem_score": stem_score,
        "order_score": order_score,
        "total_score": total,
        "reasons": reasons,
        "status": status,
    }


def build_matches(
    task_id: str,
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    threshold: float,
    margin: float,
) -> tuple[list[dict[str, Any]], set[str]]:
    q_numbers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    a_numbers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in questions:
        if item["normalized_number"]:
            q_numbers[item["normalized_number"]].append(item)
    for item in answers:
        if item["normalized_number"] and item["answer"]:
            a_numbers[item["normalized_number"]].append(item)
    matches: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for number, q_group in q_numbers.items():
        a_group = a_numbers.get(number, [])
        if len(q_group) == 1 and len(a_group) == 1:
            question, answer = q_group[0], a_group[0]
            stem = stem_similarity(question["stem"], answer["stem_hint"])
            order = order_similarity(
                question["sort_order"], len(questions), answer["sort_order"], len(answers)
            )
            if answer["stem_hint"] and stem < 0.25:
                matches[question["id"]] = _record(
                    task_id,
                    question,
                    None,
                    "unmatched",
                    1.0,
                    stem,
                    order,
                    "needs_review",
                    [f"题号 {number} 相同，但答案题干与试题明显不一致"],
                )
                continue
            matches[question["id"]] = _record(
                task_id,
                question,
                answer,
                "number_exact",
                1.0,
                stem,
                order,
                "suggested",
                [f"题号 {number} 在试卷和答案中均唯一"],
            )
            used.add(answer["id"])
        elif len(q_group) > 1 or len(a_group) > 1:
            for question in q_group:
                matches[question["id"]] = _record(
                    task_id,
                    question,
                    None,
                    "unmatched",
                    0,
                    0,
                    0,
                    "needs_review",
                    [f"规范化题号 {number} 重复，未自动匹配"],
                )
    remaining_answers = [item for item in answers if item["id"] not in used and item["answer"]]
    candidates: list[tuple[float, float, str, dict[str, Any], dict[str, Any]]] = []
    per_question: dict[str, list[tuple[float, dict[str, Any], float]]] = defaultdict(list)
    for question in questions:
        if question["id"] in matches:
            continue
        for answer in remaining_answers:
            if not answer["stem_hint"]:
                continue
            stem = stem_similarity(question["stem"], answer["stem_hint"])
            order = order_similarity(
                question["sort_order"], len(questions), answer["sort_order"], len(answers)
            )
            number_score = (
                1.0
                if question["normalized_number"]
                and question["normalized_number"] == answer["normalized_number"]
                else 0.0
            )
            total = number_score * 0.55 + stem * 0.35 + order * 0.10
            candidates.append((total, stem, answer["id"], question, answer))
            per_question[question["id"]].append((total, answer, stem))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]["id"]))
    for total, stem, _answer_id, question, answer in candidates:
        if question["id"] in matches or answer["id"] in used:
            continue
        ranked = sorted(per_question[question["id"]], key=lambda item: -item[0])
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        if stem < threshold or total - second < margin:
            continue
        order = order_similarity(
            question["sort_order"], len(questions), answer["sort_order"], len(answers)
        )
        matches[question["id"]] = _record(
            task_id,
            question,
            answer,
            "stem_similarity",
            0.0,
            stem,
            order,
            "suggested",
            [f"题干相似度 {stem:.0%}", f"领先第二候选 {total - second:.0%}"],
        )
        used.add(answer["id"])
    for question in questions:
        if question["id"] not in matches:
            matches[question["id"]] = _record(
                task_id,
                question,
                None,
                "unmatched",
                0,
                0,
                0,
                "needs_review",
                ["未找到唯一可靠的答案候选"],
            )
    return [matches[question["id"]] for question in questions], used


def build_single_match(
    task_id: str,
    question: dict[str, Any],
    active_questions: list[dict[str, Any]],
    available_answers: list[dict[str, Any]],
    threshold: float,
    margin: float,
) -> dict[str, Any]:
    """Suggest a match for one restored question without touching other matches."""
    number = str(question.get("normalized_number", ""))
    if (
        number
        and sum(1 for item in active_questions if str(item.get("normalized_number", "")) == number)
        > 1
    ):
        return _record(
            task_id,
            question,
            None,
            "unmatched",
            0,
            0,
            0,
            "needs_review",
            [f"规范化题号 {number} 重复，未自动匹配"],
        )
    matches, _used = build_matches(
        task_id,
        [question],
        available_answers,
        threshold,
        margin,
    )
    return matches[0]
