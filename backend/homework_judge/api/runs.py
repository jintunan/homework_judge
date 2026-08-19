from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..db.database import Database, json_loads
from ..errors import AppError
from .dependencies import get_database
from .response import success

router = APIRouter()


def _run(row: dict[str, Any], include_raw: bool = False) -> dict[str, Any]:
    value = dict(row)
    value["requestSummary"] = json_loads(value.pop("request_summary_json", None), {})
    value["usage"] = json_loads(value.pop("usage_json", None), {})
    value["parseIssues"] = json_loads(value.pop("parse_issues_json", None), [])
    raw = json_loads(value.pop("raw_response_json", None), None)
    if include_raw:
        value["rawResponse"] = raw
    return value


@router.get("/tasks/{task_id}/runs")
def list_runs(task_id: str, database: Database = Depends(get_database)) -> JSONResponse:
    exists = database.fetchone("SELECT id FROM tasks WHERE id=?", (task_id,))
    if not exists:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    rows = database.fetchall(
        "SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC",
        (task_id,),
    )
    return success([_run(row) for row in rows])


@router.get("/runs/{run_id}")
def get_run(run_id: str, database: Database = Depends(get_database)) -> JSONResponse:
    row = database.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
    if not row:
        raise AppError(404, "RUN_NOT_FOUND", "运行记录不存在")
    return success(_run(row, include_raw=True))
