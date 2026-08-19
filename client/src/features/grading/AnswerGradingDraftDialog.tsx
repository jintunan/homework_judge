import { AlertTriangle, X } from "lucide-react";
import type { AnswerGradingDraftPreview, AnswerGradingDraftValue } from "@shared/contracts";

const typeNames: Record<string, string> = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  fill_blank: "填空题",
  calculation: "计算题"
};

function Summary({title, value}: {title: string; value: AnswerGradingDraftValue}) {
  return <section className="draft-compare__column">
    <h3>{title}</h3>
    <dl className="draft-summary">
      <div><dt>题型</dt><dd>{typeNames[value.questionType] ?? value.questionType}</dd></div>
      <div><dt>本题满分</dt><dd>{value.maxScore}</dd></div>
      <div><dt>标准答案</dt><dd>{value.standardAnswer || "（空）"}</dd></div>
      <div><dt>解析</dt><dd>{value.explanation || "（空）"}</dd></div>
    </dl>
    {value.questionType === "fill_blank" && <div className="draft-detail-table">
      <strong>逐空批改设置（{value.blanks.length} 空）</strong>
      {value.blanks.length === 0
        ? <p>尚无逐空设置</p>
        : value.blanks.map((blank, index) => <div className="draft-blank" key={`${blank.blankKey}-${index}`}>
          <span>第 {index + 1} 空 · {blank.maxScore} 分</span>
          <b>{blank.standardAnswers.join(" / ")}</b>
          <small>{blank.answerKind === "numeric" ? "数值与单位" : blank.answerKind === "formula" ? "数学公式" : "文字"}{blank.synonyms.length ? `；同义答案：${blank.synonyms.join(" / ")}` : ""}</small>
        </div>)}
    </div>}
    {value.questionType === "calculation" && <div className="draft-detail-table">
      <strong>评分点（{value.rubricPoints.length} 项）</strong>
      {value.rubricPoints.length === 0
        ? <p>尚无冻结评分细则</p>
        : value.rubricPoints.map((point) => <div className="draft-rubric" key={point.pointKey}>
          <span>{point.pointKey} · {point.score} 分</span>
          <b>{point.criterion}</b>
        </div>)}
    </div>}
  </section>;
}

export function AnswerGradingDraftDialog({
  preview,
  busy,
  error,
  onCancel,
  onApply
}: {
  preview: AnswerGradingDraftPreview;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onApply: () => void;
}) {
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.currentTarget === event.target && !busy) onCancel();
  }}>
    <section className="draft-dialog" role="dialog" aria-modal="true" aria-labelledby="draft-preview-title">
      <header>
        <div><h2 id="draft-preview-title">预览新答案和批改设置</h2><p>当前正式内容尚未改变。请核对后再应用。</p></div>
        <button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onCancel}><X size={18} /></button>
      </header>
      {preview.warnings.length > 0 && <div className="alert alert--warning" role="status">
        <AlertTriangle size={17} /><div><strong>请重点检查</strong><ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>
      </div>}
      <div className="draft-compare">
        <Summary title="当前正式内容" value={preview.current} />
        <Summary title="模型生成草稿" value={preview.draft} />
      </div>
      <div className="alert alert--warning draft-invalidation-note">
        应用后将保留历史，但旧识别、旧分数和旧报告不再作为当前结果；已有学生答卷需要重新识别并批改。
      </div>
      {error && <div className="alert alert--error">{error}</div>}
      <footer>
        <button type="button" className="button" disabled={busy} onClick={onCancel}>取消</button>
        <button type="button" className="button button--primary" disabled={busy} onClick={onApply}>{busy ? "正在应用…" : "应用草稿并使旧结果失效"}</button>
      </footer>
    </section>
  </div>;
}
