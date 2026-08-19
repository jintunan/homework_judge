from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db.database import Database
from ..errors import AppError
from ..question_frames.service import FrameSource, QuestionFrameService
from ..recognition.service import RecognitionService
from ..review.question_rerecognition import SingleQuestionRerecognitionService
from ..schemas import (
    ExpectedRevisionRequest,
    QuestionFrameItemUpdate,
    SingleQuestionRerecognitionRequest,
)
from .dependencies import get_database, get_recognition_service, get_settings
from .response import success

router = APIRouter()


@router.get("/tasks/{task_id}/question-frame-sets/current")
def get_current_question_frame_set(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    service = QuestionFrameService(database)
    return success(
        {
            "frameSet": service.get_current(task_id),
            "studentProcessingGate": service.processing_gate(task_id),
        }
    )


@router.get("/tasks/{task_id}/student-processing-gate")
def get_student_processing_gate(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(QuestionFrameService(database).processing_gate(task_id))


@router.post("/tasks/{task_id}/question-frame-sets")
def create_question_frame_set(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    raw_source = str(payload.get("source", "teacher"))
    if raw_source not in {"model", "teacher", "legacy"}:
        raise AppError(422, "QUESTION_FRAME_SOURCE_INVALID", "题框来源非法")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise AppError(422, "QUESTION_FRAME_CANDIDATES_INVALID", "candidates 必须是数组")
    candidates = [
        cast(dict[str, object], item) for item in raw_candidates if isinstance(item, dict)
    ]
    if len(candidates) != len(raw_candidates):
        raise AppError(422, "QUESTION_FRAME_CANDIDATES_INVALID", "题框候选必须是对象")
    value = QuestionFrameService(database).create_draft(
        task_id,
        candidates,
        source=cast(FrameSource, raw_source),
        actor=settings.teacher_name,
    )
    return success(value, 201)


@router.patch("/question-frame-sets/{frame_set_id}/questions/{question_id}")
def update_question_frame_item(
    frame_set_id: str,
    question_id: str,
    payload: QuestionFrameItemUpdate,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    value = QuestionFrameService(database).update_item(
        frame_set_id,
        question_id,
        [item.model_dump() for item in payload.regions],
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)


@router.post("/question-frame-sets/{frame_set_id}/questions/{question_id}/rerecognize")
async def rerecognize_question_frame_item(
    frame_set_id: str,
    question_id: str,
    payload: SingleQuestionRerecognitionRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    recognition: RecognitionService = Depends(get_recognition_service),
) -> JSONResponse:
    value = await SingleQuestionRerecognitionService(
        settings,
        database,
        recognition,
    ).run(
        frame_set_id,
        question_id,
        [item.model_dump() for item in payload.regions],
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)


@router.post("/question-frame-sets/{frame_set_id}/normalize-model-draft")
def normalize_model_question_frame_draft(
    frame_set_id: str,
    payload: ExpectedRevisionRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    value = QuestionFrameService(database).normalize_model_draft(
        frame_set_id,
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)


@router.post("/question-frame-sets/{frame_set_id}/questions/{question_id}/confirm")
def confirm_question_frame_item(
    frame_set_id: str,
    question_id: str,
    payload: ExpectedRevisionRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    value = QuestionFrameService(database).confirm_item(
        frame_set_id,
        question_id,
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)


@router.post("/question-frame-sets/{frame_set_id}/questions/{question_id}/reopen")
def reopen_question_frame_item(
    frame_set_id: str,
    question_id: str,
    payload: ExpectedRevisionRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    value = QuestionFrameService(database).reopen_item(
        frame_set_id,
        question_id,
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)


@router.post("/question-frame-sets/{frame_set_id}/confirm")
def confirm_question_frame_set(
    frame_set_id: str,
    payload: ExpectedRevisionRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    value = QuestionFrameService(database).confirm_set(
        frame_set_id,
        expected_revision=payload.expectedRevision,
        actor=settings.teacher_name,
    )
    return success(value)
