from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from homework_judge.artifacts.service import GradingArtifactService
from homework_judge.db.database import Database, json_dumps, json_loads, now_iso
from homework_judge.errors import AppError
from homework_judge.grading.contracts import ReviewReason
from homework_judge.grading.review import GradingReviewService
from homework_judge.schemas import GradingReviewResolution

from .test_grading_pipeline import grading_settings


class RevisionBumpDatabase(Database):
    bump_on_review_read = False

    def fetchone(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        value = super().fetchone(sql, params)
        if self.bump_on_review_read and value is not None and "FROM grading_review_items i" in sql:
            self.bump_on_review_read = False
            self.execute(
                """UPDATE grading_question_results SET result_revision=result_revision+1
                   WHERE id=?""",
                (str(value["result_id"]),),
            )
        return value


def seed_calculation_review(database: Database) -> None:
    timestamp = now_iso()
    evidence = {
        "page_id": "page",
        "region_id": "region",
        "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 100},
        "cropped_image_path": None,
        "recognized_text": "steps",
        "char_or_step_range": None,
    }
    points = [
        ("point-1", "P1", "formula", "1.00", 0),
        ("point-2", "P2", "substitution", "1.00", 1),
        ("point-3", "P3", "independent conclusion", "2.00", 2),
    ]
    decisions = [
        {
            "key": "P1",
            "status": "failed",
            "score": "0",
            "max_score": "1",
            "reason": "formula",
            "evidence_refs": [evidence],
            "blocked_by": None,
        },
        {
            "key": "P2",
            "status": "blocked_by_dependency",
            "score": "0",
            "max_score": "1",
            "reason": "blocked",
            "evidence_refs": [evidence],
            "blocked_by": "P1",
        },
        {
            "key": "P3",
            "status": "satisfied",
            "score": "2",
            "max_score": "2",
            "reason": "correct",
            "evidence_refs": [evidence],
            "blocked_by": None,
        },
    ]
    config = {
        "rubricPoints": [
            {"key": "P1", "criterion": "formula", "score": "1", "order": 0, "dependencies": []},
            {
                "key": "P2",
                "criterion": "substitution",
                "score": "1",
                "order": 1,
                "dependencies": ["P1"],
            },
            {
                "key": "P3",
                "criterion": "independent",
                "score": "2",
                "order": 2,
                "dependencies": [],
            },
        ],
        "rubricPointIds": {"P1": "point-1", "P2": "point-2", "P3": "point-3"},
    }
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','T','completed',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES(
                 'question','task','source',0,'1','1','calculate','calculation',4,
                 '[1]',1,'[]','confirmed'
               )"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,question_region_status,created_at,updated_at
               ) VALUES('submission','task','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'page','submission',1,'page.jpg',1000,1400,'sha','aligned',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,recognized_text,status,
                 created_at,updated_at
               ) VALUES(
                 'response','submission','question','1','steps','recognized',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES('region','response','page',?,?,?)""",
            (
                json_dumps(evidence["original_bbox"]),
                json_dumps(evidence["original_bbox"]),
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,created_at,updated_at
               ) VALUES(
                 'rubric','question',1,'frozen','4.00','manual',?,?
               )""",
            (timestamp, timestamp),
        )
        for point_id, key, criterion, score, order in points:
            connection.execute(
                """INSERT INTO rubric_points(
                     id,rubric_version_id,point_key,criterion,score,sort_order,
                     created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (point_id, "rubric", key, criterion, score, order, timestamp, timestamp),
            )
        connection.execute(
            """INSERT INTO rubric_dependencies(
                 rubric_version_id,point_id,depends_on_point_id,created_at
               ) VALUES('rubric','point-2','point-1',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,max_score,total_score,
                 progress_total,progress_current,open_review_count,created_at,updated_at
               ) VALUES(
                 'grading','submission','task','needs_review','needs_review','hash',
                 '4.00','2.00',1,1,1,?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_question_results(
                 id,grading_run_id,question_id,student_response_id,rubric_version_id,
                 input_hash,question_type,status,raw_score,final_score,max_score,
                 answer_snapshot_json,grading_config_snapshot_json,decisions_json,
                 evidence_refs_json,error_locations_json,tool_observations_json,
                 review_reasons_json,created_at,updated_at
               ) VALUES(
                 'result','grading','question','response','rubric','question-hash',
                 'calculation','needs_review','2.00','2.00','4.00','{}',?,?,?,?,?,?,?,?
               )""",
            (
                json_dumps(config),
                json_dumps(decisions),
                json_dumps([evidence]),
                json_dumps([evidence]),
                "[]",
                json_dumps(["DEPENDENCY_CONTRADICTION"]),
                timestamp,
                timestamp,
            ),
        )
        point_states = [
            ("P1", "failed", "failed", "0", "0"),
            ("P2", "satisfied", "blocked_by_dependency", "1", "0"),
            ("P3", "satisfied", "satisfied", "2", "2"),
        ]
        for index, (key, direct, final, direct_score, final_score) in enumerate(
            point_states, start=1
        ):
            connection.execute(
                """INSERT INTO grading_point_results(
                     id,grading_question_result_id,rubric_point_id,point_key,
                     direct_status,final_status,direct_score,final_score,max_score,
                     evidence_refs_json,model_result_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"point-result-{index}",
                    "result",
                    f"point-{index}",
                    key,
                    direct,
                    final,
                    direct_score,
                    final_score,
                    points[index - 1][3],
                    json_dumps([evidence]),
                    "{}",
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            """INSERT INTO grading_review_items(
                 id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
               ) VALUES(
                 'review','grading','result','DEPENDENCY_CONTRADICTION',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_artifacts(
                 id,grading_run_id,artifact_type,result_revision,status,relative_path,
                 created_at,updated_at
               ) VALUES(
                 'artifact','grading','annotation',0,'current','old.pdf',?,?
               )""",
            (timestamp, timestamp),
        )


def seed_shared_fill_review(
    database: Database,
    *,
    recognized_text: str = "失去\n异种\n吸引",
    teacher_error_location: bool = False,
) -> None:
    timestamp = now_iso()
    shared_evidence = {
        "page_id": "page",
        "region_id": "shared-region",
        "original_bbox": {"x": 100, "y": 200, "width": 300, "height": 100},
        "cropped_image_path": None,
        "recognized_text": recognized_text,
        "char_or_step_range": None,
    }
    teacher_evidence = {
        "page_id": "page",
        "region_id": "teacher-region",
        "original_bbox": {"x": 100, "y": 350, "width": 300, "height": 100},
        "cropped_image_path": None,
        "recognized_text": "教师选择的错误位置",
        "char_or_step_range": None,
    }
    blank_specs = [
        ("B1", "1.00", "失去", ["shared-region"]),
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
            "reason": "教师同义答案完全一致",
            "evidence_refs": [shared_evidence] if key == "B1" else [],
            "blocked_by": None,
        }
        for key, score, _answer, _region_ids in blank_specs
    ]
    evidence = [shared_evidence, teacher_evidence]
    error_locations = [teacher_evidence] if teacher_error_location else []
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('fill-task','T','completed',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('fill-source','fill-task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                 stem,question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES(
                 'fill-question','fill-task','fill-source',0,'9','9','fill','fill_blank',4,
                 '[1]',1,'[]','confirmed'
               )"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,question_region_status,created_at,updated_at
               ) VALUES('fill-submission','fill-task','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'page','fill-submission',1,'page.jpg',1000,1400,'sha','aligned',?,?
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
        for index, region in enumerate(evidence):
            connection.execute(
                """INSERT INTO student_response_regions(
                     id,student_response_id,student_page_id,sort_order,
                     template_bbox_json,student_bbox_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    region["region_id"],
                    "fill-response",
                    "page",
                    index,
                    json_dumps(region["original_bbox"]),
                    json_dumps(region["original_bbox"]),
                    timestamp,
                ),
            )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,max_score,total_score,
                 progress_total,progress_current,open_review_count,created_at,updated_at
               ) VALUES(
                 'fill-grading','fill-submission','fill-task','needs_review','needs_review',
                 'fill-hash','4.00','4.00',1,1,1,?,?
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
                json_dumps(evidence),
                json_dumps(error_locations),
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
                    f"blank-{index}",
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
        connection.execute(
            """INSERT INTO grading_review_items(
                 id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
               ) VALUES(
                 'fill-review','fill-grading','fill-result','MISSING_EVIDENCE',?,?
               )""",
            (timestamp, timestamp),
        )


def test_teacher_point_override_reapplies_dependencies_and_invalidates_artifacts(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_review(database)
    service = GradingReviewService(settings, database)

    result = service.resolve(
        "review",
        GradingReviewResolution(
            action="override",
            teacherReason="原图显示公式正确",
            pointDecisions=[{"pointKey": "P1", "directStatus": "satisfied"}],
        ),
    )

    assert result["score"] == "4.00"
    row = database.fetchone("SELECT * FROM grading_question_results WHERE id='result'")
    assert row is not None
    assert row["status"] == "final"
    decisions = {item["key"]: item for item in json_loads(row["decisions_json"], [])}
    assert decisions["P1"]["status"] == "satisfied"
    assert decisions["P2"]["status"] == "satisfied"
    run = database.fetchone("SELECT * FROM grading_runs WHERE id='grading'")
    assert run is not None
    assert run["total_score"] == "4.00"
    assert run["status"] == "generating_annotation"
    artifact = database.fetchone("SELECT * FROM grading_artifacts WHERE id='artifact'")
    assert artifact is not None
    assert artifact["status"] == "stale"


def test_teacher_point_override_preserves_untouched_partial_direct_decision(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_review(database)
    row = database.fetchone(
        "SELECT decisions_json FROM grading_question_results WHERE id='result'"
    )
    assert row is not None
    decisions = json_loads(row["decisions_json"], [])
    decisions[2].update({"status": "partial", "score": "1.00"})
    with database.transaction() as connection:
        connection.execute(
            """UPDATE grading_question_results
               SET raw_score='1.00',final_score='1.00',decisions_json=?
               WHERE id='result'""",
            (json_dumps(decisions),),
        )
        connection.execute(
            """UPDATE grading_point_results
               SET direct_status='partial',final_status='partial',
                   direct_score='1.00',final_score='1.00',
                   model_result_json=?
               WHERE grading_question_result_id='result' AND point_key='P3'""",
            (json_dumps({"reason": "partially shown"}),),
        )

    result = GradingReviewService(settings, database).resolve(
        "review",
        GradingReviewResolution(
            action="override",
            teacherReason="原图显示公式正确",
            pointDecisions=[{"pointKey": "P1", "directStatus": "satisfied"}],
        ),
    )

    assert result["score"] == "3.00"
    saved = database.fetchone(
        """SELECT direct_status,final_status,direct_score,final_score
           FROM grading_point_results
           WHERE grading_question_result_id='result' AND point_key='P3'"""
    )
    assert saved == {
        "direct_status": "partial",
        "final_status": "partial",
        "direct_score": "1.00",
        "final_score": "1.00",
    }
    result_row = database.fetchone(
        "SELECT tool_observations_json FROM grading_question_results WHERE id='result'"
    )
    assert result_row is not None
    observations = json_loads(result_row["tool_observations_json"], [])
    assert observations[-1]["payload"]["scoringPolicyVersion"] == (
        "evidence-aware-alternative-methods-v3"
    )


def test_refresh_run_keeps_calculation_suggestion_for_non_structural_review(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_review(database)
    service = GradingReviewService(settings, database)

    with database.transaction() as connection:
        service._refresh_run(connection, "grading", now_iso())

    run = database.fetchone("SELECT * FROM grading_runs WHERE id='grading'")
    assert run is not None
    assert run["status"] == "needs_review"
    assert run["total_score"] == "2.00"


def test_refresh_run_hides_missing_evidence_placeholder_until_review_is_resolved(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_calculation_review(database)
    stored = database.fetchone(
        "SELECT decisions_json FROM grading_question_results WHERE id='result'"
    )
    assert stored is not None
    decisions = json_loads(stored["decisions_json"], [])
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
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """UPDATE grading_question_results SET raw_score='0',final_score='0.00',
               decisions_json=?,evidence_refs_json='[]',error_locations_json='[]',
               review_reasons_json=? WHERE id='result'""",
            (
                json_dumps(decisions),
                json_dumps(["MISSING_EVIDENCE", "LOW_RECOGNITION_CONFIDENCE"]),
            ),
        )
        connection.execute(
            "UPDATE grading_review_items SET reason='MISSING_EVIDENCE' WHERE id='review'"
        )
        connection.execute(
            """INSERT INTO grading_review_items(
                 id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
               ) VALUES('review-low','grading','result','LOW_RECOGNITION_CONFIDENCE',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """UPDATE grading_runs SET total_score=NULL,open_review_count=2
               WHERE id='grading'"""
        )
    service = GradingReviewService(settings, database)

    first = service.resolve(
        "review-low",
        GradingReviewResolution(
            action="confirm",
            teacherReason="teacher checked recognition confidence",
        ),
    )

    assert first["status"] == "needs_review"
    run = database.fetchone("SELECT * FROM grading_runs WHERE id='grading'")
    assert run is not None
    assert run["open_review_count"] == 1
    assert run["total_score"] is None

    final = service.resolve(
        "review",
        GradingReviewResolution(
            action="confirm",
            teacherReason="teacher confirmed the zero score from the original page",
        ),
    )

    assert final["status"] == "final"
    run = database.fetchone("SELECT * FROM grading_runs WHERE id='grading'")
    assert run is not None
    assert run["open_review_count"] == 0
    assert run["total_score"] == "0.00"
    assert run["status"] == "generating_annotation"


def test_existing_fill_review_keeps_only_explicit_blank_evidence_without_changing_score(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    service = GradingReviewService(settings, database)

    result = service.resolve(
        "fill-review",
        GradingReviewResolution(
            action="confirm",
            teacherReason="教师确认三个空位均正确",
        ),
    )

    assert result["score"] == "4.00"
    row = database.fetchone("SELECT * FROM grading_question_results WHERE id='fill-result'")
    assert row is not None
    decisions = json_loads(row["decisions_json"], [])
    assert [item["status"] for item in decisions] == ["correct", "correct", "correct"]
    assert [
        [evidence["region_id"] for evidence in item["evidence_refs"]] for item in decisions
    ] == [["shared-region"], [], []]
    blank_rows = database.fetchall(
        """SELECT blank_key,evidence_refs_json FROM grading_blank_results
           WHERE grading_question_result_id='fill-result' ORDER BY blank_key"""
    )
    assert [
        [item["region_id"] for item in json_loads(blank["evidence_refs_json"], [])]
        for blank in blank_rows
    ] == [["shared-region"], [], []]
    run = database.fetchone("SELECT * FROM grading_runs WHERE id='fill-grading'")
    assert run is not None
    assert run["total_score"] == "4.00"
    assert run["status"] == "generating_annotation"


def test_teacher_fill_override_wins_and_preserves_existing_error_location(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database, teacher_error_location=True)
    service = GradingReviewService(settings, database)

    result = service.resolve(
        "fill-review",
        GradingReviewResolution(
            action="override",
            teacherReason="教师确认第三空错误",
            blankDecisions=[{"blankKey": "B3", "status": "incorrect"}],
        ),
    )

    assert result["score"] == "2.00"
    row = database.fetchone("SELECT * FROM grading_question_results WHERE id='fill-result'")
    assert row is not None
    decisions = {item["key"]: item for item in json_loads(row["decisions_json"], [])}
    assert decisions["B3"]["status"] == "incorrect"
    assert Decimal(decisions["B3"]["score"]) == Decimal("0.00")
    assert [item["region_id"] for item in json_loads(row["error_locations_json"], [])] == [
        "teacher-region"
    ]


def test_unmatched_positive_fill_blanks_are_recorded_but_do_not_block_teacher(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database, recognized_text="失去")
    service = GradingReviewService(settings, database)

    result = service.resolve(
        "fill-review",
        GradingReviewResolution(
            action="confirm",
            teacherReason="教师确认当前判定",
        ),
    )

    assert result["status"] == "final"
    assert result["score"] == "4.00"
    assert result["overriddenReasons"] == ["MISSING_EVIDENCE"]
    question = database.fetchone(
        "SELECT tool_observations_json FROM grading_question_results WHERE id='fill-result'"
    )
    review = database.fetchone(
        "SELECT resolution_json FROM grading_review_items WHERE id='fill-review'"
    )
    event = database.fetchone(
        "SELECT payload_json FROM grading_events WHERE event_type='review_resolved'"
    )
    assert question is not None and review is not None and event is not None
    observation = json_loads(question["tool_observations_json"], [])[-1]
    assert observation["tool"] == "teacher_review"
    assert observation["detail"] == "教师确认当前判定"
    assert observation["payload"]["overriddenReasons"] == ["MISSING_EVIDENCE"]
    warning_details = {warning["detail"] for warning in observation["payload"]["auditWarnings"]}
    assert warning_details >= {"B2 得分但缺少证据", "B3 得分但缺少证据"}
    assert json_loads(review["resolution_json"], {})["overriddenReasons"] == ["MISSING_EVIDENCE"]
    assert json_loads(event["payload_json"], {})["overriddenReasons"] == ["MISSING_EVIDENCE"]


def test_score_inconsistency_is_recorded_without_changing_confirmed_score(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    row = database.fetchone(
        "SELECT decisions_json FROM grading_question_results WHERE id='fill-result'"
    )
    assert row is not None
    decisions = json_loads(row["decisions_json"], [])
    decisions[-1]["score"] = "0.00"
    database.execute(
        """UPDATE grading_question_results SET decisions_json=?,
           review_reasons_json='[\"SCORE_INCONSISTENCY\"]' WHERE id='fill-result'""",
        (json_dumps(decisions),),
    )
    database.execute(
        """UPDATE grading_review_items SET reason='SCORE_INCONSISTENCY'
           WHERE id='fill-review'"""
    )

    result = GradingReviewService(settings, database).resolve(
        "fill-review",
        GradingReviewResolution(
            action="confirm",
            teacherReason="教师确认最终得分为四分",
        ),
    )

    assert result["score"] == "4.00"
    assert "SCORE_INCONSISTENCY" in result["overriddenReasons"]
    stored = database.fetchone(
        """SELECT final_score,tool_observations_json
           FROM grading_question_results WHERE id='fill-result'"""
    )
    assert stored is not None
    assert stored["final_score"] == "4.00"
    observation = json_loads(stored["tool_observations_json"], [])[-1]
    assert "SCORE_INCONSISTENCY" in observation["payload"]["overriddenReasons"]


def test_resolving_same_review_twice_does_not_duplicate_teacher_record(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    service = GradingReviewService(settings, database)
    payload = GradingReviewResolution(action="confirm", teacherReason="教师确认当前判定")

    service.resolve("fill-review", payload)
    with pytest.raises(AppError) as raised:
        service.resolve("fill-review", payload)

    assert raised.value.status_code == 409
    stored = database.fetchone(
        "SELECT tool_observations_json FROM grading_question_results WHERE id='fill-result'"
    )
    assert stored is not None
    observations = json_loads(stored["tool_observations_json"], [])
    assert len([item for item in observations if item["tool"] == "teacher_review"]) == 1


def test_review_resolution_rejects_a_concurrent_result_revision(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = RevisionBumpDatabase(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    database.bump_on_review_read = True

    with pytest.raises(AppError) as raised:
        GradingReviewService(settings, database).resolve(
            "fill-review",
            GradingReviewResolution(
                action="confirm",
                teacherReason="教师确认当前判定",
            ),
        )

    assert raised.value.code == "GRADING_RESULT_REVISION_CONFLICT"
    assert database.fetchone(
        "SELECT status FROM grading_review_items WHERE id='fill-review'"
    ) == {"status": "open"}
    assert database.fetchone(
        "SELECT result_revision FROM grading_question_results WHERE id='fill-result'"
    ) == {"result_revision": 1}


@pytest.mark.parametrize("reason", list(ReviewReason))
def test_every_grading_audit_reason_is_recorded_without_blocking_teacher(
    tmp_path: Path,
    reason: ReviewReason,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    database.execute(
        "UPDATE grading_review_items SET reason=? WHERE id='fill-review'",
        (reason.value,),
    )

    result = GradingReviewService(settings, database).resolve(
        "fill-review",
        GradingReviewResolution(
            action="confirm",
            teacherReason="教师确认当前判定",
        ),
    )

    assert result["status"] == "final"
    assert reason.value in result["overriddenReasons"]


@pytest.mark.asyncio
async def test_artifact_generation_still_rejects_unreviewed_missing_location(
    tmp_path: Path,
) -> None:
    settings = grading_settings(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    seed_shared_fill_review(database)
    row = database.fetchone(
        "SELECT decisions_json FROM grading_question_results WHERE id='fill-result'"
    )
    assert row is not None
    decisions = json_loads(row["decisions_json"], [])
    decisions[-1].update({"status": "incorrect", "score": "0.00", "reason": "第三空错误"})
    database.execute("DELETE FROM grading_review_items WHERE grading_run_id='fill-grading'")
    database.execute(
        """UPDATE grading_question_results SET status='final',raw_score='2.00',
           final_score='2.00',decisions_json=?,error_locations_json='[]',
           review_reasons_json='[]' WHERE id='fill-result'""",
        (json_dumps(decisions),),
    )
    database.execute(
        """UPDATE grading_runs SET status='generating_annotation',
           stage='generating_annotation',total_score='2.00',open_review_count=0
           WHERE id='fill-grading'"""
    )

    with pytest.raises(AppError) as raised:
        await GradingArtifactService(settings, database).generate("fill-grading")

    assert raised.value.code == "ANNOTATION_ERROR_LOCATION_REQUIRED"
