from homework_judge.matching.matcher import build_matches


def question(item_id: str, number: str, stem: str, order: int) -> dict[str, object]:
    return {
        "id": item_id,
        "normalized_number": number,
        "stem": stem,
        "sort_order": order,
    }


def answer(item_id: str, number: str, stem: str, value: str, order: int) -> dict[str, object]:
    return {
        "id": item_id,
        "normalized_number": number,
        "stem_hint": stem,
        "answer": value,
        "sort_order": order,
    }


def test_unique_numbers_match_one_to_one() -> None:
    questions = [question("q1", "1", "下列说法正确的是", 0)]
    answers = [answer("a1", "1", "", "A", 0)]
    matches, used = build_matches("task", questions, answers, 0.82, 0.08)
    assert matches[0]["answer_entry_id"] == "a1"
    assert matches[0]["method"] == "number_exact"
    assert matches[0]["status"] == "suggested"
    assert used == {"a1"}


def test_duplicate_numbers_are_not_forced() -> None:
    questions = [
        question("q1", "1", "第一道题", 0),
        question("q2", "1", "另一道题", 1),
    ]
    answers = [answer("a1", "1", "", "A", 0)]
    matches, used = build_matches("task", questions, answers, 0.82, 0.08)
    assert all(item["answer_entry_id"] is None for item in matches)
    assert all(item["status"] == "needs_review" for item in matches)
    assert not used


def test_exact_number_with_conflicting_stem_requires_review() -> None:
    questions = [question("q2", "2", "下列关于电场强度的说法正确的是", 1)]
    answers = [answer("a2", "2", "摩擦起电的实质是电荷转移", "CD", 1)]
    matches, used = build_matches("task", questions, answers, 0.82, 0.08)
    assert matches[0]["answer_entry_id"] is None
    assert matches[0]["status"] == "needs_review"
    assert "明显不一致" in matches[0]["reasons"][0]
    assert not used


def test_missing_number_can_use_stem_similarity() -> None:
    stem = "如图所示，在匀强电场中放置一个带正电的小球，下列说法正确的是"
    questions = [question("q1", "", stem, 0)]
    answers = [answer("a1", "", stem + "（单选）", "C", 0)]
    matches, used = build_matches("task", questions, answers, 0.80, 0.05)
    assert matches[0]["method"] == "stem_similarity"
    assert used == {"a1"}


def test_matching_is_deterministic() -> None:
    questions = [
        question("q1", "1", "题目甲", 0),
        question("q2", "2", "题目乙", 1),
    ]
    answers = [
        answer("a1", "1", "", "A", 0),
        answer("a2", "2", "", "B", 1),
    ]
    first, _ = build_matches("task", questions, answers, 0.82, 0.08)
    second, _ = build_matches("task", questions, answers, 0.82, 0.08)
    assert [
        (item["question_id"], item["answer_entry_id"], item["method"], item["total_score"])
        for item in first
    ] == [
        (item["question_id"], item["answer_entry_id"], item["method"], item["total_score"])
        for item in second
    ]
