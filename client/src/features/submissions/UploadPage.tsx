import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  FileImage,
  FileText,
  Pencil,
  Play,
  RefreshCw,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import {
  useRef,
  useState,
  type DragEvent,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import type { Submission } from "@shared/contracts";
import { EmptyState } from "@client/components/EmptyState";
import { Feedback, type FeedbackMessage } from "@client/components/Feedback";
import { PageHeader } from "@client/components/PageHeader";
import { StatusBadge } from "@client/components/StatusBadge";
import { api } from "@client/lib/api";
import {
  formatBytes,
  formatDate,
  formatScore,
} from "@client/lib/format";

const acceptedExtensions = [".pdf", ".jpg", ".jpeg", ".png"];
const maxBytes = 20 * 1024 * 1024;

interface LocalFile {
  id: string;
  file: File;
  error: string | null;
}

function validateFile(file: File): string | null {
  const extension = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!acceptedExtensions.includes(extension)) {
    return "只支持 PDF、JPG、JPEG、PNG";
  }
  if (file.size > maxBytes) return "文件超过 20 MB";
  if (file.size === 0) return "文件内容为空";
  return null;
}

function EditableStudentName({
  submission,
  onSaved,
}: {
  submission: Submission;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(submission.studentName);
  const update = useMutation({
    mutationFn: () => api.updateStudentName(submission.id, value),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });
  if (editing) {
    return (
      <div className="name-editor">
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          autoFocus
          onKeyDown={(event) => {
            if (event.key === "Enter" && value.trim()) update.mutate();
            if (event.key === "Escape") setEditing(false);
          }}
        />
        <button
          type="button"
          onClick={() => update.mutate()}
          disabled={!value.trim() || update.isPending}
        >
          保存
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      className={`student-name-button ${
        submission.studentNameNeedsReview ? "needs-review" : ""
      }`}
      onClick={() => setEditing(true)}
    >
      <span>{submission.studentName}</span>
      <Pencil size={13} />
    </button>
  );
}

export function UploadPage() {
  const { taskId = "" } = useParams();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localFiles, setLocalFiles] = useState<LocalFile[]>([]);
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);

  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  });
  const model = useQuery({
    queryKey: ["model-status"],
    queryFn: api.getModelStatus,
  });
  const submissions = useQuery({
    queryKey: ["submissions", taskId],
    queryFn: () => api.listSubmissions(taskId),
    enabled: Boolean(taskId),
    refetchInterval: 2500,
  });

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadSubmissions(taskId, files),
    onSuccess: (result) => {
      const failed = result.results.filter((item) => !item.ok);
      setLocalFiles([]);
      setFeedback({
        type: failed.length > 0 ? "error" : "success",
        text:
          failed.length > 0
            ? `已上传 ${result.results.length - failed.length} 份，${failed.length} 份失败`
            : `成功上传 ${result.results.length} 份学生试卷`,
      });
      void queryClient.invalidateQueries({
        queryKey: ["submissions", taskId],
      });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "上传失败",
      }),
  });

  const grading = useMutation({
    mutationFn: () => api.startGrading(taskId),
    onSuccess: (result) => {
      setFeedback({
        type: "success",
        text:
          result.queued > 0
            ? `已将 ${result.queued} 份试卷加入模型批改队列`
            : "没有可处理的新试卷",
      });
      void submissions.refetch();
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "启动批改失败",
      }),
  });

  const retry = useMutation({
    mutationFn: (submissionId: string) =>
      api.retrySubmission(submissionId),
    onSuccess: () => {
      setFeedback({ type: "success", text: "已重新加入批改队列" });
      void submissions.refetch();
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "重试失败",
      }),
  });

  function addFiles(files: File[]) {
    setLocalFiles((current) => {
      const existing = new Set(
        current.map(
          (item) =>
            `${item.file.name}-${item.file.size}-${item.file.lastModified}`,
        ),
      );
      const additions = files
        .filter(
          (file) =>
            !existing.has(
              `${file.name}-${file.size}-${file.lastModified}`,
            ),
        )
        .slice(0, 50 - current.length)
        .map((file) => ({
          id: crypto.randomUUID(),
          file,
          error: validateFile(file),
        }));
      return [...current, ...additions];
    });
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
  }

  const data = submissions.data ?? [];
  const counts = {
    queued: data.filter((item) => item.status === "queued").length,
    processing: data.filter((item) => item.status === "processing").length,
    review: data.filter((item) => item.status === "review_pending").length,
    confirmed: data.filter((item) => item.status === "confirmed").length,
    failed: data.filter((item) => item.status === "failed").length,
  };
  const firstReviewable = data.find(
    (item) =>
      item.status === "review_pending" || item.status === "confirmed",
  );
  const validLocalFiles = localFiles.filter((item) => !item.error);

  if (
    task.data &&
    (task.data.answerConfigStatus !== "approved" ||
      !task.data.activeAnswerVersion)
  ) {
    return (
      <div>
        <PageHeader
          eyebrow="批量上传"
          title={task.data.paperName}
          description={`${task.data.className} · 学生试卷暂未开放上传`}
        />
        <section className="panel answer-upload-blocked">
          <span className="launch-icon">
            <AlertCircle size={24} />
          </span>
          <div>
            <span className="eyebrow">服务端准入保护</span>
            <h2>请先审核并发布答案配置</h2>
            <p>
              Agent 自动提取、检索或生成的答案只是草稿。所有题目经教师审核并发布后，
              才能上传学生试卷和启动批改。
            </p>
          </div>
          <Link
            to={`/tasks/${taskId}/answers`}
            className="button button-primary"
          >
            前往答案审核
            <ArrowRight size={17} />
          </Link>
        </section>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow="批量上传"
        title={task.data?.paperName ?? "学生试卷"}
        description={
          task.data
            ? `${task.data.className} · 答案 V${task.data.activeAnswerVersion?.versionNumber ?? "—"} · ${task.data.questionCount} 道题`
            : "正在读取任务…"
        }
        actions={
          firstReviewable ? (
            <Link
              to={`/tasks/${taskId}/review/${firstReviewable.id}`}
              className="button button-secondary"
            >
              进入复核工作台
              <ArrowRight size={17} />
            </Link>
          ) : null
        }
      />
      <Feedback message={feedback} onDismiss={() => setFeedback(null)} />

      <section className="upload-overview">
        <div>
          <span className="overview-value">{data.length}</span>
          <span className="overview-label">已上传</span>
        </div>
        <div>
          <span className="overview-value">{counts.processing}</span>
          <span className="overview-label">识别中</span>
        </div>
        <div>
          <span className="overview-value accent-amber">{counts.review}</span>
          <span className="overview-label">待复核</span>
        </div>
        <div>
          <span className="overview-value accent-green">{counts.confirmed}</span>
          <span className="overview-label">已确认</span>
        </div>
        <div className="upload-overview-model">
          <span
            className={`connection-dot ${
              model.data?.configured ? "online" : "offline"
            }`}
          />
          <div>
            <strong>
              {model.data?.configured ? "千问模型已连接" : "模型未配置"}
            </strong>
            <span>
              {model.data?.configured
                ? `${model.data.model} · ${model.data.regionHint}`
                : "请在服务端设置 DASHSCOPE_API_KEY"}
            </span>
          </div>
        </div>
      </section>

      <div className="upload-layout">
        <section className="panel upload-panel">
          <div className="card-title-row">
            <div>
              <span className="eyebrow">添加试卷</span>
              <h2>批量上传学生文件</h2>
            </div>
            <span className="file-limit">最多 50 份 / 次</span>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            multiple
            hidden
            onChange={(event) =>
              addFiles(Array.from(event.target.files ?? []))
            }
          />
          <div
            className={`batch-dropzone ${dragging ? "dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <span className="upload-orbit">
              <UploadCloud size={28} />
            </span>
            <h3>拖入学生试卷</h3>
            <p>一个文件对应一名学生，文件名建议包含姓名</p>
            <button
              type="button"
              className="button button-secondary button-small"
              onClick={() => fileInput.current?.click()}
            >
              选择多个文件
            </button>
          </div>

          {localFiles.length > 0 ? (
            <div className="local-file-list">
              <div className="local-file-list-heading">
                <strong>待上传 · {localFiles.length} 份</strong>
                <button type="button" onClick={() => setLocalFiles([])}>
                  清空
                </button>
              </div>
              {localFiles.map((item) => {
                const Icon = item.file.type === "application/pdf"
                  ? FileText
                  : FileImage;
                return (
                  <div
                    className={`local-file-row ${item.error ? "has-error" : ""}`}
                    key={item.id}
                  >
                    <span className="file-mini-icon">
                      <Icon size={17} />
                    </span>
                    <div>
                      <strong>{item.file.name}</strong>
                      <span>
                        {item.error ?? formatBytes(item.file.size)}
                      </span>
                    </div>
                    {item.error ? (
                      <AlertCircle size={17} />
                    ) : (
                      <CheckCircle2 size={17} />
                    )}
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() =>
                        setLocalFiles((current) =>
                          current.filter((file) => file.id !== item.id),
                        )
                      }
                      aria-label="移除文件"
                    >
                      <X size={15} />
                    </button>
                  </div>
                );
              })}
              <button
                type="button"
                className="button button-primary upload-selected-button"
                disabled={
                  validLocalFiles.length === 0 || upload.isPending
                }
                onClick={() =>
                  upload.mutate(validLocalFiles.map((item) => item.file))
                }
              >
                <UploadCloud size={17} />
                {upload.isPending
                  ? "正在上传…"
                  : `上传 ${validLocalFiles.length} 份有效试卷`}
              </button>
            </div>
          ) : null}
        </section>

        <aside className="panel grading-launch-card">
          <span className="launch-icon">
            <Sparkles size={22} />
          </span>
          <div className="eyebrow">模型初评</div>
          <h2>准备好后，统一发起识别</h2>
          <p>
            每次最多同时处理 2 份试卷。处理期间可以继续浏览页面，
            失败的文件不会影响其他学生。
          </p>
          <div className="launch-summary">
            <div>
              <span>等待批改</span>
              <strong>{counts.queued}</strong>
            </div>
            <div>
              <span>处理失败</span>
              <strong>{counts.failed}</strong>
            </div>
          </div>
          <button
            type="button"
            className="button button-primary button-full"
            disabled={
              !model.data?.configured ||
              counts.queued + counts.failed === 0 ||
              grading.isPending
            }
            onClick={() => grading.mutate()}
          >
            <Play size={17} />
            {grading.isPending ? "正在加入队列…" : "开始模型批改"}
          </button>
          {!model.data?.configured ? (
            <div className="launch-warning">
              <AlertCircle size={15} />
              需要服务端 API Key 才能发起真实模型调用
            </div>
          ) : null}
        </aside>
      </div>

      <section className="panel submission-list-panel">
        <div className="card-title-row">
          <div>
            <span className="eyebrow">处理队列</span>
            <h2>学生试卷</h2>
          </div>
          <button
            type="button"
            className="button button-ghost button-small"
            onClick={() => submissions.refetch()}
          >
            <RefreshCw size={15} />
            刷新状态
          </button>
        </div>
        {submissions.isLoading ? (
          <div className="table-loading">正在读取学生试卷…</div>
        ) : data.length === 0 ? (
          <EmptyState
            compact
            title="还没有学生试卷"
            description="先在上方批量上传，学生记录会显示在这里。"
          />
        ) : (
          <div className="submission-table">
            <div className="submission-row submission-head">
              <span>学生</span>
              <span>文件</span>
              <span>状态</span>
              <span>模型分</span>
              <span>更新时间</span>
              <span>操作</span>
            </div>
            {data.map((submission) => (
              <div className="submission-row" key={submission.id}>
                <div>
                  <EditableStudentName
                    submission={submission}
                    onSaved={() =>
                      void queryClient.invalidateQueries({
                        queryKey: ["submissions", taskId],
                      })
                    }
                  />
                  {submission.studentNameNeedsReview ? (
                    <small className="needs-name-note">请补充姓名</small>
                  ) : null}
                </div>
                <div className="submission-file">
                  <span className="file-mini-icon">
                    {submission.mimeType === "application/pdf" ? (
                      <FileText size={16} />
                    ) : (
                      <FileImage size={16} />
                    )}
                  </span>
                  <div>
                    <a
                      href={submission.previewUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {submission.originalName}
                    </a>
                    <small>{formatBytes(submission.fileSize)}</small>
                  </div>
                </div>
                <div>
                  <StatusBadge status={submission.status} />
                  {submission.errorMessage ? (
                    <small className="row-error">
                      {submission.errorMessage}
                    </small>
                  ) : null}
                </div>
                <strong className="numeric-cell">
                  {formatScore(submission.modelTotalScore)}
                </strong>
                <span>{formatDate(submission.updatedAt)}</span>
                <div className="row-actions">
                  {submission.status === "failed" ? (
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => retry.mutate(submission.id)}
                    >
                      <RefreshCw size={14} />
                      重试
                    </button>
                  ) : null}
                  {submission.status === "review_pending" ||
                  submission.status === "confirmed" ? (
                    <Link
                      className="text-action primary"
                      to={`/tasks/${taskId}/review/${submission.id}`}
                    >
                      {submission.status === "confirmed"
                        ? "查看"
                        : "去复核"}
                      <ArrowRight size={14} />
                    </Link>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
