from __future__ import annotations

import sqlite3

from ..db.database import now_iso
from ..errors import AppError

ACTIVE_GRADING_STATUSES = {
    "queued",
    "prechecking",
    "aligning",
    "segmenting",
    "recognizing",
    "grading",
    "auditing",
    "generating_annotation",
    "generating_report",
}


def ensure_question_context_mutable(
    connection: sqlite3.Connection,
    task_id: str,
) -> None:
    active_submission = connection.execute(
        """SELECT id FROM student_submissions
           WHERE task_id=? AND status IN ('aligning','recognizing') LIMIT 1""",
        (task_id,),
    ).fetchone()
    if active_submission:
        raise AppError(409, "STUDENT_PROCESSING_ACTIVE", "学生答卷处理中，暂不能改变题目集合")
    placeholders = ",".join("?" for _ in ACTIVE_GRADING_STATUSES)
    active_grading = connection.execute(
        f"SELECT id FROM grading_runs WHERE task_id=? AND status IN ({placeholders}) LIMIT 1",
        (task_id, *sorted(ACTIVE_GRADING_STATUSES)),
    ).fetchone()
    if active_grading:
        raise AppError(409, "GRADING_PROCESSING_ACTIVE", "评分任务处理中，暂不能改变题目集合")


def invalidate_question_context(
    connection: sqlite3.Connection,
    task_id: str,
    reason_code: str,
    reason_message: str,
) -> None:
    timestamp = now_iso()
    connection.execute(
        """UPDATE student_submissions SET status='uploaded',error_code=?,error_message=?,
           question_region_status='pending',question_region_error_code=?,
           question_region_error_message=?,updated_at=?
           WHERE task_id=? AND status NOT IN ('aligning','recognizing')""",
        (
            reason_code,
            reason_message,
            reason_code,
            reason_message,
            timestamp,
            task_id,
        ),
    )
    connection.execute(
        "UPDATE grading_runs SET is_stale=1,retryable=1,updated_at=? WHERE task_id=?",
        (timestamp, task_id),
    )
    connection.execute(
        """UPDATE grading_artifacts SET status='stale',updated_at=?
           WHERE grading_run_id IN (SELECT id FROM grading_runs WHERE task_id=? )
             AND status IN ('current','generating')""",
        (timestamp, task_id),
    )


def invalidate_frame_set_dependents(
    connection: sqlite3.Connection,
    task_id: str,
    frame_set_id: str,
    reason_code: str = "QUESTION_FRAME_SET_CHANGED",
    reason_message: str = "题框版本已更新，请按新版本重新处理学生答卷",
) -> None:
    """Detach current downstream pointers while preserving all historical rows."""

    timestamp = now_iso()
    connection.execute(
        """UPDATE question_grading_configs
           SET current_blank_config_version_id=NULL,
               config_version=config_version+1,
               updated_at=?
           WHERE current_blank_config_version_id IN (
             SELECT id FROM question_blank_config_versions WHERE frame_set_id=?
           )""",
        (timestamp, frame_set_id),
    )
    connection.execute(
        """UPDATE question_blank_config_versions SET status='stale',updated_at=?
           WHERE frame_set_id=? AND status IN ('pending','auto_confirmed','teacher_confirmed')""",
        (timestamp, frame_set_id),
    )
    affected = connection.execute(
        """SELECT id,submission_id FROM student_processing_revisions
           WHERE frame_set_id=? AND is_current=1""",
        (frame_set_id,),
    ).fetchall()
    revision_ids = [str(row["id"]) for row in affected]
    if revision_ids:
        placeholders = ",".join("?" for _value in revision_ids)
        connection.execute(
            f"UPDATE student_processing_revisions SET is_current=0 WHERE id IN ({placeholders})",
            tuple(revision_ids),
        )
        connection.execute(
            f"""UPDATE student_submissions SET current_processing_revision_id=NULL
                WHERE current_processing_revision_id IN ({placeholders})""",
            tuple(revision_ids),
        )
    invalidate_question_context(
        connection,
        task_id,
        reason_code,
        reason_message,
    )


def invalidate_blank_config_dependents(
    connection: sqlite3.Connection,
    task_id: str,
    question_id: str,
    previous_blank_config_version_id: str | None,
    reason_code: str = "BLANK_CONFIG_CHANGED",
    reason_message: str = "逐空配置已更新，请按新版本重新识别并批改学生答卷",
) -> None:
    """Detach current recognition pointers while retaining versioned evidence.

    A processing revision represents one coherent recognition snapshot.  Once
    any question's blank keys or anchors change, that snapshot cannot remain
    current even though its rows remain useful as historical evidence.
    """

    del question_id, previous_blank_config_version_id
    invalidate_answer_grading_dependents(
        connection,
        task_id,
        reason_code=reason_code,
        reason_message=reason_message,
    )


def invalidate_answer_grading_dependents(
    connection: sqlite3.Connection,
    task_id: str,
    reason_code: str = "ANSWER_GRADING_CONFIG_CHANGED",
    reason_message: str = "标准答案或批改设置已更新，请重新识别并批改学生答卷",
) -> None:
    """Detach current recognition snapshots while preserving all history."""

    affected = connection.execute(
        """SELECT r.id,r.submission_id
           FROM student_processing_revisions r
           JOIN student_submissions s ON s.id=r.submission_id
           WHERE s.task_id=? AND r.is_current=1""",
        (task_id,),
    ).fetchall()
    revision_ids = [str(row["id"]) for row in affected]
    if revision_ids:
        placeholders = ",".join("?" for _value in revision_ids)
        connection.execute(
            f"UPDATE student_processing_revisions SET is_current=0 "
            f"WHERE id IN ({placeholders})",
            tuple(revision_ids),
        )
        connection.execute(
            f"""UPDATE student_submissions SET current_processing_revision_id=NULL
                WHERE current_processing_revision_id IN ({placeholders})""",
            tuple(revision_ids),
        )
    invalidate_question_context(
        connection,
        task_id,
        reason_code,
        reason_message,
    )


def ensure_blank_config_is_current(
    connection: sqlite3.Connection,
    question_id: str,
    captured_blank_config_version_id: str,
) -> None:
    """Reject a late worker result captured against a superseded config."""

    row = connection.execute(
        """SELECT c.current_blank_config_version_id,v.status,v.frame_set_id,
                  t.current_question_frame_set_id
           FROM questions q
           JOIN tasks t ON t.id=q.task_id
           LEFT JOIN question_grading_configs c ON c.question_id=q.id
           LEFT JOIN question_blank_config_versions v
             ON v.id=c.current_blank_config_version_id
           WHERE q.id=?""",
        (question_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    current_id = str(row["current_blank_config_version_id"] or "")
    current_is_usable = (
        current_id == captured_blank_config_version_id
        and row["status"] in {"auto_confirmed", "teacher_confirmed"}
        and str(row["frame_set_id"] or "")
        == str(row["current_question_frame_set_id"] or "")
    )
    if current_is_usable:
        return
    raise AppError(
        409,
        "BLANK_CONFIG_SUPERSEDED",
        "后台结果引用的逐空配置已被替代，本次结果不会覆盖当前版本",
        {
            "questionId": question_id,
            "capturedBlankConfigVersionId": captured_blank_config_version_id,
            "currentBlankConfigVersionId": row["current_blank_config_version_id"],
        },
    )


def ensure_frame_set_is_current(
    connection: sqlite3.Connection,
    task_id: str,
    captured_frame_set_id: str,
) -> None:
    """Fail a late worker commit when its captured frame version was superseded."""

    current = connection.execute(
        "SELECT current_question_frame_set_id FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if current is None:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    if str(current["current_question_frame_set_id"] or "") != captured_frame_set_id:
        raise AppError(
            409,
            "FRAME_SET_SUPERSEDED",
            "后台结果引用的题框版本已被替代，本次结果不会覆盖当前版本",
            {
                "capturedFrameSetId": captured_frame_set_id,
                "currentFrameSetId": current["current_question_frame_set_id"],
            },
        )
