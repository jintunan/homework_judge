from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from homework_judge.config import Settings
from homework_judge.db.database import Database
from homework_judge.db.migrations import initialize_schema


def _v2_schema_fixture() -> str:
    schema = (
        Path(__file__).parents[2] / "homework_judge" / "db" / "schema.sql"
    ).read_text(encoding="utf-8")
    replacements = {
        "  extraction_issues_json TEXT NOT NULL DEFAULT '[]',\n"
        "  unresolved_issue_count INTEGER NOT NULL DEFAULT 0\n"
        "    CHECK (unresolved_issue_count >= 0),\n": "",
        "  parse_issues_json TEXT NOT NULL DEFAULT '[]',\n"
        "  normalization_json TEXT NOT NULL DEFAULT '[]',\n"
        "  requires_correction INTEGER NOT NULL DEFAULT 0,\n": "",
        "      'exam_extraction', 'reference_extraction', 'structure_repair',\n": (
            "      'exam_extraction', 'reference_extraction',\n"
        ),
        "PRAGMA user_version = 3;": "PRAGMA user_version = 2;",
    }
    for current, legacy in replacements.items():
        assert current in schema
        schema = schema.replace(current, legacy)
    return schema


def _settings(tmp_path: Path, database_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        APP_DATA_DIR=tmp_path,
        DATABASE_PATH=database_path,
        UPLOAD_DIR=tmp_path / "uploads",
        TEMP_DIR=tmp_path / "tmp",
        APP_ENV="test",
    )


@pytest.mark.asyncio
async def test_fresh_database_uses_schema_v3(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite"
    database = Database(_settings(tmp_path, path))
    await initialize_schema(database)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(answer_config_versions)")
        }
        draft_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(answer_question_drafts)")
        }
        assert {"extraction_issues_json", "unresolved_issue_count"} <= version_columns
        assert {
            "parse_issues_json",
            "normalization_json",
            "requires_correction",
        } <= draft_columns
        run_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'answer_resolution_runs'"
        ).fetchone()[0]
        assert "structure_repair" in run_sql
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_v2_migration_preserves_run_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    legacy_schema = _v2_schema_fixture()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(legacy_schema)
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            """
            INSERT INTO grading_tasks
              (id, name, class_name, paper_name, subject, answer_mode,
               answer_config_status, status, created_at, updated_at)
            VALUES (
              'task-1', '测试任务', '二班', '物理试卷', 'high_school_physics',
              'agent_search', 'extracting', 'draft', '2026-01-01', '2026-01-01'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO answer_config_versions
              (id, task_id, version_number, status, answer_mode, created_at)
            VALUES ('version-1', 'task-1', 1, 'draft', 'agent_search', '2026-01-01')
            """
        )
        connection.execute(
            """
            INSERT INTO answer_resolution_runs
              (id, task_id, version_id, kind, provider, model, status, started_at)
            VALUES (
              'run-1', 'task-1', 'version-1', 'exam_extraction',
              'test', 'test-model', 'succeeded', '2026-01-01'
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(_settings(tmp_path, path))
    await initialize_schema(database)
    await initialize_schema(database)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert connection.execute(
            "SELECT id, kind FROM answer_resolution_runs"
        ).fetchall() == [("run-1", "exam_extraction")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
