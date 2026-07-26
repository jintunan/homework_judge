from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..answer_config.orchestrator import AnswerConfigOrchestrator
from ..answer_config.publisher import create_revision, publish_answer_version
from ..db.database import Database
from ..db.repositories.answer_config import (
    get_draft_task_context,
    get_latest_answer_version,
    set_draft_review_status,
    update_answer_draft,
)
from ..db.repositories.answer_runs import get_answer_run
from ..errors import AppError
from ..schemas import AnswerDraftAction, AnswerDraftUpdate
from ..subjects import get_subject_profile
from .dependencies import get_answer_orchestrator, get_database
from .response import success

router = APIRouter()


@router.post("/tasks/{task_id}/answer-config-runs")
async def answer_config_start(
    task_id: str,
    orchestrator: AnswerConfigOrchestrator = Depends(get_answer_orchestrator),
) -> JSONResponse:
    version = await orchestrator.start_task(task_id)
    return success({"version": version, "runtime": orchestrator.jobs.state()}, 202)


@router.get("/tasks/{task_id}/answer-config")
async def answer_config_detail(
    task_id: str,
    orchestrator: AnswerConfigOrchestrator = Depends(get_answer_orchestrator),
) -> JSONResponse:
    return success(await orchestrator.get_detail(task_id))


@router.get("/tasks/{task_id}/answer-config-progress")
async def answer_config_progress(
    task_id: str,
    orchestrator: AnswerConfigOrchestrator = Depends(get_answer_orchestrator),
) -> JSONResponse:
    detail = await orchestrator.get_detail(task_id)
    return success(
        {
            "status": detail["task"]["answerConfigStatus"],
            "version": detail["version"],
            "progress": detail["progress"],
            "runtime": orchestrator.jobs.state(),
        }
    )


@router.patch("/answer-drafts/{draft_id}")
async def answer_draft_update(
    draft_id: str,
    draft_update: AnswerDraftUpdate,
    database: Database = Depends(get_database),
) -> JSONResponse:
    context = await get_draft_task_context(database, draft_id)
    profile = get_subject_profile(context["subject"])
    if draft_update.type not in profile.supported_types:
        raise AppError(
            422,
            "QUESTION_TYPE_NOT_ALLOWED",
            "该题型不属于当前科目的支持范围",
        )
    return success(await update_answer_draft(database, draft_id, draft_update))


@router.post("/answer-drafts/{draft_id}/approve")
async def answer_draft_approve(
    draft_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await set_draft_review_status(database, draft_id, "approved"))


@router.post("/answer-drafts/{draft_id}/reject")
async def answer_draft_reject(
    draft_id: str,
    action: AnswerDraftAction,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(
        await set_draft_review_status(database, draft_id, "rejected", action.reason)
    )


@router.post("/answer-drafts/{draft_id}/research")
async def answer_draft_research(
    draft_id: str,
    orchestrator: AnswerConfigOrchestrator = Depends(get_answer_orchestrator),
) -> JSONResponse:
    await orchestrator.enqueue_draft(draft_id, "research")
    return success({"status": "queued"}, 202)


@router.post("/answer-drafts/{draft_id}/regenerate")
async def answer_draft_regenerate(
    draft_id: str,
    orchestrator: AnswerConfigOrchestrator = Depends(get_answer_orchestrator),
) -> JSONResponse:
    await orchestrator.enqueue_draft(draft_id, "regenerate")
    return success({"status": "queued"}, 202)


@router.post("/tasks/{task_id}/answer-config/approve")
async def answer_config_publish(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    version = await get_latest_answer_version(database, task_id)
    if version is None:
        raise AppError(404, "ANSWER_VERSION_NOT_FOUND", "答案配置版本不存在")
    return success(await publish_answer_version(database, str(version["id"])))


@router.post("/tasks/{task_id}/answer-config/revise")
async def answer_config_revise(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await create_revision(database, task_id), 201)


@router.get("/answer-runs/{run_id}")
async def answer_run_detail(
    run_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await get_answer_run(database, run_id))
