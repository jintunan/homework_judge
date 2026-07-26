import type {
  QuestionType,
  Subject,
  SubmissionStatus,
  TaskStatus,
} from "@shared/contracts";

export const questionTypeLabel: Record<QuestionType, string> = {
  choice: "选择题",
  fill_blank: "填空题",
  short_answer: "简单简答题",
  calculation: "计算题",
};

export const subjectLabel: Record<Subject, string> = {
  middle_school_math: "初中数学",
  high_school_physics: "高中物理",
};

export const submissionStatusLabel: Record<SubmissionStatus, string> = {
  queued: "待处理",
  processing: "识别中",
  review_pending: "待复核",
  confirmed: "已确认",
  failed: "处理失败",
};

export const taskStatusLabel: Record<TaskStatus, string> = {
  draft: "配置中",
  ready: "待上传",
  grading: "批改中",
  reviewing: "复核中",
  completed: "已完成",
};

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
