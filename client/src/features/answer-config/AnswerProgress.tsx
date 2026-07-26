import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Globe2,
  Sparkles,
} from "lucide-react";
import type {
  AnswerConfigProgress as Progress,
  AnswerConfigStatus,
} from "@shared/contracts";

const statusText: Record<AnswerConfigStatus, string> = {
  not_started: "尚未启动",
  queued: "等待处理",
  extracting: "正在识别试卷",
  searching: "正在联网搜索",
  generating: "正在生成答案",
  review_pending: "等待教师审核",
  approved: "答案版本已发布",
  failed: "处理失败，可重试",
};

export function AnswerProgress({
  status,
  progress,
}: {
  status: AnswerConfigStatus;
  progress: Progress;
}) {
  const ratio =
    progress.total > 0
      ? Math.round(
          ((progress.approved +
            progress.webSearched +
            progress.modelGenerated +
            progress.failed) /
            Math.max(progress.total, 1)) *
            100,
        )
      : 0;
  return (
    <section className="answer-progress-panel panel">
      <div className="answer-progress-main">
        <span className={`answer-progress-icon status-${status}`}>
          {status === "approved" ? (
            <CheckCircle2 size={24} />
          ) : status === "failed" ? (
            <AlertTriangle size={24} />
          ) : (
            <Sparkles size={24} />
          )}
        </span>
        <div>
          <span className="eyebrow">Agent 状态</span>
          <h2>{statusText[status]}</h2>
          <div className="answer-progress-track">
            <span style={{ width: `${Math.min(100, ratio)}%` }} />
          </div>
        </div>
      </div>
      <div className="answer-progress-stats">
        <div><Clock3 size={15} /><span>题目</span><strong>{progress.total}</strong></div>
        <div><Globe2 size={15} /><span>检索</span><strong>{progress.webSearched}</strong></div>
        <div><Sparkles size={15} /><span>生成</span><strong>{progress.modelGenerated}</strong></div>
        <div><AlertTriangle size={15} /><span>关注</span><strong>{progress.needsAttention}</strong></div>
        <div><CheckCircle2 size={15} /><span>通过</span><strong>{progress.approved}</strong></div>
      </div>
    </section>
  );
}
