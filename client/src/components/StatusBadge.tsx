import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  XCircle,
} from "lucide-react";
import type {
  ReviewStatus,
  SubmissionStatus,
  TaskStatus,
} from "@shared/contracts";
import {
  submissionStatusLabel,
  taskStatusLabel,
} from "@client/lib/format";

type Status = SubmissionStatus | TaskStatus | ReviewStatus;

const appearance: Record<
  Status,
  { tone: string; label: string; icon: typeof Clock3 }
> = {
  draft: { tone: "neutral", label: taskStatusLabel.draft, icon: Clock3 },
  ready: { tone: "blue", label: taskStatusLabel.ready, icon: Clock3 },
  grading: {
    tone: "blue",
    label: taskStatusLabel.grading,
    icon: LoaderCircle,
  },
  reviewing: {
    tone: "amber",
    label: taskStatusLabel.reviewing,
    icon: AlertTriangle,
  },
  completed: {
    tone: "green",
    label: taskStatusLabel.completed,
    icon: CheckCircle2,
  },
  queued: {
    tone: "neutral",
    label: submissionStatusLabel.queued,
    icon: Clock3,
  },
  processing: {
    tone: "blue",
    label: submissionStatusLabel.processing,
    icon: LoaderCircle,
  },
  review_pending: {
    tone: "amber",
    label: submissionStatusLabel.review_pending,
    icon: AlertTriangle,
  },
  confirmed: {
    tone: "green",
    label: submissionStatusLabel.confirmed,
    icon: CheckCircle2,
  },
  failed: {
    tone: "red",
    label: submissionStatusLabel.failed,
    icon: XCircle,
  },
  pending: { tone: "neutral", label: "待检查", icon: Clock3 },
  needs_attention: {
    tone: "red",
    label: "需人工判断",
    icon: AlertTriangle,
  },
  reviewed: { tone: "green", label: "已复核", icon: CheckCircle2 },
};

export function StatusBadge({
  status,
  compact = false,
}: {
  status: Status;
  compact?: boolean;
}) {
  const item = appearance[status];
  const Icon = item.icon;
  return (
    <span className={`status-badge status-${item.tone}`}>
      <Icon
        size={compact ? 12 : 14}
        strokeWidth={2.2}
        className={status === "processing" || status === "grading" ? "spin" : ""}
      />
      {!compact && item.label}
    </span>
  );
}
