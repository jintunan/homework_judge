import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  History,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  AnswerQuestionDraft,
  QuestionType,
  ScoringPoint,
  Subject,
} from "@shared/contracts";
import { getSubjectProfile } from "@shared/subject-profiles";
import { formatScore, percent, questionTypeLabel } from "@client/lib/format";
import { SourceEvidence } from "./SourceEvidence";

export interface AnswerDraftValues {
  number: string;
  type: QuestionType;
  maxScore: number;
  standardAnswer: string;
  scoringPoints: ScoringPoint[];
}

const sourceLabels = {
  reference_extracted: "参考答案提取",
  web_searched: "联网检索",
  model_generated: "模型生成",
};

export function AnswerDraftCard({
  draft,
  subject,
  readOnly,
  busy,
  onSave,
  onApprove,
  onReject,
  onResearch,
  onRegenerate,
  onHistory,
  onDirtyChange,
}: {
  draft: AnswerQuestionDraft;
  subject: Subject;
  readOnly: boolean;
  busy: boolean;
  onSave: (values: AnswerDraftValues) => Promise<void>;
  onApprove: (values: AnswerDraftValues) => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  onResearch: () => Promise<void>;
  onRegenerate: () => Promise<void>;
  onHistory: (runId: string) => void;
  onDirtyChange: (draftId: string, changed: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(
    draft.reviewStatus !== "approved" || draft.needsAttention,
  );
  const [values, setValues] = useState<AnswerDraftValues>({
    number: draft.effectiveNumber,
    type: draft.effectiveType,
    maxScore: draft.effectiveMaxScore,
    standardAnswer: draft.effectiveAnswer,
    scoringPoints: draft.effectiveScoringPoints,
  });
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState(draft.rejectionReason ?? "");

  useEffect(() => {
    setValues({
      number: draft.effectiveNumber,
      type: draft.effectiveType,
      maxScore: draft.effectiveMaxScore,
      standardAnswer: draft.effectiveAnswer,
      scoringPoints: draft.effectiveScoringPoints,
    });
    setReason(draft.rejectionReason ?? "");
  }, [draft]);

  const changed = useMemo(
    () =>
      values.number !== draft.effectiveNumber ||
      values.type !== draft.effectiveType ||
      values.maxScore !== draft.effectiveMaxScore ||
      values.standardAnswer !== draft.effectiveAnswer ||
      JSON.stringify(values.scoringPoints) !==
        JSON.stringify(draft.effectiveScoringPoints),
    [draft, values],
  );
  const pointTotal = values.scoringPoints.reduce(
    (sum, point) => sum + Number(point.score || 0),
    0,
  );
  const invalid =
    !values.number.trim() ||
    !values.standardAnswer.trim() ||
    !Number.isFinite(values.maxScore) ||
    values.maxScore <= 0 ||
    pointTotal > values.maxScore + 1e-8;
  const profile = getSubjectProfile(subject);

  useEffect(() => {
    onDirtyChange(draft.id, changed);
  }, [changed, draft.id, onDirtyChange]);

  return (
    <article
      className={`answer-draft-card status-${draft.reviewStatus} ${
        draft.needsAttention ? "attention" : ""
      }`}
    >
      <button
        type="button"
        className="answer-draft-summary"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="draft-question-number">
          {draft.reviewStatus === "approved" ? (
            <CheckCircle2 size={17} />
          ) : draft.reviewStatus === "failed" ? (
            <XCircle size={17} />
          ) : draft.needsAttention ? (
            <AlertTriangle size={17} />
          ) : (
            <span>{draft.effectiveNumber}</span>
          )}
        </span>
        <span className="draft-summary-copy">
          <strong>第 {draft.effectiveNumber} 题</strong>
          <small>
            {questionTypeLabel[draft.effectiveType]} ·{" "}
            {formatScore(draft.effectiveMaxScore)} 分
          </small>
        </span>
        <span
          className={`answer-source-chip source-${
            draft.sourceType ?? "pending"
          }`}
        >
          {draft.sourceType
            ? sourceLabels[draft.sourceType]
            : draft.reviewStatus === "failed"
              ? "处理失败"
              : "等待处理"}
        </span>
        <span className="draft-confidence">
          置信度 {percent(draft.confidence)}
        </span>
        {expanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
      </button>

      {expanded ? (
        <div className="answer-draft-body">
          <div className="draft-question-text">
            <span>识别题干</span>
            <p>{draft.questionText}</p>
          </div>

          {draft.needsAttention || draft.reviewStatus === "failed" ? (
            <div className="draft-attention-note">
              <AlertTriangle size={16} />
              <span>
                {draft.rejectionReason ||
                  (draft.requiresCorrection
                    ? "题号或结构存在必须由教师修正的问题，请保存修正后再审核。"
                    : "识别或答案置信度不足，请教师重点核对。")}
              </span>
            </div>
          ) : null}

          {(draft.parseIssues?.length ?? 0) > 0 ? (
            <details className="draft-auto-reason">
              <summary>识别诊断（{draft.parseIssues!.length}）</summary>
              <ul>
                {draft.parseIssues!.map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>
                    {issue.message}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          {(draft.normalizations?.length ?? 0) > 0 ? (
            <details className="draft-auto-reason">
              <summary>模型输出调整记录（{draft.normalizations!.length}）</summary>
              {draft.normalizations!.map((issue, index) => (
                <div key={`${issue.code}-${index}`}>
                  <p>{issue.message}</p>
                  {issue.originalValue !== null ? (
                    <pre>
                      {JSON.stringify(
                        {
                          before: issue.originalValue,
                          after: issue.normalizedValue,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  ) : null}
                </div>
              ))}
            </details>
          ) : null}

          <div className="draft-field-grid">
            <label>
              <span>题号</span>
              <input
                value={values.number}
                disabled={readOnly}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    number: event.target.value,
                  }))
                }
              />
            </label>
            <label>
              <span>题型</span>
              <select
                value={values.type}
                disabled={readOnly}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    type: event.target.value as QuestionType,
                  }))
                }
              >
                {profile.supportedTypes.map((type) => (
                  <option value={type} key={type}>
                    {questionTypeLabel[type]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>满分</span>
              <input
                type="number"
                min={0.5}
                step={0.5}
                value={values.maxScore}
                disabled={readOnly}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    maxScore: Number(event.target.value),
                  }))
                }
              />
            </label>
          </div>

          <label className="draft-answer-field">
            <span>标准答案</span>
            <textarea
              value={values.standardAnswer}
              disabled={readOnly}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  standardAnswer: event.target.value,
                }))
              }
              rows={3}
            />
          </label>

          <div className="draft-scoring-heading">
            <div>
              <span>评分点</span>
              <small>
                合计 {formatScore(pointTotal)} / {formatScore(values.maxScore)} 分
              </small>
            </div>
            {!readOnly ? (
              <button
                type="button"
                className="text-action"
                onClick={() =>
                  setValues((current) => ({
                    ...current,
                    scoringPoints: [
                      ...current.scoringPoints,
                      { description: "", score: 0 },
                    ],
                  }))
                }
              >
                + 添加评分点
              </button>
            ) : null}
          </div>
          <div className="draft-scoring-list">
            {values.scoringPoints.length === 0 ? (
              <div className="source-empty">按最终答案整体判定</div>
            ) : (
              values.scoringPoints.map((point, index) => (
                <div className="draft-scoring-row" key={`${index}-${point.description}`}>
                  <input
                    value={point.description}
                    disabled={readOnly}
                    placeholder="评分点说明"
                    onChange={(event) => {
                      const next = [...values.scoringPoints];
                      next[index] = {
                        ...point,
                        description: event.target.value,
                      };
                      setValues((current) => ({
                        ...current,
                        scoringPoints: next,
                      }));
                    }}
                  />
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    value={point.score}
                    disabled={readOnly}
                    onChange={(event) => {
                      const next = [...values.scoringPoints];
                      next[index] = {
                        ...point,
                        score: Number(event.target.value),
                      };
                      setValues((current) => ({
                        ...current,
                        scoringPoints: next,
                      }));
                    }}
                  />
                  <span>分</span>
                  {!readOnly ? (
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="删除评分点"
                      onClick={() =>
                        setValues((current) => ({
                          ...current,
                          scoringPoints: current.scoringPoints.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    >
                      <Trash2 size={14} />
                    </button>
                  ) : null}
                </div>
              ))
            )}
          </div>
          {pointTotal > values.maxScore + 1e-8 ? (
            <div className="field-error">评分点合计不能超过题目满分</div>
          ) : null}

          <div className="draft-auto-reason">
            <Sparkles size={16} />
            <div>
              <span>Agent 配置依据</span>
              <p>{draft.autoReason || "暂无说明"}</p>
            </div>
          </div>

          {draft.sourceType === "web_searched" ? (
            <div className="draft-sources">
              <span className="field-label">联网来源</span>
              <SourceEvidence sources={draft.sources} />
            </div>
          ) : null}

          <div className="draft-actions">
            {draft.latestRunId ? (
              <button
                type="button"
                className="button button-ghost button-small"
                onClick={() => onHistory(draft.latestRunId!)}
              >
                <History size={15} />
                原始记录
              </button>
            ) : null}
            {!readOnly ? (
              <>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={busy}
                  onClick={onResearch}
                >
                  <Search size={15} />
                  重新搜索
                </button>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={busy}
                  onClick={onRegenerate}
                >
                  <RotateCcw size={15} />
                  模型重生成
                </button>
                <span className="draft-action-spacer" />
                <button
                  type="button"
                  className="button button-secondary button-small"
                  disabled={!changed || invalid || busy}
                  onClick={() => onSave(values)}
                >
                  <Save size={15} />
                  保存修改
                </button>
                <button
                  type="button"
                  className="button button-primary button-small"
                  disabled={invalid || busy}
                  onClick={() => onApprove(values)}
                >
                  <Check size={15} />
                  审核通过
                </button>
                <button
                  type="button"
                  className="button button-danger-soft button-small"
                  disabled={busy}
                  onClick={() => setRejecting((current) => !current)}
                >
                  <XCircle size={15} />
                  退回
                </button>
              </>
            ) : null}
          </div>
          {rejecting && !readOnly ? (
            <div className="draft-reject-box">
              <input
                value={reason}
                placeholder="说明退回原因（可选）"
                onChange={(event) => setReason(event.target.value)}
              />
              <button
                type="button"
                className="button button-danger-soft button-small"
                disabled={busy}
                onClick={() => onReject(reason)}
              >
                确认退回
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
