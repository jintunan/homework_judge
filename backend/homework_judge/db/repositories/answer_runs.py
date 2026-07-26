from __future__ import annotations

from typing import Any
from uuid import uuid4

from ...errors import require_found
from ...model.dashscope_search import SearchSource
from ..database import Database, json_dumps, json_loads, now_iso


async def list_search_sources(
    database: Database,
    draft_question_id: str,
) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM search_sources
        WHERE draft_question_id = ?
        ORDER BY retrieved_at DESC, rank, id
        """,
        (draft_question_id,),
    )
    return [
        {
            "id": row["id"],
            "runId": row["run_id"],
            "draftQuestionId": row["draft_question_id"],
            "title": row["title"],
            "url": row["url"],
            "snippet": row["snippet"],
            "rank": int(row["rank"]),
            "retrievedAt": row["retrieved_at"],
        }
        for row in rows
    ]


async def _run(database: Database, row: dict[str, Any]) -> dict[str, Any]:
    sources = (
        [
            source
            for source in await list_search_sources(database, row["draft_question_id"])
            if source["runId"] == row["id"]
        ]
        if row["draft_question_id"]
        else []
    )
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "versionId": row["version_id"],
        "draftQuestionId": row["draft_question_id"],
        "kind": row["kind"],
        "provider": row["provider"],
        "model": row["model"],
        "requestSnapshot": json_loads(row["request_snapshot_json"], None),
        "rawResponse": json_loads(row["raw_response_json"], None),
        "parsedOutput": json_loads(row["parsed_output_json"], None),
        "usage": json_loads(row["usage_json"], None),
        "status": row["status"],
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "sources": sources,
    }


async def start_answer_run(
    database: Database,
    *,
    task_id: str,
    version_id: str,
    kind: str,
    provider: str,
    model: str,
    request_snapshot: Any,
    draft_question_id: str | None = None,
) -> str:
    run_id = str(uuid4())
    now = now_iso()
    async with database.transaction() as connection:
        await database.execute(
            """
            INSERT INTO answer_resolution_runs
              (id, task_id, version_id, draft_question_id, kind, provider, model,
               request_snapshot_json, status, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                task_id,
                version_id,
                draft_question_id,
                kind,
                provider,
                model,
                json_dumps(request_snapshot),
                now,
            ),
            connection=connection,
        )
        if draft_question_id:
            await database.execute(
                """
                UPDATE answer_question_drafts
                SET latest_run_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_id, now, draft_question_id),
                connection=connection,
            )
    return run_id


async def finish_answer_run_success(
    database: Database,
    run_id: str,
    *,
    raw_response: Any,
    parsed_output: Any,
    usage: Any,
) -> None:
    await database.execute(
        """
        UPDATE answer_resolution_runs
        SET raw_response_json = ?, parsed_output_json = ?, usage_json = ?,
            status = 'succeeded', error_code = NULL, error_message = NULL,
            finished_at = ?
        WHERE id = ?
        """,
        (
            json_dumps(raw_response),
            json_dumps(parsed_output),
            json_dumps(usage),
            now_iso(),
            run_id,
        ),
    )


async def finish_answer_run_failure(
    database: Database,
    run_id: str,
    *,
    status: str,
    error_code: str,
    error_message: str,
    raw_response: Any = None,
) -> None:
    await database.execute(
        """
        UPDATE answer_resolution_runs
        SET raw_response_json = ?, status = ?, error_code = ?,
            error_message = ?, finished_at = ?
        WHERE id = ?
        """,
        (
            json_dumps(raw_response),
            status,
            error_code,
            error_message,
            now_iso(),
            run_id,
        ),
    )


async def save_search_sources(
    database: Database,
    run_id: str,
    draft_question_id: str,
    sources: list[SearchSource],
) -> None:
    retrieved_at = now_iso()
    async with database.transaction() as connection:
        for index, source in enumerate(sources):
            await database.execute(
                """
                INSERT INTO search_sources
                  (id, run_id, draft_question_id, title, url, snippet, rank,
                   retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    run_id,
                    draft_question_id,
                    source.title[:300],
                    source.url,
                    source.snippet[:1000],
                    index,
                    retrieved_at,
                ),
                connection=connection,
            )


async def get_answer_run(database: Database, run_id: str) -> dict[str, Any]:
    row = require_found(
        await database.fetch_one(
            "SELECT * FROM answer_resolution_runs WHERE id = ?",
            (run_id,),
        ),
        "答案处理记录不存在",
    )
    return await _run(database, row)


async def list_answer_runs(database: Database, version_id: str) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM answer_resolution_runs
        WHERE version_id = ?
        ORDER BY started_at DESC, id DESC
        """,
        (version_id,),
    )
    return [await _run(database, row) for row in rows]
