import { AlertTriangle, X } from "lucide-react";

export function ConfirmDeleteTaskDialog({
  title,
  busy,
  error,
  onCancel,
  onConfirm
}: {
  title: string;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !busy) onCancel();
    }}>
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
        <button className="icon-button confirm-dialog__close" aria-label="关闭" disabled={busy} onClick={onCancel}><X size={17} /></button>
        <span className="confirm-dialog__icon"><AlertTriangle size={24} /></span>
        <h2 id="delete-title">永久删除这个任务？</h2>
        <p>“{title}”的试卷、答案、学生答卷、识别结果和页面文件都会被永久删除，无法恢复。</p>
        <p className="confirm-dialog__note">若任务仍在处理，系统会先停止该任务的全部后台处理，再执行删除。</p>
        {error && <div className="alert alert--error">{error}</div>}
        <footer>
          <button className="button" disabled={busy} onClick={onCancel}>取消</button>
          <button className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? "正在停止并删除…" : "永久删除全部数据"}</button>
        </footer>
      </section>
    </div>
  );
}
