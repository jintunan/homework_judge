import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  CheckSquare2,
  ClipboardPenLine,
  FileUp,
  Plus,
  Sparkles,
  Users,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { EmptyState } from "@client/components/EmptyState";
import { PageHeader } from "@client/components/PageHeader";
import { StatusBadge } from "@client/components/StatusBadge";
import { api } from "@client/lib/api";
import { formatDate, formatScore } from "@client/lib/format";
import { subjectLabel } from "@shared/subject-profiles";

const workflow = [
  {
    number: "01",
    title: "配置答案",
    description: "上传试卷，Agent 提取、搜索或生成答案",
    icon: ClipboardPenLine,
  },
  {
    number: "02",
    title: "批量上传",
    description: "每个文件对应一名学生的一份完整试卷",
    icon: FileUp,
  },
  {
    number: "03",
    title: "教师复核",
    description: "原卷与模型建议并排，逐题确认或修改",
    icon: CheckSquare2,
  },
  {
    number: "04",
    title: "报告统计",
    description: "只用教师已确认成绩生成班级结论",
    icon: BarChart3,
  },
];

export function DashboardPage() {
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: api.listTasks,
  });

  const totalStudents =
    tasks.data?.reduce((sum, task) => sum + task.progress.total, 0) ?? 0;
  const confirmed =
    tasks.data?.reduce((sum, task) => sum + task.progress.confirmed, 0) ?? 0;
  const pending =
    tasks.data?.reduce(
      (sum, task) =>
        sum +
        task.progress.queued +
        task.progress.processing +
        task.progress.reviewPending,
      0,
    ) ?? 0;

  return (
    <div className="dashboard-page">
      <PageHeader
        eyebrow="教师工作台"
        title="把重复批改，变成一次可靠复核"
        description="千问视觉模型负责识别和初评，你保留每一道题的最终决定权。"
        actions={
          <Link to="/tasks/new" className="button button-primary">
            <Plus size={18} />
            创建批改任务
          </Link>
        }
      />

      <section className="hero-panel">
        <div className="hero-copy">
          <span className="hero-pill">
            <Sparkles size={14} />
            初中数学 + 高中物理 · 固定模板
          </span>
          <h2>
            模型先看一遍，
            <br />
            老师只判断关键处。
          </h2>
          <p>
            答案来源、模型初评、教师修改与最终结果全部留档。
            答案和成绩都必须经过教师确认。
          </p>
          <div className="hero-actions">
            <Link to="/tasks/new" className="button button-light">
              开始新任务
              <ArrowRight size={17} />
            </Link>
            <span className="hero-assurance">
              <CheckCircle2 size={16} />
              人在回路 · 全程可追溯
            </span>
          </div>
        </div>
        <div className="hero-metrics">
          <div className="metric-tile metric-featured">
            <span>已确认试卷</span>
            <strong>{confirmed}</strong>
            <small>教师最终结果</small>
          </div>
          <div className="metric-tile">
            <span>学生试卷</span>
            <strong>{totalStudents}</strong>
            <small>全部任务累计</small>
          </div>
          <div className="metric-tile">
            <span>待处理</span>
            <strong>{pending}</strong>
            <small>识别或复核中</small>
          </div>
        </div>
      </section>

      <section className="workflow-strip" aria-label="批改流程">
        {workflow.map((step, index) => {
          const Icon = step.icon;
          return (
            <div className="workflow-step" key={step.number}>
              <div className="workflow-icon">
                <Icon size={19} />
              </div>
              <div>
                <span>{step.number}</span>
                <strong>{step.title}</strong>
                <p>{step.description}</p>
              </div>
              {index < workflow.length - 1 ? (
                <ArrowRight className="workflow-arrow" size={17} />
              ) : null}
            </div>
          );
        })}
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <span className="eyebrow">最近任务</span>
            <h2>继续上次的批改</h2>
          </div>
          <span className="subtle-count">
            {tasks.data?.length ?? 0} 个任务
          </span>
        </div>

        {tasks.isLoading ? (
          <div className="task-grid">
            {[0, 1, 2].map((item) => (
              <div className="task-card skeleton-card" key={item} />
            ))}
          </div>
        ) : tasks.data && tasks.data.length > 0 ? (
          <div className="task-grid">
            {tasks.data.map((task) => {
              const completion =
                task.progress.total > 0
                  ? task.progress.confirmed / task.progress.total
                  : 0;
              const nextPath =
                task.answerConfigStatus !== "approved"
                  ? `/tasks/${task.id}/answers`
                  : task.progress.total === 0
                  ? `/tasks/${task.id}/upload`
                  : task.progress.reviewPending > 0
                    ? `/tasks/${task.id}/review`
                    : `/tasks/${task.id}/reports`;
              return (
                <Link className="task-card" to={nextPath} key={task.id}>
                  <div className="task-card-top">
                    <span className="subject-chip">
                      {subjectLabel(task.subject)}
                    </span>
                    <StatusBadge status={task.status} />
                  </div>
                  <h3>{task.paperName}</h3>
                  <p>{task.className} · {task.name}</p>
                  <div className="task-score-row">
                    <div>
                      <Users size={16} />
                      <span>{task.progress.total} 份试卷</span>
                    </div>
                    <strong>{formatScore(task.totalScore)} 分</strong>
                  </div>
                  <div className="progress-track">
                    <span style={{ width: `${completion * 100}%` }} />
                  </div>
                  <div className="task-card-footer">
                    <span>
                      已确认 {task.progress.confirmed}/{task.progress.total}
                    </span>
                    <span>{formatDate(task.updatedAt)} 更新</span>
                  </div>
                </Link>
              );
            })}
            <Link className="task-card task-card-new" to="/tasks/new">
              <span className="new-task-icon">
                <Plus size={24} />
              </span>
              <strong>创建新的批改任务</strong>
              <p>从固定试卷模板开始</p>
            </Link>
          </div>
        ) : (
          <EmptyState
            title="还没有批改任务"
            description="上传固定模板，让 Agent 生成答案草稿并由教师审核。"
            action={
              <Link to="/tasks/new" className="button button-primary">
                <Plus size={17} />
                创建第一个任务
              </Link>
            }
          />
        )}
      </section>
    </div>
  );
}
