from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .answer_config.extractor import VisionQuestionExtractor
from .answer_config.orchestrator import AnswerConfigOrchestrator
from .answer_config.resolver import AnswerResolver
from .api import build_api_router
from .config import Settings
from .db.database import Database
from .db.migrations import initialize_schema
from .db.recovery import recover_interrupted_work
from .errors import AppError
from .grading.client import DashScopeGradingClient
from .grading.orchestrator import GradingOrchestrator
from .jobs.manager import JobManager
from .model.answer_generator import DashScopeAnswerGenerator
from .model.dashscope import DashScopeClient
from .model.dashscope_search import DashScopeNativeSearchClient


def _validation_fields(error: RequestValidationError) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"] if part not in {"body"})
        fields.setdefault(path or "request", []).append(str(item["msg"]))
    return fields


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_settings.ensure_directories()
        database = Database(app_settings)
        await initialize_schema(database)
        await recover_interrupted_work(database)

        model_client = DashScopeClient(app_settings)
        search_client = DashScopeNativeSearchClient(app_settings)
        extractor = VisionQuestionExtractor(database, model_client)
        generator = DashScopeAnswerGenerator(model_client)
        resolver = AnswerResolver(database, search_client, generator)
        answer_jobs = JobManager(
            concurrency=app_settings.answer_config_concurrency,
            max_queue_size=200,
        )
        answer_orchestrator = AnswerConfigOrchestrator(
            database,
            extractor,
            resolver,
            answer_jobs,
        )
        grading_jobs = JobManager(
            concurrency=app_settings.grading_concurrency,
            max_queue_size=max(200, app_settings.max_files_per_batch * 4),
        )
        grading_orchestrator = GradingOrchestrator(
            database,
            DashScopeGradingClient(model_client),
            grading_jobs,
        )
        app.state.database = database
        app.state.model_client = model_client
        app.state.search_client = search_client
        app.state.answer_orchestrator = answer_orchestrator
        app.state.grading_orchestrator = grading_orchestrator
        await answer_orchestrator.start()
        await grading_orchestrator.start()
        try:
            yield
        finally:
            await answer_jobs.shutdown()
            await grading_jobs.shutdown()
            await search_client.close()
            await model_client.close()

    app = FastAPI(
        title="作业批改 Agent",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, error: AppError) -> JSONResponse:
        body: dict[str, Any] = {
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }
        if error.fields:
            body["error"]["fields"] = error.fields
        return JSONResponse(status_code=error.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求字段校验失败",
                    "fields": _validation_fields(error),
                },
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "ok": False,
                "error": {
                    "code": "HTTP_ERROR",
                    "message": str(error.detail),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器处理请求时发生错误",
                },
            },
        )

    app.include_router(build_api_router())

    client_dist = (Path.cwd() / "dist" / "client").resolve()
    assets = client_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def react_fallback(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {"code": "NOT_FOUND", "message": "API 路由不存在"},
                },
            )
        index = client_dist / "index.html"
        if not index.is_file():
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {
                        "code": "CLIENT_NOT_BUILT",
                        "message": "前端尚未构建，请先运行 npm run build",
                    },
                },
            )
        requested = (client_dist / full_path).resolve()
        if requested.is_file() and (
            requested == client_dist or client_dist in requested.parents
        ):
            return FileResponse(requested)
        return FileResponse(index)

    return app


app = create_app()
