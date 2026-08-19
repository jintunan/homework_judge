import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Download,
  FileText,
  Maximize2,
  RefreshCw,
  TriangleAlert,
  ZoomIn,
  ZoomOut
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  GradingArtifact,
  FillBlankConfigErrorDetails,
  GradingQuestionResult,
  GradingReviewItem,
  GradingRun,
  StudentSubmissionDetail
} from "@shared/contracts";
import {
  ApiError,
  api,
  correctGradingBlank,
  downloadArtifact,
  previewArtifact,
  resolveGradingReview,
  type GradingBlankResult,
  type GradingQuestionDetail
} from "@/lib/api";
import { GradingPageOverlay } from "./GradingPageOverlay";
import { calculatePageViewport, clampPageZoom, type PageViewMode } from "./page-viewport";
import { ActionFeedback } from "@/components/ActionFeedback";

const processing = new Set([
  "queued", "prechecking", "grading", "auditing",
  "generating_annotation", "generating_report"
]);

const stageText: Record<string, string> = {
  queued: "等待开始",
  prechecking: "检查评分条件",
  grading: "逐题批改",
  auditing: "检查评分一致性",
  needs_review: "等待教师复核",
  generating_annotation: "生成批注试卷",
  generating_report: "生成错题分析",
  completed: "批改完成",
  failed: "处理失败"
};

const typeText: Record<string, string> = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  fill_blank: "填空题",
  calculation: "计算题"
};

function scoreState(item: GradingQuestionResult) {
  if (item.status === "needs_review") return "review";
  const score = Number(item.finalScore ?? 0);
  const max = Number(item.maxScore);
  if (score >= max) return "correct";
  if (score > 0) return "partial";
  return "wrong";
}

export function questionScoreText(item: Pick<GradingQuestionResult, "finalScore" | "maxScore">) {
  return `${item.finalScore ?? "待复核"}/${item.maxScore}`;
}

function GradingProgress({run}: {run: GradingRun}) {
  const total = Math.max(0, run.progress.total);
  const current = Math.max(0, Math.min(run.progress.current, total || run.progress.current));
  const completedTerminal = ["completed", "needs_review"].includes(run.status);
  let percentage = total > 0
    ? Math.min(100, current / total * 100)
    : completedTerminal ? 100 : 8;
  // A failed artifact stage may follow 100% question scoring; cap the visual
  // progress so failure is never presented as a successfully completed run.
  if (run.status === "failed") percentage = Math.min(99, percentage);
  const progressText = run.status === "needs_review"
    ? `自动批改完成，${run.openReviewCount} 项待复核`
    : run.status === "completed"
      ? "批改完成"
      : run.status === "failed"
        ? `批改失败${run.lastSuccessfulStage ? ` · 已完成 ${run.lastSuccessfulStage}` : ""}`
        : stageText[run.status] ?? run.stage;
  return <div
    className={`grading-progress is-${run.status}`}
    role="progressbar"
    aria-label="批改进度"
    aria-valuemin={0}
    aria-valuenow={percentage}
    aria-valuemax={100}
  >
    <span style={{width: `${percentage}%`}} />
    <strong>{progressText}</strong>
    <small>{total ? `${current}/${total} · ${Math.round(percentage)}%` : `${Math.round(percentage)}%`}</small>
  </div>;
}

function fillBlankBlockers(details: unknown): FillBlankConfigErrorDetails["questions"] {
  if (!details || typeof details !== "object" || !("questions" in details)) return [];
  const questions = (details as {questions?: unknown}).questions;
  if (!Array.isArray(questions)) return [];
  return questions.filter((item): item is FillBlankConfigErrorDetails["questions"][number] => (
    Boolean(item) && typeof item === "object" &&
    typeof (item as {questionNumber?: unknown}).questionNumber === "string" &&
    Array.isArray((item as {reasonCodes?: unknown}).reasonCodes)
  ));
}

export function FillBlankConfigReviewNotice({error, taskId}: {error: ApiError; taskId: string}) {
  if (error.code !== "FILL_BLANK_CONFIG_REVIEW_REQUIRED") return null;
  const blockers = fillBlankBlockers(error.details);
  return <div className="alert alert--warning" role="alert">
    <strong>请先逐空检查并保存填空题配置</strong>
    {blockers.length > 0 && <p>需要处理：第 {blockers.map((item) => item.questionNumber).join("、")} 题</p>}
    <Link className="button" to={`/tasks/${taskId}/review`}>返回题目复核页</Link>
  </div>;
}

type BlankCorrection =
  | {recognizedText: string}
  | {finalStatus: "correct" | "incorrect"};

interface FillBlankReviewCardsProps {
  questionResultId: string;
  gradingRevision: number | null;
  frameSetId?: string | null;
  blankConfigVersionId?: string | null;
  processingRevisionId?: string | null;
  blankResults: GradingBlankResult[];
  teacherReason: string;
  savingBlankKey: string | null;
  onCorrect: (blankKey: string, correction: BlankCorrection) => void;
}

const blankStatusText: Record<GradingBlankResult["status"], string> = {
  correct: "正确",
  incorrect: "错误",
  needs_review: "待复核"
};

function blankStudentAnswer(blank: GradingBlankResult): string {
  return blank.studentAnswer ?? blank.recognizedAnswer ?? "";
}

function hasBlankVersions(
  blank: GradingBlankResult,
  gradingRevision: number | null,
  frameSetId?: string | null,
  blankConfigVersionId?: string | null,
  processingRevisionId?: string | null
): boolean {
  return Boolean(
    (blank.frameSetId ?? frameSetId) &&
    (blank.blankConfigVersionId ?? blankConfigVersionId) &&
    (blank.processingRevisionId ?? processingRevisionId) &&
    (blank.gradingRevision ?? gradingRevision) !== null
  );
}

export function FillBlankReviewCards({
  questionResultId,
  gradingRevision,
  frameSetId,
  blankConfigVersionId,
  processingRevisionId,
  blankResults,
  teacherReason,
  savingBlankKey,
  onCorrect
}: FillBlankReviewCardsProps) {
  const [recognizedCorrections, setRecognizedCorrections] = useState<Record<string, string>>(() => (
    Object.fromEntries(blankResults.map((blank) => [blank.blankKey, blankStudentAnswer(blank)]))
  ));
  const [finalStatuses, setFinalStatuses] = useState<Record<string, "" | "correct" | "incorrect">>({});
  const hasMissingVersions = blankResults.some((blank) => !hasBlankVersions(
    blank,
    gradingRevision,
    frameSetId,
    blankConfigVersionId,
    processingRevisionId
  ));

  return <div className="grading-blank-review-list" aria-label={`题目 ${questionResultId} 逐空复核`}>
    {hasMissingVersions ? <div className="grading-version-warning" role="alert">
      版本信息不完整：这是旧版或失效的逐空结果，只能查看；请重新处理学生答卷后再修正。
    </div> : null}
    {blankResults.map((blank) => {
      const effectiveGradingRevision = blank.gradingRevision ?? gradingRevision;
      const effectiveFrameSetId = blank.frameSetId ?? frameSetId ?? null;
      const effectiveBlankConfigVersionId = blank.blankConfigVersionId ?? blankConfigVersionId ?? null;
      const effectiveProcessingRevisionId = blank.processingRevisionId ?? processingRevisionId ?? null;
      const versionsReady = hasBlankVersions(
        blank,
        gradingRevision,
        frameSetId,
        blankConfigVersionId,
        processingRevisionId
      );
      const saving = savingBlankKey === blank.blankKey;
      const recognizedValue = recognizedCorrections[blank.blankKey] ?? blankStudentAnswer(blank);
      const finalStatus = finalStatuses[blank.blankKey] ?? "";
      const decisionReason = typeof blank.decision?.reason === "string" ? blank.decision.reason : "";
      return <article
        className={`grading-blank-review-card is-${blank.status}`}
        data-testid={`blank-review-${blank.blankKey}`}
        key={blank.blankKey}
      >
        <header>
          <strong>{blank.blankKey}</strong>
          <span>{blankStatusText[blank.status]}</span>
          <b>{blank.score} / {blank.maxScore}</b>
        </header>
        <dl>
          <div><dt>学生答案</dt><dd>{blankStudentAnswer(blank) || "（未作答）"}</dd></div>
          <div><dt>标准答案</dt><dd>{blank.standardAnswers.length > 0 ? blank.standardAnswers.join(" / ") : "（缺失）"}</dd></div>
          <div><dt>当前判定</dt><dd>{blankStatusText[blank.status]}{decisionReason ? `：${decisionReason}` : ""}</dd></div>
          <div><dt>本空满分</dt><dd>{blank.maxScore}</dd></div>
        </dl>
        {blank.reviewReasons.length > 0 ? <div className="grading-blank-review-reasons">
          <strong>needs_review 原因</strong>
          <ul>{blank.reviewReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div> : null}
        <div className="grading-blank-versions" aria-label={`${blank.blankKey} 版本`}>
          <span>题框 {effectiveFrameSetId ?? "—"}</span>
          <span>配置 {effectiveBlankConfigVersionId ?? "—"}</span>
          <span>处理 {effectiveProcessingRevisionId ?? "—"}</span>
          <span>评分 {effectiveGradingRevision === null ? "—" : `R${effectiveGradingRevision}`}</span>
        </div>
        <div className="grading-blank-correction">
          <label htmlFor={`recognized-${questionResultId}-${blank.blankKey}`}>{blank.blankKey} 学生答案修正</label>
          <input
            id={`recognized-${questionResultId}-${blank.blankKey}`}
            value={recognizedValue}
            disabled={!versionsReady || saving}
            onChange={(event) => setRecognizedCorrections((current) => ({
              ...current,
              [blank.blankKey]: event.target.value
            }))}
          />
          <button
            type="button"
            className="button"
            disabled={!versionsReady || saving || !teacherReason.trim()}
            onClick={() => onCorrect(blank.blankKey, {recognizedText: recognizedValue})}
          >{saving ? "正在保存…" : `${blank.blankKey} 按修正答案重判`}</button>
          <label htmlFor={`status-${questionResultId}-${blank.blankKey}`}>{blank.blankKey} 最终判定</label>
          <select
            id={`status-${questionResultId}-${blank.blankKey}`}
            value={finalStatus}
            disabled={!versionsReady || saving}
            onChange={(event) => setFinalStatuses((current) => ({
              ...current,
              [blank.blankKey]: event.target.value as "" | "correct" | "incorrect"
            }))}
          >
            <option value="">请选择</option>
            <option value="correct">正确</option>
            <option value="incorrect">错误</option>
          </select>
          <button
            type="button"
            className="button"
            disabled={!versionsReady || saving || !finalStatus || !teacherReason.trim()}
            onClick={() => finalStatus && onCorrect(blank.blankKey, {finalStatus})}
          >{saving ? "正在保存…" : `${blank.blankKey} 覆盖最终判定`}</button>
        </div>
      </article>;
    })}
  </div>;
}

export function GradingWorkspacePage() {
  const {taskId = "", submissionId = ""} = useParams();
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [filter, setFilter] = useState<"all" | "review" | "wrong" | "partial">("all");
  const [showQuestionFrames, setShowQuestionFrames] = useState(true);
  const [showBlankAnchors, setShowBlankAnchors] = useState(false);
  const [showEvidence, setShowEvidence] = useState(true);
  const [showMarks, setShowMarks] = useState(true);
  const [viewMode, setViewMode] = useState<PageViewMode>("fit-page");
  const [zoom, setZoom] = useState(1);
  const [focused, setFocused] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"questions" | "paper" | "detail">("paper");
  const [selectedEvidenceId, setSelectedEvidenceId] = useState("");
  const [paperViewport, setPaperViewport] = useState({width: 0, height: 0});
  const paperViewportRef = useRef<HTMLDivElement>(null);
  const [teacherReason, setTeacherReason] = useState("已查看学生原图，确认本题判定");
  const [recognizedText, setRecognizedText] = useState("");
  const [decisionOverrides, setDecisionOverrides] = useState<Record<string, string>>({});
  const [editingLocation, setEditingLocation] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const submission = useQuery({
    queryKey: ["student-submission", submissionId],
    queryFn: () => api<StudentSubmissionDetail>(`/student-submissions/${submissionId}`),
    enabled: Boolean(submissionId)
  });
  const runs = useQuery({
    queryKey: ["grading-runs", submissionId],
    queryFn: () => api<GradingRun[]>(`/student-submissions/${submissionId}/grading-runs`),
    enabled: Boolean(submissionId),
    refetchInterval: (query) => {
      const values = query.state.data;
      return !values?.length || values.some((item) => processing.has(item.status)) ? 1200 : false;
    }
  });
  useEffect(() => {
    if (!runId && runs.data?.[0]) setRunId(runs.data[0].id);
  }, [runId, runs.data]);
  const run = useQuery({
    queryKey: ["grading-run", runId],
    queryFn: () => api<GradingRun>(`/grading-runs/${runId}`),
    enabled: Boolean(runId),
    refetchInterval: (query) => processing.has(query.state.data?.status ?? "") ? 1200 : false
  });
  const questions = useQuery({
    queryKey: ["grading-questions", runId, run.data?.updatedAt],
    queryFn: () => api<GradingQuestionResult[]>(`/grading-runs/${runId}/questions`),
    enabled: Boolean(runId),
    refetchInterval: processing.has(run.data?.status ?? "") ? 1500 : false
  });
  useEffect(() => {
    if (!questionId && questions.data?.[0]) setQuestionId(questions.data[0].questionId);
  }, [questionId, questions.data]);
  const detail = useQuery({
    queryKey: ["grading-question", runId, questionId, run.data?.updatedAt],
    queryFn: () => api<GradingQuestionDetail>(`/grading-runs/${runId}/questions/${questionId}`),
    enabled: Boolean(runId && questionId)
  });
  const reviews = useQuery({
    queryKey: ["grading-reviews", runId, run.data?.updatedAt],
    queryFn: () => api<GradingReviewItem[]>(`/grading-runs/${runId}/review-items`),
    enabled: Boolean(runId)
  });
  const artifacts = useQuery({
    queryKey: ["grading-artifacts", runId, run.data?.updatedAt],
    queryFn: () => api<GradingArtifact[]>(`/grading-runs/${runId}/artifacts`),
    enabled: Boolean(runId)
  });
  const refreshRunData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({queryKey: ["grading-run", runId]}),
      queryClient.invalidateQueries({queryKey: ["grading-runs", submissionId]}),
      queryClient.invalidateQueries({queryKey: ["grading-questions", runId]}),
      queryClient.invalidateQueries({queryKey: ["grading-question", runId]}),
      queryClient.invalidateQueries({queryKey: ["grading-reviews", runId]}),
      queryClient.invalidateQueries({queryKey: ["grading-artifacts", runId]})
    ]);
  };

  const retry = useMutation({
    mutationFn: () => api(`/grading-runs/${runId}/retry`, {method: "POST"}),
    onSuccess: async () => {setError(""); setMessage("已从保存位置继续处理"); await refreshRunData();},
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "重试失败");}
  });
  const regenerate = useMutation({
    mutationFn: () => api(`/grading-runs/${runId}/regenerate`, {method: "POST"}),
    onSuccess: async () => {setError(""); setMessage("正在重新生成批注与报告"); await refreshRunData();},
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "生成失败");}
  });
  const reviewForQuestion = reviews.data?.find((item) => item.questionId === questionId);
  useEffect(() => {
    setRecognizedText("");
    setDecisionOverrides({});
  }, [questionId]);
  const resolve = useMutation({
    mutationFn: (mode: "confirm" | "override") => {
      if (!reviewForQuestion) throw new Error("没有待复核项目");
      if (mode === "confirm") {
        return resolveGradingReview(reviewForQuestion.id, {action: "confirm", teacherReason});
      }
      if (detail.data?.questionType === "fill_blank") {
        return resolveGradingReview(reviewForQuestion.id, {
          action: "override",
          teacherReason,
          blankDecisions: Object.entries(decisionOverrides).filter(([, value]) => value).map(([blankKey, status]) => ({blankKey, status: status as "correct" | "incorrect"}))
        });
      }
      if (detail.data?.questionType === "calculation") {
        return resolveGradingReview(reviewForQuestion.id, {
          action: "override",
          teacherReason,
          pointDecisions: Object.entries(decisionOverrides).filter(([, value]) => value).map(([pointKey, directStatus]) => ({pointKey, directStatus: directStatus as "satisfied" | "partial" | "failed"}))
        });
      }
      return resolveGradingReview(reviewForQuestion.id, {action: "override", teacherReason, recognizedText});
    },
    onSuccess: async (value) => {
      setError("");
      const nextReview = value.remainingReasons.length
        ? undefined
        : reviews.data?.find((item) => item.questionId !== questionId);
      setMessage(value.remainingReasons.length
        ? `复核结果已保存，本题还有 ${value.remainingReasons.length} 项需要处理`
        : nextReview
        ? `复核结果已保存，已定位到下一道待复核题`
        : "复核结果已保存，正在检查剩余项目并生成结果文件");
      await refreshRunData();
      if (nextReview) setQuestionId(nextReview.questionId);
    },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "复核保存失败");}
  });
  const correctBlank = useMutation({
    mutationFn: ({blankKey, correction}: {blankKey: string; correction: BlankCorrection}) => {
      const blank = detail.data?.blankResults?.find((item) => item.blankKey === blankKey);
      if (!detail.data || !blank) throw new Error("当前逐空结果不存在，请刷新后重试");
      const expectedGradingRevision = blank.gradingRevision ?? detail.data.gradingRevision ?? null;
      const frameSetId = blank.frameSetId ?? detail.data.frameSetId ?? null;
      const blankConfigVersionId = blank.blankConfigVersionId ?? detail.data.blankConfigVersionId ?? null;
      const processingRevisionId = blank.processingRevisionId ?? detail.data.processingRevisionId ?? null;
      if (
        !frameSetId ||
        !blankConfigVersionId ||
        !processingRevisionId ||
        expectedGradingRevision === null
      ) {
        throw new Error("当前逐空结果版本信息不完整，请重新处理学生答卷后再修正");
      }
      const versions = {
        teacherReason,
        expectedGradingRevision,
        frameSetId,
        blankConfigVersionId,
        processingRevisionId
      };
      return "recognizedText" in correction
        ? correctGradingBlank(detail.data.id, blankKey, {...versions, recognizedText: correction.recognizedText})
        : correctGradingBlank(detail.data.id, blankKey, {...versions, finalStatus: correction.finalStatus});
    },
    onSuccess: async (value) => {
      setError("");
      setMessage(`${value.blankKey} 修正已保存，本题评分版本已更新`);
      await refreshRunData();
    },
    onError: (reason) => {
      setMessage("");
      setError(reason instanceof Error ? reason.message : "逐空修正保存失败");
    }
  });

  const filtered = useMemo(() => (questions.data ?? []).filter((item) => {
    const state = scoreState(item);
    return filter === "all" || state === filter || (filter === "wrong" && state === "wrong");
  }), [filter, questions.data]);
  const capturedQuestionFrames = detail.data?.questionFrames;
  const usesLegacyQuestionFrameFallback = Boolean(
    detail.data && capturedQuestionFrames === undefined
  );
  const questionFrames = useMemo(() => {
    if (capturedQuestionFrames !== undefined) return capturedQuestionFrames;
    if (!usesLegacyQuestionFrameFallback) return [];

    // Legacy runs did not persist their own frame mapping. This is only a
    // clearly-labelled viewing fallback, never evidence about the old run.
    return (submission.data?.questionRegions ?? [])
      .filter((region) => region.questionId === questionId)
      .map((region) => ({
        id: region.id,
        questionId: region.questionId,
        pageId: region.studentPageId,
        polygon: region.studentPolygon
      }));
  }, [
    capturedQuestionFrames,
    questionId,
    submission.data?.questionRegions,
    usesLegacyQuestionFrameFallback
  ]);
  useEffect(() => {
    const element = paperViewportRef.current;
    if (!element) return;
    const updateSize = () => {
      const bounds = element.getBoundingClientRect();
      setPaperViewport({width: bounds.width, height: bounds.height});
    };
    updateSize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [runId]);

  const evidencePageId = detail.data?.evidence?.[0]?.page_id;
  useEffect(() => {
    setSelectedEvidenceId(detail.data?.evidence?.[0]?.region_id ?? "");
    if (!evidencePageId || !submission.data?.pages.length) return;
    const evidencePageIndex = submission.data.pages.findIndex((page) => page.id === evidencePageId);
    if (evidencePageIndex >= 0) setPageIndex(evidencePageIndex);
  }, [detail.data?.questionId, evidencePageId, submission.data?.pages]);

  useEffect(() => {
    const pageCount = submission.data?.pages.length ?? 0;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "ArrowLeft" && pageIndex > 0) {
        event.preventDefault();
        setPageIndex((value) => value - 1);
      }
      if (event.key === "ArrowRight" && pageIndex + 1 < pageCount) {
        event.preventDefault();
        setPageIndex((value) => value + 1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pageIndex, submission.data?.pages.length]);

  const currentPage = submission.data?.pages[pageIndex];
  const pageViewport = useMemo(() => {
    if (!currentPage) return {scale: 1, width: 1, height: 1, overflowX: false, overflowY: false};
    return calculatePageViewport({
      pageWidth: currentPage.width,
      pageHeight: currentPage.height,
      viewportWidth: paperViewport.width,
      viewportHeight: paperViewport.height,
      mode: viewMode,
      zoom,
      padding: 32
    });
  }, [currentPage, paperViewport, viewMode, zoom]);
  const currentEvidence = detail.data?.evidence?.find((item) => item.page_id === currentPage?.id);
  const updateLocation = useMutation({
    mutationFn: async (box: {x: number; y: number; width: number; height: number}) => {
      if (!detail.data || !currentPage || !currentEvidence) throw new Error("当前页没有本题作答证据");
      await api(`/grading-question-results/${detail.data.id}/error-location`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          teacherReason,
          errorLocations: [{
            pageId: currentPage.id,
            regionId: currentEvidence.region_id,
            box,
            recognizedText: currentEvidence.recognized_text
          }]
        })
      });
      if ((run.data?.openReviewCount ?? 0) === 0) {
        await api(`/grading-runs/${runId}/regenerate`, {method: "POST"});
      }
    },
    onSuccess: async () => {
      setEditingLocation(false);
      setMessage((run.data?.openReviewCount ?? 0) === 0
        ? "错误位置已保存，正在重新生成批注和报告"
        : "错误位置已保存；完成本题复核后会生成新批注");
      await Promise.all([
        queryClient.invalidateQueries({queryKey: ["grading-run", runId]}),
        queryClient.invalidateQueries({queryKey: ["grading-question", runId, questionId]}),
        queryClient.invalidateQueries({queryKey: ["grading-artifacts", runId]})
      ]);
    },
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "错误位置保存失败");}
  });
  const annotation = artifacts.data?.find((item) => item.type === "annotation" && item.status === "current" && item.resultRevision === run.data?.resultRevision);
  const report = artifacts.data?.find((item) => item.type === "error_report" && item.status === "current" && item.resultRevision === run.data?.resultRevision);
  const artifactAction = useMutation({
    mutationFn: async ({artifact, mode}: {artifact: GradingArtifact; mode: "preview" | "download"}) => {
      if (mode === "preview") return previewArtifact(artifact.previewUrl);
      return downloadArtifact(artifact.downloadUrl, artifact.type === "annotation" ? "批注试卷.pdf" : "错题报告.pdf");
    },
    onSuccess: (_value, variables) => {setError(""); setMessage(variables.mode === "preview" ? "结果文件已打开预览" : "结果文件已开始下载");},
    onError: (reason) => {setMessage(""); setError(reason instanceof Error ? reason.message : "结果文件暂时不可用");}
  });
  const marks = annotation?.preview.marks ?? [];
  const evidence = detail.data?.evidence ?? [];
  const blankAnchors = detail.data?.blankAnchors ?? [];
  const missingBlankAnchorCount = blankAnchors.filter((anchor) => (
    !anchor.studentPolygon || anchor.studentPolygon.length < 3
  )).length;
  const selectedState = detail.data ? scoreState(detail.data) : "review";
  const standardAnswerText = detail.data?.question?.standardAnswer.answer
    || detail.data?.question?.standardAnswer.options?.join(" / ")
    || "—";
  const gradingReasonText = detail.data?.decisions?.map((item) => item.reason).filter(Boolean).join("；")
    || detail.data?.reviewReasons.join("；")
    || "已按当前评分规则判定";
  const gradingToolText = detail.data?.toolConclusions?.map((item) => item.tool).filter(Boolean).join(" / ")
    || typeText[detail.data?.questionType ?? ""]
    || "规则评分";

  return (
    <section className="grading-workspace">
      <header className="grading-head">
        <div>
          <Link className="back-link" to={`/tasks/${taskId}/students`}><ChevronLeft size={16} />返回学生答卷</Link>
          <h1>作业批改工作台</h1>
          <p>{submission.data?.submission.student_name || "未命名学生"} · {stageText[run.data?.status ?? ""] ?? "尚未开始"}</p>
        </div>
        <div className="grading-head__score">
          <span>总分</span><strong>{run.data?.totalScore ?? "—"}<small> / {run.data?.maxScore ?? "—"}</small></strong>
          {run.data?.status === "failed" && <button type="button" className="button button--primary" disabled={retry.isPending || !run.data.retryable} title={!run.data.retryable ? "当前失败不可自动重试，请先处理错误原因" : undefined} onClick={() => {setMessage(""); setError(""); retry.mutate();}}><RefreshCw size={15} />{retry.isPending ? "正在继续…" : run.data.lastSuccessfulStage && ["auditing", "generating_annotation", "generating_report"].includes(run.data.lastSuccessfulStage) ? "继续生成结果" : "继续处理"}</button>}
          {run.data?.status === "completed" && <button type="button" className="button" disabled={regenerate.isPending} onClick={() => {setMessage(""); setError(""); regenerate.mutate();}}><RefreshCw size={15} />{regenerate.isPending ? "正在启动…" : "重新生成文件"}</button>}
        </div>
      </header>
      {run.data?.isStale && <div className="alert alert--warning">题目集合已发生变化，本次评分和生成文件已过期，请重新处理学生答卷后创建新的评分运行。</div>}
      <div className="students-message"><ActionFeedback message={message} error={error || run.data?.error?.message || submission.data?.submission.auto_grading_error_message || undefined} /></div>
      {run.data && <GradingProgress run={run.data} />}
      {!runId ? <div className="grading-empty"><RefreshCw className="spin" size={38} /><h2>正在等待自动批改</h2><p>答卷完成可靠配准与题框映射后会直接开始批改，无需教师预审或手动启动。</p></div> : (<>
        <nav className="grading-mobile-tabs" aria-label="批改工作台面板">
          <button type="button" className={mobilePanel === "questions" ? "active" : ""} onClick={() => setMobilePanel("questions")}>题目</button>
          <button type="button" className={mobilePanel === "paper" ? "active" : ""} onClick={() => setMobilePanel("paper")}>试卷</button>
          <button type="button" className={mobilePanel === "detail" ? "active" : ""} onClick={() => setMobilePanel("detail")}>证据与判分</button>
        </nav>
        <div className={`grading-layout ${focused ? "is-focused" : ""} is-panel-${mobilePanel}`}>
          <aside className="grading-questions">
            <div className="grading-filters">
              {(["all", "review", "wrong", "partial"] as const).map((value) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "全部" : value === "review" ? "待复核" : value === "wrong" ? "错题" : "部分分"}</button>)}
            </div>
            <div className="grading-question-list">
              {filtered.map((item) => {
                const state = scoreState(item);
                return <button key={item.questionId} className={`${questionId === item.questionId ? "active" : ""} is-${state}`} onClick={() => {setQuestionId(item.questionId); setMobilePanel("paper");}}><span>{state === "correct" ? <Check /> : state === "partial" ? <TriangleAlert /> : <CircleAlert />}</span><strong>第 {item.questionNumber} 题<small>{typeText[item.questionType]}</small></strong><b>{questionScoreText(item)}</b></button>;
              })}
              {!questions.isLoading && filtered.length === 0 && <p className="no-results">当前筛选没有题目</p>}
            </div>
          </aside>

          <main className="grading-paper">
            <div className="grading-layer-controls">
              <label><input type="checkbox" checked={showQuestionFrames} onChange={(event) => setShowQuestionFrames(event.target.checked)} /><span className="grading-layer-legend grading-layer-legend--frame" aria-hidden="true" />完整题框</label>
              <label><input type="checkbox" checked={showBlankAnchors} onChange={(event) => setShowBlankAnchors(event.target.checked)} /><span className="grading-layer-legend grading-layer-legend--anchor" aria-hidden="true" />空位锚点</label>
              <label><input type="checkbox" checked={showEvidence} onChange={(event) => setShowEvidence(event.target.checked)} /><span className="grading-layer-legend grading-layer-legend--evidence" aria-hidden="true" />识别证据</label>
              <label><input type="checkbox" checked={showMarks} onChange={(event) => setShowMarks(event.target.checked)} />最终批注</label>
              <span className="grading-control-divider" aria-hidden="true" />
              <button type="button" className={viewMode === "fit-page" ? "active" : ""} onClick={() => {setViewMode("fit-page"); setZoom(1);}} title="让完整试卷页始终显示在当前窗口"><Maximize2 size={14} />整页</button>
              <button type="button" className={viewMode === "fit-width" ? "active" : ""} onClick={() => {setViewMode("fit-width"); setZoom(1);}} title="按可用宽度显示">适宽</button>
              <button type="button" className={viewMode === "actual" ? "active" : ""} onClick={() => {setViewMode("actual"); setZoom(1);}} title="按原始像素显示">1:1</button>
              <button type="button" aria-label="缩小试卷" disabled={zoom <= .25} onClick={() => setZoom((value) => clampPageZoom(value - .1))}><ZoomOut size={14} /></button>
              <small className="grading-zoom-value">{Math.round(zoom * 100)}%</small>
              <button type="button" aria-label="放大试卷" disabled={zoom >= 3} onClick={() => setZoom((value) => clampPageZoom(value + .1))}><ZoomIn size={14} /></button>
              <button type="button" className={focused ? "active" : ""} aria-pressed={focused} onClick={() => setFocused((value) => !value)}>{focused ? "退出聚焦" : "聚焦试卷"}</button>
              {detail.data && selectedState !== "correct" && <button type="button" className={editingLocation ? "active" : ""} disabled={!currentEvidence || updateLocation.isPending} title={!currentEvidence ? "当前页没有本题作答证据，请先切换到有作答的页面" : undefined} onClick={() => setEditingLocation((value) => !value)}>{editingLocation ? "拖框标出错误位置" : updateLocation.isPending ? "正在保存位置…" : "调整错误位置"}</button>}
            </div>
            <div ref={paperViewportRef} className={`grading-paper__scroll is-${viewMode} ${pageViewport.overflowX || pageViewport.overflowY ? "is-zoomed" : ""}`}>
              {detail.data && (
                usesLegacyQuestionFrameFallback ||
                questionFrames.length === 0 ||
                (showBlankAnchors && (blankAnchors.length === 0 || missingBlankAnchorCount > 0))
              ) ? <div className="grading-overlay-warning" role="alert">
                {usesLegacyQuestionFrameFallback ? <><span>当前批改记录未保存历史题框，显示的是当前题框，仅供对照，不能作为该次评分事实。</span>{" "}</> : null}
                {showBlankAnchors && missingBlankAnchorCount > 0 ? <><span>{`有 ${missingBlankAnchorCount} 个空位缺少学生页多边形锚点，已安全隐藏；页面不会使用矩形替代或自行推算。`}</span>{" "}</> : null}
                {questionFrames.length === 0 ? "当前结果缺少已确认完整题框映射，仅供查看；请重新处理学生答卷。" : ""}
                {questionFrames.length === 0 && showBlankAnchors && blankAnchors.length === 0 ? " " : ""}
                {showBlankAnchors && blankAnchors.length === 0 ? "当前结果未提供映射后的空位锚点，页面不会自行推算或扩框。" : ""}
              </div> : null}
              {currentPage ? <GradingPageOverlay page={currentPage} marks={marks} evidence={evidence} questionFrames={questionFrames} blankAnchors={blankAnchors} showQuestionFrames={showQuestionFrames} showBlankAnchors={showBlankAnchors} showEvidence={showEvidence} showMarks={showMarks} drawingEnabled={editingLocation} selectedEvidenceRegionId={selectedEvidenceId} scale={pageViewport.scale} onBoxDraw={(box) => updateLocation.mutate(box)} /> : <p>正在读取学生原图…</p>}
            </div>
            <footer className="student-pager"><button disabled={pageIndex === 0} onClick={() => setPageIndex((value) => value - 1)}><ChevronLeft /></button><span>{pageIndex + 1} / {submission.data?.pages.length ?? 0}</span><button disabled={pageIndex + 1 >= (submission.data?.pages.length ?? 0)} onClick={() => setPageIndex((value) => value + 1)}><ChevronRight /></button></footer>
          </main>

          <aside className="grading-detail">
            {detail.data ? <>
              <div className={`grading-result-head is-${selectedState}`}><span>第 {detail.data.questionNumber} 题 · {typeText[detail.data.questionType]}</span><strong>{questionScoreText(detail.data)}</strong></div>
              <section className="grading-evidence-panel" aria-label="批改证据">
                <div className="grading-section-title"><h3>批改证据</h3><span>{evidence.length} 项</span></div>
                <dl className="grading-evidence-summary">
                  <div><dt>学生识别</dt><dd>{evidence.map((item) => item.recognized_text).filter(Boolean).join(" / ") || "无可靠文字转写"}</dd></div>
                  <div><dt>标准答案</dt><dd>{standardAnswerText}</dd></div>
                  <div><dt>规则/工具</dt><dd>{gradingToolText}</dd></div>
                  <div><dt>判分原因</dt><dd>{gradingReasonText}</dd></div>
                  <div><dt>本题得分</dt><dd>{questionScoreText(detail.data)}</dd></div>
                </dl>
                {evidence.length ? <div className="grading-evidence-list">
                  {evidence.map((item, index) => {
                    const evidencePageIndex = submission.data?.pages.findIndex((page) => page.id === item.page_id) ?? -1;
                    const pageNumber = item.pageNumber ?? (evidencePageIndex >= 0 ? submission.data?.pages[evidencePageIndex]?.pageNumber : null);
                    const previewUrl = item.previewUrl ?? `/api/grading-question-results/${detail.data.id}/evidence/${item.region_id}/preview`;
                    return <button
                      type="button"
                      className={selectedEvidenceId === item.region_id ? "active" : ""}
                      key={`${item.region_id}-${index}`}
                      onClick={() => {
                        setSelectedEvidenceId(item.region_id);
                        if (evidencePageIndex >= 0) setPageIndex(evidencePageIndex);
                      }}
                    >
                      <img src={previewUrl} alt={`第 ${pageNumber ?? "?"} 页批改证据`} loading="lazy" />
                      <span><strong>第 {pageNumber ?? "?"} 页</strong><small>{item.recognized_text || "该区域未生成文字转写"}</small></span>
                    </button>;
                  })}
                </div> : <div className="grading-evidence-empty" role="alert"><CircleAlert size={17} /><span>未生成可展示的批改证据，本题已进入复核，不会静默给出最终判定。</span></div>}
              </section>
              {detail.data.questionType === "fill_blank" ? <section className="grading-blank-section">
                <h3>逐空判分与复核</h3>
                <label className="grading-blank-teacher-reason">逐空修正说明<textarea value={teacherReason} onChange={(event) => setTeacherReason(event.target.value)} /></label>
                {(detail.data.blankResults?.length ?? 0) > 0 ? <FillBlankReviewCards
                  key={`${detail.data.id}:${detail.data.gradingRevision ?? "legacy"}`}
                  questionResultId={detail.data.id}
                  gradingRevision={detail.data.gradingRevision ?? null}
                  frameSetId={detail.data.frameSetId ?? null}
                  blankConfigVersionId={detail.data.blankConfigVersionId ?? null}
                  processingRevisionId={detail.data.processingRevisionId ?? null}
                  blankResults={detail.data.blankResults ?? []}
                  teacherReason={teacherReason}
                  savingBlankKey={correctBlank.isPending ? correctBlank.variables?.blankKey ?? null : null}
                  onCorrect={(blankKey, correction) => {
                    setMessage("");
                    setError("");
                    correctBlank.mutate({blankKey, correction});
                  }}
                /> : <div className="grading-version-warning" role="alert">当前结果没有逐空明细，属于旧版或未完成数据，请重新处理学生答卷。</div>}
              </section> : <section><h3>判分说明</h3>{detail.data.decisions?.map((item) => <div className="grading-decision" key={item.key}><span>{item.key}</span><strong>{item.score}/{item.max_score}</strong><p>{item.reason || "已按规则判定"}{item.blocked_by ? `；受 ${item.blocked_by} 影响` : ""}</p></div>)}</section>}
              {detail.data.reviewReasons.length > 0 && <section className="grading-review-box"><h3>需要教师复核</h3><p>{detail.data.reviewReasons.join("、")}</p>{detail.data.questionType !== "fill_blank" && <label>复核说明<textarea value={teacherReason} onChange={(event) => setTeacherReason(event.target.value)} /></label>}{["single_choice", "multiple_choice"].includes(detail.data.questionType) && <label>修正识别答案<input value={recognizedText} onChange={(event) => setRecognizedText(event.target.value)} placeholder="例如 AC" /></label>}{detail.data.questionType === "calculation" && detail.data.decisions?.map((item) => <label key={item.key}>{item.key} 直接判定<select value={decisionOverrides[item.key] ?? ""} onChange={(event) => setDecisionOverrides((current) => ({...current, [item.key]: event.target.value}))}><option value="">不修改</option><option value="satisfied">完全满足（100%）</option><option value="partial">部分满足（50%）</option><option value="failed">未满足（0%）</option></select></label>)}<div><button type="button" className="button button--primary" disabled={resolve.isPending} onClick={() => {setMessage(""); setError(""); resolve.mutate("confirm");}}>{resolve.isPending ? "正在保存复核…" : "确认当前判定"}</button>{(recognizedText || Object.values(decisionOverrides).some(Boolean)) && <button type="button" className="button" disabled={resolve.isPending} onClick={() => {setMessage(""); setError(""); resolve.mutate("override");}}>按修改结果重判</button>}</div></section>}
              <section><h3>给学生的提示</h3><p>{selectedState === "correct" ? "本题作答正确，继续保持。" : selectedState === "partial" ? "从红圈标出的首个失分位置开始订正，不必重抄完整答案。" : "检查红圈位置对应的概念、选项或计算步骤，再完成一道同类题。"}</p></section>
              {(annotation || report) && <section className="grading-downloads"><h3>结果文件</h3>{annotation && <button type="button" className="button" disabled={artifactAction.isPending} onClick={() => artifactAction.mutate({artifact: annotation, mode: "preview"})}><FileText size={15} />预览批注试卷</button>}{annotation && <button type="button" className="button" disabled={artifactAction.isPending} onClick={() => artifactAction.mutate({artifact: annotation, mode: "download"})}><Download size={15} />下载批注试卷</button>}{report && <button type="button" className="button" disabled={artifactAction.isPending} onClick={() => artifactAction.mutate({artifact: report, mode: "preview"})}><FileText size={15} />预览错题报告</button>}{report && <button type="button" className="button" disabled={artifactAction.isPending} onClick={() => artifactAction.mutate({artifact: report, mode: "download"})}><Download size={15} />下载错题报告</button>}</section>}
            </> : <div className="loading-page">正在读取逐题结果…</div>}
          </aside>
        </div>
      </>)}
    </section>
  );
}
