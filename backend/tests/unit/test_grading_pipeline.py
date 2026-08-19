from __future__ import annotations

import base64
import json
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from homework_judge.alignment.geometry import Homography, Polygon
from homework_judge.config import Settings
from homework_judge.db.database import Database, json_dumps, json_loads, now_iso
from homework_judge.errors import AppError, ModelError
from homework_judge.grading.contracts import (
    BoundingBox,
    EvidenceRef,
    QuestionGradingInput,
    QuestionType,
)
from homework_judge.jobs.grading_pipeline import GradingPipeline
from homework_judge.recognition.client import DashScopeClient, ModelResponse


class UnexpectedModelClient:
    async def chat(self, **kwargs: Any) -> ModelResponse:
        if "错题诊断教师" in str(kwargs.get("system_prompt", "")):
            content = kwargs["user_content"]
            payload = json.loads(content[0]["text"])
            return ModelResponse(
                content=json.dumps(
                    {
                        "summary": "本次存在多选漏选，应加强逐项核对。",
                        "questions": [
                            {
                                "questionId": item["questionId"],
                                "errorCategory": "incomplete_answer",
                                "errorReason": "学生漏选了一个符合题意的选项。",
                                "knowledgeGap": "多选题逐项验证与完整作答能力",
                                "masteredParts": ["已经选出了部分正确选项"],
                                "suggestion": "逐项写出判断依据，提交前对照题干检查是否漏选。",
                            }
                            for item in payload["questions"]
                        ],
                    },
                    ensure_ascii=False,
                ),
                raw={},
                usage={"totalTokens": 10},
            )
        raise AssertionError("objective choice grading must not call the model")


class RecoveringErrorAnalysisModel(UnexpectedModelClient):
    def __init__(self) -> None:
        self.available = False
        self.analysis_calls = 0

    async def chat(self, **kwargs: Any) -> ModelResponse:
        if "error-analysis-v1-independent-diagnosis" in str(kwargs.get("system_prompt", "")):
            self.analysis_calls += 1
            if not self.available:
                raise ModelError("MODEL_TIMEOUT", "timeout")
        return await super().chat(**kwargs)


class NoModelCallClient:
    async def chat(self, **_kwargs: Any) -> ModelResponse:
        raise AssertionError("a full-score report must not call the diagnosis model")


class InvalidErrorAnalysisModel(UnexpectedModelClient):
    async def chat(self, **kwargs: Any) -> ModelResponse:
        if "error-analysis-v1-independent-diagnosis" in str(kwargs.get("system_prompt", "")):
            return ModelResponse(
                content='{"summary":"无效结果","questions":[]}',
                raw={},
                usage={"totalTokens": 1},
            )
        return await super().chat(**kwargs)


def seed_multiple_choice_submission(
    database: Database,
    *,
    recognized_answer: str = "AC",
    confidence: float = 0.99,
) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Exam','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','task','exam_recognition','succeeded','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES(
                 'question','task','source',0,'1','1','Select all correct options',
                 'multiple_choice',6,'[1]',1,'[]','confirmed'
               )"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES('match','task','question','manual','confirmed','ACD',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES('submission','task','Student','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'student-page','submission',1,'student-page.png',1000,1400,'sha',
                 'aligned',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,recognized_text,confidence,
                 raw_recognition_json,status,created_at,updated_at
               ) VALUES(
                 'response','submission','question','1',?,?,?,'recognized',?,?
               )""",
            (
                recognized_answer,
                confidence,
                json_dumps(
                    {
                        "isBlank": False,
                        "issues": [],
                        "segments": [
                            {
                                "region_index": 1,
                                "transcription": recognized_answer,
                            }
                        ],
                    }
                ),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES('region','response',0,'student-page',?,?,?)""",
            (
                json_dumps({"x": 100, "y": 200, "width": 300, "height": 100}),
                json_dumps({"x": 105, "y": 205, "width": 300, "height": 100}),
                timestamp,
            ),
        )


def grading_settings(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite",
        port=8787,
        dashscope_api_key="test",
        dashscope_base_url="https://example.invalid/v1",
        dashscope_model="test-vl",
        model_timeout_ms=1000,
        model_retry_count=0,
        model_concurrency=1,
        model_pages_per_batch=4,
        answer_pages_per_batch=3,
        max_upload_mb=30,
        max_document_pages=30,
        auto_match_threshold=0.82,
        auto_match_margin=0.08,
        teacher_name="test",
        soffice_path="",
        grading_enabled=True,
    )


def seed_calculation_pair_storage(database: Database, settings: Settings) -> None:
    timestamp = now_iso()
    template = Image.new("RGB", (100, 100), "white")
    template.paste((255, 0, 0), (10, 10, 30, 30))
    template.save(settings.data_dir / "calculation-template.png")
    student = Image.new("RGB", (140, 100), "white")
    # Captured template->student transform moves the real answer 30 px right.
    student.paste((0, 0, 255), (40, 10, 60, 30))
    # Deliberately mark the persisted original-page bbox elsewhere. A forbidden
    # direct bbox crop would return this green patch instead of the blue answer.
    student.paste((0, 255, 0), (100, 60, 120, 80))
    student.save(settings.data_dir / "calculation-student.png")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('pair-task','Pair','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,relative_path,created_at
               ) VALUES('pair-document','pair-task','exam','template.png','template.png',
                 'image/png','.png',1,'template-sha','calculation-template.png',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('pair-template-page','pair-document',1,
                 'calculation-template.png',100,100,'template-page-sha')"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES('pair-submission','pair-task','Student','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,status,input_hash,is_current,source,
                 issues_json,created_at,updated_at,finished_at
               ) VALUES('pair-processing','pair-submission',1,'ready','pair-input',1,
                 'system','[]',?,?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 template_page_id,alignment_status,created_at,updated_at
               ) VALUES('pair-student-page','pair-submission',1,
                 'calculation-student.png',140,100,'student-sha','pair-template-page',
                 'aligned',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_page_alignment_revisions(
                 id,processing_revision_id,student_page_id,revision_number,
                 template_page_id,transform_json,quality,method,status,control_points_json,
                 metrics_json,source,is_current,issues_json,created_by,created_at,updated_at
               ) VALUES('pair-alignment','pair-processing','pair-student-page',1,
                 'pair-template-page',?,1,'test','aligned','[]','{}','model',1,'[]',
                 'test',?,?)""",
            (
                json_dumps([[1, 0, 30], [0, 1, 0], [0, 0, 1]]),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,processing_revision_id,question_number,recognized_text,
                 confidence,raw_recognition_json,status,created_at,updated_at
               ) VALUES('pair-response','pair-submission','pair-processing','1','x=1',
                 0.99,'{}','recognized',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,template_page_id,student_page_id,
                 template_bbox_json,student_bbox_json,created_at
               ) VALUES('pair-evidence','pair-response',0,'pair-template-page',
                 'pair-student-page',?,?,?)""",
            (
                json_dumps({"x": 10, "y": 10, "width": 20, "height": 20}),
                json_dumps({"x": 100, "y": 60, "width": 20, "height": 20}),
                timestamp,
            ),
        )


def seed_calculation_grading_context(database: Database) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('pair-source','pair-task','exam_recognition','succeeded','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES('pair-question','pair-task','pair-source',0,'1','1','计算',
                 'calculation',1,'[1]',1,'[]','confirmed')"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,teacher_explanation,
                 updated_at
               ) VALUES('pair-match','pair-task','pair-question','manual','confirmed','1',
                 '由标准公式代入得到 1',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('pair-question','calculation','1.00',1,?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,confirmed_by,
                 frozen_at,created_at,updated_at
               ) VALUES('pair-rubric','pair-question',1,'frozen','1.00','manual','teacher',
                 ?,?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO rubric_points(
                 id,rubric_version_id,point_key,sort_order,criterion,score,created_at,updated_at
               ) VALUES('pair-point','pair-rubric','P1',0,'answer','1.00',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            "UPDATE student_responses SET question_id='pair-question' "
            "WHERE id='pair-response'"
        )
        connection.execute(
            """UPDATE student_submissions
               SET current_processing_revision_id='pair-processing'
               WHERE id='pair-submission'"""
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,input_snapshot_json,
                 config_snapshot_json,progress_total,created_at,updated_at
               ) VALUES('pair-run','pair-submission','pair-task','grading','grading',
                 'pair-run-input','{}','{}',1,?,?)""",
            (timestamp, timestamp),
        )


def seed_structural_calculation_submission(database: Database) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('structural-task','Structural','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('structural-source','structural-task','exam_recognition',
                 'succeeded','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES('structural-question','structural-task','structural-source',0,
                 '1','1','计算','calculation',2,'[1]',1,'[]','confirmed')"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES('structural-match','structural-task','structural-question',
                 'manual','confirmed','答案',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('structural-question','calculation','2.00',1,?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,confirmed_by,
                 frozen_at,created_at,updated_at
               ) VALUES('structural-rubric','structural-question',1,'frozen','2.00',
                 'manual','teacher',?,?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO rubric_points(
                 id,rubric_version_id,point_key,sort_order,criterion,score,created_at,updated_at
               ) VALUES('structural-point','structural-rubric','P1',0,'过程','2.00',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,source,revision,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES('structural-frame','structural-task',1,'confirmed','teacher',1,
                 'structural-frame-hash','teacher',?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE tasks SET current_question_frame_set_id='structural-frame'
               WHERE id='structural-task'"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES('structural-submission','structural-task','Student','ready',
                 'needs_review',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,created_at,updated_at,finished_at
               ) VALUES('structural-processing','structural-submission',1,
                 'structural-frame','mapping_needs_review','input',1,'system','[]',?,?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions
               SET current_processing_revision_id='structural-processing'
               WHERE id='structural-submission'"""
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 question_number,recognized_text,confidence,raw_recognition_json,status,
                 created_at,updated_at
               ) VALUES('structural-response','structural-submission','structural-question',
                 'structural-processing','structural-frame','1','',0,?,'needs_review',?,?)""",
            (
                json_dumps(
                    {
                        "issues": [{"code": "tail_page_alignment_missing"}],
                        "localization": {
                            "schemaVersion": 1,
                            "evidenceComplete": False,
                            "evidence": [],
                        },
                    }
                ),
                timestamp,
                timestamp,
            ),
        )


def calculation_pair_input() -> QuestionGradingInput:
    return QuestionGradingInput(
        run_id="pair-run",
        question_id="pair-question",
        question_type=QuestionType.CALCULATION,
        max_score=Decimal("1"),
        question_content="计算",
        standard_answer_snapshot={},
        student_response={"recognizedText": "x=1"},
        evidence_regions=[
            EvidenceRef(
                page_id="pair-student-page",
                region_id="pair-evidence",
                original_bbox=BoundingBox(x=100, y=60, width=20, height=20),
                recognized_text="x=1",
                template_page_id="pair-template-page",
                template_bbox=BoundingBox(x=10, y=10, width=20, height=20),
                alignment_revision_id="pair-alignment",
                evidence_kind="located_region",
            )
        ],
        grading_config={
            "rubricPoints": [
                {"key": "P1", "criterion": "answer", "score": "1", "order": 0}
            ],
            "rubricPointIds": {"P1": "pair-point"},
        },
        rubric_version_id="pair-rubric",
        processing_revision_id="pair-processing",
        recognition_evidence_complete=True,
    )


def test_calculation_pair_loader_replays_captured_alignment_in_template_coordinates(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    pairs, complete = pipeline._load_calculation_evidence_images(calculation_pair_input())

    assert complete is True
    assert [pair.region_id for pair in pairs] == ["pair-evidence"]
    with Image.open(BytesIO(pairs[0].template_image)) as template_crop:
        assert template_crop.size == (20, 20)
        template_pixel = cast(
            tuple[int, int, int],
            template_crop.convert("RGB").getpixel((10, 10)),
        )
    with Image.open(BytesIO(pairs[0].student_image)) as student_crop:
        assert student_crop.size == (20, 20)
        student_pixel = cast(
            tuple[int, int, int],
            student_crop.convert("RGB").getpixel((10, 10)),
        )
    assert template_pixel[0] > 200 and template_pixel[1] < 40
    assert student_pixel[2] > 200 and student_pixel[1] < 40


def test_calculation_input_snapshots_standard_answer_and_explanation(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    seed_calculation_grading_context(database)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    [grading_input] = pipeline._build_inputs("pair-submission", "pair-run")

    assert grading_input.standard_answer_snapshot == {
        "answer": "1",
        "explanation": "由标准公式代入得到 1",
    }


def test_calculation_pair_loader_replays_noncurrent_captured_perspective_revision(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )
    captured = Homography.from_rows(
        (
            (1.0, 0.05, 25.0),
            (0.02, 1.0, 5.0),
            (0.0005, 0.0003, 1.0),
        )
    )
    template_space_student = Image.new("RGB", (100, 100), "white")
    template_space_student.paste((0, 0, 255), (10, 10, 30, 30))
    perspective_student = template_space_student.transform(
        (140, 100),
        Image.Transform.PERSPECTIVE,
        captured.inverse.pillow_coefficients(),
        resample=Image.Resampling.NEAREST,
        fillcolor="white",
    )
    perspective_student.save(settings.data_dir / "calculation-student.png")
    template_polygon = Polygon.rectangle(10, 10, 30, 30)
    student_polygon = captured.map_polygon(template_polygon)
    mapped_bounds = student_polygon.bounds
    mapped_box = {
        "x": mapped_bounds.left,
        "y": mapped_bounds.top,
        "width": mapped_bounds.width,
        "height": mapped_bounds.height,
    }
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE student_response_regions SET student_bbox_json=? "
            "WHERE id='pair-evidence'",
            (json_dumps(mapped_box),),
        )
        connection.execute(
            "UPDATE student_page_alignment_revisions SET transform_json=?,is_current=0 "
            "WHERE id='pair-alignment'",
            (json_dumps(captured.rows),),
        )
        # A later current revision deliberately disagrees. Grading must replay
        # the revision captured by localization, not whichever row is current.
        connection.execute(
            """INSERT INTO student_page_alignment_revisions(
                 id,processing_revision_id,student_page_id,revision_number,
                 template_page_id,transform_json,quality,method,status,control_points_json,
                 metrics_json,source,is_current,issues_json,created_by,created_at,updated_at
               ) VALUES('pair-alignment-current','pair-processing','pair-student-page',2,
                 'pair-template-page',?,1,'test','aligned','[]','{}','teacher',1,'[]',
                 'teacher',?,?)""",
            (
                json_dumps([[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                timestamp,
                timestamp,
            ),
        )
    localization = {
        "schemaVersion": 1,
        "evidenceComplete": True,
        "evidence": [
            {
                "evidenceId": "pair-evidence",
                "evidenceKind": "located_region",
                "fragmentKey": "pair-question:page:1",
                "templatePageId": "pair-template-page",
                "studentPageId": "pair-student-page",
                "alignmentRevisionId": "pair-alignment",
                "templateBboxPx": {"x": 10, "y": 10, "width": 20, "height": 20},
                "templateBboxNormalized": {
                    "x": 0.1,
                    "y": 0.1,
                    "width": 0.2,
                    "height": 0.2,
                },
                "studentBboxPx": mapped_box,
                "studentPolygonPx": student_polygon.as_dicts(),
                "batchIndex": 1,
                "attemptId": "perspective-attempt",
                "modelCandidateIndex": 0,
                "confidence": 0.99,
                "issues": [],
            }
        ],
    }

    evidence, evidence_complete = pipeline._evidence(
        "pair-response",
        {
            "segments": [{"region_index": 1, "transcription": "x=1"}],
            "localization": localization,
        },
    )

    assert evidence_complete is True
    assert len(evidence) == 1
    audit_ref = evidence[0]
    assert audit_ref.region_id == "pair-evidence"
    assert audit_ref.page_id == "pair-student-page"
    assert audit_ref.recognized_text == "x=1"
    assert audit_ref.original_bbox == BoundingBox.model_validate(mapped_box)
    assert audit_ref.template_page_id == "pair-template-page"
    assert audit_ref.template_bbox == BoundingBox(x=10, y=10, width=20, height=20)
    assert audit_ref.alignment_revision_id == "pair-alignment"
    assert audit_ref.evidence_kind == "located_region"
    grading_input = calculation_pair_input().model_copy(
        update={"evidence_regions": evidence}
    )
    pairs, pairs_complete = pipeline._load_calculation_evidence_images(grading_input)

    assert pairs_complete is True
    assert [pair.region_id for pair in pairs] == ["pair-evidence"]
    with Image.open(BytesIO(pairs[0].template_image)) as template_crop:
        assert template_crop.size == (20, 20)
        template_pixel = cast(
            tuple[int, int, int],
            template_crop.convert("RGB").getpixel((10, 10)),
        )
    with Image.open(BytesIO(pairs[0].student_image)) as student_crop:
        assert student_crop.size == (20, 20)
        student_pixel = cast(
            tuple[int, int, int],
            student_crop.convert("RGB").getpixel((10, 10)),
        )
    assert template_pixel[0] > 200 and template_pixel[1] < 40
    assert student_pixel[2] > 180 and student_pixel[1] < 60


def test_calculation_pair_loader_fails_closed_when_one_source_image_is_missing(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    (settings.data_dir / "calculation-template.png").unlink()
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    pairs, complete = pipeline._load_calculation_evidence_images(calculation_pair_input())

    assert pairs == []
    assert complete is False


def test_calculation_pair_loader_rejects_locally_clipped_evidence(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    database.execute(
        "UPDATE student_page_alignment_revisions SET transform_json=? "
        "WHERE id='pair-alignment'",
        (json_dumps([[1, 0, 0], [0, 1, 30], [0, 0, 1]]),),
    )
    grading_input = calculation_pair_input()
    clipped_evidence = grading_input.evidence_regions[0].model_copy(
        update={"template_bbox": BoundingBox(x=10, y=80, width=20, height=20)}
    )
    grading_input = grading_input.model_copy(
        update={"evidence_regions": [clipped_evidence]}
    )
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    pairs, complete = pipeline._load_calculation_evidence_images(grading_input)

    assert pairs == []
    assert complete is False


def test_localization_v1_requires_exact_structural_evidence_set(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )
    localized: dict[str, Any] = {
        "schemaVersion": 1,
        "evidenceComplete": True,
        "evidence": [
            {
                "evidenceId": "pair-evidence",
                "evidenceKind": "located_region",
                "fragmentKey": "pair-question:page:1",
                "templatePageId": "pair-template-page",
                "studentPageId": "pair-student-page",
                "alignmentRevisionId": "pair-alignment",
                "templateBboxPx": {"x": 10, "y": 10, "width": 20, "height": 20},
                "templateBboxNormalized": {
                    "x": 0.1,
                    "y": 0.1,
                    "width": 0.2,
                    "height": 0.2,
                },
                "studentBboxPx": {"x": 100, "y": 60, "width": 20, "height": 20},
                "studentPolygonPx": [
                    {"x": 100, "y": 60},
                    {"x": 120, "y": 60},
                    {"x": 120, "y": 80},
                    {"x": 100, "y": 80},
                ],
                "batchIndex": 1,
                "attemptId": "attempt-1",
                "modelCandidateIndex": 0,
                "confidence": 0.99,
                "issues": [],
            }
        ],
    }

    evidence, complete = pipeline._evidence(
        "pair-response",
        {
            "segments": [{"region_index": 1, "transcription": "x=1"}],
            "localization": localized,
        },
    )

    assert complete is True
    assert evidence[0].recognized_text == "x=1"
    assert evidence[0].alignment_revision_id == "pair-alignment"
    assert evidence[0].template_bbox == BoundingBox(x=10, y=10, width=20, height=20)
    localized["evidence"][0]["evidenceId"] = "different-evidence"
    _, mismatched_complete = pipeline._evidence(
        "pair-response",
        {"localization": localized},
    )
    assert mismatched_complete is False
    legacy_evidence, legacy_complete = pipeline._evidence(
        "pair-response",
        {"segments": [{"region_index": 1, "transcription": "legacy"}]},
    )
    assert legacy_complete is True
    assert len(legacy_evidence) == 1
    assert legacy_evidence[0].evidence_kind == "legacy"
    assert legacy_evidence[0].recognized_text == "legacy"
    assert legacy_evidence[0].template_page_id == "pair-template-page"
    assert legacy_evidence[0].template_bbox == BoundingBox(
        x=10,
        y=10,
        width=20,
        height=20,
    )
    assert legacy_evidence[0].alignment_revision_id is None
    legacy_input = calculation_pair_input().model_copy(
        update={"evidence_regions": legacy_evidence}
    )
    legacy_pairs, legacy_pairs_complete = pipeline._load_calculation_evidence_images(
        legacy_input
    )
    assert legacy_pairs_complete is True
    assert [pair.evidence_kind for pair in legacy_pairs] == ["legacy"]
    with Image.open(BytesIO(legacy_pairs[0].student_image)) as legacy_student_crop:
        legacy_pixel = cast(
            tuple[int, int, int],
            legacy_student_crop.convert("RGB").getpixel((10, 10)),
        )
    assert legacy_pixel[2] > 200 and legacy_pixel[1] < 40

    historical_payload = calculation_pair_input().model_dump(mode="json")
    historical_payload.pop("recognition_evidence_complete")
    for item in cast(list[dict[str, Any]], historical_payload["evidence_regions"]):
        for key in (
            "template_page_id",
            "template_bbox",
            "alignment_revision_id",
            "evidence_kind",
        ):
            item.pop(key)
    historical_input = QuestionGradingInput.model_validate(historical_payload)
    assert historical_input.recognition_evidence_complete is True
    assert historical_input.evidence_regions[0].evidence_kind is None


@pytest.mark.asyncio
async def test_pipeline_turns_foreign_calculation_evidence_id_into_safe_invalid_result(
    tmp_path: Path,
) -> None:
    class ForeignEvidenceModel:
        settings = SimpleNamespace(dashscope_model="foreign-evidence-stub")

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def chat(self, **kwargs: object) -> ModelResponse:
            self.calls.append(kwargs)
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": "P1",
                                "status": "satisfied",
                                "reason": "引用了其他响应",
                                "evidenceRegionIds": ["foreign-response-evidence"],
                                "confidence": 0.99,
                            }
                        ],
                        "uncoveredMethod": False,
                    },
                    ensure_ascii=False,
                ),
                raw={},
                usage={},
            )

    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    seed_calculation_grading_context(database)
    model = ForeignEvidenceModel()
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, model),
    )

    await pipeline._grade_one("pair-run", calculation_pair_input())

    assert len(model.calls) == 1
    result = database.fetchone(
        "SELECT * FROM grading_question_results "
        "WHERE grading_run_id='pair-run' AND question_id='pair-question'"
    )
    assert result is not None
    assert result["status"] == "needs_review"
    assert result["error_code"] == "INVALID_MODEL_OUTPUT"
    assert result["error_message"] == (
        "ValueError: model referenced evidence outside the current response"
    )
    assert json_loads(result["decisions_json"], []) == []
    assert "INVALID_MODEL_OUTPUT" in json_loads(result["review_reasons_json"], [])
    assert "foreign-response-evidence" not in result["evidence_refs_json"]
    reviews = database.fetchall(
        "SELECT reason FROM grading_review_items "
        "WHERE grading_run_id='pair-run' AND status='open'"
    )
    assert {row["reason"] for row in reviews} >= {"INVALID_MODEL_OUTPUT"}


@pytest.mark.asyncio
async def test_pipeline_preserves_rejected_calculation_model_output_diagnostics(
    tmp_path: Path,
) -> None:
    rejected_content = json.dumps(
        {
            "points": [
                {
                    "pointKey": "P1",
                    "status": "satisfied",
                    "reason": "证据可见",
                    "evidenceRegionIds": ["pair-evidence"],
                }
            ],
            "uncoveredMethod": False,
        },
        ensure_ascii=False,
    )

    class InvalidSchemaModel:
        settings = SimpleNamespace(dashscope_model="invalid-schema-stub")

        async def chat(self, **_kwargs: object) -> ModelResponse:
            return ModelResponse(
                content=rejected_content,
                raw={},
                usage={"totalTokens": 23},
            )

    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    seed_calculation_grading_context(database)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, InvalidSchemaModel()),
    )

    grading_input = calculation_pair_input()
    await pipeline._grade_one("pair-run", grading_input)
    pipeline._finish_scoring("pair-run", [grading_input])

    result = database.fetchone(
        "SELECT * FROM grading_question_results "
        "WHERE grading_run_id='pair-run' AND question_id='pair-question'"
    )
    assert result is not None
    assert result["status"] == "needs_review"
    assert result["error_code"] == "INVALID_MODEL_OUTPUT"
    assert "points.0.confidence" in result["error_message"]
    assert json_loads(result["decisions_json"], []) == []
    observations = json_loads(result["tool_observations_json"], [])
    assert observations[0]["tool"] == "calculation_model_output_rejected"
    assert observations[0]["payload"]["rawModelContent"] == rejected_content
    assert observations[0]["payload"]["validationErrors"] == [
        {
            "type": "missing",
            "path": "points.0.confidence",
            "message": "Field required",
        }
    ]
    run = database.fetchone("SELECT * FROM grading_runs WHERE id='pair-run'")
    assert run is not None
    assert run["total_score"] is None


@pytest.mark.asyncio
async def test_calculation_pair_pixels_stay_ephemeral_across_hash_snapshots_and_database(
    tmp_path: Path,
) -> None:
    class SatisfiedEvidenceModel:
        settings = SimpleNamespace(dashscope_model="satisfied-evidence-stub")

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def chat(self, **kwargs: object) -> ModelResponse:
            self.calls.append(kwargs)
            return ModelResponse(
                content=json.dumps(
                    {
                        "points": [
                            {
                                "pointKey": "P1",
                                "status": "satisfied",
                                "reason": "证据可见",
                                "evidenceRegionIds": ["pair-evidence"],
                                "confidence": 0.99,
                            }
                        ],
                        "uncoveredMethod": False,
                    },
                    ensure_ascii=False,
                ),
                raw={},
                usage={"totalTokens": 3},
            )

    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_pair_storage(database, settings)
    seed_calculation_grading_context(database)
    model = SatisfiedEvidenceModel()
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, model),
    )
    grading_input = calculation_pair_input()
    pairs, complete = pipeline._load_calculation_evidence_images(grading_input)
    assert complete is True
    assert len(pairs) == 1
    encoded_pixels = {
        base64.b64encode(pairs[0].template_image).decode("ascii"),
        base64.b64encode(pairs[0].student_image).decode("ascii"),
    }
    run_snapshot = {
        "submissionId": "pair-submission",
        "questions": [grading_input.model_dump(mode="json")],
    }
    database.execute(
        "UPDATE grading_runs SET input_snapshot_json=? WHERE id='pair-run'",
        (json_dumps(run_snapshot),),
    )

    await pipeline._grade_one("pair-run", grading_input)

    assert len(model.calls) == 1
    content = model.calls[0]["user_content"]
    assert isinstance(content, list)
    sent_urls = [
        item["image_url"]["url"]
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert {url.rsplit(",", 1)[1] for url in sent_urls} == encoded_pixels
    result = database.fetchone(
        "SELECT * FROM grading_question_results "
        "WHERE grading_run_id='pair-run' AND question_id='pair-question'"
    )
    assert result is not None
    assert result["status"] == "final"
    assert result["input_hash"] == pipeline._question_hash(grading_input)
    evidence_snapshot = json_loads(result["evidence_refs_json"], [])
    assert evidence_snapshot[0]["region_id"] == "pair-evidence"
    assert evidence_snapshot[0]["alignment_revision_id"] == "pair-alignment"
    assert evidence_snapshot[0]["template_page_id"] == "pair-template-page"
    assert evidence_snapshot[0]["template_bbox"] == {
        "x": 10.0,
        "y": 10.0,
        "width": 20.0,
        "height": 20.0,
    }
    with database.connect() as connection:
        database_dump = "\n".join(connection.iterdump())
    assert "data:image/jpeg;base64" not in database_dump
    assert all(encoded not in database_dump for encoded in encoded_pixels)


@pytest.mark.asyncio
async def test_pipeline_admits_structural_calculation_response_only_for_safe_review(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_structural_calculation_submission(database)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    run_id = pipeline.create_run("structural-submission")
    await pipeline.run(run_id)

    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert run is not None
    assert run["status"] == "needs_review"
    assert run["total_score"] is None
    result = database.fetchone(
        "SELECT * FROM grading_question_results WHERE grading_run_id=?",
        (run_id,),
    )
    assert result is not None
    assert result["status"] == "needs_review"
    assert Decimal(result["raw_score"]) == Decimal(0)
    assert result["final_score"] == "0.00"
    assert {item["status"] for item in json_loads(result["decisions_json"], [])} == {
        "unable"
    }
    assert json_loads(result["error_locations_json"], []) == []
    assert "MISSING_EVIDENCE" in json_loads(result["review_reasons_json"], [])
    assert database.fetchall(
        "SELECT * FROM grading_artifacts WHERE grading_run_id=?",
        (run_id,),
    ) == []


@pytest.mark.parametrize("evidence_states", [(True,), (False, True)])
def test_mapping_review_admits_complete_and_mixed_v1_calculation_snapshots(
    tmp_path: Path,
    evidence_states: tuple[bool, ...],
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_structural_calculation_submission(database)
    timestamp = now_iso()
    first_raw = {
        "localization": {
            "schemaVersion": 1,
            "evidenceComplete": evidence_states[0],
            "evidence": [],
        }
    }
    database.execute(
        "UPDATE student_responses SET raw_recognition_json=? WHERE id='structural-response'",
        (json_dumps(first_raw),),
    )
    if len(evidence_states) == 2:
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                     stem,question_type,score,source_pages_json,confidence,issues_json,
                     confirmation_status
                   ) VALUES('structural-question-2','structural-task','structural-source',1,
                     '2','2','Second','calculation',2,'[1]',1,'[]','confirmed')"""
            )
            connection.execute(
                """INSERT INTO student_responses(
                     id,submission_id,question_id,processing_revision_id,frame_set_id,
                     question_number,recognized_text,confidence,raw_recognition_json,status,
                     created_at,updated_at
                   ) VALUES('structural-response-2','structural-submission',
                     'structural-question-2','structural-processing','structural-frame','2','',
                     0,?,'needs_review',?,?)""",
                (
                    json_dumps(
                        {
                            "localization": {
                                "schemaVersion": 1,
                                "evidenceComplete": evidence_states[1],
                                "evidence": [],
                            }
                        }
                    ),
                    timestamp,
                    timestamp,
                ),
            )
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    submission = pipeline._submission("structural-submission")

    assert submission["current_processing_revision_id"] == "structural-processing"


@pytest.mark.parametrize("invalid_case", ["unknown_snapshot", "mixed_type", "missing_response"])
def test_pipeline_keeps_legacy_precheck_for_non_narrow_structural_cases(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_structural_calculation_submission(database)
    if invalid_case == "unknown_snapshot":
        database.execute(
            "UPDATE student_responses SET raw_recognition_json='{}' WHERE id='structural-response'"
        )
    elif invalid_case == "mixed_type":
        database.execute(
            """UPDATE question_grading_configs SET question_type='multiple_choice'
               WHERE question_id='structural-question'"""
        )
    else:
        database.execute("DELETE FROM student_responses WHERE id='structural-response'")
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    with pytest.raises(AppError) as captured:
        pipeline.create_run("structural-submission")

    assert captured.value.code == "QUESTION_REGIONS_NOT_READY"


def seed_keyed_fill_submission(database: Database, blank_count: int) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('fill-task','Fill','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,relative_path,created_at
               ) VALUES('exam','fill-task','exam','exam.pdf','exam.pdf','application/pdf',
                 '.pdf',1,'exam-sha','exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam',1,'template.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('fill-source','fill-task','exam_recognition','succeeded','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,confirmation_status
               ) VALUES('fill-question','fill-task','fill-source',0,'任意','任意','任意多空题',
                 'fill_blank',?,'[1]',1,'[]','confirmed')""",
            (str(blank_count),),
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES(
                 'fill-match','fill-task','fill-question','manual','confirmed','已配置',?
               )""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,source,revision,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES('frame-v1','fill-task',1,'confirmed','teacher',1,'frame-hash',
                 'teacher',?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id='frame-v1' WHERE id='fill-task'"
        )
        connection.execute(
            """INSERT INTO question_frame_items(
                 id,frame_set_id,question_id,status,issues_json,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES('frame-item','frame-v1','fill-question','confirmed','[]',?,?,?,
                 'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('fill-question','fill_blank',?,1,?)""",
            (f"{blank_count}.00", timestamp),
        )
        connection.execute(
            """INSERT INTO question_blank_config_versions(
                 id,question_id,version_number,frame_set_id,status,source,signals_json,
                 blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES('blank-config-v1','fill-question',1,'frame-v1','teacher_confirmed',
                 'teacher','{}','[]','[]','hash','teacher',?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE question_grading_configs
               SET current_blank_config_version_id='blank-config-v1'
               WHERE question_id='fill-question'"""
        )
        for index in range(1, blank_count + 1):
            key = f"B{index}"
            connection.execute(
                """INSERT INTO question_blank_definition_versions(
                     id,blank_config_version_id,blank_key,sort_order,max_score,answer_kind,
                     standard_answers_json,synonyms_json,template_page_id,page_number,
                     coordinate_space,x,y,width,height,anchor_source,anchor_confidence,
                     anchor_issues_json,anchor_json,created_at,updated_at
                   ) VALUES(?,?,?,?,'1.00','text',?,'[]','template-page',1,
                     'template_page_normalized',?,?,0.08,0.04,'teacher',1,'[]','{}',?,?)""",
                (
                    f"definition-{key}",
                    "blank-config-v1",
                    key,
                    index - 1,
                    json_dumps([f"answer-{key}"]),
                    0.1,
                    0.1 + index * 0.08,
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES('fill-submission','fill-task','Student','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,created_at,updated_at,finished_at
               ) VALUES('processing-v1','fill-submission',1,'frame-v1','ready','input-hash',1,
                 'system','[]',?,?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='processing-v1'
               WHERE id='fill-submission'"""
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES('fill-page','fill-submission',1,'student-page.png',1000,1400,'sha',
                 'aligned',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 blank_config_version_id,question_number,recognized_text,confidence,
                 raw_recognition_json,status,created_at,updated_at
               ) VALUES('fill-response','fill-submission','fill-question','processing-v1',
                 'frame-v1','blank-config-v1','任意','SUMMARY_MUST_NOT_BE_GRADED',0.99,?,
                 'recognized',?,?)""",
            (
                json_dumps(
                    {
                        "segments": [
                            {
                                "region_index": 1,
                                "transcription": "SEGMENT_MUST_NOT_BE_SPLIT",
                            }
                        ]
                    }
                ),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES('shared-evidence','fill-response',0,'fill-page',?,?,?)""",
            (
                json_dumps({"x": 10, "y": 20, "width": 30, "height": 40}),
                json_dumps({"x": 11, "y": 21, "width": 30, "height": 40}),
                timestamp,
            ),
        )
        for index in reversed(range(1, blank_count + 1)):
            key = f"B{index}"
            connection.execute(
                """INSERT INTO student_blank_responses(
                     id,student_response_id,blank_definition_id,blank_key,recognized_text,
                     is_blank,confidence,status,issues_json,evidence_refs_json,
                     recognition_model_id,prompt_version,frame_set_id,blank_config_version_id,
                     processing_revision_id,raw_item_json,created_at,updated_at
                   ) VALUES(?,?,?,? ,?,0,0.99,'recognized','[]','[\"shared-evidence\"]',
                     'model','keyed-fill-response-v2','frame-v1','blank-config-v1',
                     'processing-v1','{}',?,?)""",
                (
                    f"blank-response-{key}",
                    "fill-response",
                    f"definition-{key}",
                    key,
                    f"student-{key}",
                    timestamp,
                    timestamp,
                ),
            )


@pytest.mark.parametrize("blank_count", [1, 2, 3, 5])
def test_fill_input_reads_exact_runtime_keys_from_current_version(
    tmp_path: Path,
    blank_count: int,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_keyed_fill_submission(database, blank_count)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    [grading_input] = pipeline._build_inputs("fill-submission", "grading-run")
    blanks = grading_input.grading_config["blanks"]

    assert [item["blankKey"] for item in blanks] == [
        f"B{index}" for index in range(1, blank_count + 1)
    ]
    assert [item["studentAnswer"] for item in blanks] == [
        f"student-B{index}" for index in range(1, blank_count + 1)
    ]
    assert all(item["evidenceRegionIds"] == ["shared-evidence"] for item in blanks)
    assert "SUMMARY_MUST_NOT_BE_GRADED" not in json_dumps(blanks)
    assert "SEGMENT_MUST_NOT_BE_SPLIT" not in json_dumps(blanks)
    assert grading_input.frame_set_id == "frame-v1"
    assert grading_input.blank_config_version_id == "blank-config-v1"
    assert grading_input.processing_revision_id == "processing-v1"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            "DELETE FROM student_blank_responses WHERE blank_key='B2'",
            "FILL_RESPONSE_KEY_MISMATCH",
        ),
        (
            "UPDATE student_blank_responses SET blank_key='B9' WHERE blank_key='B2'",
            "FILL_RESPONSE_KEY_MISMATCH",
        ),
        (
            "UPDATE student_blank_responses SET processing_revision_id=NULL WHERE blank_key='B2'",
            "FILL_RESPONSE_VERSION_MISMATCH",
        ),
    ],
)
def test_fill_input_fails_closed_for_key_status_or_version_mismatch(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_keyed_fill_submission(database, 3)
    database.execute(mutation)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    with pytest.raises(AppError) as captured:
        pipeline._build_inputs("fill-submission", "grading-run")

        assert captured.value.code == expected_code


def test_fill_input_grades_low_confidence_blank_and_marks_it_for_review(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_keyed_fill_submission(database, 3)
    database.execute(
        "UPDATE student_blank_responses SET status='needs_review' WHERE blank_key='B2'"
    )
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    grading_input = pipeline._build_inputs("fill-submission", "run-review")[0]

    assert grading_input.recognition_requires_review is True


@pytest.mark.asyncio
async def test_pipeline_scores_multiple_choice_partial_credit_and_is_resumable(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    Image.new("RGB", (1000, 1400), "white").save(settings.data_dir / "student-page.png")
    seed_multiple_choice_submission(database)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    run_id = pipeline.create_run("submission")
    await pipeline.run(run_id)

    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert run is not None
    assert run["status"] == "completed"
    assert run["total_score"] == "4.00"
    assert run["max_score"] == "6.00"
    assert run["progress_current"] == 1
    result = database.fetchone(
        "SELECT * FROM grading_question_results WHERE grading_run_id=?", (run_id,)
    )
    assert result is not None
    assert result["status"] == "final"
    assert Decimal(result["raw_score"]) == Decimal("4")
    assert result["final_score"] == "4.00"
    observation = json_loads(result["tool_observations_json"], [])[0]
    assert observation["payload"]["rawRatio"] == "2/3"
    artifacts = database.fetchall(
        "SELECT * FROM grading_artifacts WHERE grading_run_id=? AND status='current'",
        (run_id,),
    )
    assert {item["artifact_type"] for item in artifacts} == {
        "annotation",
        "error_report",
    }

    await pipeline.run(run_id)
    rows = database.fetchall(
        "SELECT * FROM grading_question_results WHERE grading_run_id=?", (run_id,)
    )
    assert len(rows) == 1
    assert rows[0]["result_revision"] == 1


@pytest.mark.asyncio
async def test_error_analysis_failure_is_retryable_and_recovers_without_fallback(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    Image.new("RGB", (1000, 1400), "white").save(settings.data_dir / "student-page.png")
    seed_multiple_choice_submission(database)
    model = RecoveringErrorAnalysisModel()
    model.available = True
    pipeline = GradingPipeline(settings, database, cast(DashScopeClient, model))

    run_id = pipeline.create_run("submission")
    await pipeline.run(run_id)

    initial = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert initial is not None
    assert initial["status"] == "completed"
    model.available = False
    await pipeline.generate_artifacts(run_id)

    failed = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["retryable"] == 1
    assert failed["error_code"] == "ERROR_ANALYSIS_MODEL_FAILED"
    assert failed["last_successful_stage"] == "generating_annotation"
    current_types = {
        row["artifact_type"]
        for row in database.fetchall(
            """SELECT artifact_type FROM grading_artifacts
               WHERE grading_run_id=? AND status='current'""",
            (run_id,),
        )
    }
    assert current_types == {"annotation"}

    model.available = True
    await pipeline.generate_artifacts(run_id)

    completed = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["retryable"] == 0
    assert completed["error_code"] is None
    current_types = {
        row["artifact_type"]
        for row in database.fetchall(
            """SELECT artifact_type FROM grading_artifacts
               WHERE grading_run_id=? AND status='current'""",
            (run_id,),
        )
    }
    assert current_types == {"annotation", "error_report"}
    assert model.analysis_calls == 3


@pytest.mark.asyncio
async def test_full_score_pipeline_skips_error_analysis_model(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    Image.new("RGB", (1000, 1400), "white").save(settings.data_dir / "student-page.png")
    seed_multiple_choice_submission(database, recognized_answer="ACD")
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, NoModelCallClient()),
    )

    run_id = pipeline.create_run("submission")
    await pipeline.run(run_id)

    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert run is not None
    assert run["status"] == "completed"
    report = database.fetchone(
        """SELECT preview_json FROM grading_artifacts
           WHERE grading_run_id=? AND artifact_type='error_report' AND status='current'""",
        (run_id,),
    )
    assert report is not None
    assert json_loads(report["preview_json"], {})["questions"] == []


@pytest.mark.asyncio
async def test_invalid_error_analysis_does_not_generate_fallback_report(tmp_path: Path) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    Image.new("RGB", (1000, 1400), "white").save(settings.data_dir / "student-page.png")
    seed_multiple_choice_submission(database)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, InvalidErrorAnalysisModel()),
    )

    run_id = pipeline.create_run("submission")
    await pipeline.run(run_id)

    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert run is not None
    assert run["status"] == "failed"
    assert run["retryable"] == 1
    assert run["error_code"] == "ERROR_ANALYSIS_INVALID_OUTPUT"
    current_types = {
        row["artifact_type"]
        for row in database.fetchall(
            """SELECT artifact_type FROM grading_artifacts
               WHERE grading_run_id=? AND status='current'""",
            (run_id,),
        )
    }
    assert current_types == {"annotation"}


@pytest.mark.asyncio
async def test_pipeline_routes_low_recognition_confidence_to_teacher_review(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_multiple_choice_submission(database, confidence=0.4)
    pipeline = GradingPipeline(
        settings,
        database,
        cast(DashScopeClient, UnexpectedModelClient()),
    )

    run_id = pipeline.create_run("submission")
    await pipeline.run(run_id)

    run = database.fetchone("SELECT * FROM grading_runs WHERE id=?", (run_id,))
    assert run is not None
    assert run["status"] == "needs_review"
    assert run["open_review_count"] == 1
    assert run["total_score"] == "4.00"
    review = database.fetchone(
        "SELECT * FROM grading_review_items WHERE grading_run_id=?", (run_id,)
    )
    assert review is not None
    assert review["reason"] == "LOW_RECOGNITION_CONFIDENCE"
