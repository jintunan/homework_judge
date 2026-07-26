from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from ...errors import AppError, require_found
from ...grading.output import GradeOutput
from ...schemas import ReviewUpdate
from ..database import Database, json_loads, now_iso
from .audit import record_audit
from .model_runs import get_latest_model_run
from .submissions import (
    find_submission_navigation,
    get_submission,
)
from .tasks import (
    get_answer_version,
    get_task_summary,
    list_questions,
)


def _review(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "questionId": row["question_id"],
        "questionNumber": row["question_number"],
        "questionType": row["question_type"],
        "maxScore": float(row["max_score"]),
        "standardAnswer": row["standard_answer"],
        "scoringPoints": json_loads(row["scoring_points_json"], []),
        "modelAnswer": row["model_answer"],
        "modelScore": float(row["model_score"]),
        "modelReason": row["model_reason"],
        "confidence": float(row["confidence"]),
        "finalAnswer": row["final_answer"],
        "finalScore": float(row["final_score"]),
        "teacherComment": row["teacher_comment"],
        "reviewStatus": row["review_status"],
        "updatedAt": row["updated_at"],
    }


async def list_question_reviews(
    database: Database,
    submission_id: str,
) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT q.id AS question_id, q.number AS question_number,
               q.type AS question_type, q.max_score, q.standard_answer,
               q.scoring_points_json, r.model_answer, r.model_score,
               r.model_reason, r.confidence, r.final_answer, r.final_score,
               r.teacher_comment, r.review_status, r.updated_at
        FROM question_reviews r
        JOIN questions q ON q.id = r.question_id
        WHERE r.submission_id = ?
        ORDER BY q.sort_order, q.number
        """,
        (submission_id,),
    )
    return [_review(row) for row in rows]


async def save_model_reviews(
    database: Database,
    submission_id: str,
    model_run_id: str,
    output: GradeOutput,
) -> None:
    submission = await get_submission(database, submission_id)
    questions = await list_questions(
        database,
        str(submission["taskId"]),
        submission["answerVersionId"],
    )
    output_by_number = {
        result.question_number: result for result in output.questions
    }
    now = now_iso()
    model_total = Decimal(0)
    attention_count = 0
    async with database.transaction() as connection:
        for question in questions:
            result = output_by_number.get(str(question["number"]))
            if result is None:
                raise AppError(
                    422,
                    "MODEL_OUTPUT_MISSING_QUESTION",
                    f"模型结果缺少第 {question['number']} 题",
                )
            max_score = Decimal(str(question["maxScore"]))
            normalized_score = min(
                max_score,
                max(Decimal(0), Decimal(str(result.suggested_score))),
            )
            model_total += normalized_score
            needs_attention = (
                result.needs_attention
                or result.confidence < database.settings.low_confidence_threshold
            )
            attention_count += int(needs_attention)
            await database.execute(
                """
                INSERT INTO question_reviews
                  (id, submission_id, question_id, model_run_id, model_answer,
                   model_score, model_reason, confidence, final_answer,
                   final_score, teacher_comment, review_status, created_at,
                   updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                ON CONFLICT(submission_id, question_id) DO UPDATE SET
                  model_run_id = excluded.model_run_id,
                  model_answer = excluded.model_answer,
                  model_score = excluded.model_score,
                  model_reason = excluded.model_reason,
                  confidence = excluded.confidence,
                  final_answer = excluded.final_answer,
                  final_score = excluded.final_score,
                  teacher_comment = '',
                  review_status = excluded.review_status,
                  updated_at = excluded.updated_at
                """,
                (
                    str(uuid4()),
                    submission_id,
                    question["id"],
                    model_run_id,
                    result.recognized_answer,
                    float(normalized_score),
                    result.reason,
                    result.confidence,
                    result.recognized_answer,
                    float(normalized_score),
                    "needs_attention" if needs_attention else "pending",
                    now,
                    now,
                ),
                connection=connection,
            )
        await database.execute(
            """
            UPDATE submissions
            SET status = 'review_pending', error_code = NULL,
                error_message = NULL, model_total_score = ?,
                final_total_score = ?, confirmed_by = NULL,
                confirmed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (float(model_total), float(model_total), now, submission_id),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE grading_tasks SET status = 'reviewing', updated_at = ?
            WHERE id = ?
            """,
            (now, submission["taskId"]),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=submission["taskId"],
            submission_id=submission_id,
            event_type="model.completed",
            payload={
                "modelRunId": model_run_id,
                "modelTotalScore": float(model_total),
                "needsAttentionCount": attention_count,
            },
            connection=connection,
        )


async def get_submission_review(
    database: Database,
    submission_id: str,
) -> dict[str, Any]:
    submission = await get_submission(database, submission_id)
    return {
        "submission": submission,
        "task": await get_task_summary(database, str(submission["taskId"])),
        "answerVersion": (
            await get_answer_version(database, str(submission["answerVersionId"]))
            if submission["answerVersionId"]
            else None
        ),
        "modelRun": await get_latest_model_run(database, submission_id),
        "reviews": await list_question_reviews(database, submission_id),
        "navigation": await find_submission_navigation(
            database,
            str(submission["taskId"]),
            submission_id,
        ),
    }


async def update_question_review(
    database: Database,
    submission_id: str,
    question_id: str,
    review_update: ReviewUpdate,
) -> dict[str, Any]:
    submission = await get_submission(database, submission_id)
    before = require_found(
        next(
            (
                review
                for review in await list_question_reviews(database, submission_id)
                if review["questionId"] == question_id
            ),
            None,
        ),
        "逐题复核记录不存在",
    )
    if review_update.final_score > Decimal(str(before["maxScore"])):
        raise AppError(
            422,
            "SCORE_OUT_OF_RANGE",
            f"第 {before['questionNumber']} 题得分必须在 0 到 {before['maxScore']} 之间",
            {"finalScore": [f"得分不能超过 {before['maxScore']}"]},
        )
    now = now_iso()
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE question_reviews
            SET final_answer = ?, final_score = ?, teacher_comment = ?,
                review_status = ?, updated_at = ?
            WHERE submission_id = ? AND question_id = ?
            """,
            (
                review_update.final_answer,
                float(review_update.final_score),
                review_update.teacher_comment,
                review_update.review_status,
                now,
                submission_id,
                question_id,
            ),
            connection=connection,
        )
        total_row = await database.fetch_one(
            """
            SELECT COALESCE(SUM(final_score), 0) AS total
            FROM question_reviews WHERE submission_id = ?
            """,
            (submission_id,),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE submissions
            SET final_total_score = ?, status = 'review_pending',
                confirmed_by = NULL, confirmed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (float(total_row["total"] if total_row else 0), now, submission_id),
            connection=connection,
        )
        await record_audit(
            database,
            task_id=submission["taskId"],
            submission_id=submission_id,
            event_type=(
                "submission.reopened"
                if submission["status"] == "confirmed"
                else "review.updated"
            ),
            payload={
                "questionNumber": before["questionNumber"],
                "before": {
                    "finalAnswer": before["finalAnswer"],
                    "finalScore": before["finalScore"],
                    "teacherComment": before["teacherComment"],
                    "reviewStatus": before["reviewStatus"],
                },
                "after": review_update.model_dump(by_alias=True, mode="json"),
            },
            connection=connection,
        )
    return await get_submission_review(database, submission_id)


async def confirm_submission(
    database: Database,
    submission_id: str,
) -> dict[str, Any]:
    submission = await get_submission(database, submission_id)
    if submission["studentNameNeedsReview"] or not str(submission["studentName"]).strip():
        raise AppError(409, "STUDENT_NAME_REQUIRED", "请先补充并确认学生姓名")
    questions = await list_questions(
        database,
        str(submission["taskId"]),
        submission["answerVersionId"],
    )
    reviews = await list_question_reviews(database, submission_id)
    if len(reviews) != len(questions):
        raise AppError(409, "INCOMPLETE_REVIEW", "模型结果不完整，请重新批改或人工补齐")
    unresolved = [review for review in reviews if review["reviewStatus"] != "reviewed"]
    if unresolved:
        raise AppError(
            409,
            "UNRESOLVED_QUESTIONS",
            f"仍有 {len(unresolved)} 道题未完成教师复核",
        )
    total = sum((Decimal(str(review["finalScore"])) for review in reviews), Decimal(0))
    now = now_iso()
    async with database.transaction() as connection:
        await database.execute(
            """
            UPDATE submissions
            SET status = 'confirmed', final_total_score = ?,
                confirmed_by = ?, confirmed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                float(total),
                database.settings.teacher_name,
                now,
                now,
                submission_id,
            ),
            connection=connection,
        )
        remaining = await database.fetch_one(
            """
            SELECT COUNT(*) AS count FROM submissions
            WHERE task_id = ? AND status != 'confirmed'
            """,
            (submission["taskId"],),
            connection=connection,
        )
        if int(remaining["count"] if remaining else 0) == 0:
            await database.execute(
                """
                UPDATE grading_tasks SET status = 'completed', updated_at = ?
                WHERE id = ?
                """,
                (now, submission["taskId"]),
                connection=connection,
            )
        await record_audit(
            database,
            task_id=submission["taskId"],
            submission_id=submission_id,
            event_type="submission.confirmed",
            payload={"finalTotalScore": float(total), "confirmedAt": now},
            connection=connection,
        )
    return await get_submission_review(database, submission_id)
