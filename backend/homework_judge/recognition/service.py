from __future__ import annotations

import base64
import math
from collections.abc import Sequence
from typing import Any, cast

from ..config import Settings
from ..errors import AppError
from ..matching.numbers import normalize_question_number
from .blank_detection import (
    BlankDetectionRequest,
    BlankDetectionResult,
    normalize_blank_detection,
)
from .boundary import (
    RecognitionDraft,
    apply_boundary_decisions,
    compact_drafts,
    context_for_boundary,
)
from .calculation_localization import (
    CalculationLocalizationBatchResult,
    CalculationRecognitionBatchResult,
    CalculationSearchFragment,
    normalize_calculation_localization_batch,
    normalize_calculation_recognition_batch,
)
from .client import DashScopeClient
from .consolidator import consolidate_answers, consolidate_questions
from .normalizer import (
    normalize_answer,
    normalize_answer_regions,
    normalize_question,
    normalize_question_regions,
)
from .parser import (
    parse_blank_detection,
    parse_boundary_payload,
    parse_calculation_localization,
    parse_calculation_recognition,
    parse_keyed_fill_response,
    parse_model_payload,
    parse_student_response,
    parse_template_regions,
)
from .prompts import (
    ANSWER_BOUNDARY_PROMPT_VERSION,
    ANSWER_BOUNDARY_SYSTEM_PROMPT,
    ANSWER_PROMPT_VERSION,
    ANSWER_SYSTEM_PROMPT,
    BLANK_DETECTION_SYSTEM_PROMPT,
    CALCULATION_LOCALIZATION_PROMPT_VERSION,
    CALCULATION_LOCALIZATION_SYSTEM_PROMPT,
    CALCULATION_RECOGNITION_PROMPT_VERSION,
    CALCULATION_RECOGNITION_SYSTEM_PROMPT,
    EXAM_BOUNDARY_PROMPT_VERSION,
    EXAM_BOUNDARY_SYSTEM_PROMPT,
    EXAM_PROMPT_VERSION,
    EXAM_SYSTEM_PROMPT,
    KEYED_FILL_RESPONSE_PROMPT_VERSION,
    KEYED_FILL_RESPONSE_SYSTEM_PROMPT,
    QUESTION_REGION_PROMPT_VERSION,
    QUESTION_REGION_SYSTEM_PROMPT,
    SINGLE_QUESTION_PROMPT_VERSION,
    SINGLE_QUESTION_SYSTEM_PROMPT,
    STUDENT_RESPONSE_PROMPT_VERSION,
    STUDENT_RESPONSE_SYSTEM_PROMPT,
    TEMPLATE_REGION_PROMPT_VERSION,
    TEMPLATE_REGION_SYSTEM_PROMPT,
    blank_detection_prompt,
    boundary_user_prompt,
    calculation_localization_prompt,
    calculation_recognition_prompt,
    keyed_fill_response_prompt,
    question_region_prompt,
    single_question_prompt,
    student_response_prompt,
    template_region_prompt,
    user_prompt,
)


def _page_batches(pages: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if not pages:
        return []
    if len(pages) <= size:
        return [pages]
    return [pages[start : start + size] for start in range(0, len(pages), size)]


class _SingleQuestionOutputError(AppError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object],
        raw_response: dict[str, Any],
        model_usage: dict[str, int],
    ) -> None:
        super().__init__(422, code, message, details)
        self.raw_response = raw_response
        self.model_usage = model_usage


class RecognitionService:
    def __init__(self, settings: Settings, client: DashScopeClient) -> None:
        self.settings = settings
        self.client = client

    def _batches(self, role: str, pages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        size = (
            self.settings.answer_pages_per_batch
            if role == "answer"
            else self.settings.model_pages_per_batch
        )
        return _page_batches(pages, size)

    def _content(self, role: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        numbers = [int(page["page_number"]) for page in pages]
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt(role, numbers)}]
        for page in pages:
            path = (self.settings.data_dir / str(page["image_path"])).resolve()
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{data}"},
                }
            )
        return content

    @staticmethod
    def _parse_issue_messages(role: str, issues: Sequence[dict[str, Any]]) -> list[str]:
        subject = "题目" if role == "exam" else "答案"
        messages: list[str] = []
        for issue in issues:
            code = str(issue.get("code") or "invalid_model_output")
            path = str(issue.get("path") or "$")
            messages.append(
                f"模型返回的{subject}格式异常（{code}，位置 {path}），请核对是否存在遗漏"
            )
        return messages

    async def recognize(
        self,
        role: str,
        pages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        system = EXAM_SYSTEM_PROMPT if role == "exam" else ANSWER_SYSTEM_PROMPT
        drafts: list[RecognitionDraft] = []
        batch_records: list[dict[str, Any]] = []
        parse_issue_messages: list[str] = []
        totals = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
        batches = self._batches(role, pages)
        for batch_index, batch in enumerate(batches, 1):
            response = await self.client.chat(
                system_prompt=system,
                user_content=self._content(role, batch),
            )
            parsed = parse_model_payload(response.content, role)
            parse_issue_messages.extend(self._parse_issue_messages(role, parsed.issues))
            allowed = {int(page["page_number"]) for page in batch}
            for node in parsed.nodes:
                index = len(drafts)
                item = (
                    normalize_question(node, index, allowed)
                    if role == "exam"
                    else normalize_answer(node, index, allowed)
                )
                draft_id = f"{role}-{batch_index}-{index}"
                drafts.append(
                    RecognitionDraft(
                        draft_id=draft_id,
                        role=role,  # type: ignore[arg-type]
                        batch_index=batch_index,
                        sort_order=index,
                        item=item,
                    )
                )
            batch_records.append(
                {
                    "phase": "main_batch",
                    "role": role,
                    "batch": batch_index,
                    "pages": sorted(allowed),
                    "raw": response.raw,
                    "parseIssues": parsed.issues,
                }
            )
            for key in totals:
                totals[key] += response.usage.get(key, 0)
        for boundary_index in range(1, len(batches)):
            left_page = batches[boundary_index - 1][-1]
            right_page = batches[boundary_index][0]
            context = context_for_boundary(
                role,  # type: ignore[arg-type]
                boundary_index,
                left_page,
                right_page,
                drafts,
            )
            record: dict[str, Any] = {
                "phase": "boundary_merge",
                "role": role,
                "boundary": boundary_index,
                "pages": [int(left_page["page_number"]), int(right_page["page_number"])],
                "parseIssues": [],
            }
            try:
                response = await self.client.chat(
                    system_prompt=(
                        EXAM_BOUNDARY_SYSTEM_PROMPT
                        if role == "exam"
                        else ANSWER_BOUNDARY_SYSTEM_PROMPT
                    ),
                    user_content=self._boundary_content(context),
                )
                parsed = parse_boundary_payload(response.content)
                parse_issue_messages.extend(self._parse_issue_messages(role, parsed.issues))
                drafts, summaries = apply_boundary_decisions(
                    context,
                    drafts,
                    parsed.nodes,
                    self.settings.boundary_merge_min_confidence,
                )
                record.update(
                    raw=response.raw,
                    decisions=summaries,
                    parseIssues=parsed.issues,
                    error=None,
                )
                for key in totals:
                    totals[key] += response.usage.get(key, 0)
            except AppError as error:
                for draft in [*context.left_drafts, *context.right_drafts]:
                    draft.item["issues"] = list(
                        dict.fromkeys(
                            [
                                *draft.item.get("issues", []),
                                "boundary_merge_needs_review",
                                error.code,
                            ]
                        )
                    )
                record.update(
                    raw=None,
                    decisions=[],
                    error={"code": error.code, "message": error.message},
                )
            batch_records.append(record)
        merged = self._merge_overlaps(role, [draft.item for draft in drafts])
        if not merged:
            raise AppError(
                422,
                "RECOGNITION_EMPTY",
                "模型没有返回任何可用题目" if role == "exam" else "模型没有返回任何可用答案",
            )
        if parse_issue_messages:
            merged[0]["issues"] = list(
                dict.fromkeys([*merged[0].get("issues", []), *parse_issue_messages])
            )
        return merged, batch_records, totals

    def _boundary_content(self, context: Any) -> list[dict[str, Any]]:
        left_page = int(context.left_page["page_number"])
        right_page = int(context.right_page["page_number"])
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": boundary_user_prompt(
                    context.role,
                    left_page,
                    right_page,
                    compact_drafts(context),
                ),
            }
        ]
        for page in (context.left_page, context.right_page):
            path = (self.settings.data_dir / str(page["image_path"])).resolve()
            content.append(self._inline_image(path.read_bytes()))
        return content

    async def locate_calculation_regions(
        self,
        question: dict[str, Any],
        fragments: Sequence[CalculationSearchFragment],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[CalculationLocalizationBatchResult, dict[str, Any], dict[str, int]]:
        """Locate student-added calculation work in one already-bounded batch."""

        if not fragments:
            raise AppError(
                422,
                "CALCULATION_LOCALIZATION_FRAGMENTS_EMPTY",
                "计算题定位批次没有可检查的搜索片段",
            )
        limit = int(getattr(self.settings, "answer_pages_per_batch", len(fragments)))
        if len(fragments) > limit:
            raise AppError(
                422,
                "CALCULATION_LOCALIZATION_BATCH_TOO_LARGE",
                "计算题定位服务一次只接受一个有界批次",
                {"fragmentCount": len(fragments), "limit": limit},
            )
        ordered = sorted(fragments, key=lambda fragment: fragment.sort_order)
        fragment_keys = [fragment.fragment_key for fragment in ordered]
        page_numbers = [fragment.page_number for fragment in ordered]
        sort_orders = [fragment.sort_order for fragment in ordered]
        if (
            len(set(fragment_keys)) != len(fragment_keys)
            or len(set(page_numbers)) != len(page_numbers)
            or len(set(sort_orders)) != len(sort_orders)
        ):
            raise AppError(
                422,
                "CALCULATION_LOCALIZATION_FRAGMENTS_INVALID",
                "计算题定位批次的片段键、页码和顺序必须唯一",
            )
        if (
            not frame_set_id.strip()
            or not attempt_id.strip()
            or isinstance(batch_index, bool)
            or not isinstance(batch_index, int)
            or batch_index <= 0
        ):
            raise AppError(
                422,
                "CALCULATION_LOCALIZATION_METADATA_INVALID",
                "计算题定位批次缺少稳定的题框版本或尝试标识",
            )
        for fragment in ordered:
            geometry = (fragment.x, fragment.y, fragment.width, fragment.height)
            if (
                not fragment.fragment_key.strip()
                or not fragment.template_page_id.strip()
                or not fragment.student_page_id.strip()
                or not fragment.alignment_revision_id.strip()
                or fragment.page_number <= 0
                or not math.isfinite(fragment.alignment_confidence)
                or not 0.0 <= fragment.alignment_confidence <= 1.0
                or any(not math.isfinite(value) for value in geometry)
                or fragment.x < 0
                or fragment.y < 0
                or fragment.width <= 0
                or fragment.height <= 0
                or fragment.x + fragment.width > 1
                or fragment.y + fragment.height > 1
            ):
                raise AppError(
                    422,
                    "CALCULATION_LOCALIZATION_FRAGMENTS_INVALID",
                    "计算题定位搜索片段必须位于归一化页面范围内",
                    {"fragmentKey": fragment.fragment_key},
                )
            if (
                not isinstance(fragment.template_image, bytes)
                or not fragment.template_image
                or not isinstance(fragment.student_image, bytes)
                or not fragment.student_image
            ):
                raise AppError(
                    422,
                    "CALCULATION_LOCALIZATION_IMAGES_MISSING",
                    "每个计算题搜索片段都必须包含模板与学生配对图像",
                    {"fragmentKey": fragment.fragment_key},
                )

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": calculation_localization_prompt(
                    question,
                    [fragment.snapshot() for fragment in ordered],
                    frame_set_id=frame_set_id,
                    batch_index=batch_index,
                    attempt_id=attempt_id,
                ),
            }
        ]
        for fragment in ordered:
            content.append(
                {
                    "type": "text",
                    "text": f"Fragment {fragment.fragment_key}, blank template search window:",
                }
            )
            content.append(self._inline_image(cast(bytes, fragment.template_image)))
            content.append(
                {
                    "type": "text",
                    "text": f"Fragment {fragment.fragment_key}, aligned student search window:",
                }
            )
            content.append(self._inline_image(cast(bytes, fragment.student_image)))
        response = await self.client.chat(
            system_prompt=CALCULATION_LOCALIZATION_SYSTEM_PROMPT,
            user_content=content,
        )
        parsed = parse_calculation_localization(response.content)
        model_id = str(getattr(self.settings, "dashscope_model", "unknown-model")).strip()
        min_confidence = float(
            getattr(self.settings, "grading_recognition_review_threshold", 0.75)
        )
        result = normalize_calculation_localization_batch(
            parsed.nodes,
            ordered,
            batch_index=batch_index,
            attempt_id=attempt_id,
            model_id=model_id or "unknown-model",
            prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
            parse_issues=parsed.issues,
            min_confidence=min_confidence,
        )
        return result, response.raw, response.usage

    async def recognize_calculation_batch(
        self,
        question: dict[str, Any],
        fragments: Sequence[CalculationSearchFragment],
        *,
        frame_set_id: str,
        batch_index: int,
        attempt_id: str,
    ) -> tuple[CalculationRecognitionBatchResult, dict[str, Any], dict[str, int]]:
        """Locate and transcribe one already-bounded calculation batch."""

        if not fragments:
            raise AppError(
                422,
                "CALCULATION_RECOGNITION_FRAGMENTS_EMPTY",
                "The calculation recognition batch has no search fragments.",
            )
        limit = int(getattr(self.settings, "answer_pages_per_batch", len(fragments)))
        if len(fragments) > limit:
            raise AppError(
                422,
                "CALCULATION_RECOGNITION_BATCH_TOO_LARGE",
                "The calculation recognition batch exceeds the configured limit.",
                {"fragmentCount": len(fragments), "limit": limit},
            )
        ordered = sorted(fragments, key=lambda fragment: fragment.sort_order)
        fragment_keys = [fragment.fragment_key for fragment in ordered]
        page_numbers = [fragment.page_number for fragment in ordered]
        sort_orders = [fragment.sort_order for fragment in ordered]
        if (
            len(set(fragment_keys)) != len(fragment_keys)
            or len(set(page_numbers)) != len(page_numbers)
            or len(set(sort_orders)) != len(sort_orders)
        ):
            raise AppError(
                422,
                "CALCULATION_RECOGNITION_FRAGMENTS_INVALID",
                "Calculation fragment keys, pages, and sort orders must be unique.",
            )
        if (
            not frame_set_id.strip()
            or not attempt_id.strip()
            or isinstance(batch_index, bool)
            or not isinstance(batch_index, int)
            or batch_index <= 0
        ):
            raise AppError(
                422,
                "CALCULATION_RECOGNITION_METADATA_INVALID",
                "Calculation recognition metadata is incomplete.",
            )
        for fragment in ordered:
            geometry = (fragment.x, fragment.y, fragment.width, fragment.height)
            if (
                not fragment.fragment_key.strip()
                or not fragment.template_page_id.strip()
                or not fragment.student_page_id.strip()
                or not fragment.alignment_revision_id.strip()
                or fragment.page_number <= 0
                or not math.isfinite(fragment.alignment_confidence)
                or not 0.0 <= fragment.alignment_confidence <= 1.0
                or any(not math.isfinite(value) for value in geometry)
                or fragment.x < 0
                or fragment.y < 0
                or fragment.width <= 0
                or fragment.height <= 0
                or fragment.x + fragment.width > 1
                or fragment.y + fragment.height > 1
            ):
                raise AppError(
                    422,
                    "CALCULATION_RECOGNITION_FRAGMENTS_INVALID",
                    "Calculation search fragments must be valid normalized boxes.",
                    {"fragmentKey": fragment.fragment_key},
                )
            if (
                not isinstance(fragment.template_image, bytes)
                or not fragment.template_image
                or not isinstance(fragment.student_image, bytes)
                or not fragment.student_image
            ):
                raise AppError(
                    422,
                    "CALCULATION_RECOGNITION_IMAGES_MISSING",
                    "Each calculation search fragment requires a paired image.",
                    {"fragmentKey": fragment.fragment_key},
                )

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": calculation_recognition_prompt(
                    question,
                    [fragment.snapshot() for fragment in ordered],
                    frame_set_id=frame_set_id,
                    batch_index=batch_index,
                    attempt_id=attempt_id,
                ),
            }
        ]
        for fragment in ordered:
            content.append(
                {
                    "type": "text",
                    "text": f"Fragment {fragment.fragment_key}, blank template search window:",
                }
            )
            content.append(self._inline_image(cast(bytes, fragment.template_image)))
            content.append(
                {
                    "type": "text",
                    "text": f"Fragment {fragment.fragment_key}, aligned student search window:",
                }
            )
            content.append(self._inline_image(cast(bytes, fragment.student_image)))
        response = await self.client.chat(
            system_prompt=CALCULATION_RECOGNITION_SYSTEM_PROMPT,
            user_content=content,
        )
        parsed = parse_calculation_recognition(response.content)
        model_id = str(getattr(self.settings, "dashscope_model", "unknown-model")).strip()
        min_confidence = float(
            getattr(self.settings, "grading_recognition_review_threshold", 0.75)
        )
        result = normalize_calculation_recognition_batch(
            parsed.nodes,
            ordered,
            batch_index=batch_index,
            attempt_id=attempt_id,
            model_id=model_id or "unknown-model",
            prompt_version=CALCULATION_RECOGNITION_PROMPT_VERSION,
            parse_issues=parsed.issues,
            min_confidence=min_confidence,
        )
        return result, response.raw, response.usage

    async def recognize_student_response(
        self,
        question: dict[str, Any],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        """Transcribe one question from paired blank/student region images.

        Region crops are intentionally passed as bytes and are never persisted;
        the durable evidence is the source page plus its original-page
        coordinates.
        """
        if not regions:
            raise AppError(422, "STUDENT_REGIONS_EMPTY", "没有可识别的学生作答区域")
        page_numbers = [int(region["page_number"]) for region in regions]
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": student_response_prompt(question, page_numbers),
            }
        ]
        for index, region in enumerate(regions, 1):
            content.append(
                {
                    "type": "text",
                    "text": f"Region {index}, blank template:",
                }
            )
            content.append(self._inline_image(bytes(region["template_image"])))
            content.append(
                {
                    "type": "text",
                    "text": f"Region {index}, aligned student page:",
                }
            )
            content.append(self._inline_image(bytes(region["student_image"])))
        response = await self.client.chat(
            system_prompt=STUDENT_RESPONSE_SYSTEM_PROMPT,
            user_content=content,
        )
        parsed = parse_student_response(response.content)
        if parsed is None:
            raise AppError(422, "STUDENT_RESPONSE_INVALID", "学生作答识别结果不是有效 JSON")
        transcription = str(parsed.get("transcription", "")).strip()
        is_blank = bool(parsed.get("isBlank", False))
        issues = (
            [str(issue).strip() for issue in parsed.get("issues", []) if str(issue).strip()]
            if isinstance(parsed.get("issues"), list)
            else []
        )
        try:
            confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
            issues.append("invalid_confidence")
        if is_blank:
            transcription = ""
        elif not transcription:
            issues.append("empty_transcription")
        segments: list[dict[str, Any]] = []
        raw_segments = parsed.get("segments")
        if isinstance(raw_segments, list):
            used_indexes: set[int] = set()
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    issues.append("segment_not_object")
                    continue
                try:
                    region_index = int(raw_segment.get("regionIndex", 0))
                except (TypeError, ValueError):
                    issues.append("segment_index_invalid")
                    continue
                if not 1 <= region_index <= len(regions) or region_index in used_indexes:
                    issues.append("segment_index_invalid")
                    continue
                used_indexes.add(region_index)
                segment_blank = bool(raw_segment.get("isBlank", False))
                segment_text = str(raw_segment.get("transcription", "")).strip()
                segment_issues = (
                    [
                        str(issue).strip()
                        for issue in raw_segment.get("issues", [])
                        if str(issue).strip()
                    ]
                    if isinstance(raw_segment.get("issues"), list)
                    else []
                )
                try:
                    segment_confidence = min(
                        1.0, max(0.0, float(raw_segment.get("confidence", confidence)))
                    )
                except (TypeError, ValueError):
                    segment_confidence = 0.5
                    segment_issues.append("invalid_confidence")
                if segment_blank:
                    segment_text = ""
                segments.append(
                    {
                        "region_index": region_index,
                        "transcription": segment_text,
                        "is_blank": segment_blank,
                        "confidence": segment_confidence,
                        "issues": list(dict.fromkeys(segment_issues)),
                    }
                )
            segments.sort(key=lambda item: int(item["region_index"]))
        if not segments and len(regions) == 1:
            segments = [
                {
                    "region_index": 1,
                    "transcription": transcription,
                    "is_blank": is_blank,
                    "confidence": confidence,
                    "issues": list(dict.fromkeys(issues)),
                }
            ]
        elif len(segments) != len(regions):
            issues.append("segments_missing")
        return (
            {
                "transcription": transcription,
                "is_blank": is_blank,
                "confidence": confidence,
                "issues": list(dict.fromkeys(issues)),
                "segments": segments,
            },
            response.raw,
            response.usage,
        )

    async def recognize_keyed_fill_response(
        self,
        question: dict[str, Any],
        blanks: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        *,
        frame_set_id: str,
        blank_config_version_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
        """Transcribe arbitrary B1..Bn blanks without exposing grading truth."""

        if not regions:
            raise AppError(422, "STUDENT_REGIONS_EMPTY", "没有可识别的学生作答区域")

        expected_keys: list[str] = []
        blank_context: list[dict[str, object]] = []
        for blank in blanks:
            blank_key = blank.get("blankKey", blank.get("blank_key"))
            if not isinstance(blank_key, str) or not blank_key:
                raise AppError(422, "BLANK_KEYS_INVALID", "逐空配置缺少稳定键")
            expected_keys.append(blank_key)
            raw_anchor = blank.get("anchor")
            anchor: dict[str, object] = {}
            if isinstance(raw_anchor, dict):
                for key in (
                    "templatePageId",
                    "pageNumber",
                    "coordinateSpace",
                    "box",
                    "fragmentKey",
                ):
                    if key in raw_anchor:
                        anchor[key] = raw_anchor[key]
            blank_context.append({"blankKey": blank_key, "anchor": anchor})
        if not expected_keys or len(set(expected_keys)) != len(expected_keys):
            raise AppError(422, "BLANK_KEYS_INVALID", "逐空键必须存在且唯一")

        question_context = {
            key: question[key]
            for key in ("id", "type", "stem", "options", "subquestions", "layoutHints")
            if key in question
        }
        evidence_context: list[dict[str, object]] = []
        evidence_ids: list[str] = []
        for region in regions:
            evidence_id = region.get("evidence_id", region.get("evidenceId"))
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise AppError(422, "EVIDENCE_ID_INVALID", "识别证据缺少稳定 ID")
            evidence_ids.append(evidence_id)
            evidence_context.append(
                {
                    "evidenceId": evidence_id,
                    "pageNumber": region.get("page_number", region.get("pageNumber")),
                }
            )
        if len(set(evidence_ids)) != len(evidence_ids):
            raise AppError(422, "EVIDENCE_ID_INVALID", "识别证据 ID 重复")

        raw_responses: list[dict[str, Any]] = []
        total_usage: dict[str, int] = {}
        retry_issues: list[dict[str, object]] | None = None
        for attempt in range(1, 3):
            content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": keyed_fill_response_prompt(
                        question_context,
                        blank_context,
                        evidence_context,
                        frame_set_id=frame_set_id,
                        config_version_id=blank_config_version_id,
                        retry_issues=retry_issues,
                    ),
                }
            ]
            for evidence_id, region in zip(evidence_ids, regions, strict=True):
                content.append(
                    {
                        "type": "text",
                        "text": f"Evidence {evidence_id}, confirmed blank template frame:",
                    }
                )
                content.append(self._inline_image(bytes(region["template_image"])))
                content.append(
                    {
                        "type": "text",
                        "text": f"Evidence {evidence_id}, aligned student frame:",
                    }
                )
                content.append(self._inline_image(bytes(region["student_image"])))

            response = await self.client.chat(
                system_prompt=KEYED_FILL_RESPONSE_SYSTEM_PROMPT,
                user_content=content,
            )
            raw_responses.append(response.raw)
            for key, value in response.usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    total_usage[key] = total_usage.get(key, 0) + value
            parsed = parse_keyed_fill_response(
                response.content,
                expected_keys=expected_keys,
                allowed_evidence_refs=set(evidence_ids),
            )
            if not parsed.issues:
                return (
                    {
                        "status": "recognized",
                        "answers": parsed.nodes,
                        "issues": [],
                        "attemptCount": attempt,
                    },
                    raw_responses,
                    total_usage,
                )
            retry_issues = parsed.issues

        return (
            {
                "status": "recognition_needs_review",
                "answers": [],
                "issues": retry_issues or [],
                "attemptCount": 2,
            },
            raw_responses,
            total_usage,
        )

    async def detect_blank_anchors(
        self,
        request: BlankDetectionRequest,
    ) -> tuple[BlankDetectionResult, dict[str, Any], dict[str, int]]:
        """Detect keyed blank anchors from every fragment of a confirmed frame."""

        content: list[dict[str, Any]] = [
            {"type": "text", "text": blank_detection_prompt(request.prompt_context())}
        ]
        for fragment in request.fragments:
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Fragment {fragment.region_key}; page {fragment.page_number}; "
                        f"sortOrder {fragment.sort_order}:"
                    ),
                }
            )
            content.append(self._inline_image(fragment.image))
        response = await self.client.chat(
            system_prompt=BLANK_DETECTION_SYSTEM_PROMPT,
            user_content=content,
        )
        payload = parse_blank_detection(response.content)
        result = normalize_blank_detection(payload or {}, request)
        return result, response.raw, response.usage

    async def recognize_template_regions(
        self,
        page: dict[str, Any],
        questions: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, float | int]]], dict[str, Any], dict[str, int]]:
        """Locate answer boxes on one blank template page."""
        page_number = int(page["page_number"])
        path = (self.settings.data_dir / str(page["image_path"])).resolve()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": template_region_prompt(page_number, questions),
            },
            self._inline_image(path.read_bytes()),
        ]
        response = await self.client.chat(
            system_prompt=TEMPLATE_REGION_SYSTEM_PROMPT,
            user_content=content,
        )
        parsed = parse_template_regions(response.content)
        if parsed is None:
            raise AppError(422, "TEMPLATE_REGIONS_INVALID", "答题区域识别结果不是有效 JSON")
        allowed_ids = {str(question.get("id", "")) for question in questions}
        ids_by_number: dict[str, list[str]] = {}
        for question in questions:
            number = normalize_question_number(str(question.get("number", "")))
            if number:
                ids_by_number.setdefault(number, []).append(str(question.get("id", "")))
        output: dict[str, list[dict[str, float | int]]] = {}
        for item in parsed:
            question_id = str(item.get("questionId", ""))
            number = normalize_question_number(
                str(item.get("questionNumber", item.get("number", "")))
            )
            if question_id not in allowed_ids:
                candidates = ids_by_number.get(number, [])
                question_id = candidates[0] if len(candidates) == 1 else ""
            if not question_id:
                continue
            raw_regions = item.get("answerRegions")
            if isinstance(raw_regions, list):
                raw_regions = [
                    {**region, "pageNumber": region.get("pageNumber", page_number)}
                    for region in raw_regions
                    if isinstance(region, dict)
                ]
            regions = normalize_answer_regions(raw_regions, {page_number})
            if regions:
                output.setdefault(question_id, []).extend(regions)
        return output, response.raw, response.usage

    async def recognize_question_regions(
        self,
        page: dict[str, Any],
        questions: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, int]]:
        """Locate complete question blocks on one blank template page."""
        page_number = int(page["page_number"])
        path = (self.settings.data_dir / str(page["image_path"])).resolve()
        response = await self.client.chat(
            system_prompt=QUESTION_REGION_SYSTEM_PROMPT,
            user_content=[
                {"type": "text", "text": question_region_prompt(page_number, questions)},
                self._inline_image(path.read_bytes()),
            ],
        )
        parsed = parse_template_regions(response.content)
        if parsed is None:
            raise AppError(422, "QUESTION_REGIONS_INVALID", "整题区域识别结果不是有效 JSON")
        allowed_ids = {str(question.get("id", "")) for question in questions}
        ids_by_number: dict[str, list[str]] = {}
        for question in questions:
            number = normalize_question_number(str(question.get("number", "")))
            if number:
                ids_by_number.setdefault(number, []).append(str(question.get("id", "")))
        output: dict[str, list[dict[str, Any]]] = {}
        for item in parsed:
            question_id = str(item.get("questionId", ""))
            number = normalize_question_number(
                str(item.get("questionNumber", item.get("number", "")))
            )
            if question_id not in allowed_ids:
                candidates = ids_by_number.get(number, [])
                question_id = candidates[0] if len(candidates) == 1 else ""
            if not question_id:
                continue
            raw_regions = item.get("questionRegions")
            if isinstance(raw_regions, list):
                raw_regions = [
                    {**region, "pageNumber": region.get("pageNumber", page_number)}
                    for region in raw_regions
                    if isinstance(region, dict)
                ]
            regions = normalize_question_regions(raw_regions, {page_number})
            if regions:
                output.setdefault(question_id, []).extend(regions)
        return output, response.raw, response.usage

    async def recognize_single_question(
        self,
        question: dict[str, Any],
        fragments: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        """Recognize one complete question from ordered teacher-frame crops."""

        if not fragments:
            raise AppError(422, "SINGLE_QUESTION_FRAMES_EMPTY", "当前题没有可识别的题框片段")
        ordered = sorted(
            fragments,
            key=lambda item: (
                int(item["page_number"]),
                int(item["sort_order"]),
                str(item["region_key"]),
            ),
        )
        allowed_pages = {int(item["page_number"]) for item in ordered}
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": single_question_prompt(
                    str(question.get("number", "")),
                    sorted(allowed_pages),
                ),
            }
        ]
        for index, fragment in enumerate(ordered, 1):
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Fragment {index}; key {fragment['region_key']}; "
                        f"page {int(fragment['page_number'])}; "
                        f"sortOrder {int(fragment['sort_order'])}:"
                    ),
                }
            )
            content.append(self._inline_image(bytes(fragment["image"])))
        response = await self.client.chat(
            system_prompt=SINGLE_QUESTION_SYSTEM_PROMPT,
            user_content=content,
        )
        parsed = parse_model_payload(response.content, "exam")
        if len(parsed.nodes) != 1:
            raise _SingleQuestionOutputError(
                "SINGLE_QUESTION_RESULT_COUNT_INVALID",
                "单题重新识别必须返回且只能返回一道题",
                {"questionCount": len(parsed.nodes), "parseIssues": parsed.issues},
                response.raw,
                response.usage,
            )
        normalized = normalize_question(parsed.nodes[0], 0, allowed_pages)
        if not normalized["detected_number"] or not normalized["stem"]:
            raise _SingleQuestionOutputError(
                "SINGLE_QUESTION_RESULT_INCOMPLETE",
                "单题重新识别结果缺少题号或题干，原题内容未更新",
                {"issues": normalized["issues"], "parseIssues": parsed.issues},
                response.raw,
                response.usage,
            )
        if parsed.issues:
            normalized["issues"] = list(
                dict.fromkeys(
                    [
                        *normalized["issues"],
                        *(f"parse:{issue['code']}" for issue in parsed.issues),
                    ]
                )
            )
        return normalized, response.raw, response.usage

    @staticmethod
    def _inline_image(data: bytes) -> dict[str, Any]:
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
        }

    @staticmethod
    def _merge_overlaps(role: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return consolidate_questions(items) if role == "exam" else consolidate_answers(items)

    @staticmethod
    def prompt_version(role: str) -> str:
        if role == "exam":
            return f"{EXAM_PROMPT_VERSION}+{EXAM_BOUNDARY_PROMPT_VERSION}"
        if role == "student_response":
            return STUDENT_RESPONSE_PROMPT_VERSION
        if role == "template_regions":
            return TEMPLATE_REGION_PROMPT_VERSION
        if role == "question_regions":
            return QUESTION_REGION_PROMPT_VERSION
        if role == "single_question":
            return SINGLE_QUESTION_PROMPT_VERSION
        if role == "keyed_fill_response":
            return KEYED_FILL_RESPONSE_PROMPT_VERSION
        if role == "calculation_localization":
            return CALCULATION_LOCALIZATION_PROMPT_VERSION
        if role == "calculation_recognition":
            return CALCULATION_RECOGNITION_PROMPT_VERSION
        return f"{ANSWER_PROMPT_VERSION}+{ANSWER_BOUNDARY_PROMPT_VERSION}"
