import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, FileUp, RefreshCw, ScanLine, Trash2 } from "lucide-react";
import {FormEvent, useEffect, useMemo, useState} from "react";
import { Link, useParams } from "react-router-dom";
import type {
  AlignmentControlPointPair,
  ReviewDetail,
  StudentSubmissionDetail,
  StudentSubmissionSummary
} from "@shared/contracts";
import { api, deleteStudentSubmission, uploadStudentSubmission } from "@/lib/api";
import { StudentPageOverlay } from "./StudentPageOverlay";
import {
  StudentAlignmentEditor,
  type AlignmentStudentPage,
  type AlignmentTemplatePage
} from "./StudentAlignmentEditor";
import { ActionFeedback } from "@/components/ActionFeedback";
import {ConfirmDeleteSubmissionDialog} from "@/components/ConfirmDeleteSubmissionDialog";

type QuestionRegion = StudentSubmissionDetail["questionRegions"][number];
type MappedQuestionRegion = QuestionRegion & {
  frameSetId?: string | null;
  frameRegionId?: string | null;
  alignmentRevisionId?: string | null;
  processingRevisionId?: string | null;
};

interface MappingBlocker {
  code: string;
  message: string;
  nextAction?: string | null;
}

type AlignmentAwareStudentPage = StudentSubmissionDetail["pages"][number] & {
  alignment: StudentSubmissionDetail["pages"][number]["alignment"] & {
    revisionNumber?: number | null;
    source?: "model" | "teacher" | null;
    controlPoints?: AlignmentControlPointPair[];
  };
};

function autoGradingText(item: StudentSubmissionSummary): string {
  const status = item.auto_grading_status;
  const progress = item.auto_grading_progress_total
    ? ` ${item.auto_grading_progress_current ?? 0}/${item.auto_grading_progress_total}`
    : "";
  if (status === "running" || status === "pending") return `自动批改中${progress}`;
  if (status === "completed") return `自动批改完成${item.auto_grading_total_score ? ` · ${item.auto_grading_total_score} 分` : ""}`;
  if (status === "needs_review") return `批改完成 · ${item.auto_grading_open_review_count ?? 0} 项待复核`;
  if (status === "blocked") return "自动批改被阻断";
  if (status === "failed") return "自动批改失败";
  return "等待自动批改";
}

export function StudentSubmissionsPage() {
  const {taskId = ""} = useParams();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [historyRevisionId, setHistoryRevisionId] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [studentName, setStudentName] = useState("");
  const [studentIdentifier, setStudentIdentifier] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [alignmentEditorOpen, setAlignmentEditorOpen] = useState(false);
  const [deleting, setDeleting] = useState<StudentSubmissionSummary | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const list = useQuery({
    queryKey: ["student-submissions", taskId],
    queryFn: () => api<StudentSubmissionSummary[]>(`/tasks/${taskId}/student-submissions`),
    refetchInterval: (query) => query.state.data?.some((item) =>
      ["uploaded", "aligning", "recognizing"].includes(item.status) ||
      item.question_region_status === "processing" ||
      ["pending", "running"].includes(item.auto_grading_status ?? "")
    ) ? 1800 : false
  });
  useEffect(() => {
    if (!selectedId && list.data?.[0]) setSelectedId(list.data[0].id);
  }, [list.data, selectedId]);
  const detail = useQuery({
    queryKey: ["student-submission", selectedId, historyRevisionId],
    queryFn: () => api<StudentSubmissionDetail>(
      `/student-submissions/${selectedId}${historyRevisionId ? `?processingRevisionId=${encodeURIComponent(historyRevisionId)}` : ""}`
    ),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => {
      const value = query.state.data;
      return value && (
        value.submission.status !== "ready" ||
        value.questionRegionState.status === "processing" ||
        value.processingRevision?.status === "aligning" ||
        value.processingRevision?.status === "recognizing" ||
        ["pending", "running"].includes(value.submission.auto_grading_status ?? "")
      ) ? 1800 : false;
    }
  });
  const templateReview = useQuery<ReviewDetail>({
    queryKey: ["task-review-pages", taskId],
    queryFn: () => api<ReviewDetail>(`/tasks/${taskId}/review`),
    enabled: Boolean(taskId)
  });
  const upload = useMutation({
    mutationFn: () => uploadStudentSubmission(taskId, file!, studentIdentifier, studentName),
    onSuccess: async (value) => {
      setError(""); setMessage("学生答卷已上传，正在对齐和提取区域"); setFile(null); setSelectedId(value.submissionId); setHistoryRevisionId(""); setPageIndex(0); setAlignmentEditorOpen(false);
      await queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]});
    },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "上传失败");}
  });
  const backfill = useMutation({
    mutationFn: () => api(`/tasks/${taskId}/question-regions/process`, {method: "POST"}),
    onSuccess: async () => { setError(""); setMessage("整题区域任务已启动"); await queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]}); },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "启动失败");}
  });
  const retry = useMutation({
    mutationFn: (submissionId: string) => api(`/student-submissions/${submissionId}/process`, {method: "POST"}),
    onSuccess: async () => {
      setError(""); setMessage("已重新启动学生答卷处理");
      await queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]});
      await queryClient.invalidateQueries({queryKey: ["student-submission", selectedId]});
    },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "重试失败");}
  });
  const remove = useMutation({
    mutationFn: (submissionId: string) => deleteStudentSubmission(submissionId),
    onSuccess: async (value, deletedId) => {
      const current = list.data ?? [];
      const deletedIndex = current.findIndex((item) => item.id === deletedId);
      const remaining = current.filter((item) => item.id !== deletedId);
      const next = remaining[Math.min(Math.max(0, deletedIndex), remaining.length - 1)];
      queryClient.setQueryData<StudentSubmissionSummary[]>(
        ["student-submissions", taskId],
        (cached) => cached?.filter((item) => item.id !== deletedId)
      );
      queryClient.removeQueries({queryKey: ["student-submission", deletedId]});
      setDeleting(null);
      setDeleteError("");
      setHistoryRevisionId("");
      setPageIndex(0);
      setAlignmentEditorOpen(false);
      if (selectedId === deletedId) setSelectedId(next?.id ?? "");
      setError("");
      setMessage(value.cleanupPending
        ? "学生答卷已删除；少量临时清理将在后台继续，不影响其他答卷"
        : "学生答卷已永久删除");
      await queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]});
    },
    onError: (reason) => setDeleteError(reason instanceof Error ? reason.message : "删除失败")
  });
  const reprocessNewFlow = useMutation({
    mutationFn: (submissionId: string) => api(`/student-submissions/${submissionId}/reprocess-new-flow`, {method: "POST"}),
    onSuccess: async () => {
      setHistoryRevisionId("");
      setError("");
      setMessage("已按新流程创建新的处理版本，旧结果会保留在历史中");
      await Promise.all([
        queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]}),
        queryClient.invalidateQueries({queryKey: ["student-submission", selectedId]})
      ]);
    },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "启动新流程重处理失败");}
  });
  const currentPage = detail.data?.pages[pageIndex] as AlignmentAwareStudentPage | undefined;
  const templatePages = useMemo<AlignmentTemplatePage[]>(() => (
    templateReview.data?.pages
      .filter((page) => page.role === "exam")
      .map((page) => ({
        id: page.id,
        pageNumber: page.page_number,
        width: page.width,
        height: page.height,
        imageUrl: page.imageUrl
      })) ?? []
  ), [templateReview.data?.pages]);
  const pageRegions = useMemo<MappedQuestionRegion[]>(() => {
    if (!currentPage || !detail.data) return [];
    return (detail.data.questionRegions as MappedQuestionRegion[])
      .filter((region) => region.studentPageId === currentPage.id);
  }, [currentPage, detail.data]);
  const mappingProvenance = useMemo(() => {
    const frameSetIds = new Set<string>();
    const alignmentRevisionIds = new Set<string>();
    const processingRevisionIds = new Set<string>();
    if (detail.data?.processingRevision?.frameSetId) {
      frameSetIds.add(detail.data.processingRevision.frameSetId);
      processingRevisionIds.add(detail.data.processingRevision.id);
    }
    for (const region of pageRegions) {
      if (region.frameSetId) frameSetIds.add(region.frameSetId);
      if (region.alignmentRevisionId) alignmentRevisionIds.add(region.alignmentRevisionId);
      if (region.processingRevisionId) processingRevisionIds.add(region.processingRevisionId);
    }
    return {
      frameSet: [...frameSetIds].join(", ") || "—",
      alignmentRevision: [...alignmentRevisionIds].join(", ") || "—",
      processingRevision: detail.data?.processingRevision
        ? `R${detail.data.processingRevision.revisionNumber}`
        : [...processingRevisionIds].join(", ") || "—"
    };
  }, [detail.data?.processingRevision, pageRegions]);
  const mappingBlockers = useMemo<MappingBlocker[]>(() => {
    const blockers = new Map<string, MappingBlocker>();
    const add = (code: string, blockerMessage: string, nextAction?: string | null) => {
      const key = `${code}\u0000${blockerMessage}`;
      if (!blockers.has(key)) blockers.set(key, {code, message: blockerMessage, nextAction});
    };
    const regionState = detail.data?.questionRegionState;
    if (regionState && regionState.status !== "ready" && (regionState.errorCode || regionState.errorMessage)) {
      add(regionState.errorCode ?? "mapping_needs_review", regionState.errorMessage ?? "题框映射需要复核");
    }
    if (regionState?.missingQuestionIds.length) {
      add("missing_question_regions", `${regionState.missingQuestionIds.length} 道题没有可用的学生页映射片段`);
    }
    const processingRevision = detail.data?.processingRevision;
    if (processingRevision) {
      for (const issue of processingRevision.issues) {
        if (issue.layer === "alignment") add(issue.code, issue.message, issue.nextAction);
      }
    }
    for (const region of pageRegions) {
      for (const issue of region.issues) {
        add(issue, `题目 ${region.questionNumber} 的片段 ${region.sortOrder + 1} 需要复核`);
      }
    }
    if (processingRevision?.status === "mapping_needs_review" && blockers.size === 0) {
      add("mapping_needs_review", "当前映射修订需要教师复核");
    }
    return [...blockers.values()];
  }, [detail.data?.processingRevision, detail.data?.questionRegionState, pageRegions]);
  const alignmentStudentPage: AlignmentStudentPage | null = currentPage ? {
    id: currentPage.id,
    pageNumber: currentPage.pageNumber,
    width: currentPage.width,
    height: currentPage.height,
    imageUrl: currentPage.imageUrl,
    templatePageId: currentPage.templatePageId,
    alignment: {
      revisionNumber: currentPage.alignment.revisionNumber ?? null,
      source: currentPage.alignment.source ?? null,
      controlPoints: currentPage.alignment.controlPoints ?? []
    }
  } : null;
  const refreshAfterAlignment = async () => {
    await Promise.all([
      queryClient.invalidateQueries({queryKey: ["student-submission", selectedId]}),
      queryClient.invalidateQueries({queryKey: ["student-submissions", taskId]})
    ]);
  };
  const submit = (event: FormEvent) => { event.preventDefault(); if (file) upload.mutate(); };
  const studentUploadGate = templateReview.data?.studentUploadGate;
  const legacyRecovery = studentUploadGate?.legacyRecovery;
  const currentLegacyProcessing = detail.data?.processingHistory?.some(
    (revision) => revision.isCurrent && revision.source === "legacy"
  ) ?? false;
  const uploadBlockedReason = studentUploadGate?.ready
    ? ""
    : legacyRecovery?.required
      ? "历史任务的题框或逐空配置尚未由教师确认，暂不能上传或处理学生答卷"
      : "请先逐题确认完整题框，并确认所有填空题的逐空配置";
  const gradingReady = detail.data?.submission.status === "ready" && detail.data.questionRegionState.status === "ready";
  const gradingBlockedReason = !selectedId
    ? "请先选择一份学生答卷"
    : detail.data?.submission.status !== "ready"
      ? "学生答卷仍在处理，完成后才能进入批改"
      : detail.data.questionRegionState.status !== "ready"
        ? "题目区域尚未就绪，请先完成题框处理"
        : "";
  return (
    <section className="students-page">
      <header className="students-head">
        <div><Link className="back-link" to={`/tasks/${taskId}/review`}><ChevronLeft size={16} />返回题目复核</Link><h1>学生答卷与题目区域</h1><p>题框显示在学生原图上，仅用于定位和选择，不表示批改对错。</p></div>
        <div className="students-head__actions">{gradingReady ? <Link className="button button--primary" to={`/tasks/${taskId}/students/${selectedId}/grading`}>进入批改工作台</Link> : <button type="button" className="button button--primary" disabled title={gradingBlockedReason}>进入批改工作台</button>}<button type="button" className="button" disabled={backfill.isPending} onClick={() => {setMessage(""); setError(""); backfill.mutate();}}><RefreshCw size={16} />{backfill.isPending ? "正在补算…" : "补算全部题目区域"}</button></div>
      </header>
      <div className="students-message">
        <ActionFeedback message={message} error={error} disabledReason={uploadBlockedReason || gradingBlockedReason} />
        {legacyRecovery?.required ? (
          <div className="alert alert--warning" role="status">
            <strong>{legacyRecovery.readyForReprocess ? "历史任务已具备新流程条件" : "历史任务待确认"}</strong>
            <span>{legacyRecovery.readyForReprocess
              ? "原始答卷、旧识别和旧评分均会保留；请选择下方学生答卷后按新流程重处理。"
              : "历史题框和逐空配置不会被自动信任。请先在题目复核中逐题确认题框，并确认每道填空题的配置。"}</span>
          </div>
        ) : null}
      </div>
      <div className="students-layout">
        <aside className="student-sidebar">
          <form className="student-upload" onSubmit={submit}>
            <strong>上传学生答卷</strong>
            <input placeholder="学号（可选）" value={studentIdentifier} onChange={(e) => setStudentIdentifier(e.target.value)} />
            <input placeholder="姓名（可选）" value={studentName} onChange={(e) => setStudentName(e.target.value)} />
            <label><FileUp size={17} /><span>{file?.name ?? "选择 PDF 或图片"}</span><input type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
            <button className="button button--primary" disabled={!file || upload.isPending || !studentUploadGate?.ready} title={!file ? "请先选择学生答卷文件" : uploadBlockedReason || undefined}>{upload.isPending ? "上传中…" : "上传并自动批改"}</button>
          </form>
          <div className="student-list">
            {list.data?.map((item) => <div key={item.id} className={`student-list__item ${selectedId === item.id ? "active" : ""}`}><button className="student-list__select" onClick={() => {setSelectedId(item.id); setHistoryRevisionId(""); setPageIndex(0); setAlignmentEditorOpen(false);}}><strong>{item.student_name || item.original_name || "未命名学生"}</strong><small>{item.student_identifier || "未填写学号"} · {item.status === "ready" ? item.question_region_error_code === "STUDENT_PAGES_PARTIAL" ? "已处理（部分答卷）" : "已对齐" : item.status === "failed" ? "处理失败" : "处理中"}</small><span className={`region-state region-state--${item.question_region_status}`}>{item.question_region_status === "ready" ? "题框就绪" : item.question_region_status === "needs_review" ? "题框需检查" : item.question_region_status === "failed" ? "题框失败" : "题框处理中"}</span><span className={`auto-grade-state auto-grade-state--${item.auto_grading_status ?? "waiting"}`}>{autoGradingText(item)}</span></button><button type="button" className="student-list__delete" aria-label={`删除学生答卷 ${item.student_name || item.original_name || "未命名学生"}`} onClick={() => {setDeleteError(""); setDeleting(item);}}><Trash2 size={15} /></button></div>)}
            {!list.isLoading && !list.data?.length && <p>还没有学生答卷</p>}
          </div>
        </aside>
        <main className="student-viewer">
          {detail.data?.submission.status === "failed" && <div className="student-viewer__error"><span>{detail.data.submission.error_message || "学生答卷处理失败"}</span><button className="button" disabled={retry.isPending || !studentUploadGate?.ready} onClick={() => retry.mutate(detail.data!.submission.id)}>{retry.isPending ? "重试中…" : "重新处理"}</button></div>}
          {currentLegacyProcessing && legacyRecovery?.readyForReprocess && selectedId ? (
            <div className="student-viewer__error" role="status">
              <span>当前显示的是历史处理结果。按新流程重处理会创建新的处理版本，不会删除原图、旧识别、旧评分或产物。</span>
              <button className="button button--primary" disabled={reprocessNewFlow.isPending || detail.data?.isHistoricalView} onClick={() => reprocessNewFlow.mutate(selectedId)}>{reprocessNewFlow.isPending ? "正在创建…" : "按新流程重处理"}</button>
            </div>
          ) : null}
          {detail.data?.processingHistory && detail.data.processingHistory.length > 1 ? (
            <label className="field student-history-select">
              <span>处理版本</span>
              <select aria-label="处理版本" value={historyRevisionId || "current"} onChange={(event) => {setHistoryRevisionId(event.target.value === "current" ? "" : event.target.value); setPageIndex(0); setAlignmentEditorOpen(false);}}>
                <option value="current">当前版本</option>
                {detail.data.processingHistory.filter((revision) => !revision.isCurrent).map((revision) => <option key={revision.id} value={revision.id}>历史 R{revision.revisionNumber} · {revision.source === "legacy" ? "旧版" : revision.status} · 识别 {revision.responseCount ?? 0} · 评分 {revision.gradingResultCount ?? 0} · 产物 {revision.artifactCount ?? 0}</option>)}
              </select>
              {detail.data.isHistoricalView ? <small>正在查看历史版本：仅供核查，不能作为当前批改输入。</small> : null}
            </label>
          ) : null}
          {detail.isLoading ? <div className="loading-page">正在读取学生原图…</div> : detail.error ? <div className="alert alert--error">{detail.error.message}</div> : currentPage ? <>
            <div className="student-viewer__meta">
              <div className="student-viewer__meta-copy">
                <span>上传图片 {currentPage.pageNumber} · 对应空白卷第 {currentPage.templatePageNumber ?? "?"} 页 · 对齐 {currentPage.alignment.quality == null ? "—" : `${Math.round(currentPage.alignment.quality * 100)}%`} · {pageRegions.length} 个题框片段</span>
                <small aria-label="映射版本">题框集 {mappingProvenance.frameSet} · 配准修订 {mappingProvenance.alignmentRevision} · 处理修订 {mappingProvenance.processingRevision}</small>
              </div>
              <div className="student-viewer__meta-actions">
                {detail.data?.questionRegionState.missingQuestionIds.length ? <span className="warning-text">{detail.data.questionRegionState.errorMessage || `${detail.data.questionRegionState.missingQuestionIds.length} 题未出现在已上传页面`}</span> : null}
                <button
                  type="button"
                  className="button"
                  onClick={() => setAlignmentEditorOpen((open) => !open)}
                >
                  <ScanLine size={15} />{alignmentEditorOpen ? "返回题框查看" : "校正页面配准"}
                </button>
              </div>
            </div>
            {alignmentEditorOpen && alignmentStudentPage ? (
              <StudentAlignmentEditor
                key={`${alignmentStudentPage.id}:${alignmentStudentPage.alignment.revisionNumber ?? "missing"}`}
                submissionId={selectedId}
                studentPage={alignmentStudentPage}
                templatePages={templatePages}
                blockers={mappingBlockers}
                processingStatus={detail.data?.processingRevision?.status}
                templatesLoading={templateReview.isLoading}
                templatesError={templateReview.error?.message}
                onClose={() => setAlignmentEditorOpen(false)}
                onSaved={refreshAfterAlignment}
              />
            ) : (
              <>
                {mappingBlockers.length > 0 ? (
                  <section className="student-mapping-blockers" role="alert" aria-label="映射阻断问题">
                    <strong>映射阻断</strong>
                    <ul>{mappingBlockers.map((blocker) => <li key={`${blocker.code}-${blocker.message}`}><code>{blocker.code}</code><span>{blocker.message}</span></li>)}</ul>
                  </section>
                ) : null}
                <div className="student-viewer__scroll"><StudentPageOverlay page={currentPage} regions={pageRegions} /></div>
                <footer className="student-pager"><button disabled={pageIndex === 0} onClick={() => setPageIndex((value) => value - 1)}><ChevronLeft /></button><span>{pageIndex + 1} / {detail.data?.pages.length}</span><button disabled={pageIndex + 1 >= (detail.data?.pages.length ?? 0)} onClick={() => setPageIndex((value) => value + 1)}><ChevronRight /></button></footer>
              </>
            )}
          </> : <div className="loading-page">请选择一份学生答卷</div>}
        </main>
      </div>
      {deleting && <ConfirmDeleteSubmissionDialog name={deleting.student_name || deleting.student_identifier || deleting.original_name || "未命名学生"} busy={remove.isPending} error={deleteError} onCancel={() => {if (!remove.isPending) {setDeleting(null); setDeleteError("");}}} onConfirm={() => remove.mutate(deleting.id)} />}
    </section>
  );
}
