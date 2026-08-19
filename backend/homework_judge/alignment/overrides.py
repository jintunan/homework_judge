from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from .geometry import Homography, Point, homography_from_control_points


class AlignmentOverrideService:
    """Create an immutable page-level alignment correction and remap its page."""

    def __init__(self, database: Database, *, min_alignment_score: float = 0.55) -> None:
        if not math.isfinite(min_alignment_score) or not 0.0 <= min_alignment_score <= 1.0:
            raise ValueError("min_alignment_score must be finite and between zero and one")
        self.database = database
        self.min_alignment_score = min_alignment_score

    def apply(
        self,
        submission_id: str,
        student_page_id: str,
        *,
        expected_revision: int,
        template_page_id: str | None,
        control_points: list[dict[str, Any]],
        clear_override: bool,
        actor: str,
    ) -> dict[str, Any]:
        page = self.database.fetchone(
            """SELECT sp.*,s.task_id,s.current_processing_revision_id,
                      t.current_question_frame_set_id
               FROM student_pages sp
               JOIN student_submissions s ON s.id=sp.submission_id
               JOIN tasks t ON t.id=s.task_id
               WHERE sp.id=? AND sp.submission_id=?""",
            (student_page_id, submission_id),
        )
        if not page:
            raise AppError(404, "STUDENT_PAGE_NOT_FOUND", "学生原页不存在")
        frame_set_id = page.get("current_question_frame_set_id")
        if not frame_set_id:
            raise AppError(409, "QUESTION_FRAMES_NOT_CONFIRMED", "当前任务没有已确认题框集")

        latest = self.database.fetchone(
            """SELECT * FROM student_page_alignment_revisions
               WHERE student_page_id=? ORDER BY revision_number DESC LIMIT 1""",
            (student_page_id,),
        )
        current_revision = int(latest["revision_number"]) if latest else 0
        if current_revision != expected_revision:
            raise AppError(
                409,
                "ALIGNMENT_REVISION_CONFLICT",
                "页面配准版本已变化，请刷新后重试",
                {
                    "layer": "alignment",
                    "nextAction": "refresh_alignment_revision",
                    "expectedAlignmentRevision": expected_revision,
                    "currentAlignmentRevision": current_revision,
                },
            )

        if clear_override:
            automatic = self.database.fetchone(
                """SELECT * FROM student_page_alignment_revisions
                   WHERE student_page_id=? AND source IN ('model','legacy')
                   ORDER BY revision_number DESC LIMIT 1""",
                (student_page_id,),
            )
            if not automatic or not automatic.get("template_page_id") or not automatic.get(
                "transform_json"
            ):
                raise AppError(
                    409,
                    "ALIGNMENT_AUTO_RESULT_MISSING",
                    "没有可恢复的自动页面配准结果",
                    {"layer": "alignment", "nextAction": "correct_page_alignment"},
                )
            target_template_page_id = str(automatic["template_page_id"])
            transform = self._transform(automatic["transform_json"])
            quality = automatic.get("quality")
            method = str(automatic.get("method") or "restored_auto_alignment")
            alignment_status = str(automatic.get("status") or "aligned")
            serialized_points: list[dict[str, Any]] = []
        else:
            if not template_page_id or len(control_points) < 4:
                raise AppError(
                    422,
                    "ALIGNMENT_CONTROL_POINTS_INVALID",
                    "页面配准校正至少需要四对控制点",
                    {"layer": "alignment", "nextAction": "correct_page_alignment"},
                )
            target_template_page_id = template_page_id
            template_points = [
                Point(float(item["template"]["x"]), float(item["template"]["y"]))
                for item in control_points
            ]
            student_points = [
                Point(float(item["student"]["x"]), float(item["student"]["y"]))
                for item in control_points
            ]
            try:
                transform = homography_from_control_points(template_points, student_points)
            except (KeyError, TypeError, ValueError) as error:
                raise AppError(
                    422,
                    "ALIGNMENT_CONTROL_POINTS_INVALID",
                    "控制点无法形成稳定的页面配准",
                    {
                        "layer": "alignment",
                        "nextAction": "correct_page_alignment",
                        "reason": str(error),
                    },
                ) from error
            residuals = [
                self._distance(transform.map_point(source), target)
                for source, target in zip(template_points, student_points, strict=True)
            ]
            quality = max(0.0, 1.0 - max(residuals, default=0.0) / 10.0)
            method = "teacher_control_points"
            alignment_status = (
                "aligned" if quality >= self.min_alignment_score else "low_quality"
            )
            serialized_points = control_points

        template_page = self.database.fetchone(
            """SELECT p.* FROM pages p JOIN documents d ON d.id=p.document_id
               WHERE p.id=? AND d.task_id=? AND d.role='exam'""",
            (target_template_page_id, page["task_id"]),
        )
        if not template_page:
            raise AppError(
                422,
                "ALIGNMENT_TEMPLATE_PAGE_INVALID",
                "所选模板页不属于当前任务",
                {"layer": "alignment", "nextAction": "select_template_page"},
            )
        frame_regions = self.database.fetchall(
            """SELECT r.*,i.question_id
               FROM question_frame_sets f
               JOIN question_frame_items i ON i.frame_set_id=f.id
               JOIN question_frame_regions r ON r.frame_item_id=i.id
               WHERE f.id=? AND f.status='confirmed' AND i.status='confirmed'
               ORDER BY i.question_id,r.sort_order,r.id""",
            (frame_set_id,),
        )
        frame_regions_by_page: dict[str, list[dict[str, Any]]] = {}
        for frame_region in frame_regions:
            frame_regions_by_page.setdefault(str(frame_region["template_page_id"]), []).append(
                frame_region
            )

        alignment_plans: list[dict[str, Any]] = [
            {
                "id": uuid.uuid4().hex,
                "student_page_id": student_page_id,
                "revision_number": current_revision + 1,
                "template_page_id": target_template_page_id,
                "transform": transform,
                "quality": quality,
                "method": method,
                "status": alignment_status,
                "control_points": serialized_points,
                "metrics": {},
                "source": "teacher",
                "issues": [],
                "created_by": actor,
                "template_page": template_page,
            }
        ]
        previous_processing_revision_id = page.get("current_processing_revision_id")
        if previous_processing_revision_id:
            unaffected = self.database.fetchall(
                """SELECT ar.*,sp.width AS student_width,sp.height AS student_height,
                          tp.page_number AS template_page_number,
                          tp.width AS template_width,tp.height AS template_height
                   FROM student_page_alignment_revisions ar
                   JOIN student_pages sp ON sp.id=ar.student_page_id
                   JOIN pages tp ON tp.id=ar.template_page_id
                   WHERE ar.processing_revision_id=? AND ar.is_current=1
                     AND sp.submission_id=? AND sp.id<>?
                   ORDER BY sp.page_number""",
                (previous_processing_revision_id, submission_id, student_page_id),
            )
            duplicate_target = next(
                (
                    row
                    for row in unaffected
                    if str(row["template_page_id"]) == target_template_page_id
                ),
                None,
            )
            if duplicate_target is not None:
                raise AppError(
                    422,
                    "ALIGNMENT_TEMPLATE_PAGE_CONFLICT",
                    "所选模板页已经与另一张学生页关联",
                    {
                        "layer": "alignment",
                        "nextAction": "correct_page_alignment",
                        "templatePageId": target_template_page_id,
                        "studentPageId": duplicate_target["student_page_id"],
                    },
                )
            for row in unaffected:
                latest_for_page = self.database.fetchone(
                    """SELECT COALESCE(MAX(revision_number),0) AS revision_number
                       FROM student_page_alignment_revisions WHERE student_page_id=?""",
                    (row["student_page_id"],),
                )
                latest_revision_number = (
                    int(latest_for_page["revision_number"]) if latest_for_page else 0
                )
                alignment_plans.append(
                    {
                        "id": uuid.uuid4().hex,
                        "student_page_id": str(row["student_page_id"]),
                        "revision_number": latest_revision_number + 1,
                        "template_page_id": str(row["template_page_id"]),
                        "transform": self._transform(str(row["transform_json"])),
                        "quality": row.get("quality"),
                        "method": row.get("method"),
                        "status": str(row.get("status") or "pending"),
                        "control_points": json_loads(row.get("control_points_json"), []),
                        "metrics": json_loads(row.get("metrics_json"), {}),
                        "source": str(row.get("source") or "model"),
                        "issues": json_loads(row.get("issues_json"), []),
                        "created_by": str(row.get("created_by") or actor),
                        "template_page": {
                            "id": row["template_page_id"],
                            "page_number": row["template_page_number"],
                            "width": row["template_width"],
                            "height": row["template_height"],
                        },
                    }
                )

        alignment_revision_id = str(alignment_plans[0]["id"])
        represented_template_pages = {
            str(plan["template_page_id"]) for plan in alignment_plans
        }
        missing_regions = [
            region
            for region in frame_regions
            if str(region["template_page_id"]) not in represented_template_pages
        ]
        mapping_issues: list[dict[str, Any]] = [
            {
                "code": "mapping_page_missing",
                "message": "题框所在模板页没有可用的学生页配准",
                "layer": "alignment",
                "nextAction": "correct_page_alignment",
                "questionId": region["question_id"],
                "frameRegionId": region["id"],
                "templatePageId": region["template_page_id"],
            }
            for region in missing_regions
        ]
        for plan in alignment_plans:
            if plan["status"] == "aligned":
                continue
            mapping_issues.append(
                {
                    "code": "mapping_alignment_low_quality",
                    "message": "页面配准质量不足，不能开始答案识别",
                    "layer": "alignment",
                    "nextAction": "correct_page_alignment",
                    "studentPageId": plan["student_page_id"],
                    "templatePageId": plan["template_page_id"],
                }
            )
        processing_status = "mapping_needs_review" if mapping_issues else "recognizing"

        timestamp = now_iso()
        processing_revision_id = uuid.uuid4().hex
        input_snapshot = {
            "submissionId": submission_id,
            "studentPageId": student_page_id,
            "frameSetId": frame_set_id,
            "templatePageId": target_template_page_id,
            "alignmentRevision": current_revision + 1,
            "transform": transform.as_rows(),
            "preservedStudentPageIds": [
                str(plan["student_page_id"]) for plan in alignment_plans[1:]
            ],
        }
        input_hash = hashlib.sha256(json_dumps(input_snapshot).encode("utf-8")).hexdigest()
        alignment_plans[0]["metrics"] = {"inputHash": input_hash}
        remapped_frame_count = sum(
            len(frame_regions_by_page.get(str(plan["template_page_id"]), []))
            for plan in alignment_plans
        )
        affected_frame_count = len(frame_regions_by_page.get(target_template_page_id, []))

        with self.database.transaction() as connection:
            locked_latest = connection.execute(
                """SELECT revision_number FROM student_page_alignment_revisions
                   WHERE student_page_id=? ORDER BY revision_number DESC LIMIT 1""",
                (student_page_id,),
            ).fetchone()
            locked_revision = int(locked_latest["revision_number"]) if locked_latest else 0
            if locked_revision != expected_revision:
                raise AppError(
                    409,
                    "ALIGNMENT_REVISION_CONFLICT",
                    "页面配准版本已变化，请刷新后重试",
                    {
                        "layer": "alignment",
                        "nextAction": "refresh_alignment_revision",
                        "expectedAlignmentRevision": expected_revision,
                        "currentAlignmentRevision": locked_revision,
                    },
                )
            locked_submission = connection.execute(
                """SELECT current_processing_revision_id FROM student_submissions
                   WHERE id=?""",
                (submission_id,),
            ).fetchone()
            locked_processing_revision_id = (
                str(locked_submission["current_processing_revision_id"])
                if locked_submission and locked_submission["current_processing_revision_id"]
                else None
            )
            if locked_processing_revision_id != previous_processing_revision_id:
                raise AppError(
                    409,
                    "PROCESSING_REVISION_CONFLICT",
                    "学生答卷处理版本已变化，请刷新后重新校正",
                    {
                        "layer": "alignment",
                        "nextAction": "refresh_submission",
                        "expectedProcessingRevisionId": previous_processing_revision_id,
                        "currentProcessingRevisionId": locked_processing_revision_id,
                    },
                )
            locked_frame = connection.execute(
                "SELECT current_question_frame_set_id FROM tasks WHERE id=?",
                (page["task_id"],),
            ).fetchone()
            if not locked_frame or str(locked_frame["current_question_frame_set_id"] or "") != str(
                frame_set_id
            ):
                raise AppError(
                    409,
                    "QUESTION_FRAME_SET_CHANGED",
                    "题框版本已变化，请按新版本重新处理学生答卷",
                    {
                        "layer": "question_frame",
                        "nextAction": "reprocess_submission",
                        "expectedFrameSetId": frame_set_id,
                        "currentFrameSetId": (
                            locked_frame["current_question_frame_set_id"]
                            if locked_frame
                            else None
                        ),
                    },
                )
            next_processing_revision = int(
                connection.execute(
                    """SELECT COALESCE(MAX(revision_number),0)+1 AS value
                       FROM student_processing_revisions WHERE submission_id=?""",
                    (submission_id,),
                ).fetchone()["value"]
            )
            connection.execute(
                "UPDATE student_processing_revisions SET is_current=0 WHERE submission_id=?",
                (submission_id,),
            )
            connection.execute(
                """INSERT INTO student_processing_revisions(
                     id,submission_id,revision_number,frame_set_id,status,input_hash,is_current,
                     source,issues_json,started_at,finished_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,1,'teacher',?,?,?,?,?)""",
                (
                    processing_revision_id,
                    submission_id,
                    next_processing_revision,
                    frame_set_id,
                    processing_status,
                    input_hash,
                    json_dumps(mapping_issues),
                    timestamp,
                    timestamp if mapping_issues else None,
                    timestamp,
                    timestamp,
                ),
            )
            for plan in alignment_plans:
                plan_transform = plan["transform"]
                if not isinstance(plan_transform, Homography):
                    raise TypeError("alignment plan transform must be a homography")
                connection.execute(
                    """INSERT INTO student_page_alignment_revisions(
                         id,processing_revision_id,student_page_id,revision_number,
                         template_page_id,transform_json,quality,method,status,control_points_json,
                         metrics_json,source,is_current,issues_json,created_by,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
                    (
                        plan["id"],
                        processing_revision_id,
                        plan["student_page_id"],
                        plan["revision_number"],
                        plan["template_page_id"],
                        json_dumps(plan_transform.as_rows()),
                        plan["quality"],
                        plan["method"],
                        plan["status"],
                        json_dumps(plan["control_points"]),
                        json_dumps(plan["metrics"]),
                        plan["source"],
                        json_dumps(plan["issues"]),
                        plan["created_by"],
                        timestamp,
                        timestamp,
                    ),
                )
                for frame_region in frame_regions_by_page.get(
                    str(plan["template_page_id"]), []
                ):
                    mapped = self._map_frame_region(
                        frame_region,
                        plan["template_page"],
                        plan_transform,
                    )
                    region_issues = [str(value) for value in plan["issues"]]
                    region_status = (
                        "ready" if plan["status"] == "aligned" else "needs_review"
                    )
                    connection.execute(
                        """INSERT INTO student_question_regions(
                             id,submission_id,question_id,processing_revision_id,frame_set_id,
                             frame_region_id,alignment_revision_id,sort_order,template_page_id,
                             student_page_id,template_region_json,student_polygon_json,
                             student_bbox_json,status,issues_json,created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            uuid.uuid4().hex,
                            submission_id,
                            frame_region["question_id"],
                            processing_revision_id,
                            frame_set_id,
                            frame_region["id"],
                            plan["id"],
                            frame_region["sort_order"],
                            plan["template_page_id"],
                            plan["student_page_id"],
                            json_dumps(mapped["templateRegion"]),
                            json_dumps(mapped["studentPolygon"]),
                            json_dumps(mapped["studentBox"]),
                            region_status,
                            json_dumps(region_issues),
                            timestamp,
                            timestamp,
                        ),
                    )
            submission_region_status = "needs_review" if mapping_issues else "ready"
            submission_error_code = (
                "MAPPING_NEEDS_REVIEW"
                if mapping_issues
                else "ALIGNMENT_CHANGED_REPROCESS_REQUIRED"
            )
            submission_error_message = (
                "页面映射仍有阻断问题，请继续校正页面配准"
                if mapping_issues
                else "页面配准已校正；请按当前版本重新识别学生答案"
            )
            connection.execute(
                """UPDATE student_submissions
                   SET current_processing_revision_id=?,status='uploaded',
                       question_region_status=?,question_region_error_code=?,
                       question_region_error_message=?,error_code=?,error_message=?,updated_at=?
                   WHERE id=?""",
                (
                    processing_revision_id,
                    submission_region_status,
                    submission_error_code if mapping_issues else None,
                    submission_error_message if mapping_issues else None,
                    submission_error_code,
                    submission_error_message,
                    timestamp,
                    submission_id,
                ),
            )
            self.database.audit(
                connection,
                str(page["task_id"]),
                "student_page_alignment_overridden",
                actor,
                {
                    **input_snapshot,
                    "processingRevisionId": processing_revision_id,
                    "alignmentRevisionId": alignment_revision_id,
                    "clearOverride": clear_override,
                    "affectedFrameCount": affected_frame_count,
                    "remappedFrameCount": remapped_frame_count,
                    "mappingIssueCount": len(mapping_issues),
                },
            )
        return {
            "submissionId": submission_id,
            "studentPageId": student_page_id,
            "processingRevisionId": processing_revision_id,
            "processingRevision": next_processing_revision,
            "alignmentRevisionId": alignment_revision_id,
            "alignmentRevision": current_revision + 1,
            "templatePageId": target_template_page_id,
            "affectedFrameCount": affected_frame_count,
            "remappedFrameCount": remapped_frame_count,
            "issues": mapping_issues,
            "status": "mapping_needs_review" if mapping_issues else "recognition_pending",
            "nextAction": (
                "correct_page_alignment" if mapping_issues else "reprocess_submission"
            ),
        }

    @staticmethod
    def _transform(value: str) -> Homography:
        try:
            return Homography.from_rows(json_loads(value, []))
        except (TypeError, ValueError) as error:
            raise AppError(
                409,
                "ALIGNMENT_AUTO_RESULT_INVALID",
                "自动页面配准结果已损坏，无法恢复",
                {"layer": "alignment", "nextAction": "correct_page_alignment"},
            ) from error

    @staticmethod
    def _distance(left: Point, right: Point) -> float:
        return math.hypot(left.x - right.x, left.y - right.y)

    @staticmethod
    def _map_frame_region(
        region: dict[str, Any],
        template_page: dict[str, Any],
        transform: Homography,
    ) -> dict[str, Any]:
        width = float(template_page["width"])
        height = float(template_page["height"])
        left = float(region["x"]) * width
        top = float(region["y"]) * height
        right = (float(region["x"]) + float(region["width"])) * width
        bottom = (float(region["y"]) + float(region["height"])) * height
        points = tuple(
            transform.map_point(point)
            for point in (
                Point(left, top),
                Point(right, top),
                Point(right, bottom),
                Point(left, bottom),
            )
        )
        min_x = min(point.x for point in points)
        min_y = min(point.y for point in points)
        max_x = max(point.x for point in points)
        max_y = max(point.y for point in points)
        return {
            "templateRegion": {
                "region_key": region["region_key"],
                "page_number": region["page_number"],
                "x": region["x"],
                "y": region["y"],
                "width": region["width"],
                "height": region["height"],
            },
            "studentPolygon": [point.as_dict() for point in points],
            "studentBox": {
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x,
                "height": max_y - min_y,
            },
        }
