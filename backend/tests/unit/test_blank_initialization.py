from __future__ import annotations

from decimal import Decimal

import pytest

from homework_judge.grading.blank_initialization import (
    BlankCountSignals,
    BlankDraft,
    BlankInitializationInput,
    BlankInitializationResult,
    allocate_blank_scores,
    assess_blank_initialization,
    count_stem_blank_markers,
    initialize_fill_blanks,
    split_reference_answer,
)


def region(page: int, x: float, y: float) -> dict[str, object]:
    return {
        "template_page_id": f"template-page-{page}",
        "page_number": page,
        "coordinate_space": "template_page_normalized",
        "x": x,
        "y": y,
        "width": 0.2,
        "height": 0.05,
        "source": "teacher",
        "issues": [],
    }


def initialize(
    stem: str,
    answer: str,
    score: str = "4",
    regions: list[dict[str, object]] | None = None,
    blank_scores: list[str] | None = None,
):
    return initialize_fill_blanks(
        BlankInitializationInput(
            stem=stem,
            reference_answer=answer,
            max_score=Decimal(score),
            answer_regions=regions or [],
            blank_scores=blank_scores,
        )
    )


def test_counts_visible_markers_but_not_latex_subscripts() -> None:
    assert count_stem_blank_markers("甲_______乙＿＿＿丙﹍﹍") == 3
    assert count_stem_blank_markers(r"$E_k=\frac12mv_0^2$") == 0


def test_composite_region_is_not_copied_across_three_blanks() -> None:
    result = initialize(
        "玻璃棒_______电子，带_______电荷，互相_______。",
        "失去 异种 吸引",
        regions=[region(3, 0.1, 0.8)],
    )

    assert [item.blankKey for item in result.blanks] == ["B1", "B2", "B3"]
    assert [item.standardAnswers for item in result.blanks] == [
        ["失去"],
        ["异种"],
        ["吸引"],
    ]
    assert [item.maxScore for item in result.blanks] == ["1.33", "1.33", "1.34"]
    assert [item.region for item in result.blanks] == [None, None, None]
    assert {
        "answer_region_count_conflict",
        "blank_score_auto_allocated",
        "composite_region_shared",
        "missing_blank_anchor",
    } <= {item.code for item in result.warnings}
    readiness = assess_blank_initialization(result, "4")
    assert readiness.auto_confirmable is True
    assert readiness.blocking_reasons == []
    assert {
        "answer_region_count_conflict",
        "blank_score_auto_allocated",
        "composite_region_shared",
        "missing_blank_anchor",
    } <= {reason.code for reason in readiness.advisory_reasons}


def test_splits_scientific_notation_without_damaging_it() -> None:
    outcome = split_reference_answer("1×10⁻⁶ 负", 2)
    assert outcome.parts == ["1×10⁻⁶", "负"]


def test_flattens_numbered_answer_groups_to_expected_blank_count() -> None:
    outcome = split_reference_answer("(1)电荷转移 遵守 (2)CD", 3)
    assert outcome.parts == ["电荷转移", "遵守", "CD"]


@pytest.mark.parametrize(
    "answer",
    [
        "2 m/s",
        r"\frac {1} {2}",
        "$x + y$",
        "(未闭合 答案",
    ],
)
def test_refuses_unsafe_whitespace_splits(answer: str) -> None:
    assert split_reference_answer(answer, 2).parts is None


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("甲；乙；丙", ["甲", "乙", "丙"]),
        ("甲;乙;丙", ["甲", "乙", "丙"]),
        ("甲\n乙\n丙", ["甲", "乙", "丙"]),
    ],
)
def test_accepts_strong_separators_only_at_expected_count(answer: str, expected: list[str]) -> None:
    assert split_reference_answer(answer, 3).parts == expected
    assert split_reference_answer(answer, 2).parts is None


def test_ambiguous_answer_leaves_every_blank_empty() -> None:
    result = initialize("甲_______乙_______丙_______", "复杂 答案", regions=[])
    assert len(result.blanks) == 3
    assert [item.standardAnswers for item in result.blanks] == [[], [], []]
    assert "answer_split_ambiguous" in {item.code for item in result.warnings}


def test_regions_are_assigned_in_page_reading_order() -> None:
    result = initialize(
        "没有显式标记",
        "甲；乙；丙",
        regions=[region(2, 0.7, 0.1), region(1, 0.6, 0.4), region(1, 0.2, 0.4)],
    )
    assert [item.region["x"] if item.region else None for item in result.blanks] == [
        0.2,
        0.6,
        0.7,
    ]


def test_stem_marker_count_wins_conflicting_multiple_regions() -> None:
    result = initialize(
        "甲_______乙_______",
        "甲；乙",
        regions=[region(1, 0.1, 0.1), region(1, 0.2, 0.2), region(1, 0.3, 0.3)],
    )
    assert len(result.blanks) == 2
    readiness = assess_blank_initialization(result, "2")
    assert "answer_region_count_conflict" in {
        item.code for item in readiness.advisory_reasons
    }
    assert "blank_count_conflict" not in {
        item.code for item in readiness.blocking_reasons
    }


def test_score_allocation_preserves_exact_total() -> None:
    """The display helper remains deterministic but is not an auto-confirmation source."""
    assert allocate_blank_scores("4", 3) == [
        Decimal("1.33"),
        Decimal("1.33"),
        Decimal("1.34"),
    ]
    assert allocate_blank_scores("5", 2) == [Decimal("2.50"), Decimal("2.50")]
    assert allocate_blank_scores("5", 3) == [
        Decimal("1.67"),
        Decimal("1.67"),
        Decimal("1.66"),
    ]
    assert allocate_blank_scores("0.03", 2) == [Decimal("0.02"), Decimal("0.01")]
    assert allocate_blank_scores("0.02", 3) == [
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]


def test_single_blank_and_idempotent_serialization() -> None:
    value = BlankInitializationInput(
        "答案_______",
        "电场",
        Decimal("2"),
        [region(1, 0.1, 0.2)],
        ["2"],
    )
    first = initialize_fill_blanks(value).to_dict()
    second = initialize_fill_blanks(value).to_dict()
    assert first == second
    assert first["blanks"][0]["standardAnswers"] == ["电场"]


@pytest.mark.parametrize(
    ("scores", "total"),
    [
        (["2"], "2"),
        (["1.25", "2.25"], "3.50"),
        (["1", "1", "3"], "5"),
        (["1", "1.5", "2", "2.5", "3"], "10"),
    ],
)
def test_only_exact_n_way_inputs_with_explicit_decimal_scores_auto_confirm(
    scores: list[str],
    total: str,
) -> None:
    count = len(scores)
    result = initialize(
        "；".join(f"空{index + 1}_____" for index in range(count)),
        "；".join(f"答案{index + 1}" for index in range(count)),
        total,
        [region(1, 0.05, 0.05 + index * 0.12) for index in range(count)],
        scores,
    )

    readiness = assess_blank_initialization(result, total)

    assert readiness.auto_confirmable is True
    assert readiness.blocking_reasons == []
    assert [blank.blankKey for blank in result.blanks] == [
        f"B{index + 1}" for index in range(count)
    ]


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
@pytest.mark.parametrize("region_delta", [-1, 0, 1])
def test_anchor_count_is_advisory_for_runtime_blank_count(
    blank_count: int,
    region_delta: int,
) -> None:
    region_count = max(0, blank_count + region_delta)
    scores = ["1"] * blank_count
    result = initialize(
        " ".join("_____" for _index in range(blank_count)),
        "；".join(f"答案{index + 1}" for index in range(blank_count)),
        str(blank_count),
        [region(1, 0.05, 0.03 + index * 0.1) for index in range(region_count)],
        scores,
    )
    readiness = assess_blank_initialization(result, str(blank_count))
    blocking_codes = {reason.code for reason in readiness.blocking_reasons}
    advisory_codes = {reason.code for reason in readiness.advisory_reasons}

    assert readiness.auto_confirmable is True
    assert blocking_codes == set()
    if region_delta:
        assert "answer_region_count_conflict" in advisory_codes
    else:
        assert advisory_codes == set()


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
@pytest.mark.parametrize("answer_delta", [-1, 0, 1])
def test_standard_answer_count_must_equal_runtime_blank_count(
    blank_count: int,
    answer_delta: int,
) -> None:
    answer_count = max(0, blank_count + answer_delta)
    result = initialize(
        " ".join("_____" for _index in range(blank_count)),
        "；".join(f"答案{index + 1}" for index in range(answer_count)),
        str(blank_count),
        [region(1, 0.05, 0.03 + index * 0.1) for index in range(blank_count)],
        ["1"] * blank_count,
    )
    codes = {
        reason.code
        for reason in assess_blank_initialization(result, str(blank_count)).blocking_reasons
    }

    if answer_delta < 0:
        assert "missing_standard_answer" in codes
    elif answer_delta > 0:
        assert "extra_standard_answer" in codes
    else:
        assert codes == set()


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
@pytest.mark.parametrize("score_delta", [-1, 0, 1])
def test_explicit_score_count_must_equal_runtime_blank_count(
    blank_count: int,
    score_delta: int,
) -> None:
    score_count = max(0, blank_count + score_delta)
    result = initialize(
        " ".join("_____" for _index in range(blank_count)),
        "；".join(f"答案{index + 1}" for index in range(blank_count)),
        str(blank_count),
        [region(1, 0.05, 0.03 + index * 0.1) for index in range(blank_count)],
        ["1"] * score_count,
    )
    codes = {
        reason.code
        for reason in assess_blank_initialization(result, str(blank_count)).blocking_reasons
    }

    if score_delta < 0:
        assert "blank_score_missing" in codes
    elif score_delta > 0:
        assert "blank_score_total_conflict" in codes
    else:
        assert codes == set()


def test_total_score_supplies_deterministic_per_blank_scores() -> None:
    result = initialize(
        "摩擦起电实质是_____，电荷总量_____，应选_____。",
        "(1)电荷转移 遵守 (2)CD",
        "5",
        [region(1, 0.05, 0.1), region(1, 0.05, 0.2), region(1, 0.05, 0.3)],
    )

    assert [blank.maxScore for blank in result.blanks] == ["1.67", "1.67", "1.66"]
    readiness = assess_blank_initialization(result, "5")
    assert readiness.auto_confirmable is True
    assert "blank_score_auto_allocated" in {
        reason.code for reason in readiness.advisory_reasons
    }


def test_equal_split_values_preserve_question_total() -> None:
    result = initialize(
        "_____ _____ _____",
        "甲；乙；丙",
        "5",
        [region(1, 0.05, 0.1), region(1, 0.05, 0.2), region(1, 0.05, 0.3)],
    )

    assert [blank.maxScore for blank in result.blanks] == ["1.67", "1.67", "1.66"]
    assert sum(Decimal(blank.maxScore) for blank in result.blanks) == Decimal("5")


def test_duplicate_keys_block_while_missing_anchor_is_advisory() -> None:
    result = BlankInitializationResult(
        blanks=[
            BlankDraft("B1", 0, "1", "text", ["甲"], [], region(1, 0.1, 0.1)),
            BlankDraft("B1", 1, "1", "text", ["乙"], [], None),
        ],
        signals=BlankCountSignals(2, 1, 2, 2),
        warnings=[],
        source="saved",
    )

    readiness = assess_blank_initialization(result, "2")
    assert "duplicate_blank_key" in {
        reason.code for reason in readiness.blocking_reasons
    }
    assert "missing_blank_anchor" in {
        reason.code for reason in readiness.advisory_reasons
    }


def test_answers_and_synonyms_must_be_disjoint_within_each_blank() -> None:
    result = BlankInitializationResult(
        blanks=[
            BlankDraft("B1", 0, "1", "text", ["甲"], ["甲"], region(1, 0.1, 0.1)),
            BlankDraft("B2", 1, "1", "text", ["乙"], [], region(1, 0.1, 0.2)),
        ],
        signals=BlankCountSignals(2, 2, 2, 2),
        warnings=[],
        source="saved",
    )

    codes = {reason.code for reason in assess_blank_initialization(result, "2").blocking_reasons}
    assert "answer_synonym_conflict" in codes


def test_out_of_bounds_anchor_blocks_but_low_confidence_is_advisory() -> None:
    result = initialize(
        "_____",
        "甲",
        "1",
        [
            {
                "template_page_id": "template-page",
                "page_number": 1,
                "coordinate_space": "template_page_normalized",
                "x": 0.9,
                "y": 0.2,
                "width": 0.2,
                "height": 0.1,
                "source": "model",
                "confidence": 0.4,
                "issues": [],
            }
        ],
        ["1"],
    )
    readiness = assess_blank_initialization(result, "1")
    assert "anchor_outside_question_frame" in {
        reason.code for reason in readiness.blocking_reasons
    }
    assert "blank_anchor_low_confidence" in {
        reason.code for reason in readiness.advisory_reasons
    }


@pytest.mark.parametrize(
    ("answers", "scores", "max_score", "expected_code"),
    [
        (["甲"], ["0.00"], "1.00", "blank_score_invalid"),
        (["甲", "乙"], ["0.40", "0.40"], "1.00", "blank_score_total_conflict"),
        (["甲"], ["1.00"], None, "question_score_invalid"),
    ],
)
def test_readiness_rejects_invalid_scores(
    answers: list[str],
    scores: list[str],
    max_score: str | None,
    expected_code: str,
) -> None:
    result = BlankInitializationResult(
        blanks=[
            BlankDraft(
                blankKey=f"B{index + 1}",
                sortOrder=index,
                maxScore=score,
                answerKind="text",
                standardAnswers=[answers[index]],
                synonyms=[],
                region=region(1, 0.1, 0.1 + index * 0.1),
            )
            for index, score in enumerate(scores)
        ],
        signals=BlankCountSignals(0, 0, None, len(scores)),
        warnings=[],
    )

    readiness = assess_blank_initialization(result, max_score)

    assert readiness.auto_confirmable is False
    assert expected_code in {reason.code for reason in readiness.blocking_reasons}


def test_ambiguous_answer_is_not_auto_confirmable() -> None:
    result = initialize("甲_______乙_______丙_______", "复杂 答案")

    readiness = assess_blank_initialization(result, "4")

    assert readiness.auto_confirmable is False
    assert {
        "answer_split_ambiguous",
        "missing_standard_answer",
    } <= {reason.code for reason in readiness.blocking_reasons}
    assert "missing_blank_anchor" in {
        reason.code for reason in readiness.advisory_reasons
    }
