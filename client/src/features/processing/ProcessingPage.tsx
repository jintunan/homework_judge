import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { Progress, TaskStatus } from "@shared/contracts";
import { api } from "@/lib/api";
import { ActionFeedback } from "@/components/ActionFeedback";

const steps: Array<{status: TaskStatus; label: string; detail: string}> = [
  {status: "preparing", label: "页面准备", detail: "转换文档并保留原始页码"},
  {status: "exam_recognizing", label: "识别试卷", detail: "抽取题号、题干、选项与分值"},
  {status: "answer_recognizing", label: "识别答案", detail: "兼容精简答案和完整解析版"},
  {status: "matching", label: "答案匹配", detail: "题号优先，题干相似度辅助"}
];
const order = ["queued", ...steps.map((step) => step.status), "review_pending", "completed"];

export function ProcessingPage() {
  const {taskId = ""} = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["progress", taskId],
    queryFn: () => api<Progress>(`/tasks/${taskId}/progress`),
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      return status && ["review_pending", "completed", "failed"].includes(status) ? false : 1200;
    }
  });
  const status = query.data?.status ?? "queued";
  useEffect(() => {
    if (status === "review_pending" || status === "completed") {
      navigate(`/tasks/${taskId}/review`, {replace: true});
    }
  }, [navigate, status, taskId]);
  const retry = useMutation({
    mutationFn: () => api(`/tasks/${taskId}/process`, {method: "POST"}),
    onSuccess: () => queryClient.invalidateQueries({queryKey: ["progress", taskId]})
  });
  return (
    <section className="page page--narrow">
      <div className="page-head">
        <div>
          <p className="eyebrow">PROCESSING</p>
          <h1>正在准备逐题结果</h1>
          <p>识别阶段彼此独立，失败时可以从新运行重试，旧记录会保留。</p>
        </div>
      </div>
      <div className="panel process-panel">
        {steps.map((step) => {
          const currentIndex = order.indexOf(status);
          const stepIndex = order.indexOf(step.status);
          const done = currentIndex > stepIndex && status !== "failed";
          const active = status === step.status;
          return (
            <div key={step.status} className={`process-step ${active ? "process-step--active" : ""}`}>
              <span className="process-step__icon">
                {done ? <Check /> : active ? <LoaderCircle className="spin" /> : <span />}
              </span>
              <div><strong>{step.label}</strong><small>{step.detail}</small></div>
            </div>
          );
        })}
        {status === "failed" && (
          <div className="failure-card">
            <AlertTriangle />
            <div>
              <strong>{query.data?.errorMessage ?? "处理失败"}</strong>
              <code>{query.data?.errorCode}</code>
            </div>
            <button className="button" type="button" disabled={retry.isPending} onClick={() => retry.mutate()}><RotateCcw size={16} />{retry.isPending ? "正在重新运行…" : "重新运行"}</button>
          </div>
        )}
        <ActionFeedback message={retry.isSuccess ? "已重新启动识别处理" : undefined} error={retry.error instanceof Error ? retry.error.message : undefined} />
        {query.error && <div className="alert alert--error">{query.error.message}</div>}
      </div>
    </section>
  );
}
