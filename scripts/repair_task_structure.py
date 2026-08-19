from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from homework_judge.config import Settings  # noqa: E402
from homework_judge.db.database import Database, json_dumps, now_iso  # noqa: E402
from homework_judge.matching.matcher import build_matches  # noqa: E402
from homework_judge.recognition.consolidator import (  # noqa: E402
    consolidate_answers,
    consolidate_questions,
)


def _decode_questions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["options"] = json.loads(item["options_json"])
        item["source_pages"] = json.loads(item["source_pages_json"])
        item["issues"] = json.loads(item["issues_json"])
        output.append(item)
    return output


def _decode_answers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["source_pages"] = json.loads(item["source_pages_json"])
        item["issues"] = json.loads(item["issues_json"])
        output.append(item)
    return output


def _backup(database_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = database_path.with_name(f"{database_path.name}.backup-{timestamp}")
    with sqlite3.connect(database_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination


def _save(
    database: Database,
    task_id: str,
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    before_questions: int,
) -> None:
    timestamp = now_iso()
    kept_question_ids = {str(item["id"]) for item in questions}
    kept_answer_ids = {str(item["id"]) for item in answers}
    with database.transaction() as connection:
        connection.execute("DELETE FROM matches WHERE task_id=?", (task_id,))
        for item in questions:
            connection.execute(
                """UPDATE questions SET sort_order=?,detected_number=?,normalized_number=?,
                   stem=?,options_json=?,question_type=?,score=?,source_pages_json=?,
                   confidence=?,issues_json=?,confirmation_status=? WHERE id=?""",
                (
                    item["sort_order"],
                    item["detected_number"],
                    item["normalized_number"],
                    item["stem"],
                    json_dumps(item["options"]),
                    item["question_type"],
                    item["score"],
                    json_dumps(item["source_pages"]),
                    item["confidence"],
                    json_dumps(item["issues"]),
                    item.get("confirmation_status", "pending"),
                    item["id"],
                ),
            )
        for item in answers:
            connection.execute(
                """UPDATE answer_entries SET sort_order=?,number_hint=?,normalized_number=?,
                   stem_hint=?,answer=?,explanation=?,source_pages_json=?,confidence=?,
                   issues_json=? WHERE id=?""",
                (
                    item["sort_order"],
                    item["number_hint"],
                    item["normalized_number"],
                    item["stem_hint"],
                    item["answer"],
                    item["explanation"],
                    json_dumps(item["source_pages"]),
                    item["confidence"],
                    json_dumps(item["issues"]),
                    item["id"],
                ),
            )
        question_placeholders = ",".join("?" for _ in kept_question_ids)
        answer_placeholders = ",".join("?" for _ in kept_answer_ids)
        connection.execute(
            f"DELETE FROM questions WHERE task_id=? AND id NOT IN ({question_placeholders})",
            (task_id, *sorted(kept_question_ids)),
        )
        connection.execute(
            f"DELETE FROM answer_entries WHERE task_id=? AND id NOT IN ({answer_placeholders})",
            (task_id, *sorted(kept_answer_ids)),
        )
        for item in matches:
            connection.execute(
                """INSERT INTO matches(
                   id,task_id,question_id,answer_entry_id,method,number_score,
                   stem_score,order_score,total_score,reasons_json,status,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"],
                    task_id,
                    item["question_id"],
                    item["answer_entry_id"],
                    item["method"],
                    item["number_score"],
                    item["stem_score"],
                    item["order_score"],
                    item["total_score"],
                    json_dumps(item["reasons"]),
                    item["status"],
                    timestamp,
                ),
            )
        run_id = uuid.uuid4().hex
        summary = {
            "beforeQuestionCount": before_questions,
            "questionCount": len(questions),
            "answerCount": len(answers),
            "matchedAnswerCount": sum(item["answer_entry_id"] is not None for item in matches),
        }
        connection.execute(
            """INSERT INTO runs(
               id,task_id,kind,status,stage,progress_current,progress_total,
               request_summary_json,raw_response_json,started_at,finished_at,created_at
               ) VALUES(?,?,'structure_repair','succeeded','review_pending',1,1,?,?,?,?,?)""",
            (
                run_id,
                task_id,
                json_dumps({"mode": "deterministic"}),
                json_dumps(summary),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (timestamp, task_id))
        database.audit(connection, task_id, "structure_repaired", "system", summary)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate duplicate questions and rematch answers"
    )
    parser.add_argument("task_id")
    parser.add_argument(
        "--apply", action="store_true", help="write the repair after creating a backup"
    )
    args = parser.parse_args()

    settings = Settings.load()
    database = Database(settings.database_path)
    task = database.fetchone("SELECT id,title,status FROM tasks WHERE id=?", (args.task_id,))
    if not task:
        raise SystemExit(f"Task not found: {args.task_id}")
    edited = database.fetchone(
        """SELECT COUNT(*) AS count FROM questions q LEFT JOIN matches m ON m.question_id=q.id
           WHERE q.task_id=? AND (q.teacher_override_json IS NOT NULL OR
           m.teacher_answer IS NOT NULL OR
           m.teacher_explanation IS NOT NULL)""",
        (args.task_id,),
    )
    if edited and int(edited["count"]) > 0:
        raise SystemExit("Refusing to repair a task that already contains teacher review edits")

    question_rows = database.fetchall(
        "SELECT * FROM questions WHERE task_id=? ORDER BY sort_order", (args.task_id,)
    )
    answer_rows = database.fetchall(
        "SELECT * FROM answer_entries WHERE task_id=? ORDER BY sort_order", (args.task_id,)
    )
    questions = consolidate_questions(_decode_questions(question_rows))
    answers = consolidate_answers(_decode_answers(answer_rows))
    matches, used = build_matches(
        args.task_id,
        questions,
        answers,
        settings.auto_match_threshold,
        settings.auto_match_margin,
    )
    confirmed_question_ids = {
        str(item["id"]) for item in questions if item.get("confirmation_status") == "confirmed"
    }
    for match in matches:
        if match["question_id"] in confirmed_question_ids:
            match["status"] = "confirmed"
    result = {
        "taskId": args.task_id,
        "beforeQuestionCount": len(question_rows),
        "questionCount": len(questions),
        "answerCount": len(answers),
        "matchedAnswerCount": len(used),
        "numbers": [item["normalized_number"] for item in questions],
    }
    if not args.apply:
        print(json.dumps({"dryRun": True, **result}, ensure_ascii=False, indent=2))
        return 0

    backup = _backup(settings.database_path)
    _save(database, args.task_id, questions, answers, matches, len(question_rows))
    print(
        json.dumps({"dryRun": False, "backup": str(backup), **result}, ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
