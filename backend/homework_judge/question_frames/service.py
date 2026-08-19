from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import Any, Literal, cast

from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..review.invalidation import ensure_question_context_mutable, invalidate_frame_set_dependents
from .normalization import normalize_model_question_frame_candidates
from .validation import FrameValidationIssue, validate_question_frame_set

FrameSource = Literal["model", "teacher", "legacy"]


class QuestionFrameService:
    """Own immutable task-level question-frame versions and their upload gate."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_draft(
        self,
        task_id: str,
        candidates: Sequence[Mapping[str, object]],
        *,
        source: FrameSource,
        actor: str,
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            task = self._task(connection, task_id)
            if task["current_question_frame_set_id"]:
                raise AppError(
                    409,
                    "QUESTION_FRAME_SET_EXISTS",
                    "任务已经有当前题框版本，请编辑当前版本",
                    {"frameSetId": task["current_question_frame_set_id"]},
                )
            questions = self._active_questions(connection, task_id)
            if source == "model":
                candidates, _changed = normalize_model_question_frame_candidates(
                    candidates,
                    [str(row["id"]) for row in questions],
                )
            by_question: dict[str, Mapping[str, object]] = {}
            for candidate in candidates:
                question_id = str(candidate.get("questionId", "")).strip()
                if not question_id or question_id in by_question:
                    raise AppError(
                        422,
                        "QUESTION_FRAME_CANDIDATES_INVALID",
                        "题框候选必须按题目唯一标识",
                    )
                by_question[question_id] = candidate
            active_ids = {str(row["id"]) for row in questions}
            unknown_ids = sorted(set(by_question) - active_ids)
            if unknown_ids:
                raise AppError(
                    422,
                    "QUESTION_FRAME_QUESTION_UNKNOWN",
                    "题框候选引用了不属于当前任务的题目",
                    {"questionIds": unknown_ids},
                )
            timestamp = now_iso()
            version_number = self._next_version(connection, task_id)
            frame_set_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO question_frame_sets(
                     id,task_id,version_number,status,revision,source,content_hash,
                     created_by,created_at,updated_at
                   ) VALUES(?,?,?,'draft',0,?,'pending',?,?,?)""",
                (
                    frame_set_id,
                    task_id,
                    version_number,
                    source,
                    actor,
                    timestamp,
                    timestamp,
                ),
            )
            for question in questions:
                question_id = str(question["id"])
                raw_candidate = by_question.get(question_id)
                item_id = uuid.uuid4().hex
                item_issues = (
                    self._text_list(raw_candidate.get("issues")) if raw_candidate else []
                )
                connection.execute(
                    """INSERT INTO question_frame_items(
                         id,frame_set_id,question_id,status,revision,issues_json,
                         created_at,updated_at
                       ) VALUES(?,?,?,'pending',0,?,?,?)""",
                    (
                        item_id,
                        frame_set_id,
                        question_id,
                        json_dumps(item_issues),
                        timestamp,
                        timestamp,
                    ),
                )
                for fragment in self._candidate_fragments(raw_candidate):
                    self._insert_region(connection, item_id, fragment, timestamp)
            self._refresh_hash(connection, frame_set_id)
            connection.execute(
                """UPDATE tasks SET current_question_frame_set_id=?,status='review_pending',
                   updated_at=? WHERE id=?""",
                (frame_set_id, timestamp, task_id),
            )
            self.database.audit(
                connection,
                task_id,
                "question_frame_set_created",
                actor,
                {
                    "frameSetId": frame_set_id,
                    "versionNumber": version_number,
                    "source": source,
                },
            )
            return self._serialize(connection, frame_set_id)

    def get_current(self, task_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            task = self._task(connection, task_id)
            frame_set_id = task["current_question_frame_set_id"]
            return self._serialize(connection, str(frame_set_id)) if frame_set_id else None

    def get_frame_set(self, frame_set_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            return self._serialize(connection, frame_set_id)

    def update_item(
        self,
        frame_set_id: str,
        question_id: str,
        regions: Sequence[Mapping[str, object]],
        *,
        expected_revision: int,
        actor: str,
        require_mutable_context: bool = False,
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            frame_set = self._frame_set(connection, frame_set_id)
            self._check_revision(frame_set, expected_revision)
            self._ensure_current(connection, frame_set)
            if require_mutable_context:
                ensure_question_context_mutable(connection, str(frame_set["task_id"]))
            item = self._item(connection, frame_set_id, question_id)
            proposed = self._validation_items(
                connection,
                frame_set_id,
                replacement=(question_id, regions),
            )
            proposed_issues = validate_question_frame_set(
                proposed,
                self._template_pages(connection, str(frame_set["task_id"])),
            )
            blocking_issues = [
                issue
                for issue in proposed_issues
                if (
                    issue.question_id == question_id
                    or issue.related_question_id == question_id
                )
                and issue.code != "frame_cross_question_overlap"
            ]
            self._raise_geometry_issues(blocking_issues)

            target_set_id = frame_set_id
            if frame_set["status"] == "confirmed":
                target_set_id = self._fork_confirmed(
                    connection,
                    frame_set,
                    changed_question_id=question_id,
                    actor=actor,
                )
                item = self._item(connection, target_set_id, question_id)
            elif frame_set["status"] != "draft":
                raise AppError(
                    409,
                    "QUESTION_FRAME_SET_IMMUTABLE",
                    "该题框版本已被替代，不能继续编辑",
                )

            timestamp = now_iso()
            connection.execute(
                "DELETE FROM question_frame_regions WHERE frame_item_id=?",
                (item["id"],),
            )
            for region in regions:
                self._insert_region(connection, str(item["id"]), region, timestamp)
            connection.execute(
                """UPDATE question_frame_items SET status='pending',revision=revision+1,
                   issues_json='[]',confirmed_at=NULL,confirmed_by=NULL,updated_at=? WHERE id=?""",
                (timestamp, item["id"]),
            )
            connection.execute(
                "UPDATE question_frame_sets SET revision=revision+1,updated_at=? WHERE id=?",
                (timestamp, target_set_id),
            )
            self._refresh_hash(connection, target_set_id)
            task_id = str(frame_set["task_id"])
            self.database.audit(
                connection,
                task_id,
                "question_frame_item_updated",
                actor,
                {
                    "frameSetId": target_set_id,
                    "baseFrameSetId": frame_set_id if target_set_id != frame_set_id else None,
                    "questionId": question_id,
                    "regionKeys": [str(region.get("regionKey", "")) for region in regions],
                },
            )
            return self._serialize(connection, target_set_id)

    def normalize_model_draft(
        self,
        frame_set_id: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, object]:
        """Repair an untouched model draft without making another model call."""

        with self.database.transaction() as connection:
            frame_set = self._frame_set(connection, frame_set_id)
            self._check_revision(frame_set, expected_revision)
            self._ensure_current(connection, frame_set)
            if frame_set["status"] != "draft" or frame_set["source"] != "model":
                raise AppError(
                    409,
                    "QUESTION_FRAME_AUTO_LAYOUT_UNAVAILABLE",
                    "只有尚未冻结的模型题框草稿可以自动补齐",
                )
            current = self._serialize(connection, frame_set_id)
            items = cast(list[dict[str, object]], current["items"])
            if any(
                item["status"] != "pending"
                or any(
                    fragment.get("source") != "model"
                    for fragment in cast(list[dict[str, object]], item["fragments"])
                )
                for item in items
            ):
                raise AppError(
                    409,
                    "QUESTION_FRAME_AUTO_LAYOUT_UNAVAILABLE",
                    "已有教师确认或修改的题框；为避免覆盖人工结果，请继续手动调整",
                )
            questions = self._active_questions(connection, str(frame_set["task_id"]))
            candidates = [
                {
                    "questionId": item["questionId"],
                    "fragments": item["fragments"],
                }
                for item in items
            ]
            normalized, changed_question_ids = normalize_model_question_frame_candidates(
                candidates,
                [str(row["id"]) for row in questions],
            )
            by_question = {
                str(candidate["questionId"]): candidate for candidate in normalized
            }
            if not changed_question_ids:
                return current

            timestamp = now_iso()
            for question_id in changed_question_ids:
                item = self._item(connection, frame_set_id, question_id)
                connection.execute(
                    "DELETE FROM question_frame_regions WHERE frame_item_id=?",
                    (item["id"],),
                )
                for region in self._candidate_fragments(by_question[question_id]):
                    self._insert_region(connection, str(item["id"]), region, timestamp)
                retained_issues = [
                    issue
                    for issue in json_loads(item["issues_json"], [])
                    if not str(issue).startswith("frame_")
                ]
                connection.execute(
                    """UPDATE question_frame_items SET revision=revision+1,issues_json=?,
                       updated_at=? WHERE id=?""",
                    (json_dumps(retained_issues), timestamp, item["id"]),
                )
            connection.execute(
                "UPDATE question_frame_sets SET revision=revision+1,updated_at=? WHERE id=?",
                (timestamp, frame_set_id),
            )
            self._refresh_hash(connection, frame_set_id)
            self.database.audit(
                connection,
                str(frame_set["task_id"]),
                "question_frame_model_draft_normalized",
                actor,
                {
                    "frameSetId": frame_set_id,
                    "changedQuestionIds": changed_question_ids,
                    "modelCallCount": 0,
                },
            )
            return self._serialize(connection, frame_set_id)

    def reopen_item(
        self,
        frame_set_id: str,
        question_id: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, object]:
        current = self.get_frame_set(frame_set_id)
        item = next(
            (value for value in cast(list[dict[str, object]], current["items"])
             if value["questionId"] == question_id),
            None,
        )
        if item is None:
            raise AppError(404, "QUESTION_FRAME_ITEM_NOT_FOUND", "题目不在该题框版本中")
        return self.update_item(
            frame_set_id,
            question_id,
            cast(list[Mapping[str, object]], item["fragments"]),
            expected_revision=expected_revision,
            actor=actor,
        )

    def confirm_item(
        self,
        frame_set_id: str,
        question_id: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            frame_set = self._frame_set(connection, frame_set_id)
            self._check_revision(frame_set, expected_revision)
            self._ensure_current(connection, frame_set)
            if frame_set["status"] != "draft":
                raise AppError(
                    409,
                    "QUESTION_FRAME_SET_NOT_DRAFT",
                    "只有当前草稿中的题框可以逐题确认",
                )
            item = self._item(connection, frame_set_id, question_id)
            region_count = connection.execute(
                "SELECT COUNT(*) FROM question_frame_regions WHERE frame_item_id=?",
                (item["id"],),
            ).fetchone()[0]
            if not region_count:
                raise AppError(
                    409,
                    "QUESTION_FRAME_ITEM_EMPTY",
                    "题框没有覆盖任何模板页面，不能确认",
                    {"questionId": question_id},
                )
            issues = validate_question_frame_set(
                self._validation_items(connection, frame_set_id),
                self._template_pages(connection, str(frame_set["task_id"])),
            )
            related = [
                issue
                for issue in issues
                if issue.question_id == question_id or issue.related_question_id == question_id
            ]
            self._raise_geometry_issues(related)
            if item["status"] == "confirmed":
                return self._serialize(connection, frame_set_id)
            timestamp = now_iso()
            connection.execute(
                """UPDATE question_frame_items SET status='confirmed',revision=revision+1,
                   confirmed_at=?,confirmed_by=?,updated_at=? WHERE id=?""",
                (timestamp, actor, timestamp, item["id"]),
            )
            connection.execute(
                "UPDATE question_frame_sets SET revision=revision+1,updated_at=? WHERE id=?",
                (timestamp, frame_set_id),
            )
            self._refresh_hash(connection, frame_set_id)
            self.database.audit(
                connection,
                str(frame_set["task_id"]),
                "question_frame_item_confirmed",
                actor,
                {"frameSetId": frame_set_id, "questionId": question_id},
            )
            return self._serialize(connection, frame_set_id)

    def confirm_set(
        self,
        frame_set_id: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            frame_set = self._frame_set(connection, frame_set_id)
            self._check_revision(frame_set, expected_revision)
            self._ensure_current(connection, frame_set)
            if frame_set["status"] != "draft":
                raise AppError(
                    409,
                    "QUESTION_FRAME_SET_NOT_DRAFT",
                    "只有当前草稿可以冻结",
                )
            task_id = str(frame_set["task_id"])
            active_ids = [str(row["id"]) for row in self._active_questions(connection, task_id)]
            item_rows = connection.execute(
                """SELECT i.*,COUNT(r.id) AS region_count
                   FROM question_frame_items i
                   LEFT JOIN question_frame_regions r ON r.frame_item_id=i.id
                   WHERE i.frame_set_id=? GROUP BY i.id""",
                (frame_set_id,),
            ).fetchall()
            by_question = {str(row["question_id"]): row for row in item_rows}
            missing = [
                question_id
                for question_id in active_ids
                if question_id not in by_question
                or not int(by_question[question_id]["region_count"])
            ]
            unconfirmed = [
                question_id
                for question_id in active_ids
                if question_id in by_question
                and int(by_question[question_id]["region_count"])
                and by_question[question_id]["status"] != "confirmed"
            ]
            geometry_issues = validate_question_frame_set(
                self._validation_items(connection, frame_set_id),
                self._template_pages(connection, task_id),
            )
            if missing or unconfirmed or geometry_issues:
                raise AppError(
                    409,
                    "QUESTION_FRAME_CONFIRMATION_REQUIRED",
                    "必须逐题确认完整题框后才能冻结",
                    {
                        "frameSetId": frame_set_id,
                        "missingQuestionIds": missing,
                        "unconfirmedQuestionIds": unconfirmed,
                        "issues": [issue.as_dict() for issue in geometry_issues],
                    },
                )
            timestamp = now_iso()
            connection.execute(
                """UPDATE question_frame_sets SET status='confirmed',revision=revision+1,
                   confirmed_at=?,confirmed_by=?,updated_at=? WHERE id=?""",
                (timestamp, actor, timestamp, frame_set_id),
            )
            self._refresh_hash(connection, frame_set_id)
            self.database.audit(
                connection,
                task_id,
                "question_frame_set_confirmed",
                actor,
                {"frameSetId": frame_set_id, "questionCount": len(active_ids)},
            )
            return self._serialize(connection, frame_set_id)

    def processing_gate(
        self,
        task_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        context = nullcontext(connection) if connection is not None else self.database.connect()
        with context as active_connection:
            connection = active_connection
            task = self._task(connection, task_id)
            frame_set_id = task["current_question_frame_set_id"]
            if not frame_set_id:
                return {
                    "ready": False,
                    "frameSetId": None,
                    "frameSetVersion": None,
                    "missingQuestionIds": [
                        str(row["id"]) for row in self._active_questions(connection, task_id)
                    ],
                    "unconfirmedQuestionIds": [],
                    "issues": [
                        self._layered_issue(
                            "QUESTION_FRAME_SET_MISSING",
                            "尚未生成并确认题框",
                            "review_question_frames",
                        )
                    ],
                }
            frame_set = self._frame_set(connection, str(frame_set_id))
            active_ids = [str(row["id"]) for row in self._active_questions(connection, task_id)]
            rows = connection.execute(
                """SELECT i.question_id,i.status,COUNT(r.id) AS region_count
                   FROM question_frame_items i
                   LEFT JOIN question_frame_regions r ON r.frame_item_id=i.id
                   WHERE i.frame_set_id=? GROUP BY i.id""",
                (frame_set_id,),
            ).fetchall()
            by_question = {str(row["question_id"]): row for row in rows}
            missing = [
                question_id
                for question_id in active_ids
                if question_id not in by_question
                or not int(by_question[question_id]["region_count"])
            ]
            unconfirmed = [
                question_id
                for question_id in active_ids
                if question_id in by_question
                and int(by_question[question_id]["region_count"])
                and by_question[question_id]["status"] != "confirmed"
            ]
            issues: list[dict[str, object]] = []
            if frame_set["status"] != "confirmed":
                issues.append(
                    self._layered_issue(
                        "QUESTION_FRAME_SET_UNCONFIRMED",
                        "当前题框版本尚未冻结",
                        "review_question_frames",
                    )
                )
            for question_id in missing:
                issues.append(
                    self._layered_issue(
                        "QUESTION_FRAME_MISSING",
                        "题目缺少完整题框",
                        "review_question_frames",
                        question_id,
                    )
                )
            for question_id in unconfirmed:
                issues.append(
                    self._layered_issue(
                        "QUESTION_FRAME_UNCONFIRMED",
                        "题框尚未由教师确认",
                        "confirm_question_frame",
                        question_id,
                    )
                )
            geometry_issues = validate_question_frame_set(
                self._validation_items(connection, str(frame_set_id)),
                self._template_pages(connection, task_id),
            )
            for geometry_issue in geometry_issues:
                issue = geometry_issue.as_dict()
                issue.update(
                    {
                        "layer": "question_frame",
                        "nextAction": "edit_question_frame",
                    }
                )
                issues.append(issue)
            ready = (
                frame_set["status"] == "confirmed"
                and not missing
                and not unconfirmed
                and not geometry_issues
            )
            return {
                "ready": ready,
                "frameSetId": frame_set_id,
                "frameSetVersion": int(frame_set["version_number"]),
                "missingQuestionIds": missing,
                "unconfirmedQuestionIds": unconfirmed,
                "issues": issues,
            }

    def require_processing_ready(self, task_id: str) -> dict[str, object]:
        gate = self.processing_gate(task_id)
        if not gate["ready"]:
            raise AppError(
                409,
                "QUESTION_FRAMES_NOT_CONFIRMED",
                "请先逐题确认完整题框，再上传或处理学生试卷",
                gate,
            )
        return gate

    def _fork_confirmed(
        self,
        connection: sqlite3.Connection,
        frame_set: sqlite3.Row,
        *,
        changed_question_id: str,
        actor: str,
    ) -> str:
        old_id = str(frame_set["id"])
        task_id = str(frame_set["task_id"])
        timestamp = now_iso()
        new_id = uuid.uuid4().hex
        version = self._next_version(connection, task_id)
        connection.execute(
            """INSERT INTO question_frame_sets(
                 id,task_id,version_number,status,revision,base_frame_set_id,source,
                 content_hash,created_by,created_at,updated_at
               ) VALUES(?,?,?,'draft',0,?,'teacher','pending',?,?,?)""",
            (new_id, task_id, version, old_id, actor, timestamp, timestamp),
        )
        old_items = connection.execute(
            "SELECT * FROM question_frame_items WHERE frame_set_id=?",
            (old_id,),
        ).fetchall()
        for old_item in old_items:
            new_item_id = uuid.uuid4().hex
            changed = str(old_item["question_id"]) == changed_question_id
            connection.execute(
                """INSERT INTO question_frame_items(
                     id,frame_set_id,question_id,status,revision,issues_json,
                     carried_from_item_id,confirmed_at,confirmed_by,created_at,updated_at
                   ) VALUES(?,?,?,?,0,?,?,?,?,?,?)""",
                (
                    new_item_id,
                    new_id,
                    old_item["question_id"],
                    "pending" if changed else old_item["status"],
                    old_item["issues_json"],
                    old_item["id"],
                    None if changed else old_item["confirmed_at"],
                    None if changed else old_item["confirmed_by"],
                    timestamp,
                    timestamp,
                ),
            )
            regions = connection.execute(
                "SELECT * FROM question_frame_regions WHERE frame_item_id=? ORDER BY sort_order",
                (old_item["id"],),
            ).fetchall()
            for region in regions:
                connection.execute(
                    """INSERT INTO question_frame_regions(
                         id,frame_item_id,region_key,template_page_id,page_number,
                         coordinate_space,x,y,width,height,sort_order,source,confidence,
                         issues_json,raw_region_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        new_item_id,
                        region["region_key"],
                        region["template_page_id"],
                        region["page_number"],
                        region["coordinate_space"],
                        region["x"],
                        region["y"],
                        region["width"],
                        region["height"],
                        region["sort_order"],
                        region["source"],
                        region["confidence"],
                        region["issues_json"],
                        region["raw_region_json"],
                        timestamp,
                        timestamp,
                    ),
                )
        connection.execute(
            "UPDATE question_frame_sets SET status='superseded',updated_at=? WHERE id=?",
            (timestamp, old_id),
        )
        connection.execute(
            "UPDATE tasks SET current_question_frame_set_id=?,updated_at=? WHERE id=?",
            (new_id, timestamp, task_id),
        )
        invalidate_frame_set_dependents(connection, task_id, old_id)
        self._refresh_hash(connection, new_id)
        self.database.audit(
            connection,
            task_id,
            "question_frame_set_forked",
            actor,
            {
                "baseFrameSetId": old_id,
                "frameSetId": new_id,
                "versionNumber": version,
                "reopenedQuestionId": changed_question_id,
            },
        )
        return new_id

    @staticmethod
    def _layered_issue(
        code: str,
        message: str,
        next_action: str,
        question_id: str | None = None,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "code": code,
            "message": message,
            "layer": "question_frame",
            "nextAction": next_action,
        }
        if question_id:
            value["questionId"] = question_id
        return value

    @staticmethod
    def _candidate_fragments(
        candidate: Mapping[str, object] | None,
    ) -> Sequence[Mapping[str, object]]:
        if candidate is None:
            return ()
        value = candidate.get("fragments")
        if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
            return ()
        return [item for item in value if isinstance(item, Mapping)]

    @staticmethod
    def _text_list(value: object) -> list[str]:
        if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
            return []
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @staticmethod
    def _raise_geometry_issues(issues: Sequence[FrameValidationIssue]) -> None:
        if issues:
            raise AppError(
                422,
                "QUESTION_FRAME_INVALID",
                f"题框几何校验未通过：{issues[0].message}",
                {"issues": [issue.as_dict() for issue in issues]},
            )

    @staticmethod
    def _task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(),
        )
        if row is None:
            raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
        return row

    @staticmethod
    def _frame_set(connection: sqlite3.Connection, frame_set_id: str) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM question_frame_sets WHERE id=?", (frame_set_id,)
            ).fetchone(),
        )
        if row is None:
            raise AppError(404, "QUESTION_FRAME_SET_NOT_FOUND", "题框版本不存在")
        return row

    @staticmethod
    def _item(
        connection: sqlite3.Connection,
        frame_set_id: str,
        question_id: str,
    ) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                """SELECT * FROM question_frame_items
                   WHERE frame_set_id=? AND question_id=?""",
                (frame_set_id, question_id),
            ).fetchone(),
        )
        if row is None:
            raise AppError(404, "QUESTION_FRAME_ITEM_NOT_FOUND", "题目不在该题框版本中")
        return row

    @staticmethod
    def _check_revision(frame_set: sqlite3.Row, expected_revision: int) -> None:
        current = int(frame_set["revision"])
        if current != expected_revision:
            raise AppError(
                409,
                "FRAME_SET_REVISION_CONFLICT",
                "题框版本已被其他操作更新，请刷新后合并草稿",
                {
                    "frameSetId": frame_set["id"],
                    "expectedRevision": expected_revision,
                    "currentRevision": current,
                },
            )

    @staticmethod
    def _ensure_current(connection: sqlite3.Connection, frame_set: sqlite3.Row) -> None:
        row = connection.execute(
            "SELECT current_question_frame_set_id FROM tasks WHERE id=?",
            (frame_set["task_id"],),
        ).fetchone()
        if row is None or row["current_question_frame_set_id"] != frame_set["id"]:
            raise AppError(
                409,
                "QUESTION_FRAME_SET_NOT_CURRENT",
                "该题框版本不是任务的当前版本",
            )

    @staticmethod
    def _active_questions(connection: sqlite3.Connection, task_id: str) -> list[sqlite3.Row]:
        return connection.execute(
            """SELECT id,detected_number,sort_order FROM questions
               WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order,id""",
            (task_id,),
        ).fetchall()

    @staticmethod
    def _template_pages(connection: sqlite3.Connection, task_id: str) -> dict[str, int]:
        rows = connection.execute(
            """SELECT p.id,p.page_number FROM pages p
               JOIN documents d ON d.id=p.document_id
               WHERE d.task_id=? AND d.role='exam'""",
            (task_id,),
        ).fetchall()
        return {str(row["id"]): int(row["page_number"]) for row in rows}

    @staticmethod
    def _next_version(connection: sqlite3.Connection, task_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(version_number),0)+1 FROM question_frame_sets WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return int(row[0])

    def _validation_items(
        self,
        connection: sqlite3.Connection,
        frame_set_id: str,
        replacement: tuple[str, Sequence[Mapping[str, object]]] | None = None,
    ) -> list[dict[str, object]]:
        frame_set = self._frame_set(connection, frame_set_id)
        questions = self._active_questions(connection, str(frame_set["task_id"]))
        replacement_id = replacement[0] if replacement else None
        output: list[dict[str, object]] = []
        for question in questions:
            question_id = str(question["id"])
            fragments: Sequence[Mapping[str, object]]
            if question_id == replacement_id and replacement is not None:
                fragments = list(replacement[1])
            else:
                item = connection.execute(
                    """SELECT id FROM question_frame_items
                       WHERE frame_set_id=? AND question_id=?""",
                    (frame_set_id, question_id),
                ).fetchone()
                fragments = (
                    self._regions_for_validation(connection, str(item["id"]))
                    if item
                    else []
                )
            output.append(
                {
                    "questionId": question_id,
                    "questionNumber": str(question["detected_number"]),
                    "fragments": fragments,
                }
            )
        return output

    @staticmethod
    def _regions_for_validation(
        connection: sqlite3.Connection,
        frame_item_id: str,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            "SELECT * FROM question_frame_regions WHERE frame_item_id=? ORDER BY sort_order",
            (frame_item_id,),
        ).fetchall()
        return [
            {
                "regionKey": row["region_key"],
                "templatePageId": row["template_page_id"],
                "pageNumber": row["page_number"],
                "x": row["x"],
                "y": row["y"],
                "width": row["width"],
                "height": row["height"],
                "sortOrder": row["sort_order"],
            }
            for row in rows
        ]

    @staticmethod
    def _insert_region(
        connection: sqlite3.Connection,
        frame_item_id: str,
        raw: Mapping[str, object],
        timestamp: str,
    ) -> None:
        issues = QuestionFrameService._text_list(raw.get("issues"))
        source = str(raw.get("source", "model"))
        if source not in {"model", "teacher", "legacy"}:
            raise AppError(422, "QUESTION_FRAME_SOURCE_INVALID", "题框来源非法")
        confidence_value = raw.get("confidence")
        confidence = (
            float(cast(Any, confidence_value)) if confidence_value is not None else None
        )
        connection.execute(
            """INSERT INTO question_frame_regions(
                 id,frame_item_id,region_key,template_page_id,page_number,
                 coordinate_space,x,y,width,height,sort_order,source,confidence,
                 issues_json,raw_region_json,created_at,updated_at
               ) VALUES(?,?,?,?,?,'template_page_normalized',?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                frame_item_id,
                str(raw.get("regionKey", "")),
                str(raw.get("templatePageId", "")),
                int(cast(Any, raw.get("pageNumber"))),
                float(cast(Any, raw.get("x"))),
                float(cast(Any, raw.get("y"))),
                float(cast(Any, raw.get("width"))),
                float(cast(Any, raw.get("height"))),
                int(cast(Any, raw.get("sortOrder"))),
                source,
                confidence,
                json_dumps(issues),
                json_dumps(dict(raw)),
                timestamp,
                timestamp,
            ),
        )

    def _refresh_hash(self, connection: sqlite3.Connection, frame_set_id: str) -> None:
        items = connection.execute(
            """SELECT id,question_id,status,revision,issues_json
               FROM question_frame_items WHERE frame_set_id=? ORDER BY question_id""",
            (frame_set_id,),
        ).fetchall()
        payload: list[dict[str, object]] = []
        for item in items:
            regions = connection.execute(
                """SELECT region_key,template_page_id,page_number,x,y,width,height,
                          sort_order,source,confidence,issues_json
                   FROM question_frame_regions WHERE frame_item_id=? ORDER BY sort_order""",
                (item["id"],),
            ).fetchall()
            payload.append(
                {
                    "questionId": item["question_id"],
                    "status": item["status"],
                    "revision": item["revision"],
                    "issues": json_loads(item["issues_json"], []),
                    "regions": [dict(row) for row in regions],
                }
            )
        digest = hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE question_frame_sets SET content_hash=? WHERE id=?",
            (digest, frame_set_id),
        )

    def _serialize(
        self,
        connection: sqlite3.Connection,
        frame_set_id: str,
    ) -> dict[str, object]:
        frame_set = self._frame_set(connection, frame_set_id)
        items = connection.execute(
            """SELECT i.*,q.sort_order AS question_sort_order
               FROM question_frame_items i JOIN questions q ON q.id=i.question_id
               WHERE i.frame_set_id=? ORDER BY q.sort_order,q.id""",
            (frame_set_id,),
        ).fetchall()
        serialized_items: list[dict[str, object]] = []
        for item in items:
            regions = connection.execute(
                """SELECT * FROM question_frame_regions
                   WHERE frame_item_id=? ORDER BY sort_order,id""",
                (item["id"],),
            ).fetchall()
            serialized_items.append(
                {
                    "id": str(item["id"]),
                    "questionId": str(item["question_id"]),
                    "status": str(item["status"]),
                    "revision": int(item["revision"]),
                    "fragments": [
                        {
                            "id": str(region["id"]),
                            "regionKey": str(region["region_key"]),
                            "templatePageId": str(region["template_page_id"]),
                            "pageNumber": int(region["page_number"]),
                            "coordinateSpace": str(region["coordinate_space"]),
                            "x": float(region["x"]),
                            "y": float(region["y"]),
                            "width": float(region["width"]),
                            "height": float(region["height"]),
                            "sortOrder": int(region["sort_order"]),
                            "source": str(region["source"]),
                            "confidence": (
                                float(region["confidence"])
                                if region["confidence"] is not None
                                else None
                            ),
                            "issues": json_loads(region["issues_json"], []),
                        }
                        for region in regions
                    ],
                    "issues": json_loads(item["issues_json"], []),
                    "carriedFromItemId": item["carried_from_item_id"],
                    "confirmedAt": item["confirmed_at"],
                    "confirmedBy": item["confirmed_by"],
                }
            )
        return {
            "id": str(frame_set["id"]),
            "taskId": str(frame_set["task_id"]),
            "versionNumber": int(frame_set["version_number"]),
            "status": str(frame_set["status"]),
            "revision": int(frame_set["revision"]),
            "baseFrameSetId": frame_set["base_frame_set_id"],
            "source": str(frame_set["source"]),
            "contentHash": str(frame_set["content_hash"]),
            "items": serialized_items,
            "createdAt": str(frame_set["created_at"]),
            "createdBy": str(frame_set["created_by"]),
            "updatedAt": str(frame_set["updated_at"]),
            "confirmedAt": frame_set["confirmed_at"],
            "confirmedBy": frame_set["confirmed_by"],
        }
