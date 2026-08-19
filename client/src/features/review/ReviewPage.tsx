import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, CheckCircle2, ChevronLeft, ChevronRight, CopyX, Link2, RotateCcw, Save, Unlink } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import type { AnswerEntry, QuestionFrameSetStatus, QuestionValue, ReviewDetail, ReviewQuestion } from "@shared/contracts";
import { FilePreview } from "@/components/FilePreview";
import { MathContentEditor } from "@/components/MathContentEditor";
import {
  api,
  ApiError,
  confirmQuestionFrameItem,
  confirmQuestionFrameSet,
  normalizeQuestionFrameDraft,
  rerecognizeQuestionFrameItem,
  saveQuestionFrameItem
} from "@/lib/api";
import { GradingConfigPanel } from "@/features/grading/GradingConfigPanel";
import { ActionFeedback } from "@/components/ActionFeedback";
import { ConfirmDuplicateQuestionDialog } from "./ConfirmDuplicateQuestionDialog";
import { TemplateQuestionFrameEditor } from "./TemplateQuestionFrameEditor";

const typeLabels: Record<string, string> = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  fill_blank: "填空题",
  calculation: "计算题",
  short_answer: "简答题",
  unknown: "待确定"
};

function Confidence({value}: {value: number}) {
  const level = value >= 0.85 ? "high" : value >= 0.65 ? "medium" : "low";
  return <span className={`confidence confidence--${level}`}>{Math.round(value * 100)}%</span>;
}

function QuestionEditor({
  question,
  entries,
  onSaved,
  jump,
  position,
  total,
  onPrevious,
  onNext,
  onMarkDuplicate,
  onRestore,
  duplicateBusy,
  frameEditor,
  frameSetStatus
}: {
  question: ReviewQuestion;
  entries: AnswerEntry[];
  onSaved: () => Promise<void>;
  jump: (role: "exam" | "answer", page: number) => void;
  position: number;
  total: number;
  onPrevious: () => void;
  onNext: () => void;
  onMarkDuplicate: () => void;
  onRestore: () => void;
  duplicateBusy: boolean;
  frameEditor?: ReactNode;
  frameSetStatus: QuestionFrameSetStatus | null;
}) {
  const [value, setValue] = useState<QuestionValue>(question.effective);
  const [answer, setAnswer] = useState(question.match.answer);
  const [explanation, setExplanation] = useState(question.match.explanation);
  const [entryId, setEntryId] = useState(question.match.answerEntryId ?? "");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<"save" | "confirm" | "reopen" | null>(null);
  useEffect(() => {
    setValue(question.effective);
    setAnswer(question.match.answer);
    setExplanation(question.match.explanation);
    setEntryId(question.match.answerEntryId ?? "");
    setMessage("");
    setError("");
    setBusyAction(null);
  }, [question]);
  const save = async () => {
    setMessage("");
    setError("");
    setBusyAction("save");
    try {
      await api(`/questions/${question.id}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(value)
      });
      await api(`/matches/${question.match.id}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          answerEntryId: entryId || null,
          answer: entryId ? null : answer,
          explanation: entryId ? null : explanation
        })
      });
      setMessage("已保存，需重新确认");
      await onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusyAction(null);
    }
  };
  const confirm = async () => {
    setMessage("");
    setError("");
    setBusyAction("confirm");
    try {
      await api(`/questions/${question.id}/confirm`, {method: "POST"});
      await onSaved();
      setMessage("本题已确认");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "确认失败");
    } finally {
      setBusyAction(null);
    }
  };
  const reopen = async () => {
    setMessage("");
    setError("");
    setBusyAction("reopen");
    try {
      await api(`/questions/${question.id}/reopen`, {method: "POST"});
      await onSaved();
      setMessage("已取消确认，可继续修改");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消确认失败");
    } finally {
      setBusyAction(null);
    }
  };
  return (
    <article className="editor-workspace">
      <div className="editor-scroll">
        <div className={frameEditor ? "editor editor--frame-review" : "editor"}>
      <div className="editor__head">
        <div>
          <p className="eyebrow">QUESTION {question.sortOrder + 1}</p>
          <div className="editor__title">
            <h2>第 {value.number || "?"} 题</h2>
            <Confidence value={question.match.totalScore || question.confidence} />
          </div>
        </div>
        <span className={`review-state review-state--${question.isDuplicate ? "duplicate" : question.confirmationStatus}`}>
          {question.isDuplicate ? <CopyX size={16} /> : question.confirmationStatus === "confirmed" ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          {question.isDuplicate ? "重复题" : question.confirmationStatus === "confirmed" ? "已确认" : "待确认"}
        </span>
      </div>
      {question.isDuplicate && <div className="alert alert--warning">本题已标记为重复，仅保留识别记录；恢复后会回到待确认状态并重新生成匹配建议。</div>}
      {(question.issues.length > 0 || question.match.status === "needs_review") && (
        <div className="alert alert--warning">
          {[...question.issues, ...question.match.reasons].join("；")}
        </div>
      )}
      {frameEditor}
      <div className="field-row">
        <label className="field"><span>题号</span><input disabled={question.isDuplicate} value={value.number} onChange={(e) => setValue({...value, number: e.target.value})} /></label>
        <label className="field"><span>题型</span>
          <select disabled={question.isDuplicate} value={value.type} onChange={(e) => setValue({...value, type: e.target.value})}>
            {Object.entries(typeLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label className="field"><span>分值</span><input disabled={question.isDuplicate} type="number" min="0" step="0.5" value={value.score ?? ""} onChange={(e) => setValue({...value, score: e.target.value ? Number(e.target.value) : null})} /></label>
      </div>
      <div className="field">
        <span>题干 <button className="text-button" onClick={() => jump("exam", question.sourcePages[0] ?? 1)}>查看原页</button></span>
        <MathContentEditor disabled={question.isDuplicate} value={value.stem} ariaLabel="编辑题干" onChange={(stem) => setValue({...value, stem})} />
      </div>
      {value.options.length > 0 && (
        <div className="options-list">
          {value.options.map((option, index) => (
            <div className="option-editor" key={index}>
              <input disabled={question.isDuplicate} aria-label={`选项 ${index + 1} 标签`} value={option.label} onChange={(e) => {
                const options = [...value.options]; options[index] = {...option, label: e.target.value}; setValue({...value, options});
              }} />
              <MathContentEditor disabled={question.isDuplicate} compact value={option.text} ariaLabel={`编辑选项 ${option.label || index + 1}`} onChange={(text) => {
                const options = [...value.options]; options[index] = {...option, text}; setValue({...value, options});
              }} />
            </div>
          ))}
        </div>
      )}
      <div className="match-card">
        <div className="match-card__head">
          <div><Link2 size={17} /><strong>答案匹配</strong></div>
          <span>{question.match.method === "number_exact" ? "题号唯一命中" : question.match.method === "stem_similarity" ? "题干相似建议" : question.match.method === "manual" ? "教师指定" : question.match.method === "direct_entry" ? "教师录入" : "未匹配"}</span>
        </div>
        <label className="field">
          <span>关联答案条目</span>
          <select disabled={question.isDuplicate} value={entryId} onChange={(e) => {
            const id = e.target.value;
            setEntryId(id);
            const selected = entries.find((item) => item.id === id);
            if (selected) { setAnswer(selected.answer); setExplanation(selected.explanation); }
          }}>
            <option value="">不关联，直接填写</option>
            {entries.filter((item) => !item.ignored && (!item.questionId || item.id === question.match.answerEntryId)).map((item) => (
              <option key={item.id} value={item.id}>第 {item.numberHint || "?"} 题 · {item.answer.slice(0, 50)}</option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>标准答案 {question.match.answerSourcePages.length > 0 && <button className="text-button" onClick={() => jump("answer", question.match.answerSourcePages[0])}>查看答案原页</button>}</span>
          <MathContentEditor value={answer} ariaLabel="编辑标准答案" disabled={question.isDuplicate || Boolean(entryId)} onChange={setAnswer} />
        </div>
        <div className="field">
          <span>解析</span>
          <MathContentEditor value={explanation} ariaLabel="编辑解析" disabled={question.isDuplicate || Boolean(entryId)} onChange={setExplanation} />
        </div>
        <div className="evidence">
          <span>题号 {Math.round(question.match.numberScore * 100)}%</span>
          <span>题干 {Math.round(question.match.stemScore * 100)}%</span>
          <span>顺序 {Math.round(question.match.orderScore * 100)}%</span>
          {question.match.reasons.map((reason) => <span key={reason}>{reason}</span>)}
        </div>
      </div>
      {!question.isDuplicate && <div id={`grading-config-${question.id}`}>
        <GradingConfigPanel question={question} frameSetStatus={frameSetStatus} onApplied={onSaved} />
      </div>}
      <ActionFeedback message={message} error={error} />
        </div>
      </div>
      <footer className="editor__actions" aria-label="题目复核操作">
        <div className={frameEditor ? "editor__actions-inner editor__actions-inner--frame-review" : "editor__actions-inner"}>
          <div className="editor__navigation">
            <button className="button" disabled={position <= 0} onClick={onPrevious}><ChevronLeft size={16} />上一题</button>
            <span>{position + 1} / {total}</span>
            <button className="button" disabled={position >= total - 1} onClick={onNext}>下一题<ChevronRight size={16} /></button>
          </div>
          <div className="editor__primary-actions">
            {question.isDuplicate ? (
              <button type="button" className="button button--primary" disabled={duplicateBusy} onClick={onRestore}><RotateCcw size={16} />{duplicateBusy ? "正在恢复…" : "恢复题目"}</button>
            ) : <>
            <button type="button" className="button button--danger-subtle" disabled={busyAction !== null || duplicateBusy} onClick={onMarkDuplicate}><CopyX size={16} />标记为重复</button>
            <button type="button" className="button" disabled={busyAction !== null || duplicateBusy} onClick={save}><Save size={16} />{busyAction === "save" ? "正在保存…" : "保存修改"}</button>
            {question.confirmationStatus === "confirmed" ? (
              <button type="button" className="button" disabled={busyAction !== null} onClick={reopen}><Unlink size={16} />{busyAction === "reopen" ? "正在取消…" : "取消确认"}</button>
            ) : (
              <button type="button" className="button button--primary" disabled={busyAction !== null} onClick={confirm}><Check size={16} />{busyAction === "confirm" ? "正在确认…" : "确认本题"}</button>
            )}</>}
          </div>
        </div>
      </footer>
    </article>
  );
}

export function ReviewPage() {
  const {taskId = ""} = useParams();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["review", taskId],
    queryFn: () => api<ReviewDetail>(`/tasks/${taskId}/review`)
  });
  const [index, setIndex] = useState(0);
  const [filter, setFilter] = useState<"all" | "pending" | "confirmed" | "duplicate">("all");
  const [role, setRole] = useState<"exam" | "answer">("exam");
  const [page, setPage] = useState(1);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [completeError, setCompleteError] = useState("");
  const [pageMessage, setPageMessage] = useState("");
  const [duplicateTarget, setDuplicateTarget] = useState<ReviewQuestion | null>(null);
  const [duplicateError, setDuplicateError] = useState("");
  const [frameMessage, setFrameMessage] = useState("");
  const [frameError, setFrameError] = useState("");
  const [blankConfigFocusQuestionId, setBlankConfigFocusQuestionId] = useState<string | null>(null);
  const complete = useMutation({
    mutationFn: () => api(`/tasks/${taskId}/complete`, {method: "POST"}),
    onSuccess: async () => {setPageMessage("任务复核已完成"); await queryClient.invalidateQueries({queryKey: ["review", taskId]});},
    onError: (error) => setCompleteError(error instanceof ApiError ? error.message : "暂时无法完成")
  });
  const ignore = useMutation({
    mutationFn: (entryId: string) => api(`/answer-entries/${entryId}/ignore`, {method: "POST"}),
    onSuccess: async () => {
      setCompleteError("");
      setPageMessage("答案条目已标记为无关");
      await queryClient.invalidateQueries({queryKey: ["review", taskId]});
    },
    onError: (reason) => setCompleteError(reason instanceof Error ? reason.message : "标记失败")
  });
  const markDuplicate = useMutation({
    mutationFn: (questionId: string) => api(`/questions/${questionId}/mark-duplicate`, {method: "POST"}),
    onSuccess: async () => {
      setDuplicateTarget(null);
      setDuplicateError("");
      setCompleteError("");
      setPageMessage("题目已标记为重复，关联答案已释放");
      await queryClient.invalidateQueries({queryKey: ["review", taskId]});
    },
    onError: (reason) => setDuplicateError(reason instanceof Error ? reason.message : "标记失败")
  });
  const restoreDuplicate = useMutation({
    mutationFn: (questionId: string) => api(`/questions/${questionId}/restore`, {method: "POST"}),
    onSuccess: async () => {
      setCompleteError("");
      setPageMessage("题目已恢复为待确认，并重新生成匹配建议");
      setFilter("all");
      await queryClient.invalidateQueries({queryKey: ["review", taskId]});
    },
    onError: (reason) => setCompleteError(reason instanceof Error ? reason.message : "恢复失败")
  });
  const freezeFrames = useMutation({
    mutationFn: ({frameSetId, revision}: {frameSetId: string; revision: number}) =>
      confirmQuestionFrameSet(frameSetId, revision),
    onSuccess: async () => {
      setFrameError("");
      setFrameMessage("整套题框已冻结，可以上传和处理学生试卷");
      await queryClient.invalidateQueries({queryKey: ["review", taskId]});
    },
    onError: (reason) => {
      setFrameMessage("");
      setFrameError(reason instanceof Error ? reason.message : "题框冻结失败");
    }
  });
  const normalizeFrames = useMutation({
    mutationFn: ({frameSetId, revision}: {frameSetId: string; revision: number}) =>
      normalizeQuestionFrameDraft(frameSetId, revision),
    onSuccess: async () => {
      setFrameError("");
      setFrameMessage("已按相邻题目起点补齐并分隔模型题框；本操作未调用模型，请继续逐题核对");
      await queryClient.invalidateQueries({queryKey: ["review", taskId]});
    },
    onError: (reason) => {
      setFrameMessage("");
      setFrameError(reason instanceof Error ? reason.message : "题框自动整理失败");
    }
  });
  const detail = query.data;
  const questions = useMemo(() => {
    if (!detail) return [];
    return detail.questions.filter((item) => {
      if (filter === "duplicate") return item.isDuplicate;
      if (item.isDuplicate) return false;
      return filter === "all" || item.confirmationStatus === filter;
    });
  }, [detail, filter]);
  useEffect(() => setIndex(0), [filter]);
  const question = questions[Math.min(index, Math.max(0, questions.length - 1))];
  useEffect(() => {
    if (!blankConfigFocusQuestionId || question?.id !== blankConfigFocusQuestionId) return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(`grading-config-${blankConfigFocusQuestionId}`)
        ?.scrollIntoView?.({behavior: "smooth", block: "start"});
      setBlankConfigFocusQuestionId(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [blankConfigFocusQuestionId, question?.id]);
  if (query.isLoading) return <div className="loading-page">正在加载审核结果…</div>;
  if (query.error || !detail) return <div className="page"><div className="alert alert--error">{query.error?.message ?? "任务不存在"}</div></div>;
  const activeQuestions = detail.questions.filter((item) => !item.isDuplicate);
  const confirmed = activeQuestions.filter((item) => item.confirmationStatus === "confirmed").length;
  const orphan = detail.answerEntries.filter((item) => !item.questionId && !item.ignored);
  const refresh = () => queryClient.invalidateQueries({queryKey: ["review", taskId]});
  const frameSet = detail.questionFrameSet ?? null;
  const legacyRecovery = detail.studentUploadGate?.legacyRecovery;
  const activeQuestionIds = new Set(activeQuestions.map((item) => item.id));
  const activeFrameItems = frameSet?.items.filter((item) => activeQuestionIds.has(item.questionId)) ?? [];
  const confirmedFrameCount = activeFrameItems.filter((item) => item.status === "confirmed").length;
  const allFramesConfirmed = activeQuestions.length > 0
    && activeFrameItems.length === activeQuestions.length
    && confirmedFrameCount === activeQuestions.length;
  const frameItem = question
    ? frameSet?.items.find((item) => item.questionId === question.id) ?? null
    : null;
  const templatePages = detail.pages
    .filter((item) => item.role === "exam")
    .map((item) => ({
      id: item.id,
      pageNumber: item.page_number,
      width: item.width,
      height: item.height,
      imageUrl: item.imageUrl
    }));
  const frameGeometryIssues = (detail.studentUploadGate?.issues ?? [])
    .filter((issue) => issue.code.startsWith("frame_"));
  const blankConfigIssues = detail.studentUploadGate?.blankConfigIssues ?? [];
  const blankConfigIssueByQuestionId = new Map(
    blankConfigIssues.map((issue) => [issue.questionId, issue])
  );
  const blankConfigIssueNumber = (issue: (typeof blankConfigIssues)[number]) =>
    issue.questionNumber
    || activeQuestions.find((item) => item.id === issue.questionId)?.effective.number
    || "?";
  const blankConfigIssueLabel = (issue: (typeof blankConfigIssues)[number]) => {
    const number = blankConfigIssueNumber(issue);
    if (issue.code === "BLANK_CONFIG_MISSING") return `第 ${number} 题：未配置`;
    if (issue.code === "BLANK_CONFIG_FRAME_MISMATCH") {
      return `第 ${number} 题：题框变化后需重新确认`;
    }
    if (issue.code === "LEGACY_BLANK_CONFIG_CONFIRMATION_REQUIRED") {
      return `第 ${number} 题：历史配置待确认`;
    }
    return `第 ${number} 题：配置待确认`;
  };
  const blankConfigNumbers = blankConfigIssues.map(blankConfigIssueNumber);
  const frameReady = Boolean(
    frameSet?.status === "confirmed"
    && (detail.studentUploadGate?.missingQuestionIds.length ?? 0) === 0
    && (detail.studentUploadGate?.unconfirmedQuestionIds.length ?? 0) === 0
    && frameGeometryIssues.length === 0
  );
  const canNormalizeModelDraft = Boolean(
    frameSet?.status === "draft"
    && frameSet.source === "model"
    && activeFrameItems.length > 1
    && activeFrameItems.every((item) =>
      item.status === "pending" && item.fragments.every((fragment) => fragment.source === "model")
    )
  );
  const jump = (nextRole: "exam" | "answer", nextPage: number) => {
    setRole(nextRole);
    setPage(nextPage);
    setPreviewOpen(true);
  };
  const nextPendingFrame = activeQuestions.find((item) =>
    frameSet?.items.find((frame) => frame.questionId === item.id)?.status !== "confirmed"
  );
  const showQuestion = (questionId: string) => {
    const nextIndex = activeQuestions.findIndex((item) => item.id === questionId);
    if (nextIndex < 0) return;
    setFilter("all");
    setIndex(nextIndex);
    setRole("exam");
    setPage(activeQuestions[nextIndex].sourcePages[0] ?? 1);
  };
  const showBlankConfig = (questionId: string) => {
    setBlankConfigFocusQuestionId(questionId);
    showQuestion(questionId);
  };
  const saveFrame = async ({
    questionId,
    expectedRevision,
    regions
  }: {
    questionId: string;
    expectedRevision: number;
    regions: NonNullable<typeof frameItem>["fragments"];
  }) => {
    if (!frameSet) throw new Error("当前任务没有可编辑的题框版本");
    const saved = await saveQuestionFrameItem(
      frameSet.id,
      questionId,
      expectedRevision,
      regions
    );
    const savedItem = saved.items.find((item) => item.questionId === questionId);
    setFrameMessage("题框修改已保存，该题需要重新确认");
    setFrameError("");
    await refresh();
    return {
      revision: saved.revision,
      regions: savedItem?.fragments,
      status: savedItem?.status
    };
  };
  const confirmFrame = async ({
    questionId,
    expectedRevision
  }: {
    questionId: string;
    expectedRevision: number;
  }) => {
    if (!frameSet) throw new Error("当前任务没有可确认的题框版本");
    const saved = await confirmQuestionFrameItem(frameSet.id, questionId, expectedRevision);
    const savedItem = saved.items.find((item) => item.questionId === questionId);
    setFrameMessage("本题题框已确认");
    setFrameError("");
    await refresh();
    return {revision: saved.revision, status: savedItem?.status};
  };
  const rerecognizeFrame = async ({
    questionId,
    expectedRevision,
    regions
  }: {
    questionId: string;
    expectedRevision: number;
    regions: NonNullable<typeof frameItem>["fragments"];
  }) => {
    if (!frameSet) throw new Error("当前任务没有可编辑的题框版本");
    let result;
    try {
      result = await rerecognizeQuestionFrameItem(
        frameSet.id,
        questionId,
        expectedRevision,
        regions
      );
    } catch (reason) {
      const savedFrameSet = reason instanceof ApiError
        && reason.details
        && typeof reason.details === "object"
        && "savedFrameSet" in reason.details
          ? (reason.details as {savedFrameSet?: unknown}).savedFrameSet
          : null;
      if (savedFrameSet) await refresh();
      setFrameMessage("");
      setFrameError(reason instanceof Error ? reason.message : "本题重新识别失败");
      throw reason;
    }
    const savedItem = result.frameSet.items.find((item) => item.questionId === questionId);
    setFrameMessage(result.teacherOverridePreserved
      ? "模型原始识别已更新；当前仍显示教师修改内容，请重新确认题目和题框"
      : "本题原文已重新识别，请重新确认题目和题框");
    setFrameError("");
    await refresh();
    return {
      revision: result.frameSet.revision,
      regions: savedItem?.fragments,
      status: savedItem?.status,
      teacherOverridePreserved: result.teacherOverridePreserved
    };
  };
  const frameEditor = question && frameSet && frameItem && !question.isDuplicate ? (
    <TemplateQuestionFrameEditor
      key={`${frameSet.id}:${question.id}`}
      pages={templatePages}
      questionNumber={question.effective.number || String(question.sortOrder + 1)}
      currentItem={{...frameItem, revision: frameSet.revision}}
      otherItems={activeFrameItems
        .filter((item) => item.questionId !== question.id)
        .map((item) => ({
          questionNumber: activeQuestions.find((value) => value.id === item.questionId)
            ?.effective.number ?? "?",
          item
        }))}
      questionConfirmationStatus={question.confirmationStatus}
      geometryBlockers={frameGeometryIssues
        .filter((issue) =>
          issue.questionId === question.id || issue.relatedQuestionId === question.id
        )
        .map((issue) => issue.message)}
      onSave={saveFrame}
      onRerecognize={rerecognizeFrame}
      onConfirm={confirmFrame}
    />
  ) : !question?.isDuplicate ? (
    <div className="alert alert--warning">
      当前题目还没有可确认的完整题框，请先生成题框草稿。
    </div>
  ) : null;
  const completeReasons = [
    confirmed !== activeQuestions.length ? `还有 ${activeQuestions.length - confirmed} 道题未确认` : "",
    orphan.length > 0 ? `还有 ${orphan.length} 条未处理答案` : "",
    !frameReady ? "题框尚未全部确认并冻结" : "",
    blankConfigIssues.length > 0
      ? `还有 ${blankConfigIssues.length} 道填空题配置待确认（第 ${blankConfigNumbers.join("、")} 题）`
      : ""
  ].filter(Boolean);
  const alreadyCompleted = detail.task.status === "completed";
  return (
    <div className="review-page">
      <header className="review-head">
        <div>
          <Link to="/" className="back-link"><ChevronLeft size={16} />所有任务</Link>
          <h1>{detail.task.title}</h1>
        </div>
        <div className="review-summary">
          {detail.studentUploadGate?.ready ? (
            <Link className="button" to={`/tasks/${taskId}/students`}>学生答卷</Link>
          ) : (
            <button
              type="button"
              className="button"
              disabled
              title="逐题确认并冻结题框后开放学生答卷"
            >学生答卷（题框待确认）</button>
          )}
          <span>
            题框 v{frameSet?.versionNumber ?? "-"} · <strong>{confirmedFrameCount}</strong>
            / {activeQuestions.length}
          </span>
          {nextPendingFrame && (
            <button
              type="button"
              className="button"
              onClick={() => showQuestion(nextPendingFrame.id)}
            >下一未确认题框</button>
          )}
          <button
            type="button"
            className="button"
            aria-expanded={previewOpen}
            onClick={() => setPreviewOpen((open) => !open)}
          >{previewOpen ? "收起参考页" : "打开参考页"}</button>
          <button
            type="button"
            className="button"
            disabled={
              !frameSet
              || frameSet.status !== "draft"
              || !allFramesConfirmed
              || freezeFrames.isPending
            }
            title={!allFramesConfirmed ? "必须先逐题确认全部题框" : ""}
            onClick={() => {
              if (!frameSet) return;
              setFrameMessage("");
              setFrameError("");
              freezeFrames.mutate({frameSetId: frameSet.id, revision: frameSet.revision});
            }}
          >{frameSet?.status === "confirmed" ? "题框已冻结" : "冻结整套题框"}</button>
          <span><strong>{confirmed}</strong> / {activeQuestions.length} 已确认</span>
          <span><strong>{orphan.length}</strong> 未使用答案</span>
          <button type="button" className="button button--primary" onClick={() => {setCompleteError(""); setPageMessage(""); complete.mutate();}} disabled={alreadyCompleted || completeReasons.length > 0 || complete.isPending} title={completeReasons.join("；")}>
            {alreadyCompleted ? "任务已完成" : complete.isPending ? "正在完成…" : "完成任务"}
          </button>
        </div>
      </header>
      <div className="review-alert">
        <ActionFeedback
          message={pageMessage || frameMessage}
          error={completeError || frameError}
          disabledReason={!alreadyCompleted ? completeReasons.join("；") : undefined}
        />
        {(!frameReady || blankConfigIssues.length > 0) && (
          <div className="review-gate-summary" role="status">
            {!frameReady && confirmedFrameCount < activeQuestions.length && (
              <span><strong>{activeQuestions.length - confirmedFrameCount}</strong> 道题框待确认</span>
            )}
            {!frameReady
              && confirmedFrameCount === activeQuestions.length
              && frameSet?.status === "draft" && (
                <span><strong>全部题框已逐题确认</strong>，等待冻结整套题框</span>
              )}
            {frameGeometryIssues.length > 0 && (
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  const issue = frameGeometryIssues[0];
                  const target = issue.questionId ?? issue.relatedQuestionId;
                  if (target) showQuestion(target);
                }}
              ><strong>{frameGeometryIssues.length}</strong> 处题框边界冲突，前往处理</button>
            )}
            {blankConfigIssues.length > 0 && (
              <>
                <span><strong>{blankConfigIssues.length}</strong> 道填空题配置待确认：</span>
                {blankConfigIssues.map((issue) => (
                  <button
                    key={`${issue.questionId}-${issue.code}`}
                    type="button"
                    className="text-button"
                    title={issue.message}
                    onClick={() => {
                      if (issue.questionId) showBlankConfig(issue.questionId);
                    }}
                  >{blankConfigIssueLabel(issue)}</button>
                ))}
              </>
            )}
            {canNormalizeModelDraft && frameSet && (
              <button
                type="button"
                className="button"
                disabled={normalizeFrames.isPending}
                onClick={() => normalizeFrames.mutate({frameSetId: frameSet.id, revision: frameSet.revision})}
              >{normalizeFrames.isPending ? "正在整理…" : "自动补齐题框（不调用模型）"}</button>
            )}
          </div>
        )}
        {legacyRecovery?.required ? (
          <div className="alert alert--warning" role="status">
            <strong>{legacyRecovery.readyForReprocess ? "历史任务已确认，可按新流程重处理" : "历史任务待确认"}</strong>
            <span>{legacyRecovery.readyForReprocess
              ? "学生原图、旧识别、旧评分和产物都会保留。请前往学生答卷页，为每份历史答卷显式创建新的处理版本。"
              : "迁移得到的题框和逐空配置均不会自动确认。请逐题核对题框，并保存、确认所有填空题的逐空配置后再处理学生答卷。"}</span>
          </div>
        ) : null}
      </div>
      <div className={`review-layout ${previewOpen ? "review-layout--preview-open" : "review-layout--preview-closed"}`}>
        <aside className="question-nav">
          <div className="segmented">
            {(["all", "pending", "confirmed", "duplicate"] as const).map((value) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "全部" : value === "pending" ? "待确认" : value === "confirmed" ? "已确认" : `重复题 ${detail.questions.length - activeQuestions.length}`}</button>)}
          </div>
          <div className="question-nav__list">
            {questions.map((item, itemIndex) => {
              const blankConfigIssue = blankConfigIssueByQuestionId.get(item.id);
              return <button key={item.id} className={`${itemIndex === index ? "active" : ""} ${item.isDuplicate ? "duplicate" : item.confirmationStatus} ${blankConfigIssue ? "blank-config-pending" : ""}`} onClick={() => {setIndex(itemIndex); setRole("exam"); setPage(item.sourcePages[0] ?? 1);}}>
                <span>{item.effective.number || item.sortOrder + 1}</span>
                <small>{blankConfigIssue ? "配置待确认" : item.isDuplicate ? "重复题" : item.confirmationStatus === "confirmed" ? "已确认" : item.match.status === "needs_review" ? "需处理" : "待确认"}</small>
              </button>
            })}
          </div>
          {orphan.length > 0 && (
            <div className="orphan-box">
              <strong>未使用答案</strong>
              {orphan.map((item) => (
                <div key={item.id}><span>第 {item.numberHint || "?"} 题 · {item.answer.slice(0, 22)}</span><button type="button" disabled={ignore.isPending && ignore.variables === item.id} onClick={() => {setPageMessage(""); setCompleteError(""); ignore.mutate(item.id);}}>{ignore.isPending && ignore.variables === item.id ? "正在标记…" : "标记无关"}</button></div>
              ))}
            </div>
          )}
        </aside>
        <section className="review-center">
          {question ? (
            <QuestionEditor
              key={question.id}
              question={question}
              entries={detail.answerEntries}
              onSaved={refresh}
              jump={jump}
              position={index}
              total={questions.length}
              onPrevious={() => setIndex((current) => Math.max(0, current - 1))}
              onNext={() => setIndex((current) => Math.min(questions.length - 1, current + 1))}
              onMarkDuplicate={() => {setDuplicateError(""); setDuplicateTarget(question);}}
              onRestore={() => restoreDuplicate.mutate(question.id)}
              duplicateBusy={(markDuplicate.isPending && markDuplicate.variables === question.id) || (restoreDuplicate.isPending && restoreDuplicate.variables === question.id)}
              frameEditor={frameEditor}
              frameSetStatus={frameSet?.status ?? null}
            />
          ) : <p className="no-results">当前筛选没有题目</p>}
        </section>
        {previewOpen && <FilePreview detail={detail} role={role} page={page} onRole={(next) => {setRole(next); setPage(1);}} onPage={setPage} />}
      </div>
      {duplicateTarget && <ConfirmDuplicateQuestionDialog
        number={duplicateTarget.effective.number}
        busy={markDuplicate.isPending}
        error={duplicateError}
        onCancel={() => {if (!markDuplicate.isPending) {setDuplicateTarget(null); setDuplicateError("");}}}
        onConfirm={() => markDuplicate.mutate(duplicateTarget.id)}
      />}
    </div>
  );
}
