import sqlite3
from pathlib import Path

import pytest

from homework_judge.db.database import Database, now_iso

EXPECTED_TABLES = {
    "question_grading_configs",
    "question_blank_definitions",
    "question_blank_config_versions",
    "question_blank_definition_versions",
    "student_blank_responses",
    "rubric_versions",
    "rubric_points",
    "rubric_dependencies",
    "grading_runs",
    "grading_question_results",
    "grading_blank_results",
    "grading_point_results",
    "grading_review_items",
    "grading_events",
    "grading_artifacts",
}


def test_grading_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "grading.sqlite")
    database.migrate()
    database.migrate()
    tables = {
        row["name"]
        for row in database.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= tables
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_grading_scores_are_stored_as_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "scores.sqlite")
    database.migrate()
    columns = {
        row["name"]: row["type"]
        for row in database.fetchall("PRAGMA table_info(grading_question_results)")
    }
    assert columns["raw_score"] == "TEXT"
    assert columns["final_score"] == "TEXT"
    assert columns["max_score"] == "TEXT"


def test_v10_migration_preserves_point_rows_and_review_foreign_keys(tmp_path: Path) -> None:
    database = Database(tmp_path / "partial-score-migration.sqlite")
    timestamp = now_iso()
    with database.connect() as connection:
        connection.executescript(
            """CREATE TABLE schema_version(
                 version INTEGER PRIMARY KEY,
                 applied_at TEXT NOT NULL
               );
               CREATE TABLE grading_question_results(id TEXT PRIMARY KEY);
               CREATE TABLE rubric_points(id TEXT PRIMARY KEY);
               CREATE TABLE grading_point_results(
                 id TEXT PRIMARY KEY,
                 grading_question_result_id TEXT NOT NULL
                   REFERENCES grading_question_results(id) ON DELETE CASCADE,
                 rubric_point_id TEXT NOT NULL REFERENCES rubric_points(id),
                 point_key TEXT NOT NULL,
                 direct_status TEXT NOT NULL
                   CHECK(direct_status IN ('satisfied','failed','unable')),
                 final_status TEXT NOT NULL CHECK(final_status IN
                   ('satisfied','failed','unable','blocked_by_dependency')),
                 direct_score TEXT NOT NULL,
                 final_score TEXT NOT NULL,
                 max_score TEXT NOT NULL,
                 blocked_by TEXT,
                 evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                 reason TEXT NOT NULL DEFAULT '',
                 confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
                 model_result_json TEXT NOT NULL DEFAULT '{}',
                 created_at TEXT NOT NULL,
                 updated_at TEXT NOT NULL,
                 UNIQUE(grading_question_result_id, point_key)
               );
               CREATE TABLE grading_review_items(
                 id TEXT PRIMARY KEY,
                 grading_point_result_id TEXT
                   REFERENCES grading_point_results(id) ON DELETE CASCADE
               );"""
        )
        connection.executemany(
            "INSERT INTO schema_version(version,applied_at) VALUES(?,?)",
            [(version, timestamp) for version in range(1, 10)],
        )
        connection.execute("INSERT INTO grading_question_results VALUES('result')")
        connection.execute("INSERT INTO rubric_points VALUES('point')")
        connection.execute(
            """INSERT INTO grading_point_results(
                 id,grading_question_result_id,rubric_point_id,point_key,direct_status,
                 final_status,direct_score,final_score,max_score,created_at,updated_at
               ) VALUES(
                 'point-result','result','point','P1','satisfied','satisfied',
                 '2.00','2.00','2.00',?,?
               )""",
            (timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO grading_review_items VALUES('review','point-result')"
        )
        connection.commit()

    database.migrate()

    database.execute(
        """UPDATE grading_point_results
           SET direct_status='partial',final_status='partial',
               direct_score='1.00',final_score='1.00'
           WHERE id='point-result'"""
    )
    assert database.fetchone(
        "SELECT direct_status,final_status,direct_score FROM grading_point_results"
    ) == {
        "direct_status": "partial",
        "final_status": "partial",
        "direct_score": "1.00",
    }
    assert database.fetchone("SELECT * FROM grading_review_items") == {
        "id": "review",
        "grading_point_result_id": "point-result",
    }
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v11_migration_derives_balanced_frozen_rubric_without_rewriting_legacy(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "balanced-rubric-migration.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute("DELETE FROM schema_version WHERE version=11")
        connection.execute(
            "INSERT INTO tasks(id,title,status,created_at,updated_at) "
            "VALUES('task','T','review_pending',?,?)",
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
                 stem,question_type,score,source_pages_json,confidence,issues_json
               ) VALUES(
                 'question','task','source',0,'1','1','calculate','calculation',10,
                 '[]',1,'[]'
               )"""
        )
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,confirmed_by,
                 frozen_at,created_at,updated_at
               ) VALUES(
                 'legacy-rubric','question',1,'frozen','10.00','manual','teacher',?,?,?
               )""",
            (timestamp, timestamp, timestamp),
        )
        legacy_points = [
            ("legacy-p1", "P1", 0, "formula", "4.00"),
            ("legacy-p2", "P2", 1, "substitution", "3.00"),
            ("legacy-p3", "P3", 2, "calculation", "3.00"),
        ]
        connection.executemany(
            """INSERT INTO rubric_points(
                 id,rubric_version_id,point_key,sort_order,criterion,score,
                 created_at,updated_at
               ) VALUES(?,'legacy-rubric',?,?,?,?,?,?)""",
            [(*point, timestamp, timestamp) for point in legacy_points],
        )
        connection.execute(
            """INSERT INTO rubric_dependencies(
                 rubric_version_id,point_id,depends_on_point_id,created_at
               ) VALUES('legacy-rubric','legacy-p2','legacy-p1',?)""",
            (timestamp,),
        )

    database.migrate()
    database.migrate()

    versions = database.fetchall(
        "SELECT * FROM rubric_versions WHERE question_id='question' ORDER BY version_number"
    )
    assert len(versions) == 2
    assert versions[0]["id"] == "legacy-rubric"
    assert versions[0]["content_hash"] is None
    assert versions[1]["status"] == "frozen"
    assert versions[1]["content_hash"]
    assert versions[1]["prompt_version"].endswith(":legacy-migration")
    migrated_points = database.fetchall(
        """SELECT point_key,score FROM rubric_points
           WHERE rubric_version_id=? ORDER BY sort_order""",
        (versions[1]["id"],),
    )
    assert migrated_points == [
        {"point_key": "P1", "score": "3.20"},
        {"point_key": "P2", "score": "2.40"},
        {"point_key": "P3", "score": "2.40"},
        {"point_key": "FINAL_ANSWER", "score": "2.00"},
    ]
    final_dependencies = database.fetchall(
        """SELECT d.* FROM rubric_dependencies d
           JOIN rubric_points p ON p.id=d.point_id
           WHERE d.rubric_version_id=? AND p.point_key='FINAL_ANSWER'""",
        (versions[1]["id"],),
    )
    assert final_dependencies == []
    legacy_scores = database.fetchall(
        """SELECT point_key,score FROM rubric_points
           WHERE rubric_version_id='legacy-rubric' ORDER BY sort_order"""
    )
    assert [row["score"] for row in legacy_scores] == ["4.00", "3.00", "3.00"]
    audit = database.fetchone(
        "SELECT * FROM audit_events WHERE event_type='rubric_policy_migrated'"
    )
    assert audit is not None


def test_versioned_blank_responses_keep_grading_compatibility_foreign_keys(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "blank-response-fks.sqlite")
    database.migrate()
    with database.connect() as connection:
        blank_response_targets = {
            (row["from"], row["table"], row["to"])
            for row in connection.execute(
                "PRAGMA foreign_key_list(student_blank_responses)"
            ).fetchall()
        }
        grading_targets = {
            (row["from"], row["table"], row["to"])
            for row in connection.execute("PRAGMA foreign_key_list(grading_blank_results)")
            .fetchall()
        }
    assert (
        "blank_definition_id",
        "question_blank_definition_versions",
        "id",
    ) in blank_response_targets
    assert ("student_response_id", "student_responses", "id") in blank_response_targets
    assert (
        "blank_definition_id",
        "question_blank_definitions",
        "id",
    ) in grading_targets


def test_open_review_item_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "review.sqlite")
    database.migrate()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO tasks(id,title,status,created_at,updated_at) VALUES('t','T','draft',?,?)",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO runs(id,task_id,kind,status,stage,created_at)
               VALUES('source','t','x','done','done',?)""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO questions(
                 id,task_id,source_run_id,sort_order,detected_number,normalized_number,stem,
                 question_type,source_pages_json,confidence,issues_json
               ) VALUES('q','t','source',1,'1','1','Q','single_choice','[]',1,'[]')"""
        )
        connection.execute(
            """INSERT INTO student_submissions(id,task_id,status,created_at,updated_at)
               VALUES('s','t','ready',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_runs(
                 id,submission_id,task_id,input_hash,created_at,updated_at
               ) VALUES('g','s','t','hash',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_question_results(
                 id,grading_run_id,question_id,input_hash,question_type,max_score,created_at,updated_at
               ) VALUES('r','g','q','qh','single_choice','1.00',?,?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO grading_review_items(
                 id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
               ) VALUES('review-1','g','r','MISSING_EVIDENCE',?,?)""",
            (timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO grading_review_items(
                     id,grading_run_id,grading_question_result_id,reason,created_at,updated_at
                   ) VALUES('review-2','g','r','MISSING_EVIDENCE',?,?)""",
                (timestamp, timestamp),
            )


def test_frozen_rubric_reference_prevents_deletion(tmp_path: Path) -> None:
    database = Database(tmp_path / "rubric.sqlite")
    database.migrate()
    with database.connect() as connection:
        artifact_indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(grading_artifacts)")
        }
    assert "idx_grading_artifacts_current" in artifact_indexes
