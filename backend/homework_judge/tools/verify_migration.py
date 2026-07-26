from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db.database import Database
from ..db.migrations import initialize_schema

_TABLES = (
    "stored_files",
    "grading_tasks",
    "answer_config_versions",
    "questions",
    "answer_question_drafts",
    "answer_resolution_runs",
    "search_sources",
    "submissions",
    "model_runs",
    "question_reviews",
    "audit_events",
)


def _snapshot(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts: dict[str, int] = {}
        id_hashes: dict[str, str] = {}
        for table in _TABLES:
            if table not in tables:
                continue
            counts[table] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            columns = {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if "id" in columns:
                digest = hashlib.sha256()
                for row in connection.execute(f'SELECT id FROM "{table}" ORDER BY id'):
                    digest.update(str(row[0]).encode("utf-8"))
                    digest.update(b"\0")
                id_hashes[table] = digest.hexdigest()
        return {
            "userVersion": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "counts": counts,
            "idHashes": id_hashes,
            "foreignKeyViolations": [
                list(row) for row in connection.execute("PRAGMA foreign_key_check")
            ],
        }
    finally:
        connection.close()


def _backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro",
        uri=True,
    )
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


async def verify_copy(source: Path, target: Path) -> dict[str, Any]:
    source = await asyncio.to_thread(source.resolve)
    target = await asyncio.to_thread(target.resolve)
    if source == target:
        raise ValueError("迁移副本路径不能与正式数据库相同")
    if not await asyncio.to_thread(source.is_file):
        raise FileNotFoundError(source)
    before = await asyncio.to_thread(_snapshot, source)
    await asyncio.to_thread(_backup, source, target)
    settings = Settings(
        _env_file=None,
        APP_DATA_DIR=target.parent,
        DATABASE_PATH=target,
        UPLOAD_DIR=target.parent / "uploads-unused",
        TEMP_DIR=target.parent / "tmp-unused",
        APP_ENV="test",
    )
    await initialize_schema(Database(settings))
    after = await asyncio.to_thread(_snapshot, target)
    counts_preserved = all(
        after["counts"].get(table) == count
        for table, count in before["counts"].items()
    )
    ids_preserved = all(
        after["idHashes"].get(table) == digest
        for table, digest in before["idHashes"].items()
    )
    preserved = (
        counts_preserved
        and ids_preserved
        and not after["foreignKeyViolations"]
        and after["userVersion"] == 3
    )
    return {
        "source": str(source),
        "copy": str(target),
        "preserved": preserved,
        "before": before,
        "after": after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把 SQLite 在线备份到副本，迁移副本并比较计数、ID 与外键。"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--copy", type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(verify_copy(arguments.source, arguments.copy))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["preserved"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
