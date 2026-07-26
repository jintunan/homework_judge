from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..db.database import Database, now_iso
from ..model.dashscope import DashScopeClient
from .dependencies import get_database, get_model_client
from .response import success

router = APIRouter()


@router.get("/health")
async def health(database: Database = Depends(get_database)) -> JSONResponse:
    await database.fetch_one("SELECT 1 AS ok")
    return success(
        {
            "status": "ok",
            "database": "ok",
            "time": now_iso(),
            "teacherName": database.settings.teacher_name,
        }
    )


@router.get("/model/status")
async def model_status(client: DashScopeClient = Depends(get_model_client)) -> JSONResponse:
    return success(client.status())
