import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent
} from "react";
import type {
  NormalizedBox,
  QuestionFrameFragment,
  QuestionFrameItem,
  QuestionFrameItemStatus
} from "@shared/contracts";
import {
  drawNormalizedRect,
  moveNormalizedRect,
  normalizedToPixels,
  resizeNormalizedRect,
  updateFragmentGeometry,
  type PixelPoint,
  type ResizeHandle
} from "./question-frame-geometry";

export interface TemplateQuestionFramePage {
  id: string;
  pageNumber: number;
  width: number;
  height: number;
  imageUrl: string;
}

export interface QuestionFrameOverlayItem {
  questionNumber: string;
  item: QuestionFrameItem;
}

export interface QuestionFrameSaveInput {
  questionId: string;
  expectedRevision: number;
  regions: QuestionFrameFragment[];
}

export interface QuestionFrameSaveResult {
  revision: number;
  regions?: QuestionFrameFragment[];
  status?: QuestionFrameItemStatus;
}

export interface QuestionFrameConfirmInput {
  questionId: string;
  expectedRevision: number;
}

export interface QuestionFrameConfirmResult {
  revision: number;
  status?: QuestionFrameItemStatus;
}

export interface QuestionFrameRerecognizeResult extends QuestionFrameSaveResult {
  teacherOverridePreserved?: boolean;
}

export interface TemplateQuestionFrameEditorProps {
  pages: TemplateQuestionFramePage[];
  questionNumber: string;
  currentItem: QuestionFrameItem;
  otherItems?: QuestionFrameOverlayItem[];
  questionConfirmationStatus?: QuestionFrameItemStatus;
  geometryBlockers?: string[];
  disabled?: boolean;
  onSave: (input: QuestionFrameSaveInput) => Promise<QuestionFrameSaveResult | void>;
  onRerecognize?: (
    input: QuestionFrameSaveInput
  ) => Promise<QuestionFrameRerecognizeResult | void>;
  onConfirm?: (input: QuestionFrameConfirmInput) => Promise<QuestionFrameConfirmResult | void>;
}

type EditorMode = "select" | "redraw";

type PointerInteraction = {
  kind: "move" | "resize" | "redraw";
  pageId: string;
  regionKey: string;
  start: PixelPoint;
  origin: QuestionFrameFragment;
  handle?: ResizeHandle;
};

const RESIZE_HANDLES: ReadonlyArray<{handle: ResizeHandle; x: number; y: number; label: string}> = [
  {handle: "nw", x: 0, y: 0, label: "左上"},
  {handle: "n", x: 0.5, y: 0, label: "上"},
  {handle: "ne", x: 1, y: 0, label: "右上"},
  {handle: "e", x: 1, y: 0.5, label: "右"},
  {handle: "se", x: 1, y: 1, label: "右下"},
  {handle: "s", x: 0.5, y: 1, label: "下"},
  {handle: "sw", x: 0, y: 1, label: "左下"},
  {handle: "w", x: 0, y: 0.5, label: "左"}
];

const cloneRegions = (regions: QuestionFrameFragment[]): QuestionFrameFragment[] =>
  regions.map((region) => ({...region, issues: [...region.issues]}));

const regionFingerprint = (regions: QuestionFrameFragment[]): string => JSON.stringify(
  [...regions]
    .sort((left, right) => left.sortOrder - right.sortOrder || left.regionKey.localeCompare(right.regionKey))
    .map(({regionKey, templatePageId, pageNumber, x, y, width, height, sortOrder, source, confidence, issues}) => ({
      regionKey,
      templatePageId,
      pageNumber,
      x,
      y,
      width,
      height,
      sortOrder,
      source,
      confidence,
      issues
    }))
);

const uniqueMessages = (messages: string[]): string[] => [...new Set(messages.filter(Boolean))];

const localGeometryBlockers = (
  regions: QuestionFrameFragment[],
  pages: TemplateQuestionFramePage[]
): string[] => {
  const blockers: string[] = [];
  if (regions.length === 0) blockers.push("题框至少需要一个片段");
  const keys = new Set<string>();
  const orders = new Set<number>();
  for (const region of regions) {
    if (keys.has(region.regionKey)) blockers.push(`片段标识重复：${region.regionKey}`);
    if (orders.has(region.sortOrder)) blockers.push(`片段顺序重复：${region.sortOrder}`);
    keys.add(region.regionKey);
    orders.add(region.sortOrder);
    const owner = pages.find((page) => page.id === region.templatePageId);
    if (!owner) blockers.push(`片段 ${region.regionKey} 找不到所属模板页`);
    if (owner && owner.pageNumber !== region.pageNumber) {
      blockers.push(`片段 ${region.regionKey} 的页码与模板页不一致`);
    }
    const values = [region.x, region.y, region.width, region.height];
    if (
      values.some((value) => !Number.isFinite(value))
      || region.x < 0
      || region.y < 0
      || region.width <= 0
      || region.height <= 0
      || region.x + region.width > 1
      || region.y + region.height > 1
    ) {
      blockers.push(`片段 ${region.regionKey} 超出页面或没有有效面积`);
    }
    blockers.push(...region.issues);
  }
  return uniqueMessages(blockers);
};

const isRevisionConflict = (error: unknown): boolean => {
  if (!error || typeof error !== "object") return false;
  const candidate = error as {status?: unknown; code?: unknown; response?: {status?: unknown}};
  return candidate.status === 409
    || candidate.response?.status === 409
    || (typeof candidate.code === "string" && (
      candidate.code.includes("REVISION_CONFLICT")
      || candidate.code.includes("SUPERSEDED")
    ));
};

const errorMessage = (error: unknown, fallback: string): string => {
  const primary = error instanceof Error && error.message ? error.message : fallback;
  if (!error || typeof error !== "object") return primary;
  const details = (error as {details?: unknown}).details;
  if (!details || typeof details !== "object") return primary;
  const issues = (details as {issues?: unknown}).issues;
  if (!Array.isArray(issues)) return primary;
  const messages = uniqueMessages(issues.flatMap((issue) => {
    if (!issue || typeof issue !== "object") return [];
    const message = (issue as {message?: unknown}).message;
    return typeof message === "string" ? [message] : [];
  }));
  return messages.length > 0 ? `${primary}：${messages.join("；")}` : primary;
};

const savedFrameFromError = (
  error: unknown,
  questionId: string
): QuestionFrameSaveResult | null => {
  if (!error || typeof error !== "object") return null;
  const details = (error as {details?: unknown}).details;
  if (!details || typeof details !== "object") return null;
  const frameSet = (details as {
    savedFrameSet?: {
      revision?: unknown;
      items?: Array<{
        questionId?: unknown;
        status?: QuestionFrameItemStatus;
        fragments?: QuestionFrameFragment[];
      }>;
    };
  }).savedFrameSet;
  if (!frameSet || !Number.isInteger(frameSet.revision) || !Array.isArray(frameSet.items)) return null;
  const item = frameSet.items.find((candidate) => candidate.questionId === questionId);
  if (!item || !Array.isArray(item.fragments)) return null;
  return {
    revision: Number(frameSet.revision),
    regions: item.fragments,
    status: item.status
  };
};

const initialPageId = (
  pages: TemplateQuestionFramePage[],
  regions: QuestionFrameFragment[]
): string => {
  const regionPage = regions.find((region) => pages.some((page) => page.id === region.templatePageId));
  return regionPage?.templatePageId ?? pages[0]?.id ?? "";
};

const pointerPoint = (
  canvas: SVGSVGElement,
  clientX: number,
  clientY: number,
  page: TemplateQuestionFramePage
): PixelPoint => {
  const bounds = canvas.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return {x: 0, y: 0};
  return {
    x: Math.max(0, Math.min(page.width, (clientX - bounds.left) * page.width / bounds.width)),
    y: Math.max(0, Math.min(page.height, (clientY - bounds.top) * page.height / bounds.height))
  };
};

const capturePointer = (target: Element, pointerId: number): void => {
  const pointerTarget = target as Element & {setPointerCapture?: (id: number) => void};
  pointerTarget.setPointerCapture?.(pointerId);
};

const releasePointer = (target: Element, pointerId: number): void => {
  const pointerTarget = target as Element & {releasePointerCapture?: (id: number) => void};
  pointerTarget.releasePointerCapture?.(pointerId);
};

const asTeacherEdit = (
  fragment: QuestionFrameFragment,
  geometry: NormalizedBox
): QuestionFrameFragment => ({
  ...updateFragmentGeometry(fragment, geometry),
  source: "teacher",
  confidence: null,
  issues: []
});

const frameClassName = (item: QuestionFrameItem, fragment: QuestionFrameFragment, current: boolean): string => {
  const hasError = item.issues.length > 0 || fragment.issues.length > 0;
  const status = hasError ? "error" : item.status === "confirmed" ? "confirmed" : "draft";
  return [
    "question-frame-editor__box",
    `question-frame-editor__box--${status}`,
    current ? "question-frame-editor__box--current" : "question-frame-editor__box--other"
  ].join(" ");
};

export function TemplateQuestionFrameEditor({
  pages,
  questionNumber,
  currentItem,
  otherItems = [],
  questionConfirmationStatus = "pending",
  geometryBlockers = [],
  disabled = false,
  onSave,
  onRerecognize,
  onConfirm
}: TemplateQuestionFrameEditorProps) {
  const [baseline, setBaseline] = useState(() => cloneRegions(currentItem.fragments));
  const [draft, setDraft] = useState(() => cloneRegions(currentItem.fragments));
  const [revision, setRevision] = useState(currentItem.revision);
  const [baselineFrameStatus, setBaselineFrameStatus] = useState(currentItem.status);
  const [frameStatus, setFrameStatus] = useState(currentItem.status);
  const [activePageId, setActivePageId] = useState(() => initialPageId(pages, currentItem.fragments));
  const [selectedRegionKey, setSelectedRegionKey] = useState<string | null>(currentItem.fragments[0]?.regionKey ?? null);
  const [mode, setMode] = useState<EditorMode>("select");
  const [interaction, setInteraction] = useState<PointerInteraction | null>(null);
  const [drawPreview, setDrawPreview] = useState<NormalizedBox | null>(null);
  const [busyAction, setBusyAction] = useState<"save" | "rerecognize" | "confirm" | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const sourceKey = `${currentItem.questionId}\u0000${currentItem.revision}`;
  const sourceKeyRef = useRef(sourceKey);
  const sourceQuestionIdRef = useRef(currentItem.questionId);
  const dirty = regionFingerprint(draft) !== regionFingerprint(baseline);

  useEffect(() => {
    if (sourceKeyRef.current === sourceKey) return;
    if (sourceQuestionIdRef.current === currentItem.questionId && dirty) {
      setError("服务器题框版本已更新，本地草稿已保留；请撤销草稿或刷新后重新核对。");
      return;
    }
    sourceKeyRef.current = sourceKey;
    sourceQuestionIdRef.current = currentItem.questionId;
    const incoming = cloneRegions(currentItem.fragments);
    setBaseline(incoming);
    setDraft(cloneRegions(incoming));
    setRevision(currentItem.revision);
    setBaselineFrameStatus(currentItem.status);
    setFrameStatus(currentItem.status);
    setActivePageId(initialPageId(pages, incoming));
    setSelectedRegionKey(incoming[0]?.regionKey ?? null);
    setMode("select");
    setInteraction(null);
    setDrawPreview(null);
    setBusyAction(null);
    setMessage("");
    setError("");
  }, [currentItem.fragments, currentItem.questionId, currentItem.revision, currentItem.status, dirty, pages, sourceKey]);

  const activePage = pages.find((page) => page.id === activePageId) ?? pages[0];
  const blockers = uniqueMessages([
    ...geometryBlockers,
    ...currentItem.issues.filter((issue) => !/^frame_[a-z_]+:/.test(issue)),
    ...localGeometryBlockers(draft, pages)
  ]);
  const selectedRegion = draft.find((region) => region.regionKey === selectedRegionKey) ?? null;
  const selectedOnActivePage = selectedRegion?.templatePageId === activePage?.id;

  const updateRegion = (regionKey: string, geometry: NormalizedBox): void => {
    setDraft((regions) => regions.map((region) =>
      region.regionKey === regionKey ? asTeacherEdit(region, geometry) : region
    ));
    setFrameStatus("pending");
    setMessage("");
    setError("");
  };

  const selectPage = (pageId: string): void => {
    setActivePageId(pageId);
    setSelectedRegionKey(draft.find((region) => region.templatePageId === pageId)?.regionKey ?? null);
    setMode("select");
    setInteraction(null);
    setDrawPreview(null);
  };

  const beginMove = (event: PointerEvent<SVGRectElement>, region: QuestionFrameFragment): void => {
    if (disabled || !activePage) return;
    if (mode === "redraw") return;
    event.stopPropagation();
    const canvas = event.currentTarget.ownerSVGElement;
    if (!canvas) return;
    capturePointer(canvas, event.pointerId);
    setSelectedRegionKey(region.regionKey);
    setMode("select");
    setInteraction({
      kind: "move",
      pageId: activePage.id,
      regionKey: region.regionKey,
      start: pointerPoint(canvas, event.clientX, event.clientY, activePage),
      origin: region
    });
    setMessage("");
    setError("");
  };

  const beginResize = (
    event: PointerEvent<SVGRectElement>,
    region: QuestionFrameFragment,
    handle: ResizeHandle
  ): void => {
    if (disabled || !activePage) return;
    if (mode === "redraw") return;
    event.stopPropagation();
    const canvas = event.currentTarget.ownerSVGElement;
    if (!canvas) return;
    capturePointer(canvas, event.pointerId);
    setInteraction({
      kind: "resize",
      pageId: activePage.id,
      regionKey: region.regionKey,
      start: pointerPoint(canvas, event.clientX, event.clientY, activePage),
      origin: region,
      handle
    });
    setMessage("");
    setError("");
  };

  const beginRedraw = (event: PointerEvent<SVGSVGElement>): void => {
    if (disabled || mode !== "redraw" || !activePage || !selectedRegionKey) return;
    const region = draft.find((candidate) => candidate.regionKey === selectedRegionKey);
    if (!region || region.templatePageId !== activePage.id) return;
    capturePointer(event.currentTarget, event.pointerId);
    const start = pointerPoint(event.currentTarget, event.clientX, event.clientY, activePage);
    setInteraction({kind: "redraw", pageId: activePage.id, regionKey: region.regionKey, start, origin: region});
    setDrawPreview(null);
    setMessage("");
    setError("");
  };

  const movePointer = (event: PointerEvent<SVGSVGElement>): void => {
    if (!interaction || !activePage || interaction.pageId !== activePage.id) return;
    const current = pointerPoint(event.currentTarget, event.clientX, event.clientY, activePage);
    if (interaction.kind === "redraw") {
      setDrawPreview(drawNormalizedRect(interaction.start, current, activePage, 1));
      return;
    }
    const delta = {x: current.x - interaction.start.x, y: current.y - interaction.start.y};
    const geometry = interaction.kind === "move"
      ? moveNormalizedRect(interaction.origin, delta, activePage)
      : resizeNormalizedRect(interaction.origin, interaction.handle!, delta, activePage);
    updateRegion(interaction.regionKey, geometry);
  };

  const endPointer = (event: PointerEvent<SVGSVGElement>): void => {
    if (!interaction || !activePage || interaction.pageId !== activePage.id) return;
    releasePointer(event.currentTarget, event.pointerId);
    if (interaction.kind === "redraw") {
      const current = pointerPoint(event.currentTarget, event.clientX, event.clientY, activePage);
      const geometry = drawNormalizedRect(interaction.start, current, activePage);
      if (geometry) {
        updateRegion(interaction.regionKey, geometry);
      } else {
        setError("重画范围太小，请拖出更大的题框");
      }
      setMode("select");
      setDrawPreview(null);
    }
    setInteraction(null);
  };

  const cancelPointer = (event: PointerEvent<SVGSVGElement>): void => {
    releasePointer(event.currentTarget, event.pointerId);
    if (interaction?.kind === "move" || interaction?.kind === "resize") {
      setDraft((regions) => {
        const restored = regions.map((region) =>
          region.regionKey === interaction.regionKey
            ? {...interaction.origin, issues: [...interaction.origin.issues]}
            : region
        );
        if (regionFingerprint(restored) === regionFingerprint(baseline)) {
          setFrameStatus(baselineFrameStatus);
        }
        return restored;
      });
    }
    setInteraction(null);
    setDrawPreview(null);
    setMode("select");
  };

  const keyboardMove = (event: KeyboardEvent<SVGRectElement>, region: QuestionFrameFragment): void => {
    if (disabled || !activePage) return;
    const deltas: Partial<Record<string, PixelPoint>> = {
      ArrowLeft: {x: event.shiftKey ? -10 : -1, y: 0},
      ArrowRight: {x: event.shiftKey ? 10 : 1, y: 0},
      ArrowUp: {x: 0, y: event.shiftKey ? -10 : -1},
      ArrowDown: {x: 0, y: event.shiftKey ? 10 : 1}
    };
    const delta = deltas[event.key];
    if (!delta) return;
    event.preventDefault();
    setSelectedRegionKey(region.regionKey);
    updateRegion(region.regionKey, moveNormalizedRect(region, delta, activePage));
  };

  const addFragment = (): void => {
    if (!activePage) return;
    const keys = new Set(draft.map((region) => region.regionKey));
    let sequence = draft.length + 1;
    let regionKey = `${currentItem.questionId}-fragment-${sequence}`;
    while (keys.has(regionKey)) {
      sequence += 1;
      regionKey = `${currentItem.questionId}-fragment-${sequence}`;
    }
    const pageFragmentCount = draft.filter((region) => region.templatePageId === activePage.id).length;
    const offset = Math.min(pageFragmentCount * 0.025, 0.2);
    const next: QuestionFrameFragment = {
      regionKey,
      templatePageId: activePage.id,
      pageNumber: activePage.pageNumber,
      x: 0.08 + offset,
      y: 0.08 + offset,
      width: 0.45,
      height: 0.22,
      sortOrder: draft.reduce((maximum, region) => Math.max(maximum, region.sortOrder), -1) + 1,
      source: "teacher",
      confidence: null,
      issues: []
    };
    setDraft((regions) => [...regions, next]);
    setFrameStatus("pending");
    setSelectedRegionKey(next.regionKey);
    setMode("select");
    setMessage("");
    setError("");
  };

  const deleteSelected = (): void => {
    if (!selectedRegionKey || draft.length <= 1) return;
    const remaining = draft
      .filter((region) => region.regionKey !== selectedRegionKey)
      .sort((left, right) => left.sortOrder - right.sortOrder)
      .map((region, sortOrder) => ({...region, sortOrder}));
    setDraft(remaining);
    setFrameStatus("pending");
    setSelectedRegionKey(
      remaining.find((region) => region.templatePageId === activePage?.id)?.regionKey
      ?? remaining[0]?.regionKey
      ?? null
    );
    setMode("select");
    setMessage("");
    setError("");
  };

  const undoDraft = (): void => {
    const restored = cloneRegions(baseline);
    setDraft(restored);
    setFrameStatus(baselineFrameStatus);
    setSelectedRegionKey(
      restored.find((region) => region.templatePageId === activePage?.id)?.regionKey
      ?? restored[0]?.regionKey
      ?? null
    );
    setMode("select");
    setInteraction(null);
    setDrawPreview(null);
    setMessage("");
    setError("");
  };

  const saveDraft = async (): Promise<void> => {
    if (!dirty || busyAction) return;
    const targetQuestionId = currentItem.questionId;
    const regions = cloneRegions(draft);
    setBusyAction("save");
    setMessage("");
    setError("");
    try {
      const saved = await onSave({questionId: targetQuestionId, expectedRevision: revision, regions});
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      const savedRegions = cloneRegions(saved?.regions ?? regions);
      setBaseline(savedRegions);
      setDraft(cloneRegions(savedRegions));
      setRevision(saved?.revision ?? revision + 1);
      const savedStatus = saved?.status ?? frameStatus;
      setBaselineFrameStatus(savedStatus);
      setFrameStatus(savedStatus);
      setSelectedRegionKey((selected) =>
        savedRegions.some((region) => region.regionKey === selected)
          ? selected
          : savedRegions[0]?.regionKey ?? null
      );
      setMessage("修改已保存");
    } catch (reason) {
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      setError(isRevisionConflict(reason)
        ? "服务器题框版本已更新，本地草稿已保留；请刷新后重新核对。"
        : errorMessage(reason, "题框保存失败，请重试"));
    } finally {
      if (sourceQuestionIdRef.current === targetQuestionId) setBusyAction(null);
    }
  };

  const confirmFrame = async (): Promise<void> => {
    if (!onConfirm || dirty || blockers.length > 0 || busyAction || frameStatus === "confirmed") return;
    const targetQuestionId = currentItem.questionId;
    setBusyAction("confirm");
    setMessage("");
    setError("");
    try {
      const confirmed = await onConfirm({questionId: targetQuestionId, expectedRevision: revision});
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      setRevision(confirmed?.revision ?? revision + 1);
      const confirmedStatus = confirmed?.status ?? "confirmed";
      setBaselineFrameStatus(confirmedStatus);
      setFrameStatus(confirmedStatus);
      setMessage("题框已确认");
    } catch (reason) {
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      setError(isRevisionConflict(reason)
        ? "服务器题框版本已更新，请刷新后重新核对；当前页面内容未被覆盖。"
        : errorMessage(reason, "题框确认失败，请重试"));
    } finally {
      if (sourceQuestionIdRef.current === targetQuestionId) setBusyAction(null);
    }
  };

  const rerecognizeFrame = async (): Promise<void> => {
    if (!onRerecognize || blockers.length > 0 || busyAction) return;
    const targetQuestionId = currentItem.questionId;
    const regions = cloneRegions(draft);
    setBusyAction("rerecognize");
    setMessage("");
    setError("");
    try {
      const saved = await onRerecognize({
        questionId: targetQuestionId,
        expectedRevision: revision,
        regions
      });
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      const savedRegions = cloneRegions(saved?.regions ?? regions);
      setBaseline(savedRegions);
      setDraft(cloneRegions(savedRegions));
      setRevision(saved?.revision ?? revision + 1);
      const savedStatus = saved?.status ?? "pending";
      setBaselineFrameStatus(savedStatus);
      setFrameStatus(savedStatus);
      setSelectedRegionKey((selected) =>
        savedRegions.some((region) => region.regionKey === selected)
          ? selected
          : savedRegions[0]?.regionKey ?? null
      );
      setMessage(saved?.teacherOverridePreserved
        ? "模型原始识别已更新；当前仍显示教师修改内容，请重新确认题目和题框"
        : "本题原文已重新识别，请重新确认题目和题框");
    } catch (reason) {
      if (sourceQuestionIdRef.current !== targetQuestionId) return;
      const conflict = isRevisionConflict(reason);
      const saved = conflict ? null : savedFrameFromError(reason, targetQuestionId);
      if (saved?.regions) {
        const savedRegions = cloneRegions(saved.regions);
        setBaseline(savedRegions);
        setDraft(cloneRegions(savedRegions));
        setRevision(saved.revision);
        const savedStatus = saved.status ?? "pending";
        setBaselineFrameStatus(savedStatus);
        setFrameStatus(savedStatus);
      }
      setError(conflict
        ? "服务器题框版本已更新，本地草稿已保留；请刷新后重新识别。"
        : errorMessage(reason, "本题重新识别失败；题框已保存，原题内容未改变，可继续调整或重试"));
    } finally {
      if (sourceQuestionIdRef.current === targetQuestionId) setBusyAction(null);
    }
  };

  const confirmationDisabledReason = dirty
    ? "请先保存题框修改"
    : blockers.length > 0
      ? blockers.join("；")
      : "";

  return (
    <section className="question-frame-editor" aria-label={`题框编辑器：${questionNumber}`}>
      <header className="question-frame-editor__head">
        <div>
          <p className="eyebrow">模板原页题框</p>
          <h3>题框：{questionNumber}</h3>
        </div>
        <div className="question-frame-editor__statuses">
          <span aria-label="题目确认状态" className={`question-frame-editor__status question-frame-editor__status--${questionConfirmationStatus}`}>
            题目确认：{questionConfirmationStatus === "confirmed" ? "已确认" : "待确认"}
          </span>
          <span aria-label="题框确认状态" className={`question-frame-editor__status question-frame-editor__status--${frameStatus}`}>
            题框确认：{frameStatus === "confirmed" ? "已确认" : "待确认"}
          </span>
        </div>
      </header>

      <div className="question-frame-editor__toolbar">
        <div className="question-frame-editor__pages" aria-label="模板页码">
          {pages.map((page) => (
            <button
              type="button"
              key={page.id}
              className={page.id === activePage?.id ? "active" : ""}
              aria-label={`查看第 ${page.pageNumber} 页`}
              aria-pressed={page.id === activePage?.id}
              onClick={() => selectPage(page.id)}
            >
              第 {page.pageNumber} 页
            </button>
          ))}
        </div>
        <div className="question-frame-editor__tools">
          <button
            type="button"
            className={mode === "redraw" ? "active" : ""}
            aria-pressed={mode === "redraw"}
            disabled={disabled || !selectedOnActivePage}
            onClick={() => setMode((current) => current === "redraw" ? "select" : "redraw")}
          >
            重画选中片段
          </button>
          <button type="button" disabled={disabled || !activePage} onClick={addFragment}>增加题框片段</button>
          <button
            type="button"
            disabled={disabled || !selectedRegionKey || draft.length <= 1}
            onClick={deleteSelected}
          >
            删除选中片段
          </button>
        </div>
      </div>

      <div className={`question-frame-editor__stage ${mode === "redraw" ? "is-redrawing" : ""}`}>
        <div className="question-frame-editor__legend" aria-label="题框图例">
          <span><i className="draft" />草稿题框</span>
          <span><i className="confirmed" />已确认题框</span>
          <span><i className="error" />问题题框</span>
          <span><b>AI</b>模型建议</span>
        </div>
        {activePage ? (
          <svg
            className="question-frame-editor__canvas"
            viewBox={`0 0 ${activePage.width} ${activePage.height}`}
            aria-label={`第 ${activePage.pageNumber} 页题框编辑画布`}
            onPointerDown={beginRedraw}
            onPointerMove={movePointer}
            onPointerUp={endPointer}
            onPointerCancel={cancelPointer}
          >
            <image href={activePage.imageUrl} width={activePage.width} height={activePage.height} />
            {otherItems.flatMap(({questionNumber: otherNumber, item: otherItem}) =>
              otherItem.fragments
                .filter((region) => region.templatePageId === activePage.id)
                .map((region) => {
                  const pixel = normalizedToPixels(region, activePage);
                  return (
                    <g key={`${otherItem.questionId}-${region.regionKey}`} className="question-frame-editor__region question-frame-editor__region--other">
                      <title>{`题目 ${otherNumber}，片段 ${region.sortOrder + 1}`}</title>
                      <rect
                        data-region-key={region.regionKey}
                        className={frameClassName(otherItem, region, false)}
                        x={pixel.x}
                        y={pixel.y}
                        width={pixel.width}
                        height={pixel.height}
                      />
                      <text className="question-frame-editor__label" x={pixel.x + 8} y={pixel.y + 24}>{otherNumber}</text>
                    </g>
                  );
                })
            )}
            {draft
              .filter((region) => region.templatePageId === activePage.id)
              .map((region) => {
                const pixel = normalizedToPixels(region, activePage);
                const selected = region.regionKey === selectedRegionKey;
                const handleSize = Math.max(10, Math.min(activePage.width, activePage.height) * 0.012);
                return (
                  <g
                    key={region.regionKey}
                    className={`question-frame-editor__region question-frame-editor__region--current ${selected ? "is-selected" : ""}`}
                  >
                    <title>{`题目 ${questionNumber}，片段 ${region.sortOrder + 1}`}</title>
                    <rect
                      data-region-key={region.regionKey}
                      data-current-question-region="true"
                      className={frameClassName({...currentItem, status: frameStatus}, region, true)}
                      x={pixel.x}
                      y={pixel.y}
                      width={pixel.width}
                      height={pixel.height}
                      rx={4}
                      role="button"
                      tabIndex={0}
                      aria-label={`选择并移动题框片段 ${region.sortOrder + 1}`}
                      onPointerDown={(event) => beginMove(event, region)}
                      onKeyDown={(event) => keyboardMove(event, region)}
                    />
                    <text className="question-frame-editor__label" x={pixel.x + 8} y={pixel.y + 24}>
                      {questionNumber}{region.source === "model" ? " · 模型建议" : ""}
                    </text>
                    {selected && mode === "select" && RESIZE_HANDLES.map(({handle, x, y, label}) => (
                      <rect
                        key={handle}
                        data-resize-handle={handle}
                        className={`question-frame-editor__handle question-frame-editor__handle--${handle}`}
                        x={pixel.x + pixel.width * x - handleSize / 2}
                        y={pixel.y + pixel.height * y - handleSize / 2}
                        width={handleSize}
                        height={handleSize}
                        aria-label={`${label}缩放片段 ${region.sortOrder + 1}`}
                        onPointerDown={(event) => beginResize(event, region, handle)}
                      />
                    ))}
                  </g>
                );
              })}
            {drawPreview && (() => {
              const preview = normalizedToPixels(drawPreview, activePage);
              return <rect className="question-frame-editor__draw-preview" {...preview} />;
            })()}
          </svg>
        ) : (
          <div className="question-frame-editor__empty">没有可编辑的模板页面</div>
        )}
      </div>

      <footer className="question-frame-editor__footer">
        <div className="question-frame-editor__feedback">
          {dirty && <strong>有未保存修改</strong>}
          {blockers.length > 0 && (
            <ul aria-label="题框阻断问题">
              {blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
            </ul>
          )}
          {error && <p role="alert" className="question-frame-editor__error">{error}</p>}
          {message && <p role="status" className="question-frame-editor__message">{message}</p>}
        </div>
        <div className="question-frame-editor__actions">
          <button type="button" className="button" disabled={disabled || !dirty || busyAction !== null} onClick={undoDraft}>
            撤销未保存修改
          </button>
          <button type="button" className="button" disabled={disabled || !dirty || busyAction !== null} onClick={saveDraft}>
            {busyAction === "save" ? "正在保存…" : "保存题框修改"}
          </button>
          {onRerecognize && (
            <button
              type="button"
              className="button button--primary"
              disabled={disabled || blockers.length > 0 || busyAction !== null}
              title={blockers.length > 0 ? blockers.join("；") : "使用当前题全部题框片段重新识别原题"}
              onClick={rerecognizeFrame}
            >
              {busyAction === "rerecognize" ? "正在保存题框并重新识别…" : "保存并重新识别本题"}
            </button>
          )}
          {onConfirm && (
            <button
              type="button"
              className="button button--primary"
              disabled={disabled || dirty || blockers.length > 0 || busyAction !== null || frameStatus === "confirmed"}
              title={confirmationDisabledReason}
              onClick={confirmFrame}
            >
              {busyAction === "confirm" ? "正在确认…" : "确认题框"}
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}
