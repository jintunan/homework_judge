from __future__ import annotations

import sqlite3
import uuid
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..db.database import Database, json_dumps, json_loads, now_iso
from ..errors import AppError
from ..recognition.client import DashScopeClient
from ..schemas import ErrorLocationUpdate, GradingBlankCorrection, GradingReviewResolution
from .audit import (
    TEACHER_REVIEW_TOOL,
    TEACHER_REVIEW_VERSION,
    AuditIssue,
    audit_question,
)
from .calculation import CALCULATION_SCORING_POLICY_VERSION, partial_credit_score
from .choice import grade_multiple_choice, grade_single_choice
from .contracts import (
    DecisionRecord,
    DecisionStatus,
    EvidenceRef,
    GradingStatus,
    QuestionGradingInput,
    QuestionGradingResult,
    QuestionType,
    ReviewReason,
    ToolObservation,
)
from .dependencies import RubricPoint, propagate_dependencies
from .fill import FillBlankInput, grade_fill_question, hydrate_fill_result_evidence
from .normalization import decimal_string, parse_decimal, quantize_score


class GradingReviewService:
    """Apply teacher decisions without allowing direct writes to computed totals."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def resolve(
        self,
        review_item_id: str,
        payload: GradingReviewResolution,
    ) -> dict[str, Any]:
        item = self.database.fetchone(
            """SELECT i.*,r.question_id,r.question_type,r.max_score,r.decisions_json,
               r.evidence_refs_json,r.error_locations_json,r.tool_observations_json,
               r.answer_snapshot_json,r.grading_config_snapshot_json,r.final_score,
               r.raw_score,r.result_revision,r.id AS result_id,g.submission_id
               FROM grading_review_items i
               JOIN grading_question_results r ON r.id=i.grading_question_result_id
               JOIN grading_runs g ON g.id=i.grading_run_id
               WHERE i.id=?""",
            (review_item_id,),
        )
        if not item:
            raise AppError(404, "GRADING_REVIEW_ITEM_NOT_FOUND", "批改复核项不存在")
        if item["status"] != "open":
            raise AppError(409, "GRADING_REVIEW_ALREADY_RESOLVED", "该复核项已经处理")

        result = self._current_result(item)
        question_type = QuestionType(str(item["question_type"]))
        if question_type is QuestionType.FILL_BLANK:
            blanks = [
                FillBlankInput.model_validate(blank)
                for blank in json_loads(item["grading_config_snapshot_json"], {}).get("blanks", [])
            ]
            result = hydrate_fill_result_evidence(result, blanks)
        if payload.action == "override":
            result = self._apply_override(item, result, payload)
        else:
            result = self._confirm_current(item, result, payload.teacherReason)
        candidate = result.model_copy(update={"status": GradingStatus.GRADED, "review_reasons": []})
        issues = audit_question(candidate)
        review_reason = ReviewReason(str(item["reason"]))
        if not any(issue.reason is review_reason for issue in issues):
            issues.append(AuditIssue(review_reason, "原复核项要求教师人工确认"))
        audit_warnings = [
            {"reason": issue.reason.value, "detail": issue.detail} for issue in issues
        ]
        overridden_reasons = list(dict.fromkeys(warning["reason"] for warning in audit_warnings))
        teacher_observation = ToolObservation(
            tool=TEACHER_REVIEW_TOOL,
            status="overridden" if payload.action == "override" else "confirmed",
            detail=payload.teacherReason,
            payload={
                "reviewItemId": review_item_id,
                "reviewReason": review_reason.value,
                "action": payload.action,
                "scoringPolicyVersion": (
                    CALCULATION_SCORING_POLICY_VERSION
                    if item["question_type"] == QuestionType.CALCULATION.value
                    else None
                ),
                "overriddenReasons": overridden_reasons,
                "auditWarnings": audit_warnings,
            },
            tool_version=TEACHER_REVIEW_VERSION,
        )
        result = candidate.model_copy(
            update={
                "tool_observations": [
                    *candidate.tool_observations,
                    teacher_observation,
                ]
            }
        )
        resolution = {
            **payload.model_dump(mode="json"),
            "overriddenReasons": overridden_reasons,
            "auditWarnings": audit_warnings,
        }

        timestamp = now_iso()
        with self.database.transaction() as connection:
            updated = connection.execute(
                """UPDATE grading_review_items SET status='resolved',resolution_json=?,
                   resolved_by=?,resolved_at=?,updated_at=? WHERE id=? AND status='open'""",
                (
                    json_dumps(resolution),
                    self.settings.teacher_name,
                    timestamp,
                    timestamp,
                    review_item_id,
                ),
            )
            if updated.rowcount != 1:
                raise AppError(409, "GRADING_REVIEW_ALREADY_RESOLVED", "该复核项已经处理")
            remaining_rows = connection.execute(
                """SELECT reason FROM grading_review_items
                   WHERE grading_question_result_id=? AND status='open'
                   ORDER BY created_at""",
                (item["result_id"],),
            ).fetchall()
            remaining = [ReviewReason(str(row["reason"])) for row in remaining_rows]
            status = "needs_review" if remaining else "final"
            final_result = result.model_copy(
                update={
                    "status": (GradingStatus.NEEDS_REVIEW if remaining else GradingStatus.GRADED),
                    "review_reasons": remaining,
                }
            )
            result_update = connection.execute(
                """UPDATE grading_question_results SET status=?,raw_score=?,final_score=?,
                   decisions_json=?,error_locations_json=?,tool_observations_json=?,
                   review_reasons_json=?,
                   result_revision=result_revision+1,error_code=NULL,error_message=NULL,
                   updated_at=? WHERE id=? AND result_revision=?""",
                (
                    status,
                    str(final_result.raw_score),
                    decimal_string(final_result.final_score),
                    json_dumps([entry.model_dump(mode="json") for entry in final_result.decisions]),
                    json_dumps(
                        [entry.model_dump(mode="json") for entry in final_result.error_locations]
                    ),
                    json_dumps(
                        [entry.model_dump(mode="json") for entry in final_result.tool_observations]
                    ),
                    json_dumps([reason.value for reason in remaining]),
                    timestamp,
                    item["result_id"],
                    item["result_revision"],
                ),
            )
            if result_update.rowcount != 1:
                raise AppError(
                    409,
                    "GRADING_RESULT_REVISION_CONFLICT",
                    "逐题评分结果已被其他审核更新，请刷新后重试",
                    {"expectedGradingRevision": int(item["result_revision"])},
                )
            self._update_breakdown(connection, str(item["result_id"]), final_result)
            connection.execute(
                """INSERT INTO grading_events(
                     grading_run_id,grading_question_result_id,event_type,actor,
                     payload_json,created_at
                   ) VALUES(?,?,'review_resolved',?,?,?)""",
                (
                    item["grading_run_id"],
                    item["result_id"],
                    self.settings.teacher_name,
                    json_dumps({"reviewItemId": review_item_id, **resolution}),
                    timestamp,
                ),
            )
            self._refresh_run(connection, str(item["grading_run_id"]), timestamp)
        return {
            "reviewItemId": review_item_id,
            "gradingRunId": item["grading_run_id"],
            "questionResultId": item["result_id"],
            "status": status,
            "score": decimal_string(final_result.final_score),
            "remainingReasons": [reason.value for reason in remaining],
            "overriddenReasons": overridden_reasons,
        }

    async def correct_blank(
        self,
        result_id: str,
        blank_key: str,
        payload: GradingBlankCorrection,
        model_client: DashScopeClient,
    ) -> dict[str, Any]:
        """Correct one keyed fill result without re-grading or rewriting sibling blanks."""

        with self.database.transaction() as connection:
            context, blank_row, definition = self._blank_correction_rows(
                connection,
                result_id,
                blank_key,
                payload,
            )
            self._validate_blank_correction_versions(connection, context, payload)
            open_review_rows = connection.execute(
                """SELECT reason,grading_blank_result_id,context_json
                   FROM grading_review_items
                   WHERE grading_question_result_id=? AND status='open'""",
                (result_id,),
            ).fetchall()
            selected_keyed_review_reasons = {
                str(row["reason"])
                for row in open_review_rows
                if row["grading_blank_result_id"] == blank_row["id"]
                or json_loads(row["context_json"], {}).get("blankKey") == blank_key
            }

        current = self._result_from_correction_context(context)
        current_by_key = {decision.key: decision for decision in current.decisions}
        selected_before = current_by_key.get(blank_key)
        if selected_before is None:
            raise AppError(
                409,
                "GRADING_BLANK_DECISION_MISSING",
                "当前逐题结果缺少指定 blankKey 的判定，不能按位置猜测",
                {"blankKey": blank_key},
            )

        grading_config = json_loads(context["grading_config_snapshot_json"], {})
        config_blanks = grading_config.get("blanks", [])
        if not isinstance(config_blanks, list):
            config_blanks = []
        keyed_config = {
            str(item.get("blankKey")): item
            for item in config_blanks
            if isinstance(item, dict) and item.get("blankKey")
        }
        if len(keyed_config) != len(config_blanks) or blank_key not in keyed_config:
            raise AppError(
                409,
                "GRADING_BLANK_SNAPSHOT_KEY_MISMATCH",
                "评分快照中的 blankKey 集合不完整或重复，不能按数组下标修正",
                {"blankKey": blank_key},
            )

        selected_config = dict(keyed_config[blank_key])
        replacement_observations: list[ToolObservation] = []
        replacement_reasons: list[ReviewReason] = []
        correction_action: str
        recognized_after = str(blank_row["recognized_answer"])
        if payload.recognizedText is not None:
            correction_action = "recognized_text_rejudged"
            recognized_after = payload.recognizedText
            selected_config.update(
                {
                    "blankKey": blank_key,
                    "maxScore": definition["max_score"],
                    "answerKind": definition["answer_kind"],
                    "standardAnswers": json_loads(definition["standard_answers_json"], []),
                    "synonyms": json_loads(definition["synonyms_json"], []),
                    "studentAnswer": recognized_after,
                    "isBlank": not recognized_after.strip(),
                }
            )
            single_blank = FillBlankInput.model_validate(selected_config)
            single_result = await grade_fill_question(
                QuestionGradingInput(
                    run_id=str(context["grading_run_id"]),
                    question_id=str(context["question_id"]),
                    question_type=QuestionType.FILL_BLANK,
                    max_score=single_blank.maxScore,
                    question_content=str(context["stem"]),
                    standard_answer_snapshot={
                        "blanks": [
                            {
                                "blankKey": blank_key,
                                "maxScore": str(single_blank.maxScore),
                                "answerKind": single_blank.answerKind,
                                "standardAnswers": single_blank.standardAnswers,
                                "synonyms": single_blank.synonyms,
                            }
                        ]
                    },
                    student_response={"blankKey": blank_key, "answer": recognized_after},
                    evidence_regions=current.evidence_refs,
                    recognition_confidence=1,
                    grading_config={"blanks": [selected_config]},
                    frame_set_id=payload.frameSetId,
                    blank_config_version_id=payload.blankConfigVersionId,
                    processing_revision_id=payload.processingRevisionId,
                ),
                model_client,
                confidence_threshold=self.settings.grading_auto_confidence_threshold,
                formula_timeout_ms=self.settings.grading_formula_timeout_ms,
            )
            if len(single_result.decisions) != 1 or single_result.decisions[0].key != blank_key:
                raise AppError(
                    409,
                    "GRADING_BLANK_REJUDGE_KEY_MISMATCH",
                    "单空重判没有返回完全相同的 blankKey",
                    {"blankKey": blank_key},
                )
            selected_after = single_result.decisions[0]
            replacement_observations = single_result.tool_observations
            replacement_reasons = single_result.review_reasons
        else:
            correction_action = "final_status_overridden"
            is_correct = payload.finalStatus == "correct"
            selected_after = selected_before.model_copy(
                update={
                    "status": (
                        DecisionStatus.CORRECT if is_correct else DecisionStatus.INCORRECT
                    ),
                    "score": selected_before.max_score if is_correct else Decimal(0),
                    "reason": payload.teacherReason,
                    "blocked_by": None,
                }
            )

        decisions = [
            selected_after if decision.key == blank_key else decision
            for decision in current.decisions
        ]
        score = quantize_score(sum((decision.score for decision in decisions), Decimal(0)))
        review_reasons = [
            reason
            for reason in current.review_reasons
            if reason.value not in selected_keyed_review_reasons
        ]
        review_reasons = list(dict.fromkeys([*review_reasons, *replacement_reasons]))
        if any(decision.status is DecisionStatus.UNABLE for decision in decisions) and not (
            review_reasons
        ):
            review_reasons = [ReviewReason.MODEL_UNABLE_TO_JUDGE]

        # Evidence regions can be shared by multiple blanks.  Without a keyed ownership
        # record, removing an old location here could silently rewrite a sibling blank.
        error_locations = list(current.error_locations)
        if selected_after.status is DecisionStatus.INCORRECT and selected_after.evidence_refs:
            candidate = selected_after.evidence_refs[0]
            if not any(item.region_id == candidate.region_id for item in error_locations):
                error_locations.append(candidate)

        before_value = {
            "recognizedText": str(blank_row["recognized_answer"]),
            "decision": selected_before.model_dump(mode="json"),
        }
        after_value = {
            "recognizedText": recognized_after,
            "decision": selected_after.model_dump(mode="json"),
        }
        teacher_observation = ToolObservation(
            tool=TEACHER_REVIEW_TOOL,
            status=correction_action,
            detail=payload.teacherReason,
            payload={
                "blankKey": blank_key,
                "action": correction_action,
                "before": before_value,
                "after": after_value,
                "versions": {
                    "frameSetId": payload.frameSetId,
                    "blankConfigVersionId": payload.blankConfigVersionId,
                    "processingRevisionId": payload.processingRevisionId,
                    "gradingRevision": payload.expectedGradingRevision,
                },
            },
            tool_version=TEACHER_REVIEW_VERSION,
        )
        updated_result = current.model_copy(
            update={
                "status": (
                    GradingStatus.NEEDS_REVIEW
                    if review_reasons
                    else GradingStatus.GRADED
                ),
                "raw_score": score,
                "final_score": score,
                "decisions": decisions,
                "error_locations": error_locations,
                "tool_observations": [
                    *current.tool_observations,
                    *replacement_observations,
                    teacher_observation,
                ],
                "review_reasons": review_reasons,
            }
        )
        updated_config_blanks = [
            selected_config if str(item.get("blankKey")) == blank_key else item
            for item in config_blanks
        ]
        grading_config["blanks"] = updated_config_blanks

        timestamp = now_iso()
        new_grading_revision = payload.expectedGradingRevision + 1
        new_run_revision = int(context["run_revision"]) + 1
        with self.database.transaction() as connection:
            fresh_context, fresh_blank, fresh_definition = self._blank_correction_rows(
                connection,
                result_id,
                blank_key,
                payload,
            )
            self._validate_blank_correction_versions(connection, fresh_context, payload)
            if (
                str(fresh_blank["id"]) != str(blank_row["id"])
                or str(fresh_definition["id"]) != str(definition["id"])
            ):
                raise AppError(
                    409,
                    "GRADING_BLANK_VERSION_CONFLICT",
                    "单空判定或配置在修正期间已变化，请刷新后重试",
                )

            new_run_revision = int(fresh_context["run_revision"]) + 1
            artifact_rows = connection.execute(
                "SELECT id FROM grading_artifacts WHERE grading_run_id=? AND status='current'",
                (context["grading_run_id"],),
            ).fetchall()
            affected_artifact_ids = [str(row["id"]) for row in artifact_rows]
            updated = connection.execute(
                """UPDATE grading_question_results SET status=?,raw_score=?,final_score=?,
                   grading_config_snapshot_json=?,decisions_json=?,error_locations_json=?,
                   tool_observations_json=?,review_reasons_json=?,
                   result_revision=result_revision+1,error_code=NULL,error_message=NULL,
                   updated_at=? WHERE id=? AND result_revision=?""",
                (
                    (
                        "needs_review"
                        if updated_result.status is GradingStatus.NEEDS_REVIEW
                        else "final"
                    ),
                    decimal_string(updated_result.raw_score),
                    decimal_string(updated_result.final_score),
                    json_dumps(grading_config),
                    json_dumps(
                        [decision.model_dump(mode="json") for decision in updated_result.decisions]
                    ),
                    json_dumps(
                        [item.model_dump(mode="json") for item in updated_result.error_locations]
                    ),
                    json_dumps(
                        [
                            item.model_dump(mode="json")
                            for item in updated_result.tool_observations
                        ]
                    ),
                    json_dumps([reason.value for reason in updated_result.review_reasons]),
                    timestamp,
                    result_id,
                    payload.expectedGradingRevision,
                ),
            )
            if updated.rowcount != 1:
                raise AppError(
                    409,
                    "GRADING_RESULT_REVISION_CONFLICT",
                    "逐题评分 revision 已变化，请刷新后重试",
                    {
                        "expectedGradingRevision": payload.expectedGradingRevision,
                        "currentGradingRevision": fresh_context["grading_revision"],
                    },
                )

            observations = [
                item.model_dump(mode="json")
                for item in replacement_observations
                if item.payload.get("blankKey") == blank_key
            ]
            blank_status = (
                "correct"
                if selected_after.status is DecisionStatus.CORRECT
                else "needs_review"
                if selected_after.status is DecisionStatus.UNABLE
                else "incorrect"
            )
            blank_update_fields = {
                "exact": json_dumps(
                    [item for item in observations if item["tool"] == "fill_exact_match"]
                ),
                "model": json_dumps(
                    [item for item in observations if item["tool"] == "fill_semantic_model"]
                ),
                "verifier": json_dumps(
                    [item for item in observations if item["tool"].endswith("_verifier")]
                ),
            }
            connection.execute(
                """UPDATE grading_blank_results SET status=?,recognized_answer=?,score=?,
                   exact_match_json=CASE WHEN ? THEN ? ELSE exact_match_json END,
                   model_result_json=CASE WHEN ? THEN ? ELSE model_result_json END,
                   verifier_result_json=CASE WHEN ? THEN ? ELSE verifier_result_json END,
                   final_decision_json=?,evidence_refs_json=?,review_reasons_json=?,updated_at=?
                   WHERE id=? AND grading_question_result_id=? AND blank_key=?""",
                (
                    blank_status,
                    recognized_after,
                    decimal_string(selected_after.score),
                    payload.recognizedText is not None,
                    blank_update_fields["exact"],
                    payload.recognizedText is not None,
                    blank_update_fields["model"],
                    payload.recognizedText is not None,
                    blank_update_fields["verifier"],
                    json_dumps(selected_after.model_dump(mode="json")),
                    json_dumps(
                        [item.model_dump(mode="json") for item in selected_after.evidence_refs]
                    ),
                    json_dumps([reason.value for reason in replacement_reasons]),
                    timestamp,
                    blank_row["id"],
                    result_id,
                    blank_key,
                ),
            )
            self._replace_keyed_blank_review_items(
                connection,
                context=context,
                blank_result_id=str(blank_row["id"]),
                blank_key=blank_key,
                reasons=replacement_reasons,
                timestamp=timestamp,
            )
            open_count = int(
                connection.execute(
                    """SELECT COUNT(*) AS value FROM grading_review_items
                       WHERE grading_run_id=? AND status='open'""",
                    (context["grading_run_id"],),
                ).fetchone()["value"]
            )
            totals = connection.execute(
                """SELECT final_score FROM grading_question_results
                   WHERE grading_run_id=?""",
                (context["grading_run_id"],),
            ).fetchall()
            run_score = quantize_score(
                sum((parse_decimal(row["final_score"] or "0") for row in totals), Decimal(0))
            )
            connection.execute(
                """UPDATE grading_runs SET total_score=?,open_review_count=?,
                   status=CASE WHEN ? > 0 THEN 'needs_review' ELSE 'generating_annotation' END,
                   stage=CASE WHEN ? > 0 THEN 'needs_review' ELSE 'generating_annotation' END,
                   result_revision=result_revision+1,retryable=0,updated_at=? WHERE id=?""",
                (
                    decimal_string(run_score),
                    open_count,
                    open_count,
                    open_count,
                    timestamp,
                    context["grading_run_id"],
                ),
            )
            self._invalidate_artifacts(connection, str(context["grading_run_id"]), timestamp)
            event_payload = {
                "blankKey": blank_key,
                "action": correction_action,
                "teacherReason": payload.teacherReason,
                "before": before_value,
                "after": after_value,
                "versions": {
                    "frameSetId": payload.frameSetId,
                    "blankConfigVersionId": payload.blankConfigVersionId,
                    "processingRevisionId": payload.processingRevisionId,
                    "gradingRevisionBefore": payload.expectedGradingRevision,
                    "gradingRevisionAfter": new_grading_revision,
                    "runRevisionBefore": fresh_context["run_revision"],
                    "runRevisionAfter": new_run_revision,
                },
                "affectedResultIds": {
                    "gradingRunId": context["grading_run_id"],
                    "questionResultId": result_id,
                    "blankResultId": blank_row["id"],
                    "artifactIds": affected_artifact_ids,
                },
            }
            connection.execute(
                """INSERT INTO grading_events(
                     grading_run_id,grading_question_result_id,event_type,actor,
                     payload_json,created_at
                   ) VALUES(?,?,'blank_review_corrected',?,?,?)""",
                (
                    context["grading_run_id"],
                    result_id,
                    self.settings.teacher_name,
                    json_dumps(event_payload),
                    timestamp,
                ),
            )

        return {
            "questionResultId": result_id,
            "gradingRunId": context["grading_run_id"],
            "blankKey": blank_key,
            "gradingRevision": new_grading_revision,
            "runRevision": new_run_revision,
            "frameSetId": payload.frameSetId,
            "blankConfigVersionId": payload.blankConfigVersionId,
            "processingRevisionId": payload.processingRevisionId,
            "blankResult": {
                "id": blank_row["id"],
                "blankKey": blank_key,
                "recognizedAnswer": recognized_after,
                "status": blank_status,
                "score": decimal_string(selected_after.score),
                "maxScore": decimal_string(selected_after.max_score),
                "decision": selected_after.model_dump(mode="json"),
                "reviewReasons": [reason.value for reason in replacement_reasons],
            },
            "affectedResultIds": event_payload["affectedResultIds"],
        }

    def update_error_location(
        self,
        result_id: str,
        payload: ErrorLocationUpdate,
    ) -> dict[str, Any]:
        result = self.database.fetchone(
            """SELECT r.*,g.submission_id FROM grading_question_results r
               JOIN grading_runs g ON g.id=r.grading_run_id WHERE r.id=?""",
            (result_id,),
        )
        if not result:
            raise AppError(404, "GRADING_QUESTION_RESULT_NOT_FOUND", "逐题批改结果不存在")
        locations: list[EvidenceRef] = []
        for requested in payload.errorLocations:
            row = self.database.fetchone(
                """SELECT rr.id AS region_id,rr.student_page_id,p.width,p.height
                   FROM student_response_regions rr
                   JOIN student_responses sr ON sr.id=rr.student_response_id
                   JOIN student_pages p ON p.id=rr.student_page_id
                   WHERE rr.id=? AND rr.student_page_id=? AND sr.submission_id=?
                     AND sr.question_id=?""",
                (
                    requested.regionId,
                    requested.pageId,
                    result["submission_id"],
                    result["question_id"],
                ),
            )
            if not row:
                raise AppError(
                    422,
                    "ERROR_LOCATION_EVIDENCE_INVALID",
                    "错误位置必须属于本题的学生作答证据",
                )
            box = requested.box
            if box.x + box.width > row["width"] or box.y + box.height > row["height"]:
                raise AppError(422, "ERROR_LOCATION_OUT_OF_BOUNDS", "错误位置超出原始页面")
            locations.append(
                EvidenceRef.model_validate(
                    {
                        "page_id": requested.pageId,
                        "region_id": requested.regionId,
                        "original_bbox": requested.box.model_dump(),
                        "recognized_text": requested.recognizedText,
                    }
                )
            )

        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE grading_question_results SET error_locations_json=?,
                   result_revision=result_revision+1,updated_at=? WHERE id=?""",
                (
                    json_dumps([item.model_dump(mode="json") for item in locations]),
                    timestamp,
                    result_id,
                ),
            )
            connection.execute(
                """INSERT INTO grading_events(
                     grading_run_id,grading_question_result_id,event_type,actor,
                     payload_json,created_at
                   ) VALUES(?,?,'error_location_updated',?,?,?)""",
                (
                    result["grading_run_id"],
                    result_id,
                    self.settings.teacher_name,
                    json_dumps(
                        {
                            "teacherReason": payload.teacherReason,
                            "errorLocations": [item.model_dump(mode="json") for item in locations],
                        }
                    ),
                    timestamp,
                ),
            )
            self._invalidate_artifacts(connection, str(result["grading_run_id"]), timestamp)
            connection.execute(
                """UPDATE grading_runs SET result_revision=result_revision+1,
                   updated_at=? WHERE id=?""",
                (timestamp, result["grading_run_id"]),
            )
        return {
            "questionResultId": result_id,
            "errorLocations": [item.model_dump(mode="json") for item in locations],
        }

    def _blank_correction_rows(
        self,
        connection: sqlite3.Connection,
        result_id: str,
        blank_key: str,
        payload: GradingBlankCorrection,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        row = connection.execute(
            """SELECT r.id AS result_id,r.grading_run_id,r.question_id,
                      r.student_response_id,r.question_type,r.status AS result_status,
                      r.raw_score,r.final_score,r.max_score,r.decisions_json,
                      r.evidence_refs_json,r.error_locations_json,r.tool_observations_json,
                      r.review_reasons_json,r.grading_config_snapshot_json,
                      r.result_revision AS grading_revision,
                      g.submission_id,g.task_id,g.input_snapshot_json,
                      g.result_revision AS run_revision,g.is_stale,
                      q.stem,t.current_question_frame_set_id,
                      s.current_processing_revision_id,
                      c.current_blank_config_version_id
               FROM grading_question_results r
               JOIN grading_runs g ON g.id=r.grading_run_id
               JOIN questions q ON q.id=r.question_id
               JOIN tasks t ON t.id=g.task_id
               JOIN student_submissions s ON s.id=g.submission_id
               LEFT JOIN question_grading_configs c ON c.question_id=r.question_id
               WHERE r.id=?""",
            (result_id,),
        ).fetchone()
        if row is None:
            raise AppError(
                404,
                "GRADING_QUESTION_RESULT_NOT_FOUND",
                "逐题批改结果不存在",
            )
        context = dict(row)
        if context["question_type"] != QuestionType.FILL_BLANK.value:
            raise AppError(422, "BLANK_CORRECTION_WRONG_TYPE", "当前题目不是填空题")
        self._validate_blank_correction_versions(connection, context, payload)
        blank = connection.execute(
            """SELECT * FROM grading_blank_results
               WHERE grading_question_result_id=? AND blank_key=?""",
            (result_id, blank_key),
        ).fetchone()
        if blank is None:
            raise AppError(
                404,
                "GRADING_BLANK_RESULT_NOT_FOUND",
                "指定 blankKey 的逐空批改结果不存在",
                {"blankKey": blank_key},
            )
        definition = connection.execute(
            """SELECT * FROM question_blank_definition_versions
               WHERE blank_config_version_id=? AND blank_key=?""",
            (payload.blankConfigVersionId, blank_key),
        ).fetchone()
        if definition is None:
            raise AppError(
                409,
                "GRADING_BLANK_SNAPSHOT_KEY_MISMATCH",
                "捕获的空位配置版本中不存在指定 blankKey",
                {"blankKey": blank_key},
            )
        return context, dict(blank), dict(definition)

    @staticmethod
    def _validate_blank_correction_versions(
        connection: sqlite3.Connection,
        context: dict[str, Any],
        payload: GradingBlankCorrection,
    ) -> None:
        if int(context["grading_revision"]) != payload.expectedGradingRevision:
            raise AppError(
                409,
                "GRADING_RESULT_REVISION_CONFLICT",
                "逐题评分 revision 已变化，请刷新后重试",
                {
                    "expectedGradingRevision": payload.expectedGradingRevision,
                    "currentGradingRevision": context["grading_revision"],
                },
            )
        if context.get("is_stale"):
            raise AppError(409, "GRADING_RUN_STALE", "评分运行依赖的版本已经过期")

        snapshot = json_loads(context.get("input_snapshot_json"), {})
        questions = snapshot.get("questions", []) if isinstance(snapshot, dict) else []
        captured = next(
            (
                item
                for item in questions
                if isinstance(item, dict)
                and str(item.get("questionId")) == str(context["question_id"])
            ),
            None,
        )
        requested = {
            "frameSetId": payload.frameSetId,
            "blankConfigVersionId": payload.blankConfigVersionId,
            "processingRevisionId": payload.processingRevisionId,
        }
        current = {
            "frameSetId": context.get("current_question_frame_set_id"),
            "blankConfigVersionId": context.get("current_blank_config_version_id"),
            "processingRevisionId": context.get("current_processing_revision_id"),
        }
        captured_versions = {
            key: captured.get(key) if captured is not None else None for key in requested
        }
        if captured is None or requested != captured_versions or requested != current:
            raise AppError(
                409,
                "GRADING_BLANK_VERSION_CONFLICT",
                "题框、空位配置或学生处理版本已变化，请刷新后重试",
                {
                    "requested": requested,
                    "captured": captured_versions,
                    "current": current,
                },
            )

        frame = connection.execute(
            """SELECT status FROM question_frame_sets
               WHERE id=? AND task_id=?""",
            (payload.frameSetId, context["task_id"]),
        ).fetchone()
        config = connection.execute(
            """SELECT status,frame_set_id FROM question_blank_config_versions
               WHERE id=? AND question_id=?""",
            (payload.blankConfigVersionId, context["question_id"]),
        ).fetchone()
        processing = connection.execute(
            """SELECT status,is_current,frame_set_id FROM student_processing_revisions
               WHERE id=? AND submission_id=?""",
            (payload.processingRevisionId, context["submission_id"]),
        ).fetchone()
        response = connection.execute(
            """SELECT frame_set_id,blank_config_version_id,processing_revision_id
               FROM student_responses WHERE id=?""",
            (context["student_response_id"],),
        ).fetchone()
        response_versions = (
            {
                "frameSetId": response["frame_set_id"],
                "blankConfigVersionId": response["blank_config_version_id"],
                "processingRevisionId": response["processing_revision_id"],
            }
            if response is not None
            else {}
        )
        valid = (
            frame is not None
            and frame["status"] == "confirmed"
            and config is not None
            and config["status"] in {"auto_confirmed", "teacher_confirmed"}
            and config["frame_set_id"] == payload.frameSetId
            and processing is not None
            and processing["status"] == "ready"
            and bool(processing["is_current"])
            and processing["frame_set_id"] == payload.frameSetId
            and response_versions == requested
        )
        if not valid:
            raise AppError(
                409,
                "GRADING_BLANK_VERSION_CONFLICT",
                "捕获版本不再是可编辑的当前版本",
                {
                    "requested": requested,
                    "response": response_versions,
                },
            )

    @staticmethod
    def _result_from_correction_context(context: dict[str, Any]) -> QuestionGradingResult:
        review_reasons = json_loads(context["review_reasons_json"], [])
        return QuestionGradingResult.model_validate(
            {
                "status": (
                    "needs_review" if context["result_status"] == "needs_review" else "graded"
                ),
                "raw_score": context["raw_score"] or "0",
                "final_score": context["final_score"] or "0",
                "max_score": context["max_score"],
                "decisions": json_loads(context["decisions_json"], []),
                "evidence_refs": json_loads(context["evidence_refs_json"], []),
                "error_locations": json_loads(context["error_locations_json"], []),
                "tool_observations": json_loads(context["tool_observations_json"], []),
                "review_reasons": review_reasons,
            }
        )

    def _replace_keyed_blank_review_items(
        self,
        connection: sqlite3.Connection,
        *,
        context: dict[str, Any],
        blank_result_id: str,
        blank_key: str,
        reasons: list[ReviewReason],
        timestamp: str,
    ) -> None:
        open_rows = connection.execute(
            """SELECT id,grading_blank_result_id,context_json FROM grading_review_items
               WHERE grading_question_result_id=? AND status='open'""",
            (context["result_id"],),
        ).fetchall()
        keyed_ids = [
            str(row["id"])
            for row in open_rows
            if row["grading_blank_result_id"] == blank_result_id
            or json_loads(row["context_json"], {}).get("blankKey") == blank_key
        ]
        for review_id in keyed_ids:
            connection.execute(
                """UPDATE grading_review_items SET status='resolved',resolved_by=?,
                   resolved_at=?,resolution_json=?,updated_at=? WHERE id=? AND status='open'""",
                (
                    self.settings.teacher_name,
                    timestamp,
                    json_dumps({"action": "blank_corrected", "blankKey": blank_key}),
                    timestamp,
                    review_id,
                ),
            )
        for reason in reasons:
            exists = connection.execute(
                """SELECT id FROM grading_review_items
                   WHERE grading_question_result_id=? AND reason=? AND status='open'""",
                (context["result_id"], reason.value),
            ).fetchone()
            if exists is not None:
                continue
            connection.execute(
                """INSERT INTO grading_review_items(
                     id,grading_run_id,grading_question_result_id,grading_blank_result_id,
                     reason,status,context_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'open',?,?,?)""",
                (
                    uuid.uuid4().hex,
                    context["grading_run_id"],
                    context["result_id"],
                    blank_result_id,
                    reason.value,
                    json_dumps({"questionId": context["question_id"], "blankKey": blank_key}),
                    timestamp,
                    timestamp,
                ),
            )

    @staticmethod
    def _current_result(item: dict[str, Any]) -> QuestionGradingResult:
        return QuestionGradingResult.model_validate(
            {
                "status": "needs_review",
                "raw_score": item["raw_score"] or "0",
                "final_score": item["final_score"] or "0",
                "max_score": item["max_score"],
                "decisions": json_loads(item["decisions_json"], []),
                "evidence_refs": json_loads(item["evidence_refs_json"], []),
                "error_locations": json_loads(item["error_locations_json"], []),
                "tool_observations": json_loads(item["tool_observations_json"], []),
                "review_reasons": [item["reason"]],
            }
        )

    def _apply_override(
        self,
        item: dict[str, Any],
        current: QuestionGradingResult,
        payload: GradingReviewResolution,
    ) -> QuestionGradingResult:
        question_type = QuestionType(str(item["question_type"]))
        if payload.recognizedText is not None:
            if question_type not in {
                QuestionType.SINGLE_CHOICE,
                QuestionType.MULTIPLE_CHOICE,
            }:
                raise AppError(
                    422,
                    "RECOGNIZED_TEXT_OVERRIDE_UNSUPPORTED",
                    "本题型请修改分空或评分点判定",
                )
            grading_input = QuestionGradingInput(
                run_id=str(item["grading_run_id"]),
                question_id=str(item["question_id"]),
                question_type=question_type,
                max_score=parse_decimal(item["max_score"]),
                question_content="",
                standard_answer_snapshot=json_loads(item["answer_snapshot_json"], {}),
                student_response={"answer": payload.recognizedText},
                evidence_regions=current.evidence_refs,
                recognition_confidence=1,
                grading_config=json_loads(item["grading_config_snapshot_json"], {}),
            )
            if question_type is QuestionType.SINGLE_CHOICE:
                return grade_single_choice(grading_input)
            return grade_multiple_choice(grading_input)
        if payload.blankDecisions:
            if question_type is not QuestionType.FILL_BLANK:
                raise AppError(422, "BLANK_OVERRIDE_WRONG_TYPE", "本题不是填空题")
            return self._override_blanks(current, payload)
        if payload.pointDecisions:
            if question_type is not QuestionType.CALCULATION:
                raise AppError(422, "POINT_OVERRIDE_WRONG_TYPE", "本题不是计算题")
            return self._override_points(item, current, payload)
        return current

    @staticmethod
    def _confirm_current(
        item: dict[str, Any],
        current: QuestionGradingResult,
        teacher_reason: str,
    ) -> QuestionGradingResult:
        question_type = QuestionType(str(item["question_type"]))
        decisions: list[DecisionRecord] = []
        for decision in current.decisions:
            if decision.status is not DecisionStatus.UNABLE:
                decisions.append(decision)
                continue
            replacement = (
                DecisionStatus.FAILED
                if question_type is QuestionType.CALCULATION
                else DecisionStatus.INCORRECT
            )
            decisions.append(
                decision.model_copy(
                    update={
                        "status": replacement,
                        "score": Decimal(0),
                        "reason": teacher_reason,
                    }
                )
            )
        return current.model_copy(
            update={
                "status": GradingStatus.GRADED,
                "decisions": decisions,
                "review_reasons": [],
            }
        )

    @staticmethod
    def _override_blanks(
        current: QuestionGradingResult,
        payload: GradingReviewResolution,
    ) -> QuestionGradingResult:
        overrides = {item.blankKey: item.status for item in payload.blankDecisions}
        known = {item.key for item in current.decisions}
        if not set(overrides).issubset(known):
            raise AppError(422, "BLANK_OVERRIDE_UNKNOWN", "填空判定引用了不存在的空位")
        decisions: list[DecisionRecord] = []
        for decision in current.decisions:
            override = overrides.get(decision.key)
            if override is None:
                decisions.append(decision)
                continue
            is_correct = override == "correct"
            decisions.append(
                decision.model_copy(
                    update={
                        "status": (
                            DecisionStatus.CORRECT if is_correct else DecisionStatus.INCORRECT
                        ),
                        "score": decision.max_score if is_correct else Decimal(0),
                        "reason": payload.teacherReason,
                    }
                )
            )
        score = quantize_score(sum((item.score for item in decisions), Decimal(0)))
        inferred_error_locations = next(
            (
                item.evidence_refs[:1]
                for item in decisions
                if item.status is DecisionStatus.INCORRECT
            ),
            [],
        )
        error_locations = current.error_locations or inferred_error_locations
        return current.model_copy(
            update={
                "status": GradingStatus.GRADED,
                "raw_score": score,
                "final_score": score,
                "decisions": decisions,
                "error_locations": error_locations,
                "review_reasons": [],
            }
        )

    def _override_points(
        self,
        item: dict[str, Any],
        current: QuestionGradingResult,
        payload: GradingReviewResolution,
    ) -> QuestionGradingResult:
        config = json_loads(item["grading_config_snapshot_json"], {})
        points = [RubricPoint.model_validate(point) for point in config["rubricPoints"]]
        overrides = {entry.pointKey: entry.directStatus for entry in payload.pointDecisions}
        known = {point.key for point in points}
        if not set(overrides).issubset(known):
            raise AppError(422, "POINT_OVERRIDE_UNKNOWN", "评分点判定引用了不存在的评分点")
        existing = {decision.key: decision for decision in current.decisions}
        direct_rows = self.database.fetchall(
            """SELECT point_key,direct_status,direct_score,model_result_json
               FROM grading_point_results
               WHERE grading_question_result_id=?""",
            (item["result_id"],),
        )
        direct_by_key = {str(row["point_key"]): row for row in direct_rows}
        direct_statuses = {
            status.value: status
            for status in (
                DecisionStatus.SATISFIED,
                DecisionStatus.PARTIAL,
                DecisionStatus.FAILED,
                DecisionStatus.UNABLE,
            )
        }
        direct: list[DecisionRecord] = []
        for point in points:
            old = existing[point.key]
            requested = overrides.get(point.key)
            if requested is None:
                stored = direct_by_key.get(point.key)
                status = direct_statuses.get(
                    str(stored["direct_status"]) if stored else "",
                    DecisionStatus.UNABLE,
                )
                score = parse_decimal(stored["direct_score"]) if stored else Decimal(0)
                model_result = json_loads(
                    stored.get("model_result_json") if stored else None,
                    {},
                )
                reason = str(model_result.get("reason") or old.reason)
            else:
                status = direct_statuses[requested]
                score = (
                    point.score
                    if status is DecisionStatus.SATISFIED
                    else partial_credit_score(point.score)
                    if status is DecisionStatus.PARTIAL
                    else Decimal(0)
                )
                reason = payload.teacherReason
            direct.append(
                DecisionRecord(
                    key=point.key,
                    status=status,
                    score=score,
                    max_score=point.score,
                    reason=reason,
                    evidence_refs=old.evidence_refs,
                )
            )
        decisions = propagate_dependencies(points, direct)
        score = quantize_score(sum((entry.score for entry in decisions), Decimal(0)))
        error_locations = next(
            (
                entry.evidence_refs[:1]
                for entry in decisions
                if entry.status in {DecisionStatus.FAILED, DecisionStatus.PARTIAL}
                and entry.evidence_refs
            ),
            [],
        )
        return current.model_copy(
            update={
                "status": GradingStatus.GRADED,
                "raw_score": score,
                "final_score": score,
                "decisions": decisions,
                "error_locations": error_locations,
                "review_reasons": [],
            }
        )

    @staticmethod
    def _update_breakdown(
        connection: sqlite3.Connection,
        result_id: str,
        result: QuestionGradingResult,
    ) -> None:
        for decision in result.decisions:
            blank_status = "correct" if decision.status is DecisionStatus.CORRECT else "incorrect"
            connection.execute(
                """UPDATE grading_blank_results SET status=?,score=?,final_decision_json=?,
                   evidence_refs_json=?,review_reasons_json='[]',updated_at=?
                   WHERE grading_question_result_id=? AND blank_key=?""",
                (
                    blank_status,
                    decimal_string(decision.score),
                    json_dumps(decision.model_dump(mode="json")),
                    json_dumps([item.model_dump(mode="json") for item in decision.evidence_refs]),
                    now_iso(),
                    result_id,
                    decision.key,
                ),
            )
            connection.execute(
                """UPDATE grading_point_results SET final_status=?,final_score=?,
                   blocked_by=?,reason=?,updated_at=?
                   WHERE grading_question_result_id=? AND point_key=?""",
                (
                    decision.status.value,
                    decimal_string(decision.score),
                    decision.blocked_by,
                    decision.reason,
                    now_iso(),
                    result_id,
                    decision.key,
                ),
            )
            if decision.status in {
                DecisionStatus.SATISFIED,
                DecisionStatus.PARTIAL,
                DecisionStatus.FAILED,
            }:
                connection.execute(
                    """UPDATE grading_point_results SET direct_status=?,direct_score=?
                       WHERE grading_question_result_id=? AND point_key=?""",
                    (
                        decision.status.value,
                        decimal_string(decision.score),
                        result_id,
                        decision.key,
                    ),
                )

    def _refresh_run(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        timestamp: str,
    ) -> None:
        open_rows = connection.execute(
            """SELECT i.reason,r.question_type,r.status,r.review_reasons_json
               FROM grading_review_items i
               JOIN grading_question_results r ON r.id=i.grading_question_result_id
               WHERE i.grading_run_id=? AND i.status='open'""",
            (run_id,),
        ).fetchall()
        open_count = len(open_rows)
        has_missing_evidence_placeholder = any(
            row["reason"] == ReviewReason.MISSING_EVIDENCE.value
            and row["question_type"] == QuestionType.CALCULATION.value
            and row["status"] == "needs_review"
            and ReviewReason.MISSING_EVIDENCE.value
            in json_loads(row["review_reasons_json"], [])
            for row in open_rows
        )
        scores = connection.execute(
            """SELECT final_score FROM grading_question_results
               WHERE grading_run_id=?""",
            (run_id,),
        ).fetchall()
        total = quantize_score(
            sum((parse_decimal(row["final_score"] or "0") for row in scores), Decimal(0))
        )
        status = "needs_review" if open_count else "generating_annotation"
        stored_total = None if has_missing_evidence_placeholder else decimal_string(total)
        connection.execute(
            """UPDATE grading_runs SET status=?,stage=?,total_score=?,open_review_count=?,
               result_revision=result_revision+1,retryable=0,updated_at=? WHERE id=?""",
            (
                status,
                status,
                stored_total,
                open_count,
                timestamp,
                run_id,
            ),
        )
        self._invalidate_artifacts(connection, run_id, timestamp)

    @staticmethod
    def _invalidate_artifacts(
        connection: sqlite3.Connection,
        run_id: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            """UPDATE grading_artifacts SET status='stale',updated_at=?
               WHERE grading_run_id=? AND status='current'""",
            (timestamp, run_id),
        )
