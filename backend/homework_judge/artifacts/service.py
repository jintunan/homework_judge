from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..grading.audit import has_teacher_review
from ..grading.contracts import BoundingBox
from ..recognition.client import DashScopeClient
from .annotation_layout import AnnotationMark, build_question_marks
from .annotations import render_annotation_artifact
from .error_analysis import analyze_errors, build_error_analysis_request
from .error_report import build_error_report_data, render_error_report


class GradingArtifactService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        model_client: DashScopeClient | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.model_client = model_client

    async def generate(self, run_id: str) -> None:
        run = self.database.fetchone(
            """SELECT g.*,s.student_name FROM grading_runs g
               JOIN student_submissions s ON s.id=g.submission_id WHERE g.id=?""",
            (run_id,),
        )
        if not run:
            raise AppError(404, "GRADING_RUN_NOT_FOUND", "批改运行不存在")
        if run["open_review_count"]:
            raise AppError(409, "GRADING_REVIEW_REQUIRED", "仍有待复核题目，不能生成结果")
        rows = self._question_rows(run_id)
        if not rows:
            raise AppError(409, "GRADING_RESULTS_EMPTY", "没有可生成的逐题结果")
        if any(row["status"] != "final" for row in rows):
            raise AppError(409, "GRADING_RESULTS_NOT_FINAL", "逐题结果尚未全部最终化")

        revision = int(run["result_revision"])
        output_root = self.settings.data_dir / "artifacts" / run_id / f"revision-{revision:04d}"
        timestamp = now_iso()
        self.database.execute(
            """UPDATE grading_runs SET status='generating_annotation',
               stage='generating_annotation',updated_at=? WHERE id=?""",
            (timestamp, run_id),
        )
        pages = self.database.fetchall(
            """SELECT * FROM student_pages WHERE submission_id=? ORDER BY page_number""",
            (run["submission_id"],),
        )
        page_sizes = {str(page["id"]): (int(page["width"]), int(page["height"])) for page in pages}
        occupied: dict[str, list[BoundingBox]] = {}
        marks: list[AnnotationMark] = []
        for row in rows:
            evidence = json_loads(row["evidence_refs_json"], [])
            errors = json_loads(row["error_locations_json"], [])
            teacher_reviewed = has_teacher_review(json_loads(row["tool_observations_json"], []))
            if (
                float(row["final_score"] or 0) < float(row["max_score"])
                and not errors
                and not teacher_reviewed
            ):
                raise AppError(
                    409,
                    "ANNOTATION_ERROR_LOCATION_REQUIRED",
                    f"第 {row['detected_number']} 题缺少可靠错误位置",
                )
            marks.extend(
                build_question_marks(
                    question_result_id=str(row["id"]),
                    question_id=str(row["question_id"]),
                    status=str(row["status"]),
                    final_score=float(row["final_score"] or 0),
                    max_score=float(row["max_score"]),
                    evidence=evidence,
                    error_locations=errors,
                    page_sizes=page_sizes,
                    occupied=occupied,
                )
            )
        annotation = await asyncio.to_thread(
            render_annotation_artifact,
            settings=self.settings,
            pages=pages,
            marks=marks,
            output_dir=output_root / "annotation",
        )
        self._record(
            run_id,
            "annotation",
            revision,
            annotation.pdf_path,
            annotation.preview,
            annotation.content_hash,
        )

        self.database.execute(
            """UPDATE grading_runs SET status='generating_report',
               stage='generating_report',last_successful_stage='generating_annotation',
               updated_at=? WHERE id=?""",
            (now_iso(), run_id),
        )
        self.database.execute(
            """UPDATE grading_artifacts SET status='stale',updated_at=?
               WHERE grading_run_id=? AND artifact_type='error_report'
                 AND status='current'""",
            (now_iso(), run_id),
        )
        region_rows = self.database.fetchall(
            """SELECT rr.*,p.original_image_path FROM student_response_regions rr
               JOIN student_responses sr ON sr.id=rr.student_response_id
               JOIN student_pages p ON p.id=rr.student_page_id
               WHERE sr.submission_id=?""",
            (run["submission_id"],),
        )
        regions_by_id = {str(row["id"]): row for row in region_rows}
        incorrect_rows = [
            row
            for row in rows
            if float(row["final_score"] or 0) < float(row["max_score"])
        ]
        analysis = None
        if incorrect_rows:
            if self.model_client is None:
                raise AppError(
                    503,
                    "ERROR_ANALYSIS_MODEL_NOT_CONFIGURED",
                    "AI 错题诊断模型未配置，无法生成错题报告",
                )
            request = build_error_analysis_request(rows)
            analysis = await analyze_errors(
                self.model_client,
                self.settings,
                request,
                regions_by_id,
            )
        report_data = build_error_report_data(run, rows, analysis)
        report = await asyncio.to_thread(
            render_error_report,
            settings=self.settings,
            data=report_data,
            region_rows=regions_by_id,
            output_dir=output_root / "error-report",
        )
        self._record(
            run_id,
            "error_report",
            revision,
            report.pdf_path,
            report.preview,
            report.content_hash,
        )
        finished = now_iso()
        self.database.execute(
            """UPDATE grading_runs SET status='completed',stage='completed',
               last_successful_stage='generating_report',retryable=0,error_code=NULL,
               error_message=NULL,finished_at=?,updated_at=? WHERE id=?""",
            (finished, finished, run_id),
        )

    def mark_failed(self, run_id: str, error: Exception) -> None:
        code = error.code if isinstance(error, AppError) else "ARTIFACT_GENERATION_FAILED"
        message = error.message if isinstance(error, AppError) else "生成批注或错题报告失败"
        timestamp = now_iso()
        self.database.execute(
            """UPDATE grading_runs SET status='failed',stage='failed',error_code=?,
               error_message=?,retryable=1,attempt_count=attempt_count+1,updated_at=?
               WHERE id=?""",
            (code, message, timestamp, run_id),
        )

    def _question_rows(self, run_id: str) -> list[dict[str, Any]]:
        return self.database.fetchall(
            """SELECT r.*,q.detected_number,q.stem,sr.recognized_text
               FROM grading_question_results r
               JOIN questions q ON q.id=r.question_id
               LEFT JOIN student_responses sr ON sr.id=r.student_response_id
               WHERE r.grading_run_id=? ORDER BY q.sort_order""",
            (run_id,),
        )

    def _record(
        self,
        run_id: str,
        artifact_type: str,
        revision: int,
        path: Path,
        preview: dict[str, object],
        content_hash: str,
    ) -> None:
        relative = path.relative_to(self.settings.data_dir).as_posix()
        timestamp = now_iso()
        existing = self.database.fetchone(
            """SELECT id FROM grading_artifacts
               WHERE grading_run_id=? AND artifact_type=? AND result_revision=?""",
            (run_id, artifact_type, revision),
        )
        artifact_id = str(existing["id"]) if existing else uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE grading_artifacts SET status='stale',updated_at=?
                   WHERE grading_run_id=? AND artifact_type=? AND status='current'
                     AND id<>?""",
                (timestamp, run_id, artifact_type, artifact_id),
            )
            connection.execute(
                """INSERT INTO grading_artifacts(
                     id,grading_run_id,artifact_type,result_revision,status,relative_path,
                     preview_json,content_hash,created_at,updated_at
                   ) VALUES(?,?,?,?,'current',?,?,?,?,?)
                   ON CONFLICT(grading_run_id,artifact_type,result_revision) DO UPDATE SET
                     status='current',relative_path=excluded.relative_path,
                     preview_json=excluded.preview_json,content_hash=excluded.content_hash,
                     error_code=NULL,error_message=NULL,updated_at=excluded.updated_at""",
                (
                    artifact_id,
                    run_id,
                    artifact_type,
                    revision,
                    relative,
                    json_dumps(preview),
                    content_hash,
                    timestamp,
                    timestamp,
                ),
            )
