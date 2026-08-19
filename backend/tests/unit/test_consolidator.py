from homework_judge.matching.matcher import build_matches
from homework_judge.recognition.consolidator import consolidate_answers, consolidate_questions


def question(number: str, stem: str, order: int, pages: list[int]) -> dict[str, object]:
    return {
        "id": f"q-{order}",
        "sort_order": order,
        "detected_number": number,
        "normalized_number": number,
        "stem": stem,
        "options": [],
        "question_type": "calculation",
        "score": None,
        "source_pages": pages,
        "confidence": 0.9,
        "issues": [],
    }


def answer(number: str, value: str, explanation: str = "") -> dict[str, object]:
    return {
        "id": f"a-{number}",
        "sort_order": int(number) - 1,
        "number_hint": number,
        "normalized_number": number,
        "stem_hint": "",
        "answer": value,
        "explanation": explanation,
        "source_pages": [2],
        "confidence": 1.0,
        "issues": [],
    }


def test_merges_overlap_duplicates_with_formula_formatting_differences() -> None:
    items = [
        question(
            "10",
            r"(4分)两个完全相同的金属球 $A$、$B$，"
            r"A 球带 $4\times10^{-6}\,\text{C}$ 的正电荷，"
            r"B 球带 $6\times10^{-6}\,\text{C}$ 的负电荷。",
            9,
            [4],
        ),
        question(
            "10",
            "两个完全相同的金属球 A、B，A 球带 4×10⁻⁶ C 的正电荷，B 球带 6×10⁻⁶ C 的负电荷。",
            10,
            [4],
        ),
    ]
    merged = consolidate_questions(items)
    assert len(merged) == 1
    assert merged[0]["normalized_number"] == "10"


def test_ignores_printed_number_when_comparing_overlap_duplicates() -> None:
    first = question(
        "10",
        r"(4 points) Identical metal balls A and B carry $4\times10^{-6}\ \mathrm{C}$ "
        r"and $6\times10^{-6}\ \mathrm{C}$.",
        0,
        [4],
    )
    second = question(
        "10",
        "10. (4 points) Identical metal balls A and B carry 4×10⁻⁶ C and 6×10⁻⁶ C.",
        1,
        [4],
    )

    merged = consolidate_questions([first, second])

    assert len(merged) == 1


def test_merges_partial_and_complete_copies_from_overlapping_batches() -> None:
    shared = (
        "A physics group investigates the factors affecting electrostatic force. "
        "A charged ball is suspended from an insulating thread, and the deflection "
        "angle indicates the force. (1) Select the experimental method."
    )
    partial = question("12", shared, 0, [4])
    complete = question(
        "12",
        "12. " + shared + " A. ideal B. substitution C. amplification D. control "
        "(2) Describe how force changes with distance. (3) Calculate the charge ratio.",
        1,
        [4, 5],
    )
    partial["_recognition_batch_index"] = 1
    complete["_recognition_batch_index"] = 2

    merged = consolidate_questions([partial, complete])

    assert len(merged) == 1
    assert "Calculate the charge ratio" in merged[0]["stem"]
    assert "_recognition_batch_index" not in merged[0]


def test_keeps_different_same_number_questions_from_separate_batches() -> None:
    first = question(
        "1",
        "Calculate the electric field at point P produced by a positive point charge.",
        0,
        [4],
    )
    second = question(
        "1",
        "Determine the acceleration of a metal ball moving down a rough inclined plane.",
        1,
        [4],
    )
    first["_recognition_batch_index"] = 1
    second["_recognition_batch_index"] = 2

    merged = consolidate_questions([first, second])

    assert len(merged) == 2


def test_merges_and_orders_full_question_regions_without_duplicates() -> None:
    first = question("8", "Same question", 0, [1, 2])
    second = question("8", "Same question", 1, [1, 2])
    first["question_regions"] = [
        {"page_number": 2, "x": 0.1, "y": 0.2, "width": 0.8, "height": 0.5},
    ]
    second["question_regions"] = [
        {"page_number": 1, "x": 0.1, "y": 0.7, "width": 0.8, "height": 0.25},
        {"page_number": 2, "x": 0.1, "y": 0.2, "width": 0.8, "height": 0.5},
    ]
    merged = consolidate_questions([first, second])
    assert [(region["page_number"], region["y"]) for region in merged[0]["question_regions"]] == [
        (1, 0.7),
        (2, 0.2),
    ]


def test_merges_numbered_subquestions_into_parent() -> None:
    items = [
        question("13", "完整题干。\n(1)求静电力大小。", 0, [5]),
        question("13", "(2)求合电场强度。", 1, [5]),
        question("13", "(3)求小球电荷量。", 2, [5]),
    ]
    merged = consolidate_questions(items)
    assert len(merged) == 1
    assert "(1)求静电力大小" in merged[0]["stem"]
    assert "(2)求合电场强度" in merged[0]["stem"]
    assert "(3)求小球电荷量" in merged[0]["stem"]


def test_recovers_answer_from_visible_explanation() -> None:
    item = answer("15", "", "(1)q=2×10⁻⁵ C，带正电；(2)a=1 m/s²")
    item["confidence"] = 0.0
    item["issues"] = ["答案缺失", "missing_answer"]
    merged = consolidate_answers([item])
    assert merged[0]["answer"].startswith("(1)q=2×10⁻⁵ C")
    assert merged[0]["confidence"] == 0.5
    assert merged[0]["issues"] == ["answer_recovered_from_explanation"]


def test_consolidated_questions_match_answers_by_unique_number() -> None:
    questions = consolidate_questions(
        [
            question("10", "第十题完整题干", 0, [4]),
            question("10", "(4分)第十题完整题干", 1, [4]),
            question("13", "第十三题完整题干。(1)第一问", 2, [5]),
            question("13", "(2)第二问", 3, [5]),
            question("13", "(3)第三问", 4, [5]),
        ]
    )
    answers = consolidate_answers([answer("10", "A"), answer("13", "(1)甲 (2)乙 (3)丙")])
    matches, used = build_matches("task", questions, answers, 0.82, 0.08)
    assert len(questions) == 2
    assert all(item["method"] == "number_exact" for item in matches)
    assert used == {"a-10", "a-13"}
