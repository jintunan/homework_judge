import { FileText, Image, UploadCloud } from "lucide-react";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadTask } from "@/lib/api";
import { ActionFeedback } from "@/components/ActionFeedback";

const ACCEPT = ".pdf,.docx,.jpg,.jpeg,.png";

function FilePicker({
  label,
  hint,
  file,
  onChange
}: {
  label: string;
  hint: string;
  file: File | null;
  onChange: (file: File | null) => void;
}) {
  return (
    <label className={`upload-box ${file ? "upload-box--selected" : ""}`}>
      <input
        type="file"
        accept={ACCEPT}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
      <span className="upload-box__icon">{file ? <FileText /> : <UploadCloud />}</span>
      <strong>{file?.name ?? label}</strong>
      <small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : hint}</small>
    </label>
  );
}

export function CreateTaskPage() {
  const navigate = useNavigate();
  const [exam, setExam] = useState<File | null>(null);
  const [answer, setAnswer] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!exam || !answer) {
      setError("请分别选择试卷和参考答案文件");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await uploadTask(exam, answer, title);
      navigate(`/tasks/${result.taskId}/processing`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败");
      setBusy(false);
    }
  };
  return (
    <section className="page page--narrow">
      <div className="page-head">
        <div>
          <p className="eyebrow">NEW RECOGNITION</p>
          <h1>新建识别任务</h1>
          <p>先分别识别两份文件，再由本地规则完成一对一匹配。</p>
        </div>
      </div>
      <form className="panel form-panel" onSubmit={submit}>
        <label className="field">
          <span>任务名称 <small>可选</small></span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="默认使用试卷文件名" />
        </label>
        <div className="upload-grid">
          <FilePicker label="选择试卷" hint="PDF、DOCX、JPG 或 PNG" file={exam} onChange={setExam} />
          <FilePicker label="选择参考答案" hint="精简答案或完整解析版均可" file={answer} onChange={setAnswer} />
        </div>
        <div className="file-note"><Image size={16} />单文件最多 30 MB、30 页；公式与示意图将按页面视觉识别。</div>
        <ActionFeedback error={error} disabledReason={!exam || !answer ? "请选择试卷和参考答案后再开始识别" : undefined} />
        <div className="form-actions">
          <button type="submit" className="button button--primary button--large" disabled={busy} aria-busy={busy}>
            {busy ? "正在上传…" : "上传并开始识别"}
          </button>
        </div>
      </form>
    </section>
  );
}
