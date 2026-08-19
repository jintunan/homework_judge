import { ChevronLeft, ChevronRight, FileImage } from "lucide-react";
import type { ReviewDetail } from "@shared/contracts";

export function FilePreview({
  detail,
  role,
  page,
  onRole,
  onPage
}: {
  detail: ReviewDetail;
  role: "exam" | "answer";
  page: number;
  onRole: (role: "exam" | "answer") => void;
  onPage: (page: number) => void;
}) {
  const pages = detail.pages.filter((item) => item.role === role);
  const current = pages.find((item) => item.page_number === page) ?? pages[0];
  const document = detail.documents.find((item) => item.role === role);
  return (
    <aside className="preview">
      <div className="preview__tabs">
        <button className={role === "exam" ? "active" : ""} onClick={() => onRole("exam")}>原试卷</button>
        <button className={role === "answer" ? "active" : ""} onClick={() => onRole("answer")}>参考答案</button>
      </div>
      <div className="preview__meta"><FileImage size={15} /><span title={document?.original_name}>{document?.original_name}</span></div>
      <div className="preview__canvas">
        {current ? <img src={`/api/pages/${current.id}`} alt={`${role === "exam" ? "试卷" : "答案"}第 ${current.page_number} 页`} /> : <p>暂无页面</p>}
      </div>
      <div className="preview__pager">
        <button disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft /></button>
        <span>第 {current?.page_number ?? 0} / {pages.length} 页</span>
        <button disabled={page >= pages.length} onClick={() => onPage(page + 1)}><ChevronRight /></button>
      </div>
    </aside>
  );
}

