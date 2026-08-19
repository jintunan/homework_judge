from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ..config import Settings
from ..db.database import Database
from ..errors import AppError
from ..files.storage import resolve_data_path
from .dependencies import get_database, get_settings

router = APIRouter()


@router.get("/files/{file_id}")
def get_file(
    file_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = database.fetchone("SELECT * FROM documents WHERE id=?", (file_id,))
    if not row:
        raise AppError(404, "FILE_NOT_FOUND", "文件不存在")
    path = resolve_data_path(settings, str(row["relative_path"]))
    if not path.is_file():
        raise AppError(404, "FILE_MISSING", "文件记录存在，但原文件已丢失")
    return FileResponse(path, media_type=str(row["mime_type"]), filename=str(row["original_name"]))


@router.get("/pages/{page_id}")
def get_page(
    page_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = database.fetchone("SELECT * FROM pages WHERE id=?", (page_id,))
    if not row:
        raise AppError(404, "PAGE_NOT_FOUND", "页面不存在")
    path = resolve_data_path(settings, str(row["image_path"]))
    if not path.is_file():
        raise AppError(404, "PAGE_MISSING", "页面图像已丢失")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/student-pages/{page_id}")
def get_student_page(
    page_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    row = database.fetchone("SELECT * FROM student_pages WHERE id=?", (page_id,))
    if not row:
        raise AppError(404, "STUDENT_PAGE_NOT_FOUND", "学生答卷页面不存在")
    path = resolve_data_path(settings, str(row["original_image_path"]))
    if not path.is_file():
        raise AppError(404, "STUDENT_PAGE_MISSING", "学生答卷原页已丢失")
    return FileResponse(path, media_type="image/jpeg")
