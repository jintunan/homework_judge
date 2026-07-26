from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ..db.database import Database
from ..db.repositories.tasks import (
    create_task,
    get_task,
    list_tasks,
    save_questions,
    update_task,
)
from ..errors import AppError
from ..files.storage import (
    PersistedFile,
    persist_upload,
    remove_persisted_file,
)
from ..schemas import QuestionsBatch, TaskInput, TaskUpdate
from .dependencies import get_database
from .response import success

router = APIRouter(prefix="/tasks")


@router.get("")
async def task_list(database: Database = Depends(get_database)) -> JSONResponse:
    return success(await list_tasks(database))


@router.post("")
async def task_create(
    request: Request,
    database: Database = Depends(get_database),
) -> JSONResponse:
    form = await request.form()
    template = form.get("template")
    reference = form.get("referenceAnswer")
    if not isinstance(template, UploadFile):
        raise AppError(422, "TEMPLATE_REQUIRED", "请上传固定试卷模板")
    try:
        task_input = TaskInput.model_validate(
            {
                "name": form.get("name"),
                "className": form.get("className"),
                "paperName": form.get("paperName"),
                "subject": form.get("subject", "middle_school_math"),
                "answerMode": form.get("answerMode", "agent_search"),
            }
        )
    except ValidationError:
        await template.close()
        if isinstance(reference, UploadFile):
            await reference.close()
        raise
    if task_input.answer_mode.value == "reference_upload" and not isinstance(
        reference,
        UploadFile,
    ):
        raise AppError(
            422,
            "REFERENCE_ANSWER_REQUIRED",
            "选择上传参考答案时，请同时上传参考答案文件",
        )
    if task_input.answer_mode.value == "agent_search" and isinstance(
        reference,
        UploadFile,
    ):
        await reference.close()
        raise AppError(
            422,
            "REFERENCE_ANSWER_NOT_EXPECTED",
            "Agent 查找模式不需要上传参考答案",
        )
    persisted: list[PersistedFile] = []
    try:
        template_file = await persist_upload(database.settings, template, "template")
        persisted.append(template_file)
        reference_file = (
            await persist_upload(database.settings, reference, "reference_answer")
            if isinstance(reference, UploadFile)
            else None
        )
        if reference_file:
            persisted.append(reference_file)
        task = await create_task(
            database,
            task_input,
            template_file,
            reference_file,
        )
        return success(task, 201)
    except Exception:
        for file in persisted:
            await remove_persisted_file(database.settings, file)
        raise


@router.get("/{task_id}")
async def task_detail(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await get_task(database, task_id))


@router.put("/{task_id}")
async def task_edit(
    task_id: str,
    task_update: TaskUpdate,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await update_task(database, task_id, task_update))


@router.put("/{task_id}/questions")
async def question_save(
    task_id: str,
    batch: QuestionsBatch,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(await save_questions(database, task_id, batch.questions))
