from decimal import Decimal

import pytest

from homework_judge.grading.contracts import DecisionRecord, DecisionStatus
from homework_judge.grading.dependencies import (
    RubricPoint,
    propagate_dependencies,
    validate_rubric,
)


def point(key: str, order: int, dependencies: list[str] | None = None) -> RubricPoint:
    return RubricPoint(
        key=key,
        criterion=key,
        score=Decimal("1"),
        order=order,
        dependencies=dependencies or [],
    )


def decision(
    key: str,
    status: DecisionStatus,
    *,
    score: Decimal | str | int = 0,
) -> DecisionRecord:
    return DecisionRecord(
        key=key,
        status=status,
        score=score,
        max_score=1,
        reason=f"direct-{key}",
    )


def test_rubric_validation_rejects_cycles_unknown_dependencies_and_wrong_sum() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        validate_rubric([point("P1", 1, ["P2"]), point("P2", 2, ["P1"])], 2)
    with pytest.raises(ValueError, match="unknown"):
        validate_rubric([point("P1", 1, ["missing"])], 1)
    with pytest.raises(ValueError, match="add up"):
        validate_rubric([point("P1", 1)], 2)


@pytest.mark.parametrize(
    ("downstream_status", "direct_score", "expected_score"),
    [
        (DecisionStatus.SATISFIED, 0, Decimal("1.00")),
        (DecisionStatus.PARTIAL, Decimal("0.40"), Decimal("0.50")),
        (DecisionStatus.FAILED, 0, Decimal("0.00")),
    ],
)
def test_explicit_downstream_decision_survives_failed_prerequisite(
    downstream_status: DecisionStatus,
    direct_score: Decimal | int,
    expected_score: Decimal,
) -> None:
    points = [point("P1", 1), point("P2", 2, ["P1"])]
    decisions = [
        decision("P1", DecisionStatus.FAILED),
        decision("P2", downstream_status, score=direct_score),
    ]

    result = {item.key: item for item in propagate_dependencies(points, decisions)}

    assert result["P1"].status is DecisionStatus.FAILED
    assert result["P2"].status is downstream_status
    assert result["P2"].score == expected_score
    assert result["P2"].blocked_by is None
    assert result["P2"].reason == "direct-P2"


def test_unable_downstream_is_blocked_and_transitively_keeps_root_cause() -> None:
    points = [
        point("P1", 1),
        point("P2", 2, ["P1"]),
        point("P3", 3, ["P2"]),
        point("P4", 4),
    ]
    decisions = [
        decision("P1", DecisionStatus.FAILED),
        decision("P2", DecisionStatus.UNABLE),
        decision("P3", DecisionStatus.UNABLE),
        decision("P4", DecisionStatus.UNABLE),
    ]

    result = {item.key: item for item in propagate_dependencies(points, decisions)}

    assert result["P1"].score == 0
    assert result["P2"].status is DecisionStatus.BLOCKED_BY_DEPENDENCY
    assert result["P2"].blocked_by == "P1"
    assert result["P2"].reason == "依赖评分点 P1 未通过"
    assert result["P2"].score == Decimal("0.00")
    assert result["P3"].status is DecisionStatus.BLOCKED_BY_DEPENDENCY
    assert result["P3"].blocked_by == "P1"
    assert result["P4"].status is DecisionStatus.UNABLE
    assert result["P4"].blocked_by is None
    assert result["P4"].reason == "direct-P4"
