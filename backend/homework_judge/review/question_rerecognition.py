from __future__ import annotations

import asyncio
import math
import uuid
from io import BytesIO
from typing import Any, cast

from PIL import Image

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..files.storage import resolve_data_path
from ..matching.numbers import normalize_question_number
from ..question_frames.service import QuestionFrameService
from ..recognition.service import RecognitionService
from .invalidation import ensure_question_context_mutable, invalidate_question_context


class SingleQuestionRerecognitionService:
    """Save one frame draft and safely replace only that question's model fields."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        recognition: RecognitionService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.recognition = recognition

    async def run(
        self,
        frame_set_id: str,
        question_id: str,
        regions: list[dict[str, object]],
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, object]:
        self._ensure_available(frame_set_id, question_id)
        saved_frame_set = QuestionFrameService(self.database).update_item(
            frame_set_id,
            question_id,
            regions,
            expected_revision=expected_revision,
            actor=actor,
            require_mutable_context=True,
        )
        self.database.execute(
            "UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?",
            (now_iso(), saved_frame_set["taskId"]),
        )
        capture = self._capture(saved_frame_set, question_id)
        run_id = uuid.uuid4().hex
        self._start_run(run_id, capture)
        raw: dict[str, Any] | None = None
        usage: dict[str, int] | None = None
        try:
            fragments = await asyncio.to_thread(self._crop_fragments, capture)
            recognized, raw, usage = await self.recognition.recognize_single_question(
                cast(dict[str, Any], capture["questionContext"]),
                fragments,
            )
            teacher_override_preserved = self._commit(
                run_id,
                capture,
                recognized,
                raw,
                usage,
                actor,
            )
        except AppError as error:
            raw = cast(dict[str, Any] | None, getattr(error, "raw_response", raw))
            usage = cast(dict[str, int] | None, getattr(error, "model_usage", usage))
            self._fail_run(run_id, error, raw=raw, usage=usage)
            existing_details = error.details if isinstance(error.details, dict) else {}
            error.details = {
                **existing_details,
                "savedFrameSet": saved_frame_set,
                "questionContentUnchanged": True,
            }
            raise
        except Exception as error:
            failure = AppError(
                502,
                "SINGLE_QUESTION_RECOGNITION_FAILED",
                "本题重新识别失败，已保存题框但原题内容未改变",
                {
                    "savedFrameSet": saved_frame_set,
                    "questionContentUnchanged": True,
                },
            )
            self._fail_run(run_id, failure, raw=raw, usage=usage)
            raise failure from error
        return {
            "questionId": question_id,
            "runId": run_id,
            "frameSet": saved_frame_set,
            "recognizedQuestion": self._recognized_value(recognized),
            "teacherOverridePreserved": teacher_override_preserved,
        }

    def _ensure_available(self, frame_set_id: str, question_id: str) -> None:
        with self.database.transaction() as connection:
            question = connection.execute(
                "SELECT task_id,is_duplicate FROM questions WHERE id=?", (question_id,)
            ).fetchone()
            if question is None:
                raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
            if bool(question["is_duplicate"]):
                raise AppError(409, "QUESTION_MARKED_DUPLICATE", "重复题不能重新识别")
            frame = connection.execute(
                "SELECT task_id FROM question_frame_sets WHERE id=?", (frame_set_id,)
            ).fetchone()
            if frame is None:
                raise AppError(404, "QUESTION_FRAME_SET_NOT_FOUND", "题框版本不存在")
            if str(frame["task_id"]) != str(question["task_id"]):
                raise AppError(409, "QUESTION_FRAME_TASK_MISMATCH", "题目不属于该题框任务")
            ensure_question_context_mutable(connection, str(question["task_id"]))

    def _capture(
        self,
        saved_frame_set: dict[str, object],
        question_id: str,
    ) -> dict[str, object]:
        items = cast(list[dict[str, object]], saved_frame_set["items"])
        item = next((value for value in items if value["questionId"] == question_id), None)
        if item is None:
            raise AppError(404, "QUESTION_FRAME_ITEM_NOT_FOUND", "题目不在保存后的题框版本中")
        fragments = sorted(
            cast(list[dict[str, object]], item["fragments"]),
            key=lambda value: (
                int(cast(Any, value["pageNumber"])),
                int(cast(Any, value["sortOrder"])),
                str(value["regionKey"]),
            ),
        )
        if not fragments:
            raise AppError(422, "SINGLE_QUESTION_FRAMES_EMPTY", "当前题没有可识别的题框片段")
        with self.database.connect() as connection:
            question = connection.execute(
                "SELECT * FROM questions WHERE id=?", (question_id,)
            ).fetchone()
        if question is None:
            raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
        return {
            "taskId": str(question["task_id"]),
            "questionId": question_id,
            "frameSetId": str(saved_frame_set["id"]),
            "frameSetRevision": int(cast(Any, saved_frame_set["revision"])),
            "frameContentHash": str(saved_frame_set["contentHash"]),
            "itemRevision": int(cast(Any, item["revision"])),
            "regions": fragments,
            "questionContext": {
                "number": str(question["detected_number"]),
                "stem": str(question["stem"]),
                "type": str(question["question_type"]),
            },
        }

    def _start_run(self, run_id: str, capture: dict[str, object]) -> None:
        summary = {
            "questionId": capture["questionId"],
            "frameSetId": capture["frameSetId"],
            "frameSetRevision": capture["frameSetRevision"],
            "frameContentHash": capture["frameContentHash"],
            "itemRevision": capture["itemRevision"],
            "fragments": [
                {
                    "regionKey": value["regionKey"],
                    "templatePageId": value["templatePageId"],
                    "pageNumber": value["pageNumber"],
                    "sortOrder": value["sortOrder"],
                    "box": {
                        key: value[key] for key in ("x", "y", "width", "height")
                    },
                }
                for value in cast(list[dict[str, object]], capture["regions"])
            ],
        }
        timestamp = now_iso()
        self.database.execute(
            """INSERT INTO runs(
                 id,task_id,kind,status,stage,progress_current,progress_total,model_id,
                 prompt_version,request_summary_json,started_at,created_at
               ) VALUES(?,?,'single_question_recognition','running','recognizing',0,1,?,?,?,?,?)""",
            (
                run_id,
                capture["taskId"],
                self.settings.dashscope_model,
                self.recognition.prompt_version("single_question"),
                json_dumps(summary),
                timestamp,
                timestamp,
            ),
        )

    def _crop_fragments(self, capture: dict[str, object]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        regions = cast(list[dict[str, object]], capture["regions"])
        page_ids = [str(value["templatePageId"]) for value in regions]
        placeholders = ",".join("?" for _value in page_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.id,p.page_number,p.image_path,p.width,p.height
                    FROM pages p JOIN documents d ON d.id=p.document_id
                    WHERE d.task_id=? AND d.role='exam' AND p.id IN ({placeholders})""",
                (capture["taskId"], *page_ids),
            ).fetchall()
        pages = {str(row["id"]): dict(row) for row in rows}
        missing_pages = sorted(set(page_ids) - set(pages))
        if missing_pages:
            raise AppError(
                422,
                "SINGLE_QUESTION_FRAME_PAGE_MISSING",
                "题框引用的模板页面不存在",
                {"templatePageIds": missing_pages},
            )
        for region in regions:
            page = pages[str(region["templatePageId"])]
            expected_size = (int(cast(Any, page["width"])), int(cast(Any, page["height"])))
            path = resolve_data_path(self.settings, str(page["image_path"]))
            try:
                with Image.open(path) as opened:
                    if opened.size != expected_size:
                        raise AppError(
                            422,
                            "SINGLE_QUESTION_PAGE_SIZE_MISMATCH",
                            "模板原图尺寸与页面记录不一致，无法可靠裁剪题框",
                            {"templatePageId": region["templatePageId"]},
                        )
                    left = math.floor(float(cast(Any, region["x"])) * opened.width)
                    top = math.floor(float(cast(Any, region["y"])) * opened.height)
                    right = math.ceil(round(
                        (float(cast(Any, region["x"])) + float(cast(Any, region["width"])))
                        * opened.width,
                        9,
                    ))
                    bottom = math.ceil(round(
                        (float(cast(Any, region["y"])) + float(cast(Any, region["height"])))
                        * opened.height,
                        9,
                    ))
                    if right <= left or bottom <= top:
                        raise AppError(
                            422,
                            "SINGLE_QUESTION_FRAME_CROP_INVALID",
                            "题框裁剪没有有效面积",
                            {"regionKey": region["regionKey"]},
                        )
                    crop = opened.convert("RGB").crop((left, top, right, bottom))
                    buffer = BytesIO()
                    crop.save(buffer, format="JPEG", quality=95)
            except AppError:
                raise
            except (OSError, ValueError) as error:
                raise AppError(
                    422,
                    "SINGLE_QUESTION_PAGE_IMAGE_INVALID",
                    "模板原图无法读取，已停止本题重新识别",
                    {"templatePageId": region["templatePageId"]},
                ) from error
            output.append(
                {
                    "region_key": str(region["regionKey"]),
                    "page_number": int(cast(Any, region["pageNumber"])),
                    "sort_order": int(cast(Any, region["sortOrder"])),
                    "image": buffer.getvalue(),
                }
            )
        return output

    def _commit(
        self,
        run_id: str,
        capture: dict[str, object],
        recognized: dict[str, Any],
        raw: dict[str, Any],
        usage: dict[str, int],
        actor: str,
    ) -> bool:
        with self.database.transaction() as connection:
            ensure_question_context_mutable(connection, str(capture["taskId"]))
            current = connection.execute(
                """SELECT t.current_question_frame_set_id,f.revision,f.content_hash
                   FROM tasks t LEFT JOIN question_frame_sets f
                     ON f.id=t.current_question_frame_set_id WHERE t.id=?""",
                (capture["taskId"],),
            ).fetchone()
            item = connection.execute(
                """SELECT revision FROM question_frame_items
                   WHERE frame_set_id=? AND question_id=?""",
                (capture["frameSetId"], capture["questionId"]),
            ).fetchone()
            current_values = (
                str(current["current_question_frame_set_id"] or "") if current else "",
                int(current["revision"]) if current and current["revision"] is not None else -1,
                str(current["content_hash"] or "") if current else "",
                int(item["revision"]) if item else -1,
            )
            captured_values = (
                str(capture["frameSetId"]),
                int(cast(Any, capture["frameSetRevision"])),
                str(capture["frameContentHash"]),
                int(cast(Any, capture["itemRevision"])),
            )
            if current_values != captured_values:
                raise AppError(
                    409,
                    "SINGLE_QUESTION_FRAME_SUPERSEDED",
                    "识别期间题框已更新，本次结果不会覆盖新版本；请刷新后重试",
                    {
                        "capturedFrameSetId": capture["frameSetId"],
                        "currentFrameSetId": current_values[0] or None,
                    },
                )
            question = connection.execute(
                "SELECT teacher_override_json FROM questions WHERE id=?",
                (capture["questionId"],),
            ).fetchone()
            if question is None:
                raise AppError(404, "QUESTION_NOT_FOUND", "题目不存在")
            override = json_loads(question["teacher_override_json"], {})
            normalized_number = normalize_question_number(
                str(override.get("number", recognized["detected_number"]))
            )
            timestamp = now_iso()
            connection.execute(
                """UPDATE questions SET source_run_id=?,detected_number=?,normalized_number=?,
                   stem=?,options_json=?,question_type=?,score=?,source_pages_json=?,confidence=?,
                   issues_json=?,confirmation_status='pending' WHERE id=?""",
                (
                    run_id,
                    recognized["detected_number"],
                    normalized_number,
                    recognized["stem"],
                    json_dumps(recognized["options"]),
                    recognized["question_type"],
                    recognized["score"],
                    json_dumps(recognized["source_pages"]),
                    recognized["confidence"],
                    json_dumps(recognized["issues"]),
                    capture["questionId"],
                ),
            )
            match = connection.execute(
                "SELECT reasons_json FROM matches WHERE question_id=?",
                (capture["questionId"],),
            ).fetchone()
            if match is not None:
                reasons = [
                    *json_loads(match["reasons_json"], []),
                    "题目已重新识别，原答案关联已保留，请重新确认",
                ]
                connection.execute(
                    """UPDATE matches SET status='suggested',reasons_json=?,updated_at=?
                       WHERE question_id=?""",
                    (json_dumps(list(dict.fromkeys(reasons))), timestamp, capture["questionId"]),
                )
            connection.execute(
                """UPDATE question_blank_config_versions SET status='stale',updated_at=?
                   WHERE question_id=?
                     AND status IN ('pending','auto_confirmed','teacher_confirmed')""",
                (timestamp, capture["questionId"]),
            )
            connection.execute(
                """UPDATE question_grading_configs SET current_blank_config_version_id=NULL,
                   updated_at=? WHERE question_id=?""",
                (timestamp, capture["questionId"]),
            )
            invalidate_question_context(
                connection,
                str(capture["taskId"]),
                "QUESTION_RERECOGNIZED",
                "题目原文已重新识别，请重新确认并处理学生答卷",
            )
            connection.execute(
                "UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?",
                (timestamp, capture["taskId"]),
            )
            connection.execute(
                """UPDATE runs SET status='succeeded',stage='completed',progress_current=1,
                   raw_response_json=?,usage_json=?,finished_at=? WHERE id=?""",
                (json_dumps(raw), json_dumps(usage), timestamp, run_id),
            )
            self.database.audit(
                connection,
                str(capture["taskId"]),
                "single_question_rerecognized",
                actor,
                {
                    "questionId": capture["questionId"],
                    "frameSetId": capture["frameSetId"],
                    "frameSetRevision": capture["frameSetRevision"],
                    "runId": run_id,
                    "sourcePages": recognized["source_pages"],
                    "teacherOverridePreserved": bool(override),
                },
            )
            return bool(override)

    def _fail_run(
        self,
        run_id: str,
        error: AppError,
        *,
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

    @staticmethod
    def _recognized_value(recognized: dict[str, Any]) -> dict[str, object]:
        return {
            "number": recognized["detected_number"],
            "stem": recognized["stem"],
            "options": recognized["options"],
            "type": recognized["question_type"],
            "score": recognized["score"],
            "sourcePages": recognized["source_pages"],
            "confidence": recognized["confidence"],
            "issues": recognized["issues"],
        }
