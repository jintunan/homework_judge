from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import sqlite3
import uuid
from decimal import Decimal
from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import ValidationError

from ..alignment.geometry import (
    Bounds,
    Homography,
    polygon_out_of_bounds_ratio,
    polygon_visible_ratio,
)
from ..alignment.models import AlignmentQuality, AlignmentResult, AnswerRegion, PageSize
from ..alignment.regions import extract_answer_regions
from ..artifacts.service import GradingArtifactService
from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError, ModelError
from ..files.storage import resolve_data_path
from ..grading.audit import audit_exam, audit_question
from ..grading.calculation import (
    CALCULATION_SCORING_POLICY_VERSION,
    CalculationModelOutputError,
    partial_credit_score,
)
from ..grading.contracts import (
    BoundingBox,
    CalculationEvidenceImagePair,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    QuestionType,
    ReviewReason,
    ToolObservation,
)
from ..grading.normalization import (
    decimal_string,
    normalize_options,
    parse_decimal,
)
from ..grading.prompts import CALCULATION_JUDGE_PROMPT_VERSION
from ..grading.router import grade_question
from ..observability import bind_log_context, log_event
from ..recognition.client import DashScopeClient

SUPPORTED_TYPES = {item.value for item in QuestionType}
LOGGER = logging.getLogger("homework_judge.grading")


class GradingPipeline:
    """Persisted, deterministic-first grading workflow for one student submission."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        model_client: DashScopeClient,
        artifact_service: GradingArtifactService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.model_client = model_client
        self.artifact_service = artifact_service or GradingArtifactService(
            settings,
            database,
            model_client,
        )
        self._question_semaphore = asyncio.Semaphore(settings.grading_concurrency)

    def create_run(
        self,
        submission_id: str,
        *,
        processing_revision_id: str | None = None,
        trigger_source: str = "manual",
    ) -> str:
        if not self.settings.grading_enabled:
            raise AppError(409, "GRADING_DISABLED", "作业批改功能尚未启用")
        run_id = uuid.uuid4().hex
        submission = self._submission(submission_id)
        current_revision_id = (
            str(submission["current_processing_revision_id"])
            if submission.get("current_processing_revision_id")
            else None
        )
        if processing_revision_id is not None and processing_revision_id != current_revision_id:
            raise AppError(
                409,
                "PROCESSING_REVISION_STALE",
                "指定的学生处理版本已过期，请使用当前版本重新批改",
            )
        processing_revision_id = current_revision_id
        if trigger_source == "automatic" and processing_revision_id:
            existing = self.database.fetchone(
                """SELECT id FROM grading_runs
                   WHERE submission_id=? AND processing_revision_id=?
                     AND trigger_source='automatic'""",
                (submission_id, processing_revision_id),
            )
            if existing:
                return str(existing["id"])
        inputs = self._build_inputs(submission_id, run_id)
        snapshot = {
            "submissionId": submission_id,
            "questions": [
                {
                    "questionId": item.question_id,
                    "questionType": item.question_type,
                    "inputHash": self._question_hash(item),
                    "rubricVersionId": item.rubric_version_id,
                    "frameSetId": item.frame_set_id,
                    "blankConfigVersionId": item.blank_config_version_id,
                    "processingRevisionId": item.processing_revision_id,
                }
                for item in inputs
            ],
        }
        input_hash = self._hash(snapshot)
        timestamp = now_iso()
        max_score = sum((item.max_score for item in inputs), Decimal(0))
        values = (
            run_id,
            submission_id,
            submission["task_id"],
            processing_revision_id,
            trigger_source,
            input_hash,
            json_dumps(snapshot),
            json_dumps(
                {
                    "modelId": self.settings.dashscope_model,
                    "recognitionReviewThreshold": (
                        self.settings.grading_recognition_review_threshold
                    ),
                    "autoConfidenceThreshold": (
                        self.settings.grading_auto_confidence_threshold
                    ),
                    "formulaTimeoutMs": self.settings.grading_formula_timeout_ms,
                }
            ),
            decimal_string(max_score),
            len(inputs),
            timestamp,
            timestamp,
        )
        try:
            self.database.execute(
                """INSERT INTO grading_runs(
                     id,submission_id,task_id,processing_revision_id,trigger_source,
                     status,stage,input_hash,input_snapshot_json,config_snapshot_json,
                     max_score,progress_total,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'queued','queued',?,?,?,?,?,?,?)""",
                values,
            )
        except sqlite3.IntegrityError:
            if trigger_source != "automatic" or not processing_revision_id:
                raise
            existing = self.database.fetchone(
                """SELECT id FROM grading_runs
                   WHERE submission_id=? AND processing_revision_id=?
                     AND trigger_source='automatic'""",
                (submission_id, processing_revision_id),
            )
            if not existing:
                raise
            return str(existing["id"])
        log_event(
            LOGGER,
            logging.INFO,
            "grading_run_created",
            trigger_source=trigger_source,
            total=len(inputs),
        )
        return run_id

    async def run(self, run_id: str) -> None:
        run = self.database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
        if not run:
            return
        if run["status"] in {"completed", "needs_review"}:
            return
        try:
            self._set_run(run_id, "prechecking", "prechecking", retryable=False)
            inputs = self._build_inputs(str(run["submission_id"]), run_id)
            self._set_run(run_id, "grading", "grading", retryable=False, started=True)
            failures = await asyncio.gather(
                *(self._grade_one(run_id, item) for item in inputs),
                return_exceptions=True,
            )
            errors = [error for error in failures if isinstance(error, BaseException)]
            if errors:
                first = errors[0]
                code = first.code if isinstance(first, AppError) else "GRADING_QUESTION_FAILED"
                message = (
                    first.message
                    if isinstance(first, AppError)
                    else "部分题目批改失败，可从已完成题目继续重试"
                )
                self._fail_run(run_id, code, message, retryable=True)
                return
            ready_for_artifacts = self._finish_scoring(run_id, inputs)
            if ready_for_artifacts:
                await self.generate_artifacts(run_id)
        except asyncio.CancelledError:
            self._fail_run(
                run_id,
                "GRADING_RUN_INTERRUPTED",
                "批改任务已中断，可从已保存阶段重试",
                retryable=True,
            )
            raise
        except Exception as error:
            code = error.code if isinstance(error, AppError) else "GRADING_RUN_FAILED"
            message = (
                error.message if isinstance(error, AppError) else "批改运行失败，请检查配置后重试"
            )
            self._fail_run(run_id, code, message, retryable=True)

    async def generate_artifacts(self, run_id: str) -> None:
        try:
            await self.artifact_service.generate(run_id)
        except Exception as error:
            self.artifact_service.mark_failed(run_id, error)

    def _submission(self, submission_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            """SELECT s.*,t.current_question_frame_set_id
               FROM student_submissions s JOIN tasks t ON t.id=s.task_id
               WHERE s.id=?""",
            (submission_id,),
        )
        if not row:
            raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
        if row["status"] != "ready":
            raise AppError(409, "STUDENT_SUBMISSION_NOT_READY", "学生答卷尚未完成识别")
        processing_revision_id = row.get("current_processing_revision_id")
        structural_calculation_review = bool(
            processing_revision_id
            and self._has_structural_calculation_responses(
                str(row["task_id"]),
                submission_id,
                str(processing_revision_id),
            )
        )
        if (
            row.get("question_region_status") != "ready"
            and not structural_calculation_review
        ):
            raise AppError(409, "QUESTION_REGIONS_NOT_READY", "学生答卷题目区域需要先确认")
        if processing_revision_id:
            revision = self.database.fetchone(
                "SELECT * FROM student_processing_revisions WHERE id=? AND submission_id=?",
                (processing_revision_id, submission_id),
            )
            if (
                not revision
                or not revision["is_current"]
                or (
                    revision["status"] not in {"ready", "recognition_needs_review"}
                    and not (
                        revision["status"] == "mapping_needs_review"
                        and structural_calculation_review
                    )
                )
            ):
                raise AppError(
                    409,
                    "PROCESSING_REVISION_NOT_READY",
                    "当前学生处理版本尚未完成或已经过期",
                )
            if revision.get("frame_set_id") != row.get("current_question_frame_set_id"):
                raise AppError(
                    409,
                    "PROCESSING_FRAME_SET_STALE",
                    "学生识别结果依赖的题框版本已经过期，请重新处理",
                )
            row["processing_frame_set_id"] = revision.get("frame_set_id")
        return row

    def _has_structural_calculation_responses(
        self,
        task_id: str,
        submission_id: str,
        processing_revision_id: str,
    ) -> bool:
        """Narrowly admit calculation-only structural responses to T8's safe gate."""

        rows = self.database.fetchall(
            """SELECT q.*,c.question_type AS configured_type,
                      r.id AS response_id,r.raw_recognition_json,r.status AS response_status
               FROM questions q
               LEFT JOIN question_grading_configs c ON c.question_id=q.id
               LEFT JOIN student_responses r ON r.question_id=q.id
                 AND r.submission_id=? AND r.processing_revision_id=?
               WHERE q.task_id=? AND q.is_duplicate=0
               ORDER BY q.sort_order""",
            (submission_id, processing_revision_id, task_id),
        )
        if not rows:
            return False
        for question in rows:
            effective = self._effective_question(question)
            question_type = str(question.get("configured_type") or effective["type"])
            if question_type != QuestionType.CALCULATION.value:
                return False
            if not question.get("response_id") or question.get("response_status") not in {
                "recognized",
                "needs_review",
            }:
                return False
            raw = json_loads(question.get("raw_recognition_json"), {})
            localization = raw.get("localization") if isinstance(raw, dict) else None
            schema_version = (
                localization.get("schemaVersion") if isinstance(localization, dict) else None
            )
            evidence_complete = (
                localization.get("evidenceComplete")
                if isinstance(localization, dict)
                else None
            )
            if (
                not isinstance(localization, dict)
                or not isinstance(schema_version, int)
                or isinstance(schema_version, bool)
                or schema_version != 1
                or not isinstance(evidence_complete, bool)
            ):
                return False
        return True

    @staticmethod
    def _effective_question(row: dict[str, Any]) -> dict[str, Any]:
        override = json_loads(row.get("teacher_override_json"), {})
        return {
            "type": override.get("type", row["question_type"]),
            "score": override.get("score", row["score"]),
            "stem": override.get("stem", row["stem"]),
            "number": override.get("number", row["detected_number"]),
        }

    def _question_rows(
        self,
        task_id: str,
        submission_id: str,
        processing_revision_id: str | None,
    ) -> list[dict[str, Any]]:
        return self.database.fetchall(
            """SELECT q.*,c.question_type AS configured_type,c.max_score AS configured_score,
               c.config_version,c.current_blank_config_version_id,
               c.updated_at AS config_updated_at,
               r.id AS response_id,r.recognized_text,
               r.confidence AS response_confidence,
               r.raw_recognition_json,r.status AS response_status,
               r.processing_revision_id AS response_processing_revision_id,
               r.frame_set_id AS response_frame_set_id,
               r.blank_config_version_id AS response_blank_config_version_id,
               m.status AS match_status,
               m.teacher_answer,m.teacher_explanation,a.answer AS auto_answer,
               a.explanation AS auto_explanation
               FROM questions q
               LEFT JOIN question_grading_configs c ON c.question_id=q.id
               LEFT JOIN student_responses r ON r.question_id=q.id AND r.submission_id=?
                 AND ((? IS NULL AND r.processing_revision_id IS NULL)
                      OR r.processing_revision_id=?)
               LEFT JOIN matches m ON m.question_id=q.id
               LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
               WHERE q.task_id=? AND q.is_duplicate=0 ORDER BY q.sort_order""",
            (submission_id, processing_revision_id, processing_revision_id, task_id),
        )

    def _build_inputs(self, submission_id: str, run_id: str) -> list[QuestionGradingInput]:
        submission = self._submission(submission_id)
        processing_revision_id = (
            str(submission["current_processing_revision_id"])
            if submission.get("current_processing_revision_id")
            else None
        )
        rows = self._question_rows(
            str(submission["task_id"]),
            submission_id,
            processing_revision_id,
        )
        if not rows:
            raise AppError(409, "GRADING_QUESTIONS_EMPTY", "没有可批改的题目")
        inputs: list[QuestionGradingInput] = []
        for row in rows:
            effective = self._effective_question(row)
            if row["confirmation_status"] != "confirmed" or row["match_status"] != "confirmed":
                raise AppError(409, "QUESTION_NOT_CONFIRMED", "所有题目和标准答案必须先确认")
            question_type = str(row["configured_type"] or effective["type"])
            if question_type not in SUPPORTED_TYPES:
                raise AppError(409, "QUESTION_TYPE_UNSUPPORTED", "存在首版不支持的题型")
            raw_score = row["configured_score"] or effective["score"]
            if raw_score is None:
                raise AppError(409, "QUESTION_SCORE_REQUIRED", "所有题目必须配置分值")
            if not row["response_id"]:
                raise AppError(409, "STUDENT_RESPONSE_MISSING", "学生作答区域或识别结果缺失")
            if row["response_status"] not in {"recognized", "needs_review"}:
                raise AppError(409, "STUDENT_RESPONSE_NEEDS_REVIEW", "学生作答识别仍需复核")
            row["recognition_requires_review"] = row["response_status"] == "needs_review"
            row["captured_processing_revision_id"] = processing_revision_id
            row["captured_frame_set_id"] = submission.get("processing_frame_set_id")
            standard_answer = (
                row["teacher_answer"]
                if row["teacher_answer"] is not None
                else row["auto_answer"] or ""
            )
            standard_explanation = (
                row["teacher_explanation"]
                if row["teacher_explanation"] is not None
                else row["auto_explanation"] or ""
            )
            if not standard_answer:
                raise AppError(409, "STANDARD_ANSWER_REQUIRED", "所有题目必须有标准答案")
            normalized_standard = normalize_options(str(standard_answer))
            if question_type == QuestionType.SINGLE_CHOICE.value and (
                len(normalized_standard.options) != 1 or normalized_standard.issues
            ):
                raise AppError(
                    409,
                    "QUESTION_TYPE_ANSWER_CONFLICT",
                    f"第 {effective['number']} 题标为单选题，但标准答案不是唯一选项",
                    {
                        "questionId": row["id"],
                        "questionNumber": effective["number"],
                        "questionType": question_type,
                        "standardAnswer": standard_answer,
                    },
                )
            if question_type == QuestionType.MULTIPLE_CHOICE.value and (
                not normalized_standard.options or normalized_standard.issues
            ):
                raise AppError(
                    409,
                    "QUESTION_TYPE_ANSWER_CONFLICT",
                    f"第 {effective['number']} 题的多选标准答案不是有效选项集合",
                    {
                        "questionId": row["id"],
                        "questionNumber": effective["number"],
                        "questionType": question_type,
                        "standardAnswer": standard_answer,
                    },
                )
            inputs.append(
                self._question_input(
                    run_id,
                    row,
                    QuestionType(question_type),
                    parse_decimal(raw_score),
                    str(effective["stem"]),
                    str(standard_answer),
                    str(standard_explanation),
                )
            )
        return inputs

    def _evidence(
        self,
        response_id: str,
        raw: dict[str, Any],
    ) -> tuple[list[EvidenceRef], bool]:
        regions = self.database.fetchall(
            """SELECT r.*,p.width AS template_width,p.height AS template_height
               FROM student_response_regions r
               LEFT JOIN pages p ON p.id=r.template_page_id
               WHERE r.student_response_id=? ORDER BY r.sort_order""",
            (response_id,),
        )
        segments = {
            int(item.get("region_index", 0)): item
            for item in raw.get("segments", [])
            if isinstance(item, dict) and str(item.get("region_index", "")).isdigit()
        }
        localization_present = "localization" in raw
        localization = raw.get("localization")
        localization_by_id: dict[str, dict[str, Any]] = {}
        evidence_complete = not localization_present
        if (
            isinstance(localization, dict)
            and isinstance(localization.get("schemaVersion"), int)
            and not isinstance(localization.get("schemaVersion"), bool)
            and localization.get("schemaVersion") == 1
            and localization.get("evidenceComplete") is True
            and isinstance(localization.get("evidence"), list)
        ):
            entries = localization["evidence"]
            for value in entries:
                if not isinstance(value, dict):
                    localization_by_id.clear()
                    break
                evidence_id = value.get("evidenceId")
                if (
                    not isinstance(evidence_id, str)
                    or not evidence_id.strip()
                    or evidence_id in localization_by_id
                ):
                    localization_by_id.clear()
                    break
                localization_by_id[evidence_id] = value
            region_ids = {str(region["id"]) for region in regions}
            evidence_complete = (
                len(region_ids) == len(regions)
                and set(localization_by_id) == region_ids
                and all(
                    self._valid_localization_evidence(localization_by_id[str(region["id"])], region)
                    for region in regions
                )
            )
        output: list[EvidenceRef] = []
        for index, region in enumerate(regions, start=1):
            segment = segments.get(index, {})
            localized = localization_by_id.get(str(region["id"]), {})
            template_bbox = self._bbox_from_payload(
                localized.get("templateBboxPx", json_loads(region["template_bbox_json"], {}))
            )
            evidence_kind = localized.get("evidenceKind")
            if evidence_kind not in {
                "located_region",
                "answer_region",
                "blank_search_window",
            }:
                evidence_kind = "legacy" if not localization_present else None
            alignment_revision_id = localized.get("alignmentRevisionId")
            output.append(
                EvidenceRef.model_validate(
                    {
                        "page_id": region["student_page_id"],
                        "region_id": region["id"],
                        "original_bbox": json_loads(region["student_bbox_json"], {}),
                        "cropped_image_path": region["cropped_image_path"],
                        "recognized_text": segment.get("transcription", ""),
                        "template_page_id": region.get("template_page_id"),
                        "template_bbox": (
                            template_bbox.model_dump() if template_bbox is not None else None
                        ),
                        "alignment_revision_id": (
                            alignment_revision_id
                            if isinstance(alignment_revision_id, str)
                            and alignment_revision_id.strip()
                            else None
                        ),
                        "evidence_kind": evidence_kind,
                    }
                )
            )
        return output, evidence_complete

    @classmethod
    def _valid_localization_evidence(
        cls,
        value: dict[str, Any],
        region: dict[str, Any],
    ) -> bool:
        evidence_kind = value.get("evidenceKind")
        if evidence_kind not in {
            "located_region",
            "answer_region",
            "blank_search_window",
        }:
            return False
        required_text = (
            "evidenceId",
            "fragmentKey",
            "templatePageId",
            "studentPageId",
            "alignmentRevisionId",
            "attemptId",
        )
        if any(
            not isinstance(value.get(key), str) or not value[key].strip()
            for key in required_text
        ):
            return False
        if value["evidenceId"] != str(region["id"]):
            return False
        if value["templatePageId"] != str(region.get("template_page_id") or ""):
            return False
        if value["studentPageId"] != str(region["student_page_id"]):
            return False
        batch_index = value.get("batchIndex")
        if not isinstance(batch_index, int) or isinstance(batch_index, bool) or batch_index < 1:
            return False
        if "modelCandidateIndex" not in value:
            return False
        candidate_index = value.get("modelCandidateIndex")
        if evidence_kind in {"located_region", "answer_region"} and (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index < 0
        ):
            return False
        if evidence_kind == "blank_search_window" and candidate_index is not None and (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index < 0
        ):
            return False
        confidence = value.get("confidence")
        if (
            not isinstance(confidence, int | float)
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return False
        if not isinstance(value.get("issues"), list):
            return False
        template_bbox = cls._bbox_from_payload(value.get("templateBboxPx"))
        normalized_bbox = cls._bbox_from_payload(value.get("templateBboxNormalized"))
        student_bbox = cls._bbox_from_payload(value.get("studentBboxPx"))
        persisted_template = cls._bbox_from_payload(
            json_loads(region.get("template_bbox_json"), {})
        )
        persisted_student = cls._bbox_from_payload(json_loads(region.get("student_bbox_json"), {}))
        if any(
            item is None
            for item in (
                template_bbox,
                normalized_bbox,
                student_bbox,
                persisted_template,
                persisted_student,
            )
        ):
            return False
        assert template_bbox is not None
        assert normalized_bbox is not None
        assert student_bbox is not None
        assert persisted_template is not None
        assert persisted_student is not None
        if not cls._bbox_close(template_bbox, persisted_template):
            return False
        if not cls._bbox_close(student_bbox, persisted_student):
            return False
        template_width = int(region.get("template_width") or 0)
        template_height = int(region.get("template_height") or 0)
        if template_width <= 0 or template_height <= 0:
            return False
        expected_normalized = BoundingBox(
            x=template_bbox.x / template_width,
            y=template_bbox.y / template_height,
            width=template_bbox.width / template_width,
            height=template_bbox.height / template_height,
        )
        if (
            normalized_bbox.x + normalized_bbox.width > 1.000001
            or normalized_bbox.y + normalized_bbox.height > 1.000001
            or not cls._bbox_close(normalized_bbox, expected_normalized, tolerance=1e-5)
        ):
            return False
        polygon = value.get("studentPolygonPx")
        if not isinstance(polygon, list) or len(polygon) < 3:
            return False
        for point in polygon:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                return False
            coordinates = (point["x"], point["y"])
            if any(
                not isinstance(coordinate, int | float)
                or isinstance(coordinate, bool)
                or not math.isfinite(float(coordinate))
                for coordinate in coordinates
            ):
                return False
        return True

    @staticmethod
    def _bbox_from_payload(value: object) -> BoundingBox | None:
        if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
            return None
        if any(
            not isinstance(value.get(key), int | float)
            or isinstance(value.get(key), bool)
            or not math.isfinite(float(value[key]))
            for key in ("x", "y", "width", "height")
        ):
            return None
        try:
            return BoundingBox.model_validate(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bbox_close(
        left: BoundingBox,
        right: BoundingBox,
        *,
        tolerance: float = 1e-4,
    ) -> bool:
        return all(
            math.isclose(
                getattr(left, key),
                getattr(right, key),
                rel_tol=tolerance,
                abs_tol=tolerance,
            )
            for key in ("x", "y", "width", "height")
        )

    def _question_input(
        self,
        run_id: str,
        row: dict[str, Any],
        question_type: QuestionType,
        max_score: Decimal,
        stem: str,
        standard_answer: str,
        standard_explanation: str,
    ) -> QuestionGradingInput:
        raw = json_loads(row["raw_recognition_json"], {})
        evidence, evidence_complete = self._evidence(str(row["response_id"]), raw)
        student_response: dict[str, Any] = {
            "answer": row["recognized_text"],
            "recognizedText": row["recognized_text"],
            "isBlank": raw.get("isBlank", False),
            "issues": raw.get("issues", []),
        }
        grading_config: dict[str, Any] = {"configVersion": int(row["config_version"] or 0)}
        answer_snapshot: dict[str, Any] = {"answer": standard_answer}
        rubric_version_id: str | None = None
        blank_config_version_id: str | None = None
        recognition_confidence = row["response_confidence"]
        recognition_requires_review = bool(row.get("recognition_requires_review"))
        if question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_CHOICE}:
            answer_snapshot["options"] = list(normalize_options(standard_answer).options)
        elif question_type is QuestionType.FILL_BLANK:
            blank_config_version_id = (
                str(row["current_blank_config_version_id"])
                if row.get("current_blank_config_version_id")
                else None
            )
            if not blank_config_version_id:
                raise AppError(
                    409,
                    "FILL_BLANK_CONFIG_REQUIRED",
                    "填空题必须先确认逐空配置版本",
                )
            config_version = self.database.fetchone(
                """SELECT * FROM question_blank_config_versions
                   WHERE id=? AND question_id=?""",
                (blank_config_version_id, row["id"]),
            )
            if not config_version or config_version["status"] not in {
                "auto_confirmed",
                "teacher_confirmed",
            }:
                raise AppError(
                    409,
                    "FILL_BLANK_CONFIG_NOT_CONFIRMED",
                    "填空题逐空配置尚未确认",
                )
            captured_frame_set_id = row.get("captured_frame_set_id")
            if not captured_frame_set_id or config_version["frame_set_id"] != captured_frame_set_id:
                raise AppError(
                    409,
                    "FILL_RESPONSE_VERSION_MISMATCH",
                    "逐空配置与当前题框/处理版本不一致",
                )
            blank_rows = self.database.fetchall(
                """SELECT * FROM question_blank_definition_versions
                   WHERE blank_config_version_id=? ORDER BY sort_order""",
                (blank_config_version_id,),
            )
            if not blank_rows:
                raise AppError(409, "FILL_BLANK_CONFIG_REQUIRED", "填空题必须配置每个空")
            blank_responses = self.database.fetchall(
                """SELECT * FROM student_blank_responses
                   WHERE student_response_id=? ORDER BY blank_key""",
                (row["response_id"],),
            )
            expected_keys = [str(blank["blank_key"]) for blank in blank_rows]
            response_by_key = {
                str(response["blank_key"]): response for response in blank_responses
            }
            if len(response_by_key) != len(blank_responses) or set(response_by_key) != set(
                expected_keys
            ):
                raise AppError(
                    409,
                    "FILL_RESPONSE_KEY_MISMATCH",
                    "逐空识别键集合与确认配置不一致，不能按位置猜测",
                    {
                        "expectedKeys": expected_keys,
                        "actualKeys": sorted(response_by_key),
                    },
                )
            if any(
                response["status"] not in {"recognized", "needs_review"}
                for response in blank_responses
            ):
                raise AppError(
                    409,
                    "FILL_RESPONSE_NEEDS_REVIEW",
                    "至少一个空的识别结果仍需教师复核",
                )
            recognition_requires_review = recognition_requires_review or any(
                response["status"] == "needs_review" for response in blank_responses
            )
            captured_processing_revision_id = row.get("captured_processing_revision_id")
            if any(
                response.get("frame_set_id") != captured_frame_set_id
                or response.get("blank_config_version_id") != blank_config_version_id
                or response.get("processing_revision_id") != captured_processing_revision_id
                for response in blank_responses
            ):
                raise AppError(
                    409,
                    "FILL_RESPONSE_VERSION_MISMATCH",
                    "逐空识别结果不是当前题框、配置和处理版本的结果",
                )
            evidence_by_id = {item.region_id: item for item in evidence}
            definitions_by_key = {str(blank["blank_key"]): blank for blank in blank_rows}
            blanks: list[dict[str, Any]] = []
            for blank_key in expected_keys:
                blank = definitions_by_key[blank_key]
                response = response_by_key[blank_key]
                evidence_ids = json_loads(response["evidence_refs_json"], [])
                if not isinstance(evidence_ids, list) or any(
                    not isinstance(item, str) or item not in evidence_by_id
                    for item in evidence_ids
                ):
                    raise AppError(
                        409,
                        "FILL_RESPONSE_EVIDENCE_MISMATCH",
                        "逐空识别引用了当前响应之外的证据",
                    )
                blanks.append(
                    {
                        "blankKey": blank_key,
                        "maxScore": blank["max_score"],
                        "answerKind": blank["answer_kind"],
                        "standardAnswers": json_loads(blank["standard_answers_json"], []),
                        "synonyms": json_loads(blank["synonyms_json"], []),
                        "studentAnswer": str(response["recognized_text"]),
                        "isBlank": bool(response["is_blank"]),
                        "recognitionConfidence": response["confidence"],
                        "evidenceRegionIds": list(dict.fromkeys(evidence_ids)),
                    }
                )
            configured_score = sum(
                (parse_decimal(item["maxScore"]) for item in blanks),
                Decimal(0),
            )
            if configured_score != max_score:
                raise AppError(
                    409,
                    "FILL_SCORE_INCONSISTENCY",
                    "逐空分值合计与题目总分不一致",
                )
            recognition_confidence = min(
                (
                    float(response["confidence"])
                    if response["confidence"] is not None
                    else 0.0
                    for response in blank_responses
                ),
                default=0.0,
            )
            grading_config["configVersion"] = int(config_version["version_number"])
            grading_config["blanks"] = blanks
            answer_snapshot["blanks"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key
                    not in {
                        "studentAnswer",
                        "isBlank",
                        "recognitionConfidence",
                        "evidenceRegionIds",
                    }
                }
                for item in blanks
            ]
            student_response = {
                "summary": row["recognized_text"],
                "notForFillScoring": True,
            }
        elif question_type is QuestionType.CALCULATION:
            answer_snapshot["explanation"] = standard_explanation
            grading_config["scoringPolicyVersion"] = CALCULATION_SCORING_POLICY_VERSION
            version = self.database.fetchone(
                """SELECT * FROM rubric_versions
                   WHERE question_id=? AND status='frozen'
                   ORDER BY version_number DESC LIMIT 1""",
                (row["id"],),
            )
            if not version:
                raise AppError(409, "FROZEN_RUBRIC_REQUIRED", "计算题必须先冻结评分细则")
            if row.get("config_updated_at") and (
                not version.get("frozen_at")
                or str(version["frozen_at"]) < str(row["config_updated_at"])
            ):
                question_number = self._effective_question(row)["number"]
                raise AppError(
                    409,
                    "FROZEN_RUBRIC_REQUIRED",
                    f"第 {question_number} 题的题目或评分配置已更新，"
                    "请重新检查并冻结评分细则",
                    {
                        "questionId": row["id"],
                        "questionNumber": question_number,
                        "rubricVersionId": version["id"],
                    },
                )
            point_rows = self.database.fetchall(
                "SELECT * FROM rubric_points WHERE rubric_version_id=? ORDER BY sort_order",
                (version["id"],),
            )
            dependency_rows = self.database.fetchall(
                """SELECT p.point_key,dp.point_key AS dependency_key
                   FROM rubric_dependencies d
                   JOIN rubric_points p ON p.id=d.point_id
                   JOIN rubric_points dp ON dp.id=d.depends_on_point_id
                   WHERE d.rubric_version_id=?""",
                (version["id"],),
            )
            dependencies: dict[str, list[str]] = {}
            for dependency in dependency_rows:
                dependencies.setdefault(str(dependency["point_key"]), []).append(
                    str(dependency["dependency_key"])
                )
            grading_config["rubricPoints"] = [
                {
                    "key": point["point_key"],
                    "criterion": point["criterion"],
                    "score": point["score"],
                    "order": point["sort_order"],
                    "dependencies": sorted(dependencies.get(str(point["point_key"]), [])),
                }
                for point in point_rows
            ]
            grading_config["rubricPointIds"] = {
                point["point_key"]: point["id"] for point in point_rows
            }
            rubric_version_id = str(version["id"])
        return QuestionGradingInput(
            run_id=run_id,
            question_id=str(row["id"]),
            question_type=question_type,
            max_score=max_score,
            question_content=stem,
            standard_answer_snapshot=answer_snapshot,
            student_response=student_response,
            evidence_regions=evidence,
            recognition_confidence=recognition_confidence,
            grading_config=grading_config,
            rubric_version_id=rubric_version_id,
            frame_set_id=(
                str(row["captured_frame_set_id"])
                if row.get("captured_frame_set_id")
                else None
            ),
            blank_config_version_id=blank_config_version_id,
            processing_revision_id=(
                str(row["captured_processing_revision_id"])
                if row.get("captured_processing_revision_id")
                else None
            ),
            recognition_requires_review=recognition_requires_review,
            recognition_issue_codes=self._recognition_issue_codes(raw),
            recognition_evidence_complete=evidence_complete,
        )

    @staticmethod
    def _recognition_issue_codes(raw: dict[str, Any]) -> list[str]:
        """Extract stable codes without copying answer text into audit metadata."""

        values = raw.get("issues", [])
        if not isinstance(values, list):
            return []
        output: list[str] = []
        for value in values:
            if isinstance(value, str):
                code = value.strip()
            elif isinstance(value, dict):
                code = str(value.get("code") or value.get("reason") or "").strip()
            else:
                code = ""
            if code and code not in output:
                output.append(code[:100])
        return output

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()

    def _question_hash(self, grading_input: QuestionGradingInput) -> str:
        value = grading_input.model_dump(mode="json")
        value.pop("run_id", None)
        return self._hash(value)

    def _load_calculation_evidence_images(
        self,
        grading_input: QuestionGradingInput,
    ) -> tuple[list[CalculationEvidenceImagePair], bool]:
        """Replay captured alignments and create transient template/student pairs."""

        if (
            not grading_input.recognition_evidence_complete
            or not grading_input.processing_revision_id
            or not grading_input.evidence_regions
        ):
            return [], False
        pairs: list[CalculationEvidenceImagePair] = []
        try:
            for evidence in grading_input.evidence_regions:
                pair = self._load_calculation_evidence_pair(grading_input, evidence)
                if pair is None:
                    return [], False
                pairs.append(pair)
        except (AppError, OSError, OverflowError, TypeError, ValueError):
            return [], False
        expected_ids = [item.region_id for item in grading_input.evidence_regions]
        actual_ids = [item.region_id for item in pairs]
        if len(set(expected_ids)) != len(expected_ids) or actual_ids != expected_ids:
            return [], False
        return pairs, True

    def _load_calculation_evidence_pair(
        self,
        grading_input: QuestionGradingInput,
        evidence: EvidenceRef,
    ) -> CalculationEvidenceImagePair | None:
        if (
            evidence.template_page_id is None
            or evidence.template_bbox is None
            or evidence.evidence_kind is None
            or grading_input.processing_revision_id is None
        ):
            return None
        parameters: tuple[Any, ...]
        alignment_clause: str
        if evidence.alignment_revision_id:
            alignment_clause = "a.id=?"
            parameters = (
                evidence.alignment_revision_id,
                grading_input.processing_revision_id,
                evidence.page_id,
                evidence.template_page_id,
            )
        elif evidence.evidence_kind == "legacy":
            alignment_clause = "a.is_current=1"
            parameters = (
                grading_input.processing_revision_id,
                evidence.page_id,
                evidence.template_page_id,
            )
        else:
            return None
        row = self.database.fetchone(
            f"""SELECT a.*,sp.original_image_path,
                       sp.width AS student_width,sp.height AS student_height,
                       p.image_path AS template_image_path,
                       p.width AS template_width,p.height AS template_height,
                       p.page_number AS template_page_number
                FROM student_page_alignment_revisions a
                JOIN student_pages sp ON sp.id=a.student_page_id
                JOIN pages p ON p.id=a.template_page_id
                WHERE {alignment_clause} AND a.processing_revision_id=?
                  AND a.student_page_id=? AND a.template_page_id=?""",
            parameters,
        )
        if (
            not row
            or row.get("status") not in {"aligned", "low_quality"}
            or not row.get("transform_json")
            or not row.get("original_image_path")
            or not row.get("template_image_path")
        ):
            return None
        template_width = int(row["template_width"])
        template_height = int(row["template_height"])
        student_width = int(row["student_width"])
        student_height = int(row["student_height"])
        bbox = evidence.template_bbox
        if (
            bbox.x + bbox.width > template_width + 1e-6
            or bbox.y + bbox.height > template_height + 1e-6
        ):
            return None
        transform_rows = json_loads(row["transform_json"], None)
        if not isinstance(transform_rows, list):
            return None
        score = float(row.get("quality") or 0.0)
        reliable = row.get("status") == "aligned"
        if evidence.evidence_kind != "legacy" and (
            not reliable
            or not math.isfinite(score)
            or score < self.settings.mapping_min_alignment_score
        ):
            return None
        alignment = AlignmentResult.create(
            Homography.from_rows(transform_rows),
            PageSize(template_width, template_height),
            PageSize(student_width, student_height),
            AlignmentQuality(
                method=str(row.get("method") or "captured_alignment"),
                score=score,
                matched_features=0,
                inliers=0,
                inlier_ratio=0.0,
                mean_reprojection_error_px=None,
                template_feature_coverage=1.0,
                student_feature_coverage=1.0,
                visible_template_ratio=1.0,
                is_reliable=reliable,
                warnings=() if reliable else ("low_quality_alignment",),
            ),
        )
        student_path = resolve_data_path(self.settings, str(row["original_image_path"]))
        template_path = resolve_data_path(self.settings, str(row["template_image_path"]))
        with Image.open(student_path) as opened:
            if opened.size != (student_width, student_height):
                return None
        region = AnswerRegion.rectangle(
            grading_input.question_id,
            evidence.region_id,
            int(row["template_page_number"]),
            bbox.x,
            bbox.y,
            bbox.x + bbox.width,
            bbox.y + bbox.height,
        )
        mapped_polygon = alignment.template_to_student.map_polygon(
            region.template_polygon
        )
        student_bounds = Bounds(0.0, 0.0, float(student_width), float(student_height))
        mapped_area = mapped_polygon.area
        visible_ratio = polygon_visible_ratio(mapped_polygon, student_bounds)
        out_of_bounds_ratio = polygon_out_of_bounds_ratio(
            mapped_polygon,
            student_bounds,
        )
        if (
            not math.isfinite(mapped_area)
            or mapped_area < self.settings.mapping_min_polygon_area_px
            or visible_ratio + 1e-9 < self.settings.mapping_min_visible_ratio
            or out_of_bounds_ratio
            > self.settings.mapping_max_out_of_bounds_ratio + 1e-9
        ):
            return None
        extracted = extract_answer_regions(
            student_path,
            [region],
            alignment,
            padding=0,
        )[0]
        with Image.open(template_path) as opened:
            if opened.size != (template_width, template_height):
                return None
            template_crop = opened.convert("RGB").crop(extracted.template_crop_box)
        student_crop = extracted.image.convert("RGB")
        if template_crop.size != student_crop.size or min(template_crop.size) <= 0:
            return None
        return CalculationEvidenceImagePair(
            region_id=evidence.region_id,
            evidence_kind=evidence.evidence_kind,
            template_image=self._jpeg_bytes(template_crop),
            student_image=self._jpeg_bytes(student_crop),
        )

    @staticmethod
    def _jpeg_bytes(image: Image.Image) -> bytes:
        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        return output.getvalue()

    async def _grade_one(self, run_id: str, grading_input: QuestionGradingInput) -> None:
        calculation_evidence_images: list[CalculationEvidenceImagePair] | None = None
        if grading_input.question_type is QuestionType.CALCULATION:
            calculation_evidence_images, evidence_complete = await asyncio.to_thread(
                self._load_calculation_evidence_images,
                grading_input,
            )
            if not evidence_complete:
                grading_input = grading_input.model_copy(
                    update={"recognition_evidence_complete": False}
                )
        input_hash = self._question_hash(grading_input)
        existing = self.database.fetchone(
            """SELECT * FROM grading_question_results
               WHERE grading_run_id=? AND question_id=?""",
            (run_id, grading_input.question_id),
        )
        if (
            existing
            and existing["input_hash"] == input_hash
            and existing["status"]
            in {
                "final",
                "needs_review",
            }
        ):
            self._increment_progress(run_id)
            return
        async with self._question_semaphore:
            try:
                result = await grade_question(
                    grading_input,
                    self.model_client,
                    recognition_threshold=self.settings.grading_recognition_review_threshold,
                    model_confidence_threshold=(self.settings.grading_auto_confidence_threshold),
                    formula_timeout_ms=self.settings.grading_formula_timeout_ms,
                    calculation_evidence_images=calculation_evidence_images,
                )
            except (ValidationError, ValueError) as error:
                rejected_observations: list[ToolObservation] = []
                if isinstance(error, CalculationModelOutputError):
                    raw_content_limit = 32_000
                    rejected_observations.append(
                        ToolObservation(
                            tool="calculation_model_output_rejected",
                            status="rejected",
                            detail="模型返回未通过计算题结构校验",
                            payload={
                                "rawModelContent": error.raw_model_content[
                                    :raw_content_limit
                                ],
                                "rawModelContentTruncated": (
                                    len(error.raw_model_content) > raw_content_limit
                                ),
                                "validationErrors": error.validation_errors,
                                "usage": error.usage,
                                "promptVersion": CALCULATION_JUDGE_PROMPT_VERSION,
                            },
                            tool_version=self.model_client.settings.dashscope_model,
                        )
                    )
                result = QuestionGradingResult(
                    status=GradingStatus.NEEDS_REVIEW,
                    raw_score=Decimal(0),
                    final_score=Decimal(0),
                    max_score=grading_input.max_score,
                    evidence_refs=grading_input.evidence_regions,
                    tool_observations=rejected_observations,
                    review_reasons=[ReviewReason.INVALID_MODEL_OUTPUT],
                )
                invalid_detail = self._invalid_model_error_detail(error)
            except ModelError:
                raise
            else:
                invalid_detail = ""
        audit_reasons = [issue.reason for issue in audit_question(result)]
        if grading_input.recognition_requires_review:
            audit_reasons.append(ReviewReason.LOW_RECOGNITION_CONFIDENCE)
        reasons = list(dict.fromkeys([*result.review_reasons, *audit_reasons]))
        if reasons and result.status is not GradingStatus.NEEDS_REVIEW:
            result = result.model_copy(
                update={"status": GradingStatus.NEEDS_REVIEW, "review_reasons": reasons}
            )
        elif reasons != result.review_reasons:
            result = result.model_copy(update={"review_reasons": reasons})
        self._save_result(grading_input, input_hash, result, invalid_detail)
        self._increment_progress(run_id)

    @staticmethod
    def _invalid_model_error_detail(error: ValidationError | ValueError) -> str:
        if isinstance(error, CalculationModelOutputError):
            rejected = error.validation_errors[0] if error.validation_errors else {}
            path = str(rejected.get("path", "model_output"))
            message = str(rejected.get("message", "结构校验失败"))
            return f"CalculationModelOutputError: {path}: {message}"[:500]
        if isinstance(error, ValidationError):
            errors = error.errors(include_url=False)
            if errors:
                first = errors[0]
                path = ".".join(str(part) for part in first.get("loc", ()))
                return f"ValidationError: {path}: {first.get('msg', '')}"[:500]
        message = str(error).strip()
        return f"{type(error).__name__}: {message}"[:500]

    def _save_result(
        self,
        grading_input: QuestionGradingInput,
        input_hash: str,
        result: QuestionGradingResult,
        invalid_detail: str,
    ) -> None:
        timestamp = now_iso()
        existing = self.database.fetchone(
            """SELECT id,result_revision FROM grading_question_results
               WHERE grading_run_id=? AND question_id=?""",
            (grading_input.run_id, grading_input.question_id),
        )
        result_id = str(existing["id"]) if existing else uuid.uuid4().hex
        revision = int(existing["result_revision"] if existing else 0) + 1
        status = "needs_review" if result.status is GradingStatus.NEEDS_REVIEW else "final"
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO grading_question_results(
                     id,grading_run_id,question_id,student_response_id,rubric_version_id,
                     input_hash,question_type,status,raw_score,final_score,max_score,
                     answer_snapshot_json,grading_config_snapshot_json,decisions_json,
                     evidence_refs_json,error_locations_json,tool_observations_json,
                     review_reasons_json,attempt_count,result_revision,error_code,error_message,
                     created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
                   ON CONFLICT(grading_run_id,question_id) DO UPDATE SET
                     rubric_version_id=excluded.rubric_version_id,input_hash=excluded.input_hash,
                     question_type=excluded.question_type,status=excluded.status,
                     raw_score=excluded.raw_score,final_score=excluded.final_score,
                     max_score=excluded.max_score,answer_snapshot_json=excluded.answer_snapshot_json,
                     grading_config_snapshot_json=excluded.grading_config_snapshot_json,
                     decisions_json=excluded.decisions_json,
                     evidence_refs_json=excluded.evidence_refs_json,
                     error_locations_json=excluded.error_locations_json,
                     tool_observations_json=excluded.tool_observations_json,
                     review_reasons_json=excluded.review_reasons_json,
                     attempt_count=grading_question_results.attempt_count+1,
                     result_revision=excluded.result_revision,error_code=excluded.error_code,
                     error_message=excluded.error_message,updated_at=excluded.updated_at""",
                (
                    result_id,
                    grading_input.run_id,
                    grading_input.question_id,
                    self._response_id(grading_input.question_id, grading_input.run_id),
                    grading_input.rubric_version_id,
                    input_hash,
                    grading_input.question_type,
                    status,
                    str(result.raw_score),
                    decimal_string(result.final_score),
                    decimal_string(result.max_score),
                    json_dumps(grading_input.standard_answer_snapshot),
                    json_dumps(grading_input.grading_config),
                    json_dumps([item.model_dump(mode="json") for item in result.decisions]),
                    json_dumps([item.model_dump(mode="json") for item in result.evidence_refs]),
                    json_dumps([item.model_dump(mode="json") for item in result.error_locations]),
                    json_dumps([item.model_dump(mode="json") for item in result.tool_observations]),
                    json_dumps([item.value for item in result.review_reasons]),
                    revision,
                    "INVALID_MODEL_OUTPUT" if invalid_detail else None,
                    invalid_detail or None,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "DELETE FROM grading_blank_results WHERE grading_question_result_id=?",
                (result_id,),
            )
            connection.execute(
                "DELETE FROM grading_point_results WHERE grading_question_result_id=?",
                (result_id,),
            )
            self._save_breakdown(connection, result_id, grading_input, result, timestamp)
            connection.execute(
                """DELETE FROM grading_review_items
                   WHERE grading_question_result_id=? AND status='open'""",
                (result_id,),
            )
            for reason in result.review_reasons:
                connection.execute(
                    """INSERT INTO grading_review_items(
                         id,grading_run_id,grading_question_result_id,reason,context_json,
                         created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        grading_input.run_id,
                        result_id,
                        reason.value,
                        json_dumps({"questionId": grading_input.question_id}),
                        timestamp,
                        timestamp,
                    ),
                )
            connection.execute(
                """INSERT INTO grading_events(
                     grading_run_id,grading_question_result_id,event_type,actor,payload_json,
                     created_at
                   ) VALUES(?,?,'question_graded','system',?,?)""",
                (
                    grading_input.run_id,
                    result_id,
                    json_dumps(
                        {
                            "status": status,
                            "score": decimal_string(result.final_score),
                            "reviewReasons": [item.value for item in result.review_reasons],
                        }
                    ),
                    timestamp,
                ),
            )

    def _response_id(self, question_id: str, run_id: str) -> str | None:
        row = self.database.fetchone(
            """SELECT r.id FROM student_responses r
               JOIN grading_runs g ON g.submission_id=r.submission_id
               WHERE g.id=? AND r.question_id=?""",
            (run_id, question_id),
        )
        return str(row["id"]) if row else None

    def _save_breakdown(
        self,
        connection: Any,
        result_id: str,
        grading_input: QuestionGradingInput,
        result: QuestionGradingResult,
        timestamp: str,
    ) -> None:
        decision_by_key = {item.key: item for item in result.decisions}
        if grading_input.question_type is QuestionType.FILL_BLANK:
            for blank in grading_input.grading_config.get("blanks", []):
                decision = decision_by_key.get(str(blank["blankKey"]))
                if not decision:
                    continue
                status = (
                    "correct"
                    if decision.status.value == "correct"
                    else "needs_review"
                    if decision.status.value == "unable"
                    else "incorrect"
                )
                observations = [
                    item.model_dump(mode="json")
                    for item in result.tool_observations
                    if item.payload.get("blankKey") == blank["blankKey"]
                ]
                connection.execute(
                    """INSERT INTO grading_blank_results(
                         id,grading_question_result_id,blank_definition_id,blank_key,status,
                         recognized_answer,score,max_score,exact_match_json,model_result_json,
                         verifier_result_json,final_decision_json,evidence_refs_json,
                         review_reasons_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        result_id,
                        self._blank_id(grading_input.question_id, str(blank["blankKey"])),
                        blank["blankKey"],
                        status,
                        blank.get("studentAnswer", ""),
                        decimal_string(decision.score),
                        decimal_string(decision.max_score),
                        json_dumps(
                            [item for item in observations if item["tool"] == "fill_exact_match"]
                        ),
                        json_dumps(
                            [item for item in observations if item["tool"] == "fill_semantic_model"]
                        ),
                        json_dumps(
                            [item for item in observations if item["tool"].endswith("_verifier")]
                        ),
                        json_dumps(decision.model_dump(mode="json")),
                        json_dumps(
                            [item.model_dump(mode="json") for item in decision.evidence_refs]
                        ),
                        json_dumps([item.value for item in result.review_reasons]),
                        timestamp,
                        timestamp,
                    ),
                )
        if grading_input.question_type is QuestionType.CALCULATION:
            direct_points: dict[str, dict[str, Any]] = {}
            for observation in result.tool_observations:
                if observation.tool == "calculation_point_model":
                    direct_points = {
                        str(item["pointKey"]): item
                        for item in observation.payload.get("directPoints", [])
                    }
            point_ids = grading_input.grading_config.get("rubricPointIds", {})
            for decision in result.decisions:
                direct = direct_points.get(decision.key, {})
                direct_status = str(direct.get("status", "unable"))
                if direct_status == "satisfied":
                    direct_score = decision.max_score
                elif direct_status == "partial":
                    direct_score = partial_credit_score(decision.max_score)
                else:
                    direct_score = Decimal(0)
                connection.execute(
                    """INSERT INTO grading_point_results(
                         id,grading_question_result_id,rubric_point_id,point_key,direct_status,
                         final_status,direct_score,final_score,max_score,blocked_by,
                         evidence_refs_json,reason,confidence,model_result_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        result_id,
                        point_ids[decision.key],
                        decision.key,
                        direct_status,
                        decision.status.value,
                        decimal_string(direct_score),
                        decimal_string(decision.score),
                        decimal_string(decision.max_score),
                        decision.blocked_by,
                        json_dumps(
                            [item.model_dump(mode="json") for item in decision.evidence_refs]
                        ),
                        decision.reason,
                        direct.get("confidence"),
                        json_dumps(direct),
                        timestamp,
                        timestamp,
                    ),
                )

    def _blank_id(self, question_id: str, blank_key: str) -> str | None:
        row = self.database.fetchone(
            """SELECT id FROM question_blank_definitions
               WHERE question_id=? AND blank_key=?""",
            (question_id, blank_key),
        )
        return str(row["id"]) if row else None

    def _increment_progress(self, run_id: str) -> None:
        self.database.execute(
            """UPDATE grading_runs SET progress_current=MIN(progress_current+1,progress_total),
               updated_at=? WHERE id=?""",
            (now_iso(), run_id),
        )
        progress = self.database.fetchone(
            "SELECT progress_current,progress_total FROM grading_runs WHERE id=?",
            (run_id,),
        )
        if progress:
            with bind_log_context(grading_run_id=run_id):
                log_event(
                    LOGGER,
                    logging.INFO,
                    "grading_progress",
                    current=progress["progress_current"],
                    total=progress["progress_total"],
                )

    def _result_from_row(self, row: dict[str, Any]) -> QuestionGradingResult:
        return QuestionGradingResult.model_validate(
            {
                "status": ("needs_review" if row["status"] == "needs_review" else "graded"),
                "raw_score": row["raw_score"] or "0",
                "final_score": row["final_score"] or "0",
                "max_score": row["max_score"],
                "decisions": json_loads(row["decisions_json"], []),
                "evidence_refs": json_loads(row["evidence_refs_json"], []),
                "error_locations": json_loads(row["error_locations_json"], []),
                "tool_observations": json_loads(row["tool_observations_json"], []),
                "review_reasons": json_loads(row["review_reasons_json"], []),
            }
        )

    def _finish_scoring(self, run_id: str, inputs: list[QuestionGradingInput]) -> bool:
        self._set_run(run_id, "auditing", "auditing", retryable=False)
        rows = self.database.fetchall(
            "SELECT * FROM grading_question_results WHERE grading_run_id=?",
            (run_id,),
        )
        results = [self._result_from_row(row) for row in rows]
        open_rows = self.database.fetchall(
            """SELECT i.reason,r.question_type,r.status,r.review_reasons_json,
                      r.decisions_json,r.error_code
               FROM grading_review_items i
               JOIN grading_question_results r ON r.id=i.grading_question_result_id
               WHERE i.grading_run_id=? AND i.status='open'""",
            (run_id,),
        )
        open_count = len(open_rows)
        has_unresolved_score_placeholder = any(
            row["question_type"] == QuestionType.CALCULATION.value
            and row["status"] == "needs_review"
            and (
                (
                    row["error_code"] == ReviewReason.INVALID_MODEL_OUTPUT.value
                    and not json_loads(row["decisions_json"], [])
                )
                or (
                    row["reason"] == ReviewReason.MISSING_EVIDENCE.value
                )
            )
            for row in open_rows
        )
        max_score = sum((item.max_score for item in inputs), Decimal(0))
        issues = audit_exam(
            results,
            expected_question_count=len(inputs),
            expected_max_score=max_score,
            open_review_count=open_count,
        )
        total_score = sum((item.final_score for item in results), Decimal(0))
        stored_total_score = (
            None if has_unresolved_score_placeholder else decimal_string(total_score)
        )
        timestamp = now_iso()
        if issues or open_count:
            self.database.execute(
                """UPDATE grading_runs SET status='needs_review',stage='needs_review',
                   total_score=?,max_score=?,open_review_count=?,last_successful_stage='auditing',
                   retryable=0,updated_at=? WHERE id=?""",
                (
                    stored_total_score,
                    decimal_string(max_score),
                    open_count,
                    timestamp,
                    run_id,
                ),
            )
            return False
        self.database.execute(
            """UPDATE grading_runs SET status='generating_annotation',
               stage='generating_annotation',total_score=?,max_score=?,open_review_count=0,
               result_revision=result_revision+1,last_successful_stage='auditing',retryable=0,
               updated_at=? WHERE id=?""",
            (
                decimal_string(total_score),
                decimal_string(max_score),
                timestamp,
                run_id,
            ),
        )
        return True

    def _set_run(
        self,
        run_id: str,
        status: str,
        stage: str,
        *,
        retryable: bool,
        started: bool = False,
    ) -> None:
        timestamp = now_iso()
        self.database.execute(
            """UPDATE grading_runs SET status=?,stage=?,retryable=?,error_code=NULL,
               error_message=NULL,started_at=CASE WHEN ? THEN COALESCE(started_at,?)
               ELSE started_at END,updated_at=? WHERE id=?""",
            (status, stage, int(retryable), int(started), timestamp, timestamp, run_id),
        )
        with bind_log_context(grading_run_id=run_id):
            log_event(
                LOGGER,
                logging.INFO,
                "grading_stage_changed",
                stage=stage,
                status=status,
            )

    def _fail_run(self, run_id: str, code: str, message: str, *, retryable: bool) -> None:
        self.database.execute(
            """UPDATE grading_runs SET status='failed',stage='failed',error_code=?,
               error_message=?,retryable=?,attempt_count=attempt_count+1,updated_at=?
               WHERE id=?""",
            (code, message, int(retryable), now_iso(), run_id),
        )
        with bind_log_context(grading_run_id=run_id):
            log_event(
                LOGGER,
                logging.ERROR,
                "grading_run_failed",
                status="failed",
                error_code=code,
            )
