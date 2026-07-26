from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import aiosqlite

from ..errors import AppError
from .database import Database

CURRENT_SCHEMA_VERSION = 3
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def _table_exists(connection: aiosqlite.Connection, name: str) -> bool:
    cursor = await connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _columns(connection: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await connection.execute(f'PRAGMA table_info("{table}")')
    rows = list(await cursor.fetchall())
    await cursor.close()
    return {str(row[1]) for row in rows}


async def _add_column(
    connection: aiosqlite.Connection,
    table: str,
    name: str,
    declaration: str,
) -> None:
    if name not in await _columns(connection, table):
        await connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}')


async def _execute_all(
    connection: aiosqlite.Connection,
    statements: Iterable[str],
) -> None:
    for statement in statements:
        await connection.execute(statement)


async def _foreign_key_check(connection: aiosqlite.Connection) -> None:
    cursor = await connection.execute("PRAGMA foreign_key_check")
    rows = list(await cursor.fetchall())
    await cursor.close()
    if rows:
        sample = [tuple(row) for row in rows[:5]]
        raise AppError(
            500,
            "DATABASE_MIGRATION_FK_FAILED",
            "SQLite 迁移后外键检查失败",
            {"violations": sample, "total": len(rows)},
        )


async def _create_answer_config_tables(connection: aiosqlite.Connection) -> None:
    await _execute_all(
        connection,
        (
            """
            CREATE TABLE IF NOT EXISTS answer_config_versions (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
              version_number INTEGER NOT NULL CHECK (version_number > 0),
              status TEXT NOT NULL CHECK (
                status IN ('draft', 'review_pending', 'approved', 'superseded')
              ),
              answer_mode TEXT NOT NULL CHECK (
                answer_mode IN ('reference_upload', 'agent_search')
              ),
              extraction_issues_json TEXT NOT NULL DEFAULT '[]',
              unresolved_issue_count INTEGER NOT NULL DEFAULT 0
                CHECK (unresolved_issue_count >= 0),
              created_at TEXT NOT NULL,
              approved_by TEXT,
              approved_at TEXT,
              UNIQUE(task_id, version_number)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS answer_question_drafts (
              id TEXT PRIMARY KEY,
              version_id TEXT NOT NULL REFERENCES answer_config_versions(id) ON DELETE CASCADE,
              number TEXT NOT NULL,
              question_text TEXT NOT NULL,
              type TEXT NOT NULL CHECK (
                type IN ('choice', 'fill_blank', 'short_answer', 'calculation')
              ),
              max_score REAL NOT NULL CHECK (max_score > 0),
              auto_answer TEXT NOT NULL DEFAULT '',
              auto_scoring_points_json TEXT NOT NULL DEFAULT '[]',
              auto_reason TEXT NOT NULL DEFAULT '',
              source_type TEXT CHECK (
                source_type IN ('reference_extracted', 'web_searched', 'model_generated')
              ),
              confidence REAL NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
              needs_attention INTEGER NOT NULL DEFAULT 0,
              parse_issues_json TEXT NOT NULL DEFAULT '[]',
              normalization_json TEXT NOT NULL DEFAULT '[]',
              requires_correction INTEGER NOT NULL DEFAULT 0,
              teacher_number TEXT,
              teacher_type TEXT CHECK (
                teacher_type IS NULL OR teacher_type IN (
                  'choice', 'fill_blank', 'short_answer', 'calculation'
                )
              ),
              teacher_max_score REAL,
              teacher_answer TEXT,
              teacher_scoring_points_json TEXT,
              rejection_reason TEXT,
              review_status TEXT NOT NULL DEFAULT 'pending' CHECK (
                review_status IN ('pending', 'approved', 'rejected', 'failed')
              ),
              updated_by TEXT,
              latest_run_id TEXT,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(version_id, number)
            )
            """,
            _answer_runs_table_sql("answer_resolution_runs"),
            """
            CREATE TABLE IF NOT EXISTS search_sources (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL REFERENCES answer_resolution_runs(id) ON DELETE CASCADE,
              draft_question_id TEXT NOT NULL
                REFERENCES answer_question_drafts(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              url TEXT NOT NULL,
              snippet TEXT NOT NULL DEFAULT '',
              rank INTEGER NOT NULL DEFAULT 0,
              retrieved_at TEXT NOT NULL
            )
            """,
        ),
    )


def _answer_runs_table_sql(name: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {name} (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
          version_id TEXT NOT NULL REFERENCES answer_config_versions(id) ON DELETE CASCADE,
          draft_question_id TEXT REFERENCES answer_question_drafts(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (
            kind IN (
              'exam_extraction', 'reference_extraction', 'structure_repair',
              'web_search', 'model_generation'
            )
          ),
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          request_snapshot_json TEXT,
          raw_response_json TEXT,
          parsed_output_json TEXT,
          usage_json TEXT,
          status TEXT NOT NULL CHECK (
            status IN ('running', 'succeeded', 'parse_failed', 'request_failed')
          ),
          error_code TEXT,
          error_message TEXT,
          started_at TEXT NOT NULL,
          finished_at TEXT
        )
    """


async def _migrate_legacy_to_v2_shape(connection: aiosqlite.Connection) -> None:
    await _create_answer_config_tables(connection)
    await _add_column(
        connection,
        "grading_tasks",
        "answer_mode",
        "TEXT NOT NULL DEFAULT 'agent_search'",
    )
    await _add_column(
        connection,
        "grading_tasks",
        "reference_answer_file_id",
        "TEXT REFERENCES stored_files(id)",
    )
    await _add_column(
        connection,
        "grading_tasks",
        "answer_config_status",
        "TEXT NOT NULL DEFAULT 'not_started'",
    )
    await _add_column(
        connection,
        "grading_tasks",
        "active_answer_version_id",
        "TEXT REFERENCES answer_config_versions(id)",
    )
    await _add_column(
        connection,
        "submissions",
        "answer_version_id",
        "TEXT REFERENCES answer_config_versions(id)",
    )

    await connection.execute(
        """
        UPDATE grading_tasks
        SET subject = CASE
          WHEN subject IN ('high_school_physics', '高中物理') THEN 'high_school_physics'
          ELSE 'middle_school_math'
        END
        """
    )

    cursor = await connection.execute(
        """
        SELECT t.id, t.created_at,
               (SELECT COUNT(*) FROM questions q WHERE q.task_id = t.id) AS question_count
        FROM grading_tasks t
        """
    )
    tasks = await cursor.fetchall()
    await cursor.close()
    version_by_task: dict[str, str] = {}
    for task in tasks:
        if int(task["question_count"]) == 0:
            continue
        task_id = str(task["id"])
        version_id = str(uuid4())
        version_by_task[task_id] = version_id
        await connection.execute(
            """
            INSERT INTO answer_config_versions
              (id, task_id, version_number, status, answer_mode,
               extraction_issues_json, unresolved_issue_count,
               created_at, approved_by, approved_at)
            VALUES (?, ?, 1, 'approved', 'agent_search', '[]', 0, ?, '迁移导入', ?)
            """,
            (version_id, task_id, task["created_at"], task["created_at"]),
        )
        await connection.execute(
            """
            UPDATE grading_tasks
            SET answer_config_status = 'approved', active_answer_version_id = ?
            WHERE id = ?
            """,
            (version_id, task_id),
        )
        await connection.execute(
            "UPDATE submissions SET answer_version_id = ? WHERE task_id = ?",
            (version_id, task_id),
        )

    await _rebuild_legacy_files(connection)
    await _rebuild_legacy_questions(connection, version_by_task)


async def _rebuild_legacy_files(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE stored_files_v3 (
          id TEXT PRIMARY KEY,
          task_id TEXT,
          kind TEXT NOT NULL CHECK (kind IN ('template', 'reference_answer', 'submission')),
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL UNIQUE,
          mime_type TEXT NOT NULL,
          size INTEGER NOT NULL CHECK (size >= 0),
          relative_path TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        )
        """
    )
    await connection.execute(
        """
        INSERT INTO stored_files_v3
          (id, task_id, kind, original_name, stored_name, mime_type, size,
           relative_path, created_at)
        SELECT id, task_id, kind, original_name, stored_name, mime_type, size,
               relative_path, created_at
        FROM stored_files
        """
    )
    await connection.execute("DROP TABLE stored_files")
    await connection.execute("ALTER TABLE stored_files_v3 RENAME TO stored_files")


async def _rebuild_legacy_questions(
    connection: aiosqlite.Connection,
    version_by_task: dict[str, str],
) -> None:
    await connection.execute(
        """
        CREATE TABLE questions_v3 (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
          answer_version_id TEXT REFERENCES answer_config_versions(id) ON DELETE RESTRICT,
          source_draft_id TEXT,
          number TEXT NOT NULL,
          question_text TEXT NOT NULL DEFAULT '',
          type TEXT NOT NULL CHECK (
            type IN ('choice', 'fill_blank', 'short_answer', 'calculation')
          ),
          max_score REAL NOT NULL CHECK (max_score > 0),
          standard_answer TEXT NOT NULL,
          scoring_points_json TEXT NOT NULL DEFAULT '[]',
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(answer_version_id, number)
        )
        """
    )
    cursor = await connection.execute("SELECT * FROM questions")
    questions = await cursor.fetchall()
    await cursor.close()
    for question in questions:
        task_id = str(question["task_id"])
        await connection.execute(
            """
            INSERT INTO questions_v3
              (id, task_id, answer_version_id, source_draft_id, number,
               question_text, type, max_score, standard_answer,
               scoring_points_json, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question["id"],
                task_id,
                version_by_task.get(task_id),
                question["number"],
                question["type"],
                question["max_score"],
                question["standard_answer"],
                question["scoring_points_json"],
                question["sort_order"],
                question["created_at"],
                question["updated_at"],
            ),
        )

    await connection.execute(
        """
        CREATE TABLE question_reviews_v3 (
          id TEXT PRIMARY KEY,
          submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
          question_id TEXT NOT NULL REFERENCES questions_v3(id) ON DELETE RESTRICT,
          model_run_id TEXT REFERENCES model_runs(id),
          model_answer TEXT NOT NULL DEFAULT '',
          model_score REAL NOT NULL DEFAULT 0,
          model_reason TEXT NOT NULL DEFAULT '',
          confidence REAL NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
          final_answer TEXT NOT NULL DEFAULT '',
          final_score REAL NOT NULL DEFAULT 0,
          teacher_comment TEXT NOT NULL DEFAULT '',
          review_status TEXT NOT NULL DEFAULT 'pending' CHECK (
            review_status IN ('pending', 'needs_attention', 'reviewed')
          ),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(submission_id, question_id)
        )
        """
    )
    await connection.execute(
        """
        INSERT INTO question_reviews_v3
        SELECT id, submission_id, question_id, model_run_id, model_answer,
               model_score, model_reason, confidence, final_answer,
               final_score, teacher_comment, review_status, created_at, updated_at
        FROM question_reviews
        """
    )
    await connection.execute("DROP TABLE question_reviews")
    await connection.execute("DROP TABLE questions")
    await connection.execute("ALTER TABLE questions_v3 RENAME TO questions")
    await connection.execute("ALTER TABLE question_reviews_v3 RENAME TO question_reviews")


async def _migrate_v2_to_v3(connection: aiosqlite.Connection) -> None:
    await _add_column(
        connection,
        "answer_config_versions",
        "extraction_issues_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    await _add_column(
        connection,
        "answer_config_versions",
        "unresolved_issue_count",
        "INTEGER NOT NULL DEFAULT 0 CHECK (unresolved_issue_count >= 0)",
    )
    await _add_column(
        connection,
        "answer_question_drafts",
        "parse_issues_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    await _add_column(
        connection,
        "answer_question_drafts",
        "normalization_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    await _add_column(
        connection,
        "answer_question_drafts",
        "requires_correction",
        "INTEGER NOT NULL DEFAULT 0",
    )

    sql_cursor = await connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'answer_resolution_runs'
        """
    )
    row = await sql_cursor.fetchone()
    await sql_cursor.close()
    existing_sql = str(row["sql"] if row else "")
    if "structure_repair" not in existing_sql:
        await connection.execute(_answer_runs_table_sql("answer_resolution_runs_v3"))
        await connection.execute(
            """
            INSERT INTO answer_resolution_runs_v3
            SELECT id, task_id, version_id, draft_question_id, kind, provider, model,
                   request_snapshot_json, raw_response_json, parsed_output_json,
                   usage_json, status, error_code, error_message, started_at, finished_at
            FROM answer_resolution_runs
            """
        )
        await connection.execute("DROP TABLE answer_resolution_runs")
        await connection.execute(
            "ALTER TABLE answer_resolution_runs_v3 RENAME TO answer_resolution_runs"
        )


async def initialize_schema(database: Database) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    connection = await database.connect()
    try:
        if not await _table_exists(connection, "grading_tasks"):
            await connection.executescript(schema_sql)
            await connection.commit()
            await _foreign_key_check(connection)
            return

        cursor = await connection.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
        version = int(row[0] if row else 0)
        if version > CURRENT_SCHEMA_VERSION:
            raise AppError(
                500,
                "DATABASE_VERSION_UNSUPPORTED",
                "数据库版本高于当前程序支持的版本",
                {"databaseVersion": version, "supportedVersion": CURRENT_SCHEMA_VERSION},
            )

        await connection.execute("PRAGMA foreign_keys = OFF")
        await connection.execute("BEGIN IMMEDIATE")
        try:
            if version < 2:
                await _migrate_legacy_to_v2_shape(connection)
            if version < 3:
                await _migrate_v2_to_v3(connection)
            await connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.execute("PRAGMA foreign_keys = ON")

        await connection.executescript(schema_sql)
        await connection.commit()
        await _foreign_key_check(connection)
    finally:
        await connection.close()
