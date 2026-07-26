from __future__ import annotations

from typing import Any
from uuid import uuid4

import aiosqlite

from ..database import Database, json_dumps, json_loads, now_iso


async def record_audit(
    database: Database,
    *,
    task_id: str | None,
    event_type: str,
    payload: Any,
    submission_id: str | None = None,
    actor: str | None = None,
    connection: aiosqlite.Connection | None = None,
) -> None:
    await database.execute(
        """
        INSERT INTO audit_events
          (id, task_id, submission_id, event_type, payload_json, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            task_id,
            submission_id,
            event_type,
            json_dumps(payload),
            actor or database.settings.teacher_name,
            now_iso(),
        ),
        connection=connection,
    )


async def list_audit_events(
    database: Database,
    *,
    task_id: str | None = None,
    submission_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if submission_id:
        clauses.append("submission_id = ?")
        params.append(submission_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await database.fetch_all(
        f"""
        SELECT id, event_type, actor, payload_json, created_at
        FROM audit_events
        {where}
        ORDER BY created_at DESC, id DESC
        """,
        params,
    )
    return [
        {
            "id": row["id"],
            "eventType": row["event_type"],
            "actor": row["actor"],
            "payload": json_loads(row["payload_json"], {}),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]
