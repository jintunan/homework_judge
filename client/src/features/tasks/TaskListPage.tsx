import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus, RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { TaskSummary } from "@shared/contracts";
import { api, deleteTask } from "@/lib/api";
import { ConfirmDeleteTaskDialog } from "@/components/ConfirmDeleteTaskDialog";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { ActionFeedback } from "@/components/ActionFeedback";

export function TaskListPage() {
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState<TaskSummary | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api<TaskSummary[]>("/tasks")
  });
  const remove = useMutation({
    mutationFn: (taskId: string) => deleteTask(taskId),
    onSuccess: async () => {
      setDeleting(null);
      setDeleteError("");
      setMessage("任务已删除");
      await queryClient.invalidateQueries({queryKey: ["tasks"]});
    },
    onError: (error) => setDeleteError(error instanceof Error ? error.message : "删除失败")
  });
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <p className="eyebrow">WORKBENCH</p>
          <h1>识别任务</h1>
          <p>上传试卷与参考答案，逐题核对模型识别和匹配结果。</p>
        </div>
        <Link className="button button--primary" to="/new"><Plus size={18} />新建识别</Link>
      </div>
      <ActionFeedback message={message} />
      {query.isLoading ? (
        <div className="loading"><RotateCw className="spin" />正在读取任务…</div>
      ) : query.error ? (
        <div className="alert alert--error">{query.error.message} <button className="button" type="button" onClick={() => query.refetch()}>重新读取</button></div>
      ) : !query.data?.length ? (
        <EmptyState title="还没有识别任务" action={<Link className="button button--primary" to="/new">上传第一套试卷</Link>}>
          一套试卷配一份参考答案，系统会分别识别后再做可解释匹配。
        </EmptyState>
      ) : (
        <div className="task-grid">
          {query.data.map((task) => (
            <article key={task.id} className="task-card">
              <Link className="task-card__link" to={task.status === "review_pending" || task.status === "completed" ? `/tasks/${task.id}/review` : `/tasks/${task.id}/processing`}>
              <div className="task-card__top">
                <StatusBadge status={task.status} />
                <ArrowRight size={18} />
              </div>
              <h2>{task.title}</h2>
              <p>{task.questionCount ? `${task.confirmedCount}/${task.questionCount} 题已确认` : "等待生成题目"}</p>
              <time>{new Date(task.updated_at).toLocaleString("zh-CN")}</time>
              </Link>
              <button type="button" className="task-card__delete" aria-label={`删除任务 ${task.title}`} onClick={() => {setMessage(""); setDeleteError(""); setDeleting(task);}}><Trash2 size={16} />删除</button>
            </article>
          ))}
        </div>
      )}
      {deleting && <ConfirmDeleteTaskDialog title={deleting.title} busy={remove.isPending} error={deleteError} onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)} />}
    </section>
  );
}
