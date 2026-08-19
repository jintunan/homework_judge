import type { TaskStatus } from "@shared/contracts";

const labels: Record<TaskStatus, string> = {
  draft: "草稿",
  queued: "等待处理",
  preparing: "转换页面",
  exam_recognizing: "识别题目",
  answer_recognizing: "识别答案",
  matching: "匹配答案",
  review_pending: "待人工确认",
  completed: "已完成",
  failed: "处理失败"
};

export function StatusBadge({status}: {status: TaskStatus}) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {labels[status]}
    </span>
  );
}

