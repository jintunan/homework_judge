from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homework_judge.db.database import Database, json_loads, now_iso
from homework_judge.jobs.grading_pipeline import GradingPipeline
from homework_judge.jobs.student_pipeline import StudentPipeline
from homework_judge.recognition.client import DashScopeClient, ModelResponse
from homework_judge.recognition.service import RecognitionService
from tests.unit.test_student_pipeline import (
    BlankCalculationRecognition,
    CombinedCalculationRecognition,
    PartiallyFailingCalculationRecognition,
    _exam_page,
    _single_page_case,
)


class _SuccessfulCalculationGrader:
    settings = SimpleNamespace(dashscope_model="calculation-e2e")

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.evidence_id: str | None = None

    async def chat(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs)
        content = kwargs["user_content"]
        assert isinstance(content, list)
        prompt = json.loads(content[0]["text"])
        evidence = prompt["availableEvidence"]
        assert len(evidence) == 1
        self.evidence_id = str(evidence[0]["regionId"])
        assert evidence[0]["recognizedText"] == "x = 42"
        assert [item["type"] for item in content].count("image_url") == 2
        return ModelResponse(
            content=json.dumps(
                {
                    "points": [
                        {
                            "pointKey": "P1",
                            "status": "satisfied",
                            "reason": "过程与结论正确",
                            "evidenceRegionIds": [self.evidence_id],
                            "confidence": 0.99,
                        }
                    ],
                    "uncoveredMethod": False,
                }
            ),
            raw={"id": "calculation-e2e"},
            usage={"totalTokens": 7},
        )


class _BlankCalculationGrader:
    settings = SimpleNamespace(dashscope_model="calculation-blank-e2e")

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.evidence_id: str | None = None

    async def chat(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs)
        content = kwargs["user_content"]
        assert isinstance(content, list)
        prompt = json.loads(content[0]["text"])
        evidence = prompt["availableEvidence"]
        assert len(evidence) == 1
        assert evidence[0]["recognizedText"] == ""
        assert evidence[0]["evidenceKind"] == "blank_search_window"
        assert evidence[0]["isBlank"] is True
        assert [item["type"] for item in content].count("image_url") == 2
        self.evidence_id = str(evidence[0]["regionId"])
        return ModelResponse(
            content=json.dumps(
                {
                    "points": [
                        {
                            "pointKey": "P1",
                            "status": "failed",
                            "reason": "检查窗口内没有学生作答",
                            "evidenceRegionIds": [self.evidence_id],
                            "confidence": 0.99,
                        }
                    ],
                    "uncoveredMethod": False,
                }
            ),
            raw={"id": "calculation-blank-e2e"},
            usage={"totalTokens": 5},
        )


class _RejectingCalculationGrader:
    settings = SimpleNamespace(dashscope_model="calculation-rejecting-e2e")

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> ModelResponse:
        self.calls += 1
        raise AssertionError("structurally incomplete evidence must not call the grading model")


class _CompletingArtifacts:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def generate(self, run_id: str) -> None:
        self.database.execute(
            "UPDATE grading_runs SET status='completed',stage='completed',updated_at=? WHERE id=?",
            (now_iso(), run_id),
        )

    def mark_failed(self, run_id: str, _error: Exception) -> None:
        raise AssertionError(f"artifact generation unexpectedly failed for {run_id}")


def _configure_calculation_grading(database: Database) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE questions SET question_type='calculation',score=4 WHERE id='question'"
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES('calculation-match','task','question','manual','confirmed','x=42',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('question','calculation','4.00',1,?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,confirmed_by,
                 frozen_at,created_at,updated_at
               ) VALUES(
                 'calculation-rubric','question',1,'frozen','4.00','manual','teacher',?,?,?
               )""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO rubric_points(
                 id,rubric_version_id,point_key,sort_order,criterion,score,created_at,updated_at
               ) VALUES('calculation-point','calculation-rubric','P1',0,
                 '过程与结论正确','4.00',?,?)""",
            (timestamp, timestamp),
        )


@pytest.mark.asyncio
async def test_calculation_localization_persists_and_replays_the_same_evidence_for_grading(
    tmp_path: Path,
) -> None:
    settings, database, frame_set_id = _single_page_case(tmp_path)
    _configure_calculation_grading(database)
    recognition = CombinedCalculationRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        """SELECT * FROM student_responses
           WHERE submission_id='submission' AND question_id='question'"""
    )
    assert response is not None
    raw_recognition = json_loads(response["raw_recognition_json"], {})
    localization = raw_recognition["localization"]
    assert response["status"] == "recognized"
    assert raw_recognition["recognitionPath"] == "single_pass"
    assert recognition.calculation_recognition_calls == 1
    assert recognition.calculation_location_calls == 0
    assert recognition.student_response_calls == 0
    assert localization["evidenceComplete"] is True
    assert localization["plan"]["frameSetId"] == frame_set_id
    evidence_id = localization["evidence"][0]["evidenceId"]

    grading_model = _SuccessfulCalculationGrader()
    grading_settings = replace(settings, grading_enabled=True)
    grading = GradingPipeline(
        grading_settings,
        database,
        cast(DashScopeClient, grading_model),
        cast(Any, _CompletingArtifacts(database)),
    )
    run_id = grading.create_run("submission")
    await grading.run(run_id)

    result = database.fetchone(
        "SELECT * FROM grading_question_results WHERE grading_run_id=? AND question_id='question'",
        (run_id,),
    )
    assert result is not None
    assert result["status"] == "final"
    assert result["final_score"] == "4.00"
    assert grading_model.evidence_id == evidence_id
    assert database.fetchone("SELECT status FROM grading_runs WHERE id=?", (run_id,)) == {
        "status": "completed"
    }

    audit = database.fetchone(
        """SELECT g.id AS grading_result_id,r.id AS response_id,e.id AS evidence_id,
                  e.student_page_id,e.template_page_id,p.id AS processing_revision_id,
                  r.frame_set_id,
                  json_extract(r.raw_recognition_json,
                    '$.localization.evidence[0].alignmentRevisionId') AS alignment_revision_id
           FROM grading_question_results g
           JOIN student_responses r ON r.id=g.student_response_id
           JOIN student_response_regions e
             ON e.id=json_extract(g.evidence_refs_json,'$[0].region_id')
           JOIN student_processing_revisions p ON p.id=r.processing_revision_id
           WHERE g.id=?""",
        (result["id"],),
    )
    assert audit == {
        "grading_result_id": result["id"],
        "response_id": response["id"],
        "evidence_id": evidence_id,
        "student_page_id": localization["evidence"][0]["studentPageId"],
        "template_page_id": localization["evidence"][0]["templatePageId"],
        "processing_revision_id": response["processing_revision_id"],
        "frame_set_id": frame_set_id,
        "alignment_revision_id": localization["evidence"][0]["alignmentRevisionId"],
    }

    persisted_json = "\n".join(
        str(value)
        for value in (
            response["raw_recognition_json"],
            result["answer_snapshot_json"],
            result["grading_config_snapshot_json"],
            result["decisions_json"],
            result["evidence_refs_json"],
            result["tool_observations_json"],
        )
    )
    assert "template_image" not in persisted_json
    assert "student_image" not in persisted_json
    assert "data:image" not in persisted_json


@pytest.mark.asyncio
async def test_reliable_blank_window_is_persisted_and_graded_with_paired_negative_evidence(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    _configure_calculation_grading(database)
    recognition = BlankCalculationRecognition()

    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        """SELECT * FROM student_responses
           WHERE submission_id='submission' AND question_id='question'"""
    )
    assert response is not None
    raw_recognition = json_loads(response["raw_recognition_json"], {})
    localization = raw_recognition["localization"]
    assert raw_recognition["isBlank"] is True
    assert localization["reliableBlank"] is True
    assert localization["evidence"][0]["evidenceKind"] == "blank_search_window"
    assert recognition.student_response_calls == 0

    grading_model = _BlankCalculationGrader()
    grading = GradingPipeline(
        replace(settings, grading_enabled=True),
        database,
        cast(DashScopeClient, grading_model),
        cast(Any, _CompletingArtifacts(database)),
    )
    run_id = grading.create_run("submission")
    await grading.run(run_id)

    result = database.fetchone(
        "SELECT * FROM grading_question_results WHERE grading_run_id=? AND question_id='question'",
        (run_id,),
    )
    assert result is not None
    assert result["status"] == "final"
    assert result["final_score"] == "0.00"
    assert grading_model.evidence_id == localization["evidence"][0]["evidenceId"]
    assert len(grading_model.calls) == 1


@pytest.mark.asyncio
async def test_partial_cross_page_locator_failure_keeps_evidence_but_never_calls_grader(
    tmp_path: Path,
) -> None:
    settings, database, _frame_set_id = _single_page_case(tmp_path)
    settings = replace(settings, answer_pages_per_batch=1, grading_enabled=True)
    _configure_calculation_grading(database)
    timestamp = now_iso()
    template_page_2 = tmp_path / "template-2.jpg"
    student_page_2 = tmp_path / "uploads" / "task" / "students" / "submission" / "page-2.jpg"
    _exam_page(template_page_2, student=False)
    _exam_page(student_page_2, student=True)
    with database.transaction() as connection:
        connection.execute("UPDATE documents SET page_count=2 WHERE id='exam'")
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page-2','exam',2,'template-2.jpg',400,500,'page-2-sha')"""
        )
        connection.executemany(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
            [
                (
                    "stored-student-page-1",
                    "submission",
                    1,
                    "uploads/task/students/submission/page.png",
                    400,
                    500,
                    "student-page-1-sha",
                    timestamp,
                    timestamp,
                ),
                (
                    "stored-student-page-2",
                    "submission",
                    2,
                    "uploads/task/students/submission/page-2.jpg",
                    400,
                    500,
                    "student-page-2-sha",
                    timestamp,
                    timestamp,
                ),
            ],
        )

    recognition = PartiallyFailingCalculationRecognition()
    await StudentPipeline(
        settings,
        database,
        cast(RecognitionService, recognition),
    ).run("submission")

    response = database.fetchone(
        """SELECT * FROM student_responses
           WHERE submission_id='submission' AND question_id='question'"""
    )
    assert response is not None
    raw_recognition = json_loads(response["raw_recognition_json"], {})
    localization = raw_recognition["localization"]
    assert response["status"] == "needs_review"
    assert localization["evidenceComplete"] is False
    assert [batch["status"] for batch in localization["batches"]] == [
        "blank",
        "needs_review",
    ]
    assert len(localization["evidence"]) == 1
    assert localization["evidence"][0]["evidenceKind"] == "blank_search_window"

    grading_model = _RejectingCalculationGrader()
    grading = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, grading_model),
        cast(Any, _CompletingArtifacts(database)),
    )
    run_id = grading.create_run("submission")
    await grading.run(run_id)

    assert grading_model.calls == 0
    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    result = database.fetchone(
        "SELECT * FROM grading_question_results WHERE grading_run_id=?",
        (run_id,),
    )
    assert run is not None and result is not None
    assert run["status"] == "needs_review"
    assert run["total_score"] is None
    assert result["status"] == "needs_review"
    assert "MISSING_EVIDENCE" in json_loads(result["review_reasons_json"], [])
    assert {item["status"] for item in json_loads(result["decisions_json"], [])} == {
        "unable"
    }
    assert json_loads(result["error_locations_json"], []) == []
