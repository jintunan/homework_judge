from __future__ import annotations

from .database import Database, now_iso


async def recover_interrupted_work(database: Database) -> dict[str, int]:
    now = now_iso()
    async with database.transaction() as connection:
        answer_rows = await database.fetch_all(
            """
            SELECT id, task_id, draft_question_id
            FROM answer_resolution_runs WHERE status = 'running'
            """,
            connection=connection,
        )
        model_rows = await database.fetch_all(
            """
            SELECT id, submission_id
            FROM model_runs WHERE status = 'running'
            """,
            connection=connection,
        )
        submission_rows = await database.fetch_all(
            """
            SELECT id, task_id FROM submissions WHERE status = 'processing'
            """,
            connection=connection,
        )
        await database.execute(
            """
            UPDATE answer_resolution_runs
            SET status = 'request_failed', error_code = 'PROCESS_INTERRUPTED',
                error_message = '服务重启时任务仍在运行，请重试',
                finished_at = ?
            WHERE status = 'running'
            """,
            (now,),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE model_runs
            SET status = 'request_failed',
                error_message = '服务重启时任务仍在运行，请重试',
                finished_at = ?
            WHERE status = 'running'
            """,
            (now,),
            connection=connection,
        )
        await database.execute(
            """
            UPDATE submissions
            SET status = 'failed', error_code = 'PROCESS_INTERRUPTED',
                error_message = '服务重启中断了本次批改，请重试',
                updated_at = ?
            WHERE status = 'processing'
            """,
            (now,),
            connection=connection,
        )
        for row in answer_rows:
            if row["draft_question_id"]:
                await database.execute(
                    """
                    UPDATE answer_question_drafts
                    SET review_status = 'failed', needs_attention = 1,
                        rejection_reason = '服务重启中断了本次答案处理，请重试',
                        updated_at = ?
                    WHERE id = ? AND review_status != 'approved'
                    """,
                    (now, row["draft_question_id"]),
                    connection=connection,
                )
            await database.execute(
                """
                UPDATE grading_tasks
                SET answer_config_status = 'failed', updated_at = ?
                WHERE id = ? AND answer_config_status IN (
                  'queued', 'extracting', 'searching', 'generating'
                )
                """,
                (now, row["task_id"]),
                connection=connection,
            )
        for row in submission_rows:
            await database.execute(
                """
                UPDATE grading_tasks
                SET status = 'reviewing', updated_at = ?
                WHERE id = ? AND status = 'grading'
                """,
                (now, row["task_id"]),
                connection=connection,
            )
    return {
        "answerRuns": len(answer_rows),
        "modelRuns": len(model_rows),
        "submissions": len(submission_rows),
    }
