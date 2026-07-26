export type Subject = "middle_school_math" | "high_school_physics";
export type QuestionType =
  | "choice"
  | "fill_blank"
  | "short_answer"
  | "calculation";
export type AnswerMode = "reference_upload" | "agent_search";
export type AnswerConfigStatus =
  | "not_started"
  | "queued"
  | "extracting"
  | "searching"
  | "generating"
  | "review_pending"
  | "approved"
  | "failed";
export type AnswerVersionStatus =
  | "draft"
  | "review_pending"
  | "approved"
  | "superseded";
export type AnswerSourceType =
  | "reference_extracted"
  | "web_searched"
  | "model_generated";
export type AnswerDraftReviewStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "failed";
export type TaskStatus = "draft" | "ready" | "grading" | "reviewing" | "completed";
export type SubmissionStatus =
  | "queued"
  | "processing"
  | "review_pending"
  | "confirmed"
  | "failed";
export type ReviewStatus = "pending" | "needs_attention" | "reviewed";
export type ModelRunStatus =
  | "running"
  | "succeeded"
  | "parse_failed"
  | "request_failed";

export interface ScoringPoint {
  description: string;
  score: number;
}

export interface ParseIssue {
  path: Array<string | number>;
  code: string;
  message: string;
  severity: "attention" | "blocking" | "skipped";
  originalValue: unknown;
  normalizedValue: unknown;
  requiresCorrection: boolean;
}

export interface Question {
  id: string;
  taskId: string;
  answerVersionId: string | null;
  questionText: string;
  number: string;
  type: QuestionType;
  maxScore: number;
  standardAnswer: string;
  scoringPoints: ScoringPoint[];
  sortOrder: number;
}

export interface StoredFile {
  id: string;
  kind: "template" | "reference_answer" | "submission";
  originalName: string;
  mimeType: string;
  size: number;
  previewUrl: string;
  createdAt: string;
}

export interface TaskProgress {
  total: number;
  queued: number;
  processing: number;
  reviewPending: number;
  confirmed: number;
  failed: number;
}

export interface GradingTaskSummary {
  id: string;
  name: string;
  className: string;
  paperName: string;
  subject: Subject;
  answerMode: AnswerMode;
  answerConfigStatus: AnswerConfigStatus;
  activeAnswerVersion: AnswerConfigVersionSummary | null;
  status: TaskStatus;
  questionCount: number;
  totalScore: number;
  progress: TaskProgress;
  createdAt: string;
  updatedAt: string;
}

export interface GradingTaskDetail extends GradingTaskSummary {
  templateFile: StoredFile | null;
  referenceAnswerFile: StoredFile | null;
  questions: Question[];
}

export interface Submission {
  id: string;
  taskId: string;
  answerVersionId: string | null;
  studentName: string;
  studentNameNeedsReview: boolean;
  status: SubmissionStatus;
  originalName: string;
  mimeType: string;
  fileSize: number;
  previewUrl: string;
  errorCode: string | null;
  errorMessage: string | null;
  modelTotalScore: number | null;
  finalTotalScore: number | null;
  confirmedBy: string | null;
  confirmedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ModelRunSummary {
  id: string;
  provider: string;
  model: string;
  status: ModelRunStatus;
  requestSnapshot: unknown;
  rawResponse: unknown;
  parsedOutput: unknown;
  usage: {
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  } | null;
  errorMessage: string | null;
  startedAt: string;
  finishedAt: string | null;
}

export interface QuestionReview {
  questionId: string;
  questionNumber: string;
  questionType: QuestionType;
  maxScore: number;
  standardAnswer: string;
  scoringPoints: ScoringPoint[];
  modelAnswer: string;
  modelScore: number;
  modelReason: string;
  confidence: number;
  finalAnswer: string;
  finalScore: number;
  teacherComment: string;
  reviewStatus: ReviewStatus;
  updatedAt: string;
}

export interface SubmissionReview {
  submission: Submission;
  task: GradingTaskSummary;
  answerVersion: AnswerConfigVersionSummary | null;
  modelRun: ModelRunSummary | null;
  reviews: QuestionReview[];
  navigation: {
    previousId: string | null;
    nextId: string | null;
  };
}

export interface StudentReport {
  submission: Submission;
  task: GradingTaskSummary;
  answerVersion: AnswerConfigVersionSummary | null;
  isFinal: boolean;
  totalScore: number | null;
  maxScore: number;
  reviews: QuestionReview[];
}

export interface ScoreBand {
  label: string;
  count: number;
  minPercent: number;
}

export interface QuestionStatistic {
  questionId: string;
  answerVersionId: string;
  answerVersionNumber: number;
  number: string;
  averageScore: number;
  maxScore: number;
  scoreRate: number;
}

export interface ClassStatistics {
  subject: Subject;
  activeAnswerVersion: AnswerConfigVersionSummary | null;
  answerVersions: Array<{
    id: string;
    versionNumber: number;
    submissionCount: number;
    confirmedCount: number;
  }>;
  progress: TaskProgress;
  confirmedCount: number;
  averageScore: number | null;
  highestScore: number | null;
  lowestScore: number | null;
  totalScore: number;
  scoreBands: ScoreBand[];
  questions: QuestionStatistic[];
  students: Array<{
    submissionId: string;
    studentName: string;
    status: SubmissionStatus;
    score: number | null;
    confirmedAt: string | null;
    answerVersionId: string | null;
    answerVersionNumber: number | null;
  }>;
}

export interface ModelStatus {
  configured: boolean;
  provider: "阿里云百炼";
  model: string;
  regionHint: string;
  baseUrlConfigured: boolean;
}

export interface AnswerConfigVersionSummary {
  id: string;
  taskId: string;
  versionNumber: number;
  status: AnswerVersionStatus;
  answerMode: AnswerMode;
  extractionIssues?: ParseIssue[];
  unresolvedIssueCount?: number;
  createdAt: string;
  approvedBy: string | null;
  approvedAt: string | null;
}

export interface SearchSource {
  id: string;
  runId: string;
  draftQuestionId: string;
  title: string;
  url: string;
  snippet: string;
  rank: number;
  retrievedAt: string;
}

export interface AnswerResolutionRun {
  id: string;
  taskId: string;
  versionId: string;
  draftQuestionId: string | null;
  kind:
    | "exam_extraction"
    | "reference_extraction"
    | "structure_repair"
    | "web_search"
    | "model_generation";
  provider: string;
  model: string;
  requestSnapshot: unknown;
  rawResponse: unknown;
  parsedOutput: unknown;
  usage: {
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  } | null;
  status: ModelRunStatus;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string;
  finishedAt: string | null;
  sources: SearchSource[];
}

export interface AnswerQuestionDraft {
  id: string;
  versionId: string;
  number: string;
  questionText: string;
  type: QuestionType;
  maxScore: number;
  autoAnswer: string;
  autoScoringPoints: ScoringPoint[];
  autoReason: string;
  sourceType: AnswerSourceType | null;
  confidence: number;
  needsAttention: boolean;
  parseIssues?: ParseIssue[];
  normalizations?: ParseIssue[];
  requiresCorrection?: boolean;
  teacherNumber: string | null;
  teacherType: QuestionType | null;
  teacherMaxScore: number | null;
  teacherAnswer: string | null;
  teacherScoringPoints: ScoringPoint[] | null;
  rejectionReason: string | null;
  reviewStatus: AnswerDraftReviewStatus;
  updatedBy: string | null;
  updatedAt: string;
  effectiveNumber: string;
  effectiveType: QuestionType;
  effectiveMaxScore: number;
  effectiveAnswer: string;
  effectiveScoringPoints: ScoringPoint[];
  latestRunId: string | null;
  sources: SearchSource[];
}

export interface AnswerConfigProgress {
  total: number;
  pending: number;
  processing: number;
  webSearched: number;
  modelGenerated: number;
  needsAttention: number;
  approved: number;
  rejected: number;
  failed: number;
}

export interface AnswerConfigDetail {
  task: GradingTaskSummary;
  version: AnswerConfigVersionSummary | null;
  drafts: AnswerQuestionDraft[];
  progress: AnswerConfigProgress;
  runs: AnswerResolutionRun[];
}

export interface HealthStatus {
  status: "ok";
  database: "ok" | "error";
  time: string;
  teacherName: string;
}

export interface AuditEvent {
  id: string;
  eventType: string;
  actor: string;
  payload: unknown;
  createdAt: string;
}

export interface ApiSuccess<T> {
  ok: true;
  data: T;
}

export interface ApiFailure {
  ok: false;
  error: {
    code: string;
    message: string;
    fields?: Record<string, string[]>;
  };
}

export type ApiResponse<T> = ApiSuccess<T> | ApiFailure;
