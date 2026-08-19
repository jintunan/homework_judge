from __future__ import annotations

import uuid
from typing import Any

from ..config import Settings
from ..db.database import Database, json_dumps, now_iso
from ..errors import AppError
from ..files.renderer import prepare_document_pages
from ..matching.matcher import build_matches
from ..question_frames.service import QuestionFrameService
from ..recognition.service import RecognitionService


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        recognition: RecognitionService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.recognition = recognition

    def create_parent_run(self, task_id: str) -> str:
        run_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO runs(
                    id,task_id,kind,status,stage,created_at
                   ) VALUES(?,?,'full_pipeline','queued','queued',?)""",
                (run_id, task_id, timestamp),
            )
            connection.execute(
                """UPDATE tasks SET status='queued',active_run_id=?,last_error_code=NULL,
                   last_error_message=NULL,updated_at=? WHERE id=?""",
                (run_id, timestamp, task_id),
            )
            self.database.audit(
                connection,
                task_id,
                "processing_started",
                self.settings.teacher_name,
                {"runId": run_id},
            )
        return run_id

    def _update_task(self, task_id: str, status: str) -> None:
        self.database.execute(
            "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
            (status, now_iso(), task_id),
        )

    def _update_parent(
        self,
        run_id: str,
        status: str,
        stage: str,
        current: int,
        total: int,
    ) -> None:
        self.database.execute(
            """UPDATE runs SET status=?,stage=?,progress_current=?,progress_total=?,
               started_at=COALESCE(started_at,?) WHERE id=?""",
            (status, stage, current, total, now_iso(), run_id),
        )

    def _new_stage_run(self, task_id: str, kind: str, prompt_version: str = "") -> str:
        run_id = uuid.uuid4().hex
        self.database.execute(
            """INSERT INTO runs(
                id,task_id,kind,status,stage,model_id,prompt_version,started_at,created_at
               ) VALUES(?,?,?,'running',?,?,?, ?,?)""",
            (
                run_id,
                task_id,
                kind,
                kind,
                self.settings.dashscope_model if "recognition" in kind else None,
                prompt_version or None,
                now_iso(),
                now_iso(),
            ),
        )
        return run_id

    def _finish_stage(
        self,
        run_id: str,
        raw: Any,
        usage: dict[str, int] | None = None,
    ) -> None:
        records = raw if isinstance(raw, list) else []
        parse_issues = [
            issue
            for record in records
            if isinstance(record, dict)
            for issue in record.get("parseIssues", [])
            if isinstance(issue, dict)
        ]
        self.database.execute(
            """UPDATE runs SET status='succeeded',progress_current=progress_total,
               raw_response_json=?,parse_issues_json=?,usage_json=?,finished_at=? WHERE id=?""",
            (
                json_dumps(raw),
                json_dumps(parse_issues),
                json_dumps(usage or {}),
                now_iso(),
                run_id,
            ),
        )

    def _documents(self, task_id: str) -> dict[str, dict[str, Any]]:
        rows = self.database.fetchall("SELECT * FROM documents WHERE task_id=?", (task_id,))
        return {str(row["role"]): row for row in rows}

    async def _prepare_pages(
        self,
        task_id: str,
        documents: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {}
        for role in ("exam", "answer"):
            document = documents[role]
            pages = await prepare_document_pages(
                self.settings,
                task_id,
                str(document["id"]),
                str(document["relative_path"]),
                str(document["mime_type"]),
            )
            with self.database.transaction() as connection:
                connection.execute("DELETE FROM pages WHERE document_id=?", (document["id"],))
                for page in pages:
                    connection.execute(
                        """INSERT INTO pages(
                            id,document_id,page_number,image_path,width,height,sha256
                           ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            page.id,
                            document["id"],
                            page.page_number,
                            page.relative_path,
                            page.width,
                            page.height,
                            page.sha256,
                        ),
                    )
                connection.execute(
                    "UPDATE documents SET page_count=? WHERE id=?",
                    (len(pages), document["id"]),
                )
            output[role] = self.database.fetchall(
                "SELECT * FROM pages WHERE document_id=? ORDER BY page_number",
                (document["id"],),
            )
        return output

    def _save_questions(self, task_id: str, run_id: str, items: list[dict[str, Any]]) -> None:
        candidates: list[dict[str, object]] = []
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM matches WHERE task_id=?", (task_id,))
            connection.execute(
                "UPDATE tasks SET current_question_frame_set_id=NULL WHERE id=?",
                (task_id,),
            )
            connection.execute("DELETE FROM question_frame_sets WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM questions WHERE task_id=?", (task_id,))
            template_pages = {
                int(row["page_number"]): str(row["id"])
                for row in connection.execute(
                    """SELECT p.id,p.page_number FROM pages p
                       JOIN documents d ON d.id=p.document_id
                       WHERE d.task_id=? AND d.role='exam'""",
                    (task_id,),
                ).fetchall()
            }
            for item in items:
                question_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO questions(
                       id,task_id,source_run_id,sort_order,detected_number,normalized_number,
                       stem,options_json,question_type,score,source_pages_json,confidence,
                       issues_json,answer_regions_json,question_regions_json,confirmation_status
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
                    (
                        question_id,
                        task_id,
                        run_id,
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
                        json_dumps(item.get("answer_regions", [])),
                        json_dumps(item.get("question_regions", [])),
                    ),
                )
                fragments: list[dict[str, object]] = []
                for index, raw_region in enumerate(item.get("question_regions", [])):
                    if not isinstance(raw_region, dict):
                        continue
                    page_number = int(raw_region.get("page_number", 0))
                    template_page_id = template_pages.get(page_number)
                    if not template_page_id:
                        continue
                    fragments.append(
                        {
                            "regionKey": f"{question_id}:frame:{index + 1}",
                            "templatePageId": template_page_id,
                            "pageNumber": page_number,
                            "x": float(raw_region["x"]),
                            "y": float(raw_region["y"]),
                            "width": float(raw_region["width"]),
                            "height": float(raw_region["height"]),
                            "sortOrder": len(fragments),
                            "source": "model",
                            "confidence": float(raw_region.get("confidence", item["confidence"])),
                            "issues": [
                                str(value)
                                for value in raw_region.get("issues", [])
                                if str(value).strip()
                            ],
                        }
                    )
                candidates.append(
                    {
                        "questionId": question_id,
                        "fragments": fragments,
                        "issues": list(item.get("issues", [])),
                    }
                )
        QuestionFrameService(self.database).create_draft(
            task_id,
            candidates,
            source="model",
            actor=f"model:{self.settings.dashscope_model}",
        )

    def _save_answers(self, task_id: str, run_id: str, items: list[dict[str, Any]]) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM matches WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM answer_entries WHERE task_id=?", (task_id,))
            for item in items:
                connection.execute(
                    """INSERT INTO answer_entries(
                       id,task_id,source_run_id,sort_order,number_hint,normalized_number,
                       stem_hint,answer,explanation,source_pages_json,confidence,issues_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        task_id,
                        run_id,
                        item["sort_order"],
                        item["number_hint"],
                        item["normalized_number"],
                        item["stem_hint"],
                        item["answer"],
                        item["explanation"],
                        json_dumps(item["source_pages"]),
                        item["confidence"],
                        json_dumps(item["issues"]),
                    ),
                )

    def _save_matches(self, task_id: str, matches: list[dict[str, Any]]) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM matches WHERE task_id=?", (task_id,))
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
                        now_iso(),
                    ),
                )

    async def run(self, task_id: str, parent_run_id: str) -> None:
        stage_run_id: str | None = None
        try:
            documents = self._documents(task_id)
            if set(documents) != {"exam", "answer"}:
                raise AppError(409, "FILES_INCOMPLETE", "任务缺少试卷或参考答案")
            self._update_task(task_id, "preparing")
            self._update_parent(parent_run_id, "running", "preparing", 0, 4)
            pages = await self._prepare_pages(task_id, documents)
            self._update_parent(parent_run_id, "running", "exam_recognizing", 1, 4)
            self._update_task(task_id, "exam_recognizing")

            stage_run_id = self._new_stage_run(
                task_id,
                "exam_recognition",
                self.recognition.prompt_version("exam"),
            )
            questions, raw, usage = await self.recognition.recognize("exam", pages["exam"])
            self._save_questions(task_id, stage_run_id, questions)
            self._finish_stage(stage_run_id, raw, usage)

            self._update_parent(parent_run_id, "running", "answer_recognizing", 2, 4)
            self._update_task(task_id, "answer_recognizing")
            stage_run_id = self._new_stage_run(
                task_id,
                "answer_recognition",
                self.recognition.prompt_version("answer"),
            )
            answers, raw, usage = await self.recognition.recognize("answer", pages["answer"])
            self._save_answers(task_id, stage_run_id, answers)
            self._finish_stage(stage_run_id, raw, usage)

            self._update_parent(parent_run_id, "running", "matching", 3, 4)
            self._update_task(task_id, "matching")
            stage_run_id = self._new_stage_run(task_id, "matching")
            question_rows = self.database.fetchall(
                "SELECT * FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order",
                (task_id,),
            )
            answer_rows = self.database.fetchall(
                "SELECT * FROM answer_entries WHERE task_id=? ORDER BY sort_order",
                (task_id,),
            )
            matches, used = build_matches(
                task_id,
                question_rows,
                answer_rows,
                self.settings.auto_match_threshold,
                self.settings.auto_match_margin,
            )
            self._save_matches(task_id, matches)
            self._finish_stage(
                stage_run_id,
                {
                    "questionCount": len(question_rows),
                    "answerCount": len(answer_rows),
                    "usedAnswerCount": len(used),
                },
            )

            timestamp = now_iso()
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE runs SET status='succeeded',stage='review_pending',
                       progress_current=4,progress_total=4,finished_at=? WHERE id=?""",
                    (timestamp, parent_run_id),
                )
                connection.execute(
                    """UPDATE tasks SET status='review_pending',updated_at=? WHERE id=?""",
                    (timestamp, task_id),
                )
                self.database.audit(
                    connection,
                    task_id,
                    "processing_succeeded",
                    "system",
                    {"runId": parent_run_id, "questionCount": len(question_rows)},
                )
        except Exception as error:
            code = error.code if isinstance(error, AppError) else "PROCESSING_FAILED"
            message = (
                error.message if isinstance(error, AppError) else "处理失败，请查看运行记录后重试"
            )
            timestamp = now_iso()
            with self.database.transaction() as connection:
                if stage_run_id:
                    connection.execute(
                        """UPDATE runs SET status='failed',error_code=?,error_message=?,
                           finished_at=? WHERE id=? AND status='running'""",
                        (code, message, timestamp, stage_run_id),
                    )
                connection.execute(
                    """UPDATE runs SET status='failed',stage='failed',error_code=?,
                       error_message=?,finished_at=? WHERE id=?""",
                    (code, message, timestamp, parent_run_id),
                )
                connection.execute(
                    """UPDATE tasks SET status='failed',last_error_code=?,
                       last_error_message=?,updated_at=? WHERE id=?""",
                    (code, message, timestamp, task_id),
                )
                self.database.audit(
                    connection,
                    task_id,
                    "stage_failed",
                    "system",
                    {"runId": parent_run_id, "code": code, "message": message},
                )
