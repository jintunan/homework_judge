import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { StudentSubmissionDetail } from "../../shared/contracts";
import { ConfirmDeleteTaskDialog } from "../../client/src/components/ConfirmDeleteTaskDialog";
import { StudentPageOverlay } from "../../client/src/features/students/StudentPageOverlay";
import {StudentSubmissionsPage} from "../../client/src/features/students/StudentSubmissionsPage";
import {api} from "../../client/src/lib/api";

vi.mock("../../client/src/lib/api", () => ({
  api: vi.fn(),
  uploadStudentSubmission: vi.fn()
}));

type MappedRegion = StudentSubmissionDetail["questionRegions"][number] & {
  frameSetId?: string | null;
  frameRegionId?: string | null;
  alignmentRevisionId?: string | null;
  processingRevisionId?: string | null;
};

const page: StudentSubmissionDetail["pages"][number] = {
  id: "student-page",
  pageNumber: 1,
  width: 1000,
  height: 1400,
  templatePageId: "template-page",
  templatePageNumber: 1,
  imageUrl: "/api/student-pages/student-page",
  alignment: {
    direction: "student_original_to_template",
    transform: null,
    quality: 0.91,
    method: "feature_homography",
    status: "aligned"
  }
};

const region: StudentSubmissionDetail["questionRegions"][number] = {
  id: "region",
  questionId: "question",
  questionNumber: "2",
  sortOrder: 0,
  templatePageId: "template-page",
  studentPageId: "student-page",
  coordinateSpace: "student_original_page_pixels",
  templateRegion: {page_number: 1, x: 0.1, y: 0.2, width: 0.8, height: 0.4},
  studentPolygon: [{x: 100, y: 280}, {x: 900, y: 280}, {x: 900, y: 840}, {x: 100, y: 840}],
  studentBox: {x: 100, y: 280, width: 800, height: 560},
  status: "ready",
  issues: []
};

const lowerFragment: StudentSubmissionDetail["questionRegions"][number] = {
  ...region,
  id: "region-lower",
  sortOrder: 1,
  studentPolygon: [{x: 120, y: 900}, {x: 880, y: 900}, {x: 880, y: 1100}, {x: 120, y: 1100}],
  studentBox: {x: 120, y: 900, width: 760, height: 200}
};

const nextQuestion: StudentSubmissionDetail["questionRegions"][number] = {
  ...region,
  id: "next-question-region",
  questionId: "next-question",
  questionNumber: "综合探究乙",
  sortOrder: 0,
  studentPolygon: [{x: 90, y: 1160}, {x: 910, y: 1160}, {x: 910, y: 1320}, {x: 90, y: 1320}],
  studentBox: {x: 90, y: 1160, width: 820, height: 160}
};

const q8CandidateOracle = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "backend/tests/fixtures/q8_full_frame_oracle.json"),
    "utf8"
  )
) as {
  reviewStatus: string;
  reviewedBy: string | null;
  reviewedAt: string | null;
  frameRegions: Array<{polygon: Array<{x: number; y: number}>}>;
};

const submission: StudentSubmissionDetail["submission"] = {
  id: "submission",
  task_id: "task",
  student_identifier: "S-001",
  student_name: "测试学生",
  original_name: "answer.pdf",
  page_count: 1,
  status: "ready",
  error_code: null,
  error_message: null,
  question_region_status: "needs_review",
  question_region_error_code: "MAPPING_BLOCKED",
  question_region_error_message: "部分题框映射需要复核",
  created_at: "2026-08-09T10:00:00Z",
  updated_at: "2026-08-09T10:01:00Z"
};

const detail: StudentSubmissionDetail & {questionRegions: MappedRegion[]} = {
  submission,
  pages: [page],
  responses: [{
    id: "calculation-response",
    questionId: "question",
    questionNumber: "2",
    questionType: "calculation",
    recognizedText: "解答过程",
    confidence: 0.88,
    isBlank: false,
    issues: [],
    status: "recognized",
    regions: [{
      id: "answer-box-that-must-not-be-merged",
      sortOrder: 0,
      templatePageId: "template-page",
      studentPageId: "student-page",
      coordinateSpace: "pixel",
      templateBox: {x: 0.1, y: 0.1, width: 0.8, height: 0.8},
      studentBox: {x: 40, y: 100, width: 920, height: 1180}
    }]
  }],
  questionRegionState: {
    status: "needs_review",
    errorCode: "MAPPING_BLOCKED",
    errorMessage: "部分题框映射需要复核",
    missingQuestionIds: []
  },
  questionRegions: [
    {...region, frameSetId: "frame-set-v7", frameRegionId: "frame-region-a", alignmentRevisionId: "alignment-revision-12", processingRevisionId: "processing-revision-3"},
    {...lowerFragment, frameSetId: "frame-set-v7", frameRegionId: "frame-region-b", alignmentRevisionId: "alignment-revision-12", processingRevisionId: "processing-revision-3"},
    {...nextQuestion, frameSetId: "frame-set-v7", frameRegionId: "frame-region-c", alignmentRevisionId: "alignment-revision-12", processingRevisionId: "processing-revision-3"}
  ],
  processingRevision: {
    id: "processing-revision-3",
    submissionId: "submission",
    revisionNumber: 3,
    frameSetId: "frame-set-v7",
    status: "mapping_needs_review",
    inputHash: "input-hash",
    isCurrent: true,
    issues: [{
      code: "severe_polygon_clip",
      message: "题框片段映射后严重裁切",
      layer: "alignment",
      questionId: "question",
      regionKey: "frame-region-b",
      nextAction: "检查页面配准"
    }],
    createdAt: "2026-08-09T10:00:00Z",
    finishedAt: null
  }
};

function renderSubmissionsPage() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task/students"]}>
        <Routes><Route path="/tasks/:taskId/students" element={<StudentSubmissionsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("student original-page overlay", () => {
  it("draws every server polygon independently in original pixel coordinates", () => {
    const {container} = render(<StudentPageOverlay page={page} regions={[region, lowerFragment]} />);
    expect(screen.getByLabelText("题目区域覆盖层")).toHaveAttribute("viewBox", "0 0 1000 1400");
    expect(container.querySelectorAll("polygon")).toHaveLength(2);
    expect(container.querySelector('[data-region-id="region"] polygon')).toHaveAttribute("points", "100,280 900,280 900,840 100,840");
    expect(container.querySelector('[data-region-id="region-lower"] polygon')).toHaveAttribute("points", "120,900 880,900 880,1100 120,1100");
    expect(screen.queryByText(/正确|错误/)).not.toBeInTheDocument();
    fireEvent.click(container.querySelector('[data-region-id="region"] polygon')!);
    expect(container.querySelector('[data-region-id="region"]')).toHaveClass("is-selected");
    expect(container.querySelector('[data-region-id="region-lower"]')).not.toHaveClass("is-selected");
  });

  it("renders the candidate q8 full frame exactly and never expands it toward an adjacent question", () => {
    expect(q8CandidateOracle.reviewStatus).toBe("candidate");
    expect(q8CandidateOracle.reviewedBy).toBeNull();
    expect(q8CandidateOracle.reviewedAt).toBeNull();
    const points = q8CandidateOracle.frameRegions[0].polygon;
    const q8Region: StudentSubmissionDetail["questionRegions"][number] = {
      ...region,
      id: "q8-candidate-frame",
      questionId: "candidate-q8",
      questionNumber: "8",
      templateRegion: {page_number: 1, x: 0.095, y: 0.37, width: 0.82, height: 0.435},
      studentPolygon: points.map(({x, y}) => ({x: x * page.width, y: y * page.height})),
      studentBox: {x: 95, y: 518, width: 820, height: 609}
    };

    const {container} = render(<StudentPageOverlay page={page} regions={[q8Region]} />);

    expect(container.querySelectorAll("polygon")).toHaveLength(1);
    expect(container.querySelector('[data-region-id="q8-candidate-frame"] polygon')).toHaveAttribute(
      "points",
      "95,518 915,518 915,1127 95,1127"
    );
    expect(container.querySelector('[data-region-id="q8-candidate-frame"] polygon')).not.toHaveAttribute(
      "points",
      expect.stringContaining("1320")
    );
  });

  it("does not auto-backfill or reconstruct calculation regions and shows mapping provenance", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/tasks/task/student-submissions") return [submission] as never;
      if (path === "/student-submissions/submission") return detail as never;
      return {} as never;
    });

    const {container} = renderSubmissionsPage();
    await screen.findByLabelText("题目区域覆盖层");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

    expect(container.querySelectorAll("polygon")).toHaveLength(3);
    expect(container.querySelector('[data-region-id="region"] polygon')).toHaveAttribute("points", "100,280 900,280 900,840 100,840");
    expect(container.querySelector('[data-region-id="region-lower"] polygon')).toHaveAttribute("points", "120,900 880,900 880,1100 120,1100");
    expect(container.querySelector('[data-region-id="next-question-region"] polygon')).toHaveAttribute("points", "90,1160 910,1160 910,1320 90,1320");
    expect(screen.getByText(/题框集 frame-set-v7/)).toBeInTheDocument();
    expect(screen.getByText(/配准修订 alignment-revision-12/)).toBeInTheDocument();
    expect(screen.getByText(/处理修订 R3/)).toBeInTheDocument();
    expect(screen.getByRole("alert", {name: "映射阻断问题"})).toHaveTextContent("severe_polygon_clip");
    expect(screen.getByRole("alert", {name: "映射阻断问题"})).toHaveTextContent("题框片段映射后严重裁切");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/student-submissions/submission"));
    expect(vi.mocked(api).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("requires explicit confirmation before permanent deletion", () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    render(<ConfirmDeleteTaskDialog title="期中试卷" busy={false} error="" onCancel={cancel} onConfirm={confirm} />);
    expect(screen.getByText(/期中试卷/)).toBeInTheDocument();
    expect(screen.getByText(/学生答卷/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "取消"}));
    expect(cancel).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
  });
});
