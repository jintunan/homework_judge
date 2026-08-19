"""Read-only readiness and history metadata for migrated student work.

The v8 migration deliberately preserves legacy rows.  This module is the
single place where API entry points decide whether those rows may be used by
the *new* student-processing flow.  It never creates or confirms a frame or
blank configuration as a side effect of being read.
"""

from __future__ import annotations

from typing import Any

from ..db.database import Database, json_loads
from ..errors import AppError
from ..grading.blank_config_confirmation import prepare_task_fill_blank_configs
from ..question_frames.service import QuestionFrameService


def _effective_question_type(row: dict[str, Any]) -> str:
    override = json_loads(row.get("teacher_override_json"), {})
    if isinstance(override, dict) and override.get("type"):
        return str(override["type"])
    return str(row.get("question_type") or "")


def student_processing_gate(database: Database, task_id: str) -> dict[str, object]:
    """Return the no-write gate for upload/reprocess, including fill configs."""

    frame_gate = QuestionFrameService(database).processing_gate(task_id)
    frame_set_id = str(frame_gate.get("frameSetId") or "")
    frame_set = (
        database.fetchone("SELECT source FROM question_frame_sets WHERE id=?", (frame_set_id,))
        if frame_set_id
        else None
    )
    questions = database.fetchall(
        """SELECT id,detected_number,question_type,teacher_override_json
           FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order""",
        (task_id,),
    )
    fill_questions = [row for row in questions if _effective_question_type(row) == "fill_blank"]
    with database.connect() as connection:
        derived_batch = prepare_task_fill_blank_configs(connection, task_id)
    auto_confirmable_ids = {
        candidate.question_id for candidate in derived_batch.candidates
    }
    blank_config_issues: list[dict[str, object]] = []
    legacy_blank_config = False
    for question in fill_questions:
        override = json_loads(question.get("teacher_override_json"), {})
        question_number = (
            str(override.get("number") or question["detected_number"] or "")
            if isinstance(override, dict)
            else str(question["detected_number"] or "")
        )
        config = database.fetchone(
            """SELECT c.current_blank_config_version_id,v.status,v.source,v.frame_set_id,
                      v.blockers_json,v.confirmed_at
               FROM question_grading_configs c
               LEFT JOIN question_blank_config_versions v
                 ON v.id=c.current_blank_config_version_id
               WHERE c.question_id=?""",
            (question["id"],),
        )
        config_row = config or {}
        status = str(config_row.get("status") or "pending")
        source = str(config_row.get("source") or "")
        blockers = json_loads(config_row.get("blockers_json"), [])
        legacy_blank_config = legacy_blank_config or source == "legacy"
        valid = (
            bool(config_row.get("current_blank_config_version_id"))
            and status in {"auto_confirmed", "teacher_confirmed"}
            and source != "legacy"
            and str(config_row.get("frame_set_id") or "") == frame_set_id
            and bool(config_row.get("confirmed_at"))
            and not blockers
        )
        if valid:
            continue
        if (
            not config_row.get("current_blank_config_version_id")
            and str(question["id"]) in auto_confirmable_ids
        ):
            # A mutation entry point will persist this deterministic candidate.
            # Read-only review should not mislabel it as a manual per-blank task.
            continue
        if source == "legacy":
            code, message = (
                "LEGACY_BLANK_CONFIG_CONFIRMATION_REQUIRED",
                "历史逐空配置需由教师逐空确认后才能按新流程处理。",
            )
        elif not config_row.get("current_blank_config_version_id"):
            code, message = (
                "BLANK_CONFIG_MISSING",
                "填空题尚未建立逐空配置，不能处理学生答卷。",
            )
        elif str(config_row.get("frame_set_id") or "") != frame_set_id:
            code, message = (
                "BLANK_CONFIG_FRAME_MISMATCH",
                "逐空配置绑定的题框版本已变化，请重新确认。",
            )
        else:
            code, message = (
                "BLANK_CONFIG_CONFIRMATION_REQUIRED",
                "填空题的逐空配置尚未确认，不能处理学生答卷。",
            )
        blank_config_issues.append(
            {
                "code": code,
                "message": message,
                "layer": "blank_config",
                "questionId": str(question["id"]),
                "questionNumber": question_number,
                "blankConfigVersionId": (
                    str(config_row["current_blank_config_version_id"])
                    if config_row.get("current_blank_config_version_id")
                    else None
                ),
                "status": status,
                "source": source or None,
                "nextAction": "confirm_blank_config",
            }
        )

    legacy_processing = database.fetchone(
        """SELECT COUNT(*) AS count FROM student_processing_revisions spr
           JOIN student_submissions s ON s.id=spr.submission_id
           WHERE s.task_id=? AND spr.source='legacy'""",
        (task_id,),
    )
    current_legacy_processing = database.fetchone(
        """SELECT COUNT(*) AS count FROM student_processing_revisions spr
           JOIN student_submissions s ON s.current_processing_revision_id=spr.id
           WHERE s.task_id=? AND spr.source='legacy'""",
        (task_id,),
    )
    legacy_frame_set = bool(frame_set and frame_set.get("source") == "legacy")
    # ``required`` means that a teacher still has a recovery action to take,
    # not merely that immutable legacy rows remain in history.  Once the
    # legacy frame has been explicitly confirmed, its blank configs replaced,
    # and a non-legacy processing revision is current, the warning must clear
    # even though the old rows are intentionally retained.
    recovery_required = (
        (legacy_frame_set and not bool(frame_gate["ready"]))
        or legacy_blank_config
        or bool(current_legacy_processing and current_legacy_processing["count"])
    )
    gate = dict(frame_gate)
    raw_issues = gate.get("issues")
    issues = list(raw_issues) if isinstance(raw_issues, list) else []
    issues.extend(blank_config_issues)
    gate.update(
        {
            "ready": bool(frame_gate["ready"]) and not blank_config_issues,
            "issues": issues,
            "blankConfigIssues": blank_config_issues,
            "legacyRecovery": {
                "required": recovery_required,
                "frameSetSource": frame_set.get("source") if frame_set else None,
                "hasLegacyBlankConfig": legacy_blank_config,
                "legacyProcessingCount": int(legacy_processing["count"])
                if legacy_processing
                else 0,
                "readyForReprocess": bool(frame_gate["ready"]) and not blank_config_issues,
            },
        }
    )
    return gate


def require_student_processing_ready(database: Database, task_id: str) -> dict[str, object]:
    """Fail closed without mutating legacy records or generating configuration."""

    gate = student_processing_gate(database, task_id)
    if gate["ready"]:
        return gate
    if not bool(QuestionFrameService(database).processing_gate(task_id)["ready"]):
        raise AppError(
            409,
            "QUESTION_FRAMES_NOT_CONFIRMED",
            "请先逐题确认完整题框，再上传或处理学生试卷",
            gate,
        )
    raise AppError(
        409,
        "BLANK_CONFIGS_NOT_CONFIRMED",
        "请先确认所有填空题的逐空配置，再上传或处理学生试卷",
        gate,
    )
