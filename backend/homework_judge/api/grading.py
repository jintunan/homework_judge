from __future__ import annotations

import math
from io import BytesIO
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from PIL import Image

from ..alignment import Homography, Point
from ..config import Settings
from ..db.database import Database, json_loads, now_iso
from ..errors import AppError
from ..files.storage import resolve_data_path
from ..grading.blank_config_confirmation import (
    ensure_submission_blank_configs_current,
    ensure_task_fill_blank_configs,
)
from ..grading.review import GradingReviewService
from ..jobs.grading_pipeline import GradingPipeline
from ..jobs.manager import JobManager
from ..recognition.client import DashScopeClient
from ..schemas import ErrorLocationUpdate, GradingBlankCorrection, GradingReviewResolution
from .dependencies import (
    get_database,
    get_grading_pipeline,
    get_grading_review_service,
    get_jobs,
    get_model_client,
    get_settings,
)
from .response import success

router = APIRouter()

ACTIVE_RUN_STATUSES = {
    "queued",
    "prechecking",
    "aligning",
    "segmenting",
    "recognizing",
    "grading",
    "auditing",
    "generating_annotation",
    "generating_report",
}


def _is_unresolved_score_placeholder(row: dict[str, Any]) -> bool:
    review_reasons = json_loads(row.get("review_reasons_json"), [])
    decisions = json_loads(row.get("decisions_json"), [])
    result_status = row.get("question_result_status", row.get("status"))
    return (
        row.get("question_type") == "calculation"
        and result_status == "needs_review"
        and isinstance(review_reasons, list)
        and (
            ("INVALID_MODEL_OUTPUT" in review_reasons and not decisions)
            or "MISSING_EVIDENCE" in review_reasons
        )
    )


def _run_value(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "submissionId": row["submission_id"],
        "taskId": row["task_id"],
        "processingRevisionId": row.get("processing_revision_id"),
        "triggerSource": row.get("trigger_source", "manual"),
        "status": row["status"],
        "stage": row["stage"],
        "inputHash": row["input_hash"],
        "resultRevision": row["result_revision"],
        "totalScore": row["total_score"],
        "maxScore": row["max_score"],
        "progress": {
            "current": row["progress_current"],
            "total": row["progress_total"],
        },
        "openReviewCount": row["open_review_count"],
        "lastSuccessfulStage": row["last_successful_stage"],
        "attemptCount": row["attempt_count"],
        "retryable": bool(row["retryable"]),
        "isStale": bool(row.get("is_stale", 0)),
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _question_value(row: dict[str, Any], *, detail: bool) -> dict[str, Any]:
    hide_placeholder_score = _is_unresolved_score_placeholder(row)
    value: dict[str, Any] = {
        "id": row["id"],
        "gradingRunId": row["grading_run_id"],
        "questionId": row["question_id"],
        "questionNumber": row.get("detected_number"),
        "questionType": row["question_type"],
        "status": row["status"],
        "rawScore": None if hide_placeholder_score else row["raw_score"],
        "finalScore": None if hide_placeholder_score else row["final_score"],
        "maxScore": row["max_score"],
        "reviewReasons": json_loads(row["review_reasons_json"], []),
        "errorLocations": json_loads(row["error_locations_json"], []),
        "attemptCount": row["attempt_count"],
        "resultRevision": row["result_revision"],
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
    }
    if detail:
        value.update(
            {
                "question": {
                    "stem": row.get("stem", ""),
                    "standardAnswer": json_loads(row["answer_snapshot_json"], {}),
                },
                "gradingConfig": json_loads(row["grading_config_snapshot_json"], {}),
                "decisions": json_loads(row["decisions_json"], []),
                "evidence": json_loads(row["evidence_refs_json"], []),
                "toolConclusions": json_loads(row["tool_observations_json"], []),
            }
        )
    return value


def _evidence_values(
    database: Database,
    result_id: str,
    values: object,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    output: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        page_id = str(item.get("page_id") or item.get("pageId") or "")
        region_id = str(item.get("region_id") or item.get("regionId") or "")
        page = (
            database.fetchone(
                "SELECT page_number FROM student_pages WHERE id=?",
                (page_id,),
            )
            if page_id
            else None
        )
        item["pageNumber"] = int(page["page_number"]) if page else None
        if region_id:
            item["previewUrl"] = (
                f"/api/grading-question-results/{result_id}/evidence/{region_id}/preview"
            )
        output.append(item)
    return output


def _review_value(row: dict[str, Any], *, detail: bool) -> dict[str, Any]:
    hide_placeholder_score = _is_unresolved_score_placeholder(row)
    value: dict[str, Any] = {
        "id": row["id"],
        "gradingRunId": row["grading_run_id"],
        "questionResultId": row["grading_question_result_id"],
        "questionId": row["question_id"],
        "questionNumber": row["detected_number"],
        "reason": row["reason"],
        "status": row["status"],
        "score": None if hide_placeholder_score else row["final_score"],
        "maxScore": row["max_score"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    if detail:
        result_row = dict(row)
        result_row["id"] = row["grading_question_result_id"]
        result_row["status"] = row["question_result_status"]
        value.update(
            {
                "context": json_loads(row["context_json"], {}),
                "resolution": json_loads(row["resolution_json"], None),
                "resolvedBy": row["resolved_by"],
                "resolvedAt": row["resolved_at"],
                "questionResult": _question_value(result_row, detail=True),
            }
        )
    return value


def _blank_result_value(
    row: dict[str, Any],
    config_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = str(row["blank_key"])
    config = config_by_key.get(key, {})
    return {
        "id": row["id"],
        "blankKey": key,
        "status": row["status"],
        "recognizedAnswer": row["recognized_answer"],
        "score": row["score"],
        "maxScore": row["max_score"],
        "answerKind": config.get("answerKind"),
        "standardAnswers": config.get("standardAnswers", []),
        "synonyms": config.get("synonyms", []),
        "exactMatch": json_loads(row["exact_match_json"], []),
        "modelResult": json_loads(row["model_result_json"], None),
        "verifierResult": json_loads(row["verifier_result_json"], None),
        "decision": json_loads(row["final_decision_json"], {}),
        "evidence": json_loads(row["evidence_refs_json"], []),
        "reviewReasons": json_loads(row["review_reasons_json"], []),
    }


def _captured_question_geometry(
    database: Database,
    *,
    question_id: str,
    processing_revision_id: str | None,
    blank_config_version_id: str | None,
    include_blank_anchors: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return immutable grading overlays from the run's captured versions."""

    if not processing_revision_id:
        return [], [], [
            {
                "code": "PROCESSING_REVISION_MISSING",
                "layer": "version",
                "message": "该历史批改结果没有绑定学生处理版本",
                "nextAction": "reprocess_submission",
            }
        ]
    region_rows = database.fetchall(
        """SELECT r.*,a.transform_json,a.student_page_id AS alignment_student_page_id,
                  a.processing_revision_id AS alignment_processing_revision_id,
                  p.width AS template_width,p.height AS template_height
           FROM student_question_regions r
           LEFT JOIN student_page_alignment_revisions a ON a.id=r.alignment_revision_id
           LEFT JOIN pages p ON p.id=r.template_page_id
           WHERE r.processing_revision_id=? AND r.question_id=?
           ORDER BY r.sort_order,r.id""",
        (processing_revision_id, question_id),
    )
    question_frames: list[dict[str, Any]] = []
    geometry_issues: list[dict[str, Any]] = []
    alignments_by_template_page: dict[str, dict[str, Any] | None] = {}
    for row in region_rows:
        polygon = json_loads(row.get("student_polygon_json"), [])
        if isinstance(polygon, list) and len(polygon) >= 3:
            question_frames.append(
                {
                    "id": row["id"],
                    "questionId": row["question_id"],
                    "pageId": row["student_page_id"],
                    "polygon": polygon,
                    "frameSetId": row.get("frame_set_id"),
                    "frameRegionId": row.get("frame_region_id"),
                    "alignmentRevisionId": row.get("alignment_revision_id"),
                    "processingRevisionId": row.get("processing_revision_id"),
                    "status": row.get("status"),
                    "issues": json_loads(row.get("issues_json"), []),
                }
            )
        template_page_id = str(row["template_page_id"])
        existing = alignments_by_template_page.get(template_page_id)
        if template_page_id in alignments_by_template_page and (
            existing is None
            or str(existing.get("alignment_revision_id"))
            != str(row.get("alignment_revision_id"))
            or str(existing.get("student_page_id")) != str(row.get("student_page_id"))
        ):
            alignments_by_template_page[template_page_id] = None
            continue
        if (
            row.get("transform_json")
            and str(row.get("alignment_student_page_id") or "")
            == str(row.get("student_page_id") or "")
            and str(row.get("alignment_processing_revision_id") or "")
            == processing_revision_id
            and row.get("template_width")
            and row.get("template_height")
        ):
            alignments_by_template_page[template_page_id] = row

    if not include_blank_anchors:
        return question_frames, [], geometry_issues
    if not blank_config_version_id:
        return question_frames, [], [
            *geometry_issues,
            {
                "code": "BLANK_CONFIG_VERSION_MISSING",
                "layer": "version",
                "message": "该历史批改结果没有绑定逐空配置版本",
                "nextAction": "configure_fill_blanks",
            },
        ]
    blank_rows = database.fetchall(
        """SELECT d.* FROM question_blank_definition_versions d
           JOIN question_blank_config_versions v ON v.id=d.blank_config_version_id
           WHERE d.blank_config_version_id=? AND v.question_id=?
           ORDER BY d.sort_order,d.blank_key""",
        (blank_config_version_id, question_id),
    )
    blank_anchors: list[dict[str, Any]] = []
    for blank in blank_rows:
        template_page_id = str(blank.get("template_page_id") or "")
        alignment_row = alignments_by_template_page.get(template_page_id)
        values = (blank.get("x"), blank.get("y"), blank.get("width"), blank.get("height"))
        if alignment_row is None or not all(isinstance(value, int | float) for value in values):
            geometry_issues.append(
                {
                    "code": "BLANK_ANCHOR_MAPPING_MISSING",
                    "layer": "blank_anchor",
                    "message": f"{blank['blank_key']} 缺少可验证的页面配准或锚点坐标",
                    "nextAction": "reprocess_submission",
                    "blankKey": blank["blank_key"],
                    "templatePageId": template_page_id or None,
                }
            )
            continue
        numeric_values = cast(tuple[int | float, int | float, int | float, int | float], values)
        x, y, width, height = (float(value) for value in numeric_values)
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            geometry_issues.append(
                {
                    "code": "BLANK_ANCHOR_GEOMETRY_INVALID",
                    "layer": "blank_anchor",
                    "message": f"{blank['blank_key']} 的锚点坐标无效",
                    "nextAction": "configure_fill_blanks",
                    "blankKey": blank["blank_key"],
                }
            )
            continue
        try:
            transform = Homography.from_rows(
                json_loads(alignment_row.get("transform_json"), [])
            )
            template_width = float(alignment_row["template_width"])
            template_height = float(alignment_row["template_height"])
            left = x * template_width
            top = y * template_height
            right = (x + width) * template_width
            bottom = (y + height) * template_height
            points = [
                transform.map_point(point)
                for point in (
                    Point(left, top),
                    Point(right, top),
                    Point(right, bottom),
                    Point(left, bottom),
                )
            ]
        except (TypeError, ValueError):
            geometry_issues.append(
                {
                    "code": "BLANK_ANCHOR_MAPPING_INVALID",
                    "layer": "blank_anchor",
                    "message": f"{blank['blank_key']} 无法按捕获的配准版本映射",
                    "nextAction": "reprocess_submission",
                    "blankKey": blank["blank_key"],
                }
            )
            continue
        min_x = min(point.x for point in points)
        min_y = min(point.y for point in points)
        max_x = max(point.x for point in points)
        max_y = max(point.y for point in points)
        blank_anchors.append(
            {
                "blankKey": blank["blank_key"],
                "pageId": alignment_row["student_page_id"],
                "coordinateSpace": "student_original_page_pixels",
                "studentPolygon": [point.as_dict() for point in points],
                "studentBBox": {
                    "x": min_x,
                    "y": min_y,
                    "width": max_x - min_x,
                    "height": max_y - min_y,
                },
                "templatePageId": template_page_id,
                "frameSetId": alignment_row.get("frame_set_id"),
                "blankConfigVersionId": blank_config_version_id,
                "processingRevisionId": processing_revision_id,
                "alignmentRevisionId": alignment_row.get("alignment_revision_id"),
                "issues": json_loads(blank.get("anchor_issues_json"), []),
            }
        )
    return question_frames, blank_anchors, geometry_issues


def _get_run(database: Database, run_id: str) -> dict[str, Any]:
    row = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    if not row:
        raise AppError(404, "GRADING_RUN_NOT_FOUND", "批改运行不存在")
    return row


@router.post("/student-submissions/{submission_id}/grading-runs")
async def create_grading_run(
    submission_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: GradingPipeline = Depends(get_grading_pipeline),
) -> JSONResponse:
    active = database.fetchone(
        """SELECT id,status FROM grading_runs WHERE submission_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (submission_id,),
    )
    if active and active["status"] in ACTIVE_RUN_STATUSES:
        raise AppError(
            409,
            "GRADING_RUN_ALREADY_ACTIVE",
            "该学生答卷已有未结束的批改运行",
            {"gradingRunId": active["id"], "status": active["status"]},
        )
    submission = database.fetchone(
        "SELECT task_id FROM student_submissions WHERE id=?", (submission_id,)
    )
    if submission is None:
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    ensure_task_fill_blank_configs(database, str(submission["task_id"]), "system")
    ensure_submission_blank_configs_current(database, submission_id)
    run_id = pipeline.create_run(submission_id)
    started = await jobs.start(f"grading:{run_id}", pipeline.run(run_id))
    if not started:
        raise AppError(409, "GRADING_RUN_ALREADY_STARTED", "批改运行已经启动")
    return success({"gradingRunId": run_id, "status": "queued"}, 202)


@router.get("/student-submissions/{submission_id}/grading-runs")
def list_grading_runs(
    submission_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    if not database.fetchone("SELECT id FROM student_submissions WHERE id=?", (submission_id,)):
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    rows = database.fetchall(
        """SELECT * FROM grading_runs WHERE submission_id=?
           ORDER BY created_at DESC""",
        (submission_id,),
    )
    return success([_run_value(row) for row in rows])


@router.get("/grading-runs/{run_id}")
def get_grading_run(
    run_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(_run_value(_get_run(database, run_id)))


@router.get("/grading-runs/{run_id}/questions")
def list_grading_questions(
    run_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    _get_run(database, run_id)
    rows = database.fetchall(
        """SELECT r.*,q.detected_number,q.stem FROM grading_question_results r
           JOIN questions q ON q.id=r.question_id
           WHERE r.grading_run_id=? ORDER BY q.sort_order""",
        (run_id,),
    )
    return success([_question_value(row, detail=False) for row in rows])


@router.get("/grading-runs/{run_id}/questions/{question_id}")
def get_grading_question(
    run_id: str,
    question_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    row = database.fetchone(
        """SELECT r.*,q.detected_number,q.stem FROM grading_question_results r
           JOIN questions q ON q.id=r.question_id
           WHERE r.grading_run_id=? AND r.question_id=?""",
        (run_id, question_id),
    )
    if not row:
        raise AppError(
            404,
            "GRADING_QUESTION_RESULT_NOT_FOUND",
            "该运行中不存在此题批改结果",
        )
    blanks = database.fetchall(
        """SELECT * FROM grading_blank_results
           WHERE grading_question_result_id=? ORDER BY blank_key""",
        (row["id"],),
    )
    points = database.fetchall(
        """SELECT * FROM grading_point_results
           WHERE grading_question_result_id=? ORDER BY point_key""",
        (row["id"],),
    )
    value = _question_value(row, detail=True)
    value["evidence"] = _evidence_values(
        database,
        str(row["id"]),
        json_loads(row["evidence_refs_json"], []),
    )
    grading_config = json_loads(row["grading_config_snapshot_json"], {})
    configured_blanks = (
        grading_config.get("blanks", []) if isinstance(grading_config, dict) else []
    )
    config_by_key = {
        str(item.get("blankKey")): item
        for item in configured_blanks
        if isinstance(item, dict) and item.get("blankKey")
    }
    run = _get_run(database, run_id)
    run_snapshot = json_loads(run["input_snapshot_json"], {})
    captured = next(
        (
            item
            for item in run_snapshot.get("questions", [])
            if isinstance(item, dict) and str(item.get("questionId")) == question_id
        ),
        {},
    )
    value.update(
        {
            "gradingRevision": row["result_revision"],
            "frameSetId": captured.get("frameSetId"),
            "blankConfigVersionId": captured.get("blankConfigVersionId"),
            "processingRevisionId": captured.get("processingRevisionId"),
        }
    )
    version_fields = {
        "frameSetId": captured.get("frameSetId"),
        "blankConfigVersionId": captured.get("blankConfigVersionId"),
        "processingRevisionId": captured.get("processingRevisionId"),
        "gradingRevision": row["result_revision"],
    }
    value["blankResults"] = [
        {**_blank_result_value(dict(item), config_by_key), **version_fields}
        for item in blanks
    ]
    question_frames, blank_anchors, geometry_issues = _captured_question_geometry(
        database,
        question_id=question_id,
        processing_revision_id=(
            str(captured["processingRevisionId"])
            if captured.get("processingRevisionId")
            else None
        ),
        blank_config_version_id=(
            str(captured["blankConfigVersionId"])
            if captured.get("blankConfigVersionId")
            else None
        ),
        include_blank_anchors=row["question_type"] == "fill_blank",
    )
    value["questionFrames"] = question_frames
    value["blankAnchors"] = blank_anchors
    value["geometryIssues"] = geometry_issues
    value["pointResults"] = [dict(item) for item in points]
    return success(value)


@router.get("/grading-question-results/{result_id}/evidence/{region_id}/preview")
def preview_grading_evidence(
    result_id: str,
    region_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Render a verified evidence crop directly from the immutable student page."""

    result = database.fetchone(
        """SELECT r.*,g.submission_id FROM grading_question_results r
           JOIN grading_runs g ON g.id=r.grading_run_id WHERE r.id=?""",
        (result_id,),
    )
    if not result:
        raise AppError(404, "GRADING_QUESTION_RESULT_NOT_FOUND", "题目批改结果不存在")

    top_level_evidence = json_loads(result["evidence_refs_json"], [])
    evidence_values: list[object] = (
        list(top_level_evidence) if isinstance(top_level_evidence, list) else []
    )
    for decision in json_loads(result["decisions_json"], []):
        if isinstance(decision, dict) and isinstance(decision.get("evidence_refs"), list):
            evidence_values.extend(decision["evidence_refs"])
    evidence = next(
        (
            item
            for item in evidence_values
            if isinstance(item, dict)
            and str(item.get("region_id") or item.get("regionId") or "") == region_id
        ),
        None,
    )
    if not isinstance(evidence, dict):
        raise AppError(404, "GRADING_EVIDENCE_NOT_FOUND", "该批改结果未引用此证据")

    # The client supplies only an opaque region id. Page ownership, source path,
    # and coordinates all come from the persisted result and are revalidated
    # before opening a file, so a forged URL cannot become a path/crop oracle.
    page_id = str(evidence.get("page_id") or evidence.get("pageId") or "")
    page = database.fetchone(
        """SELECT * FROM student_pages WHERE id=? AND submission_id=?""",
        (page_id, result["submission_id"]),
    )
    if not page:
        raise AppError(409, "GRADING_EVIDENCE_PAGE_MISMATCH", "证据页不属于本次批改答卷")
    bbox = evidence.get("original_bbox") or evidence.get("originalBbox")
    if not isinstance(bbox, dict):
        raise AppError(422, "GRADING_EVIDENCE_BBOX_INVALID", "证据坐标缺失")
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        width = float(bbox["width"])
        height = float(bbox["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise AppError(422, "GRADING_EVIDENCE_BBOX_INVALID", "证据坐标格式无效") from error
    if (
        not all(math.isfinite(value) for value in (x, y, width, height))
        or x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > float(page["width"]) + 0.01
        or y + height > float(page["height"]) + 0.01
    ):
        raise AppError(422, "GRADING_EVIDENCE_BBOX_INVALID", "证据坐标超出页面范围")

    path = resolve_data_path(settings, str(page["original_image_path"]))
    if not path.is_file():
        raise AppError(404, "STUDENT_PAGE_MISSING", "证据对应的学生答卷页已丢失")
    with Image.open(path) as source:
        actual_width, actual_height = source.size
        if x + width > actual_width + 0.01 or y + height > actual_height + 0.01:
            raise AppError(422, "GRADING_EVIDENCE_BBOX_INVALID", "证据坐标超出原图范围")
        crop = source.crop(
            (
                math.floor(x),
                math.floor(y),
                math.ceil(x + width),
                math.ceil(y + height),
            )
        ).convert("RGB")
        output = BytesIO()
        crop.save(output, format="JPEG", quality=90, optimize=True)
    return Response(
        content=output.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/grading-runs/{run_id}/retry")
async def retry_grading_run(
    run_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: GradingPipeline = Depends(get_grading_pipeline),
) -> JSONResponse:
    run = _get_run(database, run_id)
    if run.get("is_stale"):
        raise AppError(409, "GRADING_RUN_STALE", "该批改运行依赖的逐空配置已过期，请重新批改")
    if run["status"] != "failed" or not run["retryable"]:
        raise AppError(409, "GRADING_RUN_NOT_RETRYABLE", "当前批改运行不可重试")
    artifact_only = run["last_successful_stage"] in {
        "auditing",
        "generating_annotation",
        "generating_report",
    }
    key = f"grading-artifacts:{run_id}" if artifact_only else f"grading:{run_id}"
    if jobs.is_running(key):
        return success({"gradingRunId": run_id, "reused": True}, 202)
    next_stage = "generating_annotation" if artifact_only else "queued"
    database.execute(
        """UPDATE grading_runs SET status=?,stage=?,error_code=NULL,
           error_message=NULL,updated_at=? WHERE id=?""",
        (next_stage, next_stage, now_iso(), run_id),
    )
    work = pipeline.generate_artifacts(run_id) if artifact_only else pipeline.run(run_id)
    started = await jobs.start(key, work)
    return success({"gradingRunId": run_id, "reused": not started}, 202)


@router.post("/grading-runs/{run_id}/regenerate")
async def regenerate_grading_artifacts(
    run_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: GradingPipeline = Depends(get_grading_pipeline),
) -> JSONResponse:
    run = _get_run(database, run_id)
    if run.get("is_stale"):
        raise AppError(409, "GRADING_RUN_STALE", "该批改运行依赖的逐空配置已过期，请重新批改")
    if run["open_review_count"]:
        raise AppError(409, "GRADING_REVIEW_REQUIRED", "仍有待复核题目")
    pending = database.fetchone(
        """SELECT COUNT(*) AS value FROM grading_question_results
           WHERE grading_run_id=? AND status<>'final'""",
        (run_id,),
    )
    if pending and pending["value"]:
        raise AppError(409, "GRADING_RESULTS_NOT_FINAL", "逐题结果尚未全部最终化")
    key = f"grading-artifacts:{run_id}"
    if jobs.is_running(key):
        return success({"gradingRunId": run_id, "reused": True}, 202)
    database.execute(
        """UPDATE grading_runs SET status='generating_annotation',
           stage='generating_annotation',error_code=NULL,error_message=NULL,
           retryable=0,updated_at=? WHERE id=?""",
        (now_iso(), run_id),
    )
    started = await jobs.start(key, pipeline.generate_artifacts(run_id))
    return success({"gradingRunId": run_id, "reused": not started}, 202)


@router.get("/grading-runs/{run_id}/review-items")
def list_grading_review_items(
    run_id: str,
    status: Annotated[Literal["open", "resolved", "all"], Query()] = "open",
    database: Database = Depends(get_database),
) -> JSONResponse:
    _get_run(database, run_id)
    condition = "" if status == "all" else "AND i.status=?"
    params: tuple[Any, ...] = (run_id,) if status == "all" else (run_id, status)
    rows = database.fetchall(
        f"""SELECT i.*,r.question_id,r.final_score,r.max_score,r.grading_run_id,
            q.detected_number,q.stem,r.question_type,r.raw_score,r.decisions_json,
            r.evidence_refs_json,r.error_locations_json,r.tool_observations_json,
            r.review_reasons_json,r.attempt_count,r.result_revision,r.error_code,
            r.error_message,r.answer_snapshot_json,r.grading_config_snapshot_json,
            r.status AS question_result_status
            FROM grading_review_items i
            JOIN grading_question_results r ON r.id=i.grading_question_result_id
            JOIN questions q ON q.id=r.question_id
            WHERE i.grading_run_id=? {condition}
            ORDER BY q.sort_order,i.created_at""",
        params,
    )
    return success([_review_value(row, detail=False) for row in rows])


@router.get("/grading-review-items/{review_item_id}")
def get_grading_review_item(
    review_item_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    row = database.fetchone(
        """SELECT i.*,r.question_id,r.final_score,r.max_score,r.grading_run_id,
           q.detected_number,q.stem,r.question_type,r.raw_score,r.decisions_json,
           r.evidence_refs_json,r.error_locations_json,r.tool_observations_json,
           r.review_reasons_json,r.attempt_count,r.result_revision,r.error_code,
           r.error_message,r.answer_snapshot_json,r.grading_config_snapshot_json,
           r.status AS question_result_status
           FROM grading_review_items i
           JOIN grading_question_results r ON r.id=i.grading_question_result_id
           JOIN questions q ON q.id=r.question_id WHERE i.id=?""",
        (review_item_id,),
    )
    if not row:
        raise AppError(404, "GRADING_REVIEW_ITEM_NOT_FOUND", "批改复核项不存在")
    return success(_review_value(row, detail=True))


@router.post("/grading-review-items/{review_item_id}/resolve")
async def resolve_grading_review_item(
    review_item_id: str,
    payload: GradingReviewResolution,
    service: GradingReviewService = Depends(get_grading_review_service),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: GradingPipeline = Depends(get_grading_pipeline),
) -> JSONResponse:
    value = service.resolve(review_item_id, payload)
    run = _get_run(database, str(value["gradingRunId"]))
    if run["status"] == "generating_annotation":
        key = f"grading-artifacts:{run['id']}"
        await jobs.start(key, pipeline.generate_artifacts(str(run["id"])))
    return success(value)


@router.patch("/grading-question-results/{result_id}/blanks/{blank_key}")
async def correct_grading_blank(
    result_id: str,
    blank_key: str,
    payload: GradingBlankCorrection,
    service: GradingReviewService = Depends(get_grading_review_service),
    model_client: DashScopeClient = Depends(get_model_client),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: GradingPipeline = Depends(get_grading_pipeline),
) -> JSONResponse:
    value = await service.correct_blank(result_id, blank_key, payload, model_client)
    run = _get_run(database, str(value["gradingRunId"]))
    if run["status"] == "generating_annotation":
        key = f"grading-artifacts:{run['id']}"
        await jobs.start(key, pipeline.generate_artifacts(str(run["id"])))
    return success(value)


@router.patch("/grading-question-results/{result_id}/error-location")
def update_grading_error_location(
    result_id: str,
    payload: ErrorLocationUpdate,
    service: GradingReviewService = Depends(get_grading_review_service),
) -> JSONResponse:
    return success(service.update_error_location(result_id, payload))
