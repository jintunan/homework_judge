import {useMutation} from "@tanstack/react-query";
import {RotateCcw, Save, Trash2, X} from "lucide-react";
import {useState} from "react";
import type {MouseEvent} from "react";
import type {
  AlignmentControlPointPair,
  ProcessingRevisionStatus
} from "@shared/contracts";
import {
  ApiError,
  updateStudentPageAlignment
} from "@/lib/api";

export interface AlignmentTemplatePage {
  id: string;
  pageNumber: number;
  width: number;
  height: number;
  imageUrl: string;
}

export interface AlignmentStudentPage {
  id: string;
  pageNumber: number;
  width: number;
  height: number;
  imageUrl: string;
  templatePageId: string | null;
  alignment: {
    revisionNumber: number | null;
    source: "model" | "teacher" | null;
    controlPoints: AlignmentControlPointPair[];
  };
}

export interface AlignmentBlocker {
  code: string;
  message: string;
  nextAction?: string | null;
}

interface StudentAlignmentEditorProps {
  submissionId: string;
  studentPage: AlignmentStudentPage;
  templatePages: AlignmentTemplatePage[];
  blockers: AlignmentBlocker[];
  processingStatus?: ProcessingRevisionStatus | null;
  templatesLoading?: boolean;
  templatesError?: string;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}

interface PixelPoint {
  x: number;
  y: number;
}

const minimumControlPointPairs = 4;

function clickPoint(
  event: MouseEvent<HTMLButtonElement>,
  imageWidth: number,
  imageHeight: number
): PixelPoint | null {
  const bounds = event.currentTarget.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return null;
  const x = (event.clientX - bounds.left) * imageWidth / bounds.width;
  const y = (event.clientY - bounds.top) * imageHeight / bounds.height;
  return {
    x: Math.round(Math.min(imageWidth, Math.max(0, x)) * 100) / 100,
    y: Math.round(Math.min(imageHeight, Math.max(0, y)) * 100) / 100
  };
}

function ControlPointMarkers({
  points,
  width,
  height,
  pendingPoint
}: {
  points: PixelPoint[];
  width: number;
  height: number;
  pendingPoint?: PixelPoint | null;
}) {
  return (
    <svg
      className="student-alignment-canvas__markers"
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      {points.map((point, index) => (
        <g key={`${point.x}-${point.y}-${index}`}>
          <circle cx={point.x} cy={point.y} r="10" />
          <text x={point.x} y={point.y}>{index + 1}</text>
        </g>
      ))}
      {pendingPoint ? (
        <g className="student-alignment-canvas__pending">
          <circle cx={pendingPoint.x} cy={pendingPoint.y} r="12" />
          <text x={pendingPoint.x} y={pendingPoint.y}>{points.length + 1}</text>
        </g>
      ) : null}
    </svg>
  );
}

export function StudentAlignmentEditor({
  submissionId,
  studentPage,
  templatePages,
  blockers,
  processingStatus,
  templatesLoading = false,
  templatesError = "",
  onClose,
  onSaved
}: StudentAlignmentEditorProps) {
  const initialTemplatePageId = studentPage.templatePageId ?? templatePages[0]?.id ?? "";
  const [selectedTemplatePageId, setSelectedTemplatePageId] = useState(initialTemplatePageId);
  const [controlPoints, setControlPoints] = useState<AlignmentControlPointPair[]>(
    studentPage.alignment.controlPoints
  );
  const [pendingTemplatePoint, setPendingTemplatePoint] = useState<PixelPoint | null>(null);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const selectedTemplatePage = templatePages.find(
    (page) => page.id === selectedTemplatePageId
  ) ?? (selectedTemplatePageId ? undefined : templatePages[0]);
  const expectedRevision = studentPage.alignment.revisionNumber;

  const updateAlignment = useMutation({
    mutationFn: (
      payload:
        | {kind: "save"; templatePageId: string; controlPoints: AlignmentControlPointPair[]}
        | {kind: "clear"}
    ) => {
      if (expectedRevision == null) {
        throw new ApiError(
          "ALIGNMENT_REVISION_MISSING",
          "当前页面缺少可用于并发校验的配准修订号，请刷新后重试"
        );
      }
      return payload.kind === "clear"
        ? updateStudentPageAlignment(submissionId, studentPage.id, {
            expectedAlignmentRevision: expectedRevision,
            clearOverride: true
          })
        : updateStudentPageAlignment(submissionId, studentPage.id, {
            expectedAlignmentRevision: expectedRevision,
            templatePageId: payload.templatePageId,
            controlPoints: payload.controlPoints
          });
    },
    onSuccess: async () => {
      setError("");
      setFeedback("已提交，正在重算整页全部题框");
      await onSaved();
    },
    onError: (reason) => {
      setFeedback("");
      setError(
        reason instanceof ApiError
          ? `${reason.code}：${reason.message}`
          : reason instanceof Error
            ? reason.message
            : "页面配准保存失败"
      );
    }
  });

  const chooseTemplatePage = (templatePageId: string) => {
    setSelectedTemplatePageId(templatePageId);
    setControlPoints([]);
    setPendingTemplatePoint(null);
    setFeedback("");
    setError("");
  };

  const addTemplatePoint = (event: MouseEvent<HTMLButtonElement>) => {
    if (!selectedTemplatePage || updateAlignment.isPending) return;
    const point = clickPoint(event, selectedTemplatePage.width, selectedTemplatePage.height);
    if (point) {
      setPendingTemplatePoint(point);
      setFeedback("");
      setError("");
    }
  };

  const addStudentPoint = (event: MouseEvent<HTMLButtonElement>) => {
    if (!pendingTemplatePoint || updateAlignment.isPending) {
      if (!pendingTemplatePoint) setError("请先在左侧模板页点击对应位置");
      return;
    }
    const point = clickPoint(event, studentPage.width, studentPage.height);
    if (!point) return;
    setControlPoints((current) => [
      ...current,
      {template: pendingTemplatePoint, student: point}
    ]);
    setPendingTemplatePoint(null);
    setFeedback("");
    setError("");
  };

  const undoLastPair = () => {
    if (pendingTemplatePoint) {
      setPendingTemplatePoint(null);
    } else {
      setControlPoints((current) => current.slice(0, -1));
    }
    setFeedback("");
    setError("");
  };

  const saveDisabled =
    updateAlignment.isPending ||
    expectedRevision == null ||
    !selectedTemplatePage ||
    pendingTemplatePoint != null ||
    controlPoints.length < minimumControlPointPairs;
  const hasTeacherOverride =
    studentPage.alignment.source === "teacher";
  const processingMessage = processingStatus === "aligning"
    ? "正在重新配准并重算整页题框…"
    : processingStatus === "mapping_needs_review"
      ? "重映射仍需教师校正"
      : "";

  return (
    <section
      className="student-alignment-editor"
      role="region"
      aria-label="页面配准校正"
    >
      <header className="student-alignment-editor__head">
        <div>
          <strong>页面配准校正</strong>
          <small>在模板页与学生原页依次点击同一位置，保存后系统会重算本页全部题框。</small>
        </div>
        <button type="button" className="icon-button" aria-label="关闭页面配准校正" onClick={onClose}>
          <X size={17} />
        </button>
      </header>

      <div className="student-alignment-editor__toolbar">
        <label>
          <span>对应模板页</span>
          <select
            aria-label="对应模板页"
            value={selectedTemplatePage?.id ?? ""}
            disabled={templatesLoading || updateAlignment.isPending || templatePages.length === 0}
            onChange={(event) => chooseTemplatePage(event.target.value)}
          >
            {templatePages.map((page) => (
              <option key={page.id} value={page.id}>模板第 {page.pageNumber} 页</option>
            ))}
          </select>
        </label>
        <div className="student-alignment-editor__pair-status">
          <strong>已配置 {controlPoints.length} / {minimumControlPointPairs} 对控制点</strong>
          <small>{pendingTemplatePoint ? "请在学生页点击第一个点的对应位置" : "每对点必须指向两页中的同一处"}</small>
        </div>
        <button
          type="button"
          className="button"
          disabled={updateAlignment.isPending || (!pendingTemplatePoint && controlPoints.length === 0)}
          onClick={undoLastPair}
        >
          <RotateCcw size={15} />撤销上一点
        </button>
      </div>

      {templatesLoading ? (
        <div className="student-alignment-editor__empty">正在读取模板页…</div>
      ) : templatesError ? (
        <div className="student-alignment-editor__empty student-alignment-editor__error" role="alert">
          模板页读取失败：{templatesError}
        </div>
      ) : selectedTemplatePage ? (
        <div className="student-alignment-editor__pages">
          <article>
            <header><strong>模板第 {selectedTemplatePage.pageNumber} 页</strong><small>先点击</small></header>
            <button
              type="button"
              className="student-alignment-canvas"
              aria-label="在模板页添加控制点"
              disabled={updateAlignment.isPending}
              onClick={addTemplatePoint}
            >
              <img src={selectedTemplatePage.imageUrl} alt="模板页预览" />
              <ControlPointMarkers
                points={controlPoints.map((pair) => pair.template)}
                width={selectedTemplatePage.width}
                height={selectedTemplatePage.height}
                pendingPoint={pendingTemplatePoint}
              />
            </button>
          </article>
          <article>
            <header><strong>学生原页第 {studentPage.pageNumber} 页</strong><small>再点击对应位置</small></header>
            <button
              type="button"
              className="student-alignment-canvas"
              aria-label="在学生页添加控制点"
              disabled={updateAlignment.isPending}
              onClick={addStudentPoint}
            >
              <img src={studentPage.imageUrl} alt="学生原页预览" />
              <ControlPointMarkers
                points={controlPoints.map((pair) => pair.student)}
                width={studentPage.width}
                height={studentPage.height}
              />
            </button>
          </article>
        </div>
      ) : (
        <div className="student-alignment-editor__empty">没有可关联的模板页</div>
      )}

      <footer className="student-alignment-editor__footer">
        <div className="student-alignment-editor__feedback" aria-live="polite">
          {expectedRevision == null ? (
            <p className="student-alignment-editor__error" role="alert">缺少配准修订号，暂时不能保存；请刷新页面。</p>
          ) : null}
          {processingMessage ? <p role="status">{processingMessage}</p> : null}
          {updateAlignment.isPending ? <p role="status">正在提交配准并重算整页题框…</p> : null}
          {!updateAlignment.isPending && feedback ? <p role="status">{feedback}</p> : null}
          {error ? <p className="student-alignment-editor__error" role="alert">{error}</p> : null}
        </div>
        <div className="student-alignment-editor__actions">
          {hasTeacherOverride ? (
            <button
              type="button"
              className="button button--danger-subtle"
              disabled={updateAlignment.isPending || expectedRevision == null}
              onClick={() => updateAlignment.mutate({kind: "clear"})}
            >
              <Trash2 size={15} />清除人工配准
            </button>
          ) : null}
          <button
            type="button"
            className="button button--primary"
            disabled={saveDisabled}
            title={controlPoints.length < minimumControlPointPairs ? "至少配置 4 对控制点" : undefined}
            onClick={() => selectedTemplatePage && updateAlignment.mutate({
              kind: "save",
              templatePageId: selectedTemplatePage.id,
              controlPoints
            })}
          >
            <Save size={15} />保存并重算整页题框
          </button>
        </div>
      </footer>

      {blockers.length > 0 ? (
        <section
          className="student-alignment-editor__blockers"
          role="alert"
          aria-label="配准阻断问题"
        >
          <strong>当前阻断</strong>
          <ul>
            {blockers.map((blocker) => (
              <li key={`${blocker.code}-${blocker.message}`}>
                <code>{blocker.code}</code>
                <span>{blocker.message}</span>
                {blocker.nextAction ? <small>下一步：{blocker.nextAction}</small> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
