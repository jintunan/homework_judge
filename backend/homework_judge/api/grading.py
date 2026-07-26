from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..db.database import Database
from ..db.repositories.submissions import get_progress
from ..grading.orchestrator import GradingOrchestrator
from .dependencies import get_database, get_grading_orchestrator
from .response import success

router = APIRouter()


@router.post("/tasks/{task_id}/grading-runs")
async def grading_start(
    task_id: str,
    database: Database = Depends(get_database),
    orchestrator: GradingOrchestrator = Depends(get_grading_orchestrator),
) -> JSONResponse:
    queued = await orchestrator.enqueue_task(task_id)
    return success(
        {
            "queued": queued,
            "progress": await get_progress(database, task_id),
            "runtime": orchestrator.jobs.state(),
        },
        202,
    )


@router.post("/submissions/{submission_id}/retry")
async def grading_retry(
    submission_id: str,
    orchestrator: GradingOrchestrator = Depends(get_grading_orchestrator),
) -> JSONResponse:
    await orchestrator.enqueue_submission(submission_id)
    return success({"submissionId": submission_id, "status": "queued"}, 202)
