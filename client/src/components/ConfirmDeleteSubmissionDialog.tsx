import {AlertTriangle, X} from "lucide-react";

export function ConfirmDeleteSubmissionDialog({
  name,
  busy,
  error,
  onCancel,
  onConfirm
}: {
  name: string;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.currentTarget === event.target && !busy) onCancel();
  }}>
    <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-submission-title">
      <button className="icon-button confirm-dialog__close" aria-label="关闭" disabled={busy} onClick={onCancel}><X size={17} /></button>
      <span className="confirm-dialog__icon"><AlertTriangle size={24} /></span>
      <h2 id="delete-submission-title">永久删除这份学生答卷？</h2>
      <p>“{name}”的原卷、识别与处理历史、批改结果、批注试卷和错题报告都会被永久删除，无法恢复。</p>
      <p className="confirm-dialog__note">任务模板和其他学生答卷不会受影响；若这份答卷仍在处理，系统会先停止它的后台任务。</p>
      {error && <div className="alert alert--error">{error}</div>}
      <footer>
        <button className="button" disabled={busy} onClick={onCancel}>取消</button>
        <button className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? "正在停止并删除…" : "永久删除这份答卷"}</button>
      </footer>
    </section>
  </div>;
}
