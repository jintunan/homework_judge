from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
import uuid
from typing import Any

from PIL import Image

from ..alignment import (
    AlignmentQuality,
    AlignmentResult,
    AnswerRegion,
    Bounds,
    Homography,
    PageSize,
    align_pages,
    extract_answer_regions,
)
from ..alignment.geometry import polygon_out_of_bounds_ratio, polygon_visible_ratio
from ..alignment.models import FramePageAlignment
from ..alignment.regions import map_confirmed_frame_set
from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..files.renderer import prepare_document_pages
from ..files.storage import resolve_data_path
from ..grading.blank_config_confirmation import ensure_task_fill_blank_configs
from ..observability import log_event
from ..question_frames.service import QuestionFrameService
from ..recognition.calculation_localization import (
    CalculationLocalizationBatchResult,
    CalculationPageBinding,
    CalculationRecognitionBatchResult,
    CalculationRegionTranscription,
    CalculationSearchFragment,
    CalculationWindowStatus,
    LocalizedCalculationRegion,
    aggregate_calculation_localization_batches,
    build_calculation_search_plan,
    failed_calculation_localization_batch,
)
from ..recognition.prompts import (
    CALCULATION_LOCALIZATION_PROMPT_VERSION,
    CALCULATION_RECOGNITION_PROMPT_VERSION,
    KEYED_FILL_RESPONSE_PROMPT_VERSION,
)
from ..recognition.service import RecognitionService

type AlignmentMap = dict[
    int,
    tuple[dict[str, Any], dict[str, Any], AlignmentResult],
]

LOGGER = logging.getLogger("homework_judge.student_recognition")


class StudentPipeline:
    """Align a fixed-layout student submission and transcribe each answer area."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        recognition: RecognitionService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.recognition = recognition
        self.question_frames = QuestionFrameService(database)

    async def run(self, submission_id: str) -> None:
        submission = self.database.fetchone(
            "SELECT * FROM student_submissions WHERE id=?",
            (submission_id,),
        )
        if not submission:
            return
        task_id = str(submission["task_id"])
        processing_revision_id: str | None = None
        try:
            frame_gate = self.question_frames.require_processing_ready(task_id)
            frame_set_id = str(frame_gate["frameSetId"])
            ensure_task_fill_blank_configs(
                self.database,
                task_id,
                "system:student_processing",
                "student_processing",
                allow_partial=True,
            )
            frame_set = self.question_frames.get_frame_set(frame_set_id)
            questions = self._questions_from_frame_set(task_id, frame_set_id)
            template_pages = self._template_pages(task_id)
            blank_config_ids, blank_configs = self._blank_config_snapshot(
                questions,
                frame_set_id,
            )
            input_hash = self._processing_input_hash(
                submission,
                frame_set,
                template_pages,
                questions,
                blank_config_ids,
            )
            processing_revision_id = self._begin_processing_revision(
                submission_id,
                task_id,
                frame_set_id,
                input_hash,
            )
            student_pages, pages_are_new = await self._student_pages(submission)
            if len(student_pages) > len(template_pages):
                raise AppError(
                    422,
                    "STUDENT_PAGE_COUNT_EXCEEDS_TEMPLATE",
                    "学生答卷图片数多于空白试卷页数，请检查是否重复上传",
                    {"templatePages": len(template_pages), "studentPages": len(student_pages)},
                )

            alignments = self._align_pages(template_pages, student_pages)
            self._assign_alignment_revisions(processing_revision_id, alignments)
            missing_template_pages = sorted(
                {int(page["page_number"]) for page in template_pages} - set(alignments)
            )
            if not self._advance_processing_revision(processing_revision_id, "recognizing"):
                return
            question_regions, mapping_blockers = self._map_question_regions(
                frame_set,
                alignments,
            )
            mapping_ready = self._mapping_ready(
                questions,
                question_regions,
                missing_template_pages,
            ) and not mapping_blockers
            responses = await self._recognize_responses(
                questions,
                alignments,
                blank_configs,
                uploaded_student_page_numbers=[
                    int(page["page_number"]) for page in student_pages
                ],
                allow_non_calculation=mapping_ready,
            )
            self._commit_results(
                processing_revision_id,
                submission_id,
                task_id,
                frame_set_id,
                str(frame_set["contentHash"]),
                blank_config_ids,
                pages_are_new,
                student_pages,
                responses,
                question_regions,
                missing_template_pages,
                mapping_ready,
                mapping_blockers,
            )
        except asyncio.CancelledError:
            if processing_revision_id:
                self._fail_processing_revision(
                    processing_revision_id,
                    "STUDENT_RUN_INTERRUPTED",
                    "学生答卷处理已中断，请重新运行",
                )
            else:
                self._set_submission(
                    submission_id,
                    "failed",
                    "STUDENT_RUN_INTERRUPTED",
                    "学生答卷处理已中断，请重新运行",
                )
                self._set_question_region_state(
                    submission_id,
                    "failed",
                    "STUDENT_RUN_INTERRUPTED",
                    "题目区域处理已中断，请重新运行",
                )
            raise
        except Exception as error:
            code = error.code if isinstance(error, AppError) else "STUDENT_PROCESSING_FAILED"
            message = (
                error.message
                if isinstance(error, AppError)
                else "学生答卷处理失败，请检查答卷页面和模板区域"
            )
            if processing_revision_id:
                self._fail_processing_revision(processing_revision_id, code, message)
            else:
                self._set_submission(submission_id, "failed", code, message)
                self._set_question_region_state(submission_id, "failed", code, message)

    async def resume_current_recognition(self, submission_id: str) -> None:
        """Continue a teacher-aligned processing revision without aligning pages again."""

        submission = self.database.fetchone(
            "SELECT * FROM student_submissions WHERE id=?",
            (submission_id,),
        )
        if not submission or not submission.get("current_processing_revision_id"):
            return
        processing_revision_id = str(submission["current_processing_revision_id"])
        revision = self.database.fetchone(
            "SELECT * FROM student_processing_revisions WHERE id=? AND submission_id=?",
            (processing_revision_id, submission_id),
        )
        if (
            not revision
            or revision["source"] != "teacher"
            or revision["status"] != "recognizing"
            or not revision["is_current"]
        ):
            return
        task_id = str(submission["task_id"])
        try:
            frame_gate = self.question_frames.require_processing_ready(task_id)
            frame_set_id = str(frame_gate["frameSetId"])
            if str(revision.get("frame_set_id") or "") != frame_set_id:
                raise AppError(
                    409,
                    "FRAME_SET_SUPERSEDED",
                    "人工配准修订绑定的题框版本已变化，请重新配准",
                )
            frame_set = self.question_frames.get_frame_set(frame_set_id)
            questions = self._questions_from_frame_set(task_id, frame_set_id)
            template_pages = self._template_pages(task_id)
            blank_config_ids, blank_configs = self._blank_config_snapshot(
                questions,
                frame_set_id,
            )
            alignments, alignment_integrity = self._persisted_alignment_map(
                processing_revision_id,
                template_pages,
            )
            persisted_regions = self.database.fetchall(
                """SELECT question_id,frame_region_id,status,alignment_revision_id
                   FROM student_question_regions WHERE processing_revision_id=?
                   ORDER BY question_id,sort_order,id""",
                (processing_revision_id,),
            )
            _preview_regions, mapping_blockers = self._map_question_regions(
                frame_set,
                alignments,
            )
            missing_template_pages = sorted(
                {int(page["page_number"]) for page in template_pages} - set(alignments)
            )
            mapping_ready = (
                alignment_integrity
                and all(
                    alignment.quality.is_reliable
                    for _template, _student, alignment in alignments.values()
                )
                and self._mapping_ready(
                    questions,
                    persisted_regions,
                    missing_template_pages,
                )
                and not mapping_blockers
            )
            responses = await self._recognize_responses(
                questions,
                alignments,
                blank_configs,
                uploaded_student_page_numbers=[
                    int(row["page_number"])
                    for row in self.database.fetchall(
                        "SELECT page_number FROM student_pages "
                        "WHERE submission_id=? ORDER BY page_number",
                        (submission_id,),
                    )
                ],
                allow_non_calculation=mapping_ready,
            )
            self._commit_existing_revision_recognition(
                processing_revision_id,
                submission_id,
                task_id,
                frame_set_id,
                str(frame_set["contentHash"]),
                blank_config_ids,
                responses,
                persisted_regions,
                {
                    str(student_page["alignment_revision_id"])
                    for _template_page, student_page, _alignment in alignments.values()
                },
                missing_template_pages,
                mapping_ready,
                mapping_blockers,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = error.code if isinstance(error, AppError) else "STUDENT_PROCESSING_FAILED"
            message = (
                error.message
                if isinstance(error, AppError)
                else "人工配准后的学生答案识别失败，请重试"
            )
            self._fail_processing_revision(processing_revision_id, code, message)

    def _set_submission(
        self,
        submission_id: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.database.execute(
            """UPDATE student_submissions SET status=?,error_code=?,error_message=?,updated_at=?
               WHERE id=?""",
            (status, error_code, error_message, now_iso(), submission_id),
        )

    def _set_question_region_state(
        self,
        submission_id: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.database.execute(
            """UPDATE student_submissions SET question_region_status=?,
               question_region_error_code=?,question_region_error_message=?,updated_at=?
               WHERE id=?""",
            (status, error_code, error_message, now_iso(), submission_id),
        )

    def _blank_config_snapshot(
        self,
        questions: list[dict[str, Any]],
        frame_set_id: str,
    ) -> tuple[dict[str, str | None], dict[str, dict[str, Any]]]:
        question_ids = [str(question["id"]) for question in questions]
        if not question_ids:
            return {}, {}
        fill_question_ids = {
            str(question["id"])
            for question in questions
            if str(question.get("type")) == "fill_blank"
        }
        placeholders = ",".join("?" for _question_id in question_ids)
        rows = self.database.fetchall(
            f"""SELECT c.question_id,c.current_blank_config_version_id,
                       v.question_id AS version_question_id,v.frame_set_id,v.status,
                       v.blockers_json,v.confirmed_at
                FROM question_grading_configs c
                LEFT JOIN question_blank_config_versions v
                  ON v.id=c.current_blank_config_version_id
                WHERE c.question_id IN ({placeholders})""",
            tuple(question_ids),
        )
        by_question = {str(row["question_id"]): row for row in rows}
        captured_ids = {
            question_id: (
                str(by_question[question_id]["current_blank_config_version_id"])
                if question_id in by_question
                and by_question[question_id].get("current_blank_config_version_id")
                else None
            )
            for question_id in question_ids
        }
        configs: dict[str, dict[str, Any]] = {}
        for question_id in fill_question_ids:
            row = by_question.get(question_id)
            version_id = captured_ids[question_id]
            issue = "fill_blank_config_missing"
            definitions: list[dict[str, Any]] = []
            if version_id and row:
                issue = "fill_blank_config_not_confirmed"
                if (
                    str(row.get("version_question_id") or "") == question_id
                    and str(row.get("frame_set_id") or "") == frame_set_id
                    and row.get("status") in {"auto_confirmed", "teacher_confirmed"}
                    and row.get("confirmed_at")
                    and not json_loads(row.get("blockers_json"), [])
                ):
                    definition_rows = self.database.fetchall(
                        """SELECT id,blank_key,sort_order,template_page_id,page_number,
                                  coordinate_space,x,y,width,height,anchor_json
                           FROM question_blank_definition_versions
                           WHERE blank_config_version_id=? ORDER BY sort_order,id""",
                        (version_id,),
                    )
                    expected_keys = [f"B{index}" for index in range(1, len(definition_rows) + 1)]
                    actual_keys = [str(item["blank_key"]) for item in definition_rows]
                    actual_orders = [int(item["sort_order"]) for item in definition_rows]
                    if (
                        definition_rows
                        and actual_keys == expected_keys
                        and actual_orders == list(range(len(definition_rows)))
                    ):
                        issue = ""
                        definitions = [
                            {
                                "id": str(item["id"]),
                                "blankKey": str(item["blank_key"]),
                                "anchor": self._blank_anchor(item),
                            }
                            for item in definition_rows
                        ]
                    else:
                        issue = "fill_blank_config_keys_invalid"
                elif str(row.get("frame_set_id") or "") != frame_set_id:
                    issue = "fill_blank_config_frame_mismatch"
            configs[question_id] = {
                "id": version_id,
                "confirmed": not issue,
                "issue": issue,
                "definitions": definitions,
            }
        return captured_ids, configs

    @staticmethod
    def _blank_anchor(row: dict[str, Any]) -> dict[str, Any]:
        anchor: dict[str, Any] = {}
        raw_anchor = json_loads(row.get("anchor_json"), {})
        if isinstance(raw_anchor, dict) and raw_anchor.get("fragmentKey"):
            anchor["fragmentKey"] = str(raw_anchor["fragmentKey"])
        if row.get("template_page_id"):
            anchor["templatePageId"] = str(row["template_page_id"])
        if row.get("page_number") is not None:
            anchor["pageNumber"] = int(row["page_number"])
        if row.get("coordinate_space"):
            anchor["coordinateSpace"] = str(row["coordinate_space"])
        if all(row.get(key) is not None for key in ("x", "y", "width", "height")):
            anchor["box"] = {
                key: float(row[key]) for key in ("x", "y", "width", "height")
            }
        return anchor

    @staticmethod
    def _processing_input_hash(
        submission: dict[str, Any],
        frame_set: dict[str, object],
        template_pages: list[dict[str, Any]],
        questions: list[dict[str, Any]],
        blank_config_ids: dict[str, str | None],
    ) -> str:
        snapshot = {
            "submission": {
                "id": str(submission["id"]),
                "sha256": str(submission.get("sha256") or ""),
                "relativePath": str(submission.get("relative_path") or ""),
                "mimeType": str(submission.get("mime_type") or ""),
                "sizeBytes": submission.get("size_bytes"),
            },
            "frameSet": {
                "id": str(frame_set["id"]),
                "revision": int(str(frame_set["revision"])),
                "contentHash": str(frame_set["contentHash"]),
            },
            "templatePages": [
                {
                    "id": str(page["id"]),
                    "pageNumber": int(page["page_number"]),
                    "sha256": str(page["sha256"]),
                    "width": int(page["width"]),
                    "height": int(page["height"]),
                }
                for page in template_pages
            ],
            "questions": [
                {
                    "id": str(question["id"]),
                    "number": str(question["number"]),
                    "type": str(question["type"]),
                    "stem": str(question["stem"]),
                    "answerRegions": json_loads(question.get("answer_regions_json"), []),
                }
                for question in questions
            ],
            "blankConfigs": [
                {"questionId": question_id, "versionId": blank_config_ids[question_id]}
                for question_id in sorted(blank_config_ids)
            ],
        }
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _revision_issue(
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "code": code,
            "message": message,
            "layer": "recognition",
            "nextAction": "reprocess_submission",
            "details": details or {},
        }

    @staticmethod
    def _issues_with(
        raw_issues: str | None,
        issue: dict[str, object],
    ) -> list[dict[str, object]]:
        issues = [
            item
            for item in json_loads(raw_issues, [])
            if isinstance(item, dict) and item.get("code") != issue["code"]
        ]
        return [*issues, issue]

    def _begin_processing_revision(
        self,
        submission_id: str,
        task_id: str,
        frame_set_id: str,
        input_hash: str,
    ) -> str:
        revision_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self.database.transaction() as connection:
            task = connection.execute(
                """SELECT t.current_question_frame_set_id,f.status AS frame_status
                   FROM tasks t LEFT JOIN question_frame_sets f
                     ON f.id=t.current_question_frame_set_id
                   WHERE t.id=?""",
                (task_id,),
            ).fetchone()
            if (
                task is None
                or str(task["current_question_frame_set_id"] or "") != frame_set_id
                or task["frame_status"] != "confirmed"
            ):
                raise AppError(
                    409,
                    "FRAME_SET_SUPERSEDED",
                    "处理开始前题框版本已变化，请重新处理学生试卷",
                    {"capturedFrameSetId": frame_set_id},
                )
            submission = connection.execute(
                """SELECT current_processing_revision_id FROM student_submissions
                   WHERE id=? AND task_id=?""",
                (submission_id, task_id),
            ).fetchone()
            if submission is None:
                raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
            previous_id = submission["current_processing_revision_id"]
            if previous_id:
                previous = connection.execute(
                    "SELECT * FROM student_processing_revisions WHERE id=?",
                    (previous_id,),
                ).fetchone()
                if previous is not None:
                    if previous["status"] in {"aligning", "recognizing"}:
                        issue = self._revision_issue(
                            "processing_revision_superseded",
                            "新的处理任务已启动，本轮旧任务不得再提交结果",
                            details={"supersededByRevisionId": revision_id},
                        )
                        connection.execute(
                            """UPDATE student_processing_revisions
                               SET status='failed',is_current=0,issues_json=?,finished_at=?,
                                   updated_at=? WHERE id=?""",
                            (
                                json_dumps(self._issues_with(previous["issues_json"], issue)),
                                timestamp,
                                timestamp,
                                previous_id,
                            ),
                        )
                    else:
                        connection.execute(
                            "UPDATE student_processing_revisions SET is_current=0,updated_at=? "
                            "WHERE id=?",
                            (timestamp, previous_id),
                        )
            next_row = connection.execute(
                """SELECT COALESCE(MAX(revision_number),0)+1 AS next_revision
                   FROM student_processing_revisions WHERE submission_id=?""",
                (submission_id,),
            ).fetchone()
            revision_number = int(next_row["next_revision"])
            connection.execute(
                """INSERT INTO student_processing_revisions(
                     id,submission_id,revision_number,frame_set_id,status,input_hash,
                     is_current,source,issues_json,started_at,created_at,updated_at
                   ) VALUES(?,?,?,?,'aligning',?,1,'system','[]',?,?,?)""",
                (
                    revision_id,
                    submission_id,
                    revision_number,
                    frame_set_id,
                    input_hash,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """UPDATE student_submissions SET current_processing_revision_id=?,
                   status='aligning',error_code=NULL,error_message=NULL,
                   question_region_status='processing',question_region_error_code=NULL,
                   question_region_error_message=NULL,updated_at=? WHERE id=?""",
                (revision_id, timestamp, submission_id),
            )
            self.database.audit(
                connection,
                task_id,
                "student_processing_revision_started",
                "system",
                {
                    "submissionId": submission_id,
                    "processingRevisionId": revision_id,
                    "revisionNumber": revision_number,
                    "frameSetId": frame_set_id,
                    "inputHash": input_hash,
                },
            )
        return revision_id

    def _advance_processing_revision(self, revision_id: str, status: str) -> bool:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT r.*,s.current_processing_revision_id
                   FROM student_processing_revisions r
                   JOIN student_submissions s ON s.id=r.submission_id
                   WHERE r.id=?""",
                (revision_id,),
            ).fetchone()
            if row is None:
                return False
            if not row["is_current"] or row["current_processing_revision_id"] != revision_id:
                self._mark_abandoned(
                    connection,
                    row,
                    "processing_pointer_changed",
                    detach_current=False,
                )
                return False
            connection.execute(
                "UPDATE student_processing_revisions SET status=?,updated_at=? WHERE id=?",
                (status, timestamp, revision_id),
            )
            connection.execute(
                """UPDATE student_submissions SET status='recognizing',updated_at=?
                   WHERE id=? AND current_processing_revision_id=?""",
                (timestamp, row["submission_id"], revision_id),
            )
            return True

    def _fail_processing_revision(
        self,
        revision_id: str,
        code: str,
        message: str,
    ) -> None:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT r.*,s.current_processing_revision_id
                   FROM student_processing_revisions r
                   JOIN student_submissions s ON s.id=r.submission_id
                   WHERE r.id=?""",
                (revision_id,),
            ).fetchone()
            if row is None:
                return
            is_current = (
                bool(row["is_current"])
                and row["current_processing_revision_id"] == revision_id
            )
            issue = self._revision_issue(code, message)
            issues = self._issues_with(row["issues_json"], issue)
            if not is_current:
                issues = self._issues_with(
                    json_dumps(issues),
                    self._revision_issue(
                        "processing_revision_abandoned",
                        "处理结果完成时已不是当前版本，因此未覆盖当前结果",
                        details={"reason": "processing_pointer_changed"},
                    ),
                )
            connection.execute(
                """UPDATE student_processing_revisions SET status='failed',is_current=?,
                   issues_json=?,finished_at=?,updated_at=? WHERE id=?""",
                (
                    1 if is_current else 0,
                    json_dumps(issues),
                    timestamp,
                    timestamp,
                    revision_id,
                ),
            )
            if is_current:
                connection.execute(
                    """UPDATE student_submissions SET status='failed',error_code=?,
                       error_message=?,question_region_status='failed',
                       question_region_error_code=?,question_region_error_message=?,updated_at=?
                       WHERE id=? AND current_processing_revision_id=?""",
                    (
                        code,
                        message,
                        code,
                        message,
                        timestamp,
                        row["submission_id"],
                        revision_id,
                    ),
                )

    def _mark_abandoned(
        self,
        connection: Any,
        revision: Any,
        reason: str,
        *,
        detach_current: bool,
    ) -> None:
        timestamp = now_iso()
        issue = self._revision_issue(
            "processing_revision_abandoned",
            "处理依赖版本已变化，本轮结果已放弃且不会覆盖当前结果",
            details={"reason": reason},
        )
        connection.execute(
            """UPDATE student_processing_revisions SET status='failed',is_current=0,
               issues_json=?,finished_at=?,updated_at=? WHERE id=?""",
            (
                json_dumps(self._issues_with(revision["issues_json"], issue)),
                timestamp,
                timestamp,
                revision["id"],
            ),
        )
        if detach_current:
            connection.execute(
                """UPDATE student_submissions SET current_processing_revision_id=NULL,
                   status='uploaded',error_code='STUDENT_PROCESSING_SUPERSEDED',
                   error_message='处理依赖版本已变化，请重新处理学生试卷',
                   question_region_status='pending',
                   question_region_error_code='STUDENT_PROCESSING_SUPERSEDED',
                   question_region_error_message='处理依赖版本已变化，请重新处理学生试卷',
                   updated_at=? WHERE id=? AND current_processing_revision_id=?""",
                (timestamp, revision["submission_id"], revision["id"]),
            )

    def _template_pages(self, task_id: str) -> list[dict[str, Any]]:
        pages = self.database.fetchall(
            """SELECT p.* FROM pages p
               JOIN documents d ON d.id=p.document_id
               WHERE d.task_id=? AND d.role='exam' ORDER BY p.page_number""",
            (task_id,),
        )
        if not pages:
            raise AppError(409, "TEMPLATE_PAGES_MISSING", "空白试卷尚未生成页面")
        return [dict(page) for page in pages]

    async def _student_pages(
        self,
        submission: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        stored = self.database.fetchall(
            """SELECT sp.*,p.page_number AS stored_template_page_number
               FROM student_pages sp LEFT JOIN pages p ON p.id=sp.template_page_id
               WHERE sp.submission_id=? ORDER BY sp.page_number""",
            (submission["id"],),
        )
        if stored:
            for page in stored:
                page["template_page_hint"] = page.get("stored_template_page_number")
            return stored, False
        return await self._render_staged_pages(submission), True

    async def _render_staged_pages(
        self,
        submission: dict[str, Any],
    ) -> list[dict[str, Any]]:
        submission_id = str(submission["id"])
        generation = uuid.uuid4().hex
        pages = await prepare_document_pages(
            self.settings,
            str(submission["task_id"]),
            f"student-{submission_id}-{generation}",
            str(submission["relative_path"]),
            str(submission["mime_type"]),
        )
        return [
            {
                "id": uuid.uuid4().hex,
                "submission_id": submission_id,
                "page_number": page.page_number,
                "original_image_path": page.relative_path,
                "width": page.width,
                "height": page.height,
                "sha256": page.sha256,
                "template_page_hint": (
                    self._page_hint(str(submission.get("original_name", "")))
                    if len(pages) == 1
                    else None
                ),
            }
            for page in pages
        ]

    def _align_pages(
        self,
        template_pages: list[dict[str, Any]],
        student_pages: list[dict[str, Any]],
    ) -> AlignmentMap:
        output: AlignmentMap = {}
        partial_submission = len(student_pages) < len(template_pages)
        unused_templates = list(template_pages)
        for student_page in student_pages:
            student_path = resolve_data_path(
                self.settings,
                str(student_page["original_image_path"]),
            )
            if partial_submission:
                page_hint = student_page.get("template_page_hint")
                hinted_template = next(
                    (
                        page
                        for page in unused_templates
                        if page_hint is not None and int(page["page_number"]) == int(page_hint)
                    ),
                    None,
                )
                templates_to_compare = (
                    [hinted_template] if hinted_template is not None else unused_templates
                )
                candidates: list[tuple[float, dict[str, Any], AlignmentResult]] = []
                for template_page in templates_to_compare:
                    template_path = resolve_data_path(
                        self.settings, str(template_page["image_path"])
                    )
                    alignment = align_pages(template_path, student_path)
                    candidates.append((alignment.quality.score, template_page, alignment))
                if not candidates:
                    raise AppError(422, "STUDENT_PAGE_UNMATCHED", "没有可匹配的空白试卷页面")
                _score, template_page, alignment = max(
                    candidates,
                    key=lambda item: (
                        int(student_page.get("template_page_hint") or 0)
                        == int(item[1]["page_number"]),
                        item[0],
                        -abs(int(item[1]["page_number"]) - int(student_page["page_number"])),
                    ),
                )
            else:
                template_page = unused_templates[0]
                template_path = resolve_data_path(self.settings, str(template_page["image_path"]))
                alignment = align_pages(template_path, student_path)
            unused_templates.remove(template_page)
            template_page_number = int(template_page["page_number"])
            student_page.update(
                {
                    "template_page_id": template_page["id"],
                    "alignment_transform": alignment.student_to_template.as_rows(),
                    "alignment_quality": alignment.quality.score,
                    "alignment_method": alignment.quality.method,
                    "alignment_status": (
                        "aligned" if alignment.quality.is_reliable else "low_quality"
                    ),
                }
            )
            output[template_page_number] = (template_page, student_page, alignment)
        return output

    @staticmethod
    def _assign_alignment_revisions(
        processing_revision_id: str,
        alignments: AlignmentMap,
    ) -> None:
        for template_page, student_page, alignment in alignments.values():
            student_page.update(
                {
                    "processing_revision_id": processing_revision_id,
                    "alignment_revision_id": uuid.uuid4().hex,
                    "alignment_revision_number": 1,
                    "alignment_revision_transform": alignment.template_to_student.as_rows(),
                    "alignment_metrics": alignment.quality.as_dict(),
                    "alignment_template_page_id": template_page["id"],
                }
            )

    def _persisted_alignment_map(
        self,
        processing_revision_id: str,
        template_pages: list[dict[str, Any]],
    ) -> tuple[AlignmentMap, bool]:
        template_by_id = {str(page["id"]): page for page in template_pages}
        rows = self.database.fetchall(
            """SELECT a.*,sp.page_number AS student_page_number,
                      sp.original_image_path,sp.width AS student_width,
                      sp.height AS student_height
               FROM student_page_alignment_revisions a
               JOIN student_pages sp ON sp.id=a.student_page_id
               WHERE a.processing_revision_id=? AND a.is_current=1
               ORDER BY a.student_page_id""",
            (processing_revision_id,),
        )
        output: AlignmentMap = {}
        duplicate_template_pages: set[int] = set()
        integrity = bool(rows)
        for row in rows:
            template_page = template_by_id.get(str(row.get("template_page_id") or ""))
            transform_rows = json_loads(row.get("transform_json"), None)
            if not template_page or not isinstance(transform_rows, list):
                integrity = False
                continue
            page_number = int(template_page["page_number"])
            if page_number in duplicate_template_pages:
                integrity = False
                continue
            if page_number in output:
                integrity = False
                output.pop(page_number, None)
                duplicate_template_pages.add(page_number)
                continue
            score = float(row.get("quality") or 0.0)
            # Persisted status carries the producer's reliability decision. The
            # configurable numeric threshold is applied exactly once by the
            # pure frame-set mapper below.
            reliable = row.get("status") == "aligned"
            try:
                alignment = AlignmentResult.create(
                    Homography.from_rows(transform_rows),
                    PageSize(int(template_page["width"]), int(template_page["height"])),
                    PageSize(int(row["student_width"]), int(row["student_height"])),
                    AlignmentQuality(
                        method=str(row.get("method") or "persisted_alignment"),
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
            except (TypeError, ValueError):
                integrity = False
                continue
            student_page = {
                "id": str(row["student_page_id"]),
                "page_number": int(row["student_page_number"]),
                "original_image_path": str(row["original_image_path"]),
                "width": int(row["student_width"]),
                "height": int(row["student_height"]),
                "alignment_revision_id": str(row["id"]),
            }
            output[page_number] = (template_page, student_page, alignment)
        return output, integrity

    @staticmethod
    def _page_hint(filename: str) -> int | None:
        stem = filename.rsplit(".", 1)[0]
        match = re.search(r"(?:^|[-_\s])(?:page[-_\s]?)?(\d{1,3})$", stem, re.I)
        if not match:
            return None
        page_number = int(match.group(1))
        return page_number if page_number > 0 else None

    def _questions_from_frame_set(
        self,
        task_id: str,
        frame_set_id: str,
    ) -> list[dict[str, Any]]:
        questions = self.database.fetchall(
            "SELECT * FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order",
            (task_id,),
        )
        if not questions:
            raise AppError(409, "QUESTIONS_MISSING", "试卷尚未识别出题目")
        frame_rows = self.database.fetchall(
            """SELECT i.question_id,r.*
               FROM question_frame_sets f
               JOIN question_frame_items i ON i.frame_set_id=f.id
               JOIN question_frame_regions r ON r.frame_item_id=i.id
               WHERE f.id=? AND f.task_id=? AND f.status='confirmed'
                 AND i.status='confirmed'
               ORDER BY i.question_id,r.sort_order,r.id""",
            (frame_set_id, task_id),
        )
        by_question: dict[str, list[dict[str, Any]]] = {}
        for row in frame_rows:
            question_id = str(row["question_id"])
            by_question.setdefault(question_id, []).append(
                {
                    "id": str(row["id"]),
                    "region_key": str(row["region_key"]),
                    "template_page_id": str(row["template_page_id"]),
                    "page_number": int(row["page_number"]),
                    "x": float(row["x"]),
                    "y": float(row["y"]),
                    "width": float(row["width"]),
                    "height": float(row["height"]),
                    "sort_order": int(row["sort_order"]),
                    "confidence": (
                        float(row["confidence"]) if row.get("confidence") is not None else 1.0
                    ),
                    "issues": json_loads(row.get("issues_json"), []),
                }
            )
        missing = [
            str(question["id"])
            for question in questions
            if not by_question.get(str(question["id"]))
        ]
        if missing:
            raise AppError(
                409,
                "QUESTION_FRAMES_NOT_CONFIRMED",
                "已确认题框版本不完整，请重新确认题框后再处理学生试卷",
                {"frameSetId": frame_set_id, "missingQuestionIds": missing},
            )
        return [
            {
                **self._effective_question(question),
                "frame_set_id": frame_set_id,
                "frame_regions": by_question[str(question["id"])],
            }
            for question in questions
        ]

    def _map_question_regions(
        self,
        frame_set: dict[str, object],
        alignments: AlignmentMap,
    ) -> tuple[list[dict[str, Any]], list[dict[str, object]]]:
        page_alignments = {
            str(template_page["id"]): FramePageAlignment.from_result(
                template_page_id=str(template_page["id"]),
                template_page_number=int(template_page["page_number"]),
                student_page_id=str(student_page["id"]),
                alignment_revision_id=str(student_page["alignment_revision_id"]),
                result=alignment,
            )
            for template_page, student_page, alignment in alignments.values()
        }
        result = map_confirmed_frame_set(
            frame_set,
            page_alignments,
            min_alignment_score=self.settings.mapping_min_alignment_score,
            min_polygon_area_px=self.settings.mapping_min_polygon_area_px,
            min_visible_ratio=self.settings.mapping_min_visible_ratio,
            max_out_of_bounds_ratio=self.settings.mapping_max_out_of_bounds_ratio,
            max_cross_question_overlap_ratio=(
                self.settings.mapping_max_cross_question_overlap_ratio
            ),
        )
        mapped_regions: list[dict[str, Any]] = []
        for mapping in result.mappings:
            visible_bbox = mapping.visible_original_page_bbox
            mapped_regions.append(
                {
                    "id": uuid.uuid4().hex,
                    "question_id": mapping.question_id,
                    "frame_set_id": mapping.frame_set_id,
                    "frame_region_id": mapping.frame_region_id,
                    "alignment_revision_id": mapping.alignment_revision_id,
                    "sort_order": mapping.sort_order,
                    "template_page_id": mapping.template_page_id,
                    "student_page_id": mapping.student_page_id,
                    "template_region": {
                        "id": mapping.frame_region_id,
                        "region_key": mapping.region_key,
                        "template_page_id": mapping.template_page_id,
                        "page_number": mapping.page_number,
                        "coordinate_space": "template_page_normalized",
                        "x": mapping.template_normalized_bbox.left,
                        "y": mapping.template_normalized_bbox.top,
                        "width": mapping.template_normalized_bbox.width,
                        "height": mapping.template_normalized_bbox.height,
                        "sort_order": mapping.sort_order,
                    },
                    "student_polygon": mapping.original_page_polygon.as_dicts(),
                    "student_bbox": (
                        self._box_dict(visible_bbox)
                        if visible_bbox is not None
                        else self._box_dict(mapping.original_page_bbox)
                    ),
                    "status": mapping.status,
                    "issues": list(mapping.issues),
                }
            )
        return mapped_regions, [blocker.as_dict() for blocker in result.blockers]

    @staticmethod
    def _mapping_ready(
        questions: list[dict[str, Any]],
        mapped_regions: list[dict[str, Any]],
        missing_template_pages: list[int],
    ) -> bool:
        if missing_template_pages:
            return False
        expected = {
            (str(question["id"]), str(region["id"]))
            for question in questions
            for region in question["frame_regions"]
        }
        actual = [
            (str(region["question_id"]), str(region["frame_region_id"]))
            for region in mapped_regions
        ]
        return (
            len(actual) == len(expected)
            and set(actual) == expected
            and all(region["status"] == "ready" for region in mapped_regions)
        )

    @staticmethod
    def _effective_question(row: dict[str, Any]) -> dict[str, Any]:
        override = json_loads(row.get("teacher_override_json"), {})
        return {
            **row,
            "number": override.get("number", row["detected_number"]),
            "stem": override.get("stem", row["stem"]),
            "type": override.get("type", row["question_type"]),
        }

    def _region(
        self,
        question: dict[str, Any],
        raw_region: dict[str, Any],
        index: int,
        alignments: AlignmentMap,
    ) -> tuple[dict[str, Any], dict[str, Any], AlignmentResult, AnswerRegion]:
        page_number = int(raw_region["page_number"])
        if page_number not in alignments:
            raise AppError(
                422,
                "TEMPLATE_REGION_PAGE_INVALID",
                f"第 {question['number']} 题的答题区域不属于当前试卷页面",
            )
        template_page, student_page, alignment = alignments[page_number]
        template_page_id = raw_region.get("template_page_id")
        if template_page_id and str(template_page_id) != str(template_page["id"]):
            raise AppError(
                422,
                "TEMPLATE_REGION_PAGE_INVALID",
                f"第 {question['number']} 题的题框页标识与模板页不一致",
            )
        width = int(template_page["width"])
        height = int(template_page["height"])
        region = AnswerRegion.rectangle(
            str(question["id"]),
            f"{question['id']}:{index}",
            page_number,
            float(raw_region["x"]) * width,
            float(raw_region["y"]) * height,
            (float(raw_region["x"]) + float(raw_region["width"])) * width,
            (float(raw_region["y"]) + float(raw_region["height"])) * height,
        )
        return template_page, student_page, alignment, region

    async def _recognize_responses(
        self,
        questions: list[dict[str, Any]],
        alignments: AlignmentMap,
        blank_configs: dict[str, dict[str, Any]],
        *,
        uploaded_student_page_numbers: list[int],
        allow_non_calculation: bool,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.settings.student_recognition_concurrency)

        async def recognize_one(question: dict[str, Any]) -> dict[str, Any] | None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await self._recognize_question_response(
                        question,
                        questions,
                        alignments,
                        blank_configs,
                        uploaded_student_page_numbers=uploaded_student_page_numbers,
                        allow_non_calculation=allow_non_calculation,
                    )
                except BaseException as error:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        "student_question_recognition_failed",
                        question_id=str(question.get("id") or ""),
                        question_number=str(question.get("number") or ""),
                        question_type=str(question.get("type") or "unknown"),
                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                        error_type=type(error).__name__,
                    )
                    raise
                raw = response.get("raw_recognition", {}) if response else {}
                localization = raw.get("localization", {}) if isinstance(raw, dict) else {}
                request_counts = (
                    localization.get("requestCounts", {})
                    if isinstance(localization, dict)
                    else {}
                )
                batches = (
                    localization.get("batches", [])
                    if isinstance(localization, dict)
                    else []
                )
                log_event(
                    LOGGER,
                    logging.INFO,
                    "student_question_recognized",
                    question_id=str(question.get("id") or ""),
                    question_number=str(question.get("number") or ""),
                    question_type=str(question.get("type") or "unknown"),
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    status=str(response.get("status") or "skipped") if response else "skipped",
                    recognition_path=(
                        str(raw.get("recognitionPath"))
                        if isinstance(raw, dict) and raw.get("recognitionPath")
                        else None
                    ),
                    batch_count=len(batches) if isinstance(batches, list) else 0,
                    fast_request_count=(
                        int(request_counts.get("fast", 0))
                        if isinstance(request_counts, dict)
                        else 0
                    ),
                    fallback_request_count=(
                        int(request_counts.get("legacyLocalization", 0))
                        + int(request_counts.get("legacyTranscription", 0))
                        if isinstance(request_counts, dict)
                        else 0
                    ),
                )
                return response

        tasks = [asyncio.create_task(recognize_one(question)) for question in questions]
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        responses: list[dict[str, Any]] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                responses.append(outcome)
        return responses

    async def _recognize_question_response(
        self,
        question: dict[str, Any],
        questions: list[dict[str, Any]],
        alignments: AlignmentMap,
        blank_configs: dict[str, dict[str, Any]],
        *,
        uploaded_student_page_numbers: list[int],
        allow_non_calculation: bool,
    ) -> dict[str, Any] | None:
        question_type = str(question.get("type", ""))
        if question_type == "calculation":
            return await self._recognize_calculation_response(
                question,
                questions,
                alignments,
                uploaded_student_page_numbers,
            )
        if not allow_non_calculation:
            return None
        if question_type == "fill_blank":
            config = blank_configs.get(str(question["id"]))
            if not config or not config["confirmed"]:
                return self._unconfigured_fill_response(
                    question,
                    str(config["issue"] if config else "fill_blank_config_missing"),
                )
            prepared, saved_regions, low_quality = self._prepare_response_regions(
                question,
                question["frame_regions"],
                alignments,
            )
            return await self._recognize_fill_response(
                question,
                config,
                prepared,
                saved_regions,
                low_quality,
            )

        raw_regions = json_loads(question.get("answer_regions_json"), [])
        if question_type in {"single_choice", "multiple_choice"}:
            # Choice marks may appear anywhere inside the complete question.
            # Calculation questions use the dedicated anchor-to-anchor locator.
            raw_regions = question["frame_regions"]
        prepared, saved_regions, low_quality = self._prepare_response_regions(
            question,
            raw_regions,
            alignments,
        )
        if not prepared:
            return None
        result, raw, usage = await self.recognition.recognize_student_response(
            {
                "number": question["number"],
                "type": question["type"],
                "stem": question["stem"],
            },
            prepared,
        )
        status = (
            "needs_review"
            if low_quality or result["issues"] or result["confidence"] < 0.75
            else "recognized"
        )
        return {
            "id": uuid.uuid4().hex,
            "question_id": question["id"],
            "question_number": str(question["number"]),
            "recognized_text": result["transcription"],
            "confidence": result["confidence"],
            "recognition_model_id": self.settings.dashscope_model,
            "raw_recognition": {
                "isBlank": result["is_blank"],
                "issues": result["issues"],
                "segments": result.get(
                    "segments",
                    [
                        {
                            "region_index": 1,
                            "transcription": result["transcription"],
                            "is_blank": result["is_blank"],
                            "confidence": result["confidence"],
                            "issues": result["issues"],
                        }
                    ]
                    if len(saved_regions) == 1
                    else [],
                ),
                "raw": raw,
                "usage": usage,
            },
            "status": status,
            "regions": saved_regions,
            "blank_config_version_id": None,
            "blanks": [],
        }

    async def _recognize_calculation_response(
        self,
        question: dict[str, Any],
        questions: list[dict[str, Any]],
        alignments: AlignmentMap,
        uploaded_student_page_numbers: list[int],
    ) -> dict[str, Any]:
        plan = build_calculation_search_plan(
            frame_set_id=str(question["frame_set_id"]),
            question_id=str(question["id"]),
            questions=questions,
            page_bindings=self._calculation_page_bindings(alignments),
            uploaded_student_page_numbers=uploaded_student_page_numbers,
        )
        batch_results: list[CalculationLocalizationBatchResult] = []
        batch_snapshots: list[dict[str, Any]] = []
        batch_paths: list[str] = []
        combined_transcriptions: dict[
            tuple[str, int], CalculationRegionTranscription
        ] = {}
        fast_request_count = 0
        legacy_location_request_count = 0
        total_usage: dict[str, int] = {}
        batch_size = self.settings.answer_pages_per_batch
        if plan.evidence_complete:
            for start in range(0, len(plan.fragments), batch_size):
                planned_batch = plan.fragments[start : start + batch_size]
                batch_index = len(batch_results) + 1
                attempt_id = uuid.uuid4().hex
                runtime_batch: tuple[CalculationSearchFragment, ...] | None = None
                fast_result: CalculationRecognitionBatchResult | None = None
                fast_raw: dict[str, Any] | None = None
                fast_usage: dict[str, int] = {}
                fast_error: dict[str, str] | None = None
                try:
                    runtime_batch = tuple(
                        self._calculation_fragment_with_images(fragment, alignments)
                        for fragment in planned_batch
                    )
                    fast_request_count += 1
                    fast_result, fast_raw, fast_usage = (
                        await self.recognition.recognize_calculation_batch(
                            {
                                "id": str(question["id"]),
                                "number": str(question["number"]),
                                "type": "calculation",
                                "stem": str(question.get("stem") or ""),
                            },
                            runtime_batch,
                            frame_set_id=str(question["frame_set_id"]),
                            batch_index=batch_index,
                            attempt_id=attempt_id,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    fast_error = {
                        "code": str(
                            getattr(error, "code", "CALCULATION_RECOGNITION_FAILED")
                        ),
                        "message": str(getattr(error, "message", str(error)))[:500],
                    }
                for key, value in fast_usage.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        total_usage[key] = total_usage.get(key, 0) + value

                legacy_raw: dict[str, Any] | None = None
                legacy_usage: dict[str, int] = {}
                legacy_error: dict[str, str] | None = None
                if fast_result is not None and fast_result.localization_contract_valid:
                    batch_result = fast_result.localization
                    combined_transcriptions.update(fast_result.transcription_by_region)
                    if batch_result.reliable_blank:
                        batch_path = "reliable_blank"
                    elif fast_result.transcription_contract_valid:
                        batch_path = "single_pass"
                    else:
                        batch_path = "transcription_fallback"
                else:
                    batch_path = "full_fallback"
                    if fast_result is not None and fast_error is None:
                        fast_error = {
                            "code": "CALCULATION_RECOGNITION_LOCATION_INVALID",
                            "message": (
                                "The combined response did not contain reusable localization."
                            ),
                        }
                    try:
                        if runtime_batch is None:
                            runtime_batch = tuple(
                                self._calculation_fragment_with_images(fragment, alignments)
                                for fragment in planned_batch
                            )
                        legacy_location_request_count += 1
                        batch_result, legacy_raw, legacy_usage = (
                            await self.recognition.locate_calculation_regions(
                                {
                                    "id": str(question["id"]),
                                    "number": str(question["number"]),
                                    "type": "calculation",
                                    "stem": str(question.get("stem") or ""),
                                },
                                runtime_batch,
                                frame_set_id=str(question["frame_set_id"]),
                                batch_index=batch_index,
                                attempt_id=attempt_id,
                            )
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        error_code = str(
                            getattr(error, "code", "CALCULATION_LOCALIZATION_FAILED")
                        )
                        issue_code = (
                            "calculation_search_fragment_clipped"
                            if error_code == "CALCULATION_SEARCH_FRAGMENT_CLIPPED"
                            else "calculation_localization_batch_failed"
                        )
                        batch_result = failed_calculation_localization_batch(
                            planned_batch,
                            batch_index=batch_index,
                            attempt_id=attempt_id,
                            model_id=self.settings.dashscope_model,
                            prompt_version=CALCULATION_LOCALIZATION_PROMPT_VERSION,
                            issue_code=issue_code,
                            issue_message=(
                                "The bounded calculation localization batch failed "
                                f"without a usable result ({error_code})."
                            ),
                        )
                        legacy_error = {
                            "code": error_code,
                            "message": str(getattr(error, "message", str(error)))[:500],
                        }
                    for key, value in legacy_usage.items():
                        if isinstance(value, int) and not isinstance(value, bool):
                            total_usage[key] = total_usage.get(key, 0) + value

                batch_results.append(batch_result)
                batch_paths.append(batch_path)
                batch_usage: dict[str, int] = {}
                for source in (fast_usage, legacy_usage):
                    for key, value in source.items():
                        if isinstance(value, int) and not isinstance(value, bool):
                            batch_usage[key] = batch_usage.get(key, 0) + value
                batch_snapshots.append(
                    {
                        **batch_result.as_dict(),
                        "recognitionPath": batch_path,
                        "fragmentKeys": [fragment.fragment_key for fragment in planned_batch],
                        "rawOutput": legacy_raw if batch_path == "full_fallback" else fast_raw,
                        "usage": batch_usage,
                        "error": legacy_error,
                        "fastPath": {
                            "promptVersion": CALCULATION_RECOGNITION_PROMPT_VERSION,
                            "rawOutput": fast_raw,
                            "usage": fast_usage,
                            "error": fast_error,
                            "localizationContractValid": (
                                fast_result.localization_contract_valid
                                if fast_result is not None
                                else False
                            ),
                            "transcriptionContractValid": (
                                fast_result.transcription_contract_valid
                                if fast_result is not None
                                else False
                            ),
                            "issues": (
                                [issue.as_dict() for issue in fast_result.issues]
                                if fast_result is not None
                                else []
                            ),
                        },
                        "legacyLocalization": (
                            {
                                "promptVersion": CALCULATION_LOCALIZATION_PROMPT_VERSION,
                                "rawOutput": legacy_raw,
                                "usage": legacy_usage,
                                "error": legacy_error,
                            }
                            if batch_path == "full_fallback"
                            else None
                        ),
                    }
                )

        localized = aggregate_calculation_localization_batches(
            plan.fragments,
            batch_results,
            plan_issues=plan.issues,
        )
        fragment_by_key = {
            fragment.fragment_key: fragment for fragment in plan.fragments
        }
        batch_by_fragment = {
            window.fragment_key: batch
            for batch in localized.batches
            for window in batch.windows
        }
        prepared_positive: list[dict[str, Any]] = []
        positive_sort_orders: list[int] = []
        positive_region_keys: list[tuple[str, int]] = []
        saved_regions: list[dict[str, Any]] = []
        evidence_snapshots: list[dict[str, Any]] = []
        evidence_issues: list[dict[str, Any]] = []
        expected_evidence_count = 0
        for window in localized.windows:
            fragment = fragment_by_key.get(window.fragment_key)
            batch = batch_by_fragment.get(window.fragment_key)
            if fragment is None or batch is None:
                continue
            if window.status is CalculationWindowStatus.LOCATED:
                candidates: list[LocalizedCalculationRegion | None] = list(window.regions)
                evidence_kind = "located_region"
            elif window.status is CalculationWindowStatus.BLANK:
                candidates = [None]
                evidence_kind = "blank_search_window"
            else:
                continue
            for candidate in candidates:
                expected_evidence_count += 1
                try:
                    prepared, saved, snapshot = self._prepare_calculation_evidence(
                        question,
                        fragment,
                        alignments,
                        evidence_kind=evidence_kind,
                        candidate=candidate,
                        batch_index=batch.batch_index,
                        attempt_id=batch.attempt_id,
                        confidence=(
                            candidate.confidence if candidate is not None else window.confidence
                        ),
                        issues=(
                            list(candidate.issues)
                            if candidate is not None
                            else list(window.issues)
                        ),
                        sort_order=len(saved_regions),
                    )
                except Exception as error:
                    error_code = str(
                        getattr(error, "code", "CALCULATION_EVIDENCE_CROP_FAILED")
                    )
                    evidence_issues.append(
                        {
                            "code": (
                                "calculation_evidence_region_clipped"
                                if error_code == "CALCULATION_EVIDENCE_REGION_CLIPPED"
                                else "calculation_evidence_crop_failed"
                            ),
                            "path": f"$.windows[{window.fragment_key}]",
                            "message": (
                                "A verified localization could not be converted "
                                "to evidence."
                            ),
                            "details": {
                                "fragmentKey": window.fragment_key,
                                "errorCode": error_code,
                                "error": type(error).__name__,
                            },
                        }
                    )
                    continue
                if evidence_kind == "located_region":
                    prepared_positive.append(prepared)
                    positive_sort_orders.append(int(saved["sort_order"]))
                    if candidate is None:
                        raise RuntimeError("located calculation evidence lacks a candidate")
                    positive_region_keys.append(
                        (candidate.fragment_key, candidate.model_candidate_index)
                    )
                saved_regions.append(saved)
                evidence_snapshots.append(snapshot)

        evidence_crops_complete = len(evidence_snapshots) == expected_evidence_count
        has_uncertain_window = any(
            window.status is CalculationWindowStatus.UNCERTAIN
            for window in localized.windows
        )
        evidence_complete = (
            localized.evidence_complete
            and evidence_crops_complete
            and not has_uncertain_window
        )
        recognition_threshold = self.settings.grading_recognition_review_threshold
        alignment_confidence = min(
            (fragment.alignment_confidence for fragment in plan.fragments),
            default=0.0,
        )
        reliable_blank = (
            localized.reliable_blank
            and evidence_complete
            and alignment_confidence >= recognition_threshold
            and len(evidence_snapshots) == len(plan.fragments)
            and all(
                item["evidenceKind"] == "blank_search_window"
                for item in evidence_snapshots
            )
        )
        transcription_segments: list[dict[str, Any]] = []
        used_global_indexes: set[int] = set()
        transcription_confidences: list[float] = []
        transcription_issues: list[str] = []
        fallback_prepared: list[dict[str, Any]] = []
        fallback_sort_orders: list[int] = []
        for prepared, sort_order, region_key in zip(
            prepared_positive,
            positive_sort_orders,
            positive_region_keys,
            strict=True,
        ):
            combined = combined_transcriptions.get(region_key)
            if combined is None:
                fallback_prepared.append(prepared)
                fallback_sort_orders.append(sort_order)
                continue
            global_index = sort_order + 1
            used_global_indexes.add(global_index)
            transcription_confidences.append(combined.confidence)
            transcription_issues.extend(combined.issues)
            transcription_segments.append(
                {
                    "region_index": global_index,
                    "transcription": combined.transcription,
                    "is_blank": False,
                    "confidence": combined.confidence,
                    "issues": list(combined.issues),
                }
            )

        transcription_raw: dict[str, Any] | None = None
        transcription_usage: dict[str, int] = {}
        fallback_segments: list[dict[str, Any]] = []
        fallback_transcription = ""
        fallback_confidence = localized.confidence
        fallback_transcription_request_count = 0
        if fallback_prepared:
            try:
                fallback_transcription_request_count = 1
                result, transcription_raw, transcription_usage = (
                    await self.recognition.recognize_student_response(
                        {
                            "number": question["number"],
                            "type": "calculation",
                            "stem": question["stem"],
                        },
                        fallback_prepared,
                    )
                )
                fallback_transcription = str(result.get("transcription") or "").strip()
                fallback_confidence = float(result.get("confidence", 0.0))
                transcription_issues.extend(
                    str(issue)
                    for issue in result.get("issues", [])
                    if str(issue).strip()
                )
                fallback_segments = [
                    dict(segment)
                    for segment in result.get("segments", [])
                    if isinstance(segment, dict)
                ]
                if bool(result.get("is_blank")):
                    transcription_issues.append("calculation_transcription_blank_conflict")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                fallback_confidence = 0.0
                transcription_issues.append("calculation_transcription_failed")
                transcription_raw = {
                    "error": {
                        "code": str(
                            getattr(error, "code", "CALCULATION_TRANSCRIPTION_FAILED")
                        ),
                        "message": str(getattr(error, "message", str(error)))[:500],
                    }
                }
            for key, value in transcription_usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    total_usage[key] = total_usage.get(key, 0) + value

            if not fallback_segments:
                if len(fallback_prepared) == 1:
                    fallback_segments = [
                        {
                            "region_index": 1,
                            "transcription": fallback_transcription,
                            "is_blank": False,
                            "confidence": fallback_confidence,
                            "issues": list(transcription_issues),
                        }
                    ]
                else:
                    transcription_issues.append("segments_missing")
            for segment in fallback_segments:
                try:
                    local_index = int(segment.get("region_index", 0))
                    global_index = fallback_sort_orders[local_index - 1] + 1
                except (IndexError, TypeError, ValueError):
                    transcription_issues.append("segment_index_invalid")
                    continue
                if local_index <= 0 or global_index in used_global_indexes:
                    transcription_issues.append("segment_index_invalid")
                    continue
                used_global_indexes.add(global_index)
                try:
                    segment_confidence = float(segment.get("confidence", fallback_confidence))
                except (TypeError, ValueError):
                    segment_confidence = 0.0
                    transcription_issues.append("segment_confidence_invalid")
                transcription_confidences.append(segment_confidence)
                transcription_segments.append({**segment, "region_index": global_index})

        expected_positive_indexes = {sort_order + 1 for sort_order in positive_sort_orders}
        if expected_positive_indexes - used_global_indexes:
            transcription_issues.append("segments_missing")
            transcription_confidences.append(0.0)
        for saved, evidence in zip(saved_regions, evidence_snapshots, strict=True):
            global_index = int(saved["sort_order"]) + 1
            if evidence["evidenceKind"] != "blank_search_window":
                continue
            segment_reliable_blank = (
                evidence_complete
                and float(evidence["confidence"]) >= recognition_threshold
                and alignment_confidence >= recognition_threshold
                and not evidence["issues"]
            )
            used_global_indexes.add(global_index)
            transcription_segments.append(
                {
                    "region_index": global_index,
                    "transcription": "",
                    "is_blank": segment_reliable_blank,
                    "confidence": float(evidence["confidence"]),
                    "issues": (
                        list(evidence["issues"])
                        if segment_reliable_blank
                        else [*list(evidence["issues"]), "localization_blank_unreliable"]
                    ),
                }
            )
        transcription_segments.sort(key=lambda segment: int(segment["region_index"]))
        transcription = "\n".join(
            str(segment.get("transcription") or "").strip()
            for segment in transcription_segments
            if not bool(segment.get("is_blank"))
            and str(segment.get("transcription") or "").strip()
        ).strip()
        transcription_confidence = (
            min(transcription_confidences)
            if transcription_confidences
            else localized.confidence
        )

        confidence = min(localized.confidence, alignment_confidence)
        if prepared_positive:
            confidence = min(confidence, max(0.0, min(1.0, transcription_confidence)))
        alignment_issue_dicts: list[dict[str, Any]] = []
        if plan.fragments and alignment_confidence < recognition_threshold:
            alignment_issue_dicts.append(
                {
                    "code": "calculation_alignment_low_confidence",
                    "path": "$.plan.fragments",
                    "message": (
                        "At least one calculation search page has alignment "
                        "confidence below the recognition review threshold."
                    ),
                    "details": {
                        "confidence": alignment_confidence,
                        "threshold": recognition_threshold,
                    },
                }
            )
        localization_issue_dicts = [
            issue.as_dict() for issue in localized.issues
        ] + evidence_issues + alignment_issue_dicts
        issue_codes = [
            str(issue.get("code"))
            for issue in localization_issue_dicts
            if str(issue.get("code") or "").strip()
        ]
        issue_codes.extend(transcription_issues)
        issue_codes = list(dict.fromkeys(issue_codes))
        status = (
            "recognized"
            if evidence_complete
            and (reliable_blank or bool(prepared_positive))
            and not issue_codes
            and confidence >= recognition_threshold
            else "needs_review"
        )
        localization_status = (
            "blank"
            if reliable_blank
            else "located"
            if status == "recognized"
            else "needs_review"
        )
        recognition_path = (
            "full_fallback"
            if "full_fallback" in batch_paths
            else "transcription_fallback"
            if "transcription_fallback" in batch_paths
            else "reliable_blank"
            if reliable_blank
            else "single_pass"
        )
        localization_snapshot = {
            "schemaVersion": 1,
            "recognitionPath": recognition_path,
            "status": localization_status,
            "evidenceComplete": evidence_complete,
            "reliableBlank": reliable_blank,
            "confidence": confidence,
            "alignmentConfidence": alignment_confidence,
            "recognitionReviewThreshold": recognition_threshold,
            "modelId": self.settings.dashscope_model if batch_results else None,
            "promptVersion": (
                CALCULATION_RECOGNITION_PROMPT_VERSION
                if recognition_path != "full_fallback"
                else CALCULATION_LOCALIZATION_PROMPT_VERSION
            ),
            "plan": plan.snapshot(),
            "batches": batch_snapshots,
            "requestCounts": {
                "fast": fast_request_count,
                "legacyLocalization": legacy_location_request_count,
                "legacyTranscription": fallback_transcription_request_count,
                "total": (
                    fast_request_count
                    + legacy_location_request_count
                    + fallback_transcription_request_count
                ),
            },
            "evidence": evidence_snapshots,
            "issues": localization_issue_dicts,
        }
        return {
            "id": uuid.uuid4().hex,
            "question_id": question["id"],
            "question_number": str(question["number"]),
            "recognized_text": transcription,
            "confidence": confidence,
            "recognition_model_id": (
                self.settings.dashscope_model if batch_results or prepared_positive else None
            ),
            "raw_recognition": {
                "isBlank": reliable_blank,
                "recognitionPath": recognition_path,
                "issues": issue_codes,
                "segments": transcription_segments,
                "raw": transcription_raw,
                "usage": total_usage,
                "localization": localization_snapshot,
            },
            "status": status,
            "regions": saved_regions,
            "blank_config_version_id": None,
            "blanks": [],
        }

    def _calculation_page_bindings(
        self,
        alignments: AlignmentMap,
    ) -> list[CalculationPageBinding]:
        return [
            CalculationPageBinding(
                page_number=page_number,
                student_page_number=int(student_page["page_number"]),
                template_page_id=str(template_page["id"]),
                student_page_id=str(student_page["id"]),
                alignment_revision_id=str(student_page["alignment_revision_id"]),
                is_reliable=(
                    alignment.quality.is_reliable
                    and alignment.quality.score
                    >= self.settings.mapping_min_alignment_score
                ),
                alignment_confidence=alignment.quality.score,
            )
            for page_number, (template_page, student_page, alignment) in sorted(
                alignments.items()
            )
        ]

    @staticmethod
    def _calculation_parent_pixel_box(
        fragment: CalculationSearchFragment,
        template_page: dict[str, Any],
    ) -> tuple[int, int, int, int]:
        """Convert a half-open normalized window without leaking boundary pixels."""

        page_width = int(template_page["width"])
        page_height = int(template_page["height"])
        epsilon = 1e-9
        left = max(0, math.ceil(fragment.x * page_width - epsilon))
        top = max(0, math.ceil(fragment.y * page_height - epsilon))
        right = min(
            page_width,
            math.floor((fragment.x + fragment.width) * page_width + epsilon),
        )
        bottom = min(
            page_height,
            math.floor((fragment.y + fragment.height) * page_height + epsilon),
        )
        if right <= left or bottom <= top:
            raise ValueError(f"search fragment {fragment.fragment_key} has no pixel area")
        return left, top, right, bottom

    def _calculation_fragment_with_images(
        self,
        fragment: CalculationSearchFragment,
        alignments: AlignmentMap,
    ) -> CalculationSearchFragment:
        template_page, student_page, alignment = alignments[fragment.page_number]
        if (
            str(template_page["id"]) != fragment.template_page_id
            or str(student_page["id"]) != fragment.student_page_id
            or str(student_page["alignment_revision_id"]) != fragment.alignment_revision_id
        ):
            raise ValueError("calculation search fragment page binding changed")
        crop_box = self._calculation_parent_pixel_box(fragment, template_page)
        region = AnswerRegion.rectangle(
            fragment.fragment_key,
            fragment.fragment_key,
            fragment.page_number,
            *crop_box,
        )
        self._require_calculation_region_visible(
            region,
            alignment,
            issue_code="CALCULATION_SEARCH_FRAGMENT_CLIPPED",
        )
        student_path = resolve_data_path(
            self.settings,
            str(student_page["original_image_path"]),
        )
        extracted = extract_answer_regions(
            student_path,
            [region],
            alignment,
            padding=0,
        )[0]
        template_path = resolve_data_path(self.settings, str(template_page["image_path"]))
        with Image.open(template_path) as opened:
            template_crop = opened.convert("RGB").crop(crop_box)
        return fragment.with_images(
            template_image=self._jpeg_bytes(template_crop),
            student_image=self._jpeg_bytes(extracted.image.convert("RGB")),
        )

    def _prepare_calculation_evidence(
        self,
        question: dict[str, Any],
        fragment: CalculationSearchFragment,
        alignments: AlignmentMap,
        *,
        evidence_kind: str,
        candidate: LocalizedCalculationRegion | None,
        batch_index: int,
        attempt_id: str,
        confidence: float,
        issues: list[str],
        sort_order: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        template_page, student_page, alignment = alignments[fragment.page_number]
        parent_left, parent_top, parent_right, parent_bottom = (
            self._calculation_parent_pixel_box(fragment, template_page)
        )
        page_width = int(template_page["width"])
        page_height = int(template_page["height"])
        if candidate is None:
            left, top, right, bottom = (
                parent_left,
                parent_top,
                parent_right,
                parent_bottom,
            )
            model_bbox: list[float] | None = None
            model_candidate_index: int | None = None
        else:
            candidate_left = candidate.x * page_width
            candidate_top = candidate.y * page_height
            candidate_right = (candidate.x + candidate.width) * page_width
            candidate_bottom = (candidate.y + candidate.height) * page_height
            epsilon = 1e-9
            left = max(parent_left, math.floor(candidate_left + epsilon) - 4)
            top = max(parent_top, math.floor(candidate_top + epsilon) - 4)
            right = min(parent_right, math.ceil(candidate_right - epsilon) + 4)
            bottom = min(parent_bottom, math.ceil(candidate_bottom - epsilon) + 4)
            model_bbox = list(candidate.model_bbox)
            model_candidate_index = candidate.model_candidate_index
        if right <= left or bottom <= top:
            raise ValueError("localized calculation evidence has no bounded pixel area")

        evidence_id = uuid.uuid4().hex
        answer_region = AnswerRegion.rectangle(
            str(question["id"]),
            evidence_id,
            fragment.page_number,
            left,
            top,
            right,
            bottom,
        )
        mapped_area, visible_ratio, out_of_bounds_ratio = (
            self._require_calculation_region_visible(
                answer_region,
                alignment,
                issue_code="CALCULATION_EVIDENCE_REGION_CLIPPED",
            )
        )
        student_path = resolve_data_path(
            self.settings,
            str(student_page["original_image_path"]),
        )
        extracted = extract_answer_regions(
            student_path,
            [answer_region],
            alignment,
            padding=0,
        )[0]
        template_path = resolve_data_path(self.settings, str(template_page["image_path"]))
        with Image.open(template_path) as opened:
            template_crop = opened.convert("RGB").crop((left, top, right, bottom))

        template_box = {
            "x": float(left),
            "y": float(top),
            "width": float(right - left),
            "height": float(bottom - top),
        }
        student_box = self._box_dict(extracted.mapping.visible_original_page_bbox)
        template_normalized = {
            "x": left / page_width,
            "y": top / page_height,
            "width": (right - left) / page_width,
            "height": (bottom - top) / page_height,
        }
        prepared = {
            "evidence_id": evidence_id,
            "page_number": fragment.page_number,
            "template_image": self._jpeg_bytes(template_crop),
            "student_image": self._jpeg_bytes(extracted.image.convert("RGB")),
        }
        saved = {
            "id": evidence_id,
            "sort_order": sort_order,
            "template_page_id": fragment.template_page_id,
            "student_page_id": fragment.student_page_id,
            "template_box": template_box,
            "student_box": student_box,
        }
        snapshot = {
            "evidenceId": evidence_id,
            "evidenceKind": evidence_kind,
            "fragmentKey": fragment.fragment_key,
            "templatePageId": fragment.template_page_id,
            "studentPageId": fragment.student_page_id,
            "alignmentRevisionId": fragment.alignment_revision_id,
            "batchIndex": batch_index,
            "attemptId": attempt_id,
            "modelCandidateIndex": model_candidate_index,
            "modelBbox": model_bbox,
            "modelBboxCoordinateSpace": "fragment_0_1000_ltrb",
            "templateBboxNormalized": template_normalized,
            "templateBboxPx": template_box,
            "studentBboxPx": student_box,
            "studentPolygonPx": extracted.mapping.original_page_polygon.as_dicts(),
            "studentMappedAreaPx": mapped_area,
            "studentVisibleRatio": visible_ratio,
            "studentOutOfBoundsRatio": out_of_bounds_ratio,
            "confidence": confidence,
            "issues": list(dict.fromkeys(issues)),
        }
        return prepared, saved, snapshot

    def _require_calculation_region_visible(
        self,
        region: AnswerRegion,
        alignment: AlignmentResult,
        *,
        issue_code: str,
    ) -> tuple[float, float, float]:
        """Reject a locally clipped region even when whole-page alignment is reliable."""

        try:
            mapped = alignment.template_to_student.map_polygon(region.template_polygon)
            student_bounds = Bounds(
                0.0,
                0.0,
                float(alignment.student_size.width),
                float(alignment.student_size.height),
            )
            mapped_area = mapped.area
            visible_ratio = polygon_visible_ratio(mapped, student_bounds)
            out_of_bounds_ratio = polygon_out_of_bounds_ratio(mapped, student_bounds)
        except (OverflowError, TypeError, ValueError) as error:
            raise AppError(
                422,
                issue_code,
                "The calculation evidence mapping could not be measured safely.",
                {"reason": type(error).__name__},
            ) from error
        if (
            not math.isfinite(mapped_area)
            or mapped_area < self.settings.mapping_min_polygon_area_px
            or visible_ratio + 1e-9 < self.settings.mapping_min_visible_ratio
            or out_of_bounds_ratio
            > self.settings.mapping_max_out_of_bounds_ratio + 1e-9
        ):
            raise AppError(
                422,
                issue_code,
                "The calculation evidence mapping is locally clipped or out of bounds.",
                {
                    "mappedAreaPx": mapped_area,
                    "minimumAreaPx": self.settings.mapping_min_polygon_area_px,
                    "visibleRatio": visible_ratio,
                    "minimumVisibleRatio": self.settings.mapping_min_visible_ratio,
                    "outOfBoundsRatio": out_of_bounds_ratio,
                    "maximumOutOfBoundsRatio": (
                        self.settings.mapping_max_out_of_bounds_ratio
                    ),
                },
            )
        return mapped_area, visible_ratio, out_of_bounds_ratio

    def _prepare_response_regions(
        self,
        question: dict[str, Any],
        raw_regions: list[dict[str, Any]],
        alignments: AlignmentMap,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        prepared: list[dict[str, Any]] = []
        saved_regions: list[dict[str, Any]] = []
        low_quality = False
        for index, raw_region in enumerate(raw_regions):
            if int(raw_region.get("page_number", 0)) not in alignments:
                continue
            template_page, student_page, alignment, region = self._region(
                question,
                raw_region,
                index,
                alignments,
            )
            student_path = resolve_data_path(
                self.settings,
                str(student_page["original_image_path"]),
            )
            extracted = extract_answer_regions(
                student_path,
                [region],
                alignment,
                padding=4,
            )[0]
            template_path = resolve_data_path(
                self.settings,
                str(template_page["image_path"]),
            )
            with Image.open(template_path) as opened:
                template_crop = opened.convert("RGB").crop(extracted.template_crop_box)
            evidence_id = uuid.uuid4().hex
            prepared.append(
                {
                    "evidence_id": evidence_id,
                    "page_number": region.page_number,
                    "template_image": self._jpeg_bytes(template_crop),
                    "student_image": self._jpeg_bytes(extracted.image.convert("RGB")),
                }
            )
            saved_regions.append(
                {
                    "id": evidence_id,
                    "sort_order": index,
                    "template_page_id": template_page["id"],
                    "student_page_id": student_page["id"],
                    "template_box": self._box_dict(region.template_polygon.bounds),
                    "student_box": self._box_dict(
                        extracted.mapping.visible_original_page_bbox
                    ),
                }
            )
            low_quality = low_quality or not alignment.quality.is_reliable
        return prepared, saved_regions, low_quality

    @staticmethod
    def _unconfigured_fill_response(
        question: dict[str, Any],
        issue: str,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "question_id": question["id"],
            "question_number": str(question["number"]),
            "recognized_text": "",
            "confidence": 0.0,
            "recognition_model_id": None,
            "raw_recognition": {
                "notForFillScoring": True,
                "summaryOnly": True,
                "issues": [{"code": issue}],
                "usage": {},
            },
            "status": "needs_review",
            "regions": [],
            "blank_config_version_id": None,
            "blanks": [],
        }

    async def _recognize_fill_response(
        self,
        question: dict[str, Any],
        config: dict[str, Any],
        prepared: list[dict[str, Any]],
        saved_regions: list[dict[str, Any]],
        low_quality: bool,
    ) -> dict[str, Any]:
        response_id = uuid.uuid4().hex
        result, raw, usage = await self.recognition.recognize_keyed_fill_response(
            {
                "id": str(question["id"]),
                "type": "fill_blank",
                "stem": str(question["stem"]),
                "options": json_loads(question.get("options_json"), []),
            },
            [
                {
                    "blankKey": definition["blankKey"],
                    "anchor": definition["anchor"],
                }
                for definition in config["definitions"]
            ],
            prepared,
            frame_set_id=str(question["frame_set_id"]),
            blank_config_version_id=str(config["id"]),
        )
        expected_keys = [
            str(definition["blankKey"]) for definition in config["definitions"]
        ]
        raw_answers = result.get("answers", [])
        answers = raw_answers if isinstance(raw_answers, list) else []
        answer_by_key = {
            str(item.get("blankKey")): item
            for item in answers
            if isinstance(item, dict) and isinstance(item.get("blankKey"), str)
        }
        structurally_valid = (
            result.get("status") == "recognized"
            and len(answer_by_key) == len(answers)
            and set(answer_by_key) == set(expected_keys)
        )
        result_issues = result.get("issues", [])
        issues = list(result_issues) if isinstance(result_issues, list) else []
        if not structurally_valid:
            issues.append(
                {
                    "code": "fill_response_key_mismatch",
                    "expectedKeys": expected_keys,
                    "actualKeys": sorted(answer_by_key),
                }
            )
        blank_rows: list[dict[str, Any]] = []
        if structurally_valid:
            allowed_evidence = {str(region["id"]) for region in saved_regions}
            definitions_by_key = {
                str(definition["blankKey"]): definition
                for definition in config["definitions"]
            }
            for blank_key in expected_keys:
                item = answer_by_key[blank_key]
                item_issues = [
                    str(value)
                    for value in item.get("issues", [])
                    if isinstance(value, str) and value.strip()
                ]
                evidence_refs = [
                    str(value)
                    for value in item.get("evidenceRefs", [])
                    if isinstance(value, str) and value in allowed_evidence
                ]
                if not evidence_refs:
                    item_issues.append("evidence_refs_missing")
                confidence = float(item.get("confidence", 0.0))
                blank_rows.append(
                    {
                        "id": uuid.uuid4().hex,
                        "blank_definition_id": definitions_by_key[blank_key]["id"],
                        "blank_key": blank_key,
                        "recognized_text": str(item.get("recognizedText", "")),
                        "is_blank": bool(item.get("isBlank", False)),
                        "confidence": confidence,
                        "status": (
                            "needs_review"
                            if low_quality or item_issues or confidence < 0.75
                            else "recognized"
                        ),
                        "issues": list(dict.fromkeys(item_issues)),
                        "evidence_refs": list(dict.fromkeys(evidence_refs)),
                        "raw_item": item,
                    }
                )
        summary = "；".join(
            f"{item['blank_key']}={'∅' if item['is_blank'] else item['recognized_text']}"
            for item in blank_rows
        )
        confidence = min(
            (float(item["confidence"]) for item in blank_rows),
            default=0.0,
        )
        status = (
            "needs_review"
            if not structurally_valid
            or low_quality
            or any(item["status"] == "needs_review" for item in blank_rows)
            else "recognized"
        )
        return {
            "id": response_id,
            "question_id": question["id"],
            "question_number": str(question["number"]),
            "recognized_text": summary,
            "confidence": confidence,
            "recognition_model_id": self.settings.dashscope_model,
            "raw_recognition": {
                "notForFillScoring": True,
                "summaryOnly": True,
                "keyedStatus": result.get("status"),
                "issues": issues,
                "attemptCount": result.get("attemptCount"),
                "raw": raw,
                "usage": usage,
            },
            "status": status,
            "regions": saved_regions,
            "blank_config_version_id": str(config["id"]),
            "blanks": blank_rows,
        }

    def _commit_results(
        self,
        processing_revision_id: str,
        submission_id: str,
        task_id: str,
        frame_set_id: str,
        frame_content_hash: str,
        blank_config_ids: dict[str, str | None],
        pages_are_new: bool,
        student_pages: list[dict[str, Any]],
        responses: list[dict[str, Any]],
        question_regions: list[dict[str, Any]],
        missing_template_pages: list[int],
        mapping_ready: bool,
        mapping_blockers: list[dict[str, object]],
    ) -> None:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            revision = connection.execute(
                """SELECT r.*,s.current_processing_revision_id
                   FROM student_processing_revisions r
                   JOIN student_submissions s ON s.id=r.submission_id
                   WHERE r.id=? AND r.submission_id=?""",
                (processing_revision_id, submission_id),
            ).fetchone()
            if revision is None:
                return
            stale_reasons: list[str] = []
            if (
                not revision["is_current"]
                or revision["status"] != "recognizing"
                or revision["current_processing_revision_id"] != processing_revision_id
            ):
                stale_reasons.append("processing_pointer_changed")
            current_frame = connection.execute(
                """SELECT t.current_question_frame_set_id,f.status,f.content_hash
                   FROM tasks t LEFT JOIN question_frame_sets f
                     ON f.id=t.current_question_frame_set_id WHERE t.id=?""",
                (task_id,),
            ).fetchone()
            if (
                current_frame is None
                or str(current_frame["current_question_frame_set_id"] or "") != frame_set_id
                or current_frame["status"] != "confirmed"
                or str(current_frame["content_hash"] or "") != frame_content_hash
            ):
                stale_reasons.append("frame_set_changed")
            if blank_config_ids:
                placeholders = ",".join("?" for _question_id in blank_config_ids)
                config_rows = connection.execute(
                    f"""SELECT question_id,current_blank_config_version_id
                        FROM question_grading_configs
                        WHERE question_id IN ({placeholders})""",
                    tuple(blank_config_ids),
                ).fetchall()
                current_configs: dict[str, str | None] = {
                    question_id: None for question_id in blank_config_ids
                }
                current_configs.update(
                    {
                        str(row["question_id"]): (
                            str(row["current_blank_config_version_id"])
                            if row["current_blank_config_version_id"]
                            else None
                        )
                        for row in config_rows
                    }
                )
                if current_configs != blank_config_ids:
                    stale_reasons.append("blank_config_changed")
            existing_alignments = connection.execute(
                """SELECT id FROM student_page_alignment_revisions
                   WHERE processing_revision_id=? AND is_current=1 LIMIT 1""",
                (processing_revision_id,),
            ).fetchone()
            if existing_alignments is not None:
                stale_reasons.append("alignment_revision_changed")
            persisted_pages = connection.execute(
                """SELECT id,page_number,original_image_path,width,height,sha256
                   FROM student_pages WHERE submission_id=? ORDER BY page_number""",
                (submission_id,),
            ).fetchall()
            if pages_are_new:
                if persisted_pages:
                    stale_reasons.append("student_pages_changed")
            else:
                persisted_identity = [
                    (
                        str(row["id"]),
                        int(row["page_number"]),
                        str(row["original_image_path"]),
                        int(row["width"]),
                        int(row["height"]),
                        str(row["sha256"]),
                    )
                    for row in persisted_pages
                ]
                planned_identity = [
                    (
                        str(page["id"]),
                        int(page["page_number"]),
                        str(page["original_image_path"]),
                        int(page["width"]),
                        int(page["height"]),
                        str(page["sha256"]),
                    )
                    for page in student_pages
                ]
                if persisted_identity != planned_identity:
                    stale_reasons.append("student_pages_changed")
            alignment_ids = {
                str(page["id"]): str(page["alignment_revision_id"])
                for page in student_pages
            }
            if any(
                str(region.get("alignment_revision_id"))
                != alignment_ids.get(str(region.get("student_page_id")))
                for region in question_regions
            ):
                stale_reasons.append("mapping_alignment_mismatch")
            if stale_reasons:
                self._mark_abandoned(
                    connection,
                    revision,
                    ",".join(dict.fromkeys(stale_reasons)),
                    detach_current=(
                        revision["current_processing_revision_id"] == processing_revision_id
                    ),
                )
                self.database.audit(
                    connection,
                    task_id,
                    "student_processing_revision_abandoned",
                    "system",
                    {
                        "submissionId": submission_id,
                        "processingRevisionId": processing_revision_id,
                        "reasons": list(dict.fromkeys(stale_reasons)),
                    },
                )
                return

            if pages_are_new:
                for page in student_pages:
                    connection.execute(
                        """INSERT INTO student_pages(
                             id,submission_id,page_number,original_image_path,width,height,sha256,
                             template_page_id,alignment_transform_json,alignment_quality,
                             alignment_method,alignment_status,created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            page["id"],
                            submission_id,
                            page["page_number"],
                            page["original_image_path"],
                            page["width"],
                            page["height"],
                            page["sha256"],
                            page["template_page_id"],
                            json_dumps(page["alignment_transform"]),
                            page["alignment_quality"],
                            page["alignment_method"],
                            page["alignment_status"],
                            timestamp,
                            timestamp,
                        ),
                    )
            for page in student_pages:
                alignment_issues = list(
                    dict.fromkeys(
                        [
                            *(
                                ["low_quality_alignment"]
                                if page["alignment_status"] != "aligned"
                                else []
                            ),
                            *[
                                str(warning)
                                for warning in page["alignment_metrics"].get("warnings", [])
                                if str(warning).strip()
                            ],
                        ]
                    )
                )
                connection.execute(
                    """INSERT INTO student_page_alignment_revisions(
                         id,processing_revision_id,student_page_id,revision_number,
                         template_page_id,transform_json,quality,method,status,
                         control_points_json,metrics_json,source,is_current,issues_json,
                         created_by,created_at,updated_at
                       ) VALUES(?,?,?,1,?,?,?,?,?,'[]',?,'model',1,?,?,?,?)""",
                    (
                        page["alignment_revision_id"],
                        processing_revision_id,
                        page["id"],
                        page["alignment_template_page_id"],
                        json_dumps(page["alignment_revision_transform"]),
                        page["alignment_quality"],
                        page["alignment_method"],
                        page["alignment_status"],
                        json_dumps(page["alignment_metrics"]),
                        json_dumps(alignment_issues),
                        f"model:{self.settings.dashscope_model}",
                        timestamp,
                        timestamp,
                    ),
                )
            for region in question_regions:
                connection.execute(
                    """INSERT INTO student_question_regions(
                         id,submission_id,question_id,processing_revision_id,frame_set_id,
                         frame_region_id,alignment_revision_id,sort_order,template_page_id,
                         student_page_id,template_region_json,student_polygon_json,
                         student_bbox_json,status,issues_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        region["id"],
                        submission_id,
                        region["question_id"],
                        processing_revision_id,
                        region["frame_set_id"],
                        region["frame_region_id"],
                        region["alignment_revision_id"],
                        region["sort_order"],
                        region["template_page_id"],
                        region["student_page_id"],
                        json_dumps(region["template_region"]),
                        json_dumps(region["student_polygon"]),
                        json_dumps(region["student_bbox"]),
                        region["status"],
                        json_dumps(region["issues"]),
                        timestamp,
                        timestamp,
                    ),
                )
            self._persist_responses(
                connection,
                submission_id,
                processing_revision_id,
                frame_set_id,
                responses,
                timestamp,
            )
            mapping_needs_review = (
                not mapping_ready
                or not question_regions
            )
            recognition_needs_review = any(
                response["status"] == "needs_review" for response in responses
            )
            processing_status = (
                "mapping_needs_review"
                if mapping_needs_review
                else "recognition_needs_review"
                if recognition_needs_review
                else "ready"
            )
            revision_issues: list[dict[str, object]] = []
            revision_issues.extend(mapping_blockers)
            if not mapping_ready:
                revision_issues.append(
                    self._revision_issue(
                        "question_mapping_not_ready",
                        "棰樻鏄犲皠涓嶅畬鏁存垨璐ㄩ噺涓嶈冻锛屾湭璋冪敤瀛︾敓绛旀璇嗗埆妯″瀷",
                        details={"missingTemplatePages": missing_template_pages},
                    )
                )
            if missing_template_pages:
                revision_issues.append(
                    self._revision_issue(
                        "student_pages_partial",
                        "本次只上传了部分答卷",
                        details={"missingTemplatePages": missing_template_pages},
                    )
                )
            connection.execute(
                """UPDATE student_processing_revisions SET status=?,issues_json=?,
                   finished_at=?,updated_at=? WHERE id=? AND is_current=1""",
                (
                    processing_status,
                    json_dumps(revision_issues),
                    timestamp,
                    timestamp,
                    processing_revision_id,
                ),
            )
            updated = connection.execute(
                """UPDATE student_submissions SET page_count=?,status='ready',error_code=NULL,
                   error_message=NULL,question_region_status=?,
                   question_region_error_code=?,question_region_error_message=?,
                   updated_at=? WHERE id=? AND current_processing_revision_id=?""",
                (
                    len(student_pages),
                    "needs_review"
                    if not mapping_ready or not question_regions
                    else "ready",
                    "STUDENT_PAGES_PARTIAL" if missing_template_pages else None,
                    (
                        "本次只上传了部分答卷，缺少空白卷第 "
                        + "、".join(str(page) for page in missing_template_pages)
                        + " 页"
                        if missing_template_pages
                        else None
                    ),
                    timestamp,
                    submission_id,
                    processing_revision_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("processing revision pointer changed inside commit transaction")
            self.database.audit(
                connection,
                task_id,
                "student_submission_ready",
                "system",
                {
                    "submissionId": submission_id,
                    "processingRevisionId": processing_revision_id,
                    "frameSetId": frame_set_id,
                    "questionCount": len(responses),
                    "missingTemplatePages": missing_template_pages,
                },
            )

    def _commit_existing_revision_recognition(
        self,
        processing_revision_id: str,
        submission_id: str,
        task_id: str,
        frame_set_id: str,
        frame_content_hash: str,
        blank_config_ids: dict[str, str | None],
        responses: list[dict[str, Any]],
        persisted_regions: list[dict[str, Any]],
        alignment_ids: set[str],
        missing_template_pages: list[int],
        mapping_ready: bool,
        mapping_blockers: list[dict[str, object]],
    ) -> None:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            revision = connection.execute(
                """SELECT r.*,s.current_processing_revision_id
                   FROM student_processing_revisions r
                   JOIN student_submissions s ON s.id=r.submission_id
                   WHERE r.id=? AND r.submission_id=?""",
                (processing_revision_id, submission_id),
            ).fetchone()
            if revision is None or revision["status"] != "recognizing":
                return
            stale_reasons: list[str] = []
            if (
                not revision["is_current"]
                or revision["source"] != "teacher"
                or revision["current_processing_revision_id"] != processing_revision_id
            ):
                stale_reasons.append("processing_pointer_changed")
            current_frame = connection.execute(
                """SELECT t.current_question_frame_set_id,f.status,f.content_hash
                   FROM tasks t LEFT JOIN question_frame_sets f
                     ON f.id=t.current_question_frame_set_id WHERE t.id=?""",
                (task_id,),
            ).fetchone()
            if (
                current_frame is None
                or str(current_frame["current_question_frame_set_id"] or "") != frame_set_id
                or current_frame["status"] != "confirmed"
                or str(current_frame["content_hash"] or "") != frame_content_hash
            ):
                stale_reasons.append("frame_set_changed")
            if blank_config_ids:
                placeholders = ",".join("?" for _question_id in blank_config_ids)
                config_rows = connection.execute(
                    f"""SELECT question_id,current_blank_config_version_id
                        FROM question_grading_configs
                        WHERE question_id IN ({placeholders})""",
                    tuple(blank_config_ids),
                ).fetchall()
                current_configs: dict[str, str | None] = {
                    question_id: None for question_id in blank_config_ids
                }
                current_configs.update(
                    {
                        str(row["question_id"]): (
                            str(row["current_blank_config_version_id"])
                            if row["current_blank_config_version_id"]
                            else None
                        )
                        for row in config_rows
                    }
                )
                if current_configs != blank_config_ids:
                    stale_reasons.append("blank_config_changed")
            current_alignment_ids = {
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM student_page_alignment_revisions
                       WHERE processing_revision_id=? AND is_current=1""",
                    (processing_revision_id,),
                ).fetchall()
            }
            if current_alignment_ids != alignment_ids:
                stale_reasons.append("alignment_revision_changed")
            captured_region_snapshot = sorted(
                (
                    str(row["question_id"]),
                    str(row["frame_region_id"]),
                    str(row["status"]),
                    str(row["alignment_revision_id"]),
                )
                for row in persisted_regions
            )
            current_region_snapshot = sorted(
                (
                    str(row["question_id"]),
                    str(row["frame_region_id"]),
                    str(row["status"]),
                    str(row["alignment_revision_id"]),
                )
                for row in connection.execute(
                    """SELECT question_id,frame_region_id,status,alignment_revision_id
                       FROM student_question_regions WHERE processing_revision_id=?""",
                    (processing_revision_id,),
                ).fetchall()
            )
            if current_region_snapshot != captured_region_snapshot:
                stale_reasons.append("question_mapping_changed")
            if stale_reasons:
                self._mark_abandoned(
                    connection,
                    revision,
                    ",".join(dict.fromkeys(stale_reasons)),
                    detach_current=(
                        revision["current_processing_revision_id"] == processing_revision_id
                    ),
                )
                self.database.audit(
                    connection,
                    task_id,
                    "student_processing_revision_abandoned",
                    "system",
                    {
                        "submissionId": submission_id,
                        "processingRevisionId": processing_revision_id,
                        "reasons": list(dict.fromkeys(stale_reasons)),
                    },
                )
                return
            existing_response = connection.execute(
                "SELECT id FROM student_responses WHERE processing_revision_id=? LIMIT 1",
                (processing_revision_id,),
            ).fetchone()
            if existing_response is not None:
                return
            self._persist_responses(
                connection,
                submission_id,
                processing_revision_id,
                frame_set_id,
                responses,
                timestamp,
            )
            recognition_needs_review = any(
                response["status"] == "needs_review" for response in responses
            )
            processing_status = (
                "mapping_needs_review"
                if not mapping_ready
                else "recognition_needs_review"
                if recognition_needs_review
                else "ready"
            )
            revision_issues = [*mapping_blockers]
            if not mapping_ready:
                revision_issues.append(
                    self._revision_issue(
                        "question_mapping_not_ready",
                        "人工配准后的题框映射仍不完整或质量不足，未调用答案识别模型",
                        details={"missingTemplatePages": missing_template_pages},
                    )
                )
            connection.execute(
                """UPDATE student_processing_revisions SET status=?,issues_json=?,
                   finished_at=?,updated_at=? WHERE id=? AND is_current=1
                   AND status='recognizing'""",
                (
                    processing_status,
                    json_dumps(revision_issues),
                    timestamp,
                    timestamp,
                    processing_revision_id,
                ),
            )
            updated = connection.execute(
                """UPDATE student_submissions SET status='ready',error_code=?,error_message=?,
                   question_region_status=?,question_region_error_code=?,
                   question_region_error_message=?,updated_at=?
                   WHERE id=? AND current_processing_revision_id=?""",
                (
                    "ALIGNMENT_MAPPING_REVIEW_REQUIRED" if not mapping_ready else None,
                    "人工配准后的题框映射仍需复核" if not mapping_ready else None,
                    "needs_review" if not mapping_ready else "ready",
                    "ALIGNMENT_MAPPING_REVIEW_REQUIRED" if not mapping_ready else None,
                    "人工配准后的题框映射仍需复核" if not mapping_ready else None,
                    timestamp,
                    submission_id,
                    processing_revision_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("processing revision pointer changed inside recognition commit")
            self.database.audit(
                connection,
                task_id,
                "student_teacher_alignment_recognition_finished",
                "system",
                {
                    "submissionId": submission_id,
                    "processingRevisionId": processing_revision_id,
                    "frameSetId": frame_set_id,
                    "status": processing_status,
                    "questionCount": len(responses),
                },
            )

    @staticmethod
    def _persist_responses(
        connection: Any,
        submission_id: str,
        processing_revision_id: str,
        frame_set_id: str,
        responses: list[dict[str, Any]],
        timestamp: str,
    ) -> None:
        for response in responses:
            StudentPipeline._validate_localization_evidence(
                connection,
                processing_revision_id,
                response,
            )
            connection.execute(
                """INSERT INTO student_responses(
                     id,submission_id,question_id,processing_revision_id,frame_set_id,
                     blank_config_version_id,question_number,recognized_text,confidence,
                     recognition_model_id,raw_recognition_json,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    response["id"],
                    submission_id,
                    response["question_id"],
                    processing_revision_id,
                    frame_set_id,
                    response["blank_config_version_id"],
                    response["question_number"],
                    response["recognized_text"],
                    response["confidence"],
                    response["recognition_model_id"],
                    json_dumps(response["raw_recognition"]),
                    response["status"],
                    timestamp,
                    timestamp,
                ),
            )
            for region in response["regions"]:
                connection.execute(
                    """INSERT INTO student_response_regions(
                         id,student_response_id,sort_order,template_page_id,student_page_id,
                         coordinate_space,template_bbox_json,student_bbox_json,created_at
                       ) VALUES(?,?,?,?,?,'pixel',?,?,?)""",
                    (
                        region["id"],
                        response["id"],
                        region["sort_order"],
                        region["template_page_id"],
                        region["student_page_id"],
                        json_dumps(region["template_box"]),
                        json_dumps(region["student_box"]),
                        timestamp,
                    ),
                )
            localization = response.get("raw_recognition", {}).get("localization")
            if isinstance(localization, dict) and localization.get("schemaVersion") == 1:
                persisted_ids = {
                    str(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM student_response_regions WHERE student_response_id=?",
                        (response["id"],),
                    ).fetchall()
                }
                expected_ids = {str(region["id"]) for region in response["regions"]}
                if persisted_ids != expected_ids:
                    raise RuntimeError("persisted localization evidence IDs do not match regions")
            for blank in response["blanks"]:
                connection.execute(
                    """INSERT INTO student_blank_responses(
                         id,student_response_id,blank_definition_id,blank_key,
                         recognized_text,is_blank,confidence,status,issues_json,
                         evidence_refs_json,recognition_model_id,prompt_version,
                         frame_set_id,blank_config_version_id,processing_revision_id,
                         raw_item_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        blank["id"],
                        response["id"],
                        blank["blank_definition_id"],
                        blank["blank_key"],
                        blank["recognized_text"],
                        1 if blank["is_blank"] else 0,
                        blank["confidence"],
                        blank["status"],
                        json_dumps(blank["issues"]),
                        json_dumps(blank["evidence_refs"]),
                        response["recognition_model_id"],
                        KEYED_FILL_RESPONSE_PROMPT_VERSION,
                        frame_set_id,
                        response["blank_config_version_id"],
                        processing_revision_id,
                        json_dumps(blank["raw_item"]),
                        timestamp,
                        timestamp,
                    ),
                )

    @staticmethod
    def _validate_localization_evidence(
        connection: Any,
        processing_revision_id: str,
        response: dict[str, Any],
    ) -> None:
        """Fail the CAS commit when a v1 localization snapshot and regions diverge."""

        raw = response.get("raw_recognition")
        if not isinstance(raw, dict) or "localization" not in raw:
            return
        localization = raw.get("localization")
        if not isinstance(localization, dict) or localization.get("schemaVersion") != 1:
            raise RuntimeError("new localization snapshots must use schemaVersion 1")
        if not isinstance(localization.get("evidenceComplete"), bool):
            raise RuntimeError("localization evidenceComplete must be boolean")
        evidence = localization.get("evidence")
        if not isinstance(evidence, list):
            raise RuntimeError("localization evidence must be a list")
        region_by_id = {
            str(region.get("id") or ""): region for region in response.get("regions", [])
        }
        region_ids = list(region_by_id)
        if any(not region_id for region_id in region_ids) or len(region_ids) != len(
            response.get("regions", [])
        ):
            raise RuntimeError("student response region IDs must be unique and non-empty")
        evidence_ids: list[str] = []
        for item in evidence:
            if not isinstance(item, dict):
                raise RuntimeError("localization evidence entries must be objects")
            evidence_id = item.get("evidenceId")
            if not isinstance(evidence_id, str) or not evidence_id:
                raise RuntimeError("localization evidenceId must be a non-empty string")
            evidence_ids.append(evidence_id)
            region = region_by_id.get(evidence_id)
            if region is None:
                continue
            if item.get("evidenceKind") not in {"located_region", "blank_search_window"}:
                raise RuntimeError("localization evidenceKind is invalid")
            if (
                item.get("templatePageId") != region.get("template_page_id")
                or item.get("studentPageId") != region.get("student_page_id")
                or item.get("templateBboxPx") != region.get("template_box")
                or item.get("studentBboxPx") != region.get("student_box")
            ):
                raise RuntimeError("localization evidence geometry does not match region row")
            alignment_revision_id = item.get("alignmentRevisionId")
            if not isinstance(alignment_revision_id, str) or not alignment_revision_id:
                raise RuntimeError("localization evidence lacks alignment revision")
            alignment = connection.execute(
                """SELECT processing_revision_id,student_page_id,template_page_id,is_current
                   FROM student_page_alignment_revisions WHERE id=?""",
                (alignment_revision_id,),
            ).fetchone()
            if (
                alignment is None
                or str(alignment["processing_revision_id"]) != processing_revision_id
                or str(alignment["student_page_id"]) != str(item["studentPageId"])
                or str(alignment["template_page_id"] or "")
                != str(item["templatePageId"] or "")
                or int(alignment["is_current"]) != 1
            ):
                raise RuntimeError("localization evidence alignment revision is stale")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise RuntimeError("localization evidence IDs must be unique")
        if set(evidence_ids) != set(region_ids):
            raise RuntimeError("localization evidence IDs must match response region IDs")

    @staticmethod
    def _box_dict(bounds: Any) -> dict[str, float]:
        return {
            "x": float(bounds.left),
            "y": float(bounds.top),
            "width": float(bounds.width),
            "height": float(bounds.height),
        }

    @staticmethod
    def _jpeg_bytes(image: Image.Image) -> bytes:
        output = io.BytesIO()
        image.save(output, "JPEG", quality=92)
        return output.getvalue()
