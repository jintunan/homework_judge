from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..grading.blank_config_confirmation import (
    blocker_details,
    persist_fill_blank_config_batch,
    prepare_question_fill_blank_config,
    prepare_task_fill_blank_configs,
)
from ..grading.normalization import decimal_string, normalize_options
from ..matching.numbers import normalize_question_number
from ..question_frames.service import QuestionFrameService
from ..review.history import student_processing_gate
from ..review.invalidation import (
    ensure_question_context_mutable,
    invalidate_answer_grading_dependents,
)
from ..review.lifecycle import mark_question_duplicate, restore_question
from ..schemas import TemplateAnswerRegion
from .dependencies import get_database, get_settings
from .response import success

router = APIRouter()


def _ensure_not_duplicate(row: dict[str, Any]) -> None:
    if bool(row.get("is_duplicate", 0)):
        raise AppError(409, "QUESTION_MARKED_DUPLICATE", "重复题只能查看或恢复")


def _effective_question(row: dict[str, Any]) -> dict[str, Any]:
    override = json_loads(row.get("teacher_override_json"), {})
    base = {
        "number": row["detected_number"],
        "stem": row["stem"],
        "options": json_loads(row["options_json"], []),
        "type": row["question_type"],
        "score": row["score"],
    }
    return {**base, **override}


def _advance_grading_context(
    connection: sqlite3.Connection,
    question_id: str,
    *,
    effective_type: object | None = None,
    effective_score: object | None = None,
) -> None:
    """Supersede grading configuration derived from edited teacher context."""

    current = connection.execute(
        """SELECT question_type,max_score,current_blank_config_version_id
           FROM question_grading_configs WHERE question_id=?""",
        (question_id,),
    ).fetchone()
    if not current:
        return
    timestamp = now_iso()
    blank_config_id = current["current_blank_config_version_id"]
    if blank_config_id:
        connection.execute(
            """UPDATE question_blank_config_versions SET status='stale',updated_at=?
               WHERE id=? AND status IN ('pending','auto_confirmed','teacher_confirmed')""",
            (timestamp, blank_config_id),
        )
    question_type = str(effective_type or current["question_type"])
    max_score = (
        decimal_string(str(effective_score))
        if effective_score is not None
        else str(current["max_score"])
    )
    connection.execute(
        """UPDATE question_grading_configs SET question_type=?,max_score=?,
           current_blank_config_version_id=NULL,config_version=config_version+1,updated_at=?
           WHERE question_id=?""",
        (question_type, max_score, timestamp, question_id),
    )


@router.get("/tasks/{task_id}/review")
def review_detail(task_id: str, database: Database = Depends(get_database)) -> JSONResponse:
    task = database.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    questions = database.fetchall(
        """SELECT q.*,m.id AS match_id,m.answer_entry_id,m.method,m.number_score,
           m.stem_score,m.order_score,m.total_score,m.reasons_json,m.status AS match_status,
           m.teacher_answer,m.teacher_explanation,
           a.number_hint AS answer_number,a.answer AS auto_answer,
           a.explanation AS auto_explanation,a.source_pages_json AS answer_source_pages
           FROM questions q
           LEFT JOIN matches m ON m.question_id=q.id
           LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
           WHERE q.task_id=? ORDER BY q.sort_order""",
        (task_id,),
    )
    items = []
    for row in questions:
        effective = _effective_question(row)
        items.append(
            {
                "id": row["id"],
                "sortOrder": row["sort_order"],
                "original": {
                    "number": row["detected_number"],
                    "stem": row["stem"],
                    "options": json_loads(row["options_json"], []),
                    "type": row["question_type"],
                    "score": row["score"],
                },
                "effective": effective,
                "sourcePages": json_loads(row["source_pages_json"], []),
                "answerRegions": [
                    {
                        "pageNumber": int(region["page_number"]),
                        "x": float(region["x"]),
                        "y": float(region["y"]),
                        "width": float(region["width"]),
                        "height": float(region["height"]),
                    }
                    for region in json_loads(row.get("answer_regions_json"), [])
                    if isinstance(region, dict)
                ],
                "confidence": row["confidence"],
                "issues": json_loads(row["issues_json"], []),
                "isDuplicate": bool(row.get("is_duplicate", 0)),
                "confirmationStatus": row["confirmation_status"],
                "match": {
                    "id": row["match_id"],
                    "answerEntryId": row["answer_entry_id"],
                    "method": row["method"],
                    "numberScore": row["number_score"] or 0,
                    "stemScore": row["stem_score"] or 0,
                    "orderScore": row["order_score"] or 0,
                    "totalScore": row["total_score"] or 0,
                    "reasons": json_loads(row["reasons_json"], []),
                    "status": row["match_status"],
                    "answer": row["teacher_answer"]
                    if row["teacher_answer"] is not None
                    else row["auto_answer"] or "",
                    "explanation": row["teacher_explanation"]
                    if row["teacher_explanation"] is not None
                    else row["auto_explanation"] or "",
                    "answerSourcePages": json_loads(row["answer_source_pages"], []),
                },
            }
        )
    answers = database.fetchall(
        """SELECT a.*,m.question_id FROM answer_entries a
           LEFT JOIN matches m ON m.answer_entry_id=a.id
           WHERE a.task_id=? ORDER BY a.sort_order""",
        (task_id,),
    )
    answer_items = [
        {
            "id": row["id"],
            "numberHint": row["number_hint"],
            "stemHint": row["stem_hint"],
            "answer": row["answer"],
            "explanation": row["explanation"],
            "sourcePages": json_loads(row["source_pages_json"], []),
            "confidence": row["confidence"],
            "issues": json_loads(row["issues_json"], []),
            "ignored": bool(row["ignored"]),
            "questionId": row["question_id"],
        }
        for row in answers
    ]
    documents = database.fetchall(
        "SELECT id,role,original_name,page_count FROM documents WHERE task_id=?",
        (task_id,),
    )
    pages = database.fetchall(
        """SELECT p.id,p.document_id,p.page_number,p.width,p.height,d.role
           FROM pages p JOIN documents d ON d.id=p.document_id
           WHERE d.task_id=? ORDER BY d.role,p.page_number""",
        (task_id,),
    )
    frame_service = QuestionFrameService(database)
    page_items = [
        {
            **page,
            "imageUrl": f"/api/pages/{page['id']}",
        }
        for page in pages
    ]
    return success(
        {
            "task": task,
            "questions": items,
            "answerEntries": answer_items,
            "documents": documents,
            "pages": page_items,
            "questionFrameSet": frame_service.get_current(task_id),
            "studentUploadGate": student_processing_gate(database, task_id),
        }
    )


@router.patch("/questions/{question_id}")
def update_question(
    question_id: str,
    payload: dict[str, Any] = Body(...),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone("SELECT * FROM questions WHERE id=?", (question_id,))
    if not row:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    _ensure_not_duplicate(row)
    allowed = {"number", "stem", "options", "type", "score"}
    override = {key: value for key, value in payload.items() if key in allowed}
    if "number" in override and not str(override["number"]).strip():
        raise AppError(422, "QUESTION_NUMBER_REQUIRED", "题号不能为空")
    current_override = json_loads(row.get("teacher_override_json"), {})
    next_override = {**current_override, **override}
    current_effective = _effective_question(row)
    next_effective = _effective_question(
        {**row, "teacher_override_json": json_dumps(next_override)}
    )
    changed_fields = {
        key for key in override if current_effective.get(key) != next_effective.get(key)
    }
    if not changed_fields:
        return success({"id": question_id, "saved": True, "changed": False})
    with database.transaction() as connection:
        ensure_question_context_mutable(connection, str(row["task_id"]))
        connection.execute(
            """UPDATE questions SET teacher_override_json=?,confirmation_status='pending'
               WHERE id=?""",
            (json_dumps(next_override), question_id),
        )
        if "number" in changed_fields:
            connection.execute(
                "UPDATE questions SET normalized_number=? WHERE id=?",
                (normalize_question_number(str(next_override["number"])), question_id),
            )
        connection.execute(
            "UPDATE matches SET status='suggested',updated_at=? WHERE question_id=?",
            (now_iso(), question_id),
        )
        if changed_fields:
            if {"stem", "options", "type", "score"}.intersection(changed_fields):
                _advance_grading_context(
                    connection,
                    question_id,
                    effective_type=next_effective.get("type"),
                    effective_score=next_effective.get("score"),
                )
            invalidate_answer_grading_dependents(
                connection,
                str(row["task_id"]),
                reason_code="QUESTION_CONTEXT_CHANGED",
                reason_message="题目信息已更新，请重新处理并批改学生答卷",
            )
        database.audit(
            connection,
            str(row["task_id"]),
            "question_edited",
            settings.teacher_name,
            {"questionId": question_id, "fields": sorted(changed_fields)},
        )
    return success({"id": question_id, "saved": True, "changed": True})


@router.patch("/questions/{question_id}/answer-regions")
def update_answer_regions(
    question_id: str,
    payload: list[TemplateAnswerRegion] = Body(...),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone(
        "SELECT task_id,is_duplicate,answer_regions_json FROM questions WHERE id=?",
        (question_id,),
    )
    if not row:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    _ensure_not_duplicate(row)
    valid_pages = {
        int(page["page_number"])
        for page in database.fetchall(
            """SELECT p.page_number FROM pages p
               JOIN documents d ON d.id=p.document_id
               WHERE d.task_id=? AND d.role='exam'""",
            (row["task_id"],),
        )
    }
    invalid_pages = sorted({item.pageNumber for item in payload} - valid_pages)
    if invalid_pages:
        raise AppError(
            422,
            "ANSWER_REGION_PAGE_INVALID",
            "答题区域页码不属于当前试卷",
            {"pageNumbers": invalid_pages},
        )
    regions = [
        {
            "page_number": item.pageNumber,
            "x": item.x,
            "y": item.y,
            "width": item.width,
            "height": item.height,
        }
        for item in payload
    ]
    if json_loads(row.get("answer_regions_json"), []) == regions:
        return success(
            {
                "id": question_id,
                "answerRegions": [item.model_dump() for item in payload],
                "changed": False,
            }
        )
    with database.transaction() as connection:
        ensure_question_context_mutable(connection, str(row["task_id"]))
        connection.execute(
            "UPDATE questions SET answer_regions_json=? WHERE id=?",
            (json_dumps(regions), question_id),
        )
        _advance_grading_context(connection, question_id)
        invalidate_answer_grading_dependents(
            connection,
            str(row["task_id"]),
            reason_code="TEMPLATE_REGIONS_CHANGED",
            reason_message="答题区域已更新，请重新处理并批改学生答卷",
        )
        database.audit(
            connection,
            str(row["task_id"]),
            "answer_regions_updated",
            settings.teacher_name,
            {"questionId": question_id, "regionCount": len(regions)},
        )
    return success(
        {
            "id": question_id,
            "answerRegions": [item.model_dump() for item in payload],
            "changed": True,
        }
    )


@router.patch("/matches/{match_id}")
def update_match(
    match_id: str,
    payload: dict[str, Any] = Body(...),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    match = database.fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
    if not match:
        raise AppError(404, "MATCH_NOT_FOUND", "匹配记录不存在")
    question = database.fetchone(
        "SELECT is_duplicate FROM questions WHERE id=?", (match["question_id"],)
    )
    if question:
        _ensure_not_duplicate(question)
    answer_entry_id = payload.get("answerEntryId")
    direct_answer = payload.get("answer")
    direct_explanation = payload.get("explanation")
    method = "unmatched"
    status = "needs_review"
    if answer_entry_id:
        answer = database.fetchone(
            "SELECT * FROM answer_entries WHERE id=? AND task_id=?",
            (answer_entry_id, match["task_id"]),
        )
        if not answer:
            raise AppError(422, "ANSWER_ENTRY_INVALID", "答案条目不属于当前任务")
        method, status = "manual", "suggested"
    elif isinstance(direct_answer, str) and direct_answer.strip():
        method, status = "direct_entry", "suggested"
    teacher_answer = direct_answer.strip() if isinstance(direct_answer, str) else None
    teacher_explanation = (
        direct_explanation.strip() if isinstance(direct_explanation, str) else None
    )
    teacher_answer = teacher_answer or None
    teacher_explanation = teacher_explanation or None
    if answer_entry_id:
        teacher_answer = None
        teacher_explanation = None
    content_unchanged = (
        (match["answer_entry_id"] or None) == (answer_entry_id or None)
        and (match["teacher_answer"] or None) == teacher_answer
        and (match["teacher_explanation"] or None) == teacher_explanation
    )
    if content_unchanged:
        return success({"id": match_id, "saved": True, "changed": False})
    try:
        with database.transaction() as connection:
            ensure_question_context_mutable(connection, str(match["task_id"]))
            connection.execute(
                """UPDATE matches SET answer_entry_id=?,method=?,status=?,
                   teacher_answer=?,teacher_explanation=?,reasons_json=?,updated_at=?
                   WHERE id=?""",
                (
                    answer_entry_id,
                    method,
                    status,
                    teacher_answer,
                    teacher_explanation,
                    json_dumps(["教师手动调整"]),
                    now_iso(),
                    match_id,
                ),
            )
            connection.execute(
                "UPDATE questions SET confirmation_status='pending' WHERE id=?",
                (match["question_id"],),
            )
            _advance_grading_context(connection, str(match["question_id"]))
            invalidate_answer_grading_dependents(connection, str(match["task_id"]))
            database.audit(
                connection,
                str(match["task_id"]),
                "match_changed",
                settings.teacher_name,
                {"matchId": match_id, "method": method},
            )
    except sqlite3.IntegrityError as error:
        raise AppError(409, "ANSWER_ALREADY_ASSIGNED", "该答案已被其他题目使用") from error
    return success({"id": match_id, "saved": True, "changed": True})


@router.post("/questions/{question_id}/confirm")
def confirm_question(
    question_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone(
        """SELECT q.*,m.status AS match_status,m.teacher_answer,a.answer AS auto_answer
           FROM questions q JOIN matches m ON m.question_id=q.id
           LEFT JOIN answer_entries a ON a.id=m.answer_entry_id WHERE q.id=?""",
        (question_id,),
    )
    if not row:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    _ensure_not_duplicate(row)
    effective = _effective_question(row)
    answer = row["teacher_answer"] if row["teacher_answer"] is not None else row["auto_answer"]
    missing = [
        field
        for field, value in (
            ("number", effective.get("number")),
            ("stem", effective.get("stem")),
            ("type", effective.get("type")),
            ("answer", answer),
        )
        if not value
    ]
    if missing:
        raise AppError(422, "QUESTION_INCOMPLETE", "题目信息不完整", {"fields": missing})
    question_type = str(effective.get("type", ""))
    if question_type in {"single_choice", "multiple_choice"}:
        normalized_answer = normalize_options(str(answer))
        invalid = bool(normalized_answer.issues) or not normalized_answer.options
        if question_type == "single_choice":
            invalid = invalid or len(normalized_answer.options) != 1
        if invalid:
            raise AppError(
                422,
                "QUESTION_TYPE_ANSWER_CONFLICT",
                "题型与标准答案不一致，请先修正后再确认",
                {
                    "questionId": question_id,
                    "questionNumber": effective.get("number"),
                    "questionType": question_type,
                    "standardAnswer": answer,
                },
            )
    with database.transaction() as connection:
        fill_blank_batch = prepare_question_fill_blank_config(connection, question_id)
        persist_fill_blank_config_batch(
            connection,
            database,
            fill_blank_batch,
            settings.teacher_name,
            "question_confirm",
        )
        connection.execute(
            "UPDATE questions SET confirmation_status='confirmed' WHERE id=?",
            (question_id,),
        )
        connection.execute(
            "UPDATE matches SET status='confirmed',updated_at=? WHERE question_id=?",
            (now_iso(), question_id),
        )
        database.audit(
            connection,
            str(row["task_id"]),
            "question_confirmed",
            settings.teacher_name,
            {"questionId": question_id},
        )
    return success({"id": question_id, "status": "confirmed"})


@router.post("/questions/{question_id}/reopen")
def reopen_question(
    question_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone("SELECT task_id,is_duplicate FROM questions WHERE id=?", (question_id,))
    if not row:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    _ensure_not_duplicate(row)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE questions SET confirmation_status='pending' WHERE id=?", (question_id,)
        )
        connection.execute(
            "UPDATE matches SET status='suggested',updated_at=? WHERE question_id=?",
            (now_iso(), question_id),
        )
        database.audit(
            connection,
            str(row["task_id"]),
            "question_reopened",
            settings.teacher_name,
            {"questionId": question_id},
        )
    return success({"id": question_id, "status": "pending"})


@router.post("/answer-entries/{entry_id}/ignore")
def ignore_answer(
    entry_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone("SELECT task_id FROM answer_entries WHERE id=?", (entry_id,))
    if not row:
        raise AppError(404, "ANSWER_ENTRY_NOT_FOUND", "答案条目不存在")
    with database.transaction() as connection:
        connection.execute("UPDATE answer_entries SET ignored=1 WHERE id=?", (entry_id,))
        database.audit(
            connection,
            str(row["task_id"]),
            "answer_ignored",
            settings.teacher_name,
            {"answerEntryId": entry_id},
        )
    return success({"id": entry_id, "ignored": True})


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    with database.transaction() as connection:
        task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
        frame_gate = QuestionFrameService(database).processing_gate(task_id, connection)
        if not bool(frame_gate["ready"]):
            raise AppError(
                409,
                "QUESTION_FRAMES_NOT_CONFIRMED",
                "请先逐题确认完整题框，再完成教师审核",
                frame_gate,
            )
        questions = connection.execute(
            """SELECT q.*,m.status AS match_status,m.answer_entry_id,m.teacher_answer,
               a.answer AS auto_answer
               FROM questions q LEFT JOIN matches m ON m.question_id=q.id
               LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
               WHERE q.task_id=? AND q.is_duplicate=0""",
            (task_id,),
        ).fetchall()
        blockers: list[dict[str, Any]] = []
        if not questions:
            blockers.append({"code": "NO_QUESTIONS", "message": "没有可确认的题目"})
        for raw_row in questions:
            row = dict(raw_row)
            effective = _effective_question(row)
            answer = (
                row["teacher_answer"] if row["teacher_answer"] is not None else row["auto_answer"]
            )
            if (
                row["confirmation_status"] != "confirmed"
                or row["match_status"] != "confirmed"
                or not effective.get("number")
                or not effective.get("stem")
                or not effective.get("type")
                or not answer
            ):
                blockers.append(
                    {
                        "code": "QUESTION_NOT_READY",
                        "questionId": row["id"],
                        "number": effective.get("number"),
                    }
                )
        orphan = connection.execute(
            """SELECT COUNT(*) AS count FROM answer_entries a
               LEFT JOIN matches m ON m.answer_entry_id=a.id
               WHERE a.task_id=? AND a.ignored=0 AND m.id IS NULL""",
            (task_id,),
        ).fetchone()
        if orphan and orphan["count"]:
            blockers.append(
                {
                    "code": "ORPHAN_ANSWERS",
                    "count": int(orphan["count"]),
                    "message": "仍有未处理的答案条目",
                }
            )
        fill_blank_batch = prepare_task_fill_blank_configs(connection, task_id)
        if blockers:
            blockers.extend(
                {
                    "code": "FILL_BLANK_CONFIG_REVIEW_REQUIRED",
                    **item,
                }
                for item in blocker_details(fill_blank_batch)
            )
            raise AppError(409, "TASK_NOT_READY", "任务仍有待处理项", {"blockers": blockers})
        persist_fill_blank_config_batch(
            connection,
            database,
            fill_blank_batch,
            settings.teacher_name,
            "task_complete",
        )
        connection.execute(
            "UPDATE tasks SET status='completed',updated_at=? WHERE id=?",
            (now_iso(), task_id),
        )
        database.audit(
            connection,
            task_id,
            "task_completed",
            settings.teacher_name,
            {"questionCount": len(questions)},
        )
    return success({"taskId": task_id, "status": "completed"})


@router.post("/questions/{question_id}/mark-duplicate")
def mark_duplicate(
    question_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return success(mark_question_duplicate(database, settings, question_id))


@router.post("/questions/{question_id}/restore")
def restore_duplicate(
    question_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return success(restore_question(database, settings, question_id))
