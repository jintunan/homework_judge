from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api.router import router
from .config import Settings
from .db.database import Database
from .errors import AppError
from .grading.review import GradingReviewService
from .jobs.grading_pipeline import GradingPipeline
from .jobs.manager import JobManager
from .jobs.pipeline import Pipeline
from .jobs.question_region_pipeline import QuestionRegionPipeline
from .jobs.student_pipeline import StudentPipeline
from .jobs.student_workflow import AutoGradingCoordinator, StudentSubmissionWorkflow
from .observability import bind_log_context, configure_logging, log_event
from .recognition.client import DashScopeClient
from .recognition.service import RecognitionService

LOGGER = logging.getLogger("homework_judge.http")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.load()
    settings.ensure_directories()
    configure_logging(settings)
    database = Database(settings.database_path)
    database.migrate()
    database.interrupt_running()
    database.interrupt_student_processing()
    database.interrupt_grading()
    client = DashScopeClient(settings)
    jobs = JobManager()
    recognition = RecognitionService(settings, client)
    app.state.settings = settings
    app.state.database = database
    app.state.model_client = client
    app.state.recognition_service = recognition
    app.state.jobs = jobs
    app.state.pipeline = Pipeline(settings, database, recognition)
    app.state.student_pipeline = StudentPipeline(settings, database, recognition)
    app.state.question_region_pipeline = QuestionRegionPipeline(settings, database, recognition)
    app.state.grading_pipeline = GradingPipeline(settings, database, client)
    app.state.student_workflow = StudentSubmissionWorkflow(
        app.state.student_pipeline,
        AutoGradingCoordinator(database, app.state.grading_pipeline),
    )
    app.state.grading_review_service = GradingReviewService(settings, database)
    yield
    await jobs.close()
    await client.close()


app = FastAPI(
    title="试卷题目识别与参考答案匹配",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_observability(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Correlate each API call without recording payloads or answer content."""

    request_id = uuid.uuid4().hex
    started = perf_counter()
    with bind_log_context(request_id=request_id):
        log_event(
            LOGGER,
            logging.INFO,
            "http_request_started",
            method=request.method,
            route=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                LOGGER,
                logging.ERROR,
                "http_request_failed",
                method=request.method,
                route=request.url.path,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        response.headers["X-Request-ID"] = request_id
        log_event(
            LOGGER,
            logging.INFO,
            "http_request_finished",
            method=request.method,
            route=request.url.path,
            status_code=response.status_code,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return response


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, error: AppError) -> JSONResponse:
    log_event(
        LOGGER,
        logging.WARNING,
        "application_error",
        status_code=error.status_code,
        error_code=error.code,
    )
    return JSONResponse(
        {
            "data": None,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
        },
        status_code=error.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = []
    for item in error.errors():
        safe_item = {key: value for key, value in item.items() if key != "ctx"}
        if "ctx" in item:
            safe_item["ctx"] = {key: str(value) for key, value in item["ctx"].items()}
        details.append(safe_item)
    return JSONResponse(
        {
            "data": None,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不完整或格式错误",
                "details": details,
            },
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, _error: Exception) -> JSONResponse:
    LOGGER.error(
        "unhandled_application_error",
        exc_info=(type(_error), _error, _error.__traceback__),
    )
    return JSONResponse(
        {
            "data": None,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务发生内部错误，请重试或查看服务日志",
                "details": None,
            },
        },
        status_code=500,
    )


@app.get("/api/health")
def health(request: Request) -> dict[str, object]:
    client: DashScopeClient = request.app.state.model_client
    return {
        "data": {
            "status": "ok",
            "database": "ok",
            "model": client.status(),
        },
        "error": None,
    }


app.include_router(router, prefix="/api")

dist = Path(__file__).resolve().parents[2] / "dist" / "client"
if dist.is_dir():
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise AppError(404, "API_NOT_FOUND", "接口不存在")
        return FileResponse(dist / "index.html", media_type="text/html")
