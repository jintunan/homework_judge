from __future__ import annotations

from fastapi import APIRouter

from . import answer_config, files, grading, health, reports, reviews, submissions, tasks


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.include_router(health.router)
    router.include_router(tasks.router)
    router.include_router(files.router)
    router.include_router(answer_config.router)
    router.include_router(submissions.router)
    router.include_router(grading.router)
    router.include_router(reviews.router)
    router.include_router(reports.router)
    return router
