from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from ..db.database import Database, json_dumps, now_iso
from ..db.repositories.answer_config import (
    create_answer_version,
    get_answer_version,
    list_answer_drafts,
)
from ..db.repositories.audit import record_audit
from ..errors import AppError, require_found


def _points_total(points: list[dict[str, Any]]) -> Decimal:
    return sum((Decimal(str(point.get("score", 0))) for point in points), Decimal(0))


async def publish_answer_version(
    database: Database,
    version_id: str,
) -> dict[str, Any]:
    version = await get_answer_version(database, version_id)
    if version["status"] not in {"draft", "review_pending"}:
        raise AppError(409, "ANSWER_VERSION_READ_ONLY", "答案版本已发布或已被替代")
    if version["unresolvedIssueCount"] > 0:
        raise AppError(
            422,
            "ANSWER_EXTRACTION_BLOCKED",
            "试卷识别仍有未解决的版本级问题，请重新识别",
        )
    drafts = await list_answer_drafts(database, version_id)
    if not drafts:
        raise AppError(422, "ANSWER_DRAFTS_EMPTY", "没有可发布的答案草稿")
    if any(draft["requiresCorrection"] for draft in drafts):
        raise AppError(
            422,
            "DRAFT_CORRECTION_REQUIRED",
            "仍有题目的临时题号或结构问题需要教师修正",
        )
    if any(draft["reviewStatus"] != "approved" for draft in drafts):
        raise AppError(
            422,
            "ANSWER_REVIEW_INCOMPLETE",
            "所有题目必须由教师逐题确认后才能发布",
        )
    numbers = [str(draft["effectiveNumber"]) for draft in drafts]
    if len(numbers) != len(set(numbers)):
        raise AppError(422, "DUPLICATE_QUESTION_NUMBER", "题号不能重复")
    for draft in drafts:
        if not str(draft["effectiveAnswer"]).strip():
            raise AppError(422, "ANSWER_REQUIRED", f"第 {draft['effectiveNumber']} 题答案为空")
        if _points_total(draft["effectiveScoringPoints"]) > Decimal(
            str(draft["effectiveMaxScore"])
        ):
            raise AppError(
                422,
                "SCORING_POINTS_OVERFLOW",
                f"第 {draft['effectiveNumber']} 题评分点合计超过满分",
            )

    now = now_iso()
    async with database.transaction() as connection:
        await database.execute(
            "DELETE FROM questions WHERE answer_version_id = ?",
            (version_id,),
            connection=connection,
        )
        for index, draft in enumerate(drafts):
            await database.execute(
                """
                INSERT INTO questions
                  (id, task_id, answer_version_id, source_draft_id, number,
                   question_text, type, max_score, standard_answer,
                   scoring_points_json, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    version["taskId"],
                    version_id,
                    draft["id"],
                    draft["effectiveNumber"],
                    draft["questionText"],
                    draft["effectiveType"],
                    draft["effectiveMaxScore"],
                    draft["effectiveAnswer"],
                    json_dumps(draft["effectiveScoringPoints"]),
                    index,
                    now,
                    now,
                ),
                connection=connection,
            )
        await database.execute(
            """
            UPDATE answer_config_versions
            SET status = 'superseded'
            WHERE task_id = ? AND status = 'approved' AND id != ?
            """,
            (version["taskId"], version_id),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE answer_config_versions
            SET status = 'approved', approved_by = ?, approved_at = ?
            WHERE id = ?
            """,
            (database.settings.teacher_name, now, version_id),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE grading_tasks
            SET active_answer_version_id = ?, answer_config_status = 'approved',
                status = 'ready', updated_at = ?
            WHERE id = ?
            """,
            (version_id, now, version["taskId"]),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=version["taskId"],
            event_type="answer_config.published",
            payload={
                "versionId": version_id,
                "versionNumber": version["versionNumber"],
                "questionCount": len(drafts),
                "totalScore": sum(
                    float(draft["effectiveMaxScore"]) for draft in drafts
                ),
            },
            connection=connection,
        )
    return await get_answer_version(database, version_id)


async def create_revision(database: Database, task_id: str) -> dict[str, Any]:
    task = require_found(
        await database.fetch_one(
            """
            SELECT active_answer_version_id, answer_mode
            FROM grading_tasks WHERE id = ?
            """,
            (task_id,),
        ),
        "批改任务不存在",
    )
    source_version_id = task["active_answer_version_id"]
    if not source_version_id:
        raise AppError(409, "NO_APPROVED_ANSWER_VERSION", "当前任务没有可修订的已发布答案")
    existing = await database.fetch_one(
        """
        SELECT id FROM answer_config_versions
        WHERE task_id = ? AND status IN ('draft', 'review_pending')
        ORDER BY version_number DESC LIMIT 1
        """,
        (task_id,),
    )
    if existing:
        return await get_answer_version(database, str(existing["id"]))

    version = await create_answer_version(
        database,
        task_id,
        str(task["answer_mode"]),
    )
    questions = await database.fetch_all(
        """
        SELECT * FROM questions
        WHERE task_id = ? AND answer_version_id = ?
        ORDER BY sort_order, number
        """,
        (task_id, source_version_id),
    )
    now = now_iso()
    async with database.transaction() as connection:
        for index, question in enumerate(questions):
            await database.execute(
                """
                INSERT INTO answer_question_drafts
                  (id, version_id, number, question_text, type, max_score,
                   auto_answer, auto_scoring_points_json, auto_reason, source_type,
                   confidence, needs_attention, parse_issues_json,
                   normalization_json, requires_correction, review_status,
                   sort_order, created_at, updated_at)
                VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, '从已发布版本创建修订', 'reference_extracted',
                  1, 0, '[]', '[]', 0, 'pending', ?, ?, ?
                )
                """,
                (
                    str(uuid4()),
                    version["id"],
                    question["number"],
                    question["question_text"],
                    question["type"],
                    question["max_score"],
                    question["standard_answer"],
                    question["scoring_points_json"],
                    index,
                    now,
                    now,
                ),
                connection=connection,
            )
        await database.execute(
            """
            UPDATE answer_config_versions SET status = 'review_pending'
            WHERE id = ?
            """,
            (version["id"],),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE grading_tasks
            SET answer_config_status = 'review_pending', updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            event_type="answer_config.revision_created",
            payload={
                "sourceVersionId": source_version_id,
                "versionId": version["id"],
                "questionCount": len(questions),
            },
            connection=connection,
        )
    return await get_answer_version(database, str(version["id"]))
