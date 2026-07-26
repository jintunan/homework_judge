from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..db.database import Database
from ..db.repositories.audit import list_audit_events
from ..db.repositories.reviews import (
    confirm_submission,
    get_submission_review,
    update_question_review,
)
from ..schemas import ReviewUpdate
from .dependencies import get_database
from .response import success

router = APIRouter()


@router.get("/submissions/{submission_id}/review")
async def submission_review(
    submission_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await get_submission_review(database, submission_id))


@router.patch("/submissions/{submission_id}/reviews/{question_id}")
async def question_review_update(
    submission_id: str,
    question_id: str,
    review_update: ReviewUpdate,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(
        await update_question_review(
            database,
            submission_id,
            question_id,
            review_update,
        )
    )


@router.post("/submissions/{submission_id}/confirm")
async def submission_confirm(
    submission_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await confirm_submission(database, submission_id))


@router.get("/submissions/{submission_id}/audit")
async def submission_audit(
    submission_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(
        await list_audit_events(database, submission_id=submission_id)
    )
