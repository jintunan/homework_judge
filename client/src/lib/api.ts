import { apiEnvelopeSchema } from "@shared/schemas";
import type {
  AnswerGradingDraftPreview,
  ApplyAnswerGradingDraftResult,
  AlignmentControlPointPair,
  GradingEvidence,
  GradingQuestionResult,
  GradingReviewResolution,
  GradingReviewResolutionResult,
  QuestionFrameFragment,
  QuestionFrameSet,
  SingleQuestionRerecognitionResult
} from "@shared/contracts";

export interface GradingPixelPoint {
  x: number;
  y: number;
}

export interface GradingPixelBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GradingQuestionFrameRegion {
  id: string;
  questionId: string;
  pageId: string;
  polygon: GradingPixelPoint[];
  /** Immutable frame mapping captured by the grading run, never a derived client box. */
  frameSetId?: string | null;
  processingRevisionId?: string | null;
}

export interface GradingBlankAnchorOverlay {
  blankKey: string;
  pageId: string;
  coordinateSpace: "student_page_pixel" | "student_original_page_pixels";
  /** A mapped student-page polygon is required before this layer may be rendered. */
  studentPolygon?: GradingPixelPoint[] | null;
  studentBBox?: GradingPixelBox | null;
}

export interface GradingRecognitionEvidence extends GradingEvidence {
  original_polygon?: GradingPixelPoint[] | null;
  blank_key?: string | null;
}

export interface GradingBlankResult {
  id: string;
  blankKey: string;
  studentAnswer?: string;
  recognizedAnswer?: string;
  standardAnswers: string[];
  answerKind?: "text" | "numeric" | "formula";
  synonyms?: string[];
  status: "correct" | "incorrect" | "needs_review";
  score: string;
  maxScore: string;
  reviewReasons: string[];
  exactMatch?: unknown;
  modelResult?: unknown;
  verifierResult?: unknown;
  decision?: Record<string, unknown>;
  evidence?: GradingRecognitionEvidence[];
  evidenceRefs?: GradingRecognitionEvidence[];
  frameSetId?: string | null;
  blankConfigVersionId?: string | null;
  processingRevisionId?: string | null;
  gradingRevision?: number | null;
}

export interface GradingQuestionDetail extends GradingQuestionResult {
  evidence?: GradingRecognitionEvidence[];
  /**
   * Question-frame mappings captured together with this grading result.
   * A missing field identifies a legacy result; it must not be treated as a
   * statement that the submission's current frame is the historical frame.
   */
  questionFrames?: GradingQuestionFrameRegion[];
  blankResults?: GradingBlankResult[];
  blankAnchors?: GradingBlankAnchorOverlay[];
  gradingRevision?: number | null;
  frameSetId?: string | null;
  blankConfigVersionId?: string | null;
  processingRevisionId?: string | null;
}

interface GradingBlankCorrectionVersions {
  teacherReason: string;
  expectedGradingRevision: number;
  frameSetId: string;
  blankConfigVersionId: string;
  processingRevisionId: string;
}

export type GradingBlankCorrection = GradingBlankCorrectionVersions & (
  | {recognizedText: string; finalStatus?: never}
  | {recognizedText?: never; finalStatus: "correct" | "incorrect"}
);

export interface GradingBlankCorrectionResult {
  questionResultId: string;
  blankKey: string;
  gradingRevision: number;
  runRevision: number;
  frameSetId: string;
  blankConfigVersionId: string;
  processingRevisionId: string;
  blankResult: Pick<
    GradingBlankResult,
    "id" | "blankKey" | "recognizedAnswer" | "status" | "score" | "maxScore" | "reviewReasons"
  > & {decision?: Record<string, unknown>};
  affectedResultIds: string[];
}

export class ApiError extends Error {
  code: string;
  details: unknown;

  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

export async function api<T>(
  path: string,
  init?: RequestInit,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(`/api${path}`, {...init, signal});
  const json: unknown = await response.json();
  const envelope = apiEnvelopeSchema.parse(json);
  if (!response.ok || envelope.error) {
    throw new ApiError(
      envelope.error?.code ?? "REQUEST_FAILED",
      envelope.error?.message ?? "请求失败",
      envelope.error?.details
    );
  }
  return envelope.data as T;
}

export function uploadTask(
  exam: File,
  answer: File,
  title: string
): Promise<{taskId: string; runId: string}> {
  const form = new FormData();
  form.append("exam", exam);
  form.append("answer", answer);
  form.append("title", title);
  return api("/tasks", {method: "POST", body: form});
}

export function uploadStudentSubmission(
  taskId: string,
  file: File,
  studentIdentifier: string,
  studentName: string
): Promise<{submissionId: string; status: string}> {
  const form = new FormData();
  form.append("file", file);
  form.append("studentIdentifier", studentIdentifier);
  form.append("studentName", studentName);
  return api(`/tasks/${taskId}/student-submissions`, {method: "POST", body: form});
}

export type StudentPageAlignmentUpdate =
  | {
      expectedAlignmentRevision: number;
      templatePageId: string;
      controlPoints: AlignmentControlPointPair[];
      clearOverride?: false;
    }
  | {
      expectedAlignmentRevision: number;
      clearOverride: true;
      templatePageId?: never;
      controlPoints?: never;
    };

export interface StudentPageAlignmentUpdateResult {
  submissionId: string;
  studentPageId: string;
  processingRevisionId: string;
  alignmentRevision: number;
  status: string;
}

export function updateStudentPageAlignment(
  submissionId: string,
  studentPageId: string,
  payload: StudentPageAlignmentUpdate
): Promise<StudentPageAlignmentUpdateResult> {
  return api(`/student-submissions/${submissionId}/pages/${studentPageId}/alignment`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
}

export function deleteTask(taskId: string): Promise<{taskId: string; deleted: boolean}> {
  return api(`/tasks/${taskId}`, {method: "DELETE"});
}

export interface DeleteStudentSubmissionResult {
  submissionId: string;
  taskId: string;
  deleted: boolean;
  cancelledJobs: number;
  cleanupPending: boolean;
}

export function deleteStudentSubmission(
  submissionId: string
): Promise<DeleteStudentSubmissionResult> {
  return api(`/student-submissions/${submissionId}`, {method: "DELETE"});
}

export function generateAnswerGradingDraft(
  questionId: string
): Promise<AnswerGradingDraftPreview> {
  return api(`/questions/${questionId}/answer-grading-drafts`, {method: "POST"});
}

export function applyAnswerGradingDraft(
  questionId: string,
  runId: string
): Promise<ApplyAnswerGradingDraftResult> {
  return api(`/questions/${questionId}/answer-grading-drafts/${runId}/apply`, {method: "POST"});
}

export function saveQuestionFrameItem(
  frameSetId: string,
  questionId: string,
  expectedRevision: number,
  regions: QuestionFrameFragment[]
): Promise<QuestionFrameSet> {
  return api(`/question-frame-sets/${frameSetId}/questions/${questionId}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expectedRevision, regions})
  });
}

export function rerecognizeQuestionFrameItem(
  frameSetId: string,
  questionId: string,
  expectedRevision: number,
  regions: QuestionFrameFragment[]
): Promise<SingleQuestionRerecognitionResult> {
  return api(`/question-frame-sets/${frameSetId}/questions/${questionId}/rerecognize`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expectedRevision, regions})
  });
}

export function normalizeQuestionFrameDraft(
  frameSetId: string,
  expectedRevision: number
): Promise<QuestionFrameSet> {
  return api(`/question-frame-sets/${frameSetId}/normalize-model-draft`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expectedRevision})
  });
}

export function confirmQuestionFrameItem(
  frameSetId: string,
  questionId: string,
  expectedRevision: number
): Promise<QuestionFrameSet> {
  return api(`/question-frame-sets/${frameSetId}/questions/${questionId}/confirm`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expectedRevision})
  });
}

export function confirmQuestionFrameSet(
  frameSetId: string,
  expectedRevision: number
): Promise<QuestionFrameSet> {
  return api(`/question-frame-sets/${frameSetId}/confirm`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expectedRevision})
  });
}

export function createGradingRun(submissionId: string): Promise<{gradingRunId: string; status: string}> {
  return api(`/student-submissions/${submissionId}/grading-runs`, {method: "POST"});
}

export function resolveGradingReview(
  reviewItemId: string,
  payload: GradingReviewResolution
): Promise<GradingReviewResolutionResult> {
  return api(`/grading-review-items/${reviewItemId}/resolve`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
}

export function correctGradingBlank(
  questionResultId: string,
  blankKey: string,
  payload: GradingBlankCorrection
): Promise<GradingBlankCorrectionResult> {
  return api(`/grading-question-results/${questionResultId}/blanks/${encodeURIComponent(blankKey)}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
}

async function artifactResponse(url: string): Promise<Response> {
  const response = await fetch(url);
  if (response.ok) return response;
  let error: unknown;
  try {
    error = await response.json();
  } catch {
    throw new ApiError("ARTIFACT_REQUEST_FAILED", "结果文件暂时无法读取，请稍后重试");
  }
  const envelope = apiEnvelopeSchema.parse(error);
  throw new ApiError(
    envelope.error?.code ?? "ARTIFACT_REQUEST_FAILED",
    envelope.error?.message ?? "结果文件暂时无法读取，请稍后重试",
    envelope.error?.details
  );
}

function fileName(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

export async function previewArtifact(url: string): Promise<void> {
  const opened = window.open("about:blank", "_blank");
  if (!opened) {
    throw new ApiError("POPUP_BLOCKED", "浏览器阻止了预览窗口，请允许弹出窗口后重试");
  }
  opened.opener = null;
  try {
    const response = await artifactResponse(url);
    const objectUrl = URL.createObjectURL(await response.blob());
    opened.location.replace(objectUrl);
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (error) {
    opened.close();
    throw error;
  }
}

export async function downloadArtifact(url: string, fallbackName: string): Promise<void> {
  const response = await artifactResponse(url);
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName(response, fallbackName);
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
}
