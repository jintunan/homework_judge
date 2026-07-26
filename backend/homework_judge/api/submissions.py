from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from ..db.database import Database
from ..db.repositories.submissions import (
    create_submission,
    get_progress,
    infer_student_name,
    list_submissions,
    update_student_name,
)
from ..db.repositories.tasks import get_task
from ..errors import AppError
from ..files.storage import (
    normalize_original_name,
    persist_upload,
    remove_persisted_file,
)
from ..schemas import StudentNameUpdate
from .dependencies import get_database
from .response import success

router = APIRouter()


@router.get("/tasks/{task_id}/submissions")
async def submission_list(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    await get_task(database, task_id)
    return success(await list_submissions(database, task_id))


@router.get("/tasks/{task_id}/grading-progress")
async def grading_progress(
    task_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    await get_task(database, task_id)
    return success(await get_progress(database, task_id))


@router.post("/tasks/{task_id}/submissions")
async def submission_upload(
    task_id: str,
    files: list[UploadFile] = File(...),
    database: Database = Depends(get_database),
) -> JSONResponse:
    task = await get_task(database, task_id)
    if (
        task["answerConfigStatus"] != "approved"
        or not task["activeAnswerVersion"]
    ):
        for upload in files:
            await upload.close()
        raise AppError(
            409,
            "ANSWER_CONFIG_NOT_APPROVED",
            "请先完成答案配置审核并发布，再上传学生试卷",
        )
    if not task["questions"]:
        for upload in files:
            await upload.close()
        raise AppError(409, "QUESTIONS_REQUIRED", "请先完成标准答案配置")
    if not files:
        raise AppError(422, "FILES_REQUIRED", "请选择学生试卷文件")
    if len(files) > database.settings.max_files_per_batch:
        for upload in files:
            await upload.close()
        raise AppError(
            422,
            "TOO_MANY_FILES",
            f"每批最多上传 {database.settings.max_files_per_batch} 份试卷",
        )

    results: list[dict[str, object]] = []
    for upload in files:
        display_name = normalize_original_name(upload.filename or "")
        persisted = None
        try:
            persisted = await persist_upload(database.settings, upload, "submission")
            student_name, needs_review = infer_student_name(display_name)
            submission = await create_submission(
                database,
                task_id,
                persisted,
                student_name,
                needs_review,
            )
            results.append(
                {
                    "originalName": display_name,
                    "ok": True,
                    "submission": submission,
                }
            )
        except Exception as error:
            if persisted:
                await remove_persisted_file(database.settings, persisted)
            results.append(
                {
                    "originalName": display_name,
                    "ok": False,
                    "error": {
                        "code": (
                            error.code if isinstance(error, AppError) else "UPLOAD_FAILED"
                        ),
                        "message": str(error) or "文件上传失败",
                    },
                }
            )
    return success({"results": results}, 201)


@router.patch("/submissions/{submission_id}")
async def submission_update(
    submission_id: str,
    update: StudentNameUpdate,
    database: Database = Depends(get_database),
) -> JSONResponse:
    return success(
        await update_student_name(database, submission_id, update.student_name)
    )
