import {
  ArrowRight,
  Atom,
  BookOpenCheck,
  Check,
  CircleAlert,
  FileCheck2,
  FileText,
  FlaskConical,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { AnswerMode, Subject } from "@shared/contracts";
import { subjectLabel } from "@shared/subject-profiles";
import { EmptyState } from "@client/components/EmptyState";
import { Feedback, type FeedbackMessage } from "@client/components/Feedback";
import { PageHeader } from "@client/components/PageHeader";
import { api } from "@client/lib/api";
import {
  formatBytes,
  formatDate,
  formatScore,
  questionTypeLabel,
} from "@client/lib/format";

const acceptedExtensions = [".pdf", ".jpg", ".jpeg", ".png"];
const maxBytes = 20 * 1024 * 1024;

function validateFile(file: File | null): string | null {
  if (!file) return "请选择文件";
  const extension = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!acceptedExtensions.includes(extension)) {
    return "只支持 PDF、JPG、JPEG、PNG";
  }
  if (file.size === 0) return "文件内容为空";
  if (file.size > maxBytes) return "文件不能超过 20 MB";
  return null;
}

function FilePicker({
  title,
  description,
  file,
  onChange,
  required,
}: {
  title: string;
  description: string;
  file: File | null;
  onChange: (file: File | null) => void;
  required?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className={`answer-file-picker ${file ? "has-file" : ""}`}>
      <input
        ref={input}
        type="file"
        accept=".pdf,.jpg,.jpeg,.png"
        hidden
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
      {file ? (
        <>
          <span className="answer-file-icon">
            <FileCheck2 size={22} />
          </span>
          <div>
            <small>{title}</small>
            <strong>{file.name}</strong>
            <span>{formatBytes(file.size)}</span>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={`移除${title}`}
            onClick={() => onChange(null)}
          >
            <X size={16} />
          </button>
        </>
      ) : (
        <button
          type="button"
          className="answer-file-empty"
          onClick={() => input.current?.click()}
        >
          <span className="answer-file-icon">
            <UploadCloud size={22} />
          </span>
          <span>
            <strong>
              {title}
              {required ? " *" : ""}
            </strong>
            <small>{description}</small>
          </span>
        </button>
      )}
      {file ? (
        <button
          type="button"
          className="text-action"
          onClick={() => input.current?.click()}
        >
          更换
        </button>
      ) : null}
    </div>
  );
}

export function CreateTaskPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [fields, setFields] = useState({
    name: "",
    className: "",
    paperName: "",
    subject: "middle_school_math" as Subject,
    answerMode: "reference_upload" as AnswerMode,
  });
  const [template, setTemplate] = useState<File | null>(null);
  const [referenceAnswer, setReferenceAnswer] = useState<File | null>(null);
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);

  const model = useQuery({
    queryKey: ["model-status"],
    queryFn: api.getModelStatus,
  });

  const create = useMutation({
    mutationFn: async () => {
      const task = await api.createTask(
        fields,
        template!,
        fields.answerMode === "reference_upload"
          ? referenceAnswer
          : null,
      );
      let startWarning: string | null = null;
      try {
        await api.startAnswerConfig(task.id);
      } catch (error) {
        startWarning =
          error instanceof Error ? error.message : "答案配置尚未启动";
      }
      return { task, startWarning };
    },
    onSuccess: ({ task, startWarning }) => {
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate(`/tasks/${task.id}/answers`, {
        state: startWarning ? { warning: startWarning } : undefined,
      });
    },
    onError: (error) =>
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "创建任务失败",
      }),
  });

  function submit() {
    setFeedback(null);
    if (
      !fields.name.trim() ||
      !fields.className.trim() ||
      !fields.paperName.trim()
    ) {
      setFeedback({ type: "error", text: "请完整填写任务、班级和试卷名称" });
      return;
    }
    const templateError = validateFile(template);
    if (templateError) {
      setFeedback({ type: "error", text: `模板试卷：${templateError}` });
      return;
    }
    if (fields.answerMode === "reference_upload") {
      const referenceError = validateFile(referenceAnswer);
      if (referenceError) {
        setFeedback({ type: "error", text: `参考答案：${referenceError}` });
        return;
      }
    }
    create.mutate();
  }

  const physics = fields.subject === "high_school_physics";

  return (
    <div>
      <PageHeader
        eyebrow="创建批改任务"
        title="上传试卷，让 Agent 配置答案"
        description="可上传教师参考答案；没有参考答案时，Agent 会先联网检索，找不到可靠答案再由模型生成。"
      />
      <Feedback message={feedback} onDismiss={() => setFeedback(null)} />

      <div className="answer-create-layout">
        <section className="panel answer-create-form">
          <div className="answer-create-section">
            <div className="answer-section-number">1</div>
            <div className="answer-section-content">
              <div className="card-title-row">
                <div>
                  <span className="eyebrow">任务信息</span>
                  <h2>选择科目与班级</h2>
                </div>
              </div>
              <div className="subject-choice-grid">
                <button
                  type="button"
                  className={`subject-choice ${
                    !physics ? "selected" : ""
                  }`}
                  onClick={() =>
                    setFields((current) => ({
                      ...current,
                      subject: "middle_school_math",
                    }))
                  }
                >
                  <span><BookOpenCheck size={22} /></span>
                  <div>
                    <strong>初中数学</strong>
                    <small>选择题 · 填空题 · 简单简答题</small>
                  </div>
                  {!physics ? <Check size={18} /> : null}
                </button>
                <button
                  type="button"
                  className={`subject-choice ${physics ? "selected" : ""}`}
                  onClick={() =>
                    setFields((current) => ({
                      ...current,
                      subject: "high_school_physics",
                    }))
                  }
                >
                  <span><Atom size={22} /></span>
                  <div>
                    <strong>高中物理</strong>
                    <small>选择题 · 填空题 · 计算题</small>
                  </div>
                  {physics ? <Check size={18} /> : null}
                </button>
              </div>
              <div className="form-grid three">
                <label>
                  <span>任务名称</span>
                  <input
                    value={fields.name}
                    placeholder={physics ? "如：高一力学周测" : "如：七年级期中批改"}
                    onChange={(event) =>
                      setFields((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  <span>班级</span>
                  <input
                    value={fields.className}
                    placeholder={physics ? "高一 2 班" : "七年级 3 班"}
                    onChange={(event) =>
                      setFields((current) => ({
                        ...current,
                        className: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  <span>试卷名称</span>
                  <input
                    value={fields.paperName}
                    placeholder={physics ? "匀变速直线运动检测" : "一元一次方程检测"}
                    onChange={(event) =>
                      setFields((current) => ({
                        ...current,
                        paperName: event.target.value,
                      }))
                    }
                  />
                </label>
              </div>
            </div>
          </div>

          <div className="answer-create-section">
            <div className="answer-section-number">2</div>
            <div className="answer-section-content">
              <span className="eyebrow">固定模板</span>
              <h2>上传教师使用的试卷</h2>
              <p className="section-description">
                Agent 将从试卷中识别题号、题干、题型和分值。
              </p>
              <FilePicker
                title="模板试卷"
                description="PDF、JPG、JPEG 或 PNG，最大 20 MB"
                file={template}
                onChange={setTemplate}
                required
              />
            </div>
          </div>

          <div className="answer-create-section">
            <div className="answer-section-number">3</div>
            <div className="answer-section-content">
              <span className="eyebrow">答案方式</span>
              <h2>Agent 从哪里获取答案？</h2>
              <div className="answer-mode-grid">
                <button
                  type="button"
                  className={`answer-mode-card ${
                    fields.answerMode === "reference_upload"
                      ? "selected"
                      : ""
                  }`}
                  onClick={() =>
                    setFields((current) => ({
                      ...current,
                      answerMode: "reference_upload",
                    }))
                  }
                >
                  <span className="mode-icon"><FileText size={22} /></span>
                  <strong>上传参考答案</strong>
                  <small>从教师答案中提取并按题号匹配，适合已有标准答案的试卷。</small>
                </button>
                <button
                  type="button"
                  className={`answer-mode-card ${
                    fields.answerMode === "agent_search"
                      ? "selected"
                      : ""
                  }`}
                  onClick={() => {
                    setReferenceAnswer(null);
                    setFields((current) => ({
                      ...current,
                      answerMode: "agent_search",
                    }));
                  }}
                >
                  <span className="mode-icon"><Search size={22} /></span>
                  <strong>没有参考答案</strong>
                  <small>逐题联网检索；没有可靠来源时由模型独立生成答案。</small>
                </button>
              </div>
              {fields.answerMode === "reference_upload" ? (
                <FilePicker
                  title="参考答案"
                  description="支持答案页、解析页或教师版试卷"
                  file={referenceAnswer}
                  onChange={setReferenceAnswer}
                  required
                />
              ) : (
                <div className="search-privacy-note">
                  <ShieldCheck size={18} />
                  <div>
                    <strong>仅将科目和公开题干用于搜索</strong>
                    <span>不会发送教师、班级、学生姓名或学生答卷。</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="answer-create-actions">
            <Link to="/" className="button button-ghost">取消</Link>
            <button
              type="button"
              className="button button-primary"
              onClick={submit}
              disabled={create.isPending}
            >
              <Sparkles size={17} />
              {create.isPending ? "正在创建并启动…" : "创建并让 Agent 配置答案"}
              <ArrowRight size={17} />
            </button>
          </div>
        </section>

        <aside className="answer-create-aside">
          <section className="panel agent-flow-card">
            <span className="eyebrow">处理流程</span>
            <h3>自动生成，教师发布</h3>
            {[
              "识别试卷题目",
              fields.answerMode === "reference_upload"
                ? "解析并匹配参考答案"
                : "联网检索公开答案",
              "必要时由模型生成",
              "教师逐题审核",
              "发布后用于学生批改",
            ].map((item, index) => (
              <div className="agent-flow-step" key={item}>
                <span>{index + 1}</span>
                <strong>{item}</strong>
              </div>
            ))}
          </section>
          <section className="panel model-ready-card">
            <span
              className={`connection-dot ${
                model.data?.configured ? "online" : "offline"
              }`}
            />
            <div>
              <strong>
                {model.data?.configured ? "百炼模型已配置" : "百炼模型未配置"}
              </strong>
              <small>
                {model.data?.configured
                  ? `${model.data.model} · 可启动真实处理`
                  : "任务仍可创建，配置 Key 后在审核页重新启动"}
              </small>
            </div>
          </section>
          <section className="scope-note">
            <CircleAlert size={17} />
            <p>
              检索答案和模型生成内容都是草稿。只有教师审核发布后的版本，
              才能用于学生试卷批改。
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

export function TaskSetupPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  });
  const revise = useMutation({
    mutationFn: () => api.reviseAnswerConfig(taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["task", taskId] });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate(`/tasks/${taskId}/answers`);
    },
  });

  if (task.isLoading) {
    return <div className="panel page-loading">正在读取任务配置…</div>;
  }
  if (!task.data) {
    return (
      <EmptyState
        title="任务不存在"
        description="请返回任务总览重新选择。"
      />
    );
  }
  const data = task.data;
  return (
    <div>
      <PageHeader
        eyebrow={`${subjectLabel(data.subject)} · 任务配置`}
        title={data.paperName}
        description={`${data.className} · ${data.questionCount} 道题 · ${formatScore(data.totalScore)} 分`}
        actions={
          <Link
            to={`/tasks/${taskId}/answers`}
            className="button button-primary"
          >
            <Sparkles size={17} />
            答案审核
          </Link>
        }
      />
      <div className="config-summary-grid">
        <section className="panel template-summary-card">
          <div className="card-title-row">
            <div>
              <span className="eyebrow">任务文件</span>
              <h2>{data.templateFile?.originalName}</h2>
            </div>
            <a
              className="button button-secondary button-small"
              href={data.templateFile?.previewUrl}
              target="_blank"
              rel="noreferrer"
            >
              查看试卷
            </a>
          </div>
          {data.referenceAnswerFile ? (
            <a
              className="reference-file-link"
              href={data.referenceAnswerFile.previewUrl}
              target="_blank"
              rel="noreferrer"
            >
              <FileCheck2 size={17} />
              <span>
                <small>参考答案</small>
                <strong>{data.referenceAnswerFile.originalName}</strong>
              </span>
            </a>
          ) : (
            <div className="reference-file-link">
              <Search size={17} />
              <span>
                <small>答案方式</small>
                <strong>联网检索，失败后模型生成</strong>
              </span>
            </div>
          )}
        </section>
        <section className="panel task-mini-stats">
          <div>
            <span>答案状态</span>
            <strong>
              {data.answerConfigStatus === "approved" ? "已发布" : "待审核"}
            </strong>
          </div>
          <div>
            <span>答案版本</span>
            <strong>
              {data.activeAnswerVersion
                ? `V${data.activeAnswerVersion.versionNumber}`
                : "—"}
            </strong>
          </div>
          <div>
            <span>批准时间</span>
            <strong className="small-stat">
              {data.activeAnswerVersion?.approvedAt
                ? formatDate(data.activeAnswerVersion.approvedAt)
                : "尚未发布"}
            </strong>
          </div>
        </section>
      </div>

      <section className="panel configured-questions">
        <div className="card-title-row">
          <div>
            <span className="eyebrow">当前正式版本</span>
            <h2>教师已审核答案与评分点</h2>
          </div>
          {data.activeAnswerVersion ? (
            <button
              type="button"
              className="button button-secondary button-small"
              disabled={revise.isPending}
              onClick={() => revise.mutate()}
            >
              <RefreshCw size={15} />
              创建修订版本
            </button>
          ) : (
            <Link
              to={`/tasks/${taskId}/answers`}
              className="button button-primary button-small"
            >
              前往审核
            </Link>
          )}
        </div>
        {data.questions.length === 0 ? (
          <EmptyState
            compact
            title="尚未发布答案"
            description="完成 Agent 处理和教师逐题审核后，正式答案会显示在这里。"
          />
        ) : (
          <div className="configured-table">
            <div className="configured-row configured-head">
              <span>题号</span>
              <span>题型</span>
              <span>标准答案</span>
              <span>评分点</span>
              <span>满分</span>
            </div>
            {data.questions.map((question) => (
              <div className="configured-row" key={question.id}>
                <strong>{question.number}</strong>
                <span>{questionTypeLabel[question.type]}</span>
                <span className="answer-cell">{question.standardAnswer}</span>
                <span className="point-cell">
                  {question.scoringPoints.length > 0
                    ? question.scoringPoints
                        .map(
                          (point) =>
                            `${point.description}（${point.score}分）`,
                        )
                        .join("；")
                    : "按答案判定"}
                </span>
                <strong>{formatScore(question.maxScore)}</strong>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
