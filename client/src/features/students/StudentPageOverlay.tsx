import {useState} from "react";
import type { StudentSubmissionDetail } from "@shared/contracts";

type Region = StudentSubmissionDetail["questionRegions"][number];

export function StudentPageOverlay({
  page,
  regions
}: {
  page: StudentSubmissionDetail["pages"][number];
  regions: Region[];
}) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="student-page-canvas" style={{aspectRatio: `${page.width} / ${page.height}`}}>
      <img src={page.imageUrl} alt={`学生答卷第 ${page.pageNumber} 页`} />
      <svg viewBox={`0 0 ${page.width} ${page.height}`} aria-label="题目区域覆盖层" preserveAspectRatio="xMidYMid meet">
        {regions.map((region, index) => {
          const points = region.studentPolygon.map((point) => `${point.x},${point.y}`).join(" ");
          const active = selected === region.id;
          const anchor = region.studentPolygon[0] ?? {x: region.studentBox.x, y: region.studentBox.y};
          const toggle = () => setSelected((current) => current === region.id ? null : region.id);
          return (
            <g
              key={region.id}
              data-region-id={region.id}
              data-question-id={region.questionId}
              className={`${active ? "is-selected" : ""} ${region.status === "needs_review" ? "needs-review" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`题目 ${region.questionNumber || index + 1}，片段 ${region.sortOrder + 1}`}
              onClick={toggle}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                toggle();
              }}
            >
              <title>{`题目 ${region.questionNumber || index + 1}，片段 ${region.sortOrder + 1}`}</title>
              <polygon points={points} vectorEffect="non-scaling-stroke" />
              <rect className="question-label" x={Math.max(0, anchor.x)} y={Math.max(0, anchor.y)} width="58" height="34" rx="8" vectorEffect="non-scaling-stroke" />
              <text x={Math.max(0, anchor.x) + 29} y={Math.max(0, anchor.y) + 23} textAnchor="middle">{region.questionNumber || index + 1}</text>
            </g>
          );
        })}
      </svg>
      {!regions.length && <div className="student-page-canvas__empty">本页暂未生成题目区域</div>}
    </div>
  );
}
