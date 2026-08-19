from __future__ import annotations

from typing import cast

from fastapi import Request

from ..config import Settings
from ..db.database import Database
from ..grading.review import GradingReviewService
from ..jobs.grading_pipeline import GradingPipeline
from ..jobs.manager import JobManager
from ..jobs.pipeline import Pipeline
from ..jobs.question_region_pipeline import QuestionRegionPipeline
from ..jobs.student_pipeline import StudentPipeline
from ..jobs.student_workflow import StudentSubmissionWorkflow
from ..recognition.client import DashScopeClient
from ..recognition.service import RecognitionService


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_jobs(request: Request) -> JobManager:
    return cast(JobManager, request.app.state.jobs)


def get_pipeline(request: Request) -> Pipeline:
    return cast(Pipeline, request.app.state.pipeline)


def get_student_pipeline(request: Request) -> StudentPipeline:
    return cast(StudentPipeline, request.app.state.student_pipeline)


def get_student_workflow(request: Request) -> StudentSubmissionWorkflow:
    return cast(StudentSubmissionWorkflow, request.app.state.student_workflow)


def get_question_region_pipeline(request: Request) -> QuestionRegionPipeline:
    return cast(QuestionRegionPipeline, request.app.state.question_region_pipeline)


def get_model_client(request: Request) -> DashScopeClient:
    return cast(DashScopeClient, request.app.state.model_client)


def get_recognition_service(request: Request) -> RecognitionService:
    return cast(RecognitionService, request.app.state.recognition_service)


def get_grading_pipeline(request: Request) -> GradingPipeline:
    return cast(GradingPipeline, request.app.state.grading_pipeline)


def get_grading_review_service(request: Request) -> GradingReviewService:
    return cast(GradingReviewService, request.app.state.grading_review_service)
