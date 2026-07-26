import type {
  ApiResponse,
  AnswerConfigDetail,
  AnswerConfigProgress,
  AnswerConfigVersionSummary,
  AnswerMode,
  AnswerQuestionDraft,
  AnswerResolutionRun,
  AuditEvent,
  ClassStatistics,
  GradingTaskDetail,
  GradingTaskSummary,
  HealthStatus,
  ModelStatus,
  Submission,
  SubmissionReview,
  Subject,
  StudentReport,
  TaskProgress,
} from "@shared/contracts";
import type { QuestionsBatchInput } from "@shared/schemas";

export class ApiClientError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly fields?: Record<string, string[]>,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function request<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, {
    ...options,
    headers,
  });
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<T>
    | null;
  if (!response.ok || !payload || !payload.ok) {
    const failure = payload && !payload.ok ? payload.error : null;
    throw new ApiClientError(
      failure?.code ?? "REQUEST_FAILED",
      failure?.message ?? `请求失败（${response.status}）`,
      response.status,
      failure?.fields,
    );
  }
  return payload.data;
}

export const api = {
  listTasks: () => request<GradingTaskSummary[]>("/api/tasks"),
  getTask: (taskId: string) =>
    request<GradingTaskDetail>(`/api/tasks/${taskId}`),
  getHealth: () => request<HealthStatus>("/api/health"),
  getModelStatus: () => request<ModelStatus>("/api/model/status"),
  createTask: (
    fields: {
      name: string;
      className: string;
      paperName: string;
      subject: Subject;
      answerMode: AnswerMode;
    },
    template: File,
    referenceAnswer?: File | null,
  ) => {
    const form = new FormData();
    form.set("name", fields.name);
    form.set("className", fields.className);
    form.set("paperName", fields.paperName);
    form.set("subject", fields.subject);
    form.set("answerMode", fields.answerMode);
    form.set("template", template);
    if (referenceAnswer) form.set("referenceAnswer", referenceAnswer);
    return request<GradingTaskDetail>("/api/tasks", {
      method: "POST",
      body: form,
    });
  },
  saveQuestions: (taskId: string, input: QuestionsBatchInput) =>
    request<GradingTaskDetail>(`/api/tasks/${taskId}/questions`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  startAnswerConfig: (taskId: string) =>
    request<{
      version: AnswerConfigVersionSummary;
      runtime: { tasks: number; questions: number };
    }>(`/api/tasks/${taskId}/answer-config-runs`, { method: "POST" }),
  getAnswerConfig: (taskId: string) =>
    request<AnswerConfigDetail>(`/api/tasks/${taskId}/answer-config`),
  getAnswerConfigProgress: (taskId: string) =>
    request<{
      status: GradingTaskSummary["answerConfigStatus"];
      version: AnswerConfigVersionSummary | null;
      progress: AnswerConfigProgress;
      runtime: { tasks: number; questions: number };
    }>(`/api/tasks/${taskId}/answer-config-progress`),
  updateAnswerDraft: (
    draftId: string,
    input: {
      number: string;
      type: AnswerQuestionDraft["type"];
      maxScore: number;
      standardAnswer: string;
      scoringPoints: AnswerQuestionDraft["effectiveScoringPoints"];
    },
  ) =>
    request<AnswerQuestionDraft>(`/api/answer-drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  approveAnswerDraft: (draftId: string) =>
    request<AnswerQuestionDraft>(
      `/api/answer-drafts/${draftId}/approve`,
      { method: "POST" },
    ),
  rejectAnswerDraft: (draftId: string, reason: string) =>
    request<AnswerQuestionDraft>(
      `/api/answer-drafts/${draftId}/reject`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  researchAnswerDraft: (draftId: string) =>
    request<{ status: string }>(
      `/api/answer-drafts/${draftId}/research`,
      { method: "POST" },
    ),
  regenerateAnswerDraft: (draftId: string) =>
    request<{ status: string }>(
      `/api/answer-drafts/${draftId}/regenerate`,
      { method: "POST" },
    ),
  publishAnswerConfig: (taskId: string) =>
    request<AnswerConfigVersionSummary>(
      `/api/tasks/${taskId}/answer-config/approve`,
      { method: "POST" },
    ),
  reviseAnswerConfig: (taskId: string) =>
    request<AnswerConfigVersionSummary>(
      `/api/tasks/${taskId}/answer-config/revise`,
      { method: "POST" },
    ),
  getAnswerRun: (runId: string) =>
    request<AnswerResolutionRun>(`/api/answer-runs/${runId}`),
  listSubmissions: (taskId: string) =>
    request<Submission[]>(`/api/tasks/${taskId}/submissions`),
  uploadSubmissions: (taskId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<{
      results: Array<
        | { originalName: string; ok: true; submission: Submission }
        | {
            originalName: string;
            ok: false;
            error: { code: string; message: string };
          }
      >;
    }>(`/api/tasks/${taskId}/submissions`, {
      method: "POST",
      body: form,
    });
  },
  updateStudentName: (submissionId: string, studentName: string) =>
    request<Submission>(`/api/submissions/${submissionId}`, {
      method: "PATCH",
      body: JSON.stringify({ studentName }),
    }),
  startGrading: (taskId: string) =>
    request<{
      queued: number;
      progress: TaskProgress;
      runtime: { pending: number; active: number };
    }>(`/api/tasks/${taskId}/grading-runs`, { method: "POST" }),
  retrySubmission: (submissionId: string) =>
    request<{ submissionId: string; status: string }>(
      `/api/submissions/${submissionId}/retry`,
      { method: "POST" },
    ),
  getReview: (submissionId: string) =>
    request<SubmissionReview>(`/api/submissions/${submissionId}/review`),
  updateReview: (
    submissionId: string,
    questionId: string,
    input: {
      finalAnswer: string;
      finalScore: number;
      teacherComment: string;
      reviewStatus: "pending" | "needs_attention" | "reviewed";
    },
  ) =>
    request<SubmissionReview>(
      `/api/submissions/${submissionId}/reviews/${questionId}`,
      {
        method: "PATCH",
        body: JSON.stringify(input),
      },
    ),
  confirmSubmission: (submissionId: string) =>
    request<SubmissionReview>(
      `/api/submissions/${submissionId}/confirm`,
      { method: "POST" },
    ),
  getStudentReport: (submissionId: string) =>
    request<StudentReport>(`/api/submissions/${submissionId}/report`),
  getStatistics: (taskId: string) =>
    request<ClassStatistics>(`/api/tasks/${taskId}/statistics`),
  getAudit: (submissionId: string) =>
    request<AuditEvent[]>(`/api/submissions/${submissionId}/audit`),
};
