from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..review.invalidation import (
    ensure_blank_config_is_current,
    ensure_question_context_mutable,
    invalidate_blank_config_dependents,
)
from .blank_initialization import (
    ADVISORY_ISSUE_CODES,
    BlankCountSignals,
    BlankDraft,
    BlankInitializationInput,
    BlankInitializationReadiness,
    BlankInitializationResult,
    assess_blank_initialization,
    count_stem_blank_markers,
    initialize_fill_blanks,
)
from .normalization import decimal_string

FillBlankConfirmationTrigger = Literal[
    "question_confirm",
    "task_complete",
    "student_processing",
    "grading_start",
]
BlankConfigSource = Literal["model", "teacher", "legacy"]
CONFIRMED_CONFIG_STATUSES = {"auto_confirmed", "teacher_confirmed"}


@dataclass(frozen=True, slots=True)
class FillBlankConfigCandidate:
    task_id: str
    question_id: str
    question_number: str
    frame_set_id: str
    max_score: Decimal
    initialization: BlankInitializationResult
    readiness: BlankInitializationReadiness


@dataclass(frozen=True, slots=True)
class FillBlankConfigBlocker:
    question_id: str
    question_number: str
    expected_blank_count: int
    reason_codes: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "questionId": self.question_id,
            "questionNumber": self.question_number,
            "expectedBlankCount": self.expected_blank_count,
            "reasonCodes": self.reason_codes,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class FillBlankConfigBatch:
    candidates: list[FillBlankConfigCandidate]
    existing_question_ids: list[str]
    blockers: list[FillBlankConfigBlocker]


@dataclass(frozen=True, slots=True)
class FillBlankConfirmationSummary:
    saved_question_ids: list[str]
    existing_question_ids: list[str]


def _question_rows(
    connection: sqlite3.Connection,
    *,
    question_id: str | None = None,
    task_id: str | None = None,
) -> list[sqlite3.Row]:
    if bool(question_id) == bool(task_id):
        raise ValueError("provide exactly one of question_id or task_id")
    where = "q.id=?" if question_id else "q.task_id=? AND q.is_duplicate=0"
    value = question_id or task_id
    return connection.execute(
        f"""SELECT q.*,t.current_question_frame_set_id,
                   m.teacher_answer,a.answer AS auto_answer,
                   c.question_type AS saved_question_type,
                   c.max_score AS saved_max_score,c.config_version AS saved_config_version,
                   c.current_blank_config_version_id
            FROM questions q JOIN tasks t ON t.id=q.task_id
            LEFT JOIN matches m ON m.question_id=q.id
            LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
            LEFT JOIN question_grading_configs c ON c.question_id=q.id
            WHERE {where} ORDER BY q.sort_order""",
        (value,),
    ).fetchall()


def _effective(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    override = json_loads(row["teacher_override_json"], {})
    configured_type = row["saved_question_type"]
    configured_score = row["saved_max_score"]
    return {
        "type": configured_type or override.get("type", row["question_type"]),
        "score": configured_score if configured_score is not None else override.get(
            "score", row["score"]
        ),
        "stem": override.get("stem", row["stem"]),
        "number": override.get("number", row["detected_number"]),
        "answer": (
            row["teacher_answer"]
            if row["teacher_answer"] is not None
            else row["auto_answer"] or ""
        ),
    }


def _decimal_score(value: Any) -> Decimal:
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    return score if score.is_finite() else Decimal(0)


def _issue(
    code: str,
    message: str,
    question_id: str,
    *,
    next_action: str = "review_blank_config",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "layer": "blank_config",
        "questionId": question_id,
        "nextAction": next_action,
    }


def _dedupe_issues(issues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in issues:
        code = str(raw.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        output.append(dict(raw))
    return output


def _blocker(
    question_id: str,
    question_number: str,
    expected_blank_count: int,
    reason_codes: Sequence[str],
) -> FillBlankConfigBlocker:
    return FillBlankConfigBlocker(
        question_id=question_id,
        question_number=question_number,
        expected_blank_count=expected_blank_count,
        reason_codes=list(dict.fromkeys(reason_codes)),
        message=f"第 {question_number or '?'} 题填空配置需要逐空检查并保存。",
    )


def _mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dict(dumped) if isinstance(dumped, Mapping) else None
    return None


def _anchor(raw: object) -> dict[str, Any] | None:
    value = _mapping(raw)
    if value is None:
        return None
    raw_box = value.get("box")
    box = _mapping(raw_box)
    if box is None:
        return None
    return {
        "templatePageId": value.get("templatePageId", value.get("template_page_id")),
        "pageNumber": value.get("pageNumber", value.get("page_number")),
        "coordinateSpace": value.get(
            "coordinateSpace", value.get("coordinate_space", "template_page_normalized")
        ),
        "box": {
            "x": box.get("x"),
            "y": box.get("y"),
            "width": box.get("width"),
            "height": box.get("height"),
        },
        "source": value.get("source"),
        "confidence": value.get("confidence"),
        "issues": list(value.get("issues") or []),
    }


def _definition_payload(raw: object) -> dict[str, Any]:
    value = _mapping(raw)
    if value is None:
        raise AppError(422, "BLANK_DEFINITION_INVALID", "逐空配置必须是对象")
    return {
        "blankKey": str(value.get("blankKey", value.get("blank_key", ""))).strip(),
        "sortOrder": int(value.get("sortOrder", value.get("sort_order", -1))),
        "maxScore": decimal_string(value.get("maxScore", value.get("max_score", ""))),
        "answerKind": str(value.get("answerKind", value.get("answer_kind", "text"))),
        "standardAnswers": [str(item).strip() for item in value.get("standardAnswers", [])],
        "synonyms": [str(item).strip() for item in value.get("synonyms", [])],
        "anchor": _anchor(value.get("anchor")),
    }


def _require_confirmed_frame(
    connection: sqlite3.Connection,
    question_id: str,
    frame_set_id: str,
    *,
    allow_draft: bool = False,
) -> sqlite3.Row:
    row = connection.execute(
        """SELECT q.task_id,t.current_question_frame_set_id,f.status AS frame_status,
                  i.status AS item_status
           FROM questions q JOIN tasks t ON t.id=q.task_id
           LEFT JOIN question_frame_sets f ON f.id=? AND f.task_id=q.task_id
           LEFT JOIN question_frame_items i
             ON i.frame_set_id=f.id AND i.question_id=q.id
           WHERE q.id=?""",
        (frame_set_id, question_id),
    ).fetchone()
    if row is None:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    if not frame_set_id:
        raise AppError(422, "QUESTION_FRAME_SET_REQUIRED", "逐空配置必须绑定已确认题框版本")
    if str(row["current_question_frame_set_id"] or "") != frame_set_id:
        raise AppError(
            409,
            "QUESTION_FRAME_SET_VERSION_CONFLICT",
            "逐空配置引用的题框不是当前版本",
            {
                "frameSetId": frame_set_id,
                "currentFrameSetId": row["current_question_frame_set_id"],
            },
        )
    if row["item_status"] != "confirmed":
        raise AppError(
            409,
            "QUESTION_FRAME_NOT_CONFIRMED",
            "必须先逐题确认题框，才能保存逐空配置",
            {"frameSetId": frame_set_id, "questionId": question_id},
        )
    frame_status_is_usable = row["frame_status"] == "confirmed" or (
        allow_draft and row["frame_status"] == "draft"
    )
    if not frame_status_is_usable:
        raise AppError(
            409,
            "QUESTION_FRAME_SET_NOT_FROZEN",
            "当前题框已确认，但整套题框尚未冻结；请先点击“冻结整套题框”再保存逐空配置",
            {
                "frameSetId": frame_set_id,
                "questionId": question_id,
                "frameSetStatus": row["frame_status"],
            },
        )
    return cast(sqlite3.Row, row)


def _anchor_scope_issues(
    connection: sqlite3.Connection,
    question_id: str,
    frame_set_id: str,
    definitions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    frame_regions = connection.execute(
        """SELECT r.* FROM question_frame_regions r
           JOIN question_frame_items i ON i.id=r.frame_item_id
           WHERE i.frame_set_id=? AND i.question_id=?""",
        (frame_set_id, question_id),
    ).fetchall()
    page_rows = connection.execute(
        """SELECT p.id,p.page_number,d.task_id,d.role
           FROM pages p JOIN documents d ON d.id=p.document_id
           JOIN questions q ON q.task_id=d.task_id
           WHERE q.id=?""",
        (question_id,),
    ).fetchall()
    pages = {str(row["id"]): row for row in page_rows if row["role"] == "exam"}
    issues: list[dict[str, Any]] = []
    for definition in definitions:
        anchor = _anchor(definition.get("anchor"))
        if anchor is None:
            continue
        page = pages.get(str(anchor.get("templatePageId") or ""))
        if page is None or int(page["page_number"]) != int(anchor.get("pageNumber") or 0):
            issues.append(
                _issue(
                    "blank_anchor_page_mismatch",
                    "空位锚点必须属于当前任务的模板页，且页码必须一致。",
                    question_id,
                )
            )
            continue
        box = cast(dict[str, Any], anchor["box"])
        try:
            x = float(box["x"])
            y = float(box["y"])
            width = float(box["width"])
            height = float(box["height"])
        except (KeyError, TypeError, ValueError):
            continue
        inside = any(
            str(region["template_page_id"]) == str(anchor["templatePageId"])
            and x + 0.000001 >= float(region["x"])
            and y + 0.000001 >= float(region["y"])
            and x + width <= float(region["x"]) + float(region["width"]) + 0.000001
            and y + height <= float(region["y"]) + float(region["height"]) + 0.000001
            for region in frame_regions
        )
        if not inside:
            issues.append(
                _issue(
                    "anchor_outside_question_frame",
                    "空位锚点必须完整位于教师确认的题框内部。",
                    question_id,
                )
            )
    return _dedupe_issues(issues)


def _validate_payload(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    stem: str,
    frame_set_id: str,
    max_score: Decimal | str | int,
    definitions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    stem_count = count_stem_blank_markers(stem)
    # The teacher-reviewed definition list is authoritative at save time. OCR
    # often drops an underline (the motivating 5-point example has three
    # visible answer positions but only two recognized markers), so using the
    # stem count here made the manual “add one blank” recovery impossible.
    expected_count = len(definitions)
    anchors = [_anchor(item.get("anchor")) for item in definitions]
    populated_answers = sum(
        1
        for item in definitions
        if any(str(answer).strip() for answer in item.get("standardAnswers", []))
    )
    signals = BlankCountSignals(
        stem_marker_count=stem_count,
        independent_region_count=sum(anchor is not None for anchor in anchors),
        structured_answer_count=populated_answers,
        selected_count=expected_count,
    )
    drafts = [
        BlankDraft(
            blankKey=str(item.get("blankKey") or ""),
            sortOrder=int(item.get("sortOrder", -1)),
            maxScore=str(item.get("maxScore") or ""),
            answerKind=cast(
                Literal["text", "numeric", "formula"],
                str(item.get("answerKind") or "text"),
            ),
            standardAnswers=[str(value) for value in item.get("standardAnswers", [])],
            synonyms=[str(value) for value in item.get("synonyms", [])],
            region=anchor,
        )
        for item, anchor in zip(definitions, anchors, strict=True)
    ]
    readiness = assess_blank_initialization(
        BlankInitializationResult(drafts, signals, [], source="saved"),
        max_score,
    )
    issues = [
        _issue(reason.code, reason.message, question_id)
        for reason in readiness.blocking_reasons
    ]
    issues.extend(_anchor_scope_issues(connection, question_id, frame_set_id, definitions))
    advisories = [
        _issue(reason.code, reason.message, question_id)
        for reason in readiness.advisory_reasons
    ]
    if stem_count and stem_count != expected_count:
        advisories.append(
            _issue(
                "stem_blank_count_conflict",
                f"题干识别到 {stem_count} 个空，教师配置了 {expected_count} 个空；"
                "已按教师配置保存。",
                question_id,
            )
        )
    return (
        {
            "stemBlankCount": signals.stem_marker_count,
            "anchorCount": signals.independent_region_count,
            "standardAnswerCount": signals.structured_answer_count,
            "expectedKeys": [f"B{index + 1}" for index in range(expected_count)],
            "maxScore": decimal_string(max_score),
        },
        _dedupe_issues(issues),
        _dedupe_issues(advisories),
    )


def _content_hash(
    question_id: str,
    frame_set_id: str,
    max_score: Decimal | str | int,
    definitions: Sequence[Mapping[str, Any]],
) -> str:
    canonical = {
        "questionId": question_id,
        "frameSetId": frame_set_id,
        "maxScore": decimal_string(max_score),
        "blanks": [dict(item) for item in definitions],
    }
    return hashlib.sha256(json_dumps(canonical).encode("utf-8")).hexdigest()


def _insert_definition_rows(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    question_id: str,
    definitions: Sequence[Mapping[str, Any]],
    timestamp: str,
) -> None:
    connection.execute(
        "DELETE FROM question_blank_definitions WHERE question_id=?", (question_id,)
    )
    for definition in definitions:
        legacy_id = uuid.uuid4().hex
        versioned_id = uuid.uuid4().hex
        anchor = _anchor(definition.get("anchor"))
        connection.execute(
            """INSERT INTO question_blank_definitions(
                 id,question_id,blank_key,sort_order,max_score,answer_kind,
                 standard_answers_json,synonyms_json,region_json,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                legacy_id,
                question_id,
                definition["blankKey"],
                definition["sortOrder"],
                definition["maxScore"],
                definition["answerKind"],
                json_dumps(definition["standardAnswers"]),
                json_dumps(definition["synonyms"]),
                json_dumps(anchor) if anchor is not None else None,
                timestamp,
                timestamp,
            ),
        )
        box = cast(dict[str, Any], anchor["box"]) if anchor is not None else {}
        connection.execute(
            """INSERT INTO question_blank_definition_versions(
                 id,blank_config_version_id,legacy_definition_id,blank_key,sort_order,
                 max_score,answer_kind,standard_answers_json,synonyms_json,
                 template_page_id,page_number,coordinate_space,x,y,width,height,
                 anchor_source,anchor_confidence,anchor_issues_json,anchor_json,
                 created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                versioned_id,
                version_id,
                legacy_id,
                definition["blankKey"],
                definition["sortOrder"],
                definition["maxScore"],
                definition["answerKind"],
                json_dumps(definition["standardAnswers"]),
                json_dumps(definition["synonyms"]),
                anchor.get("templatePageId") if anchor is not None else None,
                anchor.get("pageNumber") if anchor is not None else None,
                anchor.get("coordinateSpace", "template_page_normalized")
                if anchor is not None
                else "template_page_normalized",
                box.get("x"),
                box.get("y"),
                box.get("width"),
                box.get("height"),
                anchor.get("source") if anchor is not None else None,
                anchor.get("confidence") if anchor is not None else None,
                json_dumps(anchor.get("issues", [])) if anchor is not None else "[]",
                json_dumps(anchor) if anchor is not None else None,
                timestamp,
                timestamp,
            ),
        )


def save_blank_config_version(
    connection: sqlite3.Connection,
    database: Database,
    *,
    question_id: str,
    frame_set_id: str,
    expected_config_version: int | None,
    max_score: Decimal | str | int,
    blanks: Sequence[object],
    actor: str,
    source: BlankConfigSource,
    confirm: bool,
) -> str:
    """Create one immutable config version and atomically move the current pointer."""

    rows = _question_rows(connection, question_id=question_id)
    if not rows:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    row = rows[0]
    frame = _require_confirmed_frame(
        connection,
        question_id,
        frame_set_id,
        allow_draft=source == "model",
    )
    actual_version = int(row["saved_config_version"] or 0)
    if expected_config_version is None:
        raise AppError(
            409,
            "EXPECTED_CONFIG_VERSION_REQUIRED",
            "保存逐空配置必须提供 expectedConfigVersion",
            {"actualConfigVersion": actual_version},
        )
    if expected_config_version != actual_version:
        raise AppError(
            409,
            "BLANK_CONFIG_VERSION_CONFLICT",
            "逐空配置已被其他修改覆盖，请重新加载后再保存",
            {
                "expectedConfigVersion": expected_config_version,
                "actualConfigVersion": actual_version,
                "currentBlankConfigVersionId": row["current_blank_config_version_id"],
            },
        )
    ensure_question_context_mutable(connection, str(frame["task_id"]))
    definitions = [_definition_payload(item) for item in blanks]
    signals, blockers, advisories = _validate_payload(
        connection,
        question_id=question_id,
        stem=str(_effective(row)["stem"] or ""),
        frame_set_id=frame_set_id,
        max_score=max_score,
        definitions=definitions,
    )
    if confirm and blockers:
        raise AppError(
            409,
            "BLANK_CONFIG_NOT_READY",
            "逐空配置仍有阻塞项，不能确认",
            {
                "questionId": question_id,
                "frameSetId": frame_set_id,
                "blockingIssues": blockers,
            },
        )
    if confirm and source == "legacy":
        raise AppError(409, "LEGACY_BLANK_CONFIG_REVIEW_REQUIRED", "旧配置必须由教师重新确认")
    status = (
        "auto_confirmed"
        if confirm and source == "model"
        else "teacher_confirmed"
        if confirm and source == "teacher"
        else "pending"
    )
    version_number = actual_version + 1
    version_id = uuid.uuid4().hex
    timestamp = now_iso()
    digest = _content_hash(question_id, frame_set_id, max_score, definitions)
    connection.execute(
        """INSERT INTO question_blank_config_versions(
             id,question_id,version_number,frame_set_id,status,source,signals_json,
             blockers_json,advisories_json,content_hash,created_by,created_at,updated_at,
             confirmed_at,confirmed_by
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id,
            question_id,
            version_number,
            frame_set_id,
            status,
            source,
            json_dumps(signals),
            json_dumps(blockers),
            json_dumps(advisories),
            digest,
            actor,
            timestamp,
            timestamp,
            timestamp if status in CONFIRMED_CONFIG_STATUSES else None,
            actor if status in CONFIRMED_CONFIG_STATUSES else None,
        ),
    )
    _insert_definition_rows(
        connection,
        version_id=version_id,
        question_id=question_id,
        definitions=definitions,
        timestamp=timestamp,
    )
    connection.execute(
        """INSERT INTO question_grading_configs(
             question_id,question_type,max_score,config_version,
             current_blank_config_version_id,updated_at
           ) VALUES(?,'fill_blank',?,?,?,?)
           ON CONFLICT(question_id) DO UPDATE SET
             question_type='fill_blank',max_score=excluded.max_score,
             config_version=excluded.config_version,
             current_blank_config_version_id=excluded.current_blank_config_version_id,
             updated_at=excluded.updated_at""",
        (
            question_id,
            decimal_string(max_score),
            version_number,
            version_id,
            timestamp,
        ),
    )
    previous_version_id = (
        str(row["current_blank_config_version_id"])
        if row["current_blank_config_version_id"]
        else None
    )
    if source != "model" or previous_version_id is not None:
        connection.execute(
            "UPDATE questions SET confirmation_status='pending' WHERE id=?", (question_id,)
        )
        connection.execute(
            "UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?",
            (timestamp, frame["task_id"]),
        )
        invalidate_blank_config_dependents(
            connection,
            str(frame["task_id"]),
            question_id,
            previous_version_id,
        )
    event_type = {
        "pending": "blank_config_draft_saved",
        "teacher_confirmed": "blank_config_teacher_confirmed",
        "auto_confirmed": "blank_config_auto_confirmed",
    }[status]
    database.audit(
        connection,
        str(frame["task_id"]),
        event_type,
        actor,
        {
            "questionId": question_id,
            "blankConfigVersionId": version_id,
            "versionNumber": version_number,
            "frameSetId": frame_set_id,
            "status": status,
            "blankCount": len(definitions),
            "contentHash": digest,
            "blockingIssueCodes": [item["code"] for item in blockers],
            "scoreSource": "deterministic_equal_split" if source == "model" else "explicit",
        },
    )
    return version_id


def _anchor_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    saved = json_loads(row.get("anchor_json"), None)
    if isinstance(saved, dict):
        return saved
    if row.get("template_page_id") is None:
        return None
    value: dict[str, Any] = {
        "templatePageId": row["template_page_id"],
        "pageNumber": row["page_number"],
        "coordinateSpace": row["coordinate_space"],
        "box": {
            "x": row["x"],
            "y": row["y"],
            "width": row["width"],
            "height": row["height"],
        },
        "source": row["anchor_source"],
        "confidence": row["anchor_confidence"],
        "issues": json_loads(row.get("anchor_issues_json"), []),
    }
    return value


def _version_value(connection: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    version = connection.execute(
        """SELECT v.*,q.task_id,q.stem,q.teacher_override_json,
                  t.current_question_frame_set_id,
                  c.current_blank_config_version_id,
                  f.status AS frame_status,i.status AS frame_item_status
           FROM question_blank_config_versions v
           JOIN questions q ON q.id=v.question_id
           JOIN tasks t ON t.id=q.task_id
           LEFT JOIN question_grading_configs c ON c.question_id=q.id
           LEFT JOIN question_frame_sets f ON f.id=v.frame_set_id
           LEFT JOIN question_frame_items i
             ON i.frame_set_id=v.frame_set_id AND i.question_id=v.question_id
           WHERE v.id=?""",
        (version_id,),
    ).fetchone()
    if version is None:
        raise AppError(404, "BLANK_CONFIG_VERSION_NOT_FOUND", "逐空配置版本不存在")
    raw_rows = connection.execute(
        """SELECT * FROM question_blank_definition_versions
           WHERE blank_config_version_id=? ORDER BY sort_order,id""",
        (version_id,),
    ).fetchall()
    definitions = [
        {
            "id": row["id"],
            "blankKey": row["blank_key"],
            "sortOrder": row["sort_order"],
            "maxScore": row["max_score"],
            "answerKind": row["answer_kind"],
            "standardAnswers": json_loads(row["standard_answers_json"], []),
            "synonyms": json_loads(row["synonyms_json"], []),
            "anchor": _anchor_from_row(dict(row)),
        }
        for row in raw_rows
    ]
    stored_signals = json_loads(version["signals_json"], {})
    max_score = str(
        stored_signals.get("maxScore")
        if isinstance(stored_signals, dict) and stored_signals.get("maxScore")
        else decimal_string(
            sum((_decimal_score(item["maxScore"]) for item in definitions), Decimal(0))
        )
    )
    override = json_loads(version["teacher_override_json"], {})
    stem = str(override.get("stem", version["stem"]) or "")
    signals, dynamic_blockers, dynamic_advisories = _validate_payload(
        connection,
        question_id=str(version["question_id"]),
        stem=stem,
        frame_set_id=str(version["frame_set_id"]),
        max_score=max_score,
        definitions=definitions,
    )
    stored_blockers = json_loads(version["blockers_json"], [])
    stored_blocker_items = (
        [item for item in stored_blockers if isinstance(item, dict)]
        if isinstance(stored_blockers, list)
        else []
    )
    migrated_anchor_advisories = [
        item
        for item in stored_blocker_items
        if str(item.get("code") or "") in ADVISORY_ISSUE_CODES
    ]
    blockers = _dedupe_issues(
        [
            item
            for item in stored_blocker_items
            if str(item.get("code") or "") not in ADVISORY_ISSUE_CODES
        ]
        + dynamic_blockers
    )
    if (
        str(version["frame_set_id"])
        != str(version["current_question_frame_set_id"] or "")
        or (
            version["frame_status"] != "confirmed"
            and not (
                version["frame_status"] == "draft"
                and version["source"] == "model"
            )
        )
        or version["frame_item_status"] != "confirmed"
    ):
        blockers = _dedupe_issues(
            [
                *blockers,
                _issue(
                    "blank_config_frame_stale",
                    "逐空配置绑定的题框已不是当前版本。",
                    str(version["question_id"]),
                    next_action="redetect_blank_config",
                ),
            ]
        )
    if str(version["id"]) != str(version["current_blank_config_version_id"] or ""):
        blockers = _dedupe_issues(
            [
                *blockers,
                _issue(
                    "blank_config_version_superseded",
                    "该逐空配置版本已不是当前版本。",
                    str(version["question_id"]),
                    next_action="reload_blank_config",
                ),
            ]
        )
    stored_advisories = json_loads(version["advisories_json"], [])
    advisories = _dedupe_issues(
        (
            [item for item in stored_advisories if isinstance(item, dict)]
            if isinstance(stored_advisories, list)
            else []
        )
        + migrated_anchor_advisories
        + dynamic_advisories
    )
    readiness = {
        "status": version["status"],
        "frameSetId": version["frame_set_id"],
        **signals,
        "blockingIssues": blockers,
        "advisoryIssues": advisories,
    }
    blank_values = [
        {
            **item,
            "region": item["anchor"],
        }
        for item in definitions
    ]
    return {
        "id": version["id"],
        "questionId": version["question_id"],
        "versionNumber": version["version_number"],
        "frameSetId": version["frame_set_id"],
        "status": version["status"],
        "source": version["source"],
        "contentHash": version["content_hash"],
        "createdBy": version["created_by"],
        "createdAt": version["created_at"],
        "confirmedAt": version["confirmed_at"],
        "confirmedBy": version["confirmed_by"],
        "maxScore": max_score,
        "blanks": blank_values,
        "readiness": readiness,
    }


def serialize_blank_config_version(database: Database, version_id: str) -> dict[str, Any]:
    with database.connect() as connection:
        return _version_value(connection, version_id)


def _prepare_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> FillBlankConfigBatch:
    existing: list[str] = []
    candidates: list[FillBlankConfigCandidate] = []
    blockers: list[FillBlankConfigBlocker] = []
    for row in rows:
        effective = _effective(row)
        if effective["type"] != "fill_blank":
            continue
        question_id = str(row["id"])
        number = str(effective["number"] or "?")
        version_id = str(row["current_blank_config_version_id"] or "")
        expected_count = count_stem_blank_markers(str(effective["stem"] or ""))
        if not version_id:
            initialization = initialize_fill_blanks(
                BlankInitializationInput(
                    stem=str(effective["stem"] or ""),
                    reference_answer=str(effective["answer"] or ""),
                    max_score=effective["score"],
                    answer_regions=json_loads(row["answer_regions_json"], []),
                )
            )
            readiness = assess_blank_initialization(initialization, effective["score"])
            reason_codes = [reason.code for reason in readiness.blocking_reasons]
            frame_set_id = str(row["current_question_frame_set_id"] or "")
            if not frame_set_id:
                reason_codes.append("question_frame_set_missing")
            if reason_codes:
                blockers.append(
                    _blocker(
                        question_id,
                        number,
                        initialization.signals.selected_count or expected_count,
                        reason_codes,
                    )
                )
                continue
            candidates.append(
                FillBlankConfigCandidate(
                    task_id=str(row["task_id"]),
                    question_id=question_id,
                    question_number=number,
                    frame_set_id=frame_set_id,
                    max_score=_decimal_score(effective["score"]),
                    initialization=initialization,
                    readiness=readiness,
                )
            )
            continue
        value = _version_value(connection, version_id)
        saved_readiness = cast(dict[str, Any], value["readiness"])
        reason_codes = [
            str(item["code"])
            for item in cast(list[dict[str, Any]], saved_readiness["blockingIssues"])
        ]
        status = str(value["status"])
        if status == "pending":
            reason_codes.append("blank_config_confirmation_required")
        elif status == "stale":
            reason_codes.append("blank_config_stale")
        elif status not in CONFIRMED_CONFIG_STATUSES:
            reason_codes.append("blank_config_confirmation_required")
        if reason_codes:
            blockers.append(
                _blocker(
                    question_id,
                    number,
                    len(cast(list[dict[str, Any]], value["blanks"])),
                    reason_codes,
                )
            )
        else:
            existing.append(question_id)
    return FillBlankConfigBatch(candidates, existing, blockers)


def prepare_question_fill_blank_config(
    connection: sqlite3.Connection,
    question_id: str,
) -> FillBlankConfigBatch:
    return _prepare_rows(connection, _question_rows(connection, question_id=question_id))


def prepare_task_fill_blank_configs(
    connection: sqlite3.Connection,
    task_id: str,
) -> FillBlankConfigBatch:
    return _prepare_rows(connection, _question_rows(connection, task_id=task_id))


def raise_for_fill_blank_blockers(blockers: list[FillBlankConfigBlocker]) -> None:
    if not blockers:
        return
    numbers = "、".join(item.question_number for item in blockers)
    raise AppError(
        409,
        "FILL_BLANK_CONFIG_REVIEW_REQUIRED",
        f"第 {numbers} 题的填空配置需要逐空检查并保存，请返回题目复核页处理。",
        {"questions": [item.to_dict() for item in blockers]},
    )


def persist_fill_blank_config_batch(
    connection: sqlite3.Connection,
    database: Database,
    batch: FillBlankConfigBatch,
    actor: str,
    trigger: FillBlankConfirmationTrigger,
    *,
    allow_partial: bool = False,
) -> FillBlankConfirmationSummary:
    """Persist safe derived candidates and preserve every existing version."""

    if not allow_partial:
        raise_for_fill_blank_blockers(batch.blockers)
    saved: list[str] = []
    for candidate in batch.candidates:
        definitions = [
            {
                "blankKey": blank.blankKey,
                "sortOrder": blank.sortOrder,
                "maxScore": blank.maxScore,
                "answerKind": blank.answerKind,
                "standardAnswers": blank.standardAnswers,
                "synonyms": blank.synonyms,
                "anchor": None,
            }
            for blank in candidate.initialization.blanks
        ]
        version_id = save_blank_config_version(
            connection,
            database,
            question_id=candidate.question_id,
            frame_set_id=candidate.frame_set_id,
            expected_config_version=0,
            max_score=candidate.max_score,
            blanks=definitions,
            actor=actor,
            source="model",
            confirm=True,
        )
        saved_advisories_row = connection.execute(
            "SELECT advisories_json FROM question_blank_config_versions WHERE id=?",
            (version_id,),
        ).fetchone()
        saved_advisories = json_loads(
            saved_advisories_row["advisories_json"] if saved_advisories_row else "[]",
            [],
        )
        connection.execute(
            "UPDATE question_blank_config_versions SET advisories_json=? WHERE id=?",
            (
                json_dumps(
                    _dedupe_issues(
                        [item for item in saved_advisories if isinstance(item, dict)]
                        + [
                            _issue(
                                "blank_score_auto_allocated",
                                "未提供逐空分值，已按题目总分确定性等额分配；教师可修改。",
                                candidate.question_id,
                            )
                        ]
                    )
                ),
                version_id,
            ),
        )
        database.audit(
            connection,
            candidate.task_id,
            "fill_blank_config_auto_confirmed",
            actor,
            {
                "questionId": candidate.question_id,
                "trigger": trigger,
                "scorePolicy": "deterministic_equal_split",
                "modelCalls": 0,
            },
        )
        saved.append(candidate.question_id)
    return FillBlankConfirmationSummary(saved, list(batch.existing_question_ids))


def ensure_task_fill_blank_configs(
    database: Database,
    task_id: str,
    actor: str,
    trigger: FillBlankConfirmationTrigger = "grading_start",
    *,
    allow_partial: bool = False,
) -> FillBlankConfirmationSummary:
    with database.transaction() as connection:
        batch = prepare_task_fill_blank_configs(connection, task_id)
        return persist_fill_blank_config_batch(
            connection,
            database,
            batch,
            actor,
            trigger,
            allow_partial=allow_partial,
        )


def ensure_submission_blank_configs_current(
    database: Database,
    submission_id: str,
) -> None:
    submission = database.fetchone(
        """SELECT s.*,t.current_question_frame_set_id
           FROM student_submissions s JOIN tasks t ON t.id=s.task_id WHERE s.id=?""",
        (submission_id,),
    )
    if submission is None:
        raise AppError(404, "STUDENT_SUBMISSION_NOT_FOUND", "学生答卷不存在")
    ensure_task_fill_blank_configs(database, str(submission["task_id"]), "system")
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT q.id,c.current_blank_config_version_id
               FROM questions q
               JOIN question_grading_configs c ON c.question_id=q.id
               WHERE q.task_id=? AND q.is_duplicate=0 AND c.question_type='fill_blank'""",
            (submission["task_id"],),
        ).fetchall()
        if not rows:
            return
        current_revision_id = str(submission.get("current_processing_revision_id") or "")
        for row in rows:
            question_id = str(row["id"])
            current_config_id = str(row["current_blank_config_version_id"] or "")
            ensure_blank_config_is_current(connection, question_id, current_config_id)
            response = connection.execute(
                """SELECT * FROM student_responses
                   WHERE submission_id=? AND question_id=? AND processing_revision_id=?""",
                (submission_id, question_id, current_revision_id),
            ).fetchone()
            if (
                not current_revision_id
                or response is None
                or str(response["frame_set_id"] or "")
                != str(submission["current_question_frame_set_id"] or "")
                or str(response["blank_config_version_id"] or "") != current_config_id
            ):
                raise AppError(
                    409,
                    "BLANK_RECOGNITION_STALE",
                    "学生逐空识别结果引用了旧配置，请重新处理答卷",
                    {
                        "questionId": question_id,
                        "blankConfigVersionId": current_config_id,
                        "processingRevisionId": submission.get("current_processing_revision_id"),
                    },
                )
            expected_keys = [
                str(item["blank_key"])
                for item in connection.execute(
                    """SELECT blank_key FROM question_blank_definition_versions
                       WHERE blank_config_version_id=? ORDER BY sort_order""",
                    (current_config_id,),
                ).fetchall()
            ]
            actual_keys = [
                str(item["blank_key"])
                for item in connection.execute(
                    """SELECT blank_key FROM student_blank_responses
                       WHERE student_response_id=? ORDER BY blank_key""",
                    (response["id"],),
                ).fetchall()
            ]
            if actual_keys != expected_keys:
                raise AppError(
                    409,
                    "BLANK_RECOGNITION_INCOMPLETE",
                    "学生逐空识别键与当前配置不一致，请重新识别",
                    {
                        "questionId": question_id,
                        "expectedBlankKeys": expected_keys,
                        "actualBlankKeys": actual_keys,
                    },
                )


def blocker_details(batch: FillBlankConfigBatch) -> list[dict[str, Any]]:
    return [item.to_dict() for item in batch.blockers]


def readiness_value(readiness: BlankInitializationReadiness) -> dict[str, Any]:
    return {
        "autoConfirmable": readiness.auto_confirmable,
        "blockingReasons": [asdict(reason) for reason in readiness.blocking_reasons],
        "advisoryReasons": [asdict(reason) for reason in readiness.advisory_reasons],
    }
