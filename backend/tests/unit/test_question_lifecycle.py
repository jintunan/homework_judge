from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from homework_judge.config import Settings
from homework_judge.db.database import Database, now_iso
from homework_judge.errors import AppError
from homework_judge.review.invalidation import (
    ensure_blank_config_is_current,
    invalidate_blank_config_dependents,
)
from homework_judge.review.lifecycle import mark_question_duplicate, restore_question


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            teacher_name="测试教师",
            auto_match_threshold=0.82,
            auto_match_margin=0.08,
        ),
    )


def _database(path: Path) -> Database:
    database = Database(path)
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','测试','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('run','task','exam_recognition','succeeded','exam_recognition',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(id,task_id,source_run_id,sort_order,detected_number,
               normalized_number,stem,options_json,question_type,score,source_pages_json,
               confidence,issues_json,confirmation_status)
               VALUES('q1','task','run',0,'1','1','题干','[]','calculation',5,'[1]',1,'[]','pending')"""
        )
        connection.execute(
            """INSERT INTO answer_entries(id,task_id,source_run_id,sort_order,number_hint,
               normalized_number,stem_hint,answer,explanation,source_pages_json,confidence,issues_json)
               VALUES('a1','task','run',0,'1','1','','答案','','[1]',1,'[]')"""
        )
        connection.execute(
            """INSERT INTO matches(id,task_id,question_id,answer_entry_id,method,number_score,
               stem_score,order_score,total_score,reasons_json,status,updated_at)
               VALUES('m1','task','q1','a1','number_exact',1,0,1,1,'[]','suggested',?)""",
            (timestamp,),
        )
    return database


def test_marks_and_restores_duplicate_without_deleting_question(tmp_path: Path) -> None:
    database = _database(tmp_path / "lifecycle.sqlite")
    marked = mark_question_duplicate(database, _settings(), "q1")
    assert marked["isDuplicate"] is True
    assert marked["answerReleased"] is True
    assert database.fetchone("SELECT is_duplicate FROM questions WHERE id='q1'") == {
        "is_duplicate": 1
    }
    assert database.fetchone("SELECT answer_entry_id,status FROM matches WHERE id='m1'") == {
        "answer_entry_id": None,
        "status": "excluded",
    }

    restored = restore_question(database, _settings(), "q1")
    assert restored["isDuplicate"] is False
    assert restored["matchStatus"] == "suggested"
    assert database.fetchone("SELECT is_duplicate FROM questions WHERE id='q1'") == {
        "is_duplicate": 0
    }
    assert database.fetchone("SELECT answer_entry_id,method FROM matches WHERE id='m1'") == {
        "answer_entry_id": "a1",
        "method": "number_exact",
    }
    events = database.fetchall(
        "SELECT event_type FROM audit_events WHERE task_id='task' ORDER BY id"
    )
    assert events == [
        {"event_type": "question_marked_duplicate"},
        {"event_type": "question_restored"},
    ]


def test_duplicate_state_changes_are_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path / "idempotent.sqlite")
    mark_question_duplicate(database, _settings(), "q1")
    mark_question_duplicate(database, _settings(), "q1")
    restore_question(database, _settings(), "q1")
    restore_question(database, _settings(), "q1")
    count = database.fetchone("SELECT COUNT(*) AS count FROM audit_events WHERE task_id='task'")
    assert count == {"count": 2}


def test_active_student_processing_blocks_duplicate_change(tmp_path: Path) -> None:
    database = _database(tmp_path / "active.sqlite")
    timestamp = now_iso()
    database.execute(
        """INSERT INTO student_submissions(id,task_id,status,created_at,updated_at)
           VALUES('submission','task','recognizing',?,?)""",
        (timestamp, timestamp),
    )
    with pytest.raises(AppError) as raised:
        mark_question_duplicate(database, _settings(), "q1")
    assert raised.value.code == "STUDENT_PROCESSING_ACTIVE"
    assert database.fetchone("SELECT is_duplicate FROM questions WHERE id='q1'") == {
        "is_duplicate": 0
    }


def _seed_blank_config_dependents(database: Database) -> None:
    timestamp = now_iso()
    with database.transaction() as connection:
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
               ) VALUES('page','exam',1,'page.jpg',1000,1400,'page-sha')"""
        )
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,revision,source,content_hash,created_by,
                 created_at,updated_at,confirmed_at,confirmed_by
               ) VALUES('frame','task',1,'confirmed',1,'teacher','frame-hash','teacher',
                 ?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id='frame' WHERE id='task'"
        )
        connection.execute(
            """INSERT INTO question_blank_config_versions(
                 id,question_id,version_number,frame_set_id,status,source,signals_json,
                 blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES('blank-v1','q1',1,'frame','teacher_confirmed','teacher','{}','[]',
                 '[]','blank-hash-v1','teacher',?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,
                 current_blank_config_version_id,updated_at
               ) VALUES('q1','fill_blank','5',1,'blank-v1',?)""",
            (timestamp,),
        )
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
               ) VALUES('processing','submission',1,'frame','ready','input',1,'system',?,?)""",
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
               ) VALUES('response','submission','q1','processing','frame','blank-v1','1',
                 'recognized',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
               ) VALUES('grading','submission','task','completed','completed','input',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_artifacts(
                 id,grading_run_id,artifact_type,result_revision,status,created_at,updated_at
               ) VALUES('artifact','grading','annotation',0,'current',?,?)""",
            (timestamp, timestamp),
        )


def test_blank_config_change_detaches_current_recognition_and_stales_grading(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "blank-invalidation.sqlite")
    _seed_blank_config_dependents(database)

    with database.transaction() as connection:
        invalidate_blank_config_dependents(connection, "task", "q1", "blank-v1")

    assert database.fetchone(
        "SELECT is_current,status FROM student_processing_revisions WHERE id='processing'"
    ) == {"is_current": 0, "status": "ready"}
    assert database.fetchone(
        """SELECT current_processing_revision_id,status,error_code
           FROM student_submissions WHERE id='submission'"""
    ) == {
        "current_processing_revision_id": None,
        "status": "uploaded",
        "error_code": "BLANK_CONFIG_CHANGED",
    }
    assert database.fetchone("SELECT is_stale FROM grading_runs WHERE id='grading'") == {
        "is_stale": 1
    }
    assert database.fetchone("SELECT status FROM grading_artifacts WHERE id='artifact'") == {
        "status": "stale"
    }
    assert database.fetchone("SELECT COUNT(*) AS value FROM student_responses") == {"value": 1}


def test_late_blank_result_cannot_commit_after_config_pointer_changes(tmp_path: Path) -> None:
    database = _database(tmp_path / "late-blank.sqlite")
    _seed_blank_config_dependents(database)
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO question_blank_config_versions(
                 id,question_id,version_number,frame_set_id,status,source,signals_json,
                 blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
                 confirmed_at,confirmed_by
               ) VALUES('blank-v2','q1',2,'frame','teacher_confirmed','teacher','{}','[]',
                 '[]','blank-hash-v2','teacher',?,?,?,'teacher')""",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE question_grading_configs
               SET config_version=2,current_blank_config_version_id='blank-v2'
               WHERE question_id='q1'"""
        )
        with pytest.raises(AppError) as captured:
            ensure_blank_config_is_current(connection, "q1", "blank-v1")

    assert captured.value.code == "BLANK_CONFIG_SUPERSEDED"
    assert captured.value.details == {
        "questionId": "q1",
        "capturedBlankConfigVersionId": "blank-v1",
        "currentBlankConfigVersionId": "blank-v2",
    }
