from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from homework_judge.answer_config.extractor import VisionQuestionExtractor
from homework_judge.config import Settings
from homework_judge.db.database import Database
from homework_judge.db.migrations import initialize_schema
from homework_judge.db.repositories.answer_config import create_answer_version
from homework_judge.db.repositories.answer_runs import list_answer_runs
from homework_judge.db.repositories.tasks import create_task
from homework_judge.files.storage import PersistedFile
from homework_judge.model.dashscope import ModelResponse
from homework_judge.schemas import AnswerMode, Subject, TaskInput

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "answer-extraction-overflow.json"
).read_text(encoding="utf-8")


class FakeClient:
    model_id = "fake-qwen"

    def __init__(self, contents: list[str]) -> None:
        self.contents = iter(contents)

    def snapshot(
        self,
        *,
        prompt_version: str,
        purpose: str,
        details: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        return {
            "promptVersion": prompt_version,
            "purpose": purpose,
            **details,
        }

    async def chat(self, **_kwargs: Any) -> ModelResponse:
        content = next(self.contents)
        return ModelResponse(
            raw_response={
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 1},
            },
            content=content,
            usage={"totalTokens": 1},
        )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        APP_DATA_DIR=tmp_path / "data",
        DATABASE_PATH=tmp_path / "data" / "test.sqlite",
        UPLOAD_DIR=tmp_path / "data" / "uploads",
        TEMP_DIR=tmp_path / "data" / "tmp",
        APP_ENV="test",
    )


def _image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 140), "white").save(output, format="PNG")
    return output.getvalue()


async def _task_and_version(
    tmp_path: Path,
) -> tuple[Database, str, str]:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    database = Database(settings)
    await initialize_schema(database)
    relative = "uploads/templates/fake.png"
    path = settings.app_data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _image_bytes()
    path.write_bytes(content)
    task = await create_task(
        database,
        TaskInput(
            name="物理测试任务",
            class_name="高二一班",
            paper_name="单元测评",
            subject=Subject.HIGH_SCHOOL_PHYSICS,
            answer_mode=AnswerMode.AGENT_SEARCH,
        ),
        PersistedFile(
            id="file-template",
            kind="template",
            original_name="试卷.png",
            stored_name="fake.png",
            mime_type="image/png",
            size=len(content),
            relative_path=relative,
        ),
        None,
    )
    version = await create_answer_version(
        database,
        task["id"],
        AnswerMode.AGENT_SEARCH,
    )
    return database, str(task["id"]), str(version["id"])


@pytest.mark.asyncio
async def test_real_failure_shape_succeeds_without_repair(tmp_path: Path) -> None:
    database, task_id, version_id = await _task_and_version(tmp_path)
    extractor = VisionQuestionExtractor(database, FakeClient([FIXTURE]))  # type: ignore[arg-type]
    run_id, parsed, has_reference = await extractor.extract(task_id, version_id)
    assert has_reference is False
    assert len(parsed.questions) == 8
    assert all(not question.standard_answer for question in parsed.questions)
    runs = await list_answer_runs(database, version_id)
    assert [run["id"] for run in runs] == [run_id]
    assert runs[0]["kind"] == "exam_extraction"
    assert runs[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_zero_usable_questions_gets_one_text_only_repair(tmp_path: Path) -> None:
    database, task_id, version_id = await _task_and_version(tmp_path)
    repaired = """
    {
      "questions": [{
        "questionNumber": "1",
        "questionText": "修复后保留的题干",
        "type": "choice",
        "maxScore": 3,
        "standardAnswer": "不应保留",
        "scoringPoints": [{"description": "不应保留", "score": 3}],
        "confidence": 0.8
      }]
    }
    """
    extractor = VisionQuestionExtractor(
        database,
        FakeClient(['{"questions":[{"questionText":"","maxScore":0}]}', repaired]),  # type: ignore[arg-type]
    )
    repair_run_id, parsed, _has_reference = await extractor.extract(
        task_id,
        version_id,
    )
    assert parsed.repaired is True
    assert len(parsed.questions) == 1
    assert parsed.questions[0].standard_answer == ""
    runs = await list_answer_runs(database, version_id)
    assert len(runs) == 2
    assert runs[0]["id"] == repair_run_id
    assert runs[0]["kind"] == "structure_repair"
    assert runs[0]["status"] == "succeeded"
    assert runs[1]["kind"] == "exam_extraction"
    assert runs[1]["status"] == "parse_failed"
