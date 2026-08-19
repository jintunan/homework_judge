import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from homework_judge.db.database import (
    GRADING_SCHEMA,
    LATEST_SCHEMA_VERSION,
    QUESTION_REGION_SCHEMA,
    SCHEMA,
    STUDENT_WORK_SCHEMA,
    TEMPLATE_REGION_SCHEMA,
    Database,
    json_dumps,
    json_loads,
    now_iso,
)
from homework_judge.errors import AppError
from homework_judge.review.history import require_student_processing_ready, student_processing_gate
from homework_judge.schemas import StudentResponseCreate, StudentResponseRegion


def _unique_index_columns(
    connection: sqlite3.Connection,
    table: str,
) -> dict[tuple[str, ...], str | None]:
    indexes: dict[tuple[str, ...], str | None] = {}
    for index in connection.execute(f"PRAGMA index_list('{table}')").fetchall():
        if not index["unique"]:
            continue
        name = str(index["name"]).replace("'", "''")
        columns = tuple(
            str(row["name"])
            for row in connection.execute(f"PRAGMA index_info('{name}')").fetchall()
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (index["name"],),
        ).fetchone()
        indexes[columns] = str(sql_row["sql"]) if sql_row and sql_row["sql"] else None
    return indexes


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    database.migrate()
    database.migrate()
    row = database.fetchone("SELECT MAX(version) AS version FROM schema_version")
    assert row == {"version": LATEST_SCHEMA_VERSION}
    assert database.fetchall("SELECT version FROM schema_version ORDER BY version") == [
        {"version": version} for version in range(1, LATEST_SCHEMA_VERSION + 1)
    ]
    question_columns = {row["name"] for row in database.fetchall("PRAGMA table_info(questions)")}
    grading_columns = {row["name"] for row in database.fetchall("PRAGMA table_info(grading_runs)")}
    assert "is_duplicate" in question_columns
    assert "is_stale" in grading_columns
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_empty_database_initializes_latest_versioned_schema(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "v8.sqlite")
    database.migrate()

    assert LATEST_SCHEMA_VERSION == 11
    expected_tables = {
        "question_frame_sets",
        "question_frame_items",
        "question_frame_regions",
        "question_blank_config_versions",
        "question_blank_definition_versions",
        "student_processing_revisions",
        "student_page_alignment_revisions",
        "student_blank_responses",
        "student_auto_grading_attempts",
    }
    tables = {
        row["name"]
        for row in database.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert expected_tables <= tables

    assert "current_question_frame_set_id" in {
        row["name"] for row in database.fetchall("PRAGMA table_info(tasks)")
    }
    assert "current_blank_config_version_id" in {
        row["name"]
        for row in database.fetchall("PRAGMA table_info(question_grading_configs)")
    }
    assert "current_processing_revision_id" in {
        row["name"] for row in database.fetchall("PRAGMA table_info(student_submissions)")
    }

    with database.connect() as connection:
        pointer_targets = {
            table: {
                (row["from"], row["table"], row["to"])
                for row in connection.execute(f"PRAGMA foreign_key_list('{table}')").fetchall()
            }
            for table in ("tasks", "question_grading_configs", "student_submissions")
        }
        assert (
            "current_question_frame_set_id",
            "question_frame_sets",
            "id",
        ) in pointer_targets["tasks"]
        assert (
            "current_blank_config_version_id",
            "question_blank_config_versions",
            "id",
        ) in pointer_targets["question_grading_configs"]
        assert (
            "current_processing_revision_id",
            "student_processing_revisions",
            "id",
        ) in pointer_targets["student_submissions"]
        assert ("task_id", "version_number") in _unique_index_columns(
            connection, "question_frame_sets"
        )
        assert ("frame_set_id", "question_id") in _unique_index_columns(
            connection, "question_frame_items"
        )
        frame_region_indexes = _unique_index_columns(connection, "question_frame_regions")
        assert ("frame_item_id", "region_key") in frame_region_indexes
        assert ("frame_item_id", "sort_order") in frame_region_indexes
        assert ("question_id", "version_number") in _unique_index_columns(
            connection, "question_blank_config_versions"
        )
        assert ("blank_config_version_id", "blank_key") in _unique_index_columns(
            connection, "question_blank_definition_versions"
        )
        processing_indexes = _unique_index_columns(connection, "student_processing_revisions")
        assert ("submission_id", "revision_number") in processing_indexes
        current_processing_sql = processing_indexes[("submission_id",)]
        assert current_processing_sql is not None
        assert "WHERE is_current=1" in current_processing_sql
        assert (
            "processing_revision_id",
            "student_page_id",
            "revision_number",
        ) in _unique_index_columns(connection, "student_page_alignment_revisions")
        alignment_indexes = _unique_index_columns(
            connection, "student_page_alignment_revisions"
        )
        current_alignment_sql = alignment_indexes[
            ("processing_revision_id", "student_page_id")
        ]
        assert current_alignment_sql is not None
        assert "WHERE is_current=1" in current_alignment_sql
        assert ("processing_revision_id", "question_id") in _unique_index_columns(
            connection, "student_responses"
        )
        assert (
            "processing_revision_id",
            "question_id",
            "sort_order",
        ) in _unique_index_columns(connection, "student_question_regions")
        assert ("student_response_id", "blank_key") in _unique_index_columns(
            connection, "student_blank_responses"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_upgrades_v1_without_losing_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    timestamp = now_iso()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(1, ?)",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('existing-task','Existing','draft',?,?)""",
            (timestamp, timestamp),
        )

    database = Database(path)
    database.migrate()

    assert database.fetchone("SELECT title FROM tasks WHERE id='existing-task'") == {
        "title": "Existing"
    }
    assert database.fetchone("SELECT MAX(version) AS version FROM schema_version") == {
        "version": LATEST_SCHEMA_VERSION
    }
    table_names = {
        row["name"]
        for row in database.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'student_%'"
        )
    }
    assert table_names == {
        "student_submissions",
        "student_pages",
        "student_responses",
        "student_response_regions",
        "student_question_regions",
        "student_processing_revisions",
        "student_page_alignment_revisions",
        "student_blank_responses",
        "student_auto_grading_attempts",
    }


def test_template_region_migration_recovers_when_column_already_exists(tmp_path: Path) -> None:
    path = tmp_path / "partial-v3.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(1, ?)",
            (now_iso(),),
        )
        connection.execute(
            "ALTER TABLE questions ADD COLUMN answer_regions_json TEXT NOT NULL DEFAULT '[]'"
        )
    database = Database(path)
    database.migrate()
    assert database.fetchone("SELECT MAX(version) AS version FROM schema_version") == {
        "version": LATEST_SCHEMA_VERSION
    }


def test_student_responses_are_unique_by_processing_revision_with_legacy_fallback(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "response-index.sqlite")
    database.migrate()
    with database.connect() as connection:
        indexes = _unique_index_columns(connection, "student_responses")
    assert ("processing_revision_id", "question_id") in indexes
    legacy_index_sql = indexes[("submission_id", "question_id")]
    assert legacy_index_sql is not None
    assert "WHERE processing_revision_id IS NULL" in legacy_index_sql


def test_v4_rebuild_preserves_old_responses_regions_and_allows_duplicate_numbers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old-v3.sqlite"
    old_student_schema = STUDENT_WORK_SCHEMA.replace(
        "UNIQUE(submission_id, question_id)",
        "UNIQUE(submission_id, question_number)",
    )
    timestamp = now_iso()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO schema_version(version,applied_at) VALUES(1,?)",
            (timestamp,),
        )
        connection.executescript(old_student_schema)
        connection.execute(
            "INSERT INTO schema_version(version,applied_at) VALUES(2,?)",
            (timestamp,),
        )
        connection.execute(TEMPLATE_REGION_SCHEMA)
        connection.execute(
            "INSERT INTO schema_version(version,applied_at) VALUES(3,?)",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Task','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 1,'sha',1,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam',1,'template.jpg',100,100,'sha')"""
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('run','task','exam','succeeded','done',?)""",
            (timestamp,),
        )
        for question_id in ("question-1", "question-2"):
            connection.execute(
                """INSERT INTO questions(
                     id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                     question_type,source_pages_json,confidence,issues_json,answer_regions_json
                   ) VALUES(?,?, 'run',0,'1','1','Question','single_choice','[1]',1,'[]','[]')""",
                (question_id, "task"),
            )
        connection.execute(
            """INSERT INTO student_submissions(id,task_id,status,created_at,updated_at)
               VALUES('submission','task','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 alignment_status,created_at,updated_at
               ) VALUES('student-page','submission',1,'student.jpg',100,100,'sha','aligned',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,status,created_at,updated_at
               ) VALUES('response-1','submission','question-1','1','recognized',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,template_page_id,student_page_id,template_bbox_json,
                 student_bbox_json,created_at
               ) VALUES('region','response-1','template-page','student-page','{}','{}',?)""",
            (timestamp,),
        )

    database = Database(path)
    database.migrate()
    assert database.fetchone("SELECT question_id FROM student_responses WHERE id='response-1'") == {
        "question_id": "question-1"
    }
    assert database.fetchone(
        "SELECT student_response_id FROM student_response_regions WHERE id='region'"
    ) == {"student_response_id": "response-1"}
    database.execute(
        """INSERT INTO student_responses(
             id,submission_id,question_id,question_number,status,created_at,updated_at
           ) VALUES('response-2','submission','question-2','1','recognized',?,?)""",
        (timestamp, timestamp),
    )
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _create_v7_database_with_legacy_processing(path: Path) -> None:
    timestamp = now_iso()
    question_region = {
        "page_number": 1,
        "x": 0.02,
        "y": 0.1,
        "width": 0.96,
        "height": 0.72,
        "confidence": 1.0,
        "issues": [],
    }
    shared_blank_region = {"x": 0.28, "y": 0.16, "width": 0.62, "height": 0.2}
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES(1,?)", (timestamp,))
        connection.executescript(STUDENT_WORK_SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES(2,?)", (timestamp,))
        connection.execute(TEMPLATE_REGION_SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES(3,?)", (timestamp,))
        connection.execute("INSERT INTO schema_version VALUES(4,?)", (timestamp,))
        connection.execute(
            "ALTER TABLE questions ADD COLUMN question_regions_json TEXT NOT NULL DEFAULT '[]'"
        )
        connection.execute(
            "ALTER TABLE student_submissions ADD COLUMN question_region_status "
            "TEXT NOT NULL DEFAULT 'pending' CHECK(question_region_status IN "
            "('pending','processing','ready','needs_review','failed'))"
        )
        connection.execute(
            "ALTER TABLE student_submissions ADD COLUMN question_region_error_code TEXT"
        )
        connection.execute(
            "ALTER TABLE student_submissions ADD COLUMN question_region_error_message TEXT"
        )
        connection.executescript(QUESTION_REGION_SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES(5,?)", (timestamp,))
        connection.executescript(GRADING_SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES(6,?)", (timestamp,))
        connection.execute("INSERT INTO schema_version VALUES(7,?)", (timestamp,))

        connection.execute(
            "INSERT INTO tasks(id,title,status,created_at,updated_at) "
            "VALUES('task','Legacy task','review_pending',?,?)",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,size_bytes,
                 sha256,page_count,relative_path,created_at
               ) VALUES('exam','task','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 1,'exam-sha',1,'exam.pdf',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam',1,'template.jpg',1000,1400,'template-sha')"""
        )
        connection.execute(
            "INSERT INTO runs(id,task_id,kind,status,stage,created_at) "
            "VALUES('source','task','exam','succeeded','done',?)",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,score,source_pages_json,confidence,issues_json,
                 answer_regions_json,question_regions_json,confirmation_status
               ) VALUES('question','task','source',1,'11','11','Three blanks','fill_blank',5,
                 '[1]',1,'[]',?,?, 'confirmed')""",
            (
                json_dumps(
                    [
                        {
                            "pageNumber": 1,
                            "x": 0.28,
                            "y": 0.16,
                            "width": 0.62,
                            "height": 0.2,
                        }
                    ]
                ),
                json_dumps([question_region]),
            ),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,updated_at
               ) VALUES('question','fill_blank','5.00',1,?)""",
            (timestamp,),
        )
        for index, (blank_key, score) in enumerate(
            (("B1", "1.66"), ("B2", "1.66"), ("B3", "1.68"))
        ):
            connection.execute(
                """INSERT INTO question_blank_definitions(
                     id,question_id,blank_key,sort_order,max_score,answer_kind,
                     standard_answers_json,synonyms_json,region_json,created_at,updated_at
                   ) VALUES(?, 'question',?,?,?,?,?,'[]',?,?,?)""",
                (
                    f"blank-{index + 1}",
                    blank_key,
                    index,
                    score,
                    "text",
                    json_dumps([f"answer-{index + 1}"]),
                    json_dumps(shared_blank_region),
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            """INSERT INTO audit_events(task_id,event_type,actor,payload_json,created_at)
               VALUES('task','fill_blank_config_auto_confirmed','system',?,?)""",
            (json_dumps({"questionId": "question", "blankCount": 3}), timestamp),
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
                 template_page_id,alignment_transform_json,alignment_quality,alignment_method,
                 alignment_status,created_at,updated_at
               ) VALUES('student-page','submission',1,'student.jpg',1000,1400,'student-sha',
                 'template-page','[[1,0,0],[0,1,0],[0,0,1]]',0.99,'legacy','aligned',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_question_regions(
                 id,submission_id,question_id,sort_order,template_page_id,student_page_id,
                 template_region_json,student_polygon_json,student_bbox_json,status,issues_json,
                 created_at,updated_at
               ) VALUES('mapped-region','submission','question',0,'template-page','student-page',
                 ?,?,?,'ready','[]',?,?)""",
            (
                json_dumps(question_region),
                json_dumps([{"x": 20, "y": 140}]),
                json_dumps({"x": 20, "y": 140, "width": 960, "height": 1008}),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,question_number,recognized_text,confidence,
                 recognition_model_id,raw_recognition_json,status,created_at,updated_at
               ) VALUES('response','submission','question','11','电荷转移 遵守 CD',0.99,
                 'legacy-model','{}','recognized',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_response_regions(
                 id,student_response_id,sort_order,template_page_id,student_page_id,
                 coordinate_space,template_bbox_json,student_bbox_json,cropped_image_path,
                 created_at
               ) VALUES('evidence','response',0,'template-page','student-page','pixel',
                 '{}','{}','response.png',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,status,stage,input_hash,created_at,updated_at
               ) VALUES(
                 'grading-run','submission','task','completed','completed','old-hash',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_question_results(
                 id,grading_run_id,question_id,student_response_id,input_hash,question_type,
                 status,final_score,max_score,created_at,updated_at
               ) VALUES('grading-result','grading-run','question','response','old-question-hash',
                 'fill_blank','final','0.00','5.00',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_blank_results(
                 id,grading_question_result_id,blank_definition_id,blank_key,status,
                 recognized_answer,score,max_score,created_at,updated_at
               ) VALUES('blank-result','grading-result','blank-1','B1','incorrect','电荷转移',
                 '0.00','1.66',?,?)""",
            (timestamp, timestamp),
        )


def test_v8_migration_preserves_old_processing_generations_and_marks_them_stale(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-v7.sqlite"
    _create_v7_database_with_legacy_processing(path)
    database = Database(path)
    database.migrate()

    task = database.fetchone(
        "SELECT current_question_frame_set_id FROM tasks WHERE id='task'"
    )
    assert task is not None and task["current_question_frame_set_id"]
    frame_set_id = str(task["current_question_frame_set_id"])
    frame_set = database.fetchone("SELECT * FROM question_frame_sets WHERE id=?", (frame_set_id,))
    assert frame_set is not None
    assert (frame_set["version_number"], frame_set["status"], frame_set["source"]) == (
        1,
        "draft",
        "legacy",
    )
    frame_item = database.fetchone(
        "SELECT * FROM question_frame_items WHERE frame_set_id=? AND question_id='question'",
        (frame_set_id,),
    )
    assert frame_item is not None
    assert frame_item["status"] == "pending"
    assert database.fetchone(
        "SELECT confidence FROM question_frame_regions WHERE frame_item_id=?",
        (frame_item["id"],),
    ) == {"confidence": 1.0}
    assert json_loads(
        database.fetchone(
            "SELECT question_regions_json FROM questions WHERE id='question'"
        )["question_regions_json"],
        [],
    )[0]["confidence"] == 1.0

    config = database.fetchone(
        """SELECT v.* FROM question_blank_config_versions v
           JOIN question_grading_configs c ON c.current_blank_config_version_id=v.id
           WHERE c.question_id='question'"""
    )
    assert config is not None
    assert (config["version_number"], config["status"], config["source"]) == (
        1,
        "pending",
        "legacy",
    )
    blockers = {
        str(issue["code"])
        for issue in json_loads(config["blockers_json"], [])
        if isinstance(issue, dict) and issue.get("code")
    }
    assert {
        "missing_blank_anchor",
        "composite_region_shared",
        "answer_region_count_conflict",
        "blank_score_missing",
    } <= blockers
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM question_blank_definitions WHERE question_id='question'"
    ) == {"count": 3}
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM question_blank_definition_versions "
        "WHERE blank_config_version_id=?",
        (config["id"],),
    ) == {"count": 3}

    submission = database.fetchone(
        "SELECT current_processing_revision_id,status FROM student_submissions "
        "WHERE id='submission'"
    )
    assert submission is not None and submission["current_processing_revision_id"]
    assert submission["status"] == "ready"
    legacy_revision_id = str(submission["current_processing_revision_id"])
    legacy_revision = database.fetchone(
        "SELECT * FROM student_processing_revisions WHERE id=?", (legacy_revision_id,)
    )
    assert legacy_revision is not None
    assert legacy_revision["source"] == "legacy"
    assert legacy_revision["is_current"] == 1
    assert database.fetchone(
        "SELECT processing_revision_id FROM student_question_regions WHERE id='mapped-region'"
    ) == {"processing_revision_id": legacy_revision_id}
    assert database.fetchone(
        "SELECT processing_revision_id FROM student_responses WHERE id='response'"
    ) == {"processing_revision_id": legacy_revision_id}
    assert database.fetchone(
        "SELECT student_response_id FROM student_response_regions WHERE id='evidence'"
    ) == {"student_response_id": "response"}
    assert database.fetchone(
        "SELECT student_response_id FROM grading_question_results WHERE id='grading-result'"
    ) == {"student_response_id": "response"}
    assert database.fetchone(
        "SELECT blank_definition_id FROM grading_blank_results WHERE id='blank-result'"
    ) == {"blank_definition_id": "blank-1"}
    assert database.fetchone("SELECT is_stale FROM grading_runs WHERE id='grading-run'") == {
        "is_stale": 1
    }
    alignment = database.fetchone(
        "SELECT * FROM student_page_alignment_revisions WHERE processing_revision_id=?",
        (legacy_revision_id,),
    )
    assert alignment is not None
    assert alignment["student_page_id"] == "student-page"
    assert alignment["transform_json"] == "[[1,0,0],[0,1,0],[0,0,1]]"

    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE student_processing_revisions SET is_current=0 WHERE id=?",
            (legacy_revision_id,),
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,created_at,updated_at
               ) VALUES(
                 'revision-2','submission',2,?,'recognizing','new-hash',1,'system','[]',?,?
               )""",
            (frame_set_id, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE student_submissions SET current_processing_revision_id='revision-2' "
            "WHERE id='submission'"
        )
        connection.execute(
            """INSERT INTO student_question_regions(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,sort_order,
                 template_page_id,student_page_id,template_region_json,student_polygon_json,
                 student_bbox_json,status,issues_json,created_at,updated_at
               ) VALUES('mapped-region-2','submission','question','revision-2',?,0,
                 'template-page','student-page','{}','[]','{}','ready','[]',?,?)""",
            (frame_set_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 blank_config_version_id,question_number,status,created_at,updated_at
               ) VALUES('response-2','submission','question','revision-2',?,?,'11',
                 'recognized',?,?)""",
            (frame_set_id, config["id"], timestamp, timestamp),
        )

    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_question_regions WHERE question_id='question'"
    ) == {"count": 2}
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM student_responses WHERE question_id='question'"
    ) == {"count": 2}
    assert database.fetchone(
        "SELECT is_current FROM student_processing_revisions WHERE id=?", (legacy_revision_id,)
    ) == {"is_current": 0}
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v8_legacy_task_is_explicitly_blocked_until_frames_and_blank_config_are_reviewed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-history-gate.sqlite"
    _create_v7_database_with_legacy_processing(path)
    database = Database(path)
    database.migrate()

    gate = student_processing_gate(database, "task")

    assert gate["ready"] is False
    assert gate["legacyRecovery"] == {
        "required": True,
        "frameSetSource": "legacy",
        "hasLegacyBlankConfig": True,
        "legacyProcessingCount": 1,
        "readyForReprocess": False,
    }
    assert any(
        issue["code"] == "LEGACY_BLANK_CONFIG_CONFIRMATION_REQUIRED"
        for issue in gate["blankConfigIssues"]
    )
    with pytest.raises(AppError) as error:
        require_student_processing_ready(database, "task")
    assert getattr(error.value, "code", None) == "QUESTION_FRAMES_NOT_CONFIRMED"


def test_v8_legacy_recovery_warning_clears_after_explicit_new_flow_is_current(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-history-recovered.sqlite"
    _create_v7_database_with_legacy_processing(path)
    database = Database(path)
    database.migrate()
    timestamp = now_iso()

    with database.transaction() as connection:
        connection.execute(
            """UPDATE question_frame_sets SET status='confirmed',confirmed_at=?,
               confirmed_by='teacher' WHERE task_id='task'""",
            (timestamp,),
        )
        connection.execute(
            """UPDATE question_frame_items SET status='confirmed',confirmed_at=?,
               confirmed_by='teacher'""",
            (timestamp,),
        )
        connection.execute(
            """UPDATE question_blank_config_versions
               SET status='teacher_confirmed',source='teacher',blockers_json='[]',
                   confirmed_at=?,confirmed_by='teacher' WHERE question_id='question'""",
            (timestamp,),
        )
        frame_set_id = connection.execute(
            "SELECT current_question_frame_set_id FROM tasks WHERE id='task'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE student_processing_revisions SET is_current=0 WHERE submission_id='submission'"
        )
        connection.execute(
            """INSERT INTO student_processing_revisions(
                 id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                 source,issues_json,started_at,finished_at,created_at,updated_at
               ) VALUES('new-processing','submission',2,?,'ready','new-input',1,'system',
                        '[]',?,?,?,?)""",
            (frame_set_id, timestamp, timestamp, timestamp, timestamp),
        )
        connection.execute(
            """UPDATE student_submissions SET current_processing_revision_id='new-processing'
               WHERE id='submission'"""
        )

    gate = student_processing_gate(database, "task")

    assert gate["ready"] is True
    assert gate["legacyRecovery"] == {
        "required": False,
        "frameSetSource": "legacy",
        "hasLegacyBlankConfig": False,
        "legacyProcessingCount": 1,
        "readyForReprocess": True,
    }


def test_interrupted_student_processing_becomes_retryable_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "interrupted.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task','Task','review_pending',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,status,created_at,updated_at
               ) VALUES('submission','task','recognizing',?,?)""",
            (timestamp, timestamp),
        )
    assert database.interrupt_student_processing() == 1
    row = database.fetchone("SELECT * FROM student_submissions WHERE id='submission'")
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_code"] == "STUDENT_RUN_INTERRUPTED"


def test_student_response_regions_keep_original_page_coordinates(tmp_path: Path) -> None:
    database = Database(tmp_path / "student-work.sqlite")
    database.migrate()
    timestamp = now_iso()
    transform = [[1.0, 0.0, 12.0], [0.0, 1.0, 8.0], [0.0, 0.0, 1.0]]
    template_boxes = [
        {"x": 120, "y": 400, "width": 520, "height": 90},
        {"x": 120, "y": 500, "width": 520, "height": 130},
    ]
    original_student_boxes = [
        {"x": 134, "y": 412, "width": 516, "height": 92},
        {"x": 136, "y": 511, "width": 517, "height": 132},
    ]

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tasks(id,title,status,created_at,updated_at)
               VALUES('task-1','Student work','draft',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO documents(
                 id,task_id,role,original_name,stored_name,mime_type,extension,
                 size_bytes,sha256,page_count,relative_path,created_at
               ) VALUES(
                 'exam-document','task-1','exam','exam.pdf','exam.pdf','application/pdf','.pdf',
                 100,'exam-sha',1,'task-1/exam.pdf',?
               )""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO pages(id,document_id,page_number,image_path,width,height,sha256)
               VALUES('template-page','exam-document',1,'task-1/template-page.jpg',1000,1400,
                      'template-sha')"""
        )
        connection.execute(
            """INSERT INTO student_submissions(
                 id,task_id,student_identifier,student_name,page_count,status,created_at,updated_at
               ) VALUES('submission-1','task-1','20260001','Student One',1,'recognizing',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_pages(
                 id,submission_id,page_number,original_image_path,width,height,sha256,
                 template_page_id,alignment_transform_json,alignment_quality,alignment_method,
                 alignment_status,created_at,updated_at
               ) VALUES(
                 'student-page','submission-1',1,'task-1/students/original-page-1.jpg',1024,1434,
                 'student-sha','template-page',?,?,?,'aligned',?,?
               )""",
            (json_dumps(transform), 0.96, "orb-homography", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO student_responses(
                 id,submission_id,question_number,recognized_text,confidence,status,
                 created_at,updated_at
               ) VALUES('response-1','submission-1','12','F=qE','0.91','recognized',?,?)""",
            (timestamp, timestamp),
        )
        for index, (template_box, student_box) in enumerate(
            zip(template_boxes, original_student_boxes, strict=True)
        ):
            connection.execute(
                """INSERT INTO student_response_regions(
                     id,student_response_id,sort_order,template_page_id,student_page_id,
                     coordinate_space,template_bbox_json,student_bbox_json,created_at
                   ) VALUES(?,?,?,?,?,'pixel',?,?,?)""",
                (
                    f"region-{index}",
                    "response-1",
                    index,
                    "template-page",
                    "student-page",
                    json_dumps(template_box),
                    json_dumps(student_box),
                    timestamp,
                ),
            )

    page = database.fetchone("SELECT * FROM student_pages WHERE id='student-page'")
    assert page is not None
    assert page["page_number"] == 1
    assert page["original_image_path"] == "task-1/students/original-page-1.jpg"
    assert json_loads(page["alignment_transform_json"], None) == transform
    assert page["alignment_quality"] == pytest.approx(0.96)

    response = database.fetchone("SELECT * FROM student_responses WHERE id='response-1'")
    assert response is not None
    assert response["question_number"] == "12"
    assert response["recognized_text"] == "F=qE"
    assert response["confidence"] == pytest.approx(0.91)

    regions = database.fetchall(
        """SELECT * FROM student_response_regions
           WHERE student_response_id='response-1' ORDER BY sort_order"""
    )
    assert len(regions) == 2
    assert [json_loads(row["template_bbox_json"], {}) for row in regions] == template_boxes
    assert [json_loads(row["student_bbox_json"], {}) for row in regions] == (original_student_boxes)
    assert all(row["student_page_id"] == "student-page" for row in regions)


def test_student_response_schema_requires_paired_regions() -> None:
    region = StudentResponseRegion.model_validate(
        {
            "templatePageId": "template-page",
            "studentPageId": "student-page",
            "coordinateSpace": "normalized",
            "templateBox": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1},
            "studentBox": {"x": 0.11, "y": 0.21, "width": 0.49, "height": 0.11},
        }
    )
    response = StudentResponseCreate(
        questionNumber="12",
        recognizedText="F=qE",
        confidence=0.91,
        regions=[region],
    )
    assert response.regions[0].studentPageId == "student-page"

    with pytest.raises(ValidationError):
        StudentResponseCreate(questionNumber="12", regions=[])
    with pytest.raises(ValidationError):
        StudentResponseRegion.model_validate(
            {
                "templatePageId": "template-page",
                "studentPageId": "student-page",
                "coordinateSpace": "normalized",
                "templateBox": {"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.1},
                "studentBox": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1},
            }
        )
