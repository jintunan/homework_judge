from fastapi import APIRouter

from . import (
    answer_grading_drafts,
    files,
    grading,
    grading_artifacts,
    question_frames,
    review,
    rubrics,
    runs,
    submissions,
    tasks,
)

router = APIRouter()
router.include_router(answer_grading_drafts.router)
router.include_router(tasks.router)
router.include_router(review.router)
router.include_router(runs.router)
router.include_router(files.router)
router.include_router(submissions.router)
router.include_router(rubrics.router)
router.include_router(grading.router)
router.include_router(grading_artifacts.router)
router.include_router(question_frames.router)
