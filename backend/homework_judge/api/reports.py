from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..db.database import Database
from ..reports.statistics import build_class_statistics, build_student_report
from .dependencies import get_database
from .response import success

router = APIRouter()


@router.get("/submissions/{submission_id}/report")
async def student_report(
    submission_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await build_student_report(database, submission_id))


@router.get("/tasks/{task_id}/statistics")
async def class_statistics(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await build_class_statistics(database, task_id))
