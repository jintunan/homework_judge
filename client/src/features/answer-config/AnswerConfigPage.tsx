import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minus,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Document, Page, pdfjs } from "react-pdf";
import {
  Link,
  useBlocker,
  useLocation,
  useParams,
} from "react-router-dom";
import type {
  AnswerQuestionDraft,
  AnswerSourceType,
} from "@shared/contracts";
import { subjectLabel } from "@shared/subject-profiles";
import { EmptyState } from "@client/components/EmptyState";
import { Feedback, type FeedbackMessage } from "@client/components/Feedback";
import { PageHeader } from "@client/components/PageHeader";
import { api } from "@client/lib/api";
import { formatDate } from "@client/lib/format";
import {
  AnswerDraftCard,
  type AnswerDraftValues,
} from "./AnswerDraftCard";
import { AnswerProgress } from "./AnswerProgress";
import { RunHistoryDrawer } from "./RunHistoryDrawer";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type Filter =
  | "all"
  | "pending"
  | "attention"
  | AnswerSourceType
  | "failed";

function TemplatePreview({
  url,
  mimeType,
  name,
}: {
  url: string;
  mimeType: string;
  name: string;
}) {
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [scale, setScale] = useState(0.82);
  const pdf = mimeType === "application/pdf";
  return (
    <section className="answer-paper-pane">
      <div className="paper-toolbar">
        <div>
          <span className="paper-student">固定模板</span>
          <span>{name}</span>
        </div>
        <div className="paper-controls">
          {pdf ? (
            <>
              <button
                type="button"
                disabled={pageNumber <= 1}
                onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
              >
                <ChevronLeft size={16} />
              </button>
              <span>{pageNumber} / {pageCount}</span>
              <button
                type="button"
                disabled={pageNumber >= pageCount}
                onClick={() =>
                  setPageNumber((value) => Math.min(pageCount, value + 1))
                }
              >
                <ChevronRight size={16} />
              </button>
            </>
          ) : null}
          <button type="button" onClick={() => setScale((v) => Math.max(0.5, v - 0.1))}>
            <Minus size={15} />
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button type="button" onClick={() => setScale((v) => Math.min(1.6, v + 0.1))}>
            <Plus size={15} />
          </button>
          <button type="button" onClick={() => setScale(0.82)}>
            <Maximize2 size={15} />
          </button>
        </div>
      </div>
      <div className="paper-canvas">
        {pdf ? (
          <Document
            file={url}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
            loading={<div className="paper-loading">正在载入 PDF…</div>}
          >
            <Page
              pageNumber={pageNumber}
              scale={scale}
              renderTextLayer={false}
              renderAnnotationLayer={false}
            />
          </Document>
        ) : (
          <img
            src={url}
            alt={name}
            style={{ transform: `scale(${scale})` }}
          />
        )}
      </div>
    </section>
  );
}

export function AnswerConfigPage() {
  const { taskId = "" } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const initialWarning = (
    location.state as { warning?: string } | null
  )?.warning;
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(
    initialWarning ? { type: "error", text: initialWarning } : null,
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [historyRunId, setHistoryRunId] = useState<string | null>(null);
  const [busyDraftId, setBusyDraftId] = useState<string | null>(null);
  const [dirtyDraftIds, setDirtyDraftIds] = useState<Set<string>>(
    () => new Set(),
  );
  const hasUnsavedChanges = dirtyDraftIds.size > 0;
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      hasUnsavedChanges &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [hasUnsavedChanges]);

  const setDraftDirty = useCallback(
    (draftId: string, changed: boolean) => {
      setDirtyDraftIds((current) => {
        const next = new Set(current);
        if (changed) next.add(draftId);
        else next.delete(draftId);
        return next;
      });
    },
    [],
  );

  function changeFilter(nextFilter: Filter) {
    if (
      hasUnsavedChanges &&
      !window.confirm("当前有未保存修改，切换筛选会丢失这些内容，仍要继续吗？")
    ) {
      return;
    }
    if (hasUnsavedChanges) setDirtyDraftIds(new Set());
    setFilter(nextFilter);
  }

  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: 2500,
  });
  const config = useQuery({
    queryKey: ["answer-config", taskId],
    queryFn: () => api.getAnswerConfig(taskId),
    enabled: Boolean(taskId),
    refetchInterval: 1800,
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["answer-config", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  }

  const start = useMutation({
    mutationFn: () => api.startAnswerConfig(taskId),
    onSuccess: () => {
      setFeedback({ type: "success", text: "已启动答案配置 Agent" });
      refresh();
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "启动失败",
      }),
  });
  const publish = useMutation({
    mutationFn: () => api.publishAnswerConfig(taskId),
    onSuccess: (version) => {
      setFeedback({
        type: "success",
        text: `答案版本 V${version.versionNumber} 已发布，可上传学生试卷`,
      });
      refresh();
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "发布失败",
      }),
  });
  const revise = useMutation({
    mutationFn: () => api.reviseAnswerConfig(taskId),
    onSuccess: () => {
      setFeedback({ type: "success", text: "已创建待审核修订版本" });
      refresh();
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "创建修订版本失败",
      }),
  });

  async function runDraftAction(
    draftId: string,
    action: () => Promise<unknown>,
    successText: string,
  ) {
    setBusyDraftId(draftId);
    try {
      await action();
      setFeedback({ type: "success", text: successText });
      refresh();
    } catch (error) {
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "操作失败",
      });
    } finally {
      setBusyDraftId(null);
    }
  }

  const drafts = config.data?.drafts ?? [];
  const filtered = useMemo(
    () =>
      drafts.filter((draft) => {
        if (filter === "all") return true;
        if (filter === "pending") return draft.reviewStatus === "pending";
        if (filter === "attention") return draft.needsAttention;
        if (filter === "failed") return draft.reviewStatus === "failed";
        return draft.sourceType === filter;
      }),
    [drafts, filter],
  );
  const version = config.data?.version;
  const readOnly = version?.status === "approved";
  const publishBlocked =
    (version?.unresolvedIssueCount ?? 0) > 0 ||
    drafts.some((draft) => draft.requiresCorrection);
  const processing = [
    "queued",
    "extracting",
    "searching",
    "generating",
  ].includes(task.data?.answerConfigStatus ?? "");

  if (task.isLoading || config.isLoading) {
    return <div className="panel page-loading">正在读取答案配置…</div>;
  }
  if (!task.data || !config.data) {
    return (
      <EmptyState title="任务不存在" description="请返回任务总览重新选择。" />
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow={`${subjectLabel(task.data.subject)} · 答案配置`}
        title={task.data.paperName}
        description={
          version
            ? `答案版本 V${version.versionNumber} · ${
                task.data.answerMode === "reference_upload"
                  ? "参考答案提取"
                  : "联网检索与模型生成"
              }`
            : "尚未创建答案配置版本"
        }
        actions={
          task.data.answerConfigStatus === "approved" ? (
            <Link
              to={`/tasks/${taskId}/upload`}
              className="button button-primary"
            >
              上传学生试卷
              <ArrowRight size={17} />
            </Link>
          ) : null
        }
      />
      <Feedback message={feedback} onDismiss={() => setFeedback(null)} />

      <AnswerProgress
        status={task.data.answerConfigStatus}
        progress={config.data.progress}
      />

      {(version?.unresolvedIssueCount ?? 0) > 0 ? (
        <section className="panel answer-start-empty">
          <span><AlertTriangle size={30} /></span>
          <div>
            <h2>试卷识别仍有阻塞问题</h2>
            <p>
              当前版本有 {version!.unresolvedIssueCount} 项结构问题，不能发布。
              原始识别和修复记录已保留，可重新识别生成新版本。
            </p>
          </div>
          <button
            type="button"
            className="button button-secondary"
            disabled={start.isPending}
            onClick={() => start.mutate()}
          >
            <RefreshCw size={17} />
            重新识别
          </button>
        </section>
      ) : null}

      {!version || (task.data.answerConfigStatus === "failed" && drafts.length === 0) ? (
        <section className="panel answer-start-empty">
          <span><Sparkles size={30} /></span>
          <div>
            <h2>启动答案配置 Agent</h2>
            <p>
              Agent 会识别固定试卷，并按所选方式提取、检索或生成答案草稿。
            </p>
          </div>
          <button
            type="button"
            className="button button-primary"
            disabled={start.isPending}
            onClick={() => start.mutate()}
          >
            <Rocket size={17} />
            {start.isPending ? "正在启动…" : "开始自动配置"}
          </button>
        </section>
      ) : null}

      {processing && drafts.length === 0 ? (
        <section className="panel answer-processing-empty">
          <span className="processing-orbit"><Sparkles size={28} /></span>
          <h2>Agent 正在读取试卷</h2>
          <p>识别完成后会自动显示逐题答案草稿，可以继续浏览其他页面。</p>
        </section>
      ) : null}

      {drafts.length > 0 ? (
        <>
          <div className="answer-review-toolbar panel">
            <div className="answer-filter-tabs">
              {[
                ["all", "全部", drafts.length],
                ["pending", "待审核", config.data.progress.pending],
                ["attention", "需关注", config.data.progress.needsAttention],
                ["web_searched", "联网检索", config.data.progress.webSearched],
                ["model_generated", "模型生成", config.data.progress.modelGenerated],
                ["failed", "失败", config.data.progress.failed],
              ].map(([value, label, count]) => (
                <button
                  type="button"
                  className={filter === value ? "active" : ""}
                  onClick={() => changeFilter(value as Filter)}
                  key={String(value)}
                >
                  {label}<span>{count}</span>
                </button>
              ))}
            </div>
            <button
              type="button"
              className="button button-ghost button-small"
              onClick={() => {
                void config.refetch();
                void task.refetch();
              }}
            >
              <RefreshCw size={15} />
              刷新
            </button>
          </div>

          <div className="answer-review-workspace">
            {task.data.templateFile ? (
              <TemplatePreview
                url={task.data.templateFile.previewUrl}
                mimeType={task.data.templateFile.mimeType}
                name={task.data.templateFile.originalName}
              />
            ) : null}
            <section className="answer-draft-pane">
              <div className="answer-draft-pane-heading">
                <div>
                  <span className="eyebrow">
                    {readOnly ? "已发布版本" : "教师逐题审核"}
                  </span>
                  <h2>
                    {filtered.length} / {drafts.length} 道题
                  </h2>
                </div>
                {readOnly && version?.approvedAt ? (
                  <small>
                    {version.approvedBy} · {formatDate(version.approvedAt)}
                  </small>
                ) : null}
              </div>
              {filtered.length === 0 ? (
                <EmptyState
                  compact
                  title="当前筛选没有题目"
                  description="切换其他状态查看答案草稿。"
                />
              ) : (
                filtered.map((draft) => (
                  <AnswerDraftCard
                    key={draft.id}
                    draft={draft}
                    subject={task.data.subject}
                    readOnly={readOnly}
                    busy={busyDraftId === draft.id}
                    onHistory={setHistoryRunId}
                    onDirtyChange={setDraftDirty}
                    onSave={(values) =>
                      runDraftAction(
                        draft.id,
                        () => api.updateAnswerDraft(draft.id, values),
                        `第 ${values.number} 题已保存`,
                      )
                    }
                    onApprove={(values) =>
                      runDraftAction(
                        draft.id,
                        async () => {
                          await api.updateAnswerDraft(draft.id, values);
                          await api.approveAnswerDraft(draft.id);
                        },
                        `第 ${values.number} 题已审核通过`,
                      )
                    }
                    onReject={(reason) =>
                      runDraftAction(
                        draft.id,
                        () => api.rejectAnswerDraft(draft.id, reason),
                        `第 ${draft.effectiveNumber} 题已退回`,
                      )
                    }
                    onResearch={() =>
                      runDraftAction(
                        draft.id,
                        () => api.researchAnswerDraft(draft.id),
                        `第 ${draft.effectiveNumber} 题已加入搜索队列`,
                      )
                    }
                    onRegenerate={() =>
                      runDraftAction(
                        draft.id,
                        () => api.regenerateAnswerDraft(draft.id),
                        `第 ${draft.effectiveNumber} 题已加入生成队列`,
                      )
                    }
                  />
                ))
              )}
            </section>
          </div>

          <div className="answer-publish-bar">
            <div>
              {readOnly ? (
                <>
                  <CheckCircle2 size={20} />
                  <span>
                    <strong>答案版本已发布</strong>
                    <small>如需修改，请创建新的修订版本。</small>
                  </span>
                </>
              ) : config.data.progress.approved === drafts.length ? (
                <>
                  <CheckCircle2 size={20} />
                  <span>
                    <strong>所有题目已审核通过</strong>
                    <small>发布后才能上传和批改学生试卷。</small>
                  </span>
                </>
              ) : (
                <>
                  <AlertTriangle size={20} />
                  <span>
                    <strong>
                      已审核 {config.data.progress.approved} / {drafts.length}
                    </strong>
                    <small>仍有题目需要教师确认。</small>
                  </span>
                </>
              )}
            </div>
            {readOnly ? (
              <button
                type="button"
                className="button button-secondary"
                disabled={revise.isPending}
                onClick={() => revise.mutate()}
              >
                创建修订版本
              </button>
            ) : (
              <button
                type="button"
                className="button button-primary"
                disabled={
                  config.data.progress.approved !== drafts.length ||
                  publishBlocked ||
                  publish.isPending
                }
                onClick={() => publish.mutate()}
              >
                <CheckCircle2 size={17} />
                {publish.isPending ? "正在发布…" : "发布答案配置"}
              </button>
            )}
          </div>
        </>
      ) : null}

      {task.data.answerMode === "agent_search" ? (
        <div className="answer-search-footer">
          <Search size={16} />
          <span>
            联网搜索只发送科目与公开题干；学生姓名、班级和学生答卷不会用于搜索。
          </span>
        </div>
      ) : null}

      <RunHistoryDrawer
        runId={historyRunId}
        onClose={() => setHistoryRunId(null)}
      />
      {blocker.state === "blocked" ? (
        <div className="drawer-backdrop">
          <section className="panel unsaved-answer-dialog" role="dialog">
            <AlertTriangle size={24} />
            <div>
              <h2>还有未保存的答案修改</h2>
              <p>离开后这些本地编辑会丢失。可以返回继续保存，或确认离开。</p>
            </div>
            <div className="dialog-actions">
              <button
                type="button"
                className="button button-secondary"
                onClick={() => blocker.reset()}
              >
                返回继续编辑
              </button>
              <button
                type="button"
                className="button button-danger-soft"
                onClick={() => blocker.proceed()}
              >
                放弃修改并离开
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
