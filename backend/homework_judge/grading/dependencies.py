from __future__ import annotations

from collections import deque
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import DecisionRecord, DecisionStatus
from .normalization import parse_decimal, quantize_score


class RubricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    score: Decimal = Field(gt=0)
    order: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)


def validate_rubric(points: list[RubricPoint], max_score: Decimal | str | int) -> list[str]:
    if not points:
        raise ValueError("rubric must contain at least one point")
    by_key = {point.key: point for point in points}
    if len(by_key) != len(points):
        raise ValueError("rubric point keys must be unique")
    expected = parse_decimal(max_score)
    if sum((point.score for point in points), Decimal(0)) != expected:
        raise ValueError("rubric point scores must add up to max_score")

    indegree = {key: 0 for key in by_key}
    children: dict[str, set[str]] = {key: set() for key in by_key}
    for point in points:
        if point.key in point.dependencies:
            raise ValueError("rubric point cannot depend on itself")
        for dependency in point.dependencies:
            if dependency not in by_key:
                raise ValueError(f"unknown rubric dependency: {dependency}")
            if point.key not in children[dependency]:
                children[dependency].add(point.key)
                indegree[point.key] += 1

    ready = deque(
        sorted(
            (key for key, degree in indegree.items() if degree == 0),
            key=lambda key: by_key[key].order,
        )
    )
    order: list[str] = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for child in sorted(children[key], key=lambda item: by_key[item].order):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(points):
        raise ValueError("rubric dependencies must be acyclic")
    return order


def propagate_dependencies(
    points: list[RubricPoint],
    direct_decisions: list[DecisionRecord],
) -> list[DecisionRecord]:
    order = validate_rubric(points, sum((point.score for point in points), Decimal(0)))
    point_by_key = {point.key: point for point in points}
    direct_by_key = {decision.key: decision for decision in direct_decisions}
    if set(direct_by_key) != set(point_by_key):
        raise ValueError("every rubric point must have exactly one direct decision")

    final: dict[str, DecisionRecord] = {}
    for key in order:
        point = point_by_key[key]
        decision = direct_by_key[key]
        blockers = [
            dependency
            for dependency in point.dependencies
            if final[dependency].status
            in {DecisionStatus.FAILED, DecisionStatus.BLOCKED_BY_DEPENDENCY}
        ]
        if blockers and decision.status is DecisionStatus.UNABLE:
            first = blockers[0]
            root = final[first].blocked_by or first
            final[key] = DecisionRecord(
                key=key,
                status=DecisionStatus.BLOCKED_BY_DEPENDENCY,
                score=Decimal(0),
                max_score=quantize_score(point.score),
                reason=f"依赖评分点 {root} 未通过",
                evidence_refs=decision.evidence_refs,
                blocked_by=root,
            )
            continue
        if decision.status is DecisionStatus.SATISFIED:
            score = point.score
        elif decision.status is DecisionStatus.PARTIAL:
            score = point.score / Decimal(2)
        else:
            score = Decimal(0)
        final[key] = decision.model_copy(
            update={"score": quantize_score(score), "max_score": quantize_score(point.score)}
        )
    return [final[key] for key in order]
