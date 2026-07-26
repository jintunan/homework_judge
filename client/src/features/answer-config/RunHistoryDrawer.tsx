import { X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@client/lib/api";
import { formatDate } from "@client/lib/format";

const runKindLabels = {
  exam_extraction: "试卷结构识别",
  reference_extraction: "试卷与参考答案识别",
  structure_repair: "结构修复",
  web_search: "联网检索",
  model_generation: "模型生成",
};

export function RunHistoryDrawer({
  runId,
  onClose,
}: {
  runId: string | null;
  onClose: () => void;
}) {
  const run = useQuery({
    queryKey: ["answer-run", runId],
    queryFn: () => api.getAnswerRun(runId!),
    enabled: Boolean(runId),
  });
  if (!runId) return null;
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="run-history-drawer"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="drawer-heading">
          <div>
            <span className="eyebrow">只读审计记录</span>
            <h2>Agent 原始运行</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>
        {run.isLoading ? (
          <div className="page-loading">正在读取运行记录…</div>
        ) : run.data ? (
          <div className="run-detail">
            <div className="run-meta-grid">
              <div>
                <span>类型</span>
                <strong>{runKindLabels[run.data.kind]}</strong>
              </div>
              <div><span>模型</span><strong>{run.data.model}</strong></div>
              <div><span>状态</span><strong>{run.data.status}</strong></div>
              <div>
                <span>开始时间</span>
                <strong>{formatDate(run.data.startedAt)}</strong>
              </div>
            </div>
            {run.data.errorMessage ? (
              <div className="run-error">{run.data.errorMessage}</div>
            ) : null}
            <section>
              <h3>去敏请求摘要</h3>
              <pre>{JSON.stringify(run.data.requestSnapshot, null, 2)}</pre>
            </section>
            <section>
              <h3>解析结果</h3>
              <pre>{JSON.stringify(run.data.parsedOutput, null, 2)}</pre>
            </section>
            <section>
              <h3>百炼原始响应</h3>
              <pre>{JSON.stringify(run.data.rawResponse, null, 2)}</pre>
            </section>
          </div>
        ) : (
          <div className="run-error">运行记录不存在或读取失败</div>
        )}
      </aside>
    </div>
  );
}
