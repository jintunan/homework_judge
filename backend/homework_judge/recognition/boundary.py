from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

from ..matching.numbers import normalize_question_number
from .normalizer import merge_question_regions_by_page, normalize_answer, normalize_question

Role = Literal["exam", "answer"]


@dataclass(slots=True)
class RecognitionDraft:
    draft_id: str
    role: Role
    batch_index: int
    sort_order: int
    item: dict[str, Any]


@dataclass(slots=True)
class BoundaryContext:
    role: Role
    boundary_index: int
    left_page: dict[str, Any]
    right_page: dict[str, Any]
    left_drafts: list[RecognitionDraft]
    right_drafts: list[RecognitionDraft]

    @property
    def draft_ids(self) -> set[str]:
        return {draft.draft_id for draft in [*self.left_drafts, *self.right_drafts]}


@dataclass(slots=True)
class BoundaryDecision:
    relation: Literal["merge", "separate", "uncertain"]
    draft_ids: list[str]
    merged_item: dict[str, Any] | None
    confidence: float
    issues: list[str]


def context_for_boundary(
    role: Role,
    boundary_index: int,
    left_page: dict[str, Any],
    right_page: dict[str, Any],
    drafts: list[RecognitionDraft],
) -> BoundaryContext:
    left_number = int(left_page["page_number"])
    right_number = int(right_page["page_number"])
    left = [
        draft
        for draft in drafts
        if draft.batch_index == boundary_index
        and left_number in {int(page) for page in draft.item.get("source_pages", [])}
    ]
    right = [
        draft
        for draft in drafts
        if draft.batch_index == boundary_index + 1
        and right_number in {int(page) for page in draft.item.get("source_pages", [])}
    ]
    return BoundaryContext(role, boundary_index, left_page, right_page, left, right)


def compact_drafts(context: BoundaryContext) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for side, drafts in (("left", context.left_drafts), ("right", context.right_drafts)):
        for draft in drafts:
            item = draft.item
            output.append(
                {
                    "draftId": draft.draft_id,
                    "side": side,
                    "number": item.get("detected_number", item.get("number_hint", "")),
                    "stem": str(item.get("stem", item.get("stem_hint", "")))[:4000],
                    "answer": str(item.get("answer", ""))[:4000],
                    "explanation": str(item.get("explanation", ""))[:8000],
                    "sourcePages": item.get("source_pages", []),
                }
            )
    return output


def _decision(node: dict[str, Any]) -> BoundaryDecision:
    relation = str(node.get("relation", "uncertain"))
    if relation not in {"merge", "separate", "uncertain"}:
        relation = "uncertain"
    raw_ids = node.get("draftIds")
    draft_ids = (
        list(dict.fromkeys(str(value) for value in raw_ids if str(value).strip()))
        if isinstance(raw_ids, list)
        else []
    )
    try:
        confidence = min(1.0, max(0.0, float(node.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    issues = (
        [str(value).strip() for value in node.get("issues", []) if str(value).strip()]
        if isinstance(node.get("issues"), list)
        else []
    )
    merged = node.get("mergedItem")
    return BoundaryDecision(
        relation=relation,  # type: ignore[arg-type]
        draft_ids=draft_ids,
        merged_item=merged if isinstance(merged, dict) else None,
        confidence=confidence,
        issues=issues,
    )


def _mark_needs_review(drafts: list[RecognitionDraft], ids: set[str], reason: str) -> None:
    for draft in drafts:
        if ids and draft.draft_id not in ids:
            continue
        issues = list(draft.item.get("issues", []))
        issues.extend(["boundary_merge_needs_review", reason])
        draft.item["issues"] = list(dict.fromkeys(issues))


def _normalized_number(role: Role, item: dict[str, Any]) -> str:
    value = item.get("detected_number") if role == "exam" else item.get("number_hint")
    return normalize_question_number(str(value or ""))


def _merge_original_metadata(
    normalized: dict[str, Any],
    originals: list[RecognitionDraft],
) -> dict[str, Any]:
    output = copy.deepcopy(normalized)
    output["source_pages"] = sorted(
        {int(page) for draft in originals for page in draft.item.get("source_pages", [])}
        | {int(page) for page in output.get("source_pages", [])}
    )
    output["issues"] = list(
        dict.fromkeys(
            [
                *(str(issue) for draft in originals for issue in draft.item.get("issues", [])),
                *(str(issue) for issue in output.get("issues", [])),
            ]
        )
    )
    output["confidence"] = min(
        [
            float(output.get("confidence", 0.5)),
            *(float(draft.item.get("confidence", 0.5)) for draft in originals),
        ]
    )
    output["sort_order"] = min(draft.sort_order for draft in originals)
    if "answer_regions" in output:
        regions = [
            copy.deepcopy(region)
            for draft in originals
            for region in draft.item.get("answer_regions", [])
            if isinstance(region, dict)
        ] + [
            copy.deepcopy(region)
            for region in output.get("answer_regions", [])
            if isinstance(region, dict)
        ]
        output["answer_regions"] = list(
            {
                (
                    int(region.get("page_number", 0)),
                    float(region.get("x", 0)),
                    float(region.get("y", 0)),
                    float(region.get("width", 0)),
                    float(region.get("height", 0)),
                ): region
                for region in regions
            }.values()
        )
    if "question_regions" in output:
        output["question_regions"] = merge_question_regions_by_page(
            [
                copy.deepcopy(region)
                for draft in originals
                for region in draft.item.get("question_regions", [])
                if isinstance(region, dict)
            ]
            + [
                copy.deepcopy(region)
                for region in output.get("question_regions", [])
                if isinstance(region, dict)
            ]
        )
    return output


def apply_boundary_decisions(
    context: BoundaryContext,
    drafts: list[RecognitionDraft],
    nodes: list[dict[str, Any]],
    min_confidence: float,
) -> tuple[list[RecognitionDraft], list[dict[str, Any]]]:
    by_id = {draft.draft_id: draft for draft in drafts}
    left_ids = {draft.draft_id for draft in context.left_drafts}
    right_ids = {draft.draft_id for draft in context.right_drafts}
    consumed: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for node in nodes:
        decision = _decision(node)
        ids = set(decision.draft_ids)
        reason = ""
        if not ids or not ids <= context.draft_ids:
            reason = "boundary_draft_reference_invalid"
        elif ids & consumed:
            reason = "boundary_draft_reused"
        elif decision.relation == "merge" and (not ids & left_ids or not ids & right_ids):
            reason = "boundary_merge_requires_both_sides"
        elif decision.relation == "merge" and decision.confidence < min_confidence:
            reason = "boundary_merge_low_confidence"
        elif decision.relation == "merge" and decision.merged_item is None:
            reason = "boundary_merged_item_missing"
        if decision.relation == "uncertain":
            reason = reason or "boundary_model_uncertain"
        if reason:
            _mark_needs_review(drafts, ids & context.draft_ids, reason)
            summaries.append({"relation": "uncertain", "draftIds": sorted(ids), "reason": reason})
            continue
        if decision.relation == "separate":
            summaries.append({"relation": "separate", "draftIds": sorted(ids)})
            continue
        originals = [by_id[draft_id] for draft_id in decision.draft_ids]
        allowed_pages = {
            int(page) for draft in originals for page in draft.item.get("source_pages", [])
        } | {int(context.left_page["page_number"]), int(context.right_page["page_number"])}
        assert decision.merged_item is not None
        raw_pages = decision.merged_item.get("sourcePages", [])
        raw_pages = raw_pages if isinstance(raw_pages, list) else [raw_pages]
        try:
            invalid_pages = {int(page) for page in raw_pages} - allowed_pages
        except (TypeError, ValueError):
            invalid_pages = {-1}
        if invalid_pages:
            reason = "boundary_source_page_invalid"
            _mark_needs_review(drafts, ids, reason)
            summaries.append(
                {
                    "relation": "uncertain",
                    "draftIds": sorted(ids),
                    "reason": reason,
                }
            )
            continue
        normalized = (
            normalize_question(decision.merged_item, 0, allowed_pages)
            if context.role == "exam"
            else normalize_answer(decision.merged_item, 0, allowed_pages)
        )
        original_numbers = {
            _normalized_number(context.role, draft.item)
            for draft in originals
            if _normalized_number(context.role, draft.item)
        }
        merged_number = _normalized_number(context.role, normalized)
        if len(original_numbers) > 1 or (
            original_numbers and merged_number not in original_numbers
        ):
            reason = "boundary_question_number_conflict"
            _mark_needs_review(drafts, ids, reason)
            summaries.append({"relation": "uncertain", "draftIds": sorted(ids), "reason": reason})
            continue
        merged_item = _merge_original_metadata(normalized, originals)
        first = min(originals, key=lambda draft: draft.sort_order)
        merged_draft = RecognitionDraft(
            draft_id=first.draft_id,
            role=context.role,
            batch_index=max(draft.batch_index for draft in originals),
            sort_order=min(draft.sort_order for draft in originals),
            item=merged_item,
        )
        drafts = [draft for draft in drafts if draft.draft_id not in ids]
        drafts.append(merged_draft)
        drafts.sort(key=lambda draft: draft.sort_order)
        by_id = {draft.draft_id: draft for draft in drafts}
        consumed.update(ids)
        summaries.append(
            {"relation": "merge", "draftIds": decision.draft_ids, "confidence": decision.confidence}
        )
    if not nodes and context.draft_ids:
        _mark_needs_review(drafts, context.draft_ids, "boundary_decision_missing")
        summaries.append(
            {
                "relation": "uncertain",
                "draftIds": sorted(context.draft_ids),
                "reason": "boundary_decision_missing",
            }
        )
    return drafts, summaries
