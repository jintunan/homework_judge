from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db.database import Database
from ..recognition.client import DashScopeClient
from ..review.answer_grading_drafts import AnswerGradingDraftService
from .dependencies import get_database, get_model_client, get_settings
from .response import success

router = APIRouter()


@router.post("/questions/{question_id}/answer-grading-drafts")
async def generate_answer_grading_draft(
    question_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    client: DashScopeClient = Depends(get_model_client),
) -> JSONResponse:
    result = await AnswerGradingDraftService(settings, database, client).generate(question_id)
    return success(result, 201)


@router.post("/questions/{question_id}/answer-grading-drafts/{run_id}/apply")
def apply_answer_grading_draft(
    question_id: str,
    run_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    client: DashScopeClient = Depends(get_model_client),
) -> JSONResponse:
    result = AnswerGradingDraftService(settings, database, client).apply(
        run_id,
        actor=settings.teacher_name,
        expected_question_id=question_id,
    )
    return success(result)
