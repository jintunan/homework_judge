from __future__ import annotations

from typing import Any

from ..files.processor import PreparedPage
from ..model.dashscope import DashScopeClient, ModelResponse
from ..schemas import Subject
from .prompt import GRADING_PROMPT_VERSION, build_system_prompt, build_user_prompt


class DashScopeGradingClient:
    def __init__(self, client: DashScopeClient) -> None:
        self.client = client
        self.model_id = client.model_id

    def build_request_snapshot(
        self,
        questions: list[dict[str, Any]],
        pages: list[PreparedPage],
        subject: Subject,
    ) -> dict[str, Any]:
        return self.client.snapshot(
            prompt_version=GRADING_PROMPT_VERSION,
            purpose="student_grading",
            details={
                "subject": subject.value,
                "questionCount": len(questions),
                "pages": [
                    {
                        "pageNumber": page.page_number,
                        "mimeType": page.mime_type,
                        "byteLength": page.byte_length,
                    }
                    for page in pages
                ],
                "responseFormat": "json_object",
                "thinking": False,
                "answerConfiguration": [
                    {
                        "number": question["number"],
                        "type": question["type"],
                        "maxScore": question["maxScore"],
                        "standardAnswer": question["standardAnswer"],
                        "scoringPoints": question["scoringPoints"],
                    }
                    for question in questions
                ],
            },
        )

    async def grade(
        self,
        questions: list[dict[str, Any]],
        pages: list[PreparedPage],
        subject: Subject,
    ) -> ModelResponse:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": build_user_prompt(questions)},
            *[
                {"type": "image_url", "image_url": {"url": page.data_url}}
                for page in pages
            ],
        ]
        return await self.client.chat(
            messages=[
                {"role": "system", "content": build_system_prompt(subject)},
                {"role": "user", "content": content},
            ]
        )
