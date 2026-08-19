from __future__ import annotations

from pathlib import Path

import pytest

from homework_judge.db.database import Database, json_loads, now_iso
from homework_judge.errors import AppError
from homework_judge.grading.blank_config_confirmation import (
    ensure_submission_blank_configs_current,
    ensure_task_fill_blank_configs,
    prepare_task_fill_blank_configs,
    save_blank_config_version,
    serialize_blank_config_version,
)


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "blank-config-version.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','T','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,
                 size_bytes,sha256,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf',
                 '.pdf',1,'sha','exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(
                 id,document_id,page_number,image_path,width,height,sha256
               ) VALUES('page-1','exam',1,'page-1.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','task','exam','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,
                 confirmation_status
               ) VALUES('q1','task','source',0,'1','1','甲______乙______丙______',
                 'fill_blank',4,'[1]',1,'[]','pending')"""
        )
        connection.execute(
            """INSERT INTO matches(
                 id,task_id,question_id,method,status,teacher_answer,updated_at
               ) VALUES('match','task','q1','manual','suggested','甲 乙 丙',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,revision,source,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES('frame-v1','task',1,'confirmed',1,'teacher','frame-hash','teacher',
                 ?,?,?, 'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id='frame-v1' WHERE id='task'"
        )
        connection.execute(
            """INSERT INTO question_frame_items(
                 id,frame_set_id,question_id,status,revision,issues_json,confirmed_at,
                 confirmed_by,created_at,updated_at
               ) VALUES('frame-item','frame-v1','q1','confirmed',1,'[]',?,'teacher',?,?)""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_frame_regions(
                 id,frame_item_id,region_key,template_page_id,page_number,x,y,width,height,
                 sort_order,source,confidence,issues_json,created_at,updated_at
               ) VALUES('frame-region','frame-item','Q1','page-1',1,0.05,0.05,0.9,0.6,
                 0,'teacher',1,'[]',?,?)""",
            (timestamp, timestamp),
        )
    return database


def anchor(index: int, *, issues: list[str] | None = None) -> dict[str, object]:
    return {
        "templatePageId": "page-1",
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
        "issues": issues or [],
    }


def blanks(*, missing_anchor: bool = False) -> list[dict[str, object]]:
    scores = ("1.00", "1.00", "2.00")
    answers = ("甲", "乙", "丙")
    return [
        {
            "blankKey": f"B{index + 1}",
            "sortOrder": index,
            "maxScore": score,
            "answerKind": "text",
            "standardAnswers": [answer],
            "synonyms": [],
            "anchor": None if missing_anchor and index == 1 else anchor(index),
        }
        for index, (score, answer) in enumerate(zip(scores, answers, strict=True))
    ]


def test_prepare_and_grading_gate_auto_confirm_safe_derived_config(
    tmp_path: Path,
) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        batch = prepare_task_fill_blank_configs(connection, "task")
    assert [item.question_id for item in batch.candidates] == ["q1"]
    assert batch.existing_question_ids == []
    assert batch.blockers == []

    summary = ensure_task_fill_blank_configs(database, "task", "system:test")

    assert summary.saved_question_ids == ["q1"]
    version = database.fetchone(
        "SELECT id,status,source FROM question_blank_config_versions"
    )
    assert version is not None
    assert version["status"] == "auto_confirmed"
    assert version["source"] == "model"
    value = serialize_blank_config_version(database, str(version["id"]))
    assert [item["maxScore"] for item in value["blanks"]] == ["1.33", "1.33", "1.34"]
    assert "blank_score_auto_allocated" in {
        item["code"] for item in value["readiness"]["advisoryIssues"]
    }


def test_explicit_teacher_confirmation_persists_immutable_full_version(
    tmp_path: Path,
) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        version_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="4.00",
            blanks=blanks(),
            actor="teacher",
            source="teacher",
            confirm=True,
        )

    value = serialize_blank_config_version(database, version_id)
    assert value["versionNumber"] == 1
    assert value["status"] == "teacher_confirmed"
    assert value["frameSetId"] == "frame-v1"
    assert value["readiness"]["blockingIssues"] == []
    assert [item["maxScore"] for item in value["blanks"]] == ["1.00", "1.00", "2.00"]
    assert value["blanks"][1]["anchor"] == anchor(1)
    row = database.fetchone(
        """SELECT frame_set_id,status,source,content_hash,confirmed_by
           FROM question_blank_config_versions WHERE id=?""",
        (version_id,),
    )
    assert row is not None
    assert row["status"] == "teacher_confirmed"
    assert row["confirmed_by"] == "teacher"
    assert row["content_hash"]
    persisted = database.fetchone(
        """SELECT max_score,anchor_json FROM question_blank_definition_versions
           WHERE blank_config_version_id=? AND blank_key='B2'""",
        (version_id,),
    )
    assert persisted is not None
    assert persisted["max_score"] == "1.00"
    assert json_loads(persisted["anchor_json"], {}) == anchor(1)
    assert database.fetchone(
        """SELECT config_version,current_blank_config_version_id
           FROM question_grading_configs WHERE question_id='q1'"""
    ) == {"config_version": 1, "current_blank_config_version_id": version_id}


def test_draft_confirm_and_edit_create_new_versions_with_cas(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        draft_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="4.00",
            blanks=blanks(),
            actor="teacher",
            source="teacher",
            confirm=False,
        )
    assert serialize_blank_config_version(database, draft_id)["status"] == "pending"
    with pytest.raises(AppError) as pending:
        ensure_task_fill_blank_configs(database, "task", "teacher")
    assert "blank_config_confirmation_required" in pending.value.details["questions"][0][
        "reasonCodes"
    ]

    with database.transaction() as connection:
        confirmed_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=1,
            max_score="4.00",
            blanks=blanks(),
            actor="teacher",
            source="teacher",
            confirm=True,
        )
    changed = blanks()
    changed[0]["synonyms"] = ["第一"]
    with database.transaction() as connection:
        edited_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=2,
            max_score="4.00",
            blanks=changed,
            actor="teacher",
            source="teacher",
            confirm=False,
        )
    assert confirmed_id != draft_id != edited_id
    assert database.fetchone(
        "SELECT status FROM question_blank_config_versions WHERE id=?", (confirmed_id,)
    ) == {"status": "teacher_confirmed"}
    assert serialize_blank_config_version(database, confirmed_id)["blanks"][0][
        "synonyms"
    ] == []
    assert serialize_blank_config_version(database, edited_id)["blanks"][0]["synonyms"] == [
        "第一"
    ]

    with pytest.raises(AppError) as stale:
        with database.transaction() as connection:
            save_blank_config_version(
                connection,
                database,
                question_id="q1",
                frame_set_id="frame-v1",
                expected_config_version=2,
                max_score="4.00",
                blanks=changed,
                actor="teacher",
                source="teacher",
                confirm=True,
            )
    assert stale.value.code == "BLANK_CONFIG_VERSION_CONFLICT"
    assert stale.value.details["actualConfigVersion"] == 3


def test_missing_anchor_is_advisory_and_can_be_teacher_confirmed(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        version_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="4.00",
            blanks=blanks(missing_anchor=True),
            actor="teacher",
            source="teacher",
            confirm=True,
        )
    value = serialize_blank_config_version(database, version_id)
    readiness = value["readiness"]
    assert value["status"] == "teacher_confirmed"
    assert readiness["blockingIssues"] == []
    assert "missing_blank_anchor" in {
        item["code"] for item in readiness["advisoryIssues"]
    }
    assert value["blanks"][1]["anchor"] is None


def test_teacher_can_save_three_blanks_when_ocr_stem_only_has_two_markers(
    tmp_path: Path,
) -> None:
    database = make_database(tmp_path)
    database.execute(
        "UPDATE questions SET stem='第一处______，第二处______。' WHERE id='q1'"
    )
    with database.transaction() as connection:
        version_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="5.00",
            blanks=[
                {
                    **value,
                    "maxScore": score,
                    "standardAnswers": [answer],
                    "anchor": None,
                }
                for value, score, answer in zip(
                    blanks(missing_anchor=True),
                    ("1.67", "1.67", "1.66"),
                    ("电荷转移", "守", "CD"),
                    strict=True,
                )
            ],
            actor="teacher",
            source="teacher",
            confirm=True,
        )
    saved = serialize_blank_config_version(database, version_id)
    assert [item["maxScore"] for item in saved["blanks"]] == ["1.67", "1.67", "1.66"]
    assert "stem_blank_count_conflict" in {
        item["code"] for item in saved["readiness"]["advisoryIssues"]
    }


def test_provided_anchor_outside_confirmed_question_frame_still_blocks(
    tmp_path: Path,
) -> None:
    database = make_database(tmp_path)
    invalid = blanks()
    invalid_anchor = anchor(1)
    invalid_anchor["box"] = {"x": 0.15, "y": 0.75, "width": 0.35, "height": 0.07}
    invalid[1]["anchor"] = invalid_anchor
    with pytest.raises(AppError) as captured:
        with database.transaction() as connection:
            save_blank_config_version(
                connection,
                database,
                question_id="q1",
                frame_set_id="frame-v1",
                expected_config_version=0,
                max_score="4.00",
                blanks=invalid,
                actor="teacher",
                source="teacher",
                confirm=True,
            )
    assert captured.value.code == "BLANK_CONFIG_NOT_READY"
    assert database.fetchone(
        "SELECT COUNT(*) AS value FROM question_blank_config_versions"
    ) == {"value": 0}


def test_auto_confirmation_requires_model_source_and_zero_blockers(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        model_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="4.00",
            blanks=blanks(),
            actor="model:test",
            source="model",
            confirm=True,
        )
    assert serialize_blank_config_version(database, model_id)["status"] == "auto_confirmed"


def test_grading_rejects_student_response_bound_to_old_blank_config(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    with database.transaction() as connection:
        old_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=0,
            max_score="4.00",
            blanks=blanks(),
            actor="teacher",
            source="teacher",
            confirm=True,
        )
        timestamp = now_iso()
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,question_region_status,created_at,updated_at
               ) VALUES('submission','task','ready','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,created_at,updated_at
               ) VALUES('processing','submission',1,'frame-v1','ready','input',1,'system',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='processing'
               WHERE id='submission'"""
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 blank_config_version_id,question_number,status,created_at,updated_at
               ) VALUES('response','submission','q1','processing','frame-v1',?,'1',
                 'recognized',?,?)""",
            (old_id, timestamp, timestamp),
        )
    changed = blanks()
    changed[0]["synonyms"] = ["第一"]
    with database.transaction() as connection:
        new_id = save_blank_config_version(
            connection,
            database,
            question_id="q1",
            frame_set_id="frame-v1",
            expected_config_version=1,
            max_score="4.00",
            blanks=changed,
            actor="teacher",
            source="teacher",
            confirm=True,
        )

    with pytest.raises(AppError) as captured:
        ensure_submission_blank_configs_current(database, "submission")
    assert captured.value.code == "BLANK_RECOGNITION_STALE"
    assert database.fetchone(
        """SELECT current_blank_config_version_id FROM question_grading_configs
           WHERE question_id='q1'"""
    ) == {"current_blank_config_version_id": new_id}
    assert database.fetchone(
        "SELECT blank_config_version_id FROM student_responses WHERE id='response'"
    ) == {"blank_config_version_id": old_id}
