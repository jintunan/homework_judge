import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {beforeEach, describe, expect, it, vi} from "vitest";
import type {
  AlignmentControlPointPair,
  StudentSubmissionDetail,
  StudentSubmissionSummary
} from "../../shared/contracts";
import {StudentSubmissionsPage} from "../../client/src/features/students/StudentSubmissionsPage";
import {
  api,
  updateStudentPageAlignment
} from "../../client/src/lib/api";

vi.mock("../../client/src/lib/api", () => ({
  api: vi.fn(),
  updateStudentPageAlignment: vi.fn(),
  uploadStudentSubmission: vi.fn()
}));

type AlignmentPage = StudentSubmissionDetail["pages"][number] & {
  alignment: StudentSubmissionDetail["pages"][number]["alignment"] & {
    revisionNumber: number;
    source: "model" | "teacher";
    controlPoints: AlignmentControlPointPair[];
  };
};

const existingControlPoints: AlignmentControlPointPair[] = [
  {template: {x: 0, y: 0}, student: {x: 10, y: 20}},
  {template: {x: 320, y: 0}, student: {x: 990, y: 20}},
  {template: {x: 320, y: 240}, student: {x: 990, y: 1380}},
  {template: {x: 0, y: 240}, student: {x: 10, y: 1380}}
];

const submission: StudentSubmissionSummary = {
  id: "submission",
  task_id: "task",
  student_identifier: "S-001",
  student_name: "测试学生",
  original_name: "student.pdf",
  page_count: 1,
  status: "ready",
  error_code: null,
  error_message: null,
  question_region_status: "needs_review",
  question_region_error_code: "MAPPING_BLOCKED",
  question_region_error_message: "页面配准需要校正",
  created_at: "2026-08-09T10:00:00Z",
  updated_at: "2026-08-09T10:01:00Z"
};

const studentPage: AlignmentPage = {
  id: "student-page",
  pageNumber: 1,
  width: 1000,
  height: 1400,
  templatePageId: "template-page-1",
  templatePageNumber: 1,
  imageUrl: "/api/student-pages/student-page",
  alignment: {
    direction: "student_original_to_template",
    transform: null,
    quality: 0.42,
    method: "feature_homography",
    status: "low_quality",
    revisionNumber: 7,
    source: "model",
    controlPoints: []
  }
};

const mappedRegion: StudentSubmissionDetail["questionRegions"][number] = {
  id: "mapped-region",
  questionId: "question",
  questionNumber: "综合题",
  sortOrder: 0,
  templatePageId: "template-page-1",
  studentPageId: "student-page",
  coordinateSpace: "student_original_page_pixels",
  templateRegion: {page_number: 1, x: 0.1, y: 0.1, width: 0.8, height: 0.5},
  studentPolygon: [
    {x: 100, y: 140},
    {x: 900, y: 140},
    {x: 900, y: 840},
    {x: 100, y: 840}
  ],
  studentBox: {x: 100, y: 140, width: 800, height: 700},
  status: "needs_review",
  issues: ["mapping_alignment_low_quality"]
};

function detail(
  options: {
    source?: "model" | "teacher";
    controlPoints?: AlignmentControlPointPair[];
    revisionNumber?: number;
    processingStatus?: "aligning" | "mapping_needs_review";
  } = {}
): StudentSubmissionDetail & {pages: AlignmentPage[]} {
  const page: AlignmentPage = {
    ...studentPage,
    alignment: {
      ...studentPage.alignment,
      source: options.source ?? "model",
      controlPoints: options.controlPoints ?? [],
      revisionNumber: options.revisionNumber ?? 7
    }
  };
  return {
    submission,
    pages: [page],
    responses: [],
    questionRegionState: {
      status: "needs_review",
      errorCode: "MAPPING_BLOCKED",
      errorMessage: "页面配准需要校正",
      missingQuestionIds: []
    },
    questionRegions: [mappedRegion],
    processingRevision: {
      id: "processing-revision-4",
      submissionId: "submission",
      revisionNumber: 4,
      frameSetId: "frame-set-2",
      status: options.processingStatus ?? "mapping_needs_review",
      inputHash: "input-hash",
      isCurrent: true,
      issues: [
        {
          code: "mapping_alignment_low_quality",
          message: "页面配准质量低于题框映射阈值",
          layer: "alignment",
          nextAction: "校正页面配准"
        }
      ],
      createdAt: "2026-08-09T10:00:00Z",
      finishedAt: null
    }
  };
}

const review = {
  studentUploadGate: {
    ready: true,
    frameSetId: "frame-set-2",
    frameSetVersion: 2,
    missingQuestionIds: [],
    unconfirmedQuestionIds: [],
    issues: [],
    legacyRecovery: {
      required: false,
      frameSetSource: "teacher",
      hasLegacyBlankConfig: false,
      legacyProcessingCount: 0,
      readyForReprocess: true
    }
  },
  pages: [
    {
      id: "template-page-1",
      document_id: "exam",
      page_number: 1,
      width: 320,
      height: 240,
      role: "exam",
      imageUrl: "/api/pages/template-page-1"
    },
    {
      id: "template-page-2",
      document_id: "exam",
      page_number: 2,
      width: 320,
      height: 240,
      role: "exam",
      imageUrl: "/api/pages/template-page-2"
    }
  ]
};

function mockQueries(value: StudentSubmissionDetail & {pages: AlignmentPage[]}) {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/tasks/task/student-submissions") return [submission] as never;
    if (path === "/student-submissions/submission" || path.startsWith("/student-submissions/submission?")) return value as never;
    if (path === "/tasks/task/review") return review as never;
    if (path === "/student-submissions/submission/reprocess-new-flow") return {submissionId: "submission"} as never;
    throw new Error(`Unexpected API path: ${path}`);
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {queries: {retry: false}, mutations: {retry: false}}
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/tasks/task/students"]}>
        <Routes>
          <Route path="/tasks/:taskId/students" element={<StudentSubmissionsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function setCanvasRect(element: HTMLElement, width: number, height: number) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({})
  });
}

async function openEditor() {
  fireEvent.click(await screen.findByRole("button", {name: "校正页面配准"}));
  return screen.findByRole("region", {name: "页面配准校正"});
}

describe("student page alignment editor", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(updateStudentPageAlignment).mockReset();
    vi.mocked(updateStudentPageAlignment).mockResolvedValue({
      submissionId: "submission",
      studentPageId: "student-page",
      processingRevisionId: "processing-revision-5",
      alignmentRevision: 8,
      status: "aligning"
    });
  });

  it("collects paired points on side-by-side original pages and submits the frozen revision", async () => {
    mockQueries(detail());
    const {container} = renderPage();
    const editor = await openEditor();

    expect(screen.getByRole("img", {name: "模板页预览"})).toHaveAttribute(
      "src",
      "/api/pages/template-page-1"
    );
    expect(screen.getByRole("img", {name: "学生原页预览"})).toHaveAttribute(
      "src",
      "/api/student-pages/student-page"
    );
    fireEvent.change(screen.getByRole("combobox", {name: "对应模板页"}), {
      target: {value: "template-page-2"}
    });
    expect(screen.getByRole("img", {name: "模板页预览"})).toHaveAttribute(
      "src",
      "/api/pages/template-page-2"
    );

    const save = screen.getByRole("button", {name: "保存并重算整页题框"});
    expect(save).toBeDisabled();
    const templateCanvas = screen.getByRole("button", {name: "在模板页添加控制点"});
    const studentCanvas = screen.getByRole("button", {name: "在学生页添加控制点"});
    setCanvasRect(templateCanvas, 320, 240);
    setCanvasRect(studentCanvas, 1000, 1400);
    const clicks = [
      [10, 10, 20, 30],
      [310, 10, 980, 30],
      [310, 230, 980, 1370],
      [10, 230, 20, 1370]
    ];
    for (const [templateX, templateY, studentX, studentY] of clicks) {
      fireEvent.click(templateCanvas, {clientX: templateX, clientY: templateY});
      fireEvent.click(studentCanvas, {clientX: studentX, clientY: studentY});
    }

    expect(screen.getByText("已配置 4 / 4 对控制点")).toBeInTheDocument();
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() =>
      expect(updateStudentPageAlignment).toHaveBeenCalledWith(
        "submission",
        "student-page",
        {
          expectedAlignmentRevision: 7,
          templatePageId: "template-page-2",
          controlPoints: [
            {template: {x: 10, y: 10}, student: {x: 20, y: 30}},
            {template: {x: 310, y: 10}, student: {x: 980, y: 30}},
            {template: {x: 310, y: 230}, student: {x: 980, y: 1370}},
            {template: {x: 10, y: 230}, student: {x: 20, y: 1370}}
          ]
        }
      )
    );
    expect(await within(editor).findByText("已提交，正在重算整页全部题框")).toHaveAttribute(
      "role",
      "status"
    );
    expect(container.querySelector("[data-region-id]")).not.toBeInTheDocument();
    expect(container.querySelector("[draggable='true']")).not.toBeInTheDocument();
    expect(screen.queryByText(/拖动单题|调整单题题框/)).not.toBeInTheDocument();
  });

  it("clears a teacher override through the same PUT contract", async () => {
    mockQueries(
      detail({source: "teacher", controlPoints: existingControlPoints, revisionNumber: 8})
    );
    renderPage();
    await openEditor();

    fireEvent.click(screen.getByRole("button", {name: "清除人工配准"}));

    await waitFor(() =>
      expect(updateStudentPageAlignment).toHaveBeenCalledWith(
        "submission",
        "student-page",
        {expectedAlignmentRevision: 8, clearOverride: true}
      )
    );
  });

  it("shows alignment blockers and remapping progress without exposing question-box edits", async () => {
    mockQueries(detail({controlPoints: existingControlPoints, processingStatus: "aligning"}));
    const {container} = renderPage();
    const editor = await openEditor();

    const blocker = screen.getByRole("alert", {name: "配准阻断问题"});
    expect(blocker).toHaveTextContent("mapping_alignment_low_quality");
    expect(blocker).toHaveTextContent("页面配准质量低于题框映射阈值");
    expect(blocker).toHaveTextContent("校正页面配准");
    expect(within(editor).getByText("正在重新配准并重算整页题框…")).toHaveAttribute(
      "role",
      "status"
    );
    expect(container.querySelector("[data-region-id]")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: /拖动单题|调整单题/})).not.toBeInTheDocument();
  });

  it("marks legacy results as history and only starts a new-flow processing revision explicitly", async () => {
    const legacyDetail = detail() as StudentSubmissionDetail & {pages: AlignmentPage[]};
    legacyDetail.processingHistory = [
      {
        ...legacyDetail.processingRevision!,
        source: "legacy",
        isCurrent: true,
        responseCount: 2,
        gradingResultCount: 1,
        artifactCount: 2
      },
      {
        ...legacyDetail.processingRevision!,
        id: "processing-revision-3",
        revisionNumber: 3,
        source: "legacy",
        isCurrent: false,
        responseCount: 1,
        gradingResultCount: 1,
        artifactCount: 1
      }
    ];
    const legacyReview = {
      ...review,
      studentUploadGate: {
        ...review.studentUploadGate,
        legacyRecovery: {
          ...review.studentUploadGate.legacyRecovery,
          required: true,
          frameSetSource: "legacy",
          legacyProcessingCount: 2
        }
      }
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/tasks/task/student-submissions") return [submission] as never;
      if (path === "/student-submissions/submission" || path.startsWith("/student-submissions/submission?")) return legacyDetail as never;
      if (path === "/tasks/task/review") return legacyReview as never;
      if (path === "/student-submissions/submission/reprocess-new-flow") return {submissionId: "submission"} as never;
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderPage();

    expect(await screen.findByText("当前显示的是历史处理结果。按新流程重处理会创建新的处理版本，不会删除原图、旧识别、旧评分或产物。")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "按新流程重处理"})).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", {name: "处理版本"}), {
      target: {value: "processing-revision-3"}
    });
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/student-submissions/submission?processingRevisionId=processing-revision-3"));

    fireEvent.click(screen.getByRole("button", {name: "按新流程重处理"}));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/student-submissions/submission/reprocess-new-flow",
      {method: "POST"}
    ));
  });
});
