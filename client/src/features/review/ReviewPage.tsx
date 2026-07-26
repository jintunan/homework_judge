import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileSearch,
  History,
  Maximize2,
  Minus,
  Plus,
  Save,
  ScanText,
  Sparkles,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Document, Page, pdfjs } from "react-pdf";
import {
  Link,
  Navigate,
  useNavigate,
  useParams,
} from "react-router-dom";
import type {
  QuestionReview,
  SubmissionReview,
} from "@shared/contracts";
import { EmptyState } from "@client/components/EmptyState";
import { Feedback, type FeedbackMessage } from "@client/components/Feedback";
import { StatusBadge } from "@client/components/StatusBadge";
import { api } from "@client/lib/api";
import {
  formatDate,
  formatScore,
  percent,
  questionTypeLabel,
  subjectLabel,
} from "@client/lib/format";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface ReviewDraft {
  finalAnswer: string;
  finalScore: number;
  teacherComment: string;
  reviewStatus: QuestionReview["reviewStatus"];
}

function PreviewPane({
  review,
}: {
  review: SubmissionReview;
}) {
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [scale, setScale] = useState(0.9);
  const isPdf = review.submission.mimeType === "application/pdf";

  useEffect(() => {
    setPageNumber(1);
    setScale(0.9);
  }, [review.submission.id]);

  return (
    <section className="paper-pane">
      <div className="paper-toolbar">
        <div>
          <span className="paper-student">{review.submission.studentName}</span>
          <span>{review.submission.originalName}</span>
        </div>
        <div className="paper-controls">
          {isPdf ? (
            <>
              <button
                type="button"
                onClick={() =>
                  setPageNumber((current) => Math.max(1, current - 1))
                }
                disabled={pageNumber <= 1}
                aria-label="上一页"
              >
                <ChevronLeft size={17} />
              </button>
              <span>
                {pageNumber} / {pageCount}
              </span>
              <button
                type="button"
                onClick={() =>
                  setPageNumber((current) =>
                    Math.min(pageCount, current + 1),
                  )
                }
                disabled={pageNumber >= pageCount}
                aria-label="下一页"
              >
                <ChevronRight size={17} />
              </button>
              <i />
            </>
          ) : null}
          <button
            type="button"
            onClick={() =>
              setScale((current) => Math.max(0.5, current - 0.1))
            }
            aria-label="缩小"
          >
            <Minus size={16} />
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() =>
              setScale((current) => Math.min(1.8, current + 0.1))
            }
            aria-label="放大"
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            onClick={() => setScale(0.9)}
            aria-label="适合宽度"
          >
            <Maximize2 size={16} />
          </button>
        </div>
      </div>
      <div className="paper-canvas">
        {isPdf ? (
          <Document
            file={review.submission.previewUrl}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
            loading={<div className="paper-loading">正在载入 PDF 原卷…</div>}
            error={
              <div className="paper-error">
                PDF 预览失败，可
                <a
                  href={review.submission.previewUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开原文件
                </a>
              </div>
            }
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
            src={review.submission.previewUrl}
            alt={`${review.submission.studentName} 的原始试卷`}
            style={{ transform: `scale(${scale})` }}
          />
        )}
      </div>
    </section>
  );
}

function QuestionCard({
  review,
  draft,
  onChange,
  onSave,
  saving,
}: {
  review: QuestionReview;
  draft: ReviewDraft;
  onChange: (next: ReviewDraft) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const scoreInvalid =
    !Number.isFinite(draft.finalScore) ||
    draft.finalScore < 0 ||
    draft.finalScore > review.maxScore;
  const changed =
    draft.finalAnswer !== review.finalAnswer ||
    draft.finalScore !== review.finalScore ||
    draft.teacherComment !== review.teacherComment ||
    draft.reviewStatus !== review.reviewStatus;
  return (
    <article
      className={`review-question-card ${
        review.reviewStatus === "needs_attention" ? "attention" : ""
      }`}
    >
      <div className="review-question-heading">
        <div>
          <span className="question-number">第 {review.questionNumber} 题</span>
          <span className="question-type">
            {questionTypeLabel[review.questionType]} · {review.maxScore} 分
          </span>
        </div>
        <StatusBadge status={review.reviewStatus} />
      </div>

      {review.reviewStatus === "needs_attention" ? (
        <div className="attention-banner">
          <AlertTriangle size={16} />
          模型置信度较低或答案存在歧义，请人工判断
        </div>
      ) : null}

      <div className="model-result-block">
        <div className="model-result-top">
          <span>
            <Sparkles size={14} />
            模型初评
          </span>
          <span
            className={`confidence ${
              review.confidence < 0.65 ? "low" : ""
            }`}
          >
            置信度 {percent(review.confidence)}
          </span>
        </div>
        <div className="answer-compare">
          <div>
            <span>识别答案</span>
            <strong>{review.modelAnswer || "未识别"}</strong>
          </div>
          <div>
            <span>标准答案</span>
            <strong>{review.standardAnswer}</strong>
          </div>
          <div className="model-score">
            <span>建议得分</span>
            <strong>
              {formatScore(review.modelScore)}
              <small> / {formatScore(review.maxScore)}</small>
            </strong>
          </div>
        </div>
        <p className="model-reason">
          <ScanText size={15} />
          {review.modelReason || "模型未提供评分理由"}
        </p>
        {review.scoringPoints.length > 0 ? (
          <div className="review-points">
            {review.scoringPoints.map((point) => (
              <span key={point.description}>
                {point.description} · {point.score}分
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="teacher-decision">
        <div className="teacher-decision-heading">
          <span>教师终评</span>
          {changed ? <em>有未保存修改</em> : null}
        </div>
        <label className="field">
          <span>确认后的学生答案</span>
          <textarea
            rows={2}
            value={draft.finalAnswer}
            onChange={(event) =>
              onChange({ ...draft, finalAnswer: event.target.value })
            }
            placeholder="可修正模型识别内容"
          />
        </label>
        <div className="decision-row">
          <label className="field score-field">
            <span>最终得分</span>
            <div className={`final-score-input ${scoreInvalid ? "invalid" : ""}`}>
              <input
                type="number"
                min={0}
                max={review.maxScore}
                step={0.5}
                value={draft.finalScore}
                onChange={(event) =>
                  onChange({
                    ...draft,
                    finalScore: Number(event.target.value),
                  })
                }
              />
              <span>/ {formatScore(review.maxScore)} 分</span>
            </div>
            {scoreInvalid ? (
              <small className="field-error">
                得分必须在 0 到 {review.maxScore} 之间
              </small>
            ) : null}
          </label>
          <label className="field comment-field">
            <span>教师批注</span>
            <input
              value={draft.teacherComment}
              onChange={(event) =>
                onChange({
                  ...draft,
                  teacherComment: event.target.value,
                })
              }
              placeholder="给学生的简短反馈（可选）"
            />
          </label>
        </div>
        <div className="question-card-actions">
          {draft.reviewStatus === "reviewed" ? (
            <span className="reviewed-indicator">
              <CheckCircle2 size={15} />
              教师已复核
            </span>
          ) : (
            <span className="pending-indicator">
              <Clock3 size={15} />
              尚未确认本题
            </span>
          )}
          <button
            type="button"
            className="button button-primary button-small"
            disabled={scoreInvalid || saving}
            onClick={onSave}
          >
            <Check size={15} />
            {saving
              ? "保存中…"
              : draft.reviewStatus === "reviewed"
                ? "保存修改"
                : "保存并确认本题"}
          </button>
        </div>
      </div>
    </article>
  );
}

function ModelRecordDrawer({
  review,
  onClose,
}: {
  review: SubmissionReview;
  onClose: () => void;
}) {
  const audit = useQuery({
    queryKey: ["audit", review.submission.id],
    queryFn: () => api.getAudit(review.submission.id),
  });
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="record-drawer"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <span className="eyebrow">完整审计链</span>
            <h2>模型记录与操作历史</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={19} />
          </button>
        </div>
        <div className="record-section">
          <div className="record-meta">
            <div>
              <span>模型</span>
              <strong>{review.modelRun?.model ?? "—"}</strong>
            </div>
            <div>
              <span>调用状态</span>
              <strong>{review.modelRun?.status ?? "—"}</strong>
            </div>
            <div>
              <span>调用时间</span>
              <strong>{formatDate(review.modelRun?.startedAt)}</strong>
            </div>
          </div>
          <h3>模型原始响应</h3>
          <pre>
            {JSON.stringify(review.modelRun?.rawResponse ?? null, null, 2)}
          </pre>
        </div>
        <div className="record-section">
          <h3>教师操作记录</h3>
          {audit.isLoading ? (
            <p className="muted">正在读取审计记录…</p>
          ) : (
            <div className="audit-timeline">
              {audit.data?.map((event) => (
                <div className="audit-item" key={event.id}>
                  <span className="audit-dot" />
                  <div>
                    <strong>
                      {event.eventType === "submission.confirmed"
                        ? "确认整份试卷"
                        : event.eventType === "submission.reopened"
                          ? "修改终评并撤销确认"
                          : event.eventType === "model.completed"
                            ? "模型初评完成"
                            : event.eventType === "review.updated"
                              ? "修改逐题终评"
                              : event.eventType}
                    </strong>
                    <span>{event.actor} · {formatDate(event.createdAt)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

export function ReviewPage() {
  const { taskId = "", submissionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, ReviewDraft>>({});
  const [filter, setFilter] = useState<"all" | "attention">("all");
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [savingQuestionId, setSavingQuestionId] = useState<string | null>(
    null,
  );

  const submissions = useQuery({
    queryKey: ["submissions", taskId],
    queryFn: () => api.listSubmissions(taskId),
    enabled: Boolean(taskId),
  });
  const firstReviewable = submissions.data?.find(
    (submission) =>
      submission.status === "review_pending" ||
      submission.status === "confirmed",
  );
  const review = useQuery({
    queryKey: ["review", submissionId],
    queryFn: () => api.getReview(submissionId!),
    enabled: Boolean(submissionId),
  });

  useEffect(() => {
    if (!review.data) return;
    setDrafts(
      Object.fromEntries(
        review.data.reviews.map((item) => [
          item.questionId,
          {
            finalAnswer: item.finalAnswer,
            finalScore: item.finalScore,
            teacherComment: item.teacherComment,
            reviewStatus: item.reviewStatus,
          },
        ]),
      ),
    );
  }, [review.data]);

  const saveQuestion = useMutation({
    mutationFn: ({
      questionId,
      input,
    }: {
      questionId: string;
      input: ReviewDraft;
    }) =>
      api.updateReview(submissionId!, questionId, {
        ...input,
        reviewStatus: "reviewed",
      }),
    onMutate: ({ questionId }) => setSavingQuestionId(questionId),
    onSuccess: (data) => {
      queryClient.setQueryData(["review", submissionId], data);
      setFeedback({ type: "success", text: "本题终评已保存" });
      void queryClient.invalidateQueries({
        queryKey: ["submissions", taskId],
      });
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "保存失败",
      }),
    onSettled: () => setSavingQuestionId(null),
  });

  const confirm = useMutation({
    mutationFn: () => api.confirmSubmission(submissionId!),
    onSuccess: (data) => {
      queryClient.setQueryData(["review", submissionId], data);
      setFeedback({
        type: "success",
        text: `已确认 ${data.submission.studentName} 的最终成绩`,
      });
      void queryClient.invalidateQueries({
        queryKey: ["submissions", taskId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["statistics", taskId],
      });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "确认失败",
      }),
  });

  const unresolved = review.data?.reviews.filter(
    (item) => item.reviewStatus !== "reviewed",
  ).length ?? 0;
  const finalTotal = useMemo(
    () =>
      Object.values(drafts).reduce(
        (sum, item) =>
          sum + (Number.isFinite(item.finalScore) ? item.finalScore : 0),
        0,
      ),
    [drafts],
  );
  const visibleReviews =
    review.data?.reviews.filter(
      (item) =>
        filter === "all" || item.reviewStatus === "needs_attention",
    ) ?? [];

  if (!submissionId && firstReviewable) {
    return (
      <Navigate
        to={`/tasks/${taskId}/review/${firstReviewable.id}`}
        replace
      />
    );
  }

  if (!submissionId && submissions.isLoading) {
    return <div className="panel page-loading">正在读取待复核试卷…</div>;
  }

  if (!submissionId) {
    return (
      <EmptyState
        title="还没有待复核试卷"
        description="模型初评完成后，学生试卷会出现在这里。"
        action={
          <Link
            to={`/tasks/${taskId}/upload`}
            className="button button-primary"
          >
            前往上传与批改
          </Link>
        }
      />
    );
  }

  if (review.isLoading) {
    return <div className="panel page-loading">正在打开原卷与评分结果…</div>;
  }

  if (!review.data) {
    return (
      <EmptyState
        title="暂时无法复核这份试卷"
        description="模型可能仍在处理，或这份试卷需要重试。"
      />
    );
  }

  const data = review.data;
  return (
    <div className="review-page">
      <Feedback message={feedback} onDismiss={() => setFeedback(null)} />
      <header className="review-topbar">
        <div className="review-student-nav">
          <Link to={`/tasks/${taskId}/upload`} className="back-link">
            <ArrowLeft size={16} />
            返回队列
          </Link>
          <div className="student-selector">
            <span className="student-avatar">
              {data.submission.studentName.slice(0, 1)}
            </span>
            <div>
              <strong>{data.submission.studentName}</strong>
              <span>
                {data.task.className} · {data.task.paperName} ·{" "}
                {subjectLabel[data.task.subject]} · 答案{" "}
                {data.answerVersion
                  ? `V${data.answerVersion.versionNumber}`
                  : "版本未知"}
              </span>
            </div>
          </div>
          <StatusBadge status={data.submission.status} />
        </div>
        <div className="review-summary">
          <div>
            <span>教师终评</span>
            <strong>
              {formatScore(finalTotal)}
              <small> / {formatScore(data.task.totalScore)}</small>
            </strong>
          </div>
          <div>
            <span>复核进度</span>
            <strong>
              {data.reviews.length - unresolved}
              <small> / {data.reviews.length} 题</small>
            </strong>
          </div>
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() => setDrawerOpen(true)}
          >
            <History size={15} />
            模型记录
          </button>
        </div>
      </header>

      <div className="review-workspace">
        <PreviewPane review={data} />
        <section className="grading-pane">
          <div className="grading-pane-toolbar">
            <div>
              <button
                type="button"
                className={filter === "all" ? "active" : ""}
                onClick={() => setFilter("all")}
              >
                全部题目
                <span>{data.reviews.length}</span>
              </button>
              <button
                type="button"
                className={filter === "attention" ? "active" : ""}
                onClick={() => setFilter("attention")}
              >
                需人工判断
                <span>
                  {
                    data.reviews.filter(
                      (item) =>
                        item.reviewStatus === "needs_attention",
                    ).length
                  }
                </span>
              </button>
            </div>
            <span>
              <Sparkles size={14} />
              模型值与教师值分开保存
            </span>
          </div>
          <div className="question-review-list">
            {visibleReviews.length > 0 ? (
              visibleReviews.map((item) => {
                const itemDraft = drafts[item.questionId] ?? {
                  finalAnswer: item.finalAnswer,
                  finalScore: item.finalScore,
                  teacherComment: item.teacherComment,
                  reviewStatus: item.reviewStatus,
                };
                return (
                  <QuestionCard
                    key={item.questionId}
                    review={item}
                    draft={itemDraft}
                    onChange={(next) =>
                      setDrafts((current) => ({
                        ...current,
                        [item.questionId]: next,
                      }))
                    }
                    onSave={() =>
                      saveQuestion.mutate({
                        questionId: item.questionId,
                        input: itemDraft,
                      })
                    }
                    saving={savingQuestionId === item.questionId}
                  />
                );
              })
            ) : (
              <EmptyState
                compact
                title="没有需人工判断的题目"
                description="切换到“全部题目”继续逐题确认。"
              />
            )}
          </div>
        </section>
      </div>

      <footer className="review-actionbar">
        <div className="review-navigation">
          <button
            type="button"
            className="button button-secondary button-small"
            disabled={!data.navigation.previousId}
            onClick={() =>
              data.navigation.previousId &&
              navigate(
                `/tasks/${taskId}/review/${data.navigation.previousId}`,
              )
            }
          >
            <ArrowLeft size={15} />
            上一份
          </button>
          <button
            type="button"
            className="button button-secondary button-small"
            disabled={!data.navigation.nextId}
            onClick={() =>
              data.navigation.nextId &&
              navigate(`/tasks/${taskId}/review/${data.navigation.nextId}`)
            }
          >
            下一份
            <ArrowRight size={15} />
          </button>
        </div>
        <div className="confirm-hint">
          {unresolved > 0 ? (
            <>
              <AlertTriangle size={16} />
              还有 {unresolved} 道题未完成教师复核
            </>
          ) : (
            <>
              <CheckCircle2 size={16} />
              所有题目已复核，可以确认最终成绩
            </>
          )}
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={unresolved > 0 || confirm.isPending}
          onClick={() => confirm.mutate()}
        >
          <Save size={17} />
          {confirm.isPending
            ? "正在确认…"
            : data.submission.status === "confirmed"
              ? "重新确认整份试卷"
              : "确认整份试卷"}
        </button>
      </footer>

      {drawerOpen ? (
        <ModelRecordDrawer
          review={data}
          onClose={() => setDrawerOpen(false)}
        />
      ) : null}
    </div>
  );
}
