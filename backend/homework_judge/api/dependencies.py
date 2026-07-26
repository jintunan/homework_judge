from __future__ import annotations

from typing import cast

from fastapi import Request

from ..answer_config.orchestrator import AnswerConfigOrchestrator
from ..db.database import Database
from ..grading.orchestrator import GradingOrchestrator
from ..model.dashscope import DashScopeClient


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_model_client(request: Request) -> DashScopeClient:
    return cast(DashScopeClient, request.app.state.model_client)


def get_answer_orchestrator(request: Request) -> AnswerConfigOrchestrator:
    return cast(AnswerConfigOrchestrator, request.app.state.answer_orchestrator)


def get_grading_orchestrator(request: Request) -> GradingOrchestrator:
    return cast(GradingOrchestrator, request.app.state.grading_orchestrator)
