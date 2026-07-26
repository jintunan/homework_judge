from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ...errors import AppError, require_found
from ...files.storage import PersistedFile
from ..database import Database, now_iso
from .audit import record_audit
from .tasks import insert_stored_file

_SUBMISSION_SELECT = """
SELECT s.*, f.original_name, f.mime_type, f.size
FROM submissions s
JOIN stored_files f ON f.id = s.file_id
"""
_EXCLUDED_NAME_TOKENS = {
    "数学",
    "物理",
    "试卷",
    "作业",
    "答题卡",
    "初中数学",
    "高中物理",
    "七年级",
    "八年级",
    "九年级",
    "高一",
    "高二",
    "高三",
}


def infer_student_name(original_name: str) -> tuple[str, bool]:
    import re

    base = Path(original_name).stem
    base = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", base)
    tokens = [token for token in re.split(r"[_\-\s—]+", base) if token]
    for token in tokens:
        if token in _EXCLUDED_NAME_TOKENS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", token):
            return token, False
    match = re.search(r"[\u4e00-\u9fff]{2,4}", base)
    if match and match.group() not in _EXCLUDED_NAME_TOKENS:
        return match.group(), False
    return "待补充姓名", True


def _submission(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "answerVersionId": row["answer_version_id"],
        "studentName": row["student_name"],
        "studentNameNeedsReview": bool(row["student_name_needs_review"]),
        "status": row["status"],
        "originalName": row["original_name"],
        "mimeType": row["mime_type"],
        "fileSize": int(row["size"]),
        "previewUrl": f"/api/files/{row['file_id']}",
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
        "modelTotalScore": (
            float(row["model_total_score"]) if row["model_total_score"] is not None else None
        ),
        "finalTotalScore": (
            float(row["final_total_score"]) if row["final_total_score"] is not None else None
        ),
        "confirmedBy": row["confirmed_by"],
        "confirmedAt": row["confirmed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


async def create_submission(
    database: Database,
    task_id: str,
    file: PersistedFile,
    student_name: str,
    student_name_needs_review: bool,
) -> dict[str, Any]:
    active = require_found(
        await database.fetch_one(
            """
            SELECT active_answer_version_id, answer_config_status
            FROM grading_tasks WHERE id = ?
            """,
            (task_id,),
        ),
        "批改任务不存在",
    )
    if (
        not active["active_answer_version_id"]
        or active["answer_config_status"] != "approved"
    ):
        raise AppError(409, "ANSWER_CONFIG_NOT_APPROVED", "答案配置尚未审核发布")
    submission_id = str(uuid4())
    now = now_iso()
    async with database.transaction() as connection:
        await insert_stored_file(database, file, task_id, connection=connection)
        await database.execute(
            """
            INSERT INTO submissions
              (id, task_id, answer_version_id, file_id, student_name,
               student_name_needs_review, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                submission_id,
                task_id,
                active["active_answer_version_id"],
                file.id,
                student_name,
                int(student_name_needs_review),
                now,
                now,
            ),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            submission_id=submission_id,
            event_type="submission.uploaded",
            payload={
                "originalName": file.original_name,
                "studentName": student_name,
                "studentNameNeedsReview": student_name_needs_review,
                "answerVersionId": active["active_answer_version_id"],
            },
            connection=connection,
        )
    return await get_submission(database, submission_id)


async def get_submission(database: Database, submission_id: str) -> dict[str, Any]:
    return _submission(
        require_found(
            await database.fetch_one(
                f"{_SUBMISSION_SELECT} WHERE s.id = ?",
                (submission_id,),
            ),
            "学生试卷不存在",
        )
    )


async def list_submissions(database: Database, task_id: str) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        f"""
        {_SUBMISSION_SELECT}
        WHERE s.task_id = ?
        ORDER BY s.created_at, s.id
        """,
        (task_id,),
    )
    return [_submission(row) for row in rows]


async def update_student_name(
    database: Database,
    submission_id: str,
    student_name: str,
) -> dict[str, Any]:
    before = await get_submission(database, submission_id)
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE submissions
            SET student_name = ?, student_name_needs_review = 0, updated_at = ?
            WHERE id = ?
            """,
            (student_name, now_iso(), submission_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=before["taskId"],
            submission_id=submission_id,
            event_type="submission.student_name_updated",
            payload={"before": before["studentName"], "after": student_name},
            connection=connection,
        )
    return await get_submission(database, submission_id)


async def set_submission_status(
    database: Database,
    submission_id: str,
    status: str,
    error: dict[str, str] | None = None,
) -> None:
    await database.execute(
        """
        UPDATE submissions
        SET status = ?, error_code = ?, error_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            error.get("code") if error else None,
            error.get("message") if error else None,
            now_iso(),
            submission_id,
        ),
    )


async def list_processable_submissions(
    database: Database,
    task_id: str,
) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        f"""
        {_SUBMISSION_SELECT}
        WHERE s.task_id = ? AND s.status IN ('queued', 'failed')
        ORDER BY s.created_at
        """,
        (task_id,),
    )
    return [_submission(row) for row in rows]


async def get_progress(database: Database, task_id: str) -> dict[str, int]:
    row = await database.fetch_one(
        """
        SELECT COUNT(*) AS total,
          SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
          SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing,
          SUM(CASE WHEN status = 'review_pending' THEN 1 ELSE 0 END)
            AS review_pending,
          SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM submissions WHERE task_id = ?
        """,
        (task_id,),
    )
    return {
        "total": int(row["total"] or 0) if row else 0,
        "queued": int(row["queued"] or 0) if row else 0,
        "processing": int(row["processing"] or 0) if row else 0,
        "reviewPending": int(row["review_pending"] or 0) if row else 0,
        "confirmed": int(row["confirmed"] or 0) if row else 0,
        "failed": int(row["failed"] or 0) if row else 0,
    }


async def get_submission_file_record(
    database: Database,
    submission_id: str,
) -> dict[str, str]:
    row = require_found(
        await database.fetch_one(
            """
            SELECT f.relative_path, f.mime_type, f.original_name
            FROM submissions s
            JOIN stored_files f ON f.id = s.file_id
            WHERE s.id = ?
            """,
            (submission_id,),
        ),
        "学生试卷文件不存在",
    )
    return {
        "relativePath": str(row["relative_path"]),
        "mimeType": str(row["mime_type"]),
        "originalName": str(row["original_name"]),
    }


async def find_submission_navigation(
    database: Database,
    task_id: str,
    submission_id: str,
) -> dict[str, str | None]:
    rows = await database.fetch_all(
        """
        SELECT id FROM submissions
        WHERE task_id = ?
        ORDER BY created_at, id
        """,
        (task_id,),
    )
    ids = [str(row["id"]) for row in rows]
    try:
        index = ids.index(submission_id)
    except ValueError:
        return {"previousId": None, "nextId": None}
    return {
        "previousId": ids[index - 1] if index > 0 else None,
        "nextId": ids[index + 1] if index < len(ids) - 1 else None,
    }
