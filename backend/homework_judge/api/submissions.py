from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

from ..alignment.geometry import Homography
from ..alignment.overrides import AlignmentOverrideService
from ..config import Settings
from ..db.database import Database, json_loads, now_iso
from ..errors import AppError
from ..files.storage import remove_submission_files, save_upload
from ..files.validation import validate_upload
from ..grading.blank_config_confirmation import ensure_task_fill_blank_configs
from ..jobs.manager import JobManager
from ..jobs.question_region_pipeline import QuestionRegionPipeline
from ..jobs.student_workflow import StudentSubmissionWorkflow
from ..review.history import require_student_processing_ready
from ..schemas import AlignmentOverrideUpdate
from ..submissions.deletion import StudentSubmissionDeletionService
from .dependencies import (
    get_database,
    get_jobs,
    get_question_region_pipeline,
    get_settings,
    get_student_workflow,
)
from .response import success

router = APIRouter()


@router.delete("/student-submissions/{submission_id}")
async def delete_student_submission(
    submission_id: str,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
) -> JSONResponse:
    value = await StudentSubmissionDeletionService(settings, database, jobs).delete(
        submission_id,
        actor=settings.teacher_name,
    )
    return success(value)


@router.put("/student-submissions/{submission_id}/pages/{student_page_id}/alignment")
async def update_student_page_alignment(
    submission_id: str,
    student_page_id: str,
    payload: AlignmentOverrideUpdate,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
    workflow: StudentSubmissionWorkflow = Depends(get_student_workflow),
) -> JSONResponse:
    value = AlignmentOverrideService(
        database,
        min_alignment_score=settings.mapping_min_alignment_score,
    ).apply(
        submission_id,
        student_page_id,
        expected_revision=payload.expectedAlignmentRevision,
        template_page_id=payload.templatePageId,
        control_points=[item.model_dump(mode="json") for item in payload.controlPoints],
        clear_override=payload.clearOverride,
        actor=settings.teacher_name,
    )
    if value["status"] == "recognition_pending":
        processing_revision_id = str(value["processingRevisionId"])
        key = f"student:{submission_id}:processing:{processing_revision_id}"
        started = await jobs.start(
            key,
            workflow.resume_recognition(submission_id),
        )
        value["recognitionStarted"] = started
        value["reused"] = not started
    else:
        value["recognitionStarted"] = False
        value["reused"] = False
    return success(value)


def _submission_value(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "task_id",
        "student_identifier",
        "student_name",
        "original_name",
        "mime_type",
        "size_bytes",
        "page_count",
        "status",
        "error_code",
        "error_message",
        "question_region_status",
        "question_region_error_code",
        "question_region_error_message",
        "created_at",
        "updated_at",
        "response_count",
        "review_count",
        "auto_grading_status",
        "auto_grading_run_id",
        "auto_grading_error_code",
        "auto_grading_error_message",
        "auto_grading_progress_current",
        "auto_grading_progress_total",
        "auto_grading_total_score",
        "auto_grading_open_review_count",
    )
    return {key: row[key] for key in keys if key in row}


def _student_page_alignment(row: dict[str, Any]) -> dict[str, Any]:
    revision_id = row.get("alignment_revision_id")
    if revision_id:
        raw_transform = json_loads(row.get("revision_transform_json"), None)
        try:
            transform = Homography.from_rows(raw_transform).inverse.as_rows()
        except (TypeError, ValueError):
            transform = None
        return {
            "direction": "student_original_to_template",
            "transform": transform,
            "quality": row.get("revision_quality"),
            "method": row.get("revision_method"),
            "status": row.get("revision_status"),
            "revisionNumber": row.get("alignment_revision_number"),
            "revisionId": revision_id,
            "source": row.get("alignment_revision_source"),
            "controlPoints": json_loads(row.get("control_points_json"), []),
        }
    return {
        "direction": "student_original_to_template",
        "transform": json_loads(row.get("alignment_transform_json"), None),
        "quality": row.get("alignment_quality"),
        "method": row.get("alignment_method"),
        "status": row.get("alignment_status"),
        "revisionNumber": None,
        "revisionId": None,
        "source": None,
        "controlPoints": [],
    }


def _processing_revision_value(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "submissionId": row["submission_id"],
        "revisionNumber": row["revision_number"],
        "frameSetId": row["frame_set_id"],
        "status": row["status"],
        "inputHash": row["input_hash"],
        "isCurrent": bool(row["is_current"]),
        "source": row["source"],
        "issues": json_loads(row["issues_json"], []),
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


@router.post("/tasks/{task_id}/student-submissions")
async def create_student_submission(
    task_id: str,
    file: Annotated[UploadFile, File()],
    studentIdentifier: Annotated[str, Form()] = "",
    studentName: Annotated[str, Form()] = "",
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    workflow: StudentSubmissionWorkflow = Depends(get_student_workflow),
) -> JSONResponse:
    task = database.fetchone("SELECT id,status FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    if task["status"] not in {"review_pending", "completed"}:
        raise AppError(409, "TEMPLATE_NOT_READY", "试卷仍在处理，暂不能上传学生答卷")
    template = database.fetchone(
        """SELECT COUNT(*) AS count FROM pages p JOIN documents d ON d.id=p.document_id
           WHERE d.task_id=? AND d.role='exam'""",
        (task_id,),
    )
    questions = database.fetchone(
        "SELECT COUNT(*) AS count FROM questions WHERE task_id=? AND is_duplicate=0",
        (task_id,),
    )
    if not template or not template["count"] or not questions or not questions["count"]:
        raise AppError(409, "TEMPLATE_NOT_READY", "空白试卷页面和题目尚未准备完成")
    ensure_task_fill_blank_configs(
        database,
        task_id,
        "system:student_processing",
        "student_processing",
        allow_partial=True,
    )
    require_student_processing_ready(database, task_id)

    submission_id = uuid.uuid4().hex
    saved = None
    try:
        saved = await save_upload(
            settings,
            task_id,
            f"students/{submission_id}",
            file,
        )
        mime_type = validate_upload(settings, saved)
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO student_submissions(
                     id,task_id,student_identifier,student_name,original_name,mime_type,
                     size_bytes,sha256,relative_path,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,'uploaded',?,?)""",
                (
                    submission_id,
                    task_id,
                    studentIdentifier.strip(),
                    studentName.strip(),
                    saved.original_name,
                    mime_type,
                    saved.size_bytes,
                    saved.sha256,
                    saved.relative_path,
                    timestamp,
                    timestamp,
                ),
            )
            database.audit(
                connection,
                task_id,
                "student_submission_uploaded",
                settings.teacher_name,
                {
                    "submissionId": submission_id,
                    "studentIdentifier": studentIdentifier.strip(),
                    "studentName": studentName.strip(),
                },
            )
        started = await jobs.start(
            f"student:{submission_id}",
            workflow.process(submission_id),
        )
        return success(
            {
                "submissionId": submission_id,
                "status": "uploaded",
                "reused": not started,
            },
            202,
        )
    except Exception:
        if saved is not None:
            database.execute(
                "DELETE FROM student_submissions WHERE id=?",
                (submission_id,),
            )
            remove_submission_files(settings, task_id, submission_id)
        raise


@router.get("/tasks/{task_id}/student-submissions")
def list_student_submissions(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    if not database.fetchone("SELECT id FROM tasks WHERE id=?", (task_id,)):
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    rows = database.fetchall(
        """SELECT s.*,
           (SELECT COUNT(*) FROM student_responses r JOIN questions q ON q.id=r.question_id
              WHERE r.submission_id=s.id AND q.is_duplicate=0) response_count,
           (SELECT COUNT(*) FROM student_responses r
              JOIN questions q ON q.id=r.question_id
              WHERE r.submission_id=s.id AND r.status='needs_review'
                AND q.is_duplicate=0) review_count,
           (SELECT a.status FROM student_auto_grading_attempts a
              WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1)
              auto_grading_status,
           (SELECT a.grading_run_id FROM student_auto_grading_attempts a
              WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1)
              auto_grading_run_id,
           (SELECT a.error_code FROM student_auto_grading_attempts a
              WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1)
              auto_grading_error_code,
           (SELECT a.error_message FROM student_auto_grading_attempts a
              WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1)
              auto_grading_error_message,
           (SELECT g.progress_current FROM grading_runs g
              WHERE g.id=(SELECT a.grading_run_id FROM student_auto_grading_attempts a
                WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1))
              auto_grading_progress_current,
           (SELECT g.progress_total FROM grading_runs g
              WHERE g.id=(SELECT a.grading_run_id FROM student_auto_grading_attempts a
                WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1))
              auto_grading_progress_total,
           (SELECT g.total_score FROM grading_runs g
              WHERE g.id=(SELECT a.grading_run_id FROM student_auto_grading_attempts a
                WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1))
              auto_grading_total_score,
           (SELECT g.open_review_count FROM grading_runs g
              WHERE g.id=(SELECT a.grading_run_id FROM student_auto_grading_attempts a
                WHERE a.submission_id=s.id ORDER BY a.updated_at DESC LIMIT 1))
              auto_grading_open_review_count
           FROM student_submissions s WHERE s.task_id=? ORDER BY s.created_at DESC""",
        (task_id,),
    )
    return success([_submission_value(row) for row in rows])


@router.get("/student-submissions/{submission_id}")
def get_student_submission(
    submission_id: str,
    processingRevisionId: str | None = Query(default=None),
    database: Database = Depends(get_database),
) -> JSONResponse:
    submission = database.fetchone(
        "SELECT * FROM student_submissions WHERE id=?",
        (submission_id,),
    )
    if not submission:
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    current_processing_revision_id = submission.get("current_processing_revision_id")
    viewed_processing_revision_id = processingRevisionId or current_processing_revision_id
    if processingRevisionId:
        requested_revision = database.fetchone(
            """SELECT id FROM student_processing_revisions
               WHERE id=? AND submission_id=?""",
            (processingRevisionId, submission_id),
        )
        if not requested_revision:
            raise AppError(404, "PROCESSING_REVISION_NOT_FOUND", "处理历史不存在")
    page_rows = database.fetchall(
        """SELECT sp.*,
                  COALESCE(ar.template_page_id,sp.template_page_id) AS effective_template_page_id,
                  p.page_number AS template_page_number,
                  ar.id AS alignment_revision_id,
                  ar.revision_number AS alignment_revision_number,
                  ar.transform_json AS revision_transform_json,
                  ar.quality AS revision_quality,
                  ar.method AS revision_method,
                  ar.status AS revision_status,
                  ar.source AS alignment_revision_source,
                  ar.control_points_json
           FROM student_pages sp
           LEFT JOIN student_page_alignment_revisions ar
             ON ar.student_page_id=sp.id AND ar.processing_revision_id=?
                AND ar.is_current=1
           LEFT JOIN pages p
             ON p.id=COALESCE(ar.template_page_id,sp.template_page_id)
           WHERE sp.submission_id=? ORDER BY sp.page_number""",
        (viewed_processing_revision_id, submission_id),
    )
    pages = [
        {
            "id": row["id"],
            "pageNumber": row["page_number"],
            "width": row["width"],
            "height": row["height"],
            "templatePageId": row["effective_template_page_id"],
            "templatePageNumber": row["template_page_number"],
            "alignment": _student_page_alignment(row),
            "imageUrl": f"/api/student-pages/{row['id']}",
        }
        for row in page_rows
    ]
    response_rows = database.fetchall(
        """SELECT r.*,q.sort_order,q.question_type
           FROM student_responses r LEFT JOIN questions q ON q.id=r.question_id
           WHERE r.submission_id=? AND q.is_duplicate=0
             AND ((? IS NOT NULL AND r.processing_revision_id=?)
                  OR (? IS NULL AND r.processing_revision_id IS NULL))
           ORDER BY q.sort_order,r.question_number""",
        (
            submission_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
        ),
    )
    responses: list[dict[str, Any]] = []
    blank_responses: list[dict[str, Any]] = []
    for row in response_rows:
        region_rows = database.fetchall(
            """SELECT * FROM student_response_regions
               WHERE student_response_id=? ORDER BY sort_order""",
            (row["id"],),
        )
        raw = json_loads(row["raw_recognition_json"], {})
        blank_rows = database.fetchall(
            """SELECT * FROM student_blank_responses
               WHERE student_response_id=? ORDER BY blank_key""",
            (row["id"],),
        )
        blank_values = [
            {
                "id": blank["id"],
                "studentResponseId": blank["student_response_id"],
                "blankDefinitionId": blank["blank_definition_id"],
                "blankKey": blank["blank_key"],
                "recognizedText": blank["recognized_text"],
                "isBlank": bool(blank["is_blank"]),
                "confidence": blank["confidence"],
                "status": blank["status"],
                "issues": json_loads(blank["issues_json"], []),
                "evidenceRefs": json_loads(blank["evidence_refs_json"], []),
                "recognitionModelId": blank["recognition_model_id"],
                "promptVersion": blank["prompt_version"],
                "frameSetId": blank["frame_set_id"],
                "blankConfigVersionId": blank["blank_config_version_id"],
                "processingRevisionId": blank["processing_revision_id"],
            }
            for blank in blank_rows
        ]
        blank_responses.extend(blank_values)
        responses.append(
            {
                "id": row["id"],
                "questionId": row["question_id"],
                "questionNumber": row["question_number"],
                "questionType": row["question_type"],
                "recognizedText": row["recognized_text"],
                "confidence": row["confidence"],
                "isBlank": raw.get("isBlank", False),
                "issues": raw.get("issues", []),
                "status": row["status"],
                "processingRevisionId": row.get("processing_revision_id"),
                "frameSetId": row.get("frame_set_id"),
                "blankConfigVersionId": row.get("blank_config_version_id"),
                "blankResponses": blank_values,
                "regions": [
                    {
                        "id": region["id"],
                        "sortOrder": region["sort_order"],
                        "templatePageId": region["template_page_id"],
                        "studentPageId": region["student_page_id"],
                        "coordinateSpace": region["coordinate_space"],
                        "templateBox": json_loads(region["template_bbox_json"], {}),
                        "studentBox": json_loads(region["student_bbox_json"], {}),
                    }
                    for region in region_rows
                ],
            }
        )
    question_region_rows = database.fetchall(
        """SELECT r.*,q.detected_number,q.sort_order AS question_sort_order
           FROM student_question_regions r JOIN questions q ON q.id=r.question_id
           WHERE r.submission_id=? AND q.is_duplicate=0
             AND ((? IS NOT NULL AND r.processing_revision_id=?)
                  OR (? IS NULL AND r.processing_revision_id IS NULL))
           ORDER BY q.sort_order,r.sort_order""",
        (
            submission_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
        ),
    )
    question_regions = [
        {
            "id": row["id"],
            "questionId": row["question_id"],
            "questionNumber": row["detected_number"],
            "sortOrder": row["sort_order"],
            "processingRevisionId": row.get("processing_revision_id"),
            "frameSetId": row.get("frame_set_id"),
            "frameRegionId": row.get("frame_region_id"),
            "alignmentRevisionId": row.get("alignment_revision_id"),
            "templatePageId": row["template_page_id"],
            "studentPageId": row["student_page_id"],
            "coordinateSpace": "student_original_page_pixels",
            "templateRegion": json_loads(row["template_region_json"], {}),
            "studentPolygon": json_loads(row["student_polygon_json"], []),
            "studentBox": json_loads(row["student_bbox_json"], {}),
            "status": row["status"],
            "issues": json_loads(row["issues_json"], []),
        }
        for row in question_region_rows
    ]
    missing_rows = database.fetchall(
        """SELECT q.id FROM questions q
           WHERE q.task_id=? AND q.is_duplicate=0 AND (
             q.question_regions_json='[]' OR NOT EXISTS(
               SELECT 1 FROM student_question_regions r
               WHERE r.submission_id=? AND r.question_id=q.id
                 AND ((? IS NOT NULL AND r.processing_revision_id=?)
                      OR (? IS NULL AND r.processing_revision_id IS NULL))
             )
           ) ORDER BY q.sort_order""",
        (
            submission["task_id"],
            submission_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
            viewed_processing_revision_id,
        ),
    )
    processing_rows = database.fetchall(
        """SELECT * FROM student_processing_revisions
           WHERE submission_id=? ORDER BY revision_number DESC""",
        (submission_id,),
    )
    processing_history = [_processing_revision_value(row) for row in processing_rows]
    current_processing = next(
        (row for row in processing_history if row["id"] == viewed_processing_revision_id),
        None,
    )
    revision_summaries = []
    for revision in processing_history:
        revision_id = str(revision["id"])
        response_count = database.fetchone(
            "SELECT COUNT(*) AS count FROM student_responses WHERE processing_revision_id=?",
            (revision_id,),
        )
        region_count = database.fetchone(
            "SELECT COUNT(*) AS count FROM student_question_regions WHERE processing_revision_id=?",
            (revision_id,),
        )
        grading_count = database.fetchone(
            """SELECT COUNT(*) AS count FROM grading_runs gr
               JOIN grading_question_results gqr ON gqr.grading_run_id=gr.id
               JOIN student_responses sr ON sr.id=gqr.student_response_id
               WHERE gr.submission_id=? AND sr.processing_revision_id=?""",
            (submission_id, revision_id),
        )
        artifact_count = database.fetchone(
            """SELECT COUNT(DISTINCT ga.id) AS count FROM grading_artifacts ga
               JOIN grading_runs gr ON gr.id=ga.grading_run_id
               JOIN grading_question_results gqr ON gqr.grading_run_id=gr.id
               JOIN student_responses sr ON sr.id=gqr.student_response_id
               WHERE gr.submission_id=? AND sr.processing_revision_id=?""",
            (submission_id, revision_id),
        )
        revision_summaries.append(
            {
                **revision,
                "isHistorical": revision_id != current_processing_revision_id,
                "responseCount": int(response_count["count"]) if response_count else 0,
                "questionRegionCount": int(region_count["count"]) if region_count else 0,
                "gradingResultCount": int(grading_count["count"]) if grading_count else 0,
                "artifactCount": int(artifact_count["count"]) if artifact_count else 0,
            }
        )
    auto_attempt = database.fetchone(
        """SELECT a.*,g.progress_current AS auto_grading_progress_current,
                  g.progress_total AS auto_grading_progress_total,
                  g.total_score AS auto_grading_total_score,
                  g.open_review_count AS auto_grading_open_review_count
           FROM student_auto_grading_attempts a
           LEFT JOIN grading_runs g ON g.id=a.grading_run_id
           WHERE a.submission_id=? ORDER BY a.updated_at DESC LIMIT 1""",
        (submission_id,),
    )
    submission_value = _submission_value(submission)
    if auto_attempt:
        submission_value.update(
            {
                "auto_grading_status": auto_attempt["status"],
                "auto_grading_run_id": auto_attempt["grading_run_id"],
                "auto_grading_error_code": auto_attempt["error_code"],
                "auto_grading_error_message": auto_attempt["error_message"],
                "auto_grading_progress_current": auto_attempt[
                    "auto_grading_progress_current"
                ],
                "auto_grading_progress_total": auto_attempt["auto_grading_progress_total"],
                "auto_grading_total_score": auto_attempt["auto_grading_total_score"],
                "auto_grading_open_review_count": auto_attempt[
                    "auto_grading_open_review_count"
                ],
            }
        )
    return success(
        {
            "submission": submission_value,
            "currentProcessingRevisionId": current_processing_revision_id,
            "processingRevision": current_processing,
            "viewedProcessingRevisionId": viewed_processing_revision_id,
            "isHistoricalView": viewed_processing_revision_id != current_processing_revision_id,
            "processingHistory": revision_summaries,
            "processingRevisions": revision_summaries,
            "pages": pages,
            "responses": responses,
            "blankResponses": blank_responses,
            "questionRegionState": {
                "status": submission["question_region_status"],
                "errorCode": submission["question_region_error_code"],
                "errorMessage": submission["question_region_error_message"],
                "missingQuestionIds": [row["id"] for row in missing_rows],
            },
            "questionRegions": question_regions,
        }
    )


@router.post("/tasks/{task_id}/question-regions/process")
async def process_task_question_regions(
    task_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: QuestionRegionPipeline = Depends(get_question_region_pipeline),
) -> JSONResponse:
    task = database.fetchone("SELECT id,status FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    if task["status"] not in {"review_pending", "completed"}:
        raise AppError(409, "TEMPLATE_NOT_READY", "试卷题目尚未准备完成")
    key = f"regions:{task_id}"
    if jobs.is_running(key):
        return success({"taskId": task_id, "reused": True}, 202)
    started = await jobs.start(key, pipeline.run(task_id))
    return success({"taskId": task_id, "reused": not started}, 202)


@router.post("/student-submissions/{submission_id}/process")
async def process_student_submission(
    submission_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    workflow: StudentSubmissionWorkflow = Depends(get_student_workflow),
) -> JSONResponse:
    submission = database.fetchone(
        "SELECT id,task_id,status,current_processing_revision_id "
        "FROM student_submissions WHERE id=?",
        (submission_id,),
    )
    if not submission:
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    if submission["status"] == "ready":
        raise AppError(409, "STUDENT_SUBMISSION_ALREADY_READY", "学生答卷已有可用结果")
    task_id = str(submission["task_id"])
    ensure_task_fill_blank_configs(
        database,
        task_id,
        "system:student_processing",
        "student_processing",
        allow_partial=True,
    )
    require_student_processing_ready(database, task_id)
    current_revision = None
    if submission.get("current_processing_revision_id"):
        current_revision = database.fetchone(
            "SELECT * FROM student_processing_revisions WHERE id=?",
            (submission["current_processing_revision_id"],),
        )
    if current_revision and current_revision["status"] == "mapping_needs_review":
        raise AppError(
            409,
            "ALIGNMENT_REVIEW_REQUIRED",
            "页面映射仍有阻断问题，必须先完成页面级配准校正",
            {
                "layer": "alignment",
                "nextAction": "correct_page_alignment",
                "processingRevisionId": current_revision["id"],
                "issues": json_loads(current_revision["issues_json"], []),
            },
        )
    if (
        current_revision
        and current_revision["source"] == "teacher"
        and current_revision["status"] == "recognizing"
    ):
        key = f"student:{submission_id}:processing:{current_revision['id']}"
        if jobs.is_running(key):
            return success({"submissionId": submission_id, "reused": True}, 202)
        started = await jobs.start(key, workflow.resume_recognition(submission_id))
        return success({"submissionId": submission_id, "reused": not started}, 202)
    key = f"student:{submission_id}"
    if jobs.is_running(key):
        return success({"submissionId": submission_id, "reused": True}, 202)
    started = await jobs.start(key, workflow.process(submission_id))
    return success({"submissionId": submission_id, "reused": not started}, 202)


@router.post("/student-submissions/{submission_id}/reprocess-new-flow")
async def reprocess_student_submission_new_flow(
    submission_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    workflow: StudentSubmissionWorkflow = Depends(get_student_workflow),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Explicitly create a new processing revision without discarding history."""

    submission = database.fetchone(
        "SELECT id,task_id,current_processing_revision_id FROM student_submissions WHERE id=?",
        (submission_id,),
    )
    if not submission:
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    task_id = str(submission["task_id"])
    ensure_task_fill_blank_configs(
        database,
        task_id,
        "system:student_processing",
        "student_processing",
        allow_partial=True,
    )
    require_student_processing_ready(database, task_id)
    if submission.get("current_processing_revision_id"):
        current = database.fetchone(
            "SELECT status FROM student_processing_revisions WHERE id=?",
            (submission["current_processing_revision_id"],),
        )
        if current and current["status"] in {"aligning", "recognizing"}:
            raise AppError(
                409,
                "STUDENT_PROCESSING_ACTIVE",
                "学生答卷正在处理中，请等待当前处理完成",
            )
    key = f"student:{submission_id}:new-flow"
    if jobs.is_running(key):
        return success({"submissionId": submission_id, "reused": True}, 202)
    with database.transaction() as connection:
        database.audit(
            connection,
            str(submission["task_id"]),
            "student_reprocess_new_flow_requested",
            settings.teacher_name,
            {
                "submissionId": submission_id,
                "previousProcessingRevisionId": submission.get("current_processing_revision_id"),
            },
        )
    started = await jobs.start(key, workflow.process(submission_id))
    return success(
        {
            "submissionId": submission_id,
            "reused": not started,
            "requested": "new_flow",
        },
        202,
    )
