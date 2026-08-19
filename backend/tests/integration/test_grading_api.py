from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from homework_judge.db.database import Database, json_dumps, now_iso
from homework_judge.main import app
from homework_judge.recognition.client import ModelResponse
from homework_judge.schemas import GradingBlankCorrection


class RubricModelStub:
    settings = SimpleNamespace(dashscope_model="rubric-model-stub")

    async def chat(self, **_kwargs) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {
                    "points": [
                        {
                            "pointKey": "P1",
                            "criterion": "写出所用公式",
                            "score": "2.00",
                            "sortOrder": 0,
                            "dependencies": [],
                        },
                        {
                            "pointKey": "P2",
                            "criterion": "代入并计算结果",
                            "score": "4.00",
                            "sortOrder": 1,
                            "dependencies": ["P1"],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            raw={},
            usage={"promptTokens": 10, "completionTokens": 20, "totalTokens": 30},
        )


class KeyedFillCorrectionModelStub:
    settings = SimpleNamespace(dashscope_model="fill-correction-model-stub")

    def __init__(self, decision: str = "correct") -> None:
        self.decision = decision
        self.blank_keys: list[str] = []

    async def chat(self, **kwargs) -> ModelResponse:
        payload = json.loads(kwargs["user_content"][0]["text"])
        blank_key = str(payload["blankKey"])
        self.blank_keys.append(blank_key)
        evidence = list(payload.get("availableEvidenceRegionIds", []))[:1]
        return ModelResponse(
            content=json.dumps(
                {
                    "blankKey": blank_key,
                    "decision": self.decision,
                    "reason": "仅对指定 blankKey 重新判定",
                    "evidenceRegionIds": evidence,
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            ),
            raw={},
            usage={"promptTokens": 3, "completionTokens": 2, "totalTokens": 5},
        )


class ErrorAnalysisModelStub:
    settings = SimpleNamespace(dashscope_model="error-analysis-model-stub")

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs) -> ModelResponse:
        self.calls.append(kwargs)
        payload = json.loads(kwargs["user_content"][0]["text"])
        return ModelResponse(
            content=json.dumps(
                {
                    "summary": "本次错题反映出逐项核对不完整，应优先建立检查清单。",
                    "questions": [
                        {
                            "questionId": item["questionId"],
                            "errorCategory": "incomplete_answer",
                            "errorReason": "学生作答只覆盖了部分正确要求，未完成全部核对。",
                            "knowledgeGap": "完整读取条件并逐项验证答案的能力",
                            "masteredParts": ["已经识别并完成了部分正确内容"],
                            "suggestion": "逐条圈出题目条件，提交前按条件清单检查是否漏答。",
                        }
                        for item in payload["questions"]
                    ],
                },
                ensure_ascii=False,
            ),
            raw={},
            usage={"totalTokens": 10},
        )


def install_error_analysis_model() -> ErrorAnalysisModelStub:
    model = ErrorAnalysisModelStub()
    app.state.grading_pipeline.artifact_service.model_client = model
    return model


def seed_calculation_question(database: Database) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','T','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,confirmation_status
               ) VALUES('question','task','source',1,'1','1','计算电场强度','calculation',6,
                 '[]',1,'[]','confirmed')"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,teacher_explanation,
                 updated_at
               ) VALUES(
                 'match','task','question','manual','confirmed','E=F/q','先列公式再计算',?
               )""",
            (timestamp,),
        )


def seed_multi_blank_question(database: Database) -> None:
    seed_calculation_question(database)
    regions = [
        {
            "page_number": 2,
            "x": 100,
            "y": 200,
            "width": 500,
            "height": 120,
        }
    ]
    with database.transaction() as connection:
        connection.execute(
            """UPDATE questions
               SET stem=?,question_type='fill_blank',score=4,answer_regions_json=?
               WHERE id='question'""",
            (
                "物体因______电子而带正电，同种电荷相互______，异种电荷相互______。",
                json_dumps(regions),
            ),
        )
        connection.execute("UPDATE matches SET teacher_answer='失去 异种 吸引' WHERE id='match'")


def seed_confirmed_question_frame(
    database: Database,
    *,
    task_id: str,
    question_id: str,
) -> tuple[str, str]:
    timestamp = now_iso()
    document_id = f"{task_id}-exam"
    page_id = f"{task_id}-template-page"
    frame_set_id = f"{task_id}-frame-v1"
    item_id = f"{question_id}-frame-item"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,
                 size_bytes,sha256,relative_path,created_at
               ) VALUES(?,?, 'exam','exam.pdf','exam.pdf','application/pdf','.pdf',1,?,
                 'exam.pdf',?)""",
            (document_id, task_id, f"{task_id}-exam-sha", timestamp),
        )
        connection.execute(
            """INSERT INTO pages(
                 id,document_id,page_number,image_path,width,height,sha256
               ) VALUES(?,?,1,'template.jpg',1000,1400,?)""",
            (page_id, document_id, f"{task_id}-page-sha"),
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,revision,source,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES(?,?,1,'confirmed',1,'teacher',?,'teacher',?,?,?,'teacher')""",
            (frame_set_id, task_id, f"{task_id}-frame-hash", timestamp, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id=? WHERE id=?",
            (frame_set_id, task_id),
        )
        connection.execute(
            """INSERT INTO question_frame_items(
                 id,frame_set_id,question_id,status,revision,issues_json,confirmed_at,
                 confirmed_by,created_at,updated_at
               ) VALUES(?,?,?,'confirmed',1,'[]',?,'teacher',?,?)""",
            (item_id, frame_set_id, question_id, timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_frame_regions(
                 id,frame_item_id,region_key,template_page_id,page_number,x,y,width,height,
                 sort_order,source,confidence,issues_json,created_at,updated_at
               ) VALUES(?,?, 'Q1',?,1,0.04,0.04,0.92,0.65,0,'teacher',1,'[]',?,?)""",
            (f"{question_id}-frame-region", item_id, page_id, timestamp, timestamp),
        )
    return frame_set_id, page_id


def blank_config_payload(
    frame_set_id: str,
    page_id: str,
    *,
    expected_version: int,
    confirm: bool,
) -> dict[str, object]:
    answers = ("失去", "异种", "吸引")
    scores = ("1.00", "1.00", "2.00")
    return {
        "questionType": "fill_blank",
        "maxScore": "4.00",
        "frameSetId": frame_set_id,
        "expectedConfigVersion": expected_version,
        "confirm": confirm,
        "blanks": [
            {
                "blankKey": f"B{index + 1}",
                "sortOrder": index,
                "maxScore": score,
                "answerKind": "text",
                "standardAnswers": [answer],
                "synonyms": [],
                "anchor": {
                    "templatePageId": page_id,
                    "pageNumber": 1,
                    "coordinateSpace": "template_page_normalized",
                    "box": {
                        "x": 0.15,
                        "y": 0.12 + index * 0.13,
                        "width": 0.35,
                        "height": 0.07,
                    },
                    "source": "teacher",
                    "confidence": 1,
                    "issues": [],
                },
            }
            for index, (answer, score) in enumerate(zip(answers, scores, strict=True))
        ],
    }


def seed_choice_submission(database: Database, *, confidence: float = 0.99) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('choice-task','Choice','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('choice-source','choice-task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES(
                 'choice-question','choice-task','choice-source',0,'1','1','Choose',
                 'multiple_choice',6,'[1]',1,'[]','confirmed'
               )"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES(
                 'choice-match','choice-task','choice-question','manual','confirmed','ACD',?
               )""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES(
                 'choice-submission','choice-task','Student','ready','ready',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'choice-page','choice-submission',1,'student.png',1000,1400,'sha',
                 'aligned',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,recognized_text,confidence,
                 raw_recognition_json,status,created_at,updated_at
               ) VALUES(
                 'choice-response','choice-submission','choice-question','1','AC',?,?,
                 'recognized',?,?
               )""",
            (
                confidence,
                json_dumps(
                    {
                        "isBlank": False,
                        "issues": [],
                        "segments": [{"region_index": 1, "transcription": "AC"}],
                    }
                ),
                timestamp,
                timestamp,
            ),
        )
        box = json_dumps({"x": 100, "y": 200, "width": 300, "height": 100})
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES(
                 'choice-region','choice-response',0,'choice-page',?,?,?
               )""",
            (box, box, timestamp),
        )


def seed_unconfigured_fill_submission(
    database: Database,
    *,
    answer: str = "失去 异种 吸引",
) -> None:
    seed_choice_submission(database)
    recognized = "失去\n异种\n吸引"
    with database.transaction() as connection:
        connection.execute(
            """UPDATE questions
               SET stem='甲______乙______丙______',question_type='fill_blank',score=4,
                   answer_regions_json=? WHERE id='choice-question'""",
            (
                json_dumps(
                    [
                        {
                            "page_number": 1,
                            "x": 100,
                            "y": 200,
                            "width": 300,
                            "height": 100,
                        }
                    ]
                ),
            ),
        )
        connection.execute(
            "UPDATE matches SET teacher_answer=? WHERE id='choice-match'",
            (answer,),
        )
        connection.execute(
            """UPDATE student_responses
               SET recognized_text=?,raw_recognition_json=? WHERE id='choice-response'""",
            (
                recognized,
                json_dumps(
                    {
                        "isBlank": False,
                        "issues": [],
                        "segments": [{"region_index": 1, "transcription": recognized}],
                    }
                ),
            ),
        )


def seed_shared_fill_review_api(
    database: Database,
    *,
    recognized_text: str = "失去\n异种\n吸引",
) -> None:
    timestamp = now_iso()
    evidence = {
        "page_id": "fill-page",
        "region_id": "fill-region",
        "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 100},
        "cropped_image_path": None,
        "recognized_text": recognized_text,
        "char_or_step_range": None,
    }
    blank_specs = [
        ("B1", "1.00", "失去", ["fill-region"]),
        ("B2", "1.00", "异种", []),
        ("B3", "2.00", "吸引", []),
    ]
    config = {
        "blanks": [
            {
                "blankKey": key,
                "maxScore": score,
                "answerKind": "text",
                "standardAnswers": [answer],
                "synonyms": [],
                "studentAnswer": answer,
                "evidenceRegionIds": region_ids,
            }
            for key, score, answer, region_ids in blank_specs
        ]
    }
    decisions = [
        {
            "key": key,
            "status": "correct",
            "score": score,
            "max_score": score,
            "reason": "规范化后与标准答案完全一致",
            "evidence_refs": [evidence] if key == "B1" else [],
            "blocked_by": None,
        }
        for key, score, _answer, _region_ids in blank_specs
    ]
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('fill-task','Fill','completed',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('fill-source','fill-task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES(
                 'fill-question','fill-task','fill-source',8,'9','9','Fill','fill_blank',4,
                 '[1]',1,'[]','confirmed'
               )"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_name,status,question_region_status,created_at,updated_at
               ) VALUES(
                 'fill-submission','fill-task','Student','ready','ready',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'fill-page','fill-submission',1,'student.png',1000,1400,'fill-sha',
                 'aligned',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,recognized_text,status,
                 created_at,updated_at
               ) VALUES(
                 'fill-response','fill-submission','fill-question','9',?,'recognized',?,?
               )""",
            (recognized_text, timestamp, timestamp),
        )
        box = json_dumps(evidence["original_bbox"])
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES(
                 'fill-region','fill-response',0,'fill-page',?,?,?
               )""",
            (box, box, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,max_score,total_score,
                 progress_total,progress_current,open_review_count,created_at,updated_at
               ) VALUES(
                 'fill-grading','fill-submission','fill-task','needs_review','needs_review',
                 'fill-hash','4.00','4.00',1,1,2,?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_question_results(
                 id,grading_run_id,question_id,student_response_id,input_hash,
                 question_type,status,raw_score,final_score,max_score,
                 answer_snapshot_json,grading_config_snapshot_json,decisions_json,
                 evidence_refs_json,error_locations_json,tool_observations_json,
                 review_reasons_json,created_at,updated_at
               ) VALUES(
                 'fill-result','fill-grading','fill-question','fill-response','fill-result-hash',
                 'fill_blank','needs_review','4.00','4.00','4.00','{}',?,?,?,?,?,?,?,?
               )""",
            (
                json_dumps(config),
                json_dumps(decisions),
                json_dumps([evidence]),
                "[]",
                "[]",
                json_dumps(["MISSING_EVIDENCE"]),
                timestamp,
                timestamp,
            ),
        )
        for index, (key, score, answer, _region_ids) in enumerate(blank_specs, start=1):
            decision = next(item for item in decisions if item["key"] == key)
            connection.execute(
                """INSERT INTO grading_blank_results(
                     id,grading_question_result_id,blank_key,status,recognized_answer,
                     score,max_score,exact_match_json,final_decision_json,
                     evidence_refs_json,review_reasons_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"fill-blank-{index}",
                    "fill-result",
                    key,
                    "correct",
                    answer,
                    score,
                    score,
                    "{}",
                    json_dumps(decision),
                    json_dumps(decision["evidence_refs"]),
                    json_dumps(["MISSING_EVIDENCE"]),
                    timestamp,
                    timestamp,
                ),
            )
        for review_id, reason in (
            ("fill-review", "MISSING_EVIDENCE"),
            ("fill-review-remaining", "LOW_RECOGNITION_CONFIDENCE"),
        ):
            connection.execute(
                """INSERT INTO grading_review_items(
                     id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
                   ) VALUES(?, 'fill-grading','fill-result',?,?,?)""",
                (review_id, reason, timestamp, timestamp),
            )


def seed_versioned_fill_correction_api(database: Database) -> dict[str, object]:
    seed_shared_fill_review_api(database)
    frame_set_id, template_page_id = seed_confirmed_question_frame(
        database,
        task_id="fill-task",
        question_id="fill-question",
    )
    timestamp = now_iso()
    config_version_id = "fill-config-v1"
    processing_revision_id = "fill-processing-v1"
    answers = ("失去", "异种", "吸引")
    scores = ("1.00", "1.00", "2.00")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO question_blank_config_versions(
                 id,question_id,version_number,frame_set_id,status,source,signals_json,
                 blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES(?,'fill-question',1,?,'teacher_confirmed','teacher','{}','[]','[]',
                 'fill-config-hash','teacher',?,?,?,'teacher')""",
            (config_version_id, frame_set_id, timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,
                 current_blank_config_version_id,updated_at
               ) VALUES('fill-question','fill_blank','4.00',1,?,?)""",
            (config_version_id, timestamp),
        )
        for index, (answer, score) in enumerate(zip(answers, scores, strict=True), start=1):
            key = f"B{index}"
            connection.execute(
                """INSERT INTO question_blank_definition_versions(
                     id,blank_config_version_id,blank_key,sort_order,max_score,answer_kind,
                     standard_answers_json,synonyms_json,template_page_id,page_number,
                     coordinate_space,x,y,width,height,anchor_source,anchor_confidence,
                     anchor_issues_json,anchor_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'text',?,'[]',?,1,'template_page_normalized',
                     0.1,?,0.3,0.05,'teacher',1,'[]','{}',?,?)""",
                (
                    f"fill-definition-{key}",
                    config_version_id,
                    key,
                    index - 1,
                    score,
                    json_dumps([answer]),
                    template_page_id,
                    0.1 + index * 0.1,
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,finished_at,created_at,updated_at
               ) VALUES(?,'fill-submission',1,?,'ready','fill-processing-hash',1,
                 'system','[]',?,?,?)""",
            (processing_revision_id, frame_set_id, timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id=?
               WHERE id='fill-submission'""",
            (processing_revision_id,),
        )
        connection.execute(
            """UPDATE student_responses SET processing_revision_id=?,frame_set_id=?,
               blank_config_version_id=? WHERE id='fill-response'""",
            (processing_revision_id, frame_set_id, config_version_id),
        )
        connection.execute(
            """INSERT INTO student_page_alignment_revisions(
                 id,processing_revision_id,student_page_id,revision_number,template_page_id,
                 transform_json,quality,method,status,control_points_json,metrics_json,source,
                 is_current,issues_json,created_by,created_at,updated_at
               ) VALUES('fill-alignment-v1',?,'fill-page',1,?,
                 '[[1,0,0],[0,1,0],[0,0,1]]',1,'seed','aligned','[]','{}','model',1,
                 '[]','test',?,?)""",
            (processing_revision_id, template_page_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_question_regions(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 frame_region_id,alignment_revision_id,sort_order,template_page_id,
                 student_page_id,template_region_json,student_polygon_json,student_bbox_json,
                 status,issues_json,created_at,updated_at
               ) VALUES('fill-mapped-frame','fill-submission','fill-question',?,?,
                 'fill-question-frame-region','fill-alignment-v1',0,?,'fill-page',?,?,?,
                 'ready','[]',?,?)""",
            (
                processing_revision_id,
                frame_set_id,
                template_page_id,
                json_dumps(
                    {
                        "coordinateSpace": "template_page_normalized",
                        "x": 0.04,
                        "y": 0.04,
                        "width": 0.92,
                        "height": 0.65,
                    }
                ),
                json_dumps(
                    [
                        {"x": 40.0, "y": 56.0},
                        {"x": 960.0, "y": 56.0},
                        {"x": 960.0, "y": 966.0},
                        {"x": 40.0, "y": 966.0},
                    ]
                ),
                json_dumps({"x": 40.0, "y": 56.0, "width": 920.0, "height": 910.0}),
                timestamp,
                timestamp,
            ),
        )
        for index, answer in enumerate(answers, start=1):
            key = f"B{index}"
            connection.execute(
                """INSERT INTO student_blank_responses(
                     id,student_response_id,blank_definition_id,blank_key,recognized_text,
                     is_blank,confidence,status,issues_json,evidence_refs_json,
                     recognition_model_id,prompt_version,frame_set_id,blank_config_version_id,
                     processing_revision_id,raw_item_json,created_at,updated_at
                   ) VALUES(?, 'fill-response',?,?,?,0,0.99,'recognized','[]',?,
                     'recognition-model','keyed-fill-response-v2',?,?,?,'{}',?,?)""",
                (
                    f"fill-student-blank-{key}",
                    f"fill-definition-{key}",
                    key,
                    answer,
                    json_dumps(["fill-region"] if key == "B1" else []),
                    frame_set_id,
                    config_version_id,
                    processing_revision_id,
                    timestamp,
                    timestamp,
                ),
            )
        snapshot = {
            "submissionId": "fill-submission",
            "questions": [
                {
                    "questionId": "fill-question",
                    "frameSetId": frame_set_id,
                    "blankConfigVersionId": config_version_id,
                    "processingRevisionId": processing_revision_id,
                }
            ],
        }
        connection.execute(
            """UPDATE grading_runs SET input_snapshot_json=?,result_revision=4
               WHERE id='fill-grading'""",
            (json_dumps(snapshot),),
        )
        connection.execute(
            "UPDATE grading_question_results SET result_revision=7 WHERE id='fill-result'"
        )
        for artifact_type in ("annotation", "error_report"):
            connection.execute(
                """INSERT INTO grading_artifacts(
                     id,grading_run_id,artifact_type,result_revision,status,preview_json,
                     created_at,updated_at
                   ) VALUES(?, 'fill-grading',?,4,'current','{}',?,?)""",
                (f"fill-{artifact_type}", artifact_type, timestamp, timestamp),
            )
    return {
        "frameSetId": frame_set_id,
        "blankConfigVersionId": config_version_id,
        "processingRevisionId": processing_revision_id,
        "gradingRevision": 7,
    }


def wait_for_grading_status(
    client: TestClient,
    run_id: str,
    expected: str,
) -> dict[str, object]:
    data: dict[str, object] = {}
    for _attempt in range(60):
        response = client.get(f"/api/grading-runs/{run_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        if data["status"] == expected:
            return data
        time.sleep(0.02)
    raise AssertionError(f"grading run did not reach {expected}: {data}")


def test_grading_config_and_rubric_version_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_calculation_question(database)
        app.state.model_client = RubricModelStub()

        configured = client.put(
            "/api/questions/question/grading-config",
            json={"questionType": "calculation", "maxScore": "6.00", "blanks": []},
        )
        assert configured.status_code == 200
        assert configured.json()["data"]["maxScore"] == "6.00"

        draft = client.post("/api/questions/question/rubric-drafts")
        assert draft.status_code == 201
        rubric_id = draft.json()["data"]["id"]
        draft_points = draft.json()["data"]["points"]
        assert draft_points[1]["dependencies"] == ["P1"]
        final_point = next(
            point for point in draft_points if point["pointKey"] == "FINAL_ANSWER"
        )
        assert final_point["score"] == "1.20"
        assert final_point["dependencies"] == []

        missing_final = client.put(
            f"/api/rubric-versions/{rubric_id}",
            json={
                "maxScore": "6.00",
                "points": [
                    {
                        "pointKey": "P1",
                        "criterion": "method",
                        "score": "2.00",
                        "sortOrder": 0,
                        "dependencies": [],
                    },
                    {
                        "pointKey": "P2",
                        "criterion": "calculation",
                        "score": "4.00",
                        "sortOrder": 1,
                        "dependencies": [],
                    },
                ],
            },
        )
        assert missing_final.status_code == 422
        assert missing_final.json()["error"]["code"] == "RUBRIC_INVALID"

        invalid = client.put(
            f"/api/rubric-versions/{rubric_id}",
            json={
                "maxScore": "6.00",
                "points": [
                    {
                        "pointKey": "P1",
                        "criterion": "one",
                        "score": "2.00",
                        "sortOrder": 0,
                        "dependencies": ["P2"],
                    },
                    {
                        "pointKey": "P2",
                        "criterion": "two",
                        "score": "4.00",
                        "sortOrder": 1,
                        "dependencies": ["P1"],
                    },
                ],
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "RUBRIC_INVALID"

        frozen = client.post(f"/api/rubric-versions/{rubric_id}/freeze")
        assert frozen.status_code == 200
        assert frozen.json()["data"]["status"] == "frozen"
        assert frozen.json()["data"]["contentHash"]
        assert frozen.json()["data"]["isCurrent"] is True

        with database.transaction() as connection:
            connection.execute(
                "UPDATE questions SET confirmation_status='confirmed' WHERE id='question'"
            )
        unchanged = client.put(
            "/api/questions/question/grading-config",
            json={
                "questionType": "calculation",
                "maxScore": "6.00",
                "expectedConfigVersion": 1,
                "blanks": [],
            },
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["data"]["configVersion"] == 1
        assert database.fetchone(
            "SELECT confirmation_status FROM questions WHERE id='question'"
        ) == {"confirmation_status": "confirmed"}
        assert database.fetchone(
            """SELECT COUNT(*) AS count FROM audit_events
               WHERE event_type='grading_config_updated'"""
        ) == {"count": 1}
        listed = client.get("/api/questions/question/rubric-versions").json()["data"]
        assert listed[0]["isCurrent"] is True

        database.execute(
            "UPDATE rubric_versions SET frozen_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (rubric_id,),
        )
        stale = client.get("/api/questions/question/rubric-versions").json()["data"]
        assert stale[0]["isCurrent"] is False
        reconfirmed = client.post(f"/api/rubric-versions/{rubric_id}/freeze")
        assert reconfirmed.status_code == 200, reconfirmed.text
        assert reconfirmed.json()["data"]["isCurrent"] is True
        assert database.fetchone(
            """SELECT COUNT(*) AS count FROM audit_events
               WHERE event_type='rubric_reconfirmed'"""
        ) == {"count": 1}

        blocked = client.put(
            f"/api/rubric-versions/{rubric_id}",
            json={"maxScore": "6.00", "points": draft.json()["data"]["points"]},
        )
        assert blocked.status_code == 409


def test_fill_config_requires_independent_scores_to_add_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        seed_calculation_question(app.state.database)
        response = client.put(
            "/api/questions/question/grading-config",
            json={
                "questionType": "fill_blank",
                "maxScore": "4.00",
                "blanks": [
                    {
                        "blankKey": "B1",
                        "sortOrder": 0,
                        "maxScore": "1.00",
                        "answerKind": "text",
                        "standardAnswers": ["电场"],
                    }
                ],
            },
        )
        assert response.status_code == 422


def test_fill_config_derives_multiple_blanks_read_only_and_saved_values_win(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_multi_blank_question(database)
        frame_set_id, page_id = seed_confirmed_question_frame(
            database,
            task_id="task",
            question_id="question",
        )

        response = client.get("/api/questions/question/grading-config")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["configVersion"] == 0
        assert data["versionId"] is None
        assert data["status"] is None
        assert data["initialization"]["source"] == "derived"
        assert data["initialization"]["signals"]["selectedCount"] == 3
        assert data["initialization"]["autoConfirmable"] is True
        assert data["initialization"]["blockingReasons"] == []
        assert [blank["maxScore"] for blank in data["blanks"]] == [
            "1.33",
            "1.33",
            "1.34",
        ]
        assert {
            "answer_region_count_conflict",
            "blank_score_auto_allocated",
            "missing_blank_anchor",
        } <= {item["code"] for item in data["initialization"]["warnings"]}
        assert [blank["standardAnswers"] for blank in data["blanks"]] == [
            ["失去"],
            ["异种"],
            ["吸引"],
        ]
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_grading_configs")["count"]
            == 0
        )
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_blank_definitions")["count"]
            == 0
        )
        assert database.fetchone("SELECT COUNT(*) AS count FROM runs")["count"] == 1
        assert client.get("/api/questions/question/grading-config").json()["data"] == data

        payload = blank_config_payload(
            frame_set_id,
            page_id,
            expected_version=0,
            confirm=True,
        )
        for blank in payload["blanks"]:
            blank["anchor"] = None
        payload["blanks"][1]["standardAnswers"] = ["异性"]
        payload["blanks"][1]["synonyms"] = ["不同种"]
        saved = client.put(
            "/api/questions/question/grading-config",
            json=payload,
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["configVersion"] == 1
        assert saved.json()["data"]["status"] == "teacher_confirmed"
        assert saved.json()["data"]["frameSetId"] == frame_set_id
        assert saved.json()["data"]["readiness"]["blockingIssues"] == []
        assert "missing_blank_anchor" in {
            item["code"]
            for item in saved.json()["data"]["readiness"]["advisoryIssues"]
        }
        assert saved.json()["data"]["initialization"]["source"] == "saved"

        with database.transaction() as connection:
            connection.execute(
                "UPDATE matches SET teacher_answer='后来修改的答案' WHERE id='match'"
            )
        reloaded = client.get("/api/questions/question/grading-config").json()["data"]
        assert reloaded["initialization"]["source"] == "saved"
        assert [blank["standardAnswers"] for blank in reloaded["blanks"]] == [
            ["失去"],
            ["异性"],
            ["吸引"],
        ]
        assert reloaded["blanks"][1]["synonyms"] == ["不同种"]

        stale = client.put("/api/questions/question/grading-config", json=payload)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "BLANK_CONFIG_VERSION_CONFLICT"


def test_fill_config_keeps_ambiguous_answers_out_of_all_blanks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_multi_blank_question(database)
        with database.transaction() as connection:
            connection.execute("UPDATE matches SET teacher_answer='复杂 答案' WHERE id='match'")

        data = client.get("/api/questions/question/grading-config").json()["data"]

        assert len(data["blanks"]) == 3
        assert all(blank["standardAnswers"] == [] for blank in data["blanks"])
        assert "answer_split_ambiguous" in {
            warning["code"] for warning in data["initialization"]["warnings"]
        }
        assert data["initialization"]["autoConfirmable"] is False
        blocking_codes = {
            item["code"] for item in data["initialization"]["blockingReasons"]
        }
        assert {"answer_split_ambiguous", "missing_standard_answer"} <= (
            blocking_codes
        )


def test_confirm_question_auto_confirms_safe_derived_blank_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_multi_blank_question(database)
        frame_set_id, _page_id = seed_confirmed_question_frame(
            database,
            task_id="task",
            question_id="question",
        )
        with database.transaction() as connection:
            connection.execute(
                """UPDATE question_frame_sets
                   SET status='draft',confirmed_at=NULL,confirmed_by=NULL
                   WHERE id=?""",
                (frame_set_id,),
            )

        review = client.get("/api/tasks/task/review").json()["data"]
        assert review["studentUploadGate"]["blankConfigIssues"] == []

        confirmed = client.post("/api/questions/question/confirm")

        assert confirmed.status_code == 200
        assert database.fetchone(
            "SELECT COUNT(*) AS count FROM question_blank_config_versions"
        ) == {"count": 1}
        assert database.fetchone(
            "SELECT status,source FROM question_blank_config_versions"
        ) == {"status": "auto_confirmed", "source": "model"}
        saved = client.get("/api/questions/question/grading-config").json()["data"]
        assert saved["readiness"]["blockingIssues"] == []
        assert database.fetchone(
            """SELECT COUNT(*) AS count FROM audit_events
               WHERE event_type='fill_blank_config_auto_confirmed'"""
        ) == {"count": 1}


def test_teacher_blank_config_distinguishes_confirmed_item_from_unfrozen_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_multi_blank_question(database)
        frame_set_id, page_id = seed_confirmed_question_frame(
            database,
            task_id="task",
            question_id="question",
        )
        with database.transaction() as connection:
            connection.execute(
                """UPDATE question_frame_sets
                   SET status='draft',confirmed_at=NULL,confirmed_by=NULL WHERE id=?""",
                (frame_set_id,),
            )

        response = client.put(
            "/api/questions/question/grading-config",
            json=blank_config_payload(
                frame_set_id,
                page_id,
                expected_version=0,
                confirm=True,
            ),
        )

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "QUESTION_FRAME_SET_NOT_FROZEN"
        assert "冻结整套题框" in error["message"]
        assert error["details"]["frameSetStatus"] == "draft"


def test_confirm_question_accepts_only_teacher_confirmed_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_multi_blank_question(database)
        frame_set_id, page_id = seed_confirmed_question_frame(
            database,
            task_id="task",
            question_id="question",
        )
        draft_payload = blank_config_payload(
            frame_set_id,
            page_id,
            expected_version=0,
            confirm=False,
        )
        draft = client.put("/api/questions/question/grading-config", json=draft_payload)
        assert draft.status_code == 200
        assert draft.json()["data"]["status"] == "pending"

        response = client.post("/api/questions/question/confirm")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "FILL_BLANK_CONFIG_REVIEW_REQUIRED"
        blocker = response.json()["error"]["details"]["questions"][0]
        assert blocker["questionNumber"] == "1"
        assert blocker["expectedBlankCount"] == 3
        assert "blank_config_confirmation_required" in blocker["reasonCodes"]

        confirm_payload = blank_config_payload(
            frame_set_id,
            page_id,
            expected_version=1,
            confirm=True,
        )
        config = client.put("/api/questions/question/grading-config", json=confirm_payload)
        assert config.status_code == 200
        assert config.json()["data"]["status"] == "teacher_confirmed"
        confirmed = client.post("/api/questions/question/confirm")
        assert confirmed.status_code == 200


def test_grading_config_compatibility_for_single_blank_and_non_fill_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_calculation_question(database)

        calculation = client.get("/api/questions/question/grading-config").json()["data"]
        assert calculation["blanks"] == []
        assert calculation["initialization"] == {
            "source": "none",
            "signals": None,
            "warnings": [],
            "autoConfirmable": False,
            "blockingReasons": [],
        }

        with database.transaction() as connection:
            connection.execute(
                """UPDATE questions
                   SET stem='电场方向为______。',question_type='fill_blank',score=2,
                       answer_regions_json='[]'
                   WHERE id='question'"""
            )
            connection.execute("UPDATE matches SET teacher_answer='向右' WHERE id='match'")

        fill = client.get("/api/questions/question/grading-config").json()["data"]
        assert fill["initialization"]["source"] == "derived"
        assert len(fill["blanks"]) == 1
        assert fill["blanks"][0]["blankKey"] == "B1"
        assert fill["blanks"][0]["standardAnswers"] == ["向右"]


def test_non_fill_config_update_invalidates_existing_grading_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_calculation_question(database)
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO student_submissions(
                     id,task_id,student_name,status,question_region_status,created_at,updated_at
                   ) VALUES('config-submission','task','Student','ready','ready',?,?)""",
                (timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO grading_runs(
                     id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
                   ) VALUES('config-run','config-submission','task','completed','completed',
                     'config-run-hash',?,?)""",
                (timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO grading_artifacts(
                     id,grading_run_id,artifact_type,result_revision,status,preview_json,
                     created_at,updated_at
                   ) VALUES('config-artifact','config-run','annotation',0,'current','{}',?,?)""",
                (timestamp, timestamp),
            )

        response = client.put(
            "/api/questions/question/grading-config",
            json={
                "questionType": "calculation",
                "maxScore": "8.00",
                "expectedConfigVersion": 0,
                "blanks": [],
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["configVersion"] == 1
        assert database.fetchone(
            "SELECT confirmation_status FROM questions WHERE id='question'"
        ) == {"confirmation_status": "pending"}
        assert database.fetchone(
            "SELECT status FROM student_submissions WHERE id='config-submission'"
        ) == {"status": "uploaded"}
        assert database.fetchone("SELECT is_stale FROM grading_runs WHERE id='config-run'") == {
            "is_stale": 1
        }
        assert database.fetchone(
            "SELECT status FROM grading_artifacts WHERE id='config-artifact'"
        ) == {"status": "stale"}


def test_standard_answer_edit_supersedes_blank_config_and_grading_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,teacher_answer,updated_at
                   ) VALUES('fill-match','fill-task','fill-question','manual','confirmed',
                     '失去 异种 吸引',?)""",
                (timestamp,),
            )

        response = client.patch(
            "/api/matches/fill-match",
            json={"answer": "得到 排斥 吸引", "explanation": "教师修订"},
        )

        assert response.status_code == 200, response.text
        assert database.fetchone(
            """SELECT confirmation_status FROM questions
               WHERE id='fill-question'"""
        ) == {"confirmation_status": "pending"}
        assert database.fetchone(
            """SELECT config_version,current_blank_config_version_id
               FROM question_grading_configs WHERE question_id='fill-question'"""
        ) == {"config_version": 2, "current_blank_config_version_id": None}
        assert database.fetchone(
            "SELECT status FROM question_blank_config_versions WHERE id=?",
            (str(versions["blankConfigVersionId"]),),
        ) == {"status": "stale"}
        assert database.fetchone(
            """SELECT status,current_processing_revision_id FROM student_submissions
               WHERE id='fill-submission'"""
        ) == {"status": "uploaded", "current_processing_revision_id": None}
        assert database.fetchone(
            "SELECT is_stale FROM grading_runs WHERE id='fill-grading'"
        ) == {"is_stale": 1}
        assert {
            row["status"]
            for row in database.fetchall(
                "SELECT status FROM grading_artifacts WHERE grading_run_id='fill-grading'"
            )
        } == {"stale"}


def test_unchanged_question_and_answer_save_keeps_blank_config_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,teacher_answer,updated_at
                   ) VALUES('fill-match','fill-task','fill-question','manual','confirmed',
                     '失去 异种 吸引',?)""",
                (timestamp,),
            )

        question_response = client.patch(
            "/api/questions/fill-question",
            json={
                "number": "9",
                "stem": "Fill",
                "options": [],
                "type": "fill_blank",
                "score": 4,
            },
        )
        match_response = client.patch(
            "/api/matches/fill-match",
            json={
                "answerEntryId": None,
                "answer": "  失去 异种 吸引  ",
                "explanation": "",
            },
        )

        assert question_response.status_code == 200, question_response.text
        assert question_response.json()["data"]["changed"] is False
        assert match_response.status_code == 200, match_response.text
        assert match_response.json()["data"]["changed"] is False
        assert database.fetchone(
            """SELECT config_version,current_blank_config_version_id
               FROM question_grading_configs WHERE question_id='fill-question'"""
        ) == {
            "config_version": 1,
            "current_blank_config_version_id": versions["blankConfigVersionId"],
        }
        assert database.fetchone(
            "SELECT status FROM question_blank_config_versions WHERE id=?",
            (str(versions["blankConfigVersionId"]),),
        ) == {"status": "teacher_confirmed"}
        assert database.fetchone(
            "SELECT status,current_processing_revision_id FROM student_submissions "
            "WHERE id='fill-submission'"
        ) == {
            "status": "ready",
            "current_processing_revision_id": versions["processingRevisionId"],
        }
        assert database.fetchone(
            "SELECT is_stale FROM grading_runs WHERE id='fill-grading'"
        ) == {"is_stale": 0}


def test_grading_config_get_ignores_legacy_stale_current_pointer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        timestamp = now_iso()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO matches(
                     id,task_id,question_id,method,status,teacher_answer,updated_at
                   ) VALUES('fill-match','fill-task','fill-question','manual','confirmed',
                     '失去 异种 吸引',?)""",
                (timestamp,),
            )
            connection.execute(
                "UPDATE question_blank_config_versions SET status='stale' WHERE id=?",
                (str(versions["blankConfigVersionId"]),),
            )

        response = client.get("/api/questions/fill-question/grading-config")

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["configVersion"] == 1
        assert data["versionId"] is None
        assert data["status"] is None
        assert data["frameSetId"] == versions["frameSetId"]
        assert data["initialization"]["source"] == "derived"


def test_grading_run_api_scores_and_exposes_question_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        analysis_model = install_error_analysis_model()
        seed_choice_submission(app.state.database)
        Image.new("RGB", (1000, 1400), "white").save(app.state.settings.data_dir / "student.png")

        created = client.post("/api/student-submissions/choice-submission/grading-runs")
        assert created.status_code == 202
        run_id = created.json()["data"]["gradingRunId"]
        run = wait_for_grading_status(client, run_id, "completed")
        assert run["totalScore"] == "4.00"

        listed = client.get("/api/student-submissions/choice-submission/grading-runs")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == run_id
        questions = client.get(f"/api/grading-runs/{run_id}/questions")
        assert questions.status_code == 200
        assert questions.json()["data"][0]["finalScore"] == "4.00"
        detail = client.get(f"/api/grading-runs/{run_id}/questions/choice-question")
        assert detail.status_code == 200
        detail_value = detail.json()["data"]
        assert detail_value["toolConclusions"][0]["tool"] == ("multiple_choice_rule")
        assert detail_value["evidence"][0]["pageNumber"] == 1
        evidence_preview = client.get(detail_value["evidence"][0]["previewUrl"])
        assert evidence_preview.status_code == 200
        assert evidence_preview.headers["content-type"] == "image/jpeg"
        assert evidence_preview.content.startswith(b"\xff\xd8")
        missing_evidence = client.get(
            f"/api/grading-question-results/{detail_value['id']}/evidence/not-used/preview"
        )
        assert missing_evidence.status_code == 404
        assert missing_evidence.json()["error"]["code"] == "GRADING_EVIDENCE_NOT_FOUND"
        artifacts = client.get(f"/api/grading-runs/{run_id}/artifacts")
        assert artifacts.status_code == 200
        artifact_items = artifacts.json()["data"]
        assert {item["type"] for item in artifact_items} == {
            "annotation",
            "error_report",
        }
        report_item = next(item for item in artifact_items if item["type"] == "error_report")
        assert report_item["preview"]["questions"][0]["errorCategory"] == "漏答或步骤不完整"
        assert len(analysis_model.calls) == 1
        for item in artifact_items:
            preview = client.get(item["previewUrl"])
            assert preview.status_code == 200
            assert preview.content.startswith(b"%PDF")
            download = client.get(item["downloadUrl"])
            assert download.status_code == 200
            assert "attachment" in download.headers["content-disposition"]


def test_grading_start_auto_confirms_safe_blank_config_before_reprocessing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_unconfigured_fill_submission(database)
        seed_confirmed_question_frame(
            database,
            task_id="choice-task",
            question_id="choice-question",
        )
        Image.new("RGB", (1000, 1400), "white").save(
            app.state.settings.data_dir / "student.png"
        )

        created = client.post("/api/student-submissions/choice-submission/grading-runs")

        assert created.status_code == 409
        error = created.json()["error"]
        assert error["code"] == "BLANK_RECOGNITION_STALE"
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_grading_configs")["count"]
            == 1
        )
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_blank_definitions")["count"]
            == 3
        )
        assert database.fetchone("SELECT COUNT(*) AS count FROM grading_runs") == {"count": 0}
        assert database.fetchone(
            """SELECT COUNT(*) AS count FROM audit_events
               WHERE event_type='fill_blank_config_auto_confirmed'"""
        ) == {"count": 1}


def test_grading_start_blocks_ambiguous_fill_without_creating_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_unconfigured_fill_submission(database, answer="复杂 答案")
        seed_confirmed_question_frame(
            database,
            task_id="choice-task",
            question_id="choice-question",
        )

        response = client.post("/api/student-submissions/choice-submission/grading-runs")

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "FILL_BLANK_CONFIG_REVIEW_REQUIRED"
        assert error["details"]["questions"][0]["questionNumber"] == "1"
        assert set(error["details"]["questions"][0]["reasonCodes"]) == {
            "answer_split_ambiguous",
            "missing_standard_answer",
        }
        assert database.fetchone("SELECT COUNT(*) AS count FROM grading_runs")["count"] == 0
        assert (
            database.fetchone("SELECT COUNT(*) AS count FROM question_grading_configs")["count"]
            == 0
        )


def test_teacher_review_confirmation_and_error_location_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        install_error_analysis_model()
        seed_choice_submission(app.state.database, confidence=0.4)
        Image.new("RGB", (1000, 1400), "white").save(app.state.settings.data_dir / "student.png")
        created = client.post("/api/student-submissions/choice-submission/grading-runs")
        run_id = created.json()["data"]["gradingRunId"]
        wait_for_grading_status(client, run_id, "needs_review")

        reviews = client.get(f"/api/grading-runs/{run_id}/review-items")
        assert reviews.status_code == 200
        review = reviews.json()["data"][0]
        resolved = client.post(
            f"/api/grading-review-items/{review['id']}/resolve",
            json={
                "action": "confirm",
                "teacherReason": "原图可辨认，确认自动判分",
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["data"]["status"] == "final"
        wait_for_grading_status(client, run_id, "completed")

        question = client.get(f"/api/grading-runs/{run_id}/questions").json()["data"][0]
        invalid = client.patch(
            f"/api/grading-question-results/{question['id']}/error-location",
            json={
                "teacherReason": "调整圈选位置",
                "errorLocations": [
                    {
                        "pageId": "choice-page",
                        "regionId": "choice-region",
                        "box": {"x": 950, "y": 200, "width": 100, "height": 50},
                    }
                ],
            },
        )
        assert invalid.status_code == 422

        updated = client.patch(
            f"/api/grading-question-results/{question['id']}/error-location",
            json={
                "teacherReason": "调整圈选位置",
                "errorLocations": [
                    {
                        "pageId": "choice-page",
                        "regionId": "choice-region",
                        "box": {"x": 120, "y": 220, "width": 80, "height": 40},
                    }
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["errorLocations"][0]["original_bbox"]["x"] == 120
        stale_items = client.get(f"/api/grading-runs/{run_id}/artifacts").json()["data"]
        assert stale_items and all(item["status"] == "stale" for item in stale_items)
        assert client.get(stale_items[0]["downloadUrl"]).status_code == 409

        regenerated = client.post(f"/api/grading-runs/{run_id}/regenerate")
        assert regenerated.status_code == 202
        wait_for_grading_status(client, run_id, "completed")
        current_items = [
            item
            for item in client.get(f"/api/grading-runs/{run_id}/artifacts").json()["data"]
            if item["status"] == "current"
        ]
        assert len(current_items) == 2
        assert all(item["resultRevision"] == 2 for item in current_items)


def test_structural_calculation_placeholder_scores_are_hidden_from_grading_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_shared_fill_review_api(database)

        # MISSING_EVIDENCE on other question types remains an ordinary review
        # suggestion and must not be hidden.
        fill_question = client.get("/api/grading-runs/fill-grading/questions").json()["data"][0]
        fill_review = client.get("/api/grading-runs/fill-grading/review-items").json()[
            "data"
        ][0]
        assert fill_question["rawScore"] == "4.00"
        assert fill_question["finalScore"] == "4.00"
        assert fill_review["score"] == "4.00"

        stored = database.fetchone(
            "SELECT decisions_json FROM grading_question_results WHERE id='fill-result'"
        )
        assert stored is not None
        decisions = json.loads(stored["decisions_json"])
        for decision in decisions:
            decision.update(
                {
                    "status": "unable",
                    "score": "0",
                    "reason": "missing evidence",
                    "evidence_refs": [],
                    "blocked_by": None,
                }
            )
        with database.transaction() as connection:
            connection.execute(
                """UPDATE grading_question_results SET question_type='calculation',
                   status='needs_review',raw_score='0',final_score='0.00',decisions_json=?,
                   evidence_refs_json='[]',error_locations_json='[]',
                   review_reasons_json='[\"MISSING_EVIDENCE\"]'
                   WHERE id='fill-result'""",
                (json.dumps(decisions),),
            )
            connection.execute(
                """UPDATE grading_runs SET total_score=NULL,open_review_count=2
                   WHERE id='fill-grading'"""
            )

        run = client.get("/api/grading-runs/fill-grading").json()["data"]
        question = client.get("/api/grading-runs/fill-grading/questions").json()["data"][0]
        detail = client.get(
            "/api/grading-runs/fill-grading/questions/fill-question"
        ).json()["data"]
        reviews = client.get("/api/grading-runs/fill-grading/review-items").json()["data"]
        review_detail = client.get("/api/grading-review-items/fill-review").json()["data"]

        assert run["totalScore"] is None
        assert question["rawScore"] is None
        assert question["finalScore"] is None
        assert detail["rawScore"] is None
        assert detail["finalScore"] is None
        assert {item["status"] for item in detail["decisions"]} == {"unable"}
        assert all(item["score"] is None for item in reviews)
        assert review_detail["score"] is None
        assert review_detail["questionResult"]["rawScore"] is None
        assert review_detail["questionResult"]["finalScore"] is None

        with database.transaction() as connection:
            connection.execute(
                """UPDATE grading_question_results SET decisions_json='[]',
                   review_reasons_json='[\"INVALID_MODEL_OUTPUT\"]',
                   error_code='INVALID_MODEL_OUTPUT' WHERE id='fill-result'"""
            )
            connection.execute(
                """UPDATE grading_review_items SET reason='INVALID_MODEL_OUTPUT'
                   WHERE id='fill-review'"""
            )

        invalid_question = client.get(
            "/api/grading-runs/fill-grading/questions/fill-question"
        ).json()["data"]
        invalid_review = client.get(
            "/api/grading-review-items/fill-review"
        ).json()["data"]
        assert invalid_question["rawScore"] is None
        assert invalid_question["finalScore"] is None
        assert invalid_review["score"] is None

        regenerate = client.post("/api/grading-runs/fill-grading/regenerate")
        assert regenerate.status_code == 409
        assert regenerate.json()["error"]["code"] == "GRADING_REVIEW_REQUIRED"


def test_fill_review_api_shares_composite_region_for_existing_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        database: Database = app.state.database
        seed_shared_fill_review_api(database)

        resolved = client.post(
            "/api/grading-review-items/fill-review/resolve",
            json={
                "action": "confirm",
                "teacherReason": "教师确认三个空位均正确",
            },
        )

        assert resolved.status_code == 200
        assert resolved.json()["data"]["score"] == "4.00"
        assert resolved.json()["data"]["status"] == "needs_review"
        row = database.fetchone(
            "SELECT decisions_json FROM grading_question_results WHERE id='fill-result'"
        )
        assert row is not None
        decisions = json.loads(row["decisions_json"])
        assert [
            [evidence["region_id"] for evidence in decision["evidence_refs"]]
            for decision in decisions
        ] == [["fill-region"], [], []]


def test_fill_review_api_accepts_unmatched_positive_blank_keys_by_teacher_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        seed_shared_fill_review_api(app.state.database, recognized_text="失去")

        resolved = client.post(
            "/api/grading-review-items/fill-review/resolve",
            json={
                "action": "confirm",
                "teacherReason": "教师确认当前判定",
            },
        )

        assert resolved.status_code == 200
        data = resolved.json()["data"]
        assert data["status"] == "needs_review"
        assert data["overriddenReasons"] == ["MISSING_EVIDENCE"]
        row = app.state.database.fetchone(
            "SELECT tool_observations_json FROM grading_question_results WHERE id='fill-result'"
        )
        assert row is not None
        observation = json.loads(row["tool_observations_json"])[-1]
        assert observation["tool"] == "teacher_review"
        assert observation["payload"]["overriddenReasons"] == ["MISSING_EVIDENCE"]


@pytest.mark.parametrize("blank_key", ["B1", "B2", "B3"])
def test_teacher_recognition_correction_rejudges_only_selected_blank_key(
    tmp_path: Path,
    monkeypatch,
    blank_key: str,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        model = KeyedFillCorrectionModelStub()
        app.state.model_client = model
        before_blanks = {
            row["blank_key"]: row
            for row in database.fetchall(
                "SELECT * FROM grading_blank_results ORDER BY blank_key"
            )
        }
        before_result = database.fetchone(
            "SELECT decisions_json,grading_config_snapshot_json FROM grading_question_results "
            "WHERE id='fill-result'"
        )
        assert before_result is not None
        before_decisions = {
            item["key"]: item for item in json.loads(before_result["decisions_json"])
        }
        before_configs = {
            item["blankKey"]: item
            for item in json.loads(before_result["grading_config_snapshot_json"])["blanks"]
        }

        response = client.patch(
            f"/api/grading-question-results/fill-result/blanks/{blank_key}",
            json={
                "teacherReason": "教师核对原卷后修正该空文字",
                "expectedGradingRevision": versions["gradingRevision"],
                "frameSetId": versions["frameSetId"],
                "blankConfigVersionId": versions["blankConfigVersionId"],
                "processingRevisionId": versions["processingRevisionId"],
                "recognizedText": f"teacher-corrected-{blank_key}",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["blankKey"] == blank_key
        assert data["gradingRevision"] == 8
        assert data["runRevision"] == 5
        assert data["blankResult"]["recognizedAnswer"] == f"teacher-corrected-{blank_key}"
        assert model.blank_keys == [blank_key]

        after_blanks = {
            row["blank_key"]: row
            for row in database.fetchall(
                "SELECT * FROM grading_blank_results ORDER BY blank_key"
            )
        }
        assert after_blanks[blank_key]["recognized_answer"] == f"teacher-corrected-{blank_key}"
        for sibling_key in {"B1", "B2", "B3"} - {blank_key}:
            assert after_blanks[sibling_key] == before_blanks[sibling_key]

        after_result = database.fetchone(
            """SELECT decisions_json,grading_config_snapshot_json,result_revision
               FROM grading_question_results WHERE id='fill-result'"""
        )
        assert after_result is not None
        after_decisions = {
            item["key"]: item for item in json.loads(after_result["decisions_json"])
        }
        after_configs = {
            item["blankKey"]: item
            for item in json.loads(after_result["grading_config_snapshot_json"])["blanks"]
        }
        assert after_result["result_revision"] == 8
        for sibling_key in {"B1", "B2", "B3"} - {blank_key}:
            assert after_decisions[sibling_key] == before_decisions[sibling_key]
            assert after_configs[sibling_key] == before_configs[sibling_key]
        assert after_configs[blank_key]["studentAnswer"] == f"teacher-corrected-{blank_key}"

        run = database.fetchone(
            "SELECT result_revision FROM grading_runs WHERE id='fill-grading'"
        )
        assert run == {"result_revision": 5}
        assert {
            row["status"]
            for row in database.fetchall(
                "SELECT status FROM grading_artifacts WHERE grading_run_id='fill-grading'"
            )
        } == {"stale"}
        event = database.fetchone(
            """SELECT actor,payload_json FROM grading_events
               WHERE event_type='blank_review_corrected'"""
        )
        assert event is not None
        audit = json.loads(event["payload_json"])
        assert event["actor"] == app.state.settings.teacher_name
        assert audit["blankKey"] == blank_key
        assert audit["teacherReason"] == "教师核对原卷后修正该空文字"
        assert audit["before"]["recognizedText"] == before_blanks[blank_key][
            "recognized_answer"
        ]
        assert audit["after"]["recognizedText"] == f"teacher-corrected-{blank_key}"
        assert audit["versions"] == {
            "frameSetId": versions["frameSetId"],
            "blankConfigVersionId": versions["blankConfigVersionId"],
            "processingRevisionId": versions["processingRevisionId"],
            "gradingRevisionBefore": 7,
            "gradingRevisionAfter": 8,
            "runRevisionBefore": 4,
            "runRevisionAfter": 5,
        }
        assert audit["affectedResultIds"]["questionResultId"] == "fill-result"
        assert audit["affectedResultIds"]["blankResultId"] == before_blanks[blank_key]["id"]
        assert set(audit["affectedResultIds"]["artifactIds"]) == {
            "fill-annotation",
            "fill-error_report",
        }


@pytest.mark.asyncio
async def test_last_keyed_blank_correction_advances_run_to_artifact_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app):
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        timestamp = now_iso()
        blank = database.fetchone(
            """SELECT id FROM grading_blank_results
               WHERE grading_question_result_id='fill-result' AND blank_key='B1'"""
        )
        assert blank is not None
        with database.transaction() as connection:
            connection.execute(
                "DELETE FROM grading_review_items WHERE grading_run_id='fill-grading'"
            )
            connection.execute(
                """INSERT INTO grading_review_items(
                     id,grading_run_id,grading_question_result_id,grading_blank_result_id,
                     reason,status,context_json,created_at,updated_at
                   ) VALUES('last-blank-review','fill-grading','fill-result',?,
                     'LOW_RECOGNITION_CONFIDENCE','open',? ,?,?)""",
                (
                    blank["id"],
                    json_dumps({"questionId": "fill-question", "blankKey": "B1"}),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """UPDATE grading_question_results SET status='needs_review',
                   review_reasons_json='["LOW_RECOGNITION_CONFIDENCE"]'
                   WHERE id='fill-result'"""
            )
            connection.execute(
                """UPDATE grading_runs SET status='needs_review',stage='needs_review',
                   open_review_count=1 WHERE id='fill-grading'"""
            )

        await app.state.grading_review_service.correct_blank(
            "fill-result",
            "B1",
            GradingBlankCorrection(
                teacherReason="教师确认第一空正确",
                expectedGradingRevision=int(versions["gradingRevision"]),
                frameSetId=str(versions["frameSetId"]),
                blankConfigVersionId=str(versions["blankConfigVersionId"]),
                processingRevisionId=str(versions["processingRevisionId"]),
                finalStatus="correct",
            ),
            KeyedFillCorrectionModelStub(),
        )

        run = database.fetchone(
            """SELECT status,stage,open_review_count
               FROM grading_runs WHERE id='fill-grading'"""
        )
        assert run == {
            "status": "generating_annotation",
            "stage": "generating_annotation",
            "open_review_count": 0,
        }


def test_teacher_final_blank_override_is_keyed_and_does_not_call_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        model = KeyedFillCorrectionModelStub()
        app.state.model_client = model
        before = {
            row["blank_key"]: row
            for row in database.fetchall(
                "SELECT * FROM grading_blank_results ORDER BY blank_key"
            )
        }

        response = client.patch(
            "/api/grading-question-results/fill-result/blanks/B2",
            json={
                "teacherReason": "教师明确判定第二空错误",
                "expectedGradingRevision": versions["gradingRevision"],
                "frameSetId": versions["frameSetId"],
                "blankConfigVersionId": versions["blankConfigVersionId"],
                "processingRevisionId": versions["processingRevisionId"],
                "finalStatus": "incorrect",
            },
        )

        assert response.status_code == 200, response.text
        assert model.blank_keys == []
        selected = response.json()["data"]["blankResult"]
        assert selected["blankKey"] == "B2"
        assert selected["status"] == "incorrect"
        assert selected["score"] == "0.00"
        after = {
            row["blank_key"]: row
            for row in database.fetchall(
                "SELECT * FROM grading_blank_results ORDER BY blank_key"
            )
        }
        assert after["B1"] == before["B1"]
        assert after["B3"] == before["B3"]
        assert after["B2"]["recognized_answer"] == before["B2"]["recognized_answer"]
        assert after["B2"]["status"] == "incorrect"
        assert after["B2"]["score"] == "0.00"
        detail = client.get("/api/grading-runs/fill-grading/questions/fill-question")
        assert detail.status_code == 200
        detail_data = detail.json()["data"]
        assert detail_data["gradingRevision"] == 8
        assert detail_data["frameSetId"] == versions["frameSetId"]
        assert detail_data["blankConfigVersionId"] == versions["blankConfigVersionId"]
        assert detail_data["processingRevisionId"] == versions["processingRevisionId"]
        assert detail_data["geometryIssues"] == []
        assert detail_data["questionFrames"] == [
            {
                "id": "fill-mapped-frame",
                "questionId": "fill-question",
                "pageId": "fill-page",
                "polygon": [
                    {"x": 40.0, "y": 56.0},
                    {"x": 960.0, "y": 56.0},
                    {"x": 960.0, "y": 966.0},
                    {"x": 40.0, "y": 966.0},
                ],
                "frameSetId": versions["frameSetId"],
                "frameRegionId": "fill-question-frame-region",
                "alignmentRevisionId": "fill-alignment-v1",
                "processingRevisionId": versions["processingRevisionId"],
                "status": "ready",
                "issues": [],
            }
        ]
        assert [item["blankKey"] for item in detail_data["blankAnchors"]] == [
            "B1",
            "B2",
            "B3",
        ]
        expected_anchor_polygon = [
            {"x": 100.0, "y": 420.0},
            {"x": 400.0, "y": 420.0},
            {"x": 400.0, "y": 490.0},
            {"x": 100.0, "y": 490.0},
        ]
        for actual, expected in zip(
            detail_data["blankAnchors"][1]["studentPolygon"],
            expected_anchor_polygon,
            strict=True,
        ):
            assert actual["x"] == pytest.approx(expected["x"])
            assert actual["y"] == pytest.approx(expected["y"])
        expected_anchor_bbox = {
            "x": 100.0,
            "y": 420.0,
            "width": 300.0,
            "height": 70.0,
        }
        for key, expected in expected_anchor_bbox.items():
            assert detail_data["blankAnchors"][1]["studentBBox"][key] == pytest.approx(
                expected
            )
        b2 = next(item for item in detail_data["blankResults"] if item["blankKey"] == "B2")
        assert b2["recognizedAnswer"] == before["B2"]["recognized_answer"]
        assert b2["standardAnswers"] == ["异种"]
        assert b2["decision"]["status"] == "incorrect"
        assert b2["frameSetId"] == versions["frameSetId"]
        assert b2["blankConfigVersionId"] == versions["blankConfigVersionId"]
        assert b2["processingRevisionId"] == versions["processingRevisionId"]
        assert b2["gradingRevision"] == 8


@pytest.mark.parametrize(
    ("path_key", "payload_change", "expected_code"),
    [
        ("B99", {}, "GRADING_BLANK_RESULT_NOT_FOUND"),
        ("B2", {"expectedGradingRevision": 6}, "GRADING_RESULT_REVISION_CONFLICT"),
        ("B2", {"frameSetId": "old-frame"}, "GRADING_BLANK_VERSION_CONFLICT"),
        ("B2", {"blankConfigVersionId": "old-config"}, "GRADING_BLANK_VERSION_CONFLICT"),
        ("B2", {"processingRevisionId": "old-processing"}, "GRADING_BLANK_VERSION_CONFLICT"),
    ],
)
def test_teacher_blank_correction_rejects_unknown_key_or_stale_versions(
    tmp_path: Path,
    monkeypatch,
    path_key: str,
    payload_change: dict[str, object],
    expected_code: str,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with TestClient(app) as client:
        database: Database = app.state.database
        versions = seed_versioned_fill_correction_api(database)
        model = KeyedFillCorrectionModelStub()
        app.state.model_client = model
        payload = {
            "teacherReason": "尝试修改",
            "expectedGradingRevision": versions["gradingRevision"],
            "frameSetId": versions["frameSetId"],
            "blankConfigVersionId": versions["blankConfigVersionId"],
            "processingRevisionId": versions["processingRevisionId"],
            "recognizedText": "新文字",
            **payload_change,
        }

        response = client.patch(
            f"/api/grading-question-results/fill-result/blanks/{path_key}",
            json=payload,
        )

        assert response.status_code in {404, 409}
        assert response.json()["error"]["code"] == expected_code
        assert model.blank_keys == []
        assert database.fetchone(
            "SELECT result_revision FROM grading_question_results WHERE id='fill-result'"
        ) == {"result_revision": 7}
        assert database.fetchone(
            """SELECT COUNT(*) AS value FROM grading_events
               WHERE event_type='blank_review_corrected'"""
        ) == {"value": 0}


def test_last_teacher_review_generates_report_without_error_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("GRADING_ENABLED", "true")
    with TestClient(app) as client:
        analysis_model = install_error_analysis_model()
        database: Database = app.state.database
        seed_shared_fill_review_api(database, recognized_text="失去")
        Image.new("RGB", (1000, 1400), "white").save(app.state.settings.data_dir / "student.png")
        row = database.fetchone(
            "SELECT decisions_json FROM grading_question_results WHERE id='fill-result'"
        )
        assert row is not None
        decisions = json.loads(row["decisions_json"])
        decisions[-1].update({"status": "incorrect", "score": "0.00", "reason": "第三空错误"})
        with database.transaction() as connection:
            connection.execute(
                "DELETE FROM grading_review_items WHERE grading_run_id='fill-grading'"
            )
            connection.execute(
                """INSERT INTO grading_review_items(
                     id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
                   ) VALUES(
                     'fill-final-review','fill-grading','fill-result',
                     'UNCERTAIN_ERROR_LOCATION',datetime('now'),datetime('now')
                   )"""
            )
            connection.execute(
                """UPDATE grading_question_results SET raw_score='2.00',final_score='2.00',
                   decisions_json=?,error_locations_json='[]',
                   review_reasons_json='[\"UNCERTAIN_ERROR_LOCATION\"]'
                   WHERE id='fill-result'""",
                (json.dumps(decisions),),
            )
            connection.execute(
                """UPDATE grading_runs SET total_score='2.00',open_review_count=1
                   WHERE id='fill-grading'"""
            )

        resolved = client.post(
            "/api/grading-review-items/fill-final-review/resolve",
            json={
                "action": "confirm",
                "teacherReason": "教师查看原卷后确认该题得两分",
            },
        )

        assert resolved.status_code == 200
        assert resolved.json()["data"]["status"] == "final"
        completed = wait_for_grading_status(client, "fill-grading", "completed")
        assert completed["openReviewCount"] == 0
        artifacts = database.fetchall(
            """SELECT artifact_type,status,preview_json FROM grading_artifacts
               WHERE grading_run_id='fill-grading' ORDER BY artifact_type"""
        )
        assert {row["artifact_type"] for row in artifacts} == {
            "annotation",
            "error_report",
        }
        assert all(row["status"] == "current" for row in artifacts)
        report_row = next(row for row in artifacts if row["artifact_type"] == "error_report")
        preview = json.loads(report_row["preview_json"])
        assert preview["questions"][0]["evidenceRegionId"] == "fill-region"
        assert preview["questions"][0]["errorReason"] == (
            "学生作答只覆盖了部分正确要求，未完成全部核对。"
        )
        assert "教师查看原卷后确认该题得两分" not in preview["questions"][0]["errorReason"]
        assert len(analysis_model.calls) == 1
        request = json.loads(analysis_model.calls[0]["user_content"][0]["text"])
        assert request["questions"][0]["teacherReviewFacts"] == [
            "教师查看原卷后确认该题得两分"
        ]
