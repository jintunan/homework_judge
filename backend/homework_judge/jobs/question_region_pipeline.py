from __future__ import annotations

from typing import Any

from ..config import Settings
from ..db.database import Database, json_loads
from ..errors import AppError
from ..question_frames.service import QuestionFrameService
from ..recognition.service import RecognitionService


class QuestionRegionPipeline:
    """Generate a task-level question-frame draft from the blank template.

    Student-page mapping belongs to ``StudentPipeline`` and may only consume a
    teacher-confirmed frame set. This compatibility job therefore never reads or
    mutates student submissions, pages, or mapped regions.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        recognition: RecognitionService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.recognition = recognition
        self.question_frames = QuestionFrameService(database)

    async def run(self, task_id: str) -> None:
        task = self.database.fetchone(
            "SELECT current_question_frame_set_id FROM tasks WHERE id=?",
            (task_id,),
        )
        if task is None:
            raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
        if task.get("current_question_frame_set_id"):
            return

        pages = self._template_pages(task_id)
        questions, regions_by_question = await self._template_candidates(task_id, pages)
        page_ids = {int(page["page_number"]): str(page["id"]) for page in pages}
        candidates = [
            {
                "questionId": str(question["id"]),
                "issues": [],
                "fragments": self._fragments(
                    str(question["id"]),
                    regions_by_question.get(str(question["id"]), []),
                    page_ids,
                ),
            }
            for question in questions
        ]
        try:
            self.question_frames.create_draft(
                task_id,
                candidates,
                source="model",
                actor=f"model:{self.settings.dashscope_model}",
            )
        except AppError as error:
            # A concurrent initial-recognition job may have installed the draft
            # after our initial read. Keep this producer idempotent.
            if error.code != "QUESTION_FRAME_SET_EXISTS":
                raise

    def _template_pages(self, task_id: str) -> list[dict[str, Any]]:
        pages = self.database.fetchall(
            """SELECT p.* FROM pages p JOIN documents d ON d.id=p.document_id
               WHERE d.task_id=? AND d.role='exam' ORDER BY p.page_number""",
            (task_id,),
        )
        if not pages:
            raise AppError(409, "TEMPLATE_PAGES_MISSING", "空白试卷尚未生成页面")
        return pages

    async def _template_candidates(
        self,
        task_id: str,
        pages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        questions = self.database.fetchall(
            "SELECT * FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order",
            (task_id,),
        )
        if not questions:
            raise AppError(409, "QUESTIONS_MISSING", "试卷尚未识别出题目")

        regions_by_question = {
            str(question["id"]): list(json_loads(question.get("question_regions_json"), []))
            for question in questions
        }
        missing_ids = {
            question_id for question_id, regions in regions_by_question.items() if not regions
        }
        for page in pages:
            page_number = int(page["page_number"])
            page_questions = [
                question
                for question in questions
                if str(question["id"]) in missing_ids
                and page_number in json_loads(question.get("source_pages_json"), [])
            ]
            if not page_questions:
                continue
            detected, _raw, _usage = await self.recognition.recognize_question_regions(
                page,
                [
                    {
                        "id": question["id"],
                        "number": question["detected_number"],
                        "stem": question["stem"],
                    }
                    for question in page_questions
                ],
            )
            for question_id, regions in detected.items():
                if question_id in missing_ids:
                    regions_by_question.setdefault(question_id, []).extend(regions)
        return questions, regions_by_question

    @staticmethod
    def _fragments(
        question_id: str,
        regions: list[dict[str, Any]],
        page_ids: dict[int, str],
    ) -> list[dict[str, object]]:
        fragments: list[dict[str, object]] = []
        for index, region in enumerate(regions):
            try:
                page_number = int(region["page_number"])
                template_page_id = page_ids[page_number]
                x = float(region["x"])
                y = float(region["y"])
                width = float(region["width"])
                height = float(region["height"])
            except (KeyError, TypeError, ValueError):
                continue
            fragments.append(
                {
                    "regionKey": str(
                        region.get("region_key", f"{question_id}:part:{index + 1}")
                    ),
                    "templatePageId": template_page_id,
                    "pageNumber": page_number,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "sortOrder": index,
                    "source": "model",
                    "confidence": float(region.get("confidence", 0.8)),
                    "issues": [
                        str(issue)
                        for issue in region.get("issues", [])
                        if str(issue).strip()
                    ],
                }
            )
        return fragments
