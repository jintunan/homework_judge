from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from ...errors import AppError, require_found
from ...schemas import (
    AnswerDraftUpdate,
    AnswerMode,
    NormalizedQuestion,
    ParsedPaper,
    ScoringPoint,
)
from ..database import Database, json_dumps, json_loads, now_iso
from .answer_runs import list_search_sources
from .audit import record_audit


def _version(row: dict[str, Any]) -> dict[str, Any]:
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


async def _draft(database: Database, row: dict[str, Any]) -> dict[str, Any]:
    auto_points: list[dict[str, Any]] = json_loads(
        row["auto_scoring_points_json"],
        [],
    )
    teacher_points: list[dict[str, Any]] | None = (
        json_loads(row["teacher_scoring_points_json"], [])
        if row["teacher_scoring_points_json"] is not None
        else None
    )
    return {
        "id": row["id"],
        "versionId": row["version_id"],
        "number": row["number"],
        "questionText": row["question_text"],
        "type": row["type"],
        "maxScore": float(row["max_score"]),
        "autoAnswer": row["auto_answer"],
        "autoScoringPoints": auto_points,
        "autoReason": row["auto_reason"],
        "sourceType": row["source_type"],
        "confidence": float(row["confidence"]),
        "needsAttention": bool(row["needs_attention"]),
        "parseIssues": json_loads(row.get("parse_issues_json"), []),
        "normalizations": json_loads(row.get("normalization_json"), []),
        "requiresCorrection": bool(row.get("requires_correction")),
        "teacherNumber": row["teacher_number"],
        "teacherType": row["teacher_type"],
        "teacherMaxScore": (
            float(row["teacher_max_score"]) if row["teacher_max_score"] is not None else None
        ),
        "teacherAnswer": row["teacher_answer"],
        "teacherScoringPoints": teacher_points,
        "rejectionReason": row["rejection_reason"],
        "reviewStatus": row["review_status"],
        "updatedBy": row["updated_by"],
        "updatedAt": row["updated_at"],
        "effectiveNumber": row["teacher_number"] or row["number"],
        "effectiveType": row["teacher_type"] or row["type"],
        "effectiveMaxScore": float(
            row["teacher_max_score"]
            if row["teacher_max_score"] is not None
            else row["max_score"]
        ),
        "effectiveAnswer": (
            row["teacher_answer"] if row["teacher_answer"] is not None else row["auto_answer"]
        ),
        "effectiveScoringPoints": (
            teacher_points if teacher_points is not None else auto_points
        ),
        "latestRunId": row["latest_run_id"],
        "sources": await list_search_sources(database, str(row["id"])),
    }


async def create_answer_version(
    database: Database,
    task_id: str,
    answer_mode: AnswerMode | str,
) -> dict[str, Any]:
    mode = answer_mode.value if isinstance(answer_mode, AnswerMode) else answer_mode
    task = require_found(
        await database.fetch_one(
            "SELECT id FROM grading_tasks WHERE id = ?",
            (task_id,),
        ),
        "批改任务不存在",
    )
    assert task
    version_id = str(uuid4())
    now = now_iso()
    async with database.transaction() as connection:
        row = await database.fetch_one(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1 AS value
            FROM answer_config_versions WHERE task_id = ?
            """,
            (task_id,),
            connection=connection,
        )
        version_number = int(row["value"] if row else 1)
        await database.execute(
            """
            UPDATE answer_config_versions
            SET status = 'superseded'
            WHERE task_id = ? AND status IN ('draft', 'review_pending')
            """,
            (task_id,),
            connection=connection,
        )
        await database.execute(
            """
            INSERT INTO answer_config_versions
              (id, task_id, version_number, status, answer_mode,
               extraction_issues_json, unresolved_issue_count, created_at)
            VALUES (?, ?, ?, 'draft', ?, '[]', 0, ?)
            """,
            (version_id, task_id, version_number, mode, now),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE grading_tasks
            SET answer_config_status = 'queued', updated_at = ?
            WHERE id = ?
            """,
            (now, task_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=task_id,
            event_type="answer_config.started",
            payload={
                "versionId": version_id,
                "versionNumber": version_number,
                "answerMode": mode,
            },
            connection=connection,
        )
    return await get_answer_version(database, version_id)


async def get_answer_version(database: Database, version_id: str) -> dict[str, Any]:
    return _version(
        require_found(
            await database.fetch_one(
                "SELECT * FROM answer_config_versions WHERE id = ?",
                (version_id,),
            ),
            "答案配置版本不存在",
        )
    )


async def get_editable_answer_version(
    database: Database,
    task_id: str,
) -> dict[str, Any] | None:
    row = await database.fetch_one(
        """
        SELECT * FROM answer_config_versions
        WHERE task_id = ? AND status IN ('draft', 'review_pending')
        ORDER BY version_number DESC LIMIT 1
        """,
        (task_id,),
    )
    return _version(row) if row else None


async def get_latest_answer_version(
    database: Database,
    task_id: str,
) -> dict[str, Any] | None:
    row = await database.fetch_one(
        """
        SELECT * FROM answer_config_versions
        WHERE task_id = ? AND status != 'superseded'
        ORDER BY version_number DESC LIMIT 1
        """,
        (task_id,),
    )
    if row is None:
        row = await database.fetch_one(
            """
            SELECT * FROM answer_config_versions
            WHERE task_id = ? ORDER BY version_number DESC LIMIT 1
            """,
            (task_id,),
        )
    return _version(row) if row else None


def _normalizations(question: NormalizedQuestion) -> list[dict[str, Any]]:
    return [
        issue.model_dump(by_alias=True, mode="json")
        for issue in question.issues
        if issue.code
        in {
            "scoring_points_scaled",
            "scoring_point_invalid",
            "scoring_points_truncated",
            "premature_answer_discarded",
        }
    ]


async def replace_drafts(
    database: Database,
    version_id: str,
    parsed_paper: ParsedPaper,
    source_type: str | None,
) -> list[dict[str, Any]]:
    version = await get_answer_version(database, version_id)
    now = now_iso()
    extraction_issues = [
        issue.model_dump(by_alias=True, mode="json") for issue in parsed_paper.issues
    ]
    unresolved = sum(
        1 for issue in parsed_paper.issues if issue.severity == "blocking"
    )
    async with database.transaction() as connection:
        await database.execute(
            "DELETE FROM answer_question_drafts WHERE version_id = ?",
            (version_id,),
            connection=connection,
        )
        for index, question in enumerate(parsed_paper.questions):
            await database.execute(
                """
                INSERT INTO answer_question_drafts
                  (id, version_id, number, question_text, type, max_score,
                   auto_answer, auto_scoring_points_json, auto_reason, source_type,
                   confidence, needs_attention, parse_issues_json,
                   normalization_json, requires_correction, review_status,
                   sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    version_id,
                    question.question_number,
                    question.question_text,
                    question.type.value,
                    float(question.max_score),
                    question.standard_answer,
                    json_dumps(question.scoring_points),
                    question.reason,
                    source_type,
                    question.confidence,
                    int(question.needs_attention or not question.standard_answer),
                    json_dumps(
                        [
                            issue.model_dump(by_alias=True, mode="json")
                            for issue in question.issues
                        ]
                    ),
                    json_dumps(_normalizations(question)),
                    int(question.requires_correction),
                    index,
                    now,
                    now,
                ),
                connection=connection,
            )
        await database.execute(
            """
            UPDATE answer_config_versions
            SET status = 'review_pending', extraction_issues_json = ?,
                unresolved_issue_count = ?
            WHERE id = ?
            """,
            (json_dumps(extraction_issues), unresolved, version_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=version["taskId"],
            event_type="answer_config.extracted",
            payload={
                "versionId": version_id,
                "questionCount": len(parsed_paper.questions),
                "sourceType": source_type,
                "issueCount": len(extraction_issues),
                "unresolvedIssueCount": unresolved,
                "repaired": parsed_paper.repaired,
            },
            connection=connection,
        )
    return await list_answer_drafts(database, version_id)


async def list_answer_drafts(
    database: Database,
    version_id: str,
) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM answer_question_drafts
        WHERE version_id = ?
        ORDER BY sort_order, number
        """,
        (version_id,),
    )
    return [await _draft(database, row) for row in rows]


async def get_answer_draft(database: Database, draft_id: str) -> dict[str, Any]:
    row = require_found(
        await database.fetch_one(
            "SELECT * FROM answer_question_drafts WHERE id = ?",
            (draft_id,),
        ),
        "答案草稿不存在",
    )
    return await _draft(database, row)


async def set_draft_resolution(
    database: Database,
    draft_id: str,
    *,
    standard_answer: str,
    scoring_points: list[ScoringPoint],
    reason: str,
    source_type: str,
    confidence: float,
    needs_attention: bool,
    latest_run_id: str,
    normalizations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if normalizations is None:
        row = await database.fetch_one(
            "SELECT normalization_json FROM answer_question_drafts WHERE id = ?",
            (draft_id,),
        )
        normalizations = json_loads(row["normalization_json"], []) if row else []
    await database.execute(
        """
        UPDATE answer_question_drafts
        SET auto_answer = ?, auto_scoring_points_json = ?, auto_reason = ?,
            source_type = ?, confidence = ?, needs_attention = ?,
            normalization_json = ?, review_status = 'pending',
            rejection_reason = NULL, latest_run_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            standard_answer,
            json_dumps(scoring_points),
            reason,
            source_type,
            confidence,
            int(needs_attention),
            json_dumps(normalizations),
            latest_run_id,
            now_iso(),
            draft_id,
        ),
    )
    return await get_answer_draft(database, draft_id)


async def mark_draft_failed(
    database: Database,
    draft_id: str,
    reason: str,
    latest_run_id: str | None,
) -> dict[str, Any]:
    await database.execute(
        """
        UPDATE answer_question_drafts
        SET review_status = 'failed', needs_attention = 1,
            rejection_reason = ?, latest_run_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (reason, latest_run_id, now_iso(), draft_id),
    )
    return await get_answer_draft(database, draft_id)


def _score_total(points: list[dict[str, Any]]) -> Decimal:
    return sum((Decimal(str(point.get("score", 0))) for point in points), Decimal(0))


async def update_answer_draft(
    database: Database,
    draft_id: str,
    draft_update: AnswerDraftUpdate,
) -> dict[str, Any]:
    draft = await get_answer_draft(database, draft_id)
    version = await get_answer_version(database, str(draft["versionId"]))
    if version["status"] not in {"draft", "review_pending"}:
        raise AppError(
            409,
            "ANSWER_VERSION_READ_ONLY",
            "已发布的答案版本为只读，请先创建修订版本",
        )
    total = sum((point.score for point in draft_update.scoring_points), Decimal(0))
    if total > draft_update.max_score:
        raise AppError(422, "SCORING_POINTS_OVERFLOW", "评分点合计不能超过题目满分")
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE answer_question_drafts
            SET teacher_number = ?, teacher_type = ?, teacher_max_score = ?,
                teacher_answer = ?, teacher_scoring_points_json = ?,
                requires_correction = 0, review_status = 'pending',
                updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                draft_update.number,
                draft_update.type.value,
                float(draft_update.max_score),
                draft_update.standard_answer,
                json_dumps(draft_update.scoring_points),
                database.settings.teacher_name,
                now_iso(),
                draft_id,
            ),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE grading_tasks
            SET answer_config_status = 'review_pending', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), version["taskId"]),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=version["taskId"],
            event_type="answer_draft.updated",
            payload={
                "draftId": draft_id,
                "before": draft,
                "after": draft_update.model_dump(by_alias=True, mode="json"),
            },
            connection=connection,
        )
    return await get_answer_draft(database, draft_id)


async def set_draft_review_status(
    database: Database,
    draft_id: str,
    status: str,
    reason: str = "",
) -> dict[str, Any]:
    if status not in {"approved", "rejected"}:
        raise ValueError("unsupported answer review status")
    draft = await get_answer_draft(database, draft_id)
    version = await get_answer_version(database, str(draft["versionId"]))
    if version["status"] not in {"draft", "review_pending"}:
        raise AppError(409, "ANSWER_VERSION_READ_ONLY", "已发布的答案版本为只读")
    if status == "approved":
        if draft["requiresCorrection"]:
            raise AppError(422, "DRAFT_CORRECTION_REQUIRED", "本题存在必须由教师修正的问题")
        if not str(draft["effectiveAnswer"]).strip():
            raise AppError(422, "ANSWER_REQUIRED", "标准答案不能为空")
        duplicate = await database.fetch_one(
            """
            SELECT id FROM answer_question_drafts
            WHERE version_id = ? AND id != ?
              AND COALESCE(teacher_number, number) = ?
            """,
            (draft["versionId"], draft_id, draft["effectiveNumber"]),
        )
        if duplicate:
            raise AppError(422, "DUPLICATE_QUESTION_NUMBER", "题号不能重复")
        if _score_total(draft["effectiveScoringPoints"]) > Decimal(
            str(draft["effectiveMaxScore"])
        ):
            raise AppError(422, "SCORING_POINTS_OVERFLOW", "评分点合计不能超过题目满分")
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE answer_question_drafts
            SET review_status = ?, rejection_reason = ?, updated_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                reason if status == "rejected" else None,
                database.settings.teacher_name,
                now_iso(),
                draft_id,
            ),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=version["taskId"],
            event_type=(
                "answer_draft.approved"
                if status == "approved"
                else "answer_draft.rejected"
            ),
            payload={"draftId": draft_id, "reason": reason},
            connection=connection,
        )
    return await get_answer_draft(database, draft_id)


async def get_answer_config_progress(
    database: Database,
    version_id: str,
) -> dict[str, int]:
    drafts = await list_answer_drafts(database, version_id)
    row = await database.fetch_one(
        """
        SELECT COUNT(*) AS count FROM answer_resolution_runs
        WHERE version_id = ? AND status = 'running'
        """,
        (version_id,),
    )
    return {
        "total": len(drafts),
        "pending": sum(draft["reviewStatus"] == "pending" for draft in drafts),
        "processing": int(row["count"] if row else 0),
        "webSearched": sum(draft["sourceType"] == "web_searched" for draft in drafts),
        "modelGenerated": sum(
            draft["sourceType"] == "model_generated" for draft in drafts
        ),
        "needsAttention": sum(bool(draft["needsAttention"]) for draft in drafts),
        "approved": sum(draft["reviewStatus"] == "approved" for draft in drafts),
        "rejected": sum(draft["reviewStatus"] == "rejected" for draft in drafts),
        "failed": sum(draft["reviewStatus"] == "failed" for draft in drafts),
    }


async def get_draft_task_context(database: Database, draft_id: str) -> dict[str, Any]:
    return require_found(
        await database.fetch_one(
            """
            SELECT v.task_id AS taskId, t.subject, d.version_id AS versionId
            FROM answer_question_drafts d
            JOIN answer_config_versions v ON v.id = d.version_id
            JOIN grading_tasks t ON t.id = v.task_id
            WHERE d.id = ?
            """,
            (draft_id,),
        ),
        "答案草稿不存在",
    )
