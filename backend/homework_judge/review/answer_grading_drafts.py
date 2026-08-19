from __future__ import annotations

import hashlib
import uuid
from typing import Any

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError, ModelError
from ..grading.answer_grading_generation import (
    ANSWER_GRADING_PROMPT_VERSION,
    SUPPORTED_TYPES,
    generate_answer_grading_draft,
)
from ..grading.blank_config_confirmation import save_blank_config_version
from ..grading.blank_initialization import BlankInitializationInput, initialize_fill_blanks
from ..grading.calculation import validate_calculation_rubric_policy
from ..grading.dependencies import RubricPoint
from ..grading.normalization import decimal_string, parse_decimal
from ..recognition.client import DashScopeClient
from .invalidation import (
    ensure_question_context_mutable,
    invalidate_answer_grading_dependents,
)
from .question_images import current_question_images, reference_answer_images


def _digest(value: object) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


class AnswerGradingDraftService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        client: DashScopeClient,
    ) -> None:
        self.settings = settings
        self.database = database
        self.client = client

    async def generate(self, question_id: str) -> dict[str, Any]:
        state = self._state(question_id)
        question_type = str(state["questionType"])
        if question_type not in SUPPORTED_TYPES:
            raise AppError(
                409,
                "ANSWER_GRADING_REGENERATION_NOT_APPLICABLE",
                "只有单选、多选、填空和计算题支持重新生成答案和批改设置",
            )
        frame_set, question_images = current_question_images(
            self.database,
            self.settings,
            str(state["taskId"]),
            question_id,
        )
        # Capture after image selection so a concurrent frame edit makes the
        # draft stale rather than silently mixing two frame versions.
        state = self._state(question_id)
        capture = dict(state["capture"])
        capture["frameSetId"] = frame_set["id"]
        capture["frameRevision"] = frame_set["revision"]
        capture["frameContentHash"] = frame_set["contentHash"]
        current = dict(state["current"])
        context = {
            "questionId": question_id,
            "number": state["number"],
            "stem": state["stem"],
            "options": state["options"],
            "questionType": question_type,
            "maxScore": state["maxScore"],
            "linkedReferenceAnswer": {
                "answerEntryId": state["answerEntryId"],
                "answer": current["standardAnswer"],
                "explanation": current["explanation"],
            },
            "currentGrading": current,
            "instructions": "题目原图中的可见作答位置优先；OCR 横线数只作辅助信号。",
        }
        images = [
            *question_images,
            *reference_answer_images(
                self.database,
                self.settings,
                str(state["taskId"]),
                state["answerEntryId"],
            ),
        ]
        run_id = uuid.uuid4().hex
        timestamp = now_iso()
        request_summary = {
            "questionId": question_id,
            "capture": capture,
            "referenceAnswerEntryId": state["answerEntryId"],
            "questionImageCount": len(question_images),
            "referenceImageCount": len(images) - len(question_images),
        }
        self.database.execute(
            """INSERT INTO runs(
                 id,task_id,kind,status,stage,progress_current,progress_total,model_id,
                 prompt_version,request_summary_json,started_at,created_at
               ) VALUES(?,?,'answer_grading_regeneration','running','generating',0,1,?,?,?,?,?)""",
            (
                run_id,
                state["taskId"],
                self.settings.dashscope_model,
                ANSWER_GRADING_PROMPT_VERSION,
                json_dumps(request_summary),
                timestamp,
                timestamp,
            ),
        )
        raw: dict[str, Any] | None = None
        usage: dict[str, int] | None = None
        try:
            draft, response = await generate_answer_grading_draft(
                self.client,
                context=context,
                images=images,
            )
            raw, usage = response.raw, response.usage
        except AppError as error:
            raw = getattr(error, "raw_response", raw)
            usage = getattr(error, "model_usage", usage)
            self._fail(run_id, error, raw, usage)
            raise
        except Exception as error:
            failure = ModelError(
                "ANSWER_GRADING_GENERATION_FAILED",
                "答案和批改设置草稿生成失败，当前内容未改变",
            )
            self._fail(run_id, failure, raw, usage)
            raise failure from error
        finished = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE runs SET status='succeeded',stage='preview_ready',progress_current=1,
                   raw_response_json=?,usage_json=?,finished_at=? WHERE id=?""",
                (
                    json_dumps({"provider": raw, "current": current, "draft": draft}),
                    json_dumps(usage),
                    finished,
                    run_id,
                ),
            )
            self.database.audit(
                connection,
                str(state["taskId"]),
                "answer_grading_draft_generated",
                "system",
                {
                    "questionId": question_id,
                    "runId": run_id,
                    "modelId": self.settings.dashscope_model,
                    "promptVersion": ANSWER_GRADING_PROMPT_VERSION,
                    "referenceAnswerEntryId": state["answerEntryId"],
                    "usage": usage,
                },
            )
        return {
            "runId": run_id,
            "questionId": question_id,
            "current": current,
            "draft": draft,
            "warnings": draft["warnings"],
            "createdAt": timestamp,
        }

    def apply(
        self,
        run_id: str,
        *,
        actor: str,
        expected_question_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id=? AND kind='answer_grading_regeneration'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise AppError(404, "ANSWER_GRADING_DRAFT_NOT_FOUND", "生成草稿不存在")
            if run["stage"] == "applied":
                raise AppError(409, "ANSWER_GRADING_DRAFT_ALREADY_APPLIED", "该草稿已经应用")
            if run["status"] != "succeeded" or run["stage"] != "preview_ready":
                raise AppError(409, "ANSWER_GRADING_DRAFT_NOT_READY", "该草稿尚不可应用")
            request = json_loads(run["request_summary_json"], {})
            stored = json_loads(run["raw_response_json"], {})
            question_id = str(request.get("questionId") or "")
            draft = stored.get("draft")
            if not question_id or not isinstance(draft, dict):
                raise AppError(409, "ANSWER_GRADING_DRAFT_CORRUPT", "草稿记录不完整，请重新生成")
            if expected_question_id is not None and question_id != expected_question_id:
                raise AppError(409, "ANSWER_GRADING_DRAFT_QUESTION_MISMATCH", "草稿不属于当前题")
            current = self._state_from_connection(connection, question_id)
            ensure_question_context_mutable(connection, str(current["taskId"]))
            captured = request.get("capture")
            if not isinstance(captured, dict) or captured != current["capture"]:
                raise AppError(
                    409,
                    "ANSWER_GRADING_DRAFT_SUPERSEDED",
                    "生成后题目、答案、题框或批改设置已变化，请重新生成草稿",
                )
            if draft.get("questionType") != current["questionType"]:
                raise AppError(409, "ANSWER_GRADING_DRAFT_SUPERSEDED", "当前题型已变化，请重新生成")
            confirmation_status = str(current["confirmationStatus"])
            timestamp = now_iso()
            connection.execute(
                """UPDATE matches SET teacher_answer=?,teacher_explanation=?,method='manual',
                   status=?,updated_at=?
                   WHERE question_id=?""",
                (
                    str(draft["standardAnswer"]).strip(),
                    str(draft["explanation"]).strip(),
                    "confirmed"
                    if current["confirmationStatus"] == "confirmed"
                    else "suggested",
                    timestamp,
                    question_id,
                ),
            )
            question_type = str(draft["questionType"])
            if question_type == "fill_blank":
                save_blank_config_version(
                    connection,
                    self.database,
                    question_id=question_id,
                    frame_set_id=str(current["capture"]["frameSetId"]),
                    expected_config_version=int(current["capture"]["configVersion"]),
                    max_score=str(draft["maxScore"]),
                    blanks=draft["blanks"],
                    actor=actor,
                    source="teacher",
                    confirm=True,
                )
            else:
                self._save_basic_config(connection, question_id, question_type, draft, timestamp)
                if question_type == "calculation":
                    self._save_frozen_rubric(
                        connection,
                        question_id,
                        draft,
                        actor,
                        str(run["model_id"] or ""),
                        timestamp,
                    )
            # Applying an answer/grading draft must not silently confirm an
            # otherwise pending question. save_blank_config_version temporarily
            # reopens the question, so restore the captured state explicitly.
            connection.execute(
                "UPDATE questions SET confirmation_status=? WHERE id=?",
                (confirmation_status, question_id),
            )
            connection.execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (current["taskStatus"], timestamp, current["taskId"]),
            )
            invalidate_answer_grading_dependents(connection, str(current["taskId"]))
            connection.execute(
                "UPDATE runs SET stage='applied' WHERE id=?",
                (run_id,),
            )
            self.database.audit(
                connection,
                str(current["taskId"]),
                "answer_grading_draft_applied",
                actor,
                {
                    "questionId": question_id,
                    "runId": run_id,
                    "questionType": question_type,
                    "previousStateHash": current["capture"]["stateHash"],
                },
            )
        return {
            "runId": run_id,
            "questionId": question_id,
            "applied": True,
            "studentResultsInvalidated": True,
            "message": "新答案和批改设置已应用；旧识别、分数和报告已失效，请重新处理学生答卷。",
        }

    def _state(self, question_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            return self._state_from_connection(connection, question_id)

    def _state_from_connection(self, connection: Any, question_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT q.*,t.status AS task_status,t.current_question_frame_set_id,
                      f.revision AS frame_revision,f.content_hash AS frame_content_hash,
                      i.revision AS frame_item_revision,i.status AS frame_item_status,
                      m.id AS match_id,m.answer_entry_id,m.teacher_answer,m.teacher_explanation,
                      m.updated_at AS match_updated_at,a.answer AS auto_answer,
                      a.explanation AS auto_explanation,a.source_pages_json AS answer_source_pages,
                      c.question_type AS config_type,c.max_score AS config_score,
                      c.config_version,c.current_blank_config_version_id,
                      bv.content_hash AS blank_content_hash
               FROM questions q JOIN tasks t ON t.id=q.task_id
               LEFT JOIN question_frame_sets f ON f.id=t.current_question_frame_set_id
               LEFT JOIN question_frame_items i
                 ON i.frame_set_id=f.id AND i.question_id=q.id
               LEFT JOIN matches m ON m.question_id=q.id
               LEFT JOIN answer_entries a ON a.id=m.answer_entry_id
               LEFT JOIN question_grading_configs c ON c.question_id=q.id
               LEFT JOIN question_blank_config_versions bv
                 ON bv.id=c.current_blank_config_version_id
               WHERE q.id=?""",
            (question_id,),
        ).fetchone()
        if row is None:
            raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
        if bool(row["is_duplicate"]):
            raise AppError(409, "QUESTION_MARKED_DUPLICATE", "重复题不能重新生成答案和批改设置")
        if row["match_id"] is None:
            raise AppError(409, "QUESTION_MATCH_REQUIRED", "当前题缺少答案匹配记录")
        override = json_loads(row["teacher_override_json"], {})
        question_type = str(override.get("type", row["question_type"]))
        max_score = row["config_score"]
        if max_score is None:
            max_score = override.get("score", row["score"])
        try:
            max_score_value = decimal_string(max_score)
        except (ValueError, TypeError) as error:
            raise AppError(409, "QUESTION_SCORE_REQUIRED", "请先设置有效的本题满分") from error
        answer = (
            row["teacher_answer"]
            if row["teacher_answer"] is not None
            else row["auto_answer"] or ""
        )
        explanation = (
            row["teacher_explanation"]
            if row["teacher_explanation"] is not None
            else row["auto_explanation"] or ""
        )
        options = json_loads(row["options_json"], [])
        blanks = [
            {
                "blankKey": item["blank_key"],
                "sortOrder": item["sort_order"],
                "maxScore": item["max_score"],
                "answerKind": item["answer_kind"],
                "standardAnswers": json_loads(item["standard_answers_json"], []),
                "synonyms": json_loads(item["synonyms_json"], []),
                "anchor": json_loads(item["region_json"], None),
            }
            for item in connection.execute(
                "SELECT * FROM question_blank_definitions WHERE question_id=? ORDER BY sort_order",
                (question_id,),
            ).fetchall()
        ]
        if question_type == "fill_blank" and not blanks:
            derived = initialize_fill_blanks(
                BlankInitializationInput(
                    stem=str(override.get("stem", row["stem"]) or ""),
                    reference_answer=str(answer),
                    max_score=max_score_value,
                    answer_regions=json_loads(row["answer_regions_json"], []),
                )
            )
            blanks = [
                {
                    "blankKey": item.blankKey,
                    "sortOrder": item.sortOrder,
                    "maxScore": item.maxScore,
                    "answerKind": item.answerKind,
                    "standardAnswers": item.standardAnswers,
                    "synonyms": item.synonyms,
                    "anchor": item.region,
                }
                for item in derived.blanks
            ]
        rubric_row = connection.execute(
            """SELECT * FROM rubric_versions WHERE question_id=? AND status='frozen'
               ORDER BY version_number DESC LIMIT 1""",
            (question_id,),
        ).fetchone()
        rubric_points: list[dict[str, Any]] = []
        rubric_id = None
        rubric_hash = None
        if rubric_row is not None:
            rubric_id = str(rubric_row["id"])
            rubric_hash = rubric_row["content_hash"]
            dependencies = connection.execute(
                """SELECT p.point_key,dp.point_key AS dependency_key
                   FROM rubric_dependencies d JOIN rubric_points p ON p.id=d.point_id
                   JOIN rubric_points dp ON dp.id=d.depends_on_point_id
                   WHERE d.rubric_version_id=?""",
                (rubric_id,),
            ).fetchall()
            by_point: dict[str, list[str]] = {}
            for dependency in dependencies:
                by_point.setdefault(str(dependency["point_key"]), []).append(
                    str(dependency["dependency_key"])
                )
            rubric_points = [
                {
                    "pointKey": item["point_key"],
                    "criterion": item["criterion"],
                    "score": item["score"],
                    "sortOrder": item["sort_order"],
                    "dependencies": sorted(by_point.get(str(item["point_key"]), [])),
                }
                for item in connection.execute(
                    "SELECT * FROM rubric_points WHERE rubric_version_id=? ORDER BY sort_order",
                    (rubric_id,),
                ).fetchall()
            ]
        current = {
            "questionType": question_type,
            "standardAnswer": str(answer),
            "explanation": str(explanation),
            "maxScore": max_score_value,
            "answerOptions": [],
            "blanks": blanks,
            "rubricPoints": rubric_points,
            "warnings": [],
        }
        state_payload = {
            "question": {
                "number": override.get("number", row["detected_number"]),
                "stem": override.get("stem", row["stem"]),
                "options": override.get("options", options),
                "type": question_type,
                "score": max_score_value,
                "confirmationStatus": row["confirmation_status"],
            },
            "answer": {
                "entryId": row["answer_entry_id"],
                "teacherAnswer": row["teacher_answer"],
                "teacherExplanation": row["teacher_explanation"],
                "autoAnswer": row["auto_answer"],
                "autoExplanation": row["auto_explanation"],
                "sourcePages": json_loads(row["answer_source_pages"], []),
                "matchUpdatedAt": row["match_updated_at"],
            },
            "grading": {
                "configVersion": int(row["config_version"] or 0),
                "blankConfigVersionId": row["current_blank_config_version_id"],
                "blankContentHash": row["blank_content_hash"],
                "rubricVersionId": rubric_id,
                "rubricContentHash": rubric_hash,
            },
            "frame": {
                "frameSetId": row["current_question_frame_set_id"],
                "revision": row["frame_revision"],
                "contentHash": row["frame_content_hash"],
                "itemRevision": row["frame_item_revision"],
                "itemStatus": row["frame_item_status"],
            },
        }
        capture = {
            "stateHash": _digest(state_payload),
            "frameSetId": row["current_question_frame_set_id"],
            "frameRevision": row["frame_revision"],
            "frameContentHash": row["frame_content_hash"],
            "frameItemRevision": row["frame_item_revision"],
            "configVersion": int(row["config_version"] or 0),
            "blankConfigVersionId": row["current_blank_config_version_id"],
            "rubricVersionId": rubric_id,
        }
        return {
            "taskId": str(row["task_id"]),
            "taskStatus": str(row["task_status"]),
            "questionId": question_id,
            "questionType": question_type,
            "number": str(override.get("number", row["detected_number"])),
            "stem": str(override.get("stem", row["stem"])),
            "options": override.get("options", options),
            "maxScore": max_score_value,
            "answerEntryId": row["answer_entry_id"],
            "confirmationStatus": row["confirmation_status"],
            "current": current,
            "capture": capture,
        }

    @staticmethod
    def _save_basic_config(
        connection: Any,
        question_id: str,
        question_type: str,
        draft: dict[str, Any],
        timestamp: str,
    ) -> None:
        connection.execute(
            """UPDATE question_blank_config_versions SET status='stale',updated_at=?
               WHERE question_id=?
                 AND status IN ('pending','auto_confirmed','teacher_confirmed')""",
            (timestamp, question_id),
        )
        connection.execute(
            """INSERT INTO question_grading_configs(
                 question_id,question_type,max_score,config_version,
                 current_blank_config_version_id,updated_at
               ) VALUES(?,?,?,1,NULL,?)
               ON CONFLICT(question_id) DO UPDATE SET
                 question_type=excluded.question_type,max_score=excluded.max_score,
                 config_version=question_grading_configs.config_version+1,
                 current_blank_config_version_id=NULL,updated_at=excluded.updated_at""",
            (question_id, question_type, str(draft["maxScore"]), timestamp),
        )
        connection.execute(
            "DELETE FROM question_blank_definitions WHERE question_id=?",
            (question_id,),
        )

    @staticmethod
    def _save_frozen_rubric(
        connection: Any,
        question_id: str,
        draft: dict[str, Any],
        actor: str,
        model_id: str,
        timestamp: str,
    ) -> None:
        points = [
            RubricPoint(
                key=str(item["pointKey"]),
                criterion=str(item["criterion"]),
                score=parse_decimal(item["score"]),
                order=int(item["sortOrder"]),
                dependencies=[str(value) for value in item["dependencies"]],
            )
            for item in draft["rubricPoints"]
        ]
        try:
            validate_calculation_rubric_policy(points, str(draft["maxScore"]))
        except ValueError as error:
            raise AppError(422, "RUBRIC_INVALID", "草稿评分细则不符合正式评分政策") from error
        current = connection.execute(
            """SELECT COALESCE(MAX(version_number),0) AS value
               FROM rubric_versions WHERE question_id=?""",
            (question_id,),
        ).fetchone()
        version_id = uuid.uuid4().hex
        version_number = int(current["value"] if current else 0) + 1
        canonical = {
            "maxScore": str(draft["maxScore"]),
            "points": [
                {
                    "key": point.key,
                    "criterion": point.criterion,
                    "score": decimal_string(point.score),
                    "order": point.order,
                    "dependencies": point.dependencies,
                }
                for point in points
            ],
        }
        connection.execute(
            """INSERT INTO rubric_versions(
                 id,question_id,version_number,status,max_score,source,model_id,prompt_version,
                 content_hash,confirmed_by,frozen_at,created_at,updated_at
               ) VALUES(?,?,?,'frozen',?,'model',?,?,?,?,?,?,?)""",
            (
                version_id,
                question_id,
                version_number,
                str(draft["maxScore"]),
                model_id or None,
                ANSWER_GRADING_PROMPT_VERSION,
                _digest(canonical),
                actor,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
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

    def _fail(
        self,
        run_id: str,
        error: AppError,
        raw: dict[str, Any] | None,
        usage: dict[str, int] | None,
    ) -> None:
        self.database.execute(
            """UPDATE runs SET status='failed',stage='failed',raw_response_json=?,usage_json=?,
               error_code=?,error_message=?,finished_at=? WHERE id=?""",
            (
                json_dumps(raw) if raw is not None else None,
                json_dumps(usage) if usage is not None else None,
                error.code,
                error.message,
                now_iso(),
                run_id,
            ),
        )
