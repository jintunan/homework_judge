import type {
  AnnotationPreviewMark,
  StudentSubmissionDetail
} from "@shared/contracts";
import { useState, type PointerEvent } from "react";
import type {
  GradingBlankAnchorOverlay,
  GradingQuestionFrameRegion,
  GradingRecognitionEvidence
} from "@/lib/api";

type DrawnBox = {x: number; y: number; width: number; height: number};

interface Props {
  page: StudentSubmissionDetail["pages"][number];
  marks: AnnotationPreviewMark[];
  evidence: GradingRecognitionEvidence[];
  questionFrames?: GradingQuestionFrameRegion[];
  blankAnchors?: GradingBlankAnchorOverlay[];
  showQuestionFrames?: boolean;
  showBlankAnchors?: boolean;
  showEvidence: boolean;
  showMarks: boolean;
  selectedEvidenceRegionId?: string;
  drawingEnabled?: boolean;
  scale?: number;
  onBoxDraw?: (box: DrawnBox) => void;
}

function polygonPoints(points: Array<{x: number; y: number}>): string {
  return points.map(({x, y}) => `${x},${y}`).join(" ");
}

export function GradingPageOverlay({
  page,
  marks,
  evidence,
  questionFrames = [],
  blankAnchors = [],
  showQuestionFrames = true,
  showBlankAnchors = false,
  showEvidence,
  showMarks,
  selectedEvidenceRegionId,
  drawingEnabled = false,
  scale = 1,
  onBoxDraw
}: Props) {
  const [draft, setDraft] = useState<{startX: number; startY: number; box: DrawnBox} | null>(null);
  const pageMarks = marks.filter((item) => item.page_id === page.id);
  const pageEvidence = evidence.filter((item) => item.page_id === page.id);
  const pageFrames = questionFrames.filter((item) => item.pageId === page.id);
  const pageAnchors = blankAnchors.filter((item) => item.pageId === page.id);
  // The CSS size may change for fit-page/zoom, while viewBox and pointer math
  // stay in original-image pixels so saved evidence coordinates never drift.
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(page.width, (event.clientX - bounds.left) * page.width / bounds.width)),
      y: Math.max(0, Math.min(page.height, (event.clientY - bounds.top) * page.height / bounds.height))
    };
  };
  return (
    <svg
      className={`grading-page-canvas ${drawingEnabled ? "is-drawing" : ""}`}
      viewBox={`0 0 ${page.width} ${page.height}`}
      style={{width: page.width * scale, height: page.height * scale}}
      aria-label="批改标记覆盖层"
      onPointerDown={(event) => {
        if (!drawingEnabled) return;
        const start = point(event);
        event.currentTarget.setPointerCapture(event.pointerId);
        setDraft({startX: start.x, startY: start.y, box: {...start, width: 1, height: 1}});
      }}
      onPointerMove={(event) => {
        if (!drawingEnabled) return;
        const current = point(event);
        setDraft((activeDraft) => activeDraft ? {...activeDraft, box: {
          x: Math.min(activeDraft.startX, current.x),
          y: Math.min(activeDraft.startY, current.y),
          width: Math.abs(current.x - activeDraft.startX),
          height: Math.abs(current.y - activeDraft.startY)
        }} : null);
      }}
      onPointerUp={(event) => {
        if (!drawingEnabled || !draft) return;
        event.currentTarget.releasePointerCapture(event.pointerId);
        if (draft.box.width >= 8 && draft.box.height >= 8) onBoxDraw?.(draft.box);
        setDraft(null);
      }}
    >
      <image href={page.imageUrl} width={page.width} height={page.height} />
      {showQuestionFrames && pageFrames.map((item) => item.polygon.length >= 3 ? (
        <polygon
          key={item.id}
          className="grading-question-frame"
          points={polygonPoints(item.polygon)}
        />
      ) : null)}
      {showBlankAnchors && pageAnchors.map((item) => (
        item.studentPolygon && item.studentPolygon.length >= 3 ? (
          <polygon
            key={item.blankKey}
            className="grading-blank-anchor"
            points={polygonPoints(item.studentPolygon)}
          />
        ) : null
      ))}
      {showEvidence && pageEvidence.map((item) => (
        item.original_polygon && item.original_polygon.length >= 3 ? (
          <polygon
            key={item.region_id}
            className={`grading-evidence-box grading-recognition-evidence ${item.region_id === selectedEvidenceRegionId ? "is-selected" : ""}`}
            points={polygonPoints(item.original_polygon)}
          />
        ) : (
          <rect
            key={item.region_id}
            className={`grading-evidence-box grading-recognition-evidence ${item.region_id === selectedEvidenceRegionId ? "is-selected" : ""}`}
            x={item.original_bbox.x}
            y={item.original_bbox.y}
            width={item.original_bbox.width}
            height={item.original_bbox.height}
          />
        )
      ))}
      {showMarks && pageMarks.map((mark, index) => {
        const {x, y, width, height} = mark.box;
        return (
          <g key={`${mark.question_result_id}-${mark.mark_type}-${index}`} className={`grading-mark grading-mark--${mark.mark_type}`}>
            {mark.mark_type === "check" && (
              <path d={`M ${x + width * .08} ${y + height * .52} L ${x + width * .38} ${y + height * .82} L ${x + width * .94} ${y + height * .12}`} />
            )}
            {mark.mark_type === "error_circle" && (
              <ellipse cx={x + width / 2} cy={y + height / 2} rx={width / 2} ry={height / 2} transform={`rotate(-8 ${x + width / 2} ${y + height / 2})`} />
            )}
            {mark.mark_type === "partial_score" && <>
              <polygon points={`${x + width / 2},${y} ${x + width},${y + height} ${x},${y + height}`} />
              <text x={x + width / 2} y={y + height * .7} textAnchor="middle">{mark.label}</text>
            </>}
          </g>
        );
      })}
      {draft && <rect className="grading-location-draft" x={draft.box.x} y={draft.box.y} width={draft.box.width} height={draft.box.height} />}
    </svg>
  );
}
