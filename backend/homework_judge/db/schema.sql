PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stored_files (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('template', 'reference_answer', 'submission')),
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL UNIQUE,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK (size >= 0),
  relative_path TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grading_tasks (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  class_name TEXT NOT NULL,
  paper_name TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT 'middle_school_math'
    CHECK (subject IN ('middle_school_math', 'high_school_physics')),
  answer_mode TEXT NOT NULL DEFAULT 'agent_search'
    CHECK (answer_mode IN ('reference_upload', 'agent_search')),
  template_file_id TEXT REFERENCES stored_files(id),
  reference_answer_file_id TEXT REFERENCES stored_files(id),
  answer_config_status TEXT NOT NULL DEFAULT 'not_started'
    CHECK (answer_config_status IN (
      'not_started', 'queued', 'extracting', 'searching', 'generating',
      'review_pending', 'approved', 'failed'
    )),
  active_answer_version_id TEXT REFERENCES answer_config_versions(id),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'ready', 'grading', 'reviewing', 'completed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answer_config_versions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL CHECK (version_number > 0),
  status TEXT NOT NULL
    CHECK (status IN ('draft', 'review_pending', 'approved', 'superseded')),
  answer_mode TEXT NOT NULL
    CHECK (answer_mode IN ('reference_upload', 'agent_search')),
  extraction_issues_json TEXT NOT NULL DEFAULT '[]',
  unresolved_issue_count INTEGER NOT NULL DEFAULT 0
    CHECK (unresolved_issue_count >= 0),
  created_at TEXT NOT NULL,
  approved_by TEXT,
  approved_at TEXT,
  UNIQUE(task_id, version_number)
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
  answer_version_id TEXT REFERENCES answer_config_versions(id) ON DELETE RESTRICT,
  source_draft_id TEXT,
  number TEXT NOT NULL,
  question_text TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL
    CHECK (type IN ('choice', 'fill_blank', 'short_answer', 'calculation')),
  max_score REAL NOT NULL CHECK (max_score > 0),
  standard_answer TEXT NOT NULL,
  scoring_points_json TEXT NOT NULL DEFAULT '[]',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(answer_version_id, number)
);

CREATE TABLE IF NOT EXISTS answer_question_drafts (
  id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES answer_config_versions(id) ON DELETE CASCADE,
  number TEXT NOT NULL,
  question_text TEXT NOT NULL,
  type TEXT NOT NULL
    CHECK (type IN ('choice', 'fill_blank', 'short_answer', 'calculation')),
  max_score REAL NOT NULL CHECK (max_score > 0),
  auto_answer TEXT NOT NULL DEFAULT '',
  auto_scoring_points_json TEXT NOT NULL DEFAULT '[]',
  auto_reason TEXT NOT NULL DEFAULT '',
  source_type TEXT
    CHECK (source_type IN ('reference_extracted', 'web_searched', 'model_generated')),
  confidence REAL NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  needs_attention INTEGER NOT NULL DEFAULT 0,
  parse_issues_json TEXT NOT NULL DEFAULT '[]',
  normalization_json TEXT NOT NULL DEFAULT '[]',
  requires_correction INTEGER NOT NULL DEFAULT 0,
  teacher_number TEXT,
  teacher_type TEXT
    CHECK (teacher_type IS NULL OR teacher_type IN (
      'choice', 'fill_blank', 'short_answer', 'calculation'
    )),
  teacher_max_score REAL,
  teacher_answer TEXT,
  teacher_scoring_points_json TEXT,
  rejection_reason TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending', 'approved', 'rejected', 'failed')),
  updated_by TEXT,
  latest_run_id TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(version_id, number)
);

CREATE TABLE IF NOT EXISTS answer_resolution_runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL REFERENCES answer_config_versions(id) ON DELETE CASCADE,
  draft_question_id TEXT REFERENCES answer_question_drafts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL
    CHECK (kind IN (
      'exam_extraction', 'reference_extraction', 'structure_repair',
      'web_search', 'model_generation'
    )),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  request_snapshot_json TEXT,
  raw_response_json TEXT,
  parsed_output_json TEXT,
  usage_json TEXT,
  status TEXT NOT NULL
    CHECK (status IN ('running', 'succeeded', 'parse_failed', 'request_failed')),
  error_code TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS search_sources (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES answer_resolution_runs(id) ON DELETE CASCADE,
  draft_question_id TEXT NOT NULL REFERENCES answer_question_drafts(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  snippet TEXT NOT NULL DEFAULT '',
  rank INTEGER NOT NULL DEFAULT 0,
  retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES grading_tasks(id) ON DELETE CASCADE,
  answer_version_id TEXT REFERENCES answer_config_versions(id) ON DELETE RESTRICT,
  file_id TEXT NOT NULL REFERENCES stored_files(id),
  student_name TEXT NOT NULL,
  student_name_needs_review INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'review_pending', 'confirmed', 'failed')),
  error_code TEXT,
  error_message TEXT,
  model_total_score REAL,
  final_total_score REAL,
  confirmed_by TEXT,
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runs (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  request_snapshot_json TEXT,
  raw_response_json TEXT,
  parsed_output_json TEXT,
  usage_json TEXT,
  status TEXT NOT NULL
    CHECK (status IN ('running', 'succeeded', 'parse_failed', 'request_failed')),
  error_message TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS question_reviews (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  model_run_id TEXT REFERENCES model_runs(id),
  model_answer TEXT NOT NULL DEFAULT '',
  model_score REAL NOT NULL DEFAULT 0,
  model_reason TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  final_answer TEXT NOT NULL DEFAULT '',
  final_score REAL NOT NULL DEFAULT 0,
  teacher_comment TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending', 'needs_attention', 'reviewed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(submission_id, question_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES grading_tasks(id) ON DELETE CASCADE,
  submission_id TEXT REFERENCES submissions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_questions_task_version
  ON questions(task_id, answer_version_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_answer_versions_task
  ON answer_config_versions(task_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_answer_drafts_version
  ON answer_question_drafts(version_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_answer_runs_version
  ON answer_resolution_runs(version_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_answer_runs_draft
  ON answer_resolution_runs(draft_question_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_sources_draft
  ON search_sources(draft_question_id, rank);
CREATE INDEX IF NOT EXISTS idx_files_task
  ON stored_files(task_id, kind);
CREATE INDEX IF NOT EXISTS idx_submissions_task_status
  ON submissions(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_model_runs_submission
  ON model_runs(submission_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_submission
  ON question_reviews(submission_id);
CREATE INDEX IF NOT EXISTS idx_audit_submission
  ON audit_events(submission_id, created_at DESC);

PRAGMA user_version = 3;
