import {
  AlertCircle,
  ArrowRight,
  Award,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  FileText,
  Gauge,
  Medal,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import type {
  ClassStatistics,
  StudentReport,
} from "@shared/contracts";
import { EmptyState } from "@client/components/EmptyState";
import { PageHeader } from "@client/components/PageHeader";
import { StatusBadge } from "@client/components/StatusBadge";
import { api } from "@client/lib/api";
import {
  formatDate,
  formatScore,
  percent,
  questionTypeLabel,
  subjectLabel,
} from "@client/lib/format";

function MetricCard({
  icon: Icon,
  label,
  value,
  note,
  tone,
}: {
  icon: typeof Users;
  label: string;
  value: string;
  note: string;
  tone: string;
}) {
  return (
    <article className={`report-metric report-metric-${tone}`}>
      <span className="report-metric-icon">
        <Icon size={19} />
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </article>
  );
}

function ScoreBands({
  statistics,
}: {
  statistics: ClassStatistics;
}) {
  const maxCount = Math.max(
    1,
    ...statistics.scoreBands.map((band) => band.count),
  );
  return (
    <div className="score-bands">
      {statistics.scoreBands.map((band, index) => (
        <div className="score-band-row" key={band.label}>
          <span>{band.label}</span>
          <div className="score-band-track">
            <i
              className={`band-${index}`}
              style={{ width: `${(band.count / maxCount) * 100}%` }}
            />
          </div>
          <strong>{band.count} 人</strong>
        </div>
      ))}
    </div>
  );
}

function QuestionRates({
  statistics,
}: {
  statistics: ClassStatistics;
}) {
  return (
    <div className="question-rates">
      {statistics.questions.map((question) => (
        <div className="question-rate" key={question.questionId}>
          <div>
          <span>
            V{question.answerVersionNumber} · 第 {question.number} 题
          </span>
            <strong>{percent(question.scoreRate)}</strong>
          </div>
          <div className="rate-track">
            <i
              className={
                question.scoreRate < 0.6
                  ? "rate-low"
                  : question.scoreRate < 0.8
                    ? "rate-mid"
                    : "rate-high"
              }
              style={{ width: `${question.scoreRate * 100}%` }}
            />
          </div>
          <small>
            均分 {formatScore(question.averageScore)} /{" "}
            {formatScore(question.maxScore)}
          </small>
        </div>
      ))}
    </div>
  );
}

function StudentReportView({
  report,
}: {
  report: StudentReport;
}) {
  return (
    <div className="student-report-view">
      <div className={`student-report-hero ${report.isFinal ? "final" : ""}`}>
        <div className="student-report-identity">
          <span className="student-report-avatar">
            {report.submission.studentName.slice(0, 1)}
          </span>
          <div>
            <span className="eyebrow">学生报告</span>
            <h2>{report.submission.studentName}</h2>
            <p>
              {report.task.className} · {report.task.paperName} ·{" "}
              {subjectLabel[report.task.subject]} · 答案{" "}
              {report.answerVersion
                ? `V${report.answerVersion.versionNumber}`
                : "版本未知"}
            </p>
          </div>
        </div>
        <div className="student-report-score">
          {report.isFinal ? (
            <>
              <span>教师确认最终成绩</span>
              <strong>{formatScore(report.totalScore)}</strong>
              <small>/ {formatScore(report.maxScore)} 分</small>
            </>
          ) : (
            <>
              <span className="pending-final">
                <AlertCircle size={16} />
                待教师确认
              </span>
              <strong>—</strong>
              <small>模型初评分不作为最终成绩</small>
            </>
          )}
        </div>
      </div>

      <div className="student-report-meta">
        <div>
          <span>复核状态</span>
          <StatusBadge status={report.submission.status} />
        </div>
        <div>
          <span>确认教师</span>
          <strong>{report.submission.confirmedBy ?? "尚未确认"}</strong>
        </div>
        <div>
          <span>确认时间</span>
          <strong>{formatDate(report.submission.confirmedAt)}</strong>
        </div>
        <div>
          <span>模型建议分</span>
          <strong>
            {formatScore(report.submission.modelTotalScore)} 分
          </strong>
        </div>
      </div>

      <div className="report-question-list">
        <div className="card-title-row">
          <div>
            <span className="eyebrow">逐题结果</span>
            <h2>得分与教师反馈</h2>
          </div>
        </div>
        {report.reviews.map((review) => (
          <article className="report-question" key={review.questionId}>
            <div className="report-question-number">
              <span>{review.questionNumber}</span>
            </div>
            <div className="report-question-main">
              <div className="report-question-title">
                <strong>{questionTypeLabel[review.questionType]}</strong>
                <span>标准答案：{review.standardAnswer}</span>
              </div>
              <div className="report-answer-grid">
                <div>
                  <span>学生答案</span>
                  <p>{review.finalAnswer || "未作答"}</p>
                </div>
                <div>
                  <span>教师批注</span>
                  <p>{review.teacherComment || "暂无批注"}</p>
                </div>
              </div>
            </div>
            <div className="report-question-score">
              <strong>{formatScore(review.finalScore)}</strong>
              <span>/ {formatScore(review.maxScore)}</span>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

export function ReportsPage() {
  const { taskId = "" } = useParams();
  const [tab, setTab] = useState<"class" | "student">("class");
  const [selectedStudentId, setSelectedStudentId] = useState<string>("");

  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  });
  const statistics = useQuery({
    queryKey: ["statistics", taskId],
    queryFn: () => api.getStatistics(taskId),
    enabled: Boolean(taskId),
  });
  const submissions = useQuery({
    queryKey: ["submissions", taskId],
    queryFn: () => api.listSubmissions(taskId),
    enabled: Boolean(taskId),
  });
  const report = useQuery({
    queryKey: ["student-report", selectedStudentId],
    queryFn: () => api.getStudentReport(selectedStudentId),
    enabled: Boolean(selectedStudentId),
  });

  useEffect(() => {
    if (!selectedStudentId && submissions.data?.[0]) {
      setSelectedStudentId(submissions.data[0].id);
    }
  }, [selectedStudentId, submissions.data]);

  const stats = statistics.data;
  return (
    <div>
      <PageHeader
        eyebrow="报告与统计"
        title={task.data?.paperName ?? "班级成绩分析"}
        description={
          task.data
            ? `${task.data.className} · ${subjectLabel[task.data.subject]} · 答案 ${task.data.activeAnswerVersion ? `V${task.data.activeAnswerVersion.versionNumber}` : "未发布"} · 统计只包含教师已确认的最终成绩`
            : "正在读取任务…"
        }
        actions={
          <Link
            to={`/tasks/${taskId}/review`}
            className="button button-secondary"
          >
            返回复核工作台
            <ArrowRight size={17} />
          </Link>
        }
      />

      <div className="report-tabs">
        <button
          type="button"
          className={tab === "class" ? "active" : ""}
          onClick={() => setTab("class")}
        >
          <BarChart3 size={17} />
          班级概览
        </button>
        <button
          type="button"
          className={tab === "student" ? "active" : ""}
          onClick={() => setTab("student")}
        >
          <FileText size={17} />
          学生报告
        </button>
        <span className="report-rule">
          <CheckCircle2 size={15} />
          仅聚合已确认结果
        </span>
      </div>

      {tab === "class" ? (
        statistics.isLoading ? (
          <div className="panel page-loading">正在计算班级统计…</div>
        ) : !stats ? (
          <EmptyState
            title="统计数据暂不可用"
            description="请稍后重试。"
          />
        ) : (
          <>
            <section className="report-metric-grid">
              <MetricCard
                icon={Users}
                label="已上传学生"
                value={String(stats.progress.total)}
                note={`${stats.confirmedCount} 人已确认`}
                tone="blue"
              />
              <MetricCard
                icon={Gauge}
                label="班级平均分"
                value={
                  stats.averageScore === null
                    ? "—"
                    : formatScore(stats.averageScore)
                }
                note={`试卷满分 ${formatScore(stats.totalScore)}`}
                tone="teal"
              />
              <MetricCard
                icon={Award}
                label="最高分"
                value={formatScore(stats.highestScore)}
                note="仅教师确认成绩"
                tone="amber"
              />
              <MetricCard
                icon={TrendingUp}
                label="待复核"
                value={String(stats.progress.reviewPending)}
                note={`${stats.progress.failed} 份处理失败`}
                tone="purple"
              />
            </section>

            {stats.confirmedCount === 0 ? (
              <section className="panel statistics-empty">
                <span className="statistics-empty-icon">
                  <BarChart3 size={28} />
                </span>
                <h2>等待第一份教师确认成绩</h2>
                <p>
                  模型初评不会直接进入统计。完成逐题复核并确认整卷后，
                  这里会自动生成班级分析。
                </p>
                <Link
                  to={`/tasks/${taskId}/review`}
                  className="button button-primary"
                >
                  去复核试卷
                </Link>
              </section>
            ) : (
              <div className="statistics-grid">
                <section className="panel chart-card">
                  <div className="card-title-row">
                    <div>
                      <span className="eyebrow">成绩分布</span>
                      <h2>班级分数段</h2>
                    </div>
                    <span className="chart-note">
                      {stats.confirmedCount} 份确认成绩
                    </span>
                  </div>
                  <ScoreBands statistics={stats} />
                </section>
                <section className="panel chart-card">
                  <div className="card-title-row">
                    <div>
                      <span className="eyebrow">题目洞察</span>
                      <h2>逐题得分率</h2>
                    </div>
                    <span className="chart-note">得分率低于 60% 需关注</span>
                  </div>
                  <QuestionRates statistics={stats} />
                </section>
              </div>
            )}

            <section className="panel class-roster">
              <div className="card-title-row">
                <div>
                  <span className="eyebrow">学生明细</span>
                  <h2>成绩确认进度</h2>
                </div>
                <span className="completion-note">
                  {stats.confirmedCount}/{stats.progress.total} 已确认
                </span>
              </div>
              <div className="roster-table">
                <div className="roster-row roster-head">
                  <span>学生</span>
                  <span>状态</span>
                  <span>最终成绩</span>
                  <span>确认时间</span>
                  <span />
                </div>
                {stats.students.map((student) => (
                  <div className="roster-row" key={student.submissionId}>
                    <strong>{student.studentName}</strong>
                    <StatusBadge status={student.status} />
                    <span className="roster-score">
                      {student.score === null
                        ? "—"
                        : `${formatScore(student.score)} 分`}
                    </span>
                    <span>
                      {formatDate(student.confirmedAt)}
                      {student.answerVersionNumber
                        ? ` · V${student.answerVersionNumber}`
                        : ""}
                    </span>
                    <button
                      type="button"
                      className="text-action primary"
                      onClick={() => {
                        setSelectedStudentId(student.submissionId);
                        setTab("student");
                      }}
                    >
                      查看报告
                      <ArrowRight size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          </>
        )
      ) : (
        <div className="student-report-layout">
          <aside className="panel student-list-panel">
            <div className="student-list-heading">
              <span className="eyebrow">选择学生</span>
              <h2>{submissions.data?.length ?? 0} 份试卷</h2>
            </div>
            <label className="mobile-student-select">
              <select
                value={selectedStudentId}
                onChange={(event) =>
                  setSelectedStudentId(event.target.value)
                }
              >
                {submissions.data?.map((submission) => (
                  <option value={submission.id} key={submission.id}>
                    {submission.studentName}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} />
            </label>
            <div className="student-report-list">
              {submissions.data?.map((submission) => (
                <button
                  type="button"
                  key={submission.id}
                  className={
                    submission.id === selectedStudentId ? "active" : ""
                  }
                  onClick={() => setSelectedStudentId(submission.id)}
                >
                  <span className="mini-avatar">
                    {submission.studentName.slice(0, 1)}
                  </span>
                  <div>
                    <strong>{submission.studentName}</strong>
                    <span>
                      {submission.status === "confirmed"
                        ? `${formatScore(submission.finalTotalScore)} 分`
                        : "待教师确认"}
                    </span>
                  </div>
                  {submission.status === "confirmed" ? (
                    <CheckCircle2 size={15} />
                  ) : (
                    <AlertCircle size={15} />
                  )}
                </button>
              ))}
            </div>
          </aside>
          <section className="panel student-report-panel">
            {!selectedStudentId ? (
              <EmptyState
                title="还没有学生试卷"
                description="上传并批改后，可以在这里查看学生报告。"
              />
            ) : report.isLoading ? (
              <div className="page-loading">正在生成学生报告…</div>
            ) : report.data ? (
              <StudentReportView report={report.data} />
            ) : (
              <EmptyState
                title="学生报告暂不可用"
                description="该试卷可能尚未完成模型初评。"
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
