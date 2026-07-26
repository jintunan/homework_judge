from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import aiosqlite

from ...errors import AppError, require_found
from ...files.storage import PersistedFile
from ...schemas import QuestionInput, TaskInput, TaskUpdate
from ..database import Database, json_dumps, json_loads, now_iso
from .audit import record_audit

_TASK_SUMMARY_SELECT = """
SELECT t.*,
  COUNT(DISTINCT q.id) AS question_count,
  COALESCE((
    SELECT SUM(max_score) FROM questions
    WHERE answer_version_id = t.active_answer_version_id
  ), 0) AS total_score,
  COALESCE((SELECT COUNT(*) FROM submissions WHERE task_id = t.id), 0)
    AS total_submissions,
  COALESCE((
    SELECT COUNT(*) FROM submissions WHERE task_id = t.id AND status = 'queued'
  ), 0) AS queued_count,
  COALESCE((
    SELECT COUNT(*) FROM submissions WHERE task_id = t.id AND status = 'processing'
  ), 0) AS processing_count,
  COALESCE((
    SELECT COUNT(*) FROM submissions WHERE task_id = t.id AND status = 'review_pending'
  ), 0) AS review_pending_count,
  COALESCE((
    SELECT COUNT(*) FROM submissions WHERE task_id = t.id AND status = 'confirmed'
  ), 0) AS confirmed_count,
  COALESCE((
    SELECT COUNT(*) FROM submissions WHERE task_id = t.id AND status = 'failed'
  ), 0) AS failed_count
FROM grading_tasks t
LEFT JOIN questions q ON q.answer_version_id = t.active_answer_version_id
"""


def _version(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "versionNumber": int(row["version_number"]),
        "status": row["status"],
        "answerMode": row["answer_mode"],
        "extractionIssues": json_loads(row.get("extraction_issues_json"), []),
        "unresolvedIssueCount": int(row.get("unresolved_issue_count") or 0),
        "createdAt": row["created_at"],
        "approvedBy": row["approved_by"],
        "approvedAt": row["approved_at"],
    }


async def get_answer_version(database: Database, version_id: str) -> dict[str, Any]:
    row = require_found(
        await database.fetch_one(
            "SELECT * FROM answer_config_versions WHERE id = ?",
            (version_id,),
        ),
        "答案版本不存在",
    )
    result = _version(row)
    assert result is not None
    return result


async def _active_version(database: Database, task_id: str) -> dict[str, Any] | None:
    return _version(
        await database.fetch_one(
            """
            SELECT v.*
            FROM answer_config_versions v
            JOIN grading_tasks t ON t.active_answer_version_id = v.id
            WHERE t.id = ?
            """,
            (task_id,),
        )
    )


async def _summary(database: Database, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "className": row["class_name"],
        "paperName": row["paper_name"],
        "subject": row["subject"],
        "answerMode": row["answer_mode"],
        "answerConfigStatus": row["answer_config_status"],
        "activeAnswerVersion": await _active_version(database, str(row["id"])),
        "status": row["status"],
        "questionCount": int(row["question_count"]),
        "totalScore": float(row["total_score"]),
        "progress": {
            "total": int(row["total_submissions"]),
            "queued": int(row["queued_count"]),
            "processing": int(row["processing_count"]),
            "reviewPending": int(row["review_pending_count"]),
            "confirmed": int(row["confirmed_count"]),
            "failed": int(row["failed_count"]),
        },
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


async def insert_stored_file(
    database: Database,
    file: PersistedFile,
    task_id: str | None,
    *,
    connection: aiosqlite.Connection,
) -> None:
    await database.execute(
        """
        INSERT INTO stored_files
          (id, task_id, kind, original_name, stored_name, mime_type, size,
           relative_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file.id,
            task_id,
            file.kind,
            file.original_name,
            file.stored_name,
            file.mime_type,
            file.size,
            file.relative_path,
            now_iso(),
        ),
        connection=connection,
    )


async def create_task(
    database: Database,
    task_input: TaskInput,
    template_file: PersistedFile,
    reference_file: PersistedFile | None,
) -> dict[str, Any]:
    task_id = str(uuid4())
    now = now_iso()
    async with database.transaction() as connection:
        await insert_stored_file(database, template_file, task_id, connection=connection)
        if reference_file is not None:
            await insert_stored_file(database, reference_file, task_id, connection=connection)
        await database.execute(
            """
            INSERT INTO grading_tasks
              (id, name, class_name, paper_name, subject, answer_mode,
               template_file_id, reference_answer_file_id, answer_config_status,
               status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'not_started', 'draft', ?, ?)
            """,
            (
                task_id,
                task_input.name,
                task_input.class_name,
                task_input.paper_name,
                task_input.subject.value,
                task_input.answer_mode.value,
                template_file.id,
                reference_file.id if reference_file else None,
                now,
                now,
            ),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            event_type="task.created",
            payload={
                **task_input.model_dump(by_alias=True, mode="json"),
                "templateOriginalName": template_file.original_name,
                "referenceAnswerOriginalName": (
                    reference_file.original_name if reference_file else None
                ),
            },
            connection=connection,
        )
    return await get_task(database, task_id)


async def list_tasks(database: Database) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        f"""
        {_TASK_SUMMARY_SELECT}
        GROUP BY t.id
        ORDER BY t.updated_at DESC
        """
    )
    return [await _summary(database, row) for row in rows]


async def get_task_summary(database: Database, task_id: str) -> dict[str, Any]:
    row = require_found(
        await database.fetch_one(
            f"""
            {_TASK_SUMMARY_SELECT}
            WHERE t.id = ?
            GROUP BY t.id
            """,
            (task_id,),
        ),
        "批改任务不存在",
    )
    return await _summary(database, row)


async def list_questions(
    database: Database,
    task_id: str,
    answer_version_id: str | None | object = ...,
) -> list[dict[str, Any]]:
    if answer_version_id is ...:
        task = await database.fetch_one(
            "SELECT active_answer_version_id FROM grading_tasks WHERE id = ?",
            (task_id,),
        )
        version_id = task["active_answer_version_id"] if task else None
    else:
        version_id = answer_version_id
    if not isinstance(version_id, str) or not version_id:
        return []
    rows = await database.fetch_all(
        """
        SELECT id, task_id, answer_version_id, question_text, number, type,
               max_score, standard_answer, scoring_points_json, sort_order
        FROM questions
        WHERE task_id = ? AND answer_version_id = ?
        ORDER BY sort_order, number
        """,
        (task_id, version_id),
    )
    return [
        {
            "id": row["id"],
            "taskId": row["task_id"],
            "answerVersionId": row["answer_version_id"],
            "questionText": row["question_text"],
            "number": row["number"],
            "type": row["type"],
            "maxScore": float(row["max_score"]),
            "standardAnswer": row["standard_answer"],
            "scoringPoints": json_loads(row["scoring_points_json"], []),
            "sortOrder": int(row["sort_order"]),
        }
        for row in rows
    ]


def _file(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "originalName": row["original_name"],
        "mimeType": row["mime_type"],
        "size": int(row["size"]),
        "previewUrl": f"/api/files/{row['id']}",
        "createdAt": row["created_at"],
    }


async def _task_file(database: Database, task_id: str, column: str) -> dict[str, Any] | None:
    if column not in {"template_file_id", "reference_answer_file_id"}:
        raise ValueError("unsupported task file column")
    return _file(
        await database.fetch_one(
            f"""
            SELECT id, kind, original_name, mime_type, size, created_at
            FROM stored_files
            WHERE id = (SELECT {column} FROM grading_tasks WHERE id = ?)
            """,
            (task_id,),
        )
    )


async def get_task(database: Database, task_id: str) -> dict[str, Any]:
    summary = await get_task_summary(database, task_id)
    return {
        **summary,
        "templateFile": await _task_file(database, task_id, "template_file_id"),
        "referenceAnswerFile": await _task_file(
            database,
            task_id,
            "reference_answer_file_id",
        ),
        "questions": await list_questions(database, task_id),
    }


async def update_task(
    database: Database,
    task_id: str,
    task_update: TaskUpdate,
) -> dict[str, Any]:
    current = await get_task(database, task_id)
    updates = task_update.model_dump(exclude_none=True)
    next_values = {
        "name": updates.get("name", current["name"]),
        "class_name": updates.get("class_name", current["className"]),
        "paper_name": updates.get("paper_name", current["paperName"]),
        "subject": (
            updates["subject"].value if "subject" in updates else current["subject"]
        ),
        "answer_mode": (
            updates["answer_mode"].value
            if "answer_mode" in updates
            else current["answerMode"]
        ),
    }
    if (
        (next_values["subject"] != current["subject"])
        or (next_values["answer_mode"] != current["answerMode"])
    ) and current["answerConfigStatus"] != "not_started":
        raise AppError(
            409,
            "TASK_CONFIGURATION_LOCKED",
            "答案配置开始后不能修改科目或答案来源模式",
        )
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE grading_tasks
            SET name = ?, class_name = ?, paper_name = ?, subject = ?,
                answer_mode = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_values["name"],
                next_values["class_name"],
                next_values["paper_name"],
                next_values["subject"],
                next_values["answer_mode"],
                now_iso(),
                task_id,
            ),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            event_type="task.updated",
            payload={"before": current, "after": next_values},
            connection=connection,
        )
    return await get_task(database, task_id)


async def save_questions(
    database: Database,
    task_id: str,
    questions: list[QuestionInput],
) -> dict[str, Any]:
    task = await get_task_summary(database, task_id)
    count_row = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM submissions WHERE task_id = ?",
        (task_id,),
    )
    if int(count_row["count"] if count_row else 0) > 0:
        raise AppError(
            409,
            "QUESTIONS_LOCKED",
            "已有学生试卷后不能通过兼容接口修改题目配置",
        )
    now = now_iso()
    version_id = str(uuid4())
    async with database.transaction() as connection:
        version_row = await database.fetch_one(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1 AS value
            FROM answer_config_versions WHERE task_id = ?
            """,
            (task_id,),
            connection=connection,
        )
        version_number = int(version_row["value"] if version_row else 1)
        await database.execute(
            """
            UPDATE answer_config_versions SET status = 'superseded'
            WHERE task_id = ? AND status = 'approved'
            """,
            (task_id,),
            connection=connection,
        )
        await database.execute(
            """
            INSERT INTO answer_config_versions
              (id, task_id, version_number, status, answer_mode,
               extraction_issues_json, unresolved_issue_count,
               created_at, approved_by, approved_at)
            VALUES (?, ?, ?, 'approved', ?, '[]', 0, ?, ?, ?)
            """,
            (
                version_id,
                task_id,
                version_number,
                task["answerMode"],
                now,
                database.settings.teacher_name,
                now,
            ),
            connection=connection,
        )
        for index, question in enumerate(questions):
            await database.execute(
                """
                INSERT INTO questions
                  (id, task_id, answer_version_id, source_draft_id, number,
                   question_text, type, max_score, standard_answer,
                   scoring_points_json, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question.id or str(uuid4()),
                    task_id,
                    version_id,
                    question.number,
                    question.type.value,
                    float(question.max_score),
                    question.standard_answer,
                    json_dumps(question.scoring_points),
                    question.sort_order if question.sort_order else index,
                    now,
                    now,
                ),
                connection=connection,
            )
        await database.execute(
            """
            UPDATE grading_tasks
            SET status = 'ready', answer_config_status = 'approved',
                active_answer_version_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (version_id, now, task_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            event_type="questions.replaced",
            payload={
                "nextCount": len(questions),
                "answerVersionId": version_id,
                "totalScore": float(
                    sum((question.max_score for question in questions), Decimal(0))
                ),
            },
            connection=connection,
        )
    return await get_task(database, task_id)


async def set_task_status(database: Database, task_id: str, status: str) -> None:
    await database.execute(
        "UPDATE grading_tasks SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), task_id),
    )


async def set_answer_config_status(database: Database, task_id: str, status: str) -> None:
    await database.execute(
        """
        UPDATE grading_tasks
        SET answer_config_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, now_iso(), task_id),
    )


async def get_stored_file(database: Database, file_id: str) -> dict[str, Any]:
    return require_found(
        await database.fetch_one(
            """
            SELECT id, task_id, kind, original_name, stored_name, mime_type,
                   size, relative_path, created_at
            FROM stored_files WHERE id = ?
            """,
            (file_id,),
        ),
        "文件不存在",
    )
