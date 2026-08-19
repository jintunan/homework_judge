from __future__ import annotations

from typing import Any

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..matching.matcher import build_single_match
from .invalidation import ensure_question_context_mutable, invalidate_question_context


def _value(is_duplicate: bool, answer_released: bool, match_status: str) -> dict[str, Any]:
    return {
        "isDuplicate": is_duplicate,
        "answerReleased": answer_released,
        "matchStatus": match_status,
    }


def mark_question_duplicate(
    database: Database,
    settings: Settings,
    question_id: str,
) -> dict[str, Any]:
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT q.*,m.id AS match_id,m.answer_entry_id,m.method,m.status AS match_status,
               m.reasons_json,m.teacher_answer,m.teacher_explanation
               FROM questions q LEFT JOIN matches m ON m.question_id=q.id WHERE q.id=?""",
            (question_id,),
        ).fetchone()
        if not row:
            raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
        value = dict(row)
        if bool(value["is_duplicate"]):
            return {"id": question_id, **_value(True, False, "excluded")}
        ensure_question_context_mutable(connection, str(value["task_id"]))
        answer_released = value.get("answer_entry_id") is not None
        old_match = {
            "id": value.get("match_id"),
            "answerEntryId": value.get("answer_entry_id"),
            "method": value.get("method"),
            "status": value.get("match_status"),
            "reasons": json_loads(value.get("reasons_json"), []),
        }
        connection.execute(
            "UPDATE questions SET is_duplicate=1,confirmation_status='pending' WHERE id=?",
            (question_id,),
        )
        if value.get("match_id"):
            connection.execute(
                """UPDATE matches SET answer_entry_id=NULL,method='duplicate_excluded',
                   status='excluded',teacher_answer=NULL,teacher_explanation=NULL,
                   number_score=0,stem_score=0,order_score=0,total_score=0,
                   reasons_json=?,updated_at=? WHERE id=?""",
                (json_dumps(["教师标记为重复题，已退出匹配"]), now_iso(), value["match_id"]),
            )
        connection.execute(
            "UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?",
            (now_iso(), value["task_id"]),
        )
        invalidate_question_context(
            connection,
            str(value["task_id"]),
            "QUESTION_SET_CHANGED",
            "题目已标记为重复，请重新处理学生答卷",
        )
        database.audit(
            connection,
            str(value["task_id"]),
            "question_marked_duplicate",
            settings.teacher_name,
            {"questionId": question_id, "oldMatch": old_match},
        )
    return {"id": question_id, **_value(True, answer_released, "excluded")}


def restore_question(
    database: Database,
    settings: Settings,
    question_id: str,
) -> dict[str, Any]:
    with database.transaction() as connection:
        row = connection.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if not row:
            raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
        question = dict(row)
        if not bool(question["is_duplicate"]):
            match = connection.execute(
                "SELECT status FROM matches WHERE question_id=?", (question_id,)
            ).fetchone()
            return {
                "id": question_id,
                **_value(False, False, str(match["status"]) if match else "needs_review"),
            }
        task_id = str(question["task_id"])
        ensure_question_context_mutable(connection, task_id)
        connection.execute(
            "UPDATE questions SET is_duplicate=0,confirmation_status='pending' WHERE id=?",
            (question_id,),
        )
        question["is_duplicate"] = 0
        active_questions = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order",
                (task_id,),
            ).fetchall()
        ]
        used_ids = {
            str(item["answer_entry_id"])
            for item in connection.execute(
                """SELECT m.answer_entry_id FROM matches m JOIN questions q ON q.id=m.question_id
                   WHERE m.task_id=? AND q.is_duplicate=0 AND q.id<>?
                     AND m.answer_entry_id IS NOT NULL""",
                (task_id, question_id),
            ).fetchall()
        }
        answers = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM answer_entries WHERE task_id=? AND ignored=0 ORDER BY sort_order",
                (task_id,),
            ).fetchall()
            if str(item["id"]) not in used_ids
        ]
        suggestion = build_single_match(
            task_id,
            question,
            active_questions,
            answers,
            settings.auto_match_threshold,
            settings.auto_match_margin,
        )
        existing = connection.execute(
            "SELECT id FROM matches WHERE question_id=?", (question_id,)
        ).fetchone()
        if existing:
            connection.execute(
                """UPDATE matches SET answer_entry_id=?,method=?,number_score=?,stem_score=?,
                   order_score=?,total_score=?,reasons_json=?,status=?,teacher_answer=NULL,
                   teacher_explanation=NULL,updated_at=? WHERE id=?""",
                (
                    suggestion["answer_entry_id"],
                    suggestion["method"],
                    suggestion["number_score"],
                    suggestion["stem_score"],
                    suggestion["order_score"],
                    suggestion["total_score"],
                    json_dumps(suggestion["reasons"]),
                    suggestion["status"],
                    now_iso(),
                    existing["id"],
                ),
            )
        else:
            connection.execute(
                """INSERT INTO matches(id,task_id,question_id,answer_entry_id,method,
                   number_score,stem_score,order_score,total_score,reasons_json,status,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    suggestion["id"],
                    task_id,
                    question_id,
                    suggestion["answer_entry_id"],
                    suggestion["method"],
                    suggestion["number_score"],
                    suggestion["stem_score"],
                    suggestion["order_score"],
                    suggestion["total_score"],
                    json_dumps(suggestion["reasons"]),
                    suggestion["status"],
                    now_iso(),
                ),
            )
        connection.execute(
            "UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?",
            (now_iso(), task_id),
        )
        invalidate_question_context(
            connection,
            task_id,
            "QUESTION_SET_CHANGED",
            "重复题已恢复，请重新处理学生答卷",
        )
        database.audit(
            connection,
            task_id,
            "question_restored",
            settings.teacher_name,
            {
                "questionId": question_id,
                "matchMethod": suggestion["method"],
                "matchStatus": suggestion["status"],
            },
        )
    return {
        "id": question_id,
        **_value(False, False, str(suggestion["status"])),
    }
