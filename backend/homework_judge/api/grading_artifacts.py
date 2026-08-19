from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from ..config import Settings
from ..db.database import Database, json_loads
from ..errors import AppError
from ..files.storage import resolve_data_path
from .dependencies import get_database, get_settings
from .response import success

router = APIRouter()


def _artifact_value(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "gradingRunId": row["grading_run_id"],
        "type": row["artifact_type"],
        "resultRevision": row["result_revision"],
        "status": row["status"],
        "preview": json_loads(row["preview_json"], {}),
        "contentHash": row["content_hash"],
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
        "previewUrl": f"/api/grading-artifacts/{row['id']}/preview",
        "downloadUrl": f"/api/grading-artifacts/{row['id']}/download",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _get_artifact(database: Database, artifact_id: str) -> dict[str, Any]:
    row = database.fetchone("SELECT * FROM grading_artifacts WHERE id=?", (artifact_id,))
    if not row:
        raise AppError(404, "GRADING_ARTIFACT_NOT_FOUND", "批改生成物不存在")
    return row


def _path(settings: Settings, row: dict[str, Any]) -> Path:
    if not row["relative_path"]:
        raise AppError(409, "GRADING_ARTIFACT_NOT_READY", "批改生成物尚未就绪")
    path = resolve_data_path(settings, str(row["relative_path"]))
    if not path.is_file():
        raise AppError(404, "GRADING_ARTIFACT_FILE_MISSING", "批改生成物文件已丢失")
    return path


@router.get("/grading-runs/{run_id}/artifacts")
def list_grading_artifacts(
    run_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    if not database.fetchone("SELECT id FROM grading_runs WHERE id=?", (run_id,)):
        raise AppError(404, "GRADING_RUN_NOT_FOUND", "批改运行不存在")
    rows = database.fetchall(
        """SELECT * FROM grading_artifacts WHERE grading_run_id=?
           ORDER BY result_revision DESC,artifact_type""",
        (run_id,),
    )
    return success([_artifact_value(row) for row in rows])


@router.get("/grading-artifacts/{artifact_id}/preview")
def preview_grading_artifact(
    artifact_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = _get_artifact(database, artifact_id)
    if row["status"] not in {"current", "stale"}:
        raise AppError(409, "GRADING_ARTIFACT_NOT_READY", "批改生成物尚未就绪")
    return FileResponse(_path(settings, row), media_type="application/pdf")


@router.get("/grading-artifacts/{artifact_id}/download")
def download_grading_artifact(
    artifact_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = _get_artifact(database, artifact_id)
    if row["status"] != "current":
        raise AppError(409, "GRADING_ARTIFACT_STALE", "旧版本已过期，请下载最新结果")
    filename = "批注试卷.pdf" if row["artifact_type"] == "annotation" else "错题分析报告.pdf"
    return FileResponse(
        _path(settings, row),
        media_type="application/pdf",
        filename=filename,
    )
