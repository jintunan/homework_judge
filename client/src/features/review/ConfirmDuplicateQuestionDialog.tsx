import { AlertTriangle, X } from "lucide-react";

export function ConfirmDuplicateQuestionDialog({
  number,
  busy,
  error,
  onCancel,
  onConfirm
}: {
  number: string;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !busy) onCancel();
    }}>
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="duplicate-title">
        <button type="button" className="icon-button confirm-dialog__close" aria-label="关闭" disabled={busy} onClick={onCancel}><X size={17} /></button>
        <span className="confirm-dialog__icon"><AlertTriangle size={24} /></span>
        <h2 id="duplicate-title">将第 {number || "?"} 题标记为重复？</h2>
        <p>该题会退出正常题目列表、统计、答案匹配和完成校验；当前关联答案会重新变为可用。</p>
        <p className="confirm-dialog__note">识别记录不会删除。之后可以在“重复题”筛选中查看并恢复。</p>
        {error && <div className="alert alert--error">{error}</div>}
        <footer>
          <button type="button" className="button" disabled={busy} onClick={onCancel}>取消</button>
          <button type="button" className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? "正在标记…" : "标记为重复"}</button>
        </footer>
      </section>
    </div>
  );
}
