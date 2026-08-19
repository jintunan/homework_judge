from decimal import Decimal

import pytest
from pydantic import ValidationError

from homework_judge.grading.contracts import (
    BoundingBox,
    DecisionRecord,
    DecisionStatus,
    EvidenceRef,
)
from homework_judge.grading.normalization import (
    decimal_string,
    matches_exact_or_synonym,
    normalize_options,
    normalize_text,
    parse_decimal,
    quantize_score,
)


def test_decimal_score_rounds_half_up_and_serializes_stably() -> None:
    assert quantize_score(Decimal("2.675")) == Decimal("2.68")
    assert decimal_string(Decimal(1) / Decimal(3)) == "0.33"
    assert decimal_string("2.680") == "2.68"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.01", "not-a-number"])
def test_decimal_score_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        quantize_score(value)


def test_option_normalization_is_unique_sorted_and_reports_unknown_values() -> None:
    result = normalize_options("a,c,A,? ")
    assert result.options == ("A", "C")
    assert result.issues == ("UNRECOGNIZED_OPTION:?",)


def test_option_normalization_accepts_brackets_and_leading_ocr_label() -> None:
    assert normalize_options("（ B D").options == ("B", "D")
    leaked = normalize_options(r"A.\frac{kq}{L^2}")
    assert leaked.options == ("A",)
    assert leaked.issues == (r"OPTION_WITH_TRAILING_TEXT:.\FRAC{KQ}{L^2}",)


def test_text_normalization_and_teacher_synonyms() -> None:
    assert normalize_text("  电 场 强 度。") == "电场强度"
    assert matches_exact_or_synonym("E 场", ["电场"], ["E场"])
    assert not matches_exact_or_synonym("场强", ["电场"], [])


def test_contract_rejects_invalid_evidence_range() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(
            page_id="page",
            region_id="region",
            original_bbox=BoundingBox(x=0, y=0, width=10, height=10),
            char_or_step_range=(3, 3),
        )


def test_blocked_decision_requires_blocking_point() -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(
            key="P2",
            status=DecisionStatus.BLOCKED_BY_DEPENDENCY,
            score=parse_decimal("0"),
            max_score=parse_decimal("2"),
        )
