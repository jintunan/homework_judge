from __future__ import annotations

import hashlib
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError, ModelError
from ..grading.blank_config_confirmation import (
    save_blank_config_version,
    serialize_blank_config_version,
)
from ..grading.blank_initialization import (
    BlankInitializationInput,
    assess_blank_initialization,
    initialize_fill_blanks,
)
from ..grading.calculation import (
    generate_rubric_draft,
    validate_calculation_rubric_policy,
)
from ..grading.dependencies import RubricPoint
from ..grading.normalization import decimal_string, parse_decimal
from ..grading.prompts import RUBRIC_PROMPT_VERSION
from ..recognition.client import DashScopeClient
from ..review.invalidation import (
    ensure_question_context_mutable,
    invalidate_answer_grading_dependents,
    invalidate_blank_config_dependents,
)
from ..schemas import QuestionGradingConfigUpdate, RubricVersionUpdate
from .dependencies import get_database, get_model_client, get_settings
from .response import success

router = APIRouter()


def _question(database: Database, question_id: str) -> dict[str, Any]:
    row = database.fetchone(
        """SELECT q.*,t.current_question_frame_set_id,
           m.teacher_answer,m.teacher_explanation,
           a.answer AS auto_answer,a.explanation AS auto_explanation
           FROM questions q
           JOIN tasks t ON t.id=q.task_id
           LEFT JOIN matches m ON m.question_id=q.id
           LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
           WHERE q.id=?""",
        (question_id,),
    )
    if not row:
        raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
    if row.get("is_duplicate"):
        raise AppError(409, "QUESTION_MARKED_DUPLICATE", "重复题目不参与评分配置")
    override = json_loads(row.get("teacher_override_json"), {})
    row["effective_type"] = override.get("type", row["question_type"])
    row["effective_score"] = override.get("score", row["score"])
    row["effective_stem"] = override.get("stem", row["stem"])
    row["effective_answer"] = (
        row["teacher_answer"] if row["teacher_answer"] is not None else row["auto_answer"] or ""
    )
    row["effective_explanation"] = (
        row["teacher_explanation"]
        if row["teacher_explanation"] is not None
        else row["auto_explanation"] or ""
    )
    return row


def _blank_value(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "blankKey": row["blank_key"],
        "sortOrder": row["sort_order"],
        "maxScore": row["max_score"],
        "answerKind": row["answer_kind"],
        "standardAnswers": json_loads(row["standard_answers_json"], []),
        "synonyms": json_loads(row["synonyms_json"], []),
        "region": json_loads(row["region_json"], None),
    }


@router.get("/questions/{question_id}/grading-config")
def get_grading_config(
    question_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    question = _question(database, question_id)
    config = database.fetchone(
        "SELECT * FROM question_grading_configs WHERE question_id=?", (question_id,)
    )
    current_version_id = (
        str(config.get("current_blank_config_version_id") or "") if config else ""
    )
    version: dict[str, Any] | None = None
    if current_version_id:
        candidate = serialize_blank_config_version(database, current_version_id)
        if (
            candidate["status"] in {"pending", "auto_confirmed", "teacher_confirmed"}
            and str(candidate.get("frameSetId") or "")
            == str(question.get("current_question_frame_set_id") or "")
        ):
            version = candidate
    if version:
        blank_values = version["blanks"]
        readiness = version["readiness"]
        blocking = readiness["blockingIssues"]
        initialization: dict[str, Any] = {
            "source": "saved",
            "signals": {
                "stemMarkerCount": readiness["stemBlankCount"],
                "independentRegionCount": readiness["anchorCount"],
                "structuredAnswerCount": readiness["standardAnswerCount"],
                "selectedCount": len(readiness["expectedKeys"]),
            },
            "warnings": readiness["advisoryIssues"],
            "autoConfirmable": not blocking,
            "blockingReasons": [
                {"code": item["code"], "message": item["message"]} for item in blocking
            ],
        }
    elif question["effective_type"] == "fill_blank":
        result = initialize_fill_blanks(
            BlankInitializationInput(
                stem=str(question["effective_stem"] or ""),
                reference_answer=str(question["effective_answer"] or ""),
                max_score=question["effective_score"],
                answer_regions=json_loads(question.get("answer_regions_json"), []),
            )
        )
        readiness = assess_blank_initialization(result, question["effective_score"])
        derived = result.to_dict()
        blank_values = derived.pop("blanks")
        derived.update(
            {
                "autoConfirmable": readiness.auto_confirmable,
                "blockingReasons": [
                    {"code": reason.code, "message": reason.message}
                    for reason in readiness.blocking_reasons
                ],
            }
        )
        initialization = derived
    else:
        blank_values = []
        initialization = {
            "source": "none",
            "signals": None,
            "warnings": [],
            "autoConfirmable": False,
            "blockingReasons": [],
        }
    return success(
        {
            "questionId": question_id,
            "questionType": config["question_type"] if config else question["effective_type"],
            "maxScore": version["maxScore"]
            if version
            else config["max_score"]
            if config
            else question["effective_score"],
            "configVersion": config["config_version"] if config else 0,
            "versionId": version["id"] if version else None,
            "status": version["status"] if version else None,
            "frameSetId": version["frameSetId"]
            if version
            else question.get("current_question_frame_set_id"),
            "readiness": version["readiness"] if version else None,
            "confirmationStatus": question["confirmation_status"],
            "blanks": blank_values,
            "initialization": initialization,
        }
    )


@router.put("/questions/{question_id}/grading-config")
def update_grading_config(
    question_id: str,
    body: QuestionGradingConfigUpdate,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    question = _question(database, question_id)
    if body.questionType == "fill_blank":
        if not body.frameSetId:
            raise AppError(
                422,
                "QUESTION_FRAME_SET_REQUIRED",
                "填空配置必须绑定当前已确认题框版本",
            )
        with database.transaction() as connection:
            save_blank_config_version(
                connection,
                database,
                question_id=question_id,
                frame_set_id=body.frameSetId,
                expected_config_version=body.expectedConfigVersion,
                max_score=body.maxScore,
                blanks=[item.model_dump(mode="python") for item in body.blanks],
                actor=settings.teacher_name,
                source="teacher",
                confirm=body.confirm,
            )
        return get_grading_config(question_id, database)

    timestamp = now_iso()
    with database.transaction() as connection:
        ensure_question_context_mutable(connection, str(question["task_id"]))
        current = connection.execute(
            """SELECT question_type,max_score,config_version,current_blank_config_version_id
               FROM question_grading_configs WHERE question_id=?""",
            (question_id,),
        ).fetchone()
        desired_max_score = decimal_string(body.maxScore)
        unchanged = bool(
            current
            and str(current["question_type"]) == body.questionType
            and str(current["max_score"]) == desired_max_score
            and not current["current_blank_config_version_id"]
        )
        if (
            body.expectedConfigVersion is not None
            and int(current["config_version"] if current else 0) != body.expectedConfigVersion
            and not unchanged
        ):
            raise AppError(
                409,
                "GRADING_CONFIG_VERSION_CONFLICT",
                "评分配置已被其他修改覆盖，请重新加载后保存",
                {
                    "expectedConfigVersion": body.expectedConfigVersion,
                    "actualConfigVersion": int(current["config_version"] if current else 0),
                },
            )
        if not unchanged:
            previous_blank_config_id = (
                str(current["current_blank_config_version_id"])
                if current and current["current_blank_config_version_id"]
                else None
            )
            connection.execute(
                """INSERT INTO question_grading_configs(
                     question_id,question_type,max_score,config_version,
                     current_blank_config_version_id,updated_at
                   ) VALUES(?,?,?,1,NULL,?)
                   ON CONFLICT(question_id) DO UPDATE SET
                     question_type=excluded.question_type,max_score=excluded.max_score,
                     config_version=question_grading_configs.config_version+1,
                     current_blank_config_version_id=NULL,
                     updated_at=excluded.updated_at""",
                (question_id, body.questionType, desired_max_score, timestamp),
            )
            connection.execute(
                "DELETE FROM question_blank_definitions WHERE question_id=?", (question_id,)
            )
            connection.execute(
                "UPDATE questions SET confirmation_status='pending' WHERE id=?",
                (question_id,),
            )
            if previous_blank_config_id:
                connection.execute(
                    """UPDATE question_blank_config_versions SET status='stale',updated_at=?
                       WHERE id=? AND status IN ('pending','auto_confirmed','teacher_confirmed')""",
                    (timestamp, previous_blank_config_id),
                )
                invalidate_blank_config_dependents(
                    connection,
                    str(question["task_id"]),
                    question_id,
                    previous_blank_config_id,
                )
            else:
                invalidate_answer_grading_dependents(connection, str(question["task_id"]))
            database.audit(
                connection,
                str(question["task_id"]),
                "grading_config_updated",
                settings.teacher_name,
                {"questionId": question_id, "questionType": body.questionType},
            )
    return get_grading_config(question_id, database)


def _version_value(database: Database, row: dict[str, Any]) -> dict[str, Any]:
    point_rows = database.fetchall(
        "SELECT * FROM rubric_points WHERE rubric_version_id=? ORDER BY sort_order",
        (row["id"],),
    )
    dependencies = database.fetchall(
        """SELECT p.point_key,dp.point_key AS dependency_key
           FROM rubric_dependencies d
           JOIN rubric_points p ON p.id=d.point_id
           JOIN rubric_points dp ON dp.id=d.depends_on_point_id
           WHERE d.rubric_version_id=?""",
        (row["id"],),
    )
    by_point: dict[str, list[str]] = {}
    for item in dependencies:
        by_point.setdefault(str(item["point_key"]), []).append(str(item["dependency_key"]))
    config = database.fetchone(
        "SELECT updated_at FROM question_grading_configs WHERE question_id=?",
        (row["question_id"],),
    )
    is_current = bool(
        row["status"] == "frozen"
        and row.get("frozen_at")
        and (not config or str(row["frozen_at"]) >= str(config["updated_at"]))
    )
    return {
        "id": row["id"],
        "questionId": row["question_id"],
        "versionNumber": row["version_number"],
        "status": row["status"],
        "maxScore": row["max_score"],
        "source": row["source"],
        "modelId": row["model_id"],
        "promptVersion": row["prompt_version"],
        "contentHash": row["content_hash"],
        "confirmedBy": row["confirmed_by"],
        "frozenAt": row["frozen_at"],
        "isCurrent": is_current,
        "points": [
            {
                "id": point["id"],
                "pointKey": point["point_key"],
                "criterion": point["criterion"],
                "score": point["score"],
                "sortOrder": point["sort_order"],
                "dependencies": sorted(by_point.get(str(point["point_key"]), [])),
            }
            for point in point_rows
        ],
    }


def _save_points(
    connection: sqlite3.Connection,
    version_id: str,
    points: list[RubricPoint],
    timestamp: str,
) -> None:
    ids = {point.key: uuid.uuid4().hex for point in points}
    for point in points:
        connection.execute(
            """INSERT INTO rubric_points(
                 id,rubric_version_id,point_key,sort_order,criterion,score,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                ids[point.key],
                version_id,
                point.key,
                point.order,
                point.criterion,
                decimal_string(point.score),
                timestamp,
                timestamp,
            ),
        )
    for point in points:
        for dependency in point.dependencies:
            connection.execute(
                """INSERT INTO rubric_dependencies(
                     rubric_version_id,point_id,depends_on_point_id,created_at
                   ) VALUES(?,?,?,?)""",
                (version_id, ids[point.key], ids[dependency], timestamp),
            )


@router.post("/questions/{question_id}/rubric-drafts")
async def create_rubric_draft(
    question_id: str,
    database: Database = Depends(get_database),
    client: DashScopeClient = Depends(get_model_client),
) -> JSONResponse:
    question = _question(database, question_id)
    config = database.fetchone(
        "SELECT * FROM question_grading_configs WHERE question_id=?", (question_id,)
    )
    question_type = config["question_type"] if config else question["effective_type"]
    raw_max_score = config["max_score"] if config else question["effective_score"]
    if question_type != "calculation" or raw_max_score is None:
        raise AppError(409, "RUBRIC_NOT_APPLICABLE", "只有已配置分值的计算题可以生成评分细则")
    if not question["effective_answer"]:
        raise AppError(409, "STANDARD_ANSWER_REQUIRED", "生成评分细则前需要确认标准答案")
    try:
        points, response = await generate_rubric_draft(
            client,
            question=str(question["effective_stem"]),
            standard_answer=str(question["effective_answer"]),
            explanation=str(question["effective_explanation"]),
            max_score=parse_decimal(raw_max_score),
        )
    except (ValidationError, ValueError) as error:
        raise ModelError("RUBRIC_DRAFT_INVALID", "模型返回的评分细则不符合约束") from error
    current = database.fetchone(
        "SELECT COALESCE(MAX(version_number),0) AS value FROM rubric_versions WHERE question_id=?",
        (question_id,),
    )
    version_number = int(current["value"] if current else 0) + 1
    version_id = uuid.uuid4().hex
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,model_id,prompt_version,
                 created_at,updated_at
               ) VALUES(?,?,?,'draft',?,'model',?,?,?,?)""",
            (
                version_id,
                question_id,
                version_number,
                decimal_string(raw_max_score),
                client.settings.dashscope_model,
                RUBRIC_PROMPT_VERSION,
                timestamp,
                timestamp,
            ),
        )
        _save_points(connection, version_id, points, timestamp)
        database.audit(
            connection,
            str(question["task_id"]),
            "rubric_draft_created",
            "system",
            {
                "questionId": question_id,
                "rubricVersionId": version_id,
                "modelId": client.settings.dashscope_model,
                "promptVersion": RUBRIC_PROMPT_VERSION,
                "usage": response.usage,
            },
        )
    row = database.fetchone("SELECT * FROM rubric_versions WHERE id=?", (version_id,))
    assert row is not None
    return success(_version_value(database, row), 201)


@router.get("/questions/{question_id}/rubric-versions")
def list_rubric_versions(
    question_id: str,
    database: Database = Depends(get_database),
) -> JSONResponse:
    _question(database, question_id)
    rows = database.fetchall(
        "SELECT * FROM rubric_versions WHERE question_id=? ORDER BY version_number DESC",
        (question_id,),
    )
    return success([_version_value(database, row) for row in rows])


@router.put("/rubric-versions/{version_id}")
def update_rubric_version(
    version_id: str,
    body: RubricVersionUpdate,
    database: Database = Depends(get_database),
) -> JSONResponse:
    row = database.fetchone("SELECT * FROM rubric_versions WHERE id=?", (version_id,))
    if not row:
        raise AppError(404, "RUBRIC_VERSION_NOT_FOUND", "评分细则版本不存在")
    if row["status"] != "draft":
        raise AppError(409, "RUBRIC_VERSION_FROZEN", "冻结的评分细则不能原地修改")
    points = [
        RubricPoint(
            key=item.pointKey,
            criterion=item.criterion,
            score=item.score,
            order=item.sortOrder,
            dependencies=item.dependencies,
        )
        for item in body.points
    ]
    try:
        validate_calculation_rubric_policy(points, body.maxScore)
    except ValueError as error:
        raise AppError(422, "RUBRIC_INVALID", str(error)) from error
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM rubric_dependencies WHERE rubric_version_id=?", (version_id,)
        )
        connection.execute("DELETE FROM rubric_points WHERE rubric_version_id=?", (version_id,))
        connection.execute(
            "UPDATE rubric_versions SET max_score=?,updated_at=? WHERE id=?",
            (decimal_string(body.maxScore), timestamp, version_id),
        )
        _save_points(connection, version_id, points, timestamp)
    updated = database.fetchone("SELECT * FROM rubric_versions WHERE id=?", (version_id,))
    assert updated is not None
    return success(_version_value(database, updated))


@router.post("/rubric-versions/{version_id}/freeze")
def freeze_rubric_version(
    version_id: str,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    row = database.fetchone("SELECT * FROM rubric_versions WHERE id=?", (version_id,))
    if not row:
        raise AppError(404, "RUBRIC_VERSION_NOT_FOUND", "评分细则版本不存在")
    question = _question(database, str(row["question_id"]))
    value = _version_value(database, row)
    points = [
        RubricPoint(
            key=item["pointKey"],
            criterion=item["criterion"],
            score=parse_decimal(item["score"]),
            order=item["sortOrder"],
            dependencies=item["dependencies"],
        )
        for item in value["points"]
    ]
    try:
        validate_calculation_rubric_policy(points, parse_decimal(row["max_score"]))
    except ValueError as error:
        raise AppError(409, "RUBRIC_INVALID", str(error)) from error
    config = database.fetchone(
        "SELECT question_type,max_score FROM question_grading_configs WHERE question_id=?",
        (row["question_id"],),
    )
    current_type = str(config["question_type"] if config else question["effective_type"])
    current_max_score = config["max_score"] if config else question["effective_score"]
    if current_type != "calculation":
        raise AppError(409, "RUBRIC_NOT_APPLICABLE", "当前题目已不是计算题")
    if decimal_string(current_max_score) != decimal_string(row["max_score"]):
        raise AppError(
            409,
            "RUBRIC_SCORE_OUTDATED",
            "当前题目满分已变化，请生成并检查新的评分细则",
        )
    canonical = json_dumps(
        {
            "maxScore": decimal_string(row["max_score"]),
            "points": [point.model_dump(mode="json") for point in points],
        }
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    timestamp = now_iso()
    with database.transaction() as connection:
        connection.execute(
            """UPDATE rubric_versions SET status='frozen',content_hash=?,confirmed_by=?,
               frozen_at=?,updated_at=? WHERE id=?""",
            (digest, settings.teacher_name, timestamp, timestamp, version_id),
        )
        database.audit(
            connection,
            str(question["task_id"]),
            "rubric_reconfirmed" if row["status"] == "frozen" else "rubric_frozen",
            settings.teacher_name,
            {"questionId": row["question_id"], "rubricVersionId": version_id},
        )
    frozen = database.fetchone("SELECT * FROM rubric_versions WHERE id=?", (version_id,))
    assert frozen is not None
    return success(_version_value(database, frozen))
