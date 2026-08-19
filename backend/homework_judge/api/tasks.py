from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db.database import Database, now_iso
from ..errors import AppError
from ..files.storage import remove_task_files, save_upload
from ..files.validation import validate_upload
from ..jobs.manager import JobManager
from ..jobs.pipeline import Pipeline
from .dependencies import get_database, get_jobs, get_pipeline, get_settings
from .response import success

router = APIRouter()


def _task_summary(database: Database, row: dict[str, Any]) -> dict[str, Any]:
    counts = database.fetchone(
        """SELECT COUNT(*) AS total,
           SUM(CASE WHEN confirmation_status='confirmed' THEN 1 ELSE 0 END) AS confirmed
           FROM questions WHERE task_id=? AND is_duplicate=0""",
        (row["id"],),
    ) or {"total": 0, "confirmed": 0}
    return {
        **row,
        "questionCount": int(counts["total"] or 0),
        "confirmedCount": int(counts["confirmed"] or 0),
    }


@router.get("/tasks")
def list_tasks(database: Database = Depends(get_database)) -> JSONResponse:
    rows = database.fetchall("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 100")
    return success([_task_summary(database, row) for row in rows])


@router.post("/tasks")
async def create_task(
    exam: Annotated[UploadFile, File()],
    answer: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: Pipeline = Depends(get_pipeline),
) -> JSONResponse:
    task_id = uuid.uuid4().hex
    timestamp = now_iso()
    saved_items: list[tuple[str, Any, str]] = []
    try:
        for role, upload in (("exam", exam), ("answer", answer)):
            saved = await save_upload(settings, task_id, role, upload)
            mime_type = validate_upload(settings, saved)
            saved_items.append((role, saved, mime_type))
        task_title = title.strip() or Path(saved_items[0][1].original_name).stem
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks(id,title,status,created_at,updated_at)
                   VALUES(?,?,'draft',?,?)""",
                (task_id, task_title, timestamp, timestamp),
            )
            for role, saved, mime_type in saved_items:
                connection.execute(
                    """INSERT INTO documents(
                       id,task_id,role,original_name,stored_name,mime_type,extension,
                       size_bytes,sha256,relative_path,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        task_id,
                        role,
                        saved.original_name,
                        saved.stored_name,
                        mime_type,
                        saved.extension,
                        saved.size_bytes,
                        saved.sha256,
                        saved.relative_path,
                        timestamp,
                    ),
                )
            database.audit(
                connection,
                task_id,
                "task_created",
                settings.teacher_name,
                {"files": [item[1].original_name for item in saved_items]},
            )
        run_id = pipeline.create_parent_run(task_id)
        await jobs.start(task_id, pipeline.run(task_id, run_id))
        return success({"taskId": task_id, "runId": run_id}, 201)
    except Exception:
        remove_task_files(settings, task_id)
        raise


@router.get("/tasks/{task_id}")
def get_task(task_id: str, database: Database = Depends(get_database)) -> JSONResponse:
    row = database.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    documents = database.fetchall(
        """SELECT id,role,original_name,mime_type,size_bytes,page_count,created_at
           FROM documents WHERE task_id=? ORDER BY role""",
        (task_id,),
    )
    return success({**_task_summary(database, row), "documents": documents})


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
) -> JSONResponse:
    task = database.fetchone("SELECT id FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    submissions = database.fetchall(
        "SELECT id FROM student_submissions WHERE task_id=?", (task_id,)
    )
    job_keys = [
        task_id,
        f"regions:{task_id}",
        *(f"student:{row['id']}" for row in submissions),
    ]
    cancelled = await jobs.cancel(job_keys)
    try:
        remove_task_files(settings, task_id)
    except AppError:
        raise
    except OSError as error:
        raise AppError(
            500,
            "TASK_FILE_DELETE_FAILED",
            "任务文件删除失败，数据库记录已保留，请重试",
            {"reason": str(error)},
        ) from error
    with database.transaction() as connection:
        connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return success({"taskId": task_id, "deleted": True, "cancelledJobs": cancelled})


@router.get("/tasks/{task_id}/progress")
def get_progress(task_id: str, database: Database = Depends(get_database)) -> JSONResponse:
    task = database.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    run = (
        database.fetchone("SELECT * FROM runs WHERE id=?", (task["active_run_id"],))
        if task.get("active_run_id")
        else None
    )
    return success(
        {
            "taskId": task_id,
            "status": task["status"],
            "errorCode": task["last_error_code"],
            "errorMessage": task["last_error_message"],
            "run": run,
        }
    )


@router.post("/tasks/{task_id}/process")
async def process_task(
    task_id: str,
    database: Database = Depends(get_database),
    jobs: JobManager = Depends(get_jobs),
    pipeline: Pipeline = Depends(get_pipeline),
) -> JSONResponse:
    task = database.fetchone("SELECT id FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    submissions = database.fetchone(
        "SELECT COUNT(*) AS count FROM student_submissions WHERE task_id=?",
        (task_id,),
    )
    if submissions and submissions["count"]:
        raise AppError(
            409,
            "TEMPLATE_HAS_STUDENT_SUBMISSIONS",
            "已有学生答卷，不能重建试卷页面和题目坐标",
            {"submissionCount": int(submissions["count"])},
        )
    if jobs.is_running(task_id):
        current = database.fetchone("SELECT active_run_id FROM tasks WHERE id=?", (task_id,))
        if current is None:
            raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
        return success({"taskId": task_id, "runId": current["active_run_id"], "reused": True}, 202)
    run_id = pipeline.create_parent_run(task_id)
    await jobs.start(task_id, pipeline.run(task_id, run_id))
    return success({"taskId": task_id, "runId": run_id, "reused": False}, 202)
