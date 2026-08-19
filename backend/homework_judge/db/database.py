from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  active_run_id TEXT,
  last_error_code TEXT,
  last_error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('exam','answer')),
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  page_count INTEGER,
  relative_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(task_id, role)
);
CREATE TABLE IF NOT EXISTS pages (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  image_path TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  UNIQUE(document_id, page_number)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  model_id TEXT,
  prompt_version TEXT,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  raw_response_json TEXT,
  usage_json TEXT,
  parse_issues_json TEXT NOT NULL DEFAULT '[]',
  error_code TEXT,
  error_message TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  source_run_id TEXT NOT NULL REFERENCES runs(id),
  sort_order INTEGER NOT NULL,
  detected_number TEXT NOT NULL,
  normalized_number TEXT NOT NULL,
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '[]',
  question_type TEXT NOT NULL,
  score REAL,
  source_pages_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  is_duplicate INTEGER NOT NULL DEFAULT 0 CHECK(is_duplicate IN (0,1)),
  teacher_override_json TEXT,
  confirmation_status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS answer_entries (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  source_run_id TEXT NOT NULL REFERENCES runs(id),
  sort_order INTEGER NOT NULL,
  number_hint TEXT NOT NULL,
  normalized_number TEXT NOT NULL,
  stem_hint TEXT NOT NULL,
  answer TEXT NOT NULL,
  explanation TEXT NOT NULL,
  source_pages_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  ignored INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
  answer_entry_id TEXT REFERENCES answer_entries(id),
  method TEXT NOT NULL,
  number_score REAL NOT NULL DEFAULT 0,
  stem_score REAL NOT NULL DEFAULT 0,
  order_score REAL NOT NULL DEFAULT 0,
  total_score REAL NOT NULL DEFAULT 0,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  teacher_answer TEXT,
  teacher_explanation TEXT,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_match_answer_unique
  ON matches(answer_entry_id) WHERE answer_entry_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_task ON questions(task_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_answers_task ON answer_entries(task_id, sort_order);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""


STUDENT_WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_submissions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  student_identifier TEXT NOT NULL DEFAULT '',
  student_name TEXT NOT NULL DEFAULT '',
  original_name TEXT,
  mime_type TEXT,
  size_bytes INTEGER CHECK(size_bytes IS NULL OR size_bytes >= 0),
  sha256 TEXT,
  relative_path TEXT,
  page_count INTEGER NOT NULL DEFAULT 0 CHECK(page_count >= 0),
  status TEXT NOT NULL DEFAULT 'uploaded'
    CHECK(status IN ('uploaded','aligning','recognizing','ready','failed')),
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS student_pages (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL CHECK(page_number > 0),
  original_image_path TEXT NOT NULL,
  width INTEGER NOT NULL CHECK(width > 0),
  height INTEGER NOT NULL CHECK(height > 0),
  sha256 TEXT NOT NULL,
  template_page_id TEXT REFERENCES pages(id) ON DELETE SET NULL,
  alignment_transform_json TEXT,
  alignment_quality REAL
    CHECK(alignment_quality IS NULL OR alignment_quality BETWEEN 0 AND 1),
  alignment_method TEXT,
  alignment_status TEXT NOT NULL DEFAULT 'pending'
    CHECK(alignment_status IN ('pending','aligned','low_quality','failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, page_number)
);
CREATE TABLE IF NOT EXISTS student_responses (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
  question_number TEXT NOT NULL,
  recognized_text TEXT NOT NULL DEFAULT '',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  recognition_model_id TEXT,
  raw_recognition_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','recognized','needs_review','failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, question_id)
);
CREATE TABLE IF NOT EXISTS student_response_regions (
  id TEXT PRIMARY KEY,
  student_response_id TEXT NOT NULL REFERENCES student_responses(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  template_page_id TEXT REFERENCES pages(id) ON DELETE SET NULL,
  student_page_id TEXT NOT NULL REFERENCES student_pages(id) ON DELETE CASCADE,
  coordinate_space TEXT NOT NULL DEFAULT 'pixel'
    CHECK(coordinate_space IN ('pixel','normalized')),
  template_bbox_json TEXT NOT NULL,
  student_bbox_json TEXT NOT NULL,
  cropped_image_path TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(student_response_id, sort_order)
);
CREATE INDEX IF NOT EXISTS idx_student_submissions_task
  ON student_submissions(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_student_pages_submission
  ON student_pages(submission_id, page_number);
CREATE INDEX IF NOT EXISTS idx_student_pages_template_page
  ON student_pages(template_page_id);
CREATE INDEX IF NOT EXISTS idx_student_responses_submission
  ON student_responses(submission_id, question_number);
CREATE INDEX IF NOT EXISTS idx_student_responses_question
  ON student_responses(question_id);
CREATE INDEX IF NOT EXISTS idx_student_response_regions_response
  ON student_response_regions(student_response_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_student_response_regions_student_page
  ON student_response_regions(student_page_id);
"""


TEMPLATE_REGION_SCHEMA = """
ALTER TABLE questions ADD COLUMN answer_regions_json TEXT NOT NULL DEFAULT '[]';
"""


STUDENT_RESPONSE_IDENTITY_SCHEMA = """
CREATE TABLE student_responses_v4 (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
  question_number TEXT NOT NULL,
  recognized_text TEXT NOT NULL DEFAULT '',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  recognition_model_id TEXT,
  raw_recognition_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','recognized','needs_review','failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, question_id)
)
"""

QUESTION_REGION_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_question_regions (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  template_page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  student_page_id TEXT NOT NULL REFERENCES student_pages(id) ON DELETE CASCADE,
  template_region_json TEXT NOT NULL,
  student_polygon_json TEXT NOT NULL,
  student_bbox_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready'
    CHECK(status IN ('ready','needs_review')),
  issues_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, question_id, sort_order)
);
CREATE INDEX IF NOT EXISTS idx_student_question_regions_submission
  ON student_question_regions(submission_id, student_page_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_student_question_regions_question
  ON student_question_regions(question_id);
"""


GRADING_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_grading_configs (
  question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  question_type TEXT NOT NULL
    CHECK(question_type IN ('single_choice','multiple_choice','fill_blank','calculation')),
  max_score TEXT NOT NULL,
  config_version INTEGER NOT NULL DEFAULT 1 CHECK(config_version > 0),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_blank_definitions (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  blank_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  max_score TEXT NOT NULL,
  answer_kind TEXT NOT NULL DEFAULT 'text'
    CHECK(answer_kind IN ('text','numeric','formula')),
  standard_answers_json TEXT NOT NULL DEFAULT '[]',
  synonyms_json TEXT NOT NULL DEFAULT '[]',
  region_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(question_id, blank_key),
  UNIQUE(question_id, sort_order)
);
CREATE TABLE IF NOT EXISTS rubric_versions (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL CHECK(version_number > 0),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','frozen')),
  max_score TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','model')),
  model_id TEXT,
  prompt_version TEXT,
  content_hash TEXT,
  confirmed_by TEXT,
  frozen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(question_id, version_number)
);
CREATE TABLE IF NOT EXISTS rubric_points (
  id TEXT PRIMARY KEY,
  rubric_version_id TEXT NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  point_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  criterion TEXT NOT NULL,
  score TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(rubric_version_id, point_key),
  UNIQUE(rubric_version_id, sort_order)
);
CREATE TABLE IF NOT EXISTS rubric_dependencies (
  rubric_version_id TEXT NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  point_id TEXT NOT NULL REFERENCES rubric_points(id) ON DELETE CASCADE,
  depends_on_point_id TEXT NOT NULL REFERENCES rubric_points(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(rubric_version_id, point_id, depends_on_point_id),
  CHECK(point_id <> depends_on_point_id)
);
CREATE TABLE IF NOT EXISTS grading_runs (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN (
      'queued','prechecking','aligning','segmenting','recognizing','grading','auditing',
      'needs_review','generating_annotation','generating_report','completed','failed'
    )),
  stage TEXT NOT NULL DEFAULT 'queued',
  input_hash TEXT NOT NULL,
  input_snapshot_json TEXT NOT NULL DEFAULT '{}',
  config_snapshot_json TEXT NOT NULL DEFAULT '{}',
  result_revision INTEGER NOT NULL DEFAULT 0 CHECK(result_revision >= 0),
  total_score TEXT,
  max_score TEXT,
  progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
  progress_total INTEGER NOT NULL DEFAULT 0 CHECK(progress_total >= 0),
  open_review_count INTEGER NOT NULL DEFAULT 0 CHECK(open_review_count >= 0),
  last_successful_stage TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),
  is_stale INTEGER NOT NULL DEFAULT 0 CHECK(is_stale IN (0,1)),
  error_code TEXT,
  error_message TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grading_question_results (
  id TEXT PRIMARY KEY,
  grading_run_id TEXT NOT NULL REFERENCES grading_runs(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  student_response_id TEXT REFERENCES student_responses(id) ON DELETE SET NULL,
  rubric_version_id TEXT REFERENCES rubric_versions(id),
  input_hash TEXT NOT NULL,
  question_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','grading','auto_graded','needs_review','final','failed')),
  raw_score TEXT,
  final_score TEXT,
  max_score TEXT NOT NULL,
  answer_snapshot_json TEXT NOT NULL DEFAULT '{}',
  grading_config_snapshot_json TEXT NOT NULL DEFAULT '{}',
  decisions_json TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  error_locations_json TEXT NOT NULL DEFAULT '[]',
  tool_observations_json TEXT NOT NULL DEFAULT '[]',
  review_reasons_json TEXT NOT NULL DEFAULT '[]',
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  result_revision INTEGER NOT NULL DEFAULT 0 CHECK(result_revision >= 0),
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(grading_run_id, question_id),
  UNIQUE(grading_run_id, input_hash)
);
CREATE TABLE IF NOT EXISTS grading_blank_results (
  id TEXT PRIMARY KEY,
  grading_question_result_id TEXT NOT NULL
    REFERENCES grading_question_results(id) ON DELETE CASCADE,
  blank_definition_id TEXT REFERENCES question_blank_definitions(id) ON DELETE SET NULL,
  blank_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('correct','incorrect','needs_review')),
  recognized_answer TEXT NOT NULL DEFAULT '',
  score TEXT NOT NULL,
  max_score TEXT NOT NULL,
  exact_match_json TEXT NOT NULL DEFAULT '{}',
  model_result_json TEXT,
  verifier_result_json TEXT,
  final_decision_json TEXT NOT NULL DEFAULT '{}',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  review_reasons_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(grading_question_result_id, blank_key)
);
CREATE TABLE IF NOT EXISTS grading_point_results (
  id TEXT PRIMARY KEY,
  grading_question_result_id TEXT NOT NULL
    REFERENCES grading_question_results(id) ON DELETE CASCADE,
  rubric_point_id TEXT NOT NULL REFERENCES rubric_points(id),
  point_key TEXT NOT NULL,
  direct_status TEXT NOT NULL
    CHECK(direct_status IN ('satisfied','partial','failed','unable')),
  final_status TEXT NOT NULL
    CHECK(final_status IN ('satisfied','partial','failed','unable','blocked_by_dependency')),
  direct_score TEXT NOT NULL,
  final_score TEXT NOT NULL,
  max_score TEXT NOT NULL,
  blocked_by TEXT,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  reason TEXT NOT NULL DEFAULT '',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  model_result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(grading_question_result_id, point_key)
);
CREATE TABLE IF NOT EXISTS grading_review_items (
  id TEXT PRIMARY KEY,
  grading_run_id TEXT NOT NULL REFERENCES grading_runs(id) ON DELETE CASCADE,
  grading_question_result_id TEXT NOT NULL
    REFERENCES grading_question_results(id) ON DELETE CASCADE,
  grading_blank_result_id TEXT REFERENCES grading_blank_results(id) ON DELETE CASCADE,
  grading_point_result_id TEXT REFERENCES grading_point_results(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
  context_json TEXT NOT NULL DEFAULT '{}',
  resolution_json TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grading_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  grading_run_id TEXT NOT NULL REFERENCES grading_runs(id) ON DELETE CASCADE,
  grading_question_result_id TEXT
    REFERENCES grading_question_results(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grading_artifacts (
  id TEXT PRIMARY KEY,
  grading_run_id TEXT NOT NULL REFERENCES grading_runs(id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL CHECK(artifact_type IN ('annotation','error_report')),
  result_revision INTEGER NOT NULL CHECK(result_revision >= 0),
  status TEXT NOT NULL CHECK(status IN ('generating','current','stale','failed')),
  relative_path TEXT,
  preview_json TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(grading_run_id, artifact_type, result_revision)
);
CREATE INDEX IF NOT EXISTS idx_blank_definitions_question
  ON question_blank_definitions(question_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_rubric_versions_question
  ON rubric_versions(question_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_rubric_points_version
  ON rubric_points(rubric_version_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_grading_runs_submission
  ON grading_runs(submission_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_grading_runs_task
  ON grading_runs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_grading_question_results_run
  ON grading_question_results(grading_run_id, status, question_id);
CREATE INDEX IF NOT EXISTS idx_grading_review_items_run
  ON grading_review_items(grading_run_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grading_review_open_unique
  ON grading_review_items(grading_run_id, grading_question_result_id, reason)
  WHERE status='open';
CREATE INDEX IF NOT EXISTS idx_grading_events_run
  ON grading_events(grading_run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grading_artifacts_current
  ON grading_artifacts(grading_run_id, artifact_type)
  WHERE status='current';
"""


VERSIONED_QUESTION_PROCESSING_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_frame_sets (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL CHECK(version_number > 0),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK(status IN ('draft','confirmed','superseded')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  base_frame_set_id TEXT REFERENCES question_frame_sets(id) ON DELETE SET NULL,
  source TEXT NOT NULL CHECK(source IN ('model','teacher','legacy')),
  content_hash TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  confirmed_at TEXT,
  confirmed_by TEXT,
  UNIQUE(task_id, version_number)
);
CREATE TABLE IF NOT EXISTS question_frame_items (
  id TEXT PRIMARY KEY,
  frame_set_id TEXT NOT NULL REFERENCES question_frame_sets(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  issues_json TEXT NOT NULL DEFAULT '[]',
  carried_from_item_id TEXT REFERENCES question_frame_items(id) ON DELETE SET NULL,
  confirmed_at TEXT,
  confirmed_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(frame_set_id, question_id)
);
CREATE TABLE IF NOT EXISTS question_frame_regions (
  id TEXT PRIMARY KEY,
  frame_item_id TEXT NOT NULL REFERENCES question_frame_items(id) ON DELETE CASCADE,
  region_key TEXT NOT NULL,
  template_page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL CHECK(page_number > 0),
  coordinate_space TEXT NOT NULL DEFAULT 'template_page_normalized'
    CHECK(coordinate_space='template_page_normalized'),
  x REAL NOT NULL CHECK(x BETWEEN 0 AND 1),
  y REAL NOT NULL CHECK(y BETWEEN 0 AND 1),
  width REAL NOT NULL CHECK(width > 0 AND width <= 1),
  height REAL NOT NULL CHECK(height > 0 AND height <= 1),
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  source TEXT NOT NULL CHECK(source IN ('model','teacher','legacy')),
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  issues_json TEXT NOT NULL DEFAULT '[]',
  raw_region_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(x + width <= 1.000001),
  CHECK(y + height <= 1.000001),
  UNIQUE(frame_item_id, region_key),
  UNIQUE(frame_item_id, sort_order)
);
CREATE INDEX IF NOT EXISTS idx_question_frame_sets_task
  ON question_frame_sets(task_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_question_frame_items_question
  ON question_frame_items(question_id, frame_set_id);
CREATE INDEX IF NOT EXISTS idx_question_frame_regions_page
  ON question_frame_regions(template_page_id, frame_item_id, sort_order);

CREATE TABLE IF NOT EXISTS question_blank_config_versions (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL CHECK(version_number > 0),
  frame_set_id TEXT NOT NULL REFERENCES question_frame_sets(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','auto_confirmed','teacher_confirmed','stale')),
  source TEXT NOT NULL CHECK(source IN ('model','teacher','legacy')),
  signals_json TEXT NOT NULL DEFAULT '{}',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  advisories_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  confirmed_at TEXT,
  confirmed_by TEXT,
  UNIQUE(question_id, version_number)
);
CREATE TABLE IF NOT EXISTS question_blank_definition_versions (
  id TEXT PRIMARY KEY,
  blank_config_version_id TEXT NOT NULL
    REFERENCES question_blank_config_versions(id) ON DELETE CASCADE,
  legacy_definition_id TEXT REFERENCES question_blank_definitions(id) ON DELETE SET NULL,
  blank_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  max_score TEXT NOT NULL,
  answer_kind TEXT NOT NULL DEFAULT 'text'
    CHECK(answer_kind IN ('text','numeric','formula')),
  standard_answers_json TEXT NOT NULL DEFAULT '[]',
  synonyms_json TEXT NOT NULL DEFAULT '[]',
  template_page_id TEXT REFERENCES pages(id) ON DELETE SET NULL,
  page_number INTEGER CHECK(page_number IS NULL OR page_number > 0),
  coordinate_space TEXT NOT NULL DEFAULT 'template_page_normalized'
    CHECK(coordinate_space='template_page_normalized'),
  x REAL CHECK(x IS NULL OR x BETWEEN 0 AND 1),
  y REAL CHECK(y IS NULL OR y BETWEEN 0 AND 1),
  width REAL CHECK(width IS NULL OR (width > 0 AND width <= 1)),
  height REAL CHECK(height IS NULL OR (height > 0 AND height <= 1)),
  anchor_source TEXT CHECK(anchor_source IS NULL OR anchor_source IN ('model','teacher','legacy')),
  anchor_confidence REAL
    CHECK(anchor_confidence IS NULL OR anchor_confidence BETWEEN 0 AND 1),
  anchor_issues_json TEXT NOT NULL DEFAULT '[]',
  anchor_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(x IS NULL OR width IS NULL OR x + width <= 1.000001),
  CHECK(y IS NULL OR height IS NULL OR y + height <= 1.000001),
  UNIQUE(blank_config_version_id, blank_key),
  UNIQUE(blank_config_version_id, sort_order)
);
CREATE INDEX IF NOT EXISTS idx_blank_config_versions_question
  ON question_blank_config_versions(question_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_blank_config_versions_frame_set
  ON question_blank_config_versions(frame_set_id, status);
CREATE INDEX IF NOT EXISTS idx_blank_definition_versions_config
  ON question_blank_definition_versions(blank_config_version_id, sort_order);

CREATE TABLE IF NOT EXISTS student_processing_revisions (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  revision_number INTEGER NOT NULL CHECK(revision_number > 0),
  frame_set_id TEXT REFERENCES question_frame_sets(id) ON DELETE SET NULL,
  status TEXT NOT NULL
    CHECK(status IN (
      'aligning','mapping_needs_review','recognizing','recognition_needs_review','ready','failed'
    )),
  input_hash TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0,1)),
  source TEXT NOT NULL DEFAULT 'system' CHECK(source IN ('system','teacher','legacy')),
  issues_json TEXT NOT NULL DEFAULT '[]',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, revision_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_student_processing_current
  ON student_processing_revisions(submission_id) WHERE is_current=1;
CREATE INDEX IF NOT EXISTS idx_student_processing_frame_set
  ON student_processing_revisions(frame_set_id, status);

CREATE TABLE IF NOT EXISTS student_page_alignment_revisions (
  id TEXT PRIMARY KEY,
  processing_revision_id TEXT NOT NULL
    REFERENCES student_processing_revisions(id) ON DELETE CASCADE,
  student_page_id TEXT NOT NULL REFERENCES student_pages(id) ON DELETE CASCADE,
  revision_number INTEGER NOT NULL CHECK(revision_number > 0),
  template_page_id TEXT REFERENCES pages(id) ON DELETE SET NULL,
  transform_json TEXT,
  quality REAL CHECK(quality IS NULL OR quality BETWEEN 0 AND 1),
  method TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','aligned','low_quality','failed')),
  control_points_json TEXT NOT NULL DEFAULT '[]',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'model' CHECK(source IN ('model','teacher','legacy')),
  is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
  issues_json TEXT NOT NULL DEFAULT '[]',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(processing_revision_id, student_page_id, revision_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_student_page_alignment_current
  ON student_page_alignment_revisions(processing_revision_id, student_page_id)
  WHERE is_current=1;
CREATE INDEX IF NOT EXISTS idx_student_page_alignment_page
  ON student_page_alignment_revisions(student_page_id, created_at DESC);

CREATE TABLE IF NOT EXISTS student_blank_responses (
  id TEXT PRIMARY KEY,
  student_response_id TEXT NOT NULL REFERENCES student_responses(id) ON DELETE CASCADE,
  blank_definition_id TEXT
    REFERENCES question_blank_definition_versions(id) ON DELETE SET NULL,
  blank_key TEXT NOT NULL,
  recognized_text TEXT NOT NULL DEFAULT '',
  is_blank INTEGER NOT NULL DEFAULT 0 CHECK(is_blank IN (0,1)),
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'recognized' CHECK(status IN ('recognized','needs_review')),
  issues_json TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  recognition_model_id TEXT,
  prompt_version TEXT,
  frame_set_id TEXT REFERENCES question_frame_sets(id) ON DELETE SET NULL,
  blank_config_version_id TEXT
    REFERENCES question_blank_config_versions(id) ON DELETE SET NULL,
  processing_revision_id TEXT
    REFERENCES student_processing_revisions(id) ON DELETE CASCADE,
  raw_item_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(student_response_id, blank_key)
);
CREATE INDEX IF NOT EXISTS idx_student_blank_responses_response
  ON student_blank_responses(student_response_id, blank_key);
CREATE INDEX IF NOT EXISTS idx_student_blank_responses_processing
  ON student_blank_responses(processing_revision_id, status);
"""


STUDENT_RESPONSES_V8_SCHEMA = """
CREATE TABLE student_responses_v8 (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
  -- Legacy callers may omit the revision until the processing services move to v8.
  processing_revision_id TEXT REFERENCES student_processing_revisions(id) ON DELETE CASCADE,
  frame_set_id TEXT REFERENCES question_frame_sets(id) ON DELETE SET NULL,
  blank_config_version_id TEXT
    REFERENCES question_blank_config_versions(id) ON DELETE SET NULL,
  question_number TEXT NOT NULL,
  recognized_text TEXT NOT NULL DEFAULT '',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  recognition_model_id TEXT,
  raw_recognition_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','recognized','needs_review','failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(processing_revision_id, question_id)
)
"""


STUDENT_QUESTION_REGIONS_V8_SCHEMA = """
CREATE TABLE student_question_regions_v8 (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  -- NULL keeps the old writer usable; migrated rows are always revision-bound.
  processing_revision_id TEXT REFERENCES student_processing_revisions(id) ON DELETE CASCADE,
  frame_set_id TEXT REFERENCES question_frame_sets(id) ON DELETE SET NULL,
  frame_region_id TEXT REFERENCES question_frame_regions(id) ON DELETE SET NULL,
  alignment_revision_id TEXT
    REFERENCES student_page_alignment_revisions(id) ON DELETE SET NULL,
  sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
  template_page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  student_page_id TEXT NOT NULL REFERENCES student_pages(id) ON DELETE CASCADE,
  template_region_json TEXT NOT NULL,
  student_polygon_json TEXT NOT NULL,
  student_bbox_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready'
    CHECK(status IN ('ready','needs_review')),
  issues_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(processing_revision_id, question_id, sort_order)
)
"""


AUTO_GRADING_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_auto_grading_attempts (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES student_submissions(id) ON DELETE CASCADE,
  processing_revision_id TEXT NOT NULL UNIQUE
    REFERENCES student_processing_revisions(id) ON DELETE CASCADE,
  grading_run_id TEXT REFERENCES grading_runs(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','running','blocked','needs_review','completed','failed')),
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_grading_submission
  ON student_auto_grading_attempts(submission_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_auto_grading_run
  ON student_auto_grading_attempts(grading_run_id);
"""


LATEST_SCHEMA_VERSION = 11
MIGRATIONS = (
    (1, SCHEMA),
    (2, STUDENT_WORK_SCHEMA),
    (3, TEMPLATE_REGION_SCHEMA),
    (4, STUDENT_RESPONSE_IDENTITY_SCHEMA),
    (5, QUESTION_REGION_SCHEMA),
    (6, GRADING_SCHEMA),
    (7, ""),
    (8, ""),
    (9, ""),
    (10, ""),
    (11, ""),
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_version (
                   version INTEGER PRIMARY KEY,
                   applied_at TEXT NOT NULL
                   )"""
            )
            applied_versions = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_version").fetchall()
            }
            for version, script in MIGRATIONS:
                if version in applied_versions:
                    continue
                if version == 4:
                    self._migrate_student_response_identity(connection)
                    continue
                if version == 5:
                    self._migrate_question_regions(connection)
                    continue
                if version == 7:
                    self._migrate_duplicate_question_state(connection)
                    continue
                if version == 8:
                    self._migrate_versioned_question_processing(connection)
                    continue
                if version == 9:
                    self._migrate_auto_grading(connection)
                    continue
                if version == 10:
                    self._migrate_partial_calculation_scores(connection)
                    continue
                if version == 11:
                    self._migrate_balanced_calculation_rubrics(connection)
                    continue
                if version == 3:
                    columns = {
                        str(row["name"])
                        for row in connection.execute("PRAGMA table_info(questions)").fetchall()
                    }
                    if "answer_regions_json" not in columns:
                        connection.execute(script)
                else:
                    connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES(?, ?)",
                    (version, now_iso()),
                )
            connection.commit()

    def _migrate_partial_calculation_scores(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Expand calculation point checks without losing review-item references."""

        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='grading_point_results'"
        ).fetchone()
        if not table:
            raise RuntimeError("grading_point_results is missing during schema migration")
        if "'partial'" in str(table["sql"]):
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(10, ?)",
                (now_iso(),),
            )
            return

        # SQLite cannot alter a CHECK constraint in place. Foreign keys are
        # disabled only for this transactional table replacement so child rows
        # remain intact and point to the replacement table with the same name.
        connection.commit()
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE grading_point_results_v10 (
                  id TEXT PRIMARY KEY,
                  grading_question_result_id TEXT NOT NULL
                    REFERENCES grading_question_results(id) ON DELETE CASCADE,
                  rubric_point_id TEXT NOT NULL REFERENCES rubric_points(id),
                  point_key TEXT NOT NULL,
                  direct_status TEXT NOT NULL
                    CHECK(direct_status IN ('satisfied','partial','failed','unable')),
                  final_status TEXT NOT NULL CHECK(final_status IN
                    ('satisfied','partial','failed','unable','blocked_by_dependency')),
                  direct_score TEXT NOT NULL,
                  final_score TEXT NOT NULL,
                  max_score TEXT NOT NULL,
                  blocked_by TEXT,
                  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                  reason TEXT NOT NULL DEFAULT '',
                  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
                  model_result_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(grading_question_result_id, point_key)
                )"""
            )
            connection.execute(
                """INSERT INTO grading_point_results_v10(
                     id,grading_question_result_id,rubric_point_id,point_key,direct_status,
                     final_status,direct_score,final_score,max_score,blocked_by,
                     evidence_refs_json,reason,confidence,model_result_json,created_at,updated_at
                   ) SELECT
                     id,grading_question_result_id,rubric_point_id,point_key,direct_status,
                     final_status,direct_score,final_score,max_score,blocked_by,
                     evidence_refs_json,reason,confidence,model_result_json,created_at,updated_at
                   FROM grading_point_results"""
            )
            connection.execute("DROP TABLE grading_point_results")
            connection.execute(
                "ALTER TABLE grading_point_results_v10 RENAME TO grading_point_results"
            )
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("foreign key violation after calculation score migration")
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(10, ?)",
                (now_iso(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    def _migrate_balanced_calculation_rubrics(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Derive immutable balanced versions from legacy frozen calculation rubrics."""

        required_tables = {"rubric_versions", "rubric_points", "rubric_dependencies"}
        available_tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not required_tables <= available_tables:
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(11, ?)",
                (now_iso(),),
            )
            return

        # Local imports keep the database module independent during startup while
        # ensuring legacy versions use exactly the same deterministic allocation
        # and canonical hashing as newly generated rubric drafts.
        import hashlib

        from ..grading.calculation import (
            FINAL_ANSWER_POINT_KEY,
            normalize_calculation_rubric,
        )
        from ..grading.dependencies import RubricPoint
        from ..grading.normalization import decimal_string, parse_decimal
        from ..grading.prompts import RUBRIC_PROMPT_VERSION

        legacy_versions = connection.execute(
            """SELECT rv.*,q.task_id
               FROM rubric_versions rv
               JOIN questions q ON q.id=rv.question_id
               JOIN (
                 SELECT question_id,MAX(version_number) AS version_number
                 FROM rubric_versions WHERE status='frozen' GROUP BY question_id
               ) latest
                 ON latest.question_id=rv.question_id
                AND latest.version_number=rv.version_number
               WHERE rv.status='frozen'
                 AND NOT EXISTS(
                   SELECT 1 FROM rubric_points rp
                   WHERE rp.rubric_version_id=rv.id
                     AND rp.point_key=?
                 )""",
            (FINAL_ANSWER_POINT_KEY,),
        ).fetchall()
        timestamp = now_iso()
        for legacy in legacy_versions:
            point_rows = connection.execute(
                """SELECT * FROM rubric_points
                   WHERE rubric_version_id=? ORDER BY sort_order""",
                (legacy["id"],),
            ).fetchall()
            dependency_rows = connection.execute(
                """SELECT p.point_key,dp.point_key AS dependency_key
                   FROM rubric_dependencies d
                   JOIN rubric_points p ON p.id=d.point_id
                   JOIN rubric_points dp ON dp.id=d.depends_on_point_id
                   WHERE d.rubric_version_id=?""",
                (legacy["id"],),
            ).fetchall()
            dependencies: dict[str, list[str]] = {}
            for dependency in dependency_rows:
                dependencies.setdefault(str(dependency["point_key"]), []).append(
                    str(dependency["dependency_key"])
                )
            proposed = [
                RubricPoint(
                    key=str(point["point_key"]),
                    criterion=str(point["criterion"]),
                    score=parse_decimal(point["score"]),
                    order=int(point["sort_order"]),
                    dependencies=sorted(
                        dependencies.get(str(point["point_key"]), [])
                    ),
                )
                for point in point_rows
            ]
            balanced = normalize_calculation_rubric(
                proposed,
                parse_decimal(legacy["max_score"]),
            )
            next_version = connection.execute(
                """SELECT COALESCE(MAX(version_number),0)+1 AS value
                   FROM rubric_versions WHERE question_id=?""",
                (legacy["question_id"],),
            ).fetchone()
            version_id = uuid.uuid4().hex
            canonical = json_dumps(
                {
                    "maxScore": decimal_string(legacy["max_score"]),
                    "points": [point.model_dump(mode="json") for point in balanced],
                }
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO rubric_versions(
                     id,question_id,version_number,status,max_score,source,model_id,
                     prompt_version,content_hash,confirmed_by,frozen_at,created_at,updated_at
                   ) VALUES(?,?,?,'frozen',?,?,?,?,?,?,?,?,?)""",
                (
                    version_id,
                    legacy["question_id"],
                    int(next_version["value"]),
                    decimal_string(legacy["max_score"]),
                    legacy["source"],
                    legacy["model_id"],
                    f"{RUBRIC_PROMPT_VERSION}:legacy-migration",
                    digest,
                    legacy["confirmed_by"] or "system-policy-migration",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            point_ids = {point.key: uuid.uuid4().hex for point in balanced}
            for point in balanced:
                connection.execute(
                    """INSERT INTO rubric_points(
                         id,rubric_version_id,point_key,sort_order,criterion,score,
                         created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        point_ids[point.key],
                        version_id,
                        point.key,
                        point.order,
                        point.criterion,
                        decimal_string(point.score),
                        timestamp,
                        timestamp,
                    ),
                )
            for point in balanced:
                for dependency in point.dependencies:
                    connection.execute(
                        """INSERT INTO rubric_dependencies(
                             rubric_version_id,point_id,depends_on_point_id,created_at
                           ) VALUES(?,?,?,?)""",
                        (
                            version_id,
                            point_ids[point.key],
                            point_ids[dependency],
                            timestamp,
                        ),
                    )
            self.audit(
                connection,
                str(legacy["task_id"]),
                "rubric_policy_migrated",
                "system",
                {
                    "questionId": legacy["question_id"],
                    "sourceRubricVersionId": legacy["id"],
                    "rubricVersionId": version_id,
                    "promptVersion": f"{RUBRIC_PROMPT_VERSION}:legacy-migration",
                },
            )
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(11, ?)",
            (timestamp,),
        )

    def _migrate_auto_grading(self, connection: sqlite3.Connection) -> None:
        """Bind new grading runs to processing revisions without rewriting history."""

        grading_columns = self._table_columns(connection, "grading_runs")
        if "processing_revision_id" not in grading_columns:
            connection.execute(
                "ALTER TABLE grading_runs ADD COLUMN processing_revision_id TEXT "
                "REFERENCES student_processing_revisions(id) ON DELETE SET NULL"
            )
        if "trigger_source" not in grading_columns:
            connection.execute(
                "ALTER TABLE grading_runs ADD COLUMN trigger_source TEXT NOT NULL "
                "DEFAULT 'manual' CHECK(trigger_source IN ('manual','automatic','retry'))"
            )
        connection.executescript(AUTO_GRADING_SCHEMA)
        # JobManager only deduplicates work inside one process. This partial index is
        # the final guard when requests race or the service restarts mid-workflow.
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_grading_runs_auto_revision
               ON grading_runs(submission_id, processing_revision_id)
               WHERE trigger_source='automatic' AND processing_revision_id IS NOT NULL"""
        )
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(9, ?)",
            (now_iso(),),
        )

    def _migrate_duplicate_question_state(self, connection: sqlite3.Connection) -> None:
        question_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(questions)").fetchall()
        }
        if "is_duplicate" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN is_duplicate "
                "INTEGER NOT NULL DEFAULT 0 CHECK(is_duplicate IN (0,1))"
            )
        grading_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(grading_runs)").fetchall()
        }
        if grading_columns and "is_stale" not in grading_columns:
            connection.execute(
                "ALTER TABLE grading_runs ADD COLUMN is_stale "
                "INTEGER NOT NULL DEFAULT 0 CHECK(is_stale IN (0,1))"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_task_active "
            "ON questions(task_id,is_duplicate,sort_order)"
        )
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(7, ?)",
            (now_iso(),),
        )

    @staticmethod
    def _migration_id(kind: str, *parts: object) -> str:
        value = ":".join(("homework-judge-v8", kind, *(str(part) for part in parts)))
        return uuid.uuid5(uuid.NAMESPACE_URL, value).hex

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()
        }

    def _migrate_versioned_question_processing(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        connection.commit()
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + VERSIONED_QUESTION_PROCESSING_SCHEMA)
            self._add_v8_current_pointers(connection)
            self._migrate_legacy_question_frames(connection)
            self._migrate_legacy_blank_configs(connection)
            self._migrate_legacy_student_processing(connection)
            self._rebuild_student_question_regions_v8(connection)
            self._rebuild_student_responses_v8(connection)
            if self._table_columns(connection, "grading_runs"):
                connection.execute(
                    """UPDATE grading_runs SET is_stale=1
                       WHERE submission_id IN(
                         SELECT submission_id FROM student_processing_revisions
                         WHERE source='legacy'
                       )"""
                )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                details = [tuple(row) for row in violations[:10]]
                raise RuntimeError(f"schema v8 foreign key violations: {details}")
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(8, ?)",
                (now_iso(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    def _add_v8_current_pointers(self, connection: sqlite3.Connection) -> None:
        if "current_question_frame_set_id" not in self._table_columns(connection, "tasks"):
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN current_question_frame_set_id TEXT "
                "REFERENCES question_frame_sets(id) ON DELETE SET NULL"
            )
        if "current_blank_config_version_id" not in self._table_columns(
            connection, "question_grading_configs"
        ):
            connection.execute(
                "ALTER TABLE question_grading_configs "
                "ADD COLUMN current_blank_config_version_id TEXT "
                "REFERENCES question_blank_config_versions(id) ON DELETE SET NULL"
            )
        if "current_processing_revision_id" not in self._table_columns(
            connection, "student_submissions"
        ):
            connection.execute(
                "ALTER TABLE student_submissions ADD COLUMN current_processing_revision_id TEXT "
                "REFERENCES student_processing_revisions(id) ON DELETE SET NULL"
            )

    def _migrate_legacy_question_frames(self, connection: sqlite3.Connection) -> None:
        timestamp = now_iso()
        tasks = connection.execute(
            "SELECT id,created_at,updated_at,current_question_frame_set_id FROM tasks"
        ).fetchall()
        for task_row in tasks:
            task = dict(task_row)
            existing = connection.execute(
                "SELECT id,source FROM question_frame_sets "
                "WHERE task_id=? AND version_number=1",
                (task["id"],),
            ).fetchone()
            frame_set_id = (
                str(existing["id"])
                if existing
                else self._migration_id("frame-set", task["id"], 1)
            )
            if not existing:
                connection.execute(
                    """INSERT INTO question_frame_sets(
                         id,task_id,version_number,status,revision,source,content_hash,
                         created_by,created_at,updated_at
                       ) VALUES(?,?,1,'draft',0,'legacy',?,'migration:v8',?,?)""",
                    (
                        frame_set_id,
                        task["id"],
                        f"legacy:{task['id']}:question-frames:v1",
                        task.get("created_at") or timestamp,
                        task.get("updated_at") or timestamp,
                    ),
                )
            if not task.get("current_question_frame_set_id"):
                connection.execute(
                    "UPDATE tasks SET current_question_frame_set_id=? WHERE id=?",
                    (frame_set_id, task["id"]),
                )
            if existing and str(existing["source"]) != "legacy":
                continue

            template_pages = {
                int(row["page_number"]): str(row["id"])
                for row in connection.execute(
                    """SELECT p.id,p.page_number FROM pages p
                       JOIN documents d ON d.id=p.document_id
                       WHERE d.task_id=? AND d.role='exam'""",
                    (task["id"],),
                ).fetchall()
            }
            questions = connection.execute(
                """SELECT id,question_regions_json,created_at
                   FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order"""
                if "created_at" in self._table_columns(connection, "questions")
                else """SELECT id,question_regions_json
                        FROM questions WHERE task_id=? AND is_duplicate=0 ORDER BY sort_order""",
                (task["id"],),
            ).fetchall()
            for question_row in questions:
                question = dict(question_row)
                item_id = self._migration_id("frame-item", frame_set_id, question["id"])
                item_issues = ["legacy_unverified"]
                raw_regions = json_loads(question.get("question_regions_json"), [])
                if not isinstance(raw_regions, list):
                    raw_regions = []
                    item_issues.append("legacy_regions_invalid")
                valid_regions: list[dict[str, Any]] = []
                used_keys: set[str] = set()
                for index, raw_value in enumerate(raw_regions):
                    if not isinstance(raw_value, dict):
                        item_issues.append("legacy_region_invalid")
                        continue
                    raw = dict(raw_value)
                    raw_box = raw.get("box")
                    box: dict[str, Any] = dict(raw_box) if isinstance(raw_box, dict) else raw
                    try:
                        page_number = int(raw.get("page_number", raw.get("pageNumber", 0)))
                        x = float(box["x"])
                        y = float(box["y"])
                        width = float(box["width"])
                        height = float(box["height"])
                    except (KeyError, TypeError, ValueError):
                        item_issues.append("legacy_region_invalid")
                        continue
                    template_page_id = template_pages.get(page_number)
                    if not template_page_id:
                        item_issues.append("legacy_region_page_missing")
                        continue
                    if (
                        x < 0
                        or y < 0
                        or width <= 0
                        or height <= 0
                        or x + width > 1.000001
                        or y + height > 1.000001
                    ):
                        item_issues.append("legacy_region_out_of_bounds")
                        continue
                    region_key = str(
                        raw.get("region_key") or raw.get("regionKey") or f"R{index + 1}"
                    ).strip()
                    if not region_key or region_key in used_keys:
                        region_key = f"R{index + 1}"
                    while region_key in used_keys:
                        region_key = f"{region_key}_{index + 1}"
                    used_keys.add(region_key)
                    confidence_value = raw.get("confidence")
                    try:
                        confidence = (
                            float(confidence_value) if confidence_value is not None else None
                        )
                    except (TypeError, ValueError):
                        confidence = None
                    if confidence is not None and not 0 <= confidence <= 1:
                        confidence = None
                    issues = raw.get("issues", [])
                    valid_regions.append(
                        {
                            "id": self._migration_id("frame-region", item_id, region_key),
                            "region_key": region_key,
                            "template_page_id": template_page_id,
                            "page_number": page_number,
                            "x": x,
                            "y": y,
                            "width": width,
                            "height": height,
                            "sort_order": index,
                            "confidence": confidence,
                            "issues": issues if isinstance(issues, list) else [],
                            "raw": raw,
                        }
                    )
                if raw_regions and not valid_regions:
                    item_issues.append("legacy_regions_unusable")
                connection.execute(
                    """INSERT OR IGNORE INTO question_frame_items(
                         id,frame_set_id,question_id,status,revision,issues_json,
                         created_at,updated_at
                       ) VALUES(?,?,?,'pending',0,?,?,?)""",
                    (
                        item_id,
                        frame_set_id,
                        question["id"],
                        json_dumps(list(dict.fromkeys(item_issues))),
                        timestamp,
                        timestamp,
                    ),
                )
                for region in valid_regions:
                    connection.execute(
                        """INSERT OR IGNORE INTO question_frame_regions(
                             id,frame_item_id,region_key,template_page_id,page_number,x,y,width,
                             height,sort_order,source,confidence,issues_json,raw_region_json,
                             created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,'legacy',?,?,?,?,?)""",
                        (
                            region["id"],
                            item_id,
                            region["region_key"],
                            region["template_page_id"],
                            region["page_number"],
                            region["x"],
                            region["y"],
                            region["width"],
                            region["height"],
                            region["sort_order"],
                            region["confidence"],
                            json_dumps(region["issues"]),
                            json_dumps(region["raw"]),
                            timestamp,
                            timestamp,
                        ),
                    )

    @staticmethod
    def _layered_issue(
        code: str,
        message: str,
        layer: str,
        question_id: str | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any]:
        issue: dict[str, Any] = {"code": code, "message": message, "layer": layer}
        if question_id is not None:
            issue["questionId"] = question_id
        if next_action is not None:
            issue["nextAction"] = next_action
        return issue

    def _add_unique_layered_issue(
        self,
        issues: list[dict[str, Any]],
        code: str,
        message: str,
        layer: str,
        question_id: str | None = None,
        next_action: str | None = None,
    ) -> None:
        if any(item["code"] == code for item in issues):
            return
        issues.append(
            self._layered_issue(code, message, layer, question_id, next_action)
        )

    @staticmethod
    def _normalized_anchor(
        raw_value: Any,
        template_pages: dict[int, str],
    ) -> dict[str, Any]:
        if not isinstance(raw_value, dict):
            return {
                "raw": raw_value,
                "template_page_id": None,
                "page_number": None,
                "x": None,
                "y": None,
                "width": None,
                "height": None,
            }
        raw = dict(raw_value)
        raw_box = raw.get("box")
        box: dict[str, Any] = dict(raw_box) if isinstance(raw_box, dict) else raw
        page_value = raw.get("page_number", raw.get("pageNumber"))
        try:
            page_number = int(page_value) if page_value is not None else None
        except (TypeError, ValueError):
            page_number = None
        if page_number is not None and page_number <= 0:
            page_number = None
        values: dict[str, float | None] = {}
        for key in ("x", "y", "width", "height"):
            try:
                values[key] = float(box[key]) if box.get(key) is not None else None
            except (TypeError, ValueError):
                values[key] = None
        x, y, width, height = (
            values["x"],
            values["y"],
            values["width"],
            values["height"],
        )
        valid_box = (
            x is not None
            and y is not None
            and width is not None
            and height is not None
            and x >= 0
            and y >= 0
            and width > 0
            and height > 0
            and x + width <= 1.000001
            and y + height <= 1.000001
        )
        if not valid_box:
            x = y = width = height = None
        return {
            "raw": raw,
            "template_page_id": template_pages.get(page_number) if page_number else None,
            "page_number": page_number,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def _migrate_legacy_blank_configs(self, connection: sqlite3.Connection) -> None:
        timestamp = now_iso()
        auto_confirmed_questions: set[str] = set()
        for row in connection.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type='fill_blank_config_auto_confirmed'"
        ).fetchall():
            payload = json_loads(row["payload_json"], {})
            if isinstance(payload, dict) and payload.get("questionId"):
                auto_confirmed_questions.add(str(payload["questionId"]))

        configs = connection.execute(
            """SELECT c.*,q.task_id,q.answer_regions_json
               FROM question_grading_configs c
               JOIN questions q ON q.id=c.question_id
               WHERE c.question_type='fill_blank'"""
        ).fetchall()
        for config_row in configs:
            config = dict(config_row)
            existing = connection.execute(
                "SELECT id FROM question_blank_config_versions "
                "WHERE question_id=? AND version_number=1",
                (config["question_id"],),
            ).fetchone()
            config_version_id = (
                str(existing["id"])
                if existing
                else self._migration_id("blank-config", config["question_id"], 1)
            )
            frame_row = connection.execute(
                "SELECT current_question_frame_set_id FROM tasks WHERE id=?",
                (config["task_id"],),
            ).fetchone()
            frame_set_id = str(frame_row["current_question_frame_set_id"] or "")
            if not frame_set_id:
                continue
            definitions = connection.execute(
                "SELECT * FROM question_blank_definitions WHERE question_id=? "
                "ORDER BY sort_order,id",
                (config["question_id"],),
            ).fetchall()
            answer_regions = json_loads(config.get("answer_regions_json"), [])
            answer_region_count = len(answer_regions) if isinstance(answer_regions, list) else 0
            blockers: list[dict[str, Any]] = []

            if not definitions:
                self._add_unique_layered_issue(
                    blockers,
                    "blank_count_conflict",
                    "旧填空评分配置没有逐空定义，需要重新检测空位。",
                    "blank_config",
                    str(config["question_id"]),
                    "review_blank_config",
                )
                self._add_unique_layered_issue(
                    blockers,
                    "missing_blank_anchor",
                    "旧填空评分配置没有可迁移的空位锚点。",
                    "blank_config",
                    str(config["question_id"]),
                    "review_blank_config",
                )
            if answer_region_count != len(definitions):
                self._add_unique_layered_issue(
                    blockers,
                    "answer_region_count_conflict",
                    "旧答案区域数量与逐空配置数量不一致，需要逐空重新定位。",
                    "blank_config",
                    str(config["question_id"]),
                    "review_blank_config",
                )
            region_fingerprints: list[str] = []
            template_pages = {
                int(row["page_number"]): str(row["id"])
                for row in connection.execute(
                    """SELECT p.id,p.page_number FROM pages p
                       JOIN documents d ON d.id=p.document_id
                       WHERE d.task_id=? AND d.role='exam'""",
                    (config["task_id"],),
                ).fetchall()
            }
            normalized_anchors: dict[str, dict[str, Any]] = {}
            for definition_row in definitions:
                definition = dict(definition_row)
                raw_region = json_loads(definition.get("region_json"), None)
                anchor = self._normalized_anchor(raw_region, template_pages)
                normalized_anchors[str(definition["id"])] = anchor
                if not anchor["page_number"] or not anchor["template_page_id"]:
                    self._add_unique_layered_issue(
                        blockers,
                        "missing_blank_anchor",
                        "旧空位区域缺少可验证的模板页码，需要教师重新定位。",
                        "blank_config",
                        str(config["question_id"]),
                        "review_blank_config",
                    )
                if isinstance(raw_region, dict):
                    region_fingerprints.append(json_dumps(raw_region))
            if len(region_fingerprints) != len(set(region_fingerprints)):
                self._add_unique_layered_issue(
                    blockers,
                    "composite_region_shared",
                    "多个旧空位共享同一区域，不能视为独立空位锚点。",
                    "blank_config",
                    str(config["question_id"]),
                    "review_blank_config",
                )
            if str(config["question_id"]) in auto_confirmed_questions:
                self._add_unique_layered_issue(
                    blockers,
                    "blank_score_missing",
                    "旧配置由题目总分自动均分，缺少每空分值的明确来源。",
                    "blank_config",
                    str(config["question_id"]),
                    "review_blank_config",
                )

            signals = {
                "legacyConfigVersion": int(config.get("config_version") or 1),
                "stemBlankCount": len(definitions),
                "anchorCount": sum(
                    1
                    for anchor in normalized_anchors.values()
                    if anchor["template_page_id"] and anchor["x"] is not None
                ),
                "standardAnswerCount": sum(
                    len(json_loads(dict(row).get("standard_answers_json"), []))
                    for row in definitions
                ),
                "expectedKeys": [str(row["blank_key"]) for row in definitions],
            }
            if not existing:
                connection.execute(
                    """INSERT INTO question_blank_config_versions(
                         id,question_id,version_number,frame_set_id,status,source,signals_json,
                         blockers_json,advisories_json,content_hash,created_by,created_at,updated_at
                       ) VALUES(?,?,1,?,'pending','legacy',?,?,?,?,'migration:v8',?,?)""",
                    (
                        config_version_id,
                        config["question_id"],
                        frame_set_id,
                        json_dumps(signals),
                        json_dumps(blockers),
                        json_dumps(
                            [
                                self._layered_issue(
                                    "legacy_unverified",
                                    "旧空位配置已保留，但必须经过新流程复核。",
                                    "blank_config",
                                    str(config["question_id"]),
                                    "review_blank_config",
                                )
                            ]
                        ),
                        f"legacy:{config['question_id']}:blank-config:v1",
                        timestamp,
                        timestamp,
                    ),
                )
            connection.execute(
                "UPDATE question_grading_configs "
                "SET current_blank_config_version_id=? WHERE question_id=?",
                (config_version_id, config["question_id"]),
            )
            for definition_row in definitions:
                definition = dict(definition_row)
                definition_id = self._migration_id(
                    "blank-definition", config_version_id, definition["id"]
                )
                anchor = normalized_anchors[str(definition["id"])]
                connection.execute(
                    """INSERT OR IGNORE INTO question_blank_definition_versions(
                         id,blank_config_version_id,legacy_definition_id,blank_key,sort_order,
                         max_score,answer_kind,standard_answers_json,synonyms_json,
                         template_page_id,page_number,x,y,width,height,anchor_source,
                         anchor_issues_json,anchor_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'legacy',?,?,?,?)""",
                    (
                        definition_id,
                        config_version_id,
                        definition["id"],
                        definition["blank_key"],
                        definition["sort_order"],
                        definition["max_score"],
                        definition["answer_kind"],
                        definition["standard_answers_json"],
                        definition["synonyms_json"],
                        anchor["template_page_id"],
                        anchor["page_number"],
                        anchor["x"],
                        anchor["y"],
                        anchor["width"],
                        anchor["height"],
                        json_dumps(
                            ["legacy_anchor_unverified"]
                            if anchor["template_page_id"]
                            else ["missing_blank_anchor"]
                        ),
                        json_dumps(anchor["raw"]) if anchor["raw"] is not None else None,
                        definition.get("created_at") or timestamp,
                        definition.get("updated_at") or timestamp,
                    ),
                )

    def _migrate_legacy_student_processing(self, connection: sqlite3.Connection) -> None:
        timestamp = now_iso()
        status_map = {
            "uploaded": "mapping_needs_review",
            "aligning": "aligning",
            "recognizing": "recognizing",
            "ready": "recognition_needs_review",
            "failed": "failed",
        }
        submissions = connection.execute(
            """SELECT s.*,t.current_question_frame_set_id
               FROM student_submissions s JOIN tasks t ON t.id=s.task_id"""
        ).fetchall()
        for submission_row in submissions:
            submission = dict(submission_row)
            existing = connection.execute(
                "SELECT id FROM student_processing_revisions "
                "WHERE submission_id=? AND revision_number=1",
                (submission["id"],),
            ).fetchone()
            revision_id = (
                str(existing["id"])
                if existing
                else self._migration_id("processing-revision", submission["id"], 1)
            )
            frame_set_id = submission.get("current_question_frame_set_id")
            if not existing:
                issues = [
                    self._layered_issue(
                        "legacy_processing_unverified",
                        "旧学生处理结果已保留，但其题框和逐空配置尚未按新流程确认。",
                        "recognition",
                        next_action="reprocess_submission",
                    )
                ]
                connection.execute(
                    """INSERT INTO student_processing_revisions(
                         id,submission_id,revision_number,frame_set_id,status,input_hash,
                         is_current,source,issues_json,started_at,finished_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,1,'legacy',?,?,?,?,?)""",
                    (
                        revision_id,
                        submission["id"],
                        1,
                        frame_set_id,
                        status_map.get(str(submission.get("status")), "mapping_needs_review"),
                        str(submission.get("sha256") or f"legacy:{submission['id']}:v1"),
                        json_dumps(issues),
                        submission.get("created_at") or timestamp,
                        submission.get("updated_at")
                        if submission.get("status") in {"ready", "failed"}
                        else None,
                        submission.get("created_at") or timestamp,
                        submission.get("updated_at") or timestamp,
                    ),
                )
            connection.execute(
                "UPDATE student_submissions SET current_processing_revision_id=? WHERE id=?",
                (revision_id, submission["id"]),
            )

            pages = connection.execute(
                "SELECT * FROM student_pages WHERE submission_id=? ORDER BY page_number",
                (submission["id"],),
            ).fetchall()
            for page_row in pages:
                page = dict(page_row)
                alignment_id = self._migration_id(
                    "alignment-revision", revision_id, page["id"], 1
                )
                alignment_issues = (
                    []
                    if page.get("alignment_status") == "aligned"
                    else ["legacy_alignment_unverified"]
                )
                connection.execute(
                    """INSERT OR IGNORE INTO student_page_alignment_revisions(
                         id,processing_revision_id,student_page_id,revision_number,
                         template_page_id,transform_json,quality,method,status,
                         control_points_json,metrics_json,source,is_current,issues_json,
                         created_by,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,'[]','{}','legacy',1,?,
                         'migration:v8',?,?)""",
                    (
                        alignment_id,
                        revision_id,
                        page["id"],
                        1,
                        page.get("template_page_id"),
                        page.get("alignment_transform_json"),
                        page.get("alignment_quality"),
                        page.get("alignment_method"),
                        page.get("alignment_status") or "pending",
                        json_dumps(alignment_issues),
                        page.get("created_at") or timestamp,
                        page.get("updated_at") or timestamp,
                    ),
                )

    @staticmethod
    def _v8_copy_expression(
        columns: set[str],
        column: str,
        fallback: str,
    ) -> str:
        return f"COALESCE(old.{column},{fallback})" if column in columns else fallback

    def _rebuild_student_question_regions_v8(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = self._table_columns(connection, "student_question_regions")
        if not columns:
            return
        old_count = int(
            connection.execute("SELECT COUNT(*) FROM student_question_regions").fetchone()[0]
        )
        connection.execute("DROP TABLE IF EXISTS student_question_regions_v8")
        connection.execute(STUDENT_QUESTION_REGIONS_V8_SCHEMA)
        processing_fallback = (
            "(SELECT s.current_processing_revision_id FROM student_submissions s "
            "WHERE s.id=old.submission_id)"
        )
        processing_expression = self._v8_copy_expression(
            columns, "processing_revision_id", processing_fallback
        )
        frame_fallback = (
            "(SELECT spr.frame_set_id FROM student_processing_revisions spr "
            f"WHERE spr.id={processing_expression})"
        )
        frame_expression = self._v8_copy_expression(columns, "frame_set_id", frame_fallback)
        frame_region_fallback = (
            "(SELECT qfr.id FROM question_frame_regions qfr "
            "JOIN question_frame_items qfi ON qfi.id=qfr.frame_item_id "
            f"WHERE qfi.frame_set_id={frame_expression} "
            "AND qfi.question_id=old.question_id "
            "AND qfr.sort_order=old.sort_order LIMIT 1)"
        )
        frame_region_expression = self._v8_copy_expression(
            columns, "frame_region_id", frame_region_fallback
        )
        alignment_fallback = (
            "(SELECT ar.id FROM student_page_alignment_revisions ar "
            f"WHERE ar.processing_revision_id={processing_expression} "
            "AND ar.student_page_id=old.student_page_id AND ar.is_current=1 LIMIT 1)"
        )
        alignment_expression = self._v8_copy_expression(
            columns, "alignment_revision_id", alignment_fallback
        )
        connection.execute(
            f"""INSERT INTO student_question_regions_v8(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 frame_region_id,alignment_revision_id,sort_order,template_page_id,
                 student_page_id,template_region_json,student_polygon_json,student_bbox_json,
                 status,issues_json,created_at,updated_at
               )
               SELECT old.id,old.submission_id,old.question_id,{processing_expression},
                 {frame_expression},{frame_region_expression},{alignment_expression},
                 old.sort_order,old.template_page_id,old.student_page_id,
                 old.template_region_json,old.student_polygon_json,old.student_bbox_json,
                 old.status,old.issues_json,old.created_at,old.updated_at
               FROM student_question_regions old"""
        )
        new_count = int(
            connection.execute("SELECT COUNT(*) FROM student_question_regions_v8").fetchone()[0]
        )
        if new_count != old_count:
            raise RuntimeError(
                "schema v8 student_question_regions copy count mismatch: "
                f"{old_count} != {new_count}"
            )
        connection.execute("DROP TABLE student_question_regions")
        connection.execute(
            "ALTER TABLE student_question_regions_v8 RENAME TO student_question_regions"
        )
        connection.execute(
            """CREATE INDEX idx_student_question_regions_submission
               ON student_question_regions(
                 submission_id,processing_revision_id,student_page_id,sort_order
               )"""
        )
        connection.execute(
            "CREATE INDEX idx_student_question_regions_question "
            "ON student_question_regions(question_id,processing_revision_id)"
        )
        connection.execute(
            "CREATE INDEX idx_student_question_regions_frame "
            "ON student_question_regions(frame_set_id,frame_region_id)"
        )
        connection.execute(
            """CREATE UNIQUE INDEX idx_student_question_regions_legacy_identity
               ON student_question_regions(submission_id,question_id,sort_order)
               WHERE processing_revision_id IS NULL"""
        )

    def _rebuild_student_responses_v8(self, connection: sqlite3.Connection) -> None:
        columns = self._table_columns(connection, "student_responses")
        if not columns:
            return
        old_count = int(connection.execute("SELECT COUNT(*) FROM student_responses").fetchone()[0])
        connection.execute("DROP TABLE IF EXISTS student_responses_v8")
        connection.execute(STUDENT_RESPONSES_V8_SCHEMA)
        processing_fallback = (
            "(SELECT s.current_processing_revision_id FROM student_submissions s "
            "WHERE s.id=old.submission_id)"
        )
        processing_expression = self._v8_copy_expression(
            columns, "processing_revision_id", processing_fallback
        )
        frame_fallback = (
            "(SELECT spr.frame_set_id FROM student_processing_revisions spr "
            f"WHERE spr.id={processing_expression})"
        )
        frame_expression = self._v8_copy_expression(columns, "frame_set_id", frame_fallback)
        blank_config_fallback = (
            "(SELECT c.current_blank_config_version_id FROM question_grading_configs c "
            "WHERE c.question_id=old.question_id)"
        )
        blank_config_expression = self._v8_copy_expression(
            columns, "blank_config_version_id", blank_config_fallback
        )
        connection.execute(
            f"""INSERT INTO student_responses_v8(
                 id,submission_id,question_id,processing_revision_id,frame_set_id,
                 blank_config_version_id,question_number,recognized_text,confidence,
                 recognition_model_id,raw_recognition_json,status,created_at,updated_at
               )
               SELECT old.id,old.submission_id,old.question_id,{processing_expression},
                 {frame_expression},{blank_config_expression},old.question_number,
                 old.recognized_text,old.confidence,old.recognition_model_id,
                 old.raw_recognition_json,old.status,old.created_at,old.updated_at
               FROM student_responses old"""
        )
        new_count = int(
            connection.execute("SELECT COUNT(*) FROM student_responses_v8").fetchone()[0]
        )
        if new_count != old_count:
            raise RuntimeError(
                f"schema v8 student_responses copy count mismatch: {old_count} != {new_count}"
            )
        connection.execute("DROP TABLE student_responses")
        connection.execute("ALTER TABLE student_responses_v8 RENAME TO student_responses")
        connection.execute(
            "CREATE INDEX idx_student_responses_submission "
            "ON student_responses(submission_id,processing_revision_id,question_number)"
        )
        connection.execute(
            "CREATE INDEX idx_student_responses_question "
            "ON student_responses(question_id,processing_revision_id)"
        )
        connection.execute(
            """CREATE UNIQUE INDEX idx_student_responses_legacy_identity
               ON student_responses(submission_id,question_id)
               WHERE processing_revision_id IS NULL"""
        )

    def _migrate_question_regions(self, connection: sqlite3.Connection) -> None:
        question_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(questions)").fetchall()
        }
        if "question_regions_json" not in question_columns:
            connection.execute(
                "ALTER TABLE questions ADD COLUMN question_regions_json TEXT NOT NULL DEFAULT '[]'"
            )

        submission_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(student_submissions)").fetchall()
        }
        if "question_region_status" not in submission_columns:
            connection.execute(
                "ALTER TABLE student_submissions ADD COLUMN question_region_status "
                "TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(question_region_status IN "
                "('pending','processing','ready','needs_review','failed'))"
            )
        if "question_region_error_code" not in submission_columns:
            connection.execute(
                "ALTER TABLE student_submissions ADD COLUMN question_region_error_code TEXT"
            )
        if "question_region_error_message" not in submission_columns:
            connection.execute(
                "ALTER TABLE student_submissions ADD COLUMN question_region_error_message TEXT"
            )
        connection.executescript(QUESTION_REGION_SCHEMA)
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(5, ?)",
            (now_iso(),),
        )

    def _migrate_student_response_identity(self, connection: sqlite3.Connection) -> None:
        unique_columns: list[tuple[str, ...]] = []
        for index in connection.execute("PRAGMA index_list(student_responses)").fetchall():
            if not index["unique"]:
                continue
            name = str(index["name"]).replace("'", "''")
            columns = tuple(
                str(row["name"])
                for row in connection.execute(f"PRAGMA index_info('{name}')").fetchall()
            )
            unique_columns.append(columns)
        correct = ("submission_id", "question_id") in unique_columns
        obsolete = ("submission_id", "question_number") in unique_columns
        if correct and not obsolete:
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(4, ?)",
                (now_iso(),),
            )
            return

        connection.commit()
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS student_responses_v4")
            connection.execute(STUDENT_RESPONSE_IDENTITY_SCHEMA)
            connection.execute(
                """INSERT INTO student_responses_v4(
                     id,submission_id,question_id,question_number,recognized_text,confidence,
                     recognition_model_id,raw_recognition_json,status,created_at,updated_at
                   )
                   SELECT id,submission_id,question_id,question_number,recognized_text,confidence,
                     recognition_model_id,raw_recognition_json,status,created_at,updated_at
                   FROM student_responses"""
            )
            connection.execute("DROP TABLE student_responses")
            connection.execute("ALTER TABLE student_responses_v4 RENAME TO student_responses")
            connection.execute(
                """CREATE INDEX idx_student_responses_submission
                   ON student_responses(submission_id, question_number)"""
            )
            connection.execute(
                """CREATE INDEX idx_student_responses_question
                   ON student_responses(question_id)"""
            )
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(4, ?)",
                (now_iso(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock, self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(sql, params)
            connection.commit()

    def interrupt_running(self) -> int:
        timestamp = now_iso()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT id, task_id FROM runs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE runs SET status='interrupted', error_code='RUN_INTERRUPTED',
                       error_message='服务在处理期间中断，请重新运行', finished_at=?
                       WHERE id=?""",
                    (timestamp, row["id"]),
                )
                connection.execute(
                    """UPDATE tasks SET status='failed', last_error_code='RUN_INTERRUPTED',
                       last_error_message='服务在处理期间中断，请重新运行', updated_at=?
                       WHERE id=?""",
                    (timestamp, row["task_id"]),
                )
        return len(rows)

    def interrupt_student_processing(self) -> int:
        timestamp = now_iso()
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT id FROM student_submissions
                   WHERE status IN ('aligning','recognizing')"""
            ).fetchall()
            connection.execute(
                """UPDATE student_submissions SET status='failed',
                   error_code='STUDENT_RUN_INTERRUPTED',
                   error_message='学生答卷处理已中断，请重新运行',updated_at=?
                   WHERE status IN ('aligning','recognizing')""",
                (timestamp,),
            )
        return len(rows)

    def interrupt_grading(self) -> int:
        """Make in-flight grading runs explicitly resumable after a service restart."""
        timestamp = now_iso()
        active_statuses = (
            "queued",
            "prechecking",
            "aligning",
            "segmenting",
            "recognizing",
            "grading",
            "auditing",
            "generating_annotation",
            "generating_report",
        )
        placeholders = ",".join("?" for _item in active_statuses)
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT id FROM grading_runs WHERE status IN ({placeholders})",
                active_statuses,
            ).fetchall()
            connection.execute(
                f"""UPDATE grading_runs SET status='failed',stage='failed',
                     error_code='GRADING_RUN_INTERRUPTED',
                     error_message='批改任务因服务中断，可从已保存阶段重试',retryable=1,
                     updated_at=? WHERE status IN ({placeholders})""",
                (timestamp, *active_statuses),
            )
        return len(rows)

    def audit(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(task_id,event_type,actor,payload_json,created_at)
               VALUES(?,?,?,?,?)""",
            (task_id, event_type, actor, json_dumps(payload or {}), now_iso()),
        )
