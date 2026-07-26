from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..database import Database, json_dumps, json_loads, now_iso


def _model_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "model": row["model"],
        "status": row["status"],
        "requestSnapshot": json_loads(row["request_snapshot_json"], None),
        "rawResponse": json_loads(row["raw_response_json"], None),
        "parsedOutput": json_loads(row["parsed_output_json"], None),
        "usage": json_loads(row["usage_json"], None),
        "errorMessage": row["error_message"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


async def start_model_run(
    database: Database,
    submission_id: str,
    model: str,
    request_snapshot: Any,
) -> str:
    run_id = str(uuid4())
    await database.execute(
        """
        INSERT INTO model_runs
          (id, submission_id, provider, model, request_snapshot_json,
           status, started_at)
        VALUES (?, ?, '阿里云百炼', ?, ?, 'running', ?)
        """,
        (
            run_id,
            submission_id,
            model,
            json_dumps(request_snapshot),
            now_iso(),
        ),
    )
    return run_id


async def finish_model_run_success(
    database: Database,
    run_id: str,
    *,
    raw_response: Any,
    parsed_output: Any,
    usage: Any,
) -> None:
    await database.execute(
        """
        UPDATE model_runs
        SET raw_response_json = ?, parsed_output_json = ?, usage_json = ?,
            status = 'succeeded', error_message = NULL, finished_at = ?
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


async def finish_model_run_failure(
    database: Database,
    run_id: str,
    *,
    status: str,
    raw_response: Any,
    error_message: str,
) -> None:
    await database.execute(
        """
        UPDATE model_runs
        SET raw_response_json = ?, status = ?, error_message = ?, finished_at = ?
        WHERE id = ?
        """,
        (json_dumps(raw_response), status, error_message, now_iso(), run_id),
    )


async def get_latest_model_run(
    database: Database,
    submission_id: str,
) -> dict[str, Any] | None:
    row = await database.fetch_one(
        """
        SELECT id, provider, model, status, request_snapshot_json,
               raw_response_json, parsed_output_json, usage_json, error_message,
               started_at, finished_at
        FROM model_runs
        WHERE submission_id = ?
        ORDER BY started_at DESC LIMIT 1
        """,
        (submission_id,),
    )
    return _model_run(row) if row else None
