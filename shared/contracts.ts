export type TaskStatus =
  | "draft"
  | "queued"
  | "preparing"
  | "exam_recognizing"
  | "answer_recognizing"
  | "matching"
  | "review_pending"
  | "completed"
  | "failed";

export interface TaskSummary {
  id: string;
  title: string;
  status: TaskStatus;
  active_run_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at: string;
  updated_at: string;
  questionCount: number;
  confirmedCount: number;
}

export interface Progress {
  taskId: string;
  status: TaskStatus;
  errorCode: string | null;
  errorMessage: string | null;
  run: {
    id: string;
    stage: string;
    status: string;
    progress_current: number;
    progress_total: number;
  } | null;
}

export interface OptionValue {
  label: string;
  text: string;
}

export interface QuestionValue {
  number: string;
  stem: string;
  options: OptionValue[];
  type: string;
  score: number | null;
}

export type QuestionFrameSetStatus = "draft" | "confirmed" | "superseded";
export type QuestionFrameItemStatus = "pending" | "confirmed";
export type QuestionFrameSource = "model" | "teacher" | "legacy";
export type IssueLayer =
  | "question_frame"
  | "blank_config"
  | "alignment"
  | "recognition"
  | "grading";

export interface NormalizedBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface QuestionFrameFragment extends NormalizedBox {
  regionKey: string;
  templatePageId: string;
  pageNumber: number;
  sortOrder: number;
  source: QuestionFrameSource;
  confidence: number | null;
  issues: string[];
}

export interface QuestionFrameItem {
  questionId: string;
  status: QuestionFrameItemStatus;
  revision: number;
  fragments: QuestionFrameFragment[];
  issues: string[];
  carriedFromItemId: string | null;
  confirmedAt: string | null;
  confirmedBy: string | null;
}

export interface QuestionFrameSet {
  id: string;
  taskId: string;
  versionNumber: number;
  status: QuestionFrameSetStatus;
  revision: number;
  baseFrameSetId: string | null;
  source: QuestionFrameSource;
  contentHash: string;
  items: QuestionFrameItem[];
  createdAt: string;
  createdBy: string;
  updatedAt: string;
  confirmedAt: string | null;
  confirmedBy: string | null;
}

export interface SingleQuestionRerecognitionResult {
  questionId: string;
  runId: string;
  frameSet: QuestionFrameSet;
  recognizedQuestion: QuestionValue & {
    sourcePages: number[];
    confidence: number;
    issues: string[];
  };
  teacherOverridePreserved: boolean;
}

export interface LayeredIssue {
  code: string;
  message: string;
  layer: IssueLayer;
  questionId?: string | null;
  regionKey?: string | null;
  questionNumber?: string | null;
  relatedQuestionId?: string | null;
  relatedQuestionNumber?: string | null;
  relatedRegionKey?: string | null;
  details?: Record<string, unknown>;
  nextAction?: string | null;
}

export interface StudentProcessingGate {
  ready: boolean;
  frameSetId: string | null;
  frameSetVersion: number | null;
  missingQuestionIds: string[];
  unconfirmedQuestionIds: string[];
  issues: LayeredIssue[];
  blankConfigIssues?: Array<LayeredIssue & {
    questionNumber?: string;
    blankConfigVersionId?: string | null;
    status?: BlankConfigVersionStatus;
    source?: "model" | "teacher" | "legacy" | null;
  }>;
  legacyRecovery?: {
    required: boolean;
    frameSetSource: QuestionFrameSource | null;
    hasLegacyBlankConfig: boolean;
    legacyProcessingCount: number;
    readyForReprocess: boolean;
  };
}

export interface BlankAnchor {
  templatePageId: string;
  pageNumber: number;
  coordinateSpace: "template_page_normalized";
  box: NormalizedBox;
  source: "model" | "teacher" | "legacy";
  confidence: number | null;
  issues: string[];
}

export type BlankConfigVersionStatus =
  | "pending"
  | "auto_confirmed"
  | "teacher_confirmed"
  | "stale";

export interface BlankConfigReadiness {
  status: BlankConfigVersionStatus;
  frameSetId: string;
  stemBlankCount: number;
  anchorCount: number;
  standardAnswerCount: number;
  expectedKeys: string[];
  blockingIssues: LayeredIssue[];
  advisoryIssues: LayeredIssue[];
}

export type ProcessingRevisionStatus =
  | "aligning"
  | "mapping_needs_review"
  | "recognizing"
  | "recognition_needs_review"
  | "ready"
  | "failed";

export interface StudentProcessingRevision {
  id: string;
  submissionId: string;
  revisionNumber: number;
  frameSetId: string | null;
  status: ProcessingRevisionStatus;
  inputHash: string;
  isCurrent: boolean;
  source?: "system" | "teacher" | "legacy";
  issues: LayeredIssue[];
  startedAt?: string | null;
  createdAt: string;
  finishedAt: string | null;
  updatedAt?: string;
  isHistorical?: boolean;
  responseCount?: number;
  questionRegionCount?: number;
  gradingResultCount?: number;
  artifactCount?: number;
}

export interface StudentBlankResponse {
  id: string;
  studentResponseId: string;
  blankDefinitionId: string | null;
  blankKey: string;
  recognizedText: string;
  isBlank: boolean;
  confidence: number | null;
  status: "recognized" | "needs_review";
  issues: string[];
  evidenceRefs: string[];
  frameSetId: string | null;
  blankConfigVersionId: string | null;
  processingRevisionId: string | null;
  recognitionModelId?: string | null;
  promptVersion?: string | null;
}

export interface AlignmentControlPointPair {
  template: {x: number; y: number};
  student: {x: number; y: number};
}

export interface ReviewQuestion {
  id: string;
  sortOrder: number;
  original: QuestionValue;
  effective: QuestionValue;
  sourcePages: number[];
  answerRegions: Array<{
    pageNumber: number;
    x: number;
    y: number;
    width: number;
    height: number;
  }>;
  confidence: number;
  issues: string[];
  isDuplicate: boolean;
  confirmationStatus: "pending" | "confirmed";
  questionFrame?: QuestionFrameItem;
  blankConfigReadiness?: BlankConfigReadiness | null;
  match: {
    id: string;
    answerEntryId: string | null;
    method: string;
    numberScore: number;
    stemScore: number;
    orderScore: number;
    totalScore: number;
    reasons: string[];
    status: string;
    answer: string;
    explanation: string;
    answerSourcePages: number[];
  };
}

export interface AnswerEntry {
  id: string;
  numberHint: string;
  stemHint: string;
  answer: string;
  explanation: string;
  sourcePages: number[];
  confidence: number;
  issues: string[];
  ignored: boolean;
  questionId: string | null;
}

export interface ReviewDetail {
  task: TaskSummary;
  questions: ReviewQuestion[];
  answerEntries: AnswerEntry[];
  documents: Array<{
    id: string;
    role: "exam" | "answer";
    original_name: string;
    page_count: number;
  }>;
  pages: Array<{
    id: string;
    document_id: string;
    page_number: number;
    width: number;
    height: number;
    role: "exam" | "answer";
    imageUrl: string;
  }>;
  questionFrameSet?: QuestionFrameSet | null;
  studentUploadGate?: StudentProcessingGate;
}

export interface GradingBlankDefinition {
  id?: string;
  blankKey: string;
  sortOrder: number;
  maxScore: string;
  answerKind: "text" | "numeric" | "formula";
  standardAnswers: string[];
  synonyms: string[];
  region?: {x: number; y: number; width: number; height: number} | null;
  anchor?: BlankAnchor | null;
}

export interface GradingConfigInitialization {
  source: "saved" | "derived" | "none";
  signals: {
    stemMarkerCount: number;
    independentRegionCount: number;
    structuredAnswerCount: number | null;
    selectedCount: number;
  } | null;
  warnings: Array<{code: string; message: string}>;
  autoConfirmable: boolean;
  blockingReasons: Array<{code: string; message: string}>;
}

export interface FillBlankConfigBlocker {
  questionId: string;
  questionNumber: string;
  expectedBlankCount: number;
  reasonCodes: string[];
  message: string;
}

export interface FillBlankConfigErrorDetails {
  questions: FillBlankConfigBlocker[];
}

export interface GradingConfig {
  questionId: string;
  questionType: string;
  maxScore: string | number | null;
  configVersion: number;
  confirmationStatus: "pending" | "confirmed";
  versionId?: string | null;
  status?: BlankConfigVersionStatus | null;
  blankConfigVersionId?: string | null;
  frameSetId?: string | null;
  versionStatus?: BlankConfigVersionStatus;
  readiness?: BlankConfigReadiness | null;
  blanks: GradingBlankDefinition[];
  initialization: GradingConfigInitialization;
}

export interface AnswerGradingDraftPoint {
  pointKey: string;
  criterion: string;
  score: string;
  sortOrder: number;
  dependencies: string[];
}

export interface AnswerGradingDraftValue {
  questionType: "single_choice" | "multiple_choice" | "fill_blank" | "calculation";
  standardAnswer: string;
  explanation: string;
  maxScore: string;
  answerOptions: string[];
  blanks: GradingBlankDefinition[];
  rubricPoints: AnswerGradingDraftPoint[];
  warnings: string[];
}

export interface AnswerGradingDraftPreview {
  runId: string;
  questionId: string;
  current: AnswerGradingDraftValue;
  draft: AnswerGradingDraftValue;
  warnings: string[];
  createdAt: string;
}

export interface ApplyAnswerGradingDraftResult {
  runId: string;
  questionId: string;
  applied: boolean;
  studentResultsInvalidated: boolean;
  message: string;
}

export interface StudentSubmissionSummary {
  id: string;
  task_id: string;
  student_identifier: string;
  student_name: string;
  original_name: string;
  page_count: number;
  status: "uploaded" | "aligning" | "recognizing" | "ready" | "failed";
  error_code: string | null;
  error_message: string | null;
  question_region_status: "pending" | "processing" | "ready" | "needs_review" | "failed";
  question_region_error_code: string | null;
  question_region_error_message: string | null;
  response_count?: number;
  review_count?: number;
  auto_grading_status?: "pending" | "running" | "blocked" | "needs_review" | "completed" | "failed" | null;
  auto_grading_run_id?: string | null;
  auto_grading_error_code?: string | null;
  auto_grading_error_message?: string | null;
  auto_grading_progress_current?: number | null;
  auto_grading_progress_total?: number | null;
  auto_grading_total_score?: string | null;
  auto_grading_open_review_count?: number | null;
  created_at: string;
  updated_at: string;
}

export interface StudentSubmissionDetail {
  submission: StudentSubmissionSummary;
  pages: Array<{
    id: string;
    pageNumber: number;
    width: number;
    height: number;
    templatePageId: string | null;
    templatePageNumber: number | null;
    imageUrl: string;
    alignment: {
      direction: "student_original_to_template";
      transform: number[][] | null;
      quality: number | null;
      method: string | null;
      status: "pending" | "aligned" | "low_quality" | "failed";
      revisionNumber?: number | null;
      revisionId?: string | null;
      source?: "model" | "teacher" | "legacy" | null;
      controlPoints?: AlignmentControlPointPair[];
    };
  }>;
  responses: Array<{
    id: string;
    questionId: string | null;
    questionNumber: string;
    questionType: string | null;
    recognizedText: string;
    confidence: number | null;
    isBlank: boolean;
    issues: string[];
    status: "pending" | "recognized" | "needs_review" | "failed";
    processingRevisionId?: string | null;
    frameSetId?: string | null;
    blankConfigVersionId?: string | null;
    blankResponses?: StudentBlankResponse[];
    regions: Array<{
      id: string;
      sortOrder: number;
      templatePageId: string | null;
      studentPageId: string;
      coordinateSpace: "pixel" | "normalized";
      templateBox: {x: number; y: number; width: number; height: number};
      studentBox: {x: number; y: number; width: number; height: number};
    }>;
  }>;
  questionRegionState: {
    status: "pending" | "processing" | "ready" | "needs_review" | "failed";
    errorCode: string | null;
    errorMessage: string | null;
    missingQuestionIds: string[];
  };
  questionRegions: Array<{
    id: string;
    questionId: string;
    questionNumber: string;
    sortOrder: number;
    processingRevisionId?: string | null;
    frameSetId?: string | null;
    frameRegionId?: string | null;
    alignmentRevisionId?: string | null;
    templatePageId: string;
    studentPageId: string;
    coordinateSpace: "student_original_page_pixels";
    templateRegion: {page_number: number; x: number; y: number; width: number; height: number};
    studentPolygon: Array<{x: number; y: number}>;
    studentBox: {x: number; y: number; width: number; height: number};
    status: "ready" | "needs_review";
    issues: string[];
  }>;
  processingRevision?: StudentProcessingRevision | null;
  viewedProcessingRevisionId?: string | null;
  isHistoricalView?: boolean;
  processingHistory?: StudentProcessingRevision[];
  currentProcessingRevisionId?: string | null;
  processingRevisions?: StudentProcessingRevision[];
  blankResponses?: StudentBlankResponse[];
}

export type GradingRunStatus =
  | "queued"
  | "prechecking"
  | "grading"
  | "auditing"
  | "needs_review"
  | "generating_annotation"
  | "generating_report"
  | "completed"
  | "failed";

export interface GradingRun {
  id: string;
  submissionId: string;
  taskId: string;
  processingRevisionId?: string | null;
  triggerSource?: "manual" | "automatic" | "retry";
  status: GradingRunStatus;
  stage: string;
  inputHash: string;
  resultRevision: number;
  totalScore: string | null;
  maxScore: string | null;
  progress: {current: number; total: number};
  openReviewCount: number;
  lastSuccessfulStage: string | null;
  attemptCount: number;
  retryable: boolean;
  isStale?: boolean;
  error: {code: string; message: string} | null;
  createdAt: string;
  updatedAt: string;
}

export interface GradingEvidence {
  page_id: string;
  region_id: string;
  original_bbox: {x: number; y: number; width: number; height: number};
  cropped_image_path: string | null;
  recognized_text: string;
  char_or_step_range: [number, number] | null;
  pageNumber?: number | null;
  previewUrl?: string;
}

export interface GradingDecision {
  key: string;
  status: "correct" | "partial" | "incorrect" | "unable" | "satisfied" | "failed" | "blocked_by_dependency";
  score: string;
  max_score: string;
  reason: string;
  evidence_refs: GradingEvidence[];
  blocked_by: string | null;
}

export interface GradingQuestionResult {
  id: string;
  gradingRunId: string;
  questionId: string;
  questionNumber: string;
  questionType: "single_choice" | "multiple_choice" | "fill_blank" | "calculation";
  status: "final" | "needs_review" | "failed";
  rawScore: string | null;
  finalScore: string | null;
  maxScore: string;
  reviewReasons: string[];
  errorLocations: GradingEvidence[];
  decisions?: GradingDecision[];
  evidence?: GradingEvidence[];
  toolConclusions?: Array<{tool: string; status: string; detail: string; payload: Record<string, unknown>}>;
  question?: {stem: string; standardAnswer: {answer?: string; options?: string[]}};
  gradingConfig?: Record<string, unknown>;
  resultRevision: number;
  error: {code: string; message: string} | null;
}

export interface GradingReviewItem {
  id: string;
  gradingRunId: string;
  questionResultId: string;
  questionId: string;
  questionNumber: string;
  reason: string;
  status: "open" | "resolved";
  score: string | null;
  maxScore: string;
  createdAt: string;
  updatedAt: string;
}

export interface GradingReviewResolution {
  action: "confirm" | "override";
  teacherReason: string;
  recognizedText?: string;
  blankDecisions?: Array<{blankKey: string; status: "correct" | "incorrect"}>;
  pointDecisions?: Array<{pointKey: string; directStatus: "satisfied" | "partial" | "failed"}>;
}

export interface GradingReviewResolutionResult {
  reviewItemId: string;
  gradingRunId: string;
  questionResultId: string;
  status: "final" | "needs_review";
  score: string;
  remainingReasons: string[];
  overriddenReasons?: string[];
}

export interface AnnotationPreviewMark {
  mark_type: "check" | "error_circle" | "partial_score";
  page_id: string;
  question_result_id: string;
  question_id: string;
  box: {x: number; y: number; width: number; height: number};
  target_box: {x: number; y: number; width: number; height: number} | null;
  label: string;
  color: string;
}

export interface GradingArtifact {
  id: string;
  gradingRunId: string;
  type: "annotation" | "error_report";
  resultRevision: number;
  status: "generating" | "current" | "stale" | "failed";
  preview: {
    marks?: AnnotationPreviewMark[];
    pages?: Array<{pageId: string; pageNumber: number; markCount: number}>;
    questions?: Array<{
      questionId: string;
      questionNumber: string;
      questionType: string;
      score: string;
      maxScore: string;
      errorCategory: string;
      errorReason: string;
      knowledgeGap: string;
      masteredParts: string[];
      suggestion: string;
      evidenceRegionId: string | null;
    }>;
    summary?: string;
  };
  contentHash: string | null;
  previewUrl: string;
  downloadUrl: string;
  error: {code: string; message: string} | null;
  createdAt: string;
  updatedAt: string;
}
