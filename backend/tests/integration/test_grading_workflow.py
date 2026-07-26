from __future__ import annotations

from pathlib import Path

import pytest

from homework_judge.answer_config.publisher import (
    create_revision,
    publish_answer_version,
)
from homework_judge.config import Settings
from homework_judge.db.database import Database
from homework_judge.db.migrations import initialize_schema
from homework_judge.db.repositories.answer_config import (
    create_answer_version,
    list_answer_drafts,
    replace_drafts,
    set_draft_review_status,
)
from homework_judge.db.repositories.model_runs import (
    finish_model_run_success,
    start_model_run,
)
from homework_judge.db.repositories.reviews import (
    confirm_submission,
    save_model_reviews,
    update_question_review,
)
from homework_judge.db.repositories.submissions import create_submission
from homework_judge.db.repositories.tasks import create_task
from homework_judge.files.storage import PersistedFile
from homework_judge.grading.output import GradeOutput, GradeQuestionResult
from homework_judge.reports.statistics import (
    build_class_statistics,
    build_student_report,
)
from homework_judge.schemas import (
    AnswerMode,
    NormalizedQuestion,
    ParsedPaper,
    QuestionType,
    ReviewUpdate,
    ScoringPoint,
    Subject,
    TaskInput,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        APP_DATA_DIR=tmp_path / "data",
        DATABASE_PATH=tmp_path / "data" / "workflow.sqlite",
        UPLOAD_DIR=tmp_path / "data" / "uploads",
        TEMP_DIR=tmp_path / "data" / "tmp",
        APP_ENV="test",
    )


def _file(
    settings: Settings,
    *,
    file_id: str,
    kind: str,
    relative_path: str,
) -> PersistedFile:
    target = settings.app_data_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return PersistedFile(
        id=file_id,
        kind=kind,  # type: ignore[arg-type]
        original_name=f"{file_id}.png",
        stored_name=f"{file_id}.png",
        mime_type="image/png",
        size=15,
        relative_path=relative_path,
    )


@pytest.mark.asyncio
async def test_teacher_review_report_and_revision_keep_answer_version(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    database = Database(settings)
    await initialize_schema(database)
    task = await create_task(
        database,
        TaskInput(
            name="物理批改任务",
            class_name="高二一班",
            paper_name="固定模板",
            subject=Subject.HIGH_SCHOOL_PHYSICS,
            answer_mode=AnswerMode.AGENT_SEARCH,
        ),
        _file(
            settings,
            file_id="template-file",
            kind="template",
            relative_path="uploads/templates/template-file.png",
        ),
        None,
    )
    version = await create_answer_version(
        database,
        task["id"],
        AnswerMode.AGENT_SEARCH,
    )
    await replace_drafts(
        database,
        version["id"],
        ParsedPaper(
            questions=[
                NormalizedQuestion(
                    question_number="1",
                    question_text="示例选择题",
                    type=QuestionType.CHOICE,
                    max_score=5,
                    standard_answer="A",
                    scoring_points=[
                        ScoringPoint(description="选择正确", score=5)
                    ],
                    reason="测试答案",
                    confidence=1,
                    source_index=0,
                )
            ]
        ),
        "model_generated",
    )
    drafts = await list_answer_drafts(database, version["id"])
    await set_draft_review_status(database, drafts[0]["id"], "approved")
    await publish_answer_version(database, version["id"])

    submission = await create_submission(
        database,
        task["id"],
        _file(
            settings,
            file_id="submission-file",
            kind="submission",
            relative_path="uploads/submissions/submission-file.png",
        ),
        "张三",
        False,
    )
    run_id = await start_model_run(database, submission["id"], "fake-model", {})
    output = GradeOutput(
        questions=[
            GradeQuestionResult(
                question_number="1",
                recognized_answer="A",
                suggested_score=5,
                reason="与标准答案一致",
                confidence=0.99,
                needs_attention=False,
            )
        ],
        overall_note=None,
    )
    await finish_model_run_success(
        database,
        run_id,
        raw_response={"fixture": True},
        parsed_output=output.to_dict(),
        usage={"totalTokens": 1},
    )
    await save_model_reviews(database, submission["id"], run_id, output)
    question_row = await database.fetch_one(
        """
        SELECT question_id FROM question_reviews
        WHERE submission_id = ?
        """,
        (submission["id"],),
    )
    assert question_row is not None
    await update_question_review(
        database,
        submission["id"],
        question_row["question_id"],
        ReviewUpdate(
            final_answer="A",
            final_score=4,
            teacher_comment="步骤说明不足，教师改为 4 分",
            review_status="reviewed",
        ),
    )
    confirmed = await confirm_submission(database, submission["id"])
    assert confirmed["submission"]["finalTotalScore"] == 4

    report = await build_student_report(database, submission["id"])
    assert report["isFinal"] is True
    assert report["totalScore"] == 4
    assert report["answerVersion"]["id"] == version["id"]
    statistics = await build_class_statistics(database, task["id"])
    assert statistics["confirmedCount"] == 1
    assert statistics["averageScore"] == 4

    revision = await create_revision(database, task["id"])
    assert revision["versionNumber"] == version["versionNumber"] + 1
    old_report = await build_student_report(database, submission["id"])
    assert old_report["answerVersion"]["id"] == version["id"]
