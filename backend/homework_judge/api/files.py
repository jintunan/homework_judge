from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ..db.database import Database
from ..db.repositories.tasks import get_stored_file
from ..errors import AppError
from ..files.storage import resolve_data_path
from .dependencies import get_database

router = APIRouter(prefix="/files")


@router.get("/{file_id}")
async def file_preview(
    file_id: str,
    database: Database = Depends(get_database),
) -> FileResponse:
    row = await get_stored_file(database, file_id)
    path = resolve_data_path(database.settings, str(row["relative_path"]))
    if not path.is_file():
        raise AppError(404, "FILE_MISSING", "文件记录存在，但原始文件已丢失")
    return FileResponse(
        path,
        media_type=str(row["mime_type"]),
        filename=str(row["original_name"]),
        content_disposition_type="inline",
    )
