import { Plus, Snowflake, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { AnswerGradingDraftPreview, GradingBlankDefinition, GradingConfig, QuestionFrameSetStatus, ReviewQuestion } from "@shared/contracts";
import { api, applyAnswerGradingDraft, generateAnswerGradingDraft } from "@/lib/api";
import { AnswerGradingDraftDialog } from "./AnswerGradingDraftDialog";
import {blankConfigErrors, rebalanceBlanks} from "./blank-score-allocation";

type RubricPoint = {
  pointKey: string;
  criterion: string;
  score: string;
  sortOrder: number;
  dependencies: string[];
};

type RubricVersion = {
  id: string;
  versionNumber: number;
  status: "draft" | "frozen";
  maxScore: string;
  points: RubricPoint[];
  frozenAt?: string | null;
  isCurrent?: boolean;
};

export function GradingConfigPanel({
  question,
  frameSetStatus,
  onApplied
}: {
  question: ReviewQuestion;
  frameSetStatus?: QuestionFrameSetStatus | null;
  onApplied?: () => Promise<void>;
}) {
  const supported = !question.isDuplicate && ["single_choice", "multiple_choice", "fill_blank", "calculation"].includes(question.effective.type);
  const [config, setConfig] = useState<GradingConfig | null>(null);
  const [versions, setVersions] = useState<RubricVersion[]>([]);
  const [draft, setDraft] = useState<RubricVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<AnswerGradingDraftPreview | null>(null);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [draftError, setDraftError] = useState("");
  const configContextKey = JSON.stringify({
    questionId: question.id,
    confirmationStatus: question.confirmationStatus,
    frameRevision: question.questionFrame?.revision ?? null,
    frameStatus: question.questionFrame?.status ?? null,
    frameSetStatus: frameSetStatus ?? null,
    type: question.effective.type,
    score: question.effective.score,
    stem: question.effective.stem,
    options: question.effective.options,
    answerRegions: question.answerRegions,
    answerEntryId: question.match.answerEntryId,
    answer: question.match.answer,
    explanation: question.match.explanation,
    matchStatus: question.match.status
  });

  const load = async () => {
    if (!supported) return;
    const nextConfig = await api<GradingConfig>(`/questions/${question.id}/grading-config`);
    setConfig(nextConfig);
    if (question.effective.type === "calculation") {
      const nextVersions = await api<RubricVersion[]>(`/questions/${question.id}/rubric-versions`);
      setVersions(nextVersions);
      setDraft(nextVersions.find((item) => item.status === "draft") ?? null);
    }
  };
  useEffect(() => {
    setMessage("");
    setConfig(null);
    setVersions([]);
    setDraft(null);
    setPreview(null);
    setDraftError("");
    void load().catch((error) => setMessage(error instanceof Error ? error.message : "评分配置读取失败"));
  }, [configContextKey]);

  if (!supported) return null;
  if (!config) return <div className="grading-config-card"><p>正在读取评分配置…</p></div>;
  const fillFrameSetNotFrozen = question.effective.type === "fill_blank"
    && frameSetStatus !== undefined
    && frameSetStatus !== "confirmed";
  const latestFrozen = versions.find((item) => item.status === "frozen") ?? null;

  const saveConfig = async () => {
    if (fillFrameSetNotFrozen) {
      setMessage("请先点击页面顶部的“冻结整套题框”，再保存逐空配置");
      return false;
    }
    if (question.effective.type === "fill_blank") {
      const validation = blankConfigErrors(config.blanks, config.maxScore);
      const firstError = validation.rows.find(Boolean) || validation.total;
      if (firstError) {
        setMessage(firstError);
        return false;
      }
    }
    setBusy(true); setMessage("");
    try {
      const saved = await api<GradingConfig>(`/questions/${question.id}/grading-config`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          questionType: question.effective.type,
          maxScore: String(config.maxScore ?? question.effective.score ?? 0),
          frameSetId: question.effective.type === "fill_blank" ? config.frameSetId : undefined,
          expectedConfigVersion: config.configVersion,
          confirm: question.effective.type === "fill_blank" ? true : undefined,
          blanks: question.effective.type === "fill_blank" ? config.blanks : []
        })
      });
      setConfig(saved); setMessage("评分配置已保存");
      return true;
    } catch (error) {
      const code = typeof error === "object" && error !== null && "code" in error
        ? String((error as {code?: unknown}).code ?? "")
        : "";
      if (["BLANK_CONFIG_VERSION_CONFLICT", "GRADING_CONFIG_VERSION_CONFLICT"].includes(code)) {
        try {
          await load();
          setMessage("配置已自动刷新到最新版本，请检查后重新保存");
        } catch (reloadError) {
          setMessage(reloadError instanceof Error ? reloadError.message : "配置刷新失败，请重新加载页面");
        }
      } else {
        setMessage(error instanceof Error ? error.message : "保存失败");
      }
      return false;
    } finally { setBusy(false); }
  };
  const createDraft = async () => {
    setBusy(true); setMessage("");
    try {
      if (!await saveConfig()) return;
      const created = await api<RubricVersion>(`/questions/${question.id}/rubric-drafts`, {method: "POST"});
      setVersions((current) => [created, ...current]); setDraft(created); setMessage("评分细则草案已生成，请检查后冻结");
    } catch (error) { setMessage(error instanceof Error ? error.message : "草案生成失败"); }
    finally { setBusy(false); }
  };
  const saveDraft = async () => {
    if (!draft) return false;
    setBusy(true); setMessage("");
    try {
      const saved = await api<RubricVersion>(`/rubric-versions/${draft.id}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({maxScore: config.maxScore, points: draft.points.map((item, index) => ({...item, sortOrder: index}))})
      });
      setDraft(saved); setMessage("评分细则草案已保存");
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "细则保存失败");
      return false;
    }
    finally { setBusy(false); }
  };
  const freeze = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      if (!await saveDraft()) return;
      const frozen = await api<RubricVersion>(`/rubric-versions/${draft.id}/freeze`, {method: "POST"});
      setDraft(null); setVersions((current) => [frozen, ...current.filter((item) => item.id !== frozen.id)]); setMessage("评分细则已冻结，可用于正式批改");
    } catch (error) { setMessage(error instanceof Error ? error.message : "冻结失败"); }
    finally { setBusy(false); }
  };
  const reconfirmFrozen = async (version: RubricVersion) => {
    setBusy(true); setMessage("");
    try {
      const refreshed = await api<RubricVersion>(`/rubric-versions/${version.id}/freeze`, {method: "POST"});
      setVersions((current) => current.map((item) => item.id === refreshed.id ? refreshed : item));
      setMessage(`评分细则 v${refreshed.versionNumber} 已按当前题目重新确认`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "评分细则重新确认失败");
    } finally { setBusy(false); }
  };
  const regenerate = async () => {
    setGenerationBusy(true); setDraftError(""); setMessage("");
    try {
      setPreview(await generateAnswerGradingDraft(question.id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "草稿生成失败，当前内容未改变");
    } finally { setGenerationBusy(false); }
  };
  const applyGenerated = async () => {
    if (!preview) return;
    setApplyBusy(true); setDraftError("");
    try {
      const result = await applyAnswerGradingDraft(question.id, preview.runId);
      setPreview(null);
      if (onApplied) await onApplied();
      else await load();
      setMessage(result.message);
    } catch (error) {
      setDraftError(error instanceof Error ? error.message : "草稿应用失败，当前内容未改变");
    } finally { setApplyBusy(false); }
  };

  return <section className="grading-config-card">
    <header><div><strong>正式批改设置</strong><small>批改运行只使用这里确认的规则</small></div><span>版本 {config.configVersion}</span></header>
    {fillFrameSetNotFrozen && <div className="alert alert--warning" role="status">
      <strong>{question.questionFrame?.status === "confirmed" ? "本题题框已确认，但整套题框尚未冻结" : "当前题框尚未确认并冻结"}</strong>
      <span>请先完成题框确认，然后点击页面顶部的“冻结整套题框”，再保存逐空配置。</span>
    </div>}
    <label className="field"><span>本题满分</span><input type="number" min="0.01" step="0.01" value={String(config.maxScore ?? "")} onChange={(event) => setConfig({...config, maxScore: event.target.value})} /></label>
    {question.effective.type === "fill_blank" && config.initialization.source === "derived" && <div className="alert alert--warning" role="status">
      <strong>{config.initialization.autoConfirmable
        ? `已识别 ${config.initialization.signals?.selectedCount ?? config.blanks.length} 个空并生成默认分值；确认题目或处理学生卷时会自动建立配置，无需逐空手工保存。`
        : `已识别 ${config.initialization.signals?.selectedCount ?? config.blanks.length} 个空，但需要逐空检查并保存后才能批改。`}</strong>
      {config.initialization.blockingReasons.length > 0 && <ul>{config.initialization.blockingReasons.map((reason) => <li key={`blocking-${reason.code}`}>{reason.message}</li>)}</ul>}
      {config.initialization.warnings.length > 0 && <ul>{config.initialization.warnings.map((warning) => <li key={warning.code}>{warning.message}</li>)}</ul>}
    </div>}
    {question.effective.type === "fill_blank" && <div className="blank-anchor-note" role="note">
      <strong>空位定位为可选</strong>
      <span>未单独定位的空将使用完整题框作为共享识别范围，无需教师绘制“独立锚点”。</span>
    </div>}
    {question.effective.type === "fill_blank" && <div className="blank-config-list">
      {config.blanks.map((blank, index) => {
        const rowError = blankConfigErrors(config.blanks, config.maxScore).rows[index];
        return <div className="blank-config-item" key={blank.blankKey}>
        <div className="blank-config-row">
        <strong>第 {index + 1} 空</strong>
        <input aria-label={`第 ${index + 1} 空分值`} type="number" min="0.01" step="0.01" value={blank.maxScore} onChange={(event) => {const blanks = [...config.blanks]; blanks[index] = {...blank, maxScore: event.target.value}; setConfig({...config, blanks});}} />
        <select value={blank.answerKind} onChange={(event) => {const blanks = [...config.blanks]; blanks[index] = {...blank, answerKind: event.target.value as GradingBlankDefinition["answerKind"]}; setConfig({...config, blanks});}}><option value="text">文字</option><option value="numeric">数值与单位</option><option value="formula">数学公式</option></select>
        <input aria-label={`第 ${index + 1} 空标准答案`} value={blank.standardAnswers.join("；")} placeholder="标准答案，用；分隔" onChange={(event) => {const blanks = [...config.blanks]; blanks[index] = {...blank, standardAnswers: event.target.value.split("；").map((item) => item.trim()).filter(Boolean)}; setConfig({...config, blanks});}} />
        <input aria-label={`第 ${index + 1} 空同义答案`} value={blank.synonyms.join("；")} placeholder="同义答案，用；分隔" onChange={(event) => {const blanks = [...config.blanks]; blanks[index] = {...blank, synonyms: event.target.value.split("；").map((item) => item.trim()).filter(Boolean)}; setConfig({...config, blanks});}} />
        <button type="button" aria-label={`删除第 ${index + 1} 空`} onClick={() => setConfig({...config, blanks: rebalanceBlanks(config.blanks.filter((_, itemIndex) => itemIndex !== index), config.maxScore)})}><Trash2 size={14} /></button>
        </div>
        {rowError && <small className="field-error">{rowError}</small>}
      </div>})}
      {blankConfigErrors(config.blanks, config.maxScore).total && <div className="field-error" role="alert">{blankConfigErrors(config.blanks, config.maxScore).total}</div>}
      <button type="button" className="text-button" onClick={() => setConfig({...config, blanks: rebalanceBlanks([...config.blanks, {blankKey: "", sortOrder: config.blanks.length, maxScore: "", answerKind: "text", standardAnswers: [], synonyms: []}], config.maxScore)})}><Plus size={14} />增加一空</button>
    </div>}
    <div className="grading-config-actions"><button type="button" className="button" disabled={busy || generationBusy || fillFrameSetNotFrozen} title={fillFrameSetNotFrozen ? "请先冻结整套题框" : ""} onClick={saveConfig}>保存批改设置</button><button type="button" className="button button--primary" disabled={busy || generationBusy} onClick={regenerate}><Sparkles size={14} />{generationBusy ? "正在生成预览…" : "重新生成答案和批改设置"}</button>{question.effective.type === "calculation" && <button type="button" className="button" disabled={busy || generationBusy} onClick={createDraft}><Sparkles size={14} />仅生成评分细则草案</button>}</div>
    {question.effective.type === "calculation" && <>
      {latestFrozen?.isCurrent === false && <div className="alert alert--warning rubric-reconfirm" role="status">
        <strong>评分细则 v{latestFrozen.versionNumber} 需要重新确认</strong>
        <span>评分配置的更新时间晚于细则冻结时间。请核对以下评分点；内容仍适用时可直接重新确认，无需再次调用模型。</span>
        <ul>{latestFrozen.points.map((point) => (
          <li key={point.pointKey}>{point.pointKey}：{point.criterion}（{point.score} 分）</li>
        ))}</ul>
        <button type="button" className="button" disabled={busy} onClick={() => reconfirmFrozen(latestFrozen)}>
          <Snowflake size={14} />确认 v{latestFrozen.versionNumber} 仍适用
        </button>
      </div>}
      {draft && <div className="rubric-editor"><h4>评分细则草案 v{draft.versionNumber}</h4><p>FINAL_ANSWER 为独立的最终答案评分点，固定占本题约 20%。省略的非关键步骤可由后续正确公式证明；不同正确解法按作用等价的评分点给分。</p>{draft.points.map((point, index) => <div className="rubric-point-row" key={`${point.pointKey}-${index}`}><input value={point.pointKey} readOnly={point.pointKey === "FINAL_ANSWER"} aria-label={`评分点 ${index + 1} 编号`} onChange={(event) => {const points = [...draft.points]; points[index] = {...point, pointKey: event.target.value}; setDraft({...draft, points});}} /><input value={point.criterion} aria-label={`评分点 ${index + 1} 要求`} onChange={(event) => {const points = [...draft.points]; points[index] = {...point, criterion: event.target.value}; setDraft({...draft, points});}} /><input type="number" min="0.01" step="0.01" value={point.score} readOnly={point.pointKey === "FINAL_ANSWER"} aria-label={`评分点 ${index + 1} 分值`} onChange={(event) => {const points = [...draft.points]; points[index] = {...point, score: event.target.value}; setDraft({...draft, points});}} /><input value={point.dependencies.join(",")} readOnly={point.pointKey === "FINAL_ANSWER"} aria-label={`评分点 ${index + 1} 依赖`} placeholder="依赖点，如 P1,P2" onChange={(event) => {const points = [...draft.points]; points[index] = {...point, dependencies: event.target.value.split(",").map((item) => item.trim()).filter(Boolean)}; setDraft({...draft, points});}} /><button disabled={point.pointKey === "FINAL_ANSWER"} aria-label={`删除评分点 ${index + 1}`} onClick={() => setDraft({...draft, points: draft.points.filter((_, itemIndex) => itemIndex !== index)})}><Trash2 size={14} /></button></div>)}<button className="text-button" onClick={() => setDraft({...draft, points: [...draft.points, {pointKey: `P${draft.points.length + 1}`, criterion: "", score: "1.00", sortOrder: draft.points.length, dependencies: []}]})}><Plus size={14} />增加评分点</button><div className="grading-config-actions"><button className="button" disabled={busy} onClick={saveDraft}>保存草案</button><button className="button button--primary" disabled={busy} onClick={freeze}><Snowflake size={14} />校验并冻结</button></div></div>}
      {versions.some((item) => item.status === "frozen") && <p className="rubric-frozen">已冻结版本：{versions.filter((item) => item.status === "frozen").map((item) => `v${item.versionNumber}${item.isCurrent === false ? "（需重新确认）" : ""}`).join("、")}</p>}
    </>}
    {message && <div className="inline-message">{message}</div>}
    {preview && <AnswerGradingDraftDialog preview={preview} busy={applyBusy} error={draftError} onCancel={() => {setPreview(null); setDraftError("");}} onApply={applyGenerated} />}
  </section>;
}
