import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  GradingArtifact,
  GradingQuestionResult,
  GradingReviewItem,
  GradingRun,
  StudentSubmissionDetail
} from "../../shared/contracts";
import { GradingWorkspacePage } from "../../client/src/features/grading/GradingWorkspacePage";
import {
  api,
  correctGradingBlank,
  downloadArtifact,
  previewArtifact,
  resolveGradingReview
} from "../../client/src/lib/api";

vi.mock("../../client/src/lib/api", () => ({
  api: vi.fn(),
  createGradingRun: vi.fn(),
  correctGradingBlank: vi.fn(),
  resolveGradingReview: vi.fn(),
  previewArtifact: vi.fn(),
  downloadArtifact: vi.fn()
}));

const runBase: GradingRun = {
  id: "run", submissionId: "submission", taskId: "task", status: "needs_review",
  stage: "needs_review", inputHash: "hash", resultRevision: 1, totalScore: "0.00",
  maxScore: "2.00", progress: {current: 1, total: 1}, openReviewCount: 1,
  lastSuccessfulStage: "auditing", attemptCount: 0, retryable: false, error: null,
  createdAt: "2026-01-01", updatedAt: "2026-01-01"
};

const questionBase: GradingQuestionResult = {
  id: "result", gradingRunId: "run", questionId: "question", questionNumber: "1",
  questionType: "single_choice", status: "needs_review", rawScore: "0.00",
  finalScore: "0.00", maxScore: "2.00", reviewReasons: ["LOW_RECOGNITION_CONFIDENCE"],
  errorLocations: [{page_id: "page", region_id: "region", original_bbox: {x: 20, y: 30, width: 80, height: 30}, cropped_image_path: null, recognized_text: "B", char_or_step_range: null}],
  decisions: [{key: "choice", status: "incorrect", score: "0.00", max_score: "2.00", reason: "选择错误", evidence_refs: [], blocked_by: null}],
  evidence: [{page_id: "page", region_id: "region", original_bbox: {x: 20, y: 30, width: 80, height: 30}, cropped_image_path: null, recognized_text: "B", char_or_step_range: null}],
  resultRevision: 0, error: null
};

type BlankFixture = {
  id: string;
  blankKey: string;
  studentAnswer: string;
  standardAnswers: string[];
  status: "correct" | "incorrect" | "needs_review";
  score: string;
  maxScore: string;
  reviewReasons: string[];
  evidenceRefs: NonNullable<GradingQuestionResult["evidence"]>;
  frameSetId: string | null;
  blankConfigVersionId: string | null;
  processingRevisionId: string | null;
  gradingRevision: number | null;
};

type QuestionFixture = GradingQuestionResult & {
  gradingRevision?: number | null;
  frameSetId?: string | null;
  blankConfigVersionId?: string | null;
  processingRevisionId?: string | null;
  questionFrames?: Array<{
    id: string;
    questionId: string;
    pageId: string;
    polygon: Array<{x: number; y: number}>;
  }>;
  blankResults?: BlankFixture[];
  blankAnchors?: Array<{
    blankKey: string;
    pageId: string;
    coordinateSpace: "student_page_pixel";
    studentPolygon: Array<{x: number; y: number}>;
    studentBBox: {x: number; y: number; width: number; height: number};
  }>;
};

function blankFixture(blankKey: string, index: number): BlankFixture {
  return {
    id: `blank-${blankKey}`,
    blankKey,
    studentAnswer: `原答案-${blankKey}`,
    standardAnswers: [`标准-${blankKey}`],
    status: index === 1 ? "needs_review" : "correct",
    score: index === 1 ? "0.00" : "1.00",
    maxScore: "1.00",
    reviewReasons: index === 1 ? ["LOW_RECOGNITION_CONFIDENCE"] : [],
    evidenceRefs: questionBase.evidence ?? [],
    frameSetId: "frame-v3",
    blankConfigVersionId: "config-v4",
    processingRevisionId: "processing-v6",
    gradingRevision: 8
  };
}

const submission: StudentSubmissionDetail = {
  submission: {
    id: "submission", task_id: "task", student_identifier: "1", student_name: "学生",
    original_name: "answer.pdf", page_count: 1, status: "ready", error_code: null,
    error_message: null, question_region_status: "ready", question_region_error_code: null,
    question_region_error_message: null, created_at: "2026-01-01", updated_at: "2026-01-01"
  },
  pages: [{id: "page", pageNumber: 1, width: 1000, height: 1400, templatePageId: "template", templatePageNumber: 1, imageUrl: "/page.jpg", alignment: {direction: "student_original_to_template", transform: null, quality: 1, method: "test", status: "aligned"}}],
  responses: [], questionRegionState: {status: "ready", errorCode: null, errorMessage: null, missingQuestionIds: []}, questionRegions: [{
    id: "frame-region", questionId: "question", questionNumber: "1", sortOrder: 0,
    templatePageId: "template", studentPageId: "page", coordinateSpace: "student_original_page_pixels",
    templateRegion: {page_number: 1, x: 0.05, y: 0.08, width: 0.9, height: 0.72},
    studentPolygon: [{x: 61, y: 109}, {x: 936, y: 118}, {x: 914, y: 1107}, {x: 48, y: 1091}],
    studentBox: {x: 48, y: 109, width: 888, height: 998}, status: "ready", issues: []
  }]
};

const review: GradingReviewItem = {
  id: "review", gradingRunId: "run", questionResultId: "result", questionId: "question",
  questionNumber: "1", reason: "LOW_RECOGNITION_CONFIDENCE", status: "open",
  score: "0.00", maxScore: "2.00", createdAt: "2026-01-01", updatedAt: "2026-01-01"
};

const report: GradingArtifact = {
  id: "report", gradingRunId: "run", type: "error_report", resultRevision: 2,
  status: "current", preview: {summary: "需要订正", questions: []}, contentHash: "hash",
  previewUrl: "/api/report/preview", downloadUrl: "/api/report/download", error: null,
  createdAt: "2026-01-01", updatedAt: "2026-01-01"
};

function renderPage() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: 0}, mutations: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task/students/submission/grading"]}>
        <Routes><Route path="/tasks/:taskId/students/:submissionId/grading" element={<GradingWorkspacePage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("grading review to report", () => {
  let resolved = false;
  let currentQuestion: QuestionFixture;

  beforeEach(() => {
    resolved = false;
    currentQuestion = questionBase;
    vi.mocked(api).mockReset();
    vi.mocked(correctGradingBlank).mockReset();
    vi.mocked(resolveGradingReview).mockReset();
    vi.mocked(previewArtifact).mockReset();
    vi.mocked(downloadArtifact).mockReset();
    vi.mocked(resolveGradingReview).mockImplementation(async () => {
      resolved = true;
      return {reviewItemId: "review", gradingRunId: "run", questionResultId: "result", status: "final", score: "0.00", remainingReasons: []};
    });
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/student-submissions/submission") return submission as never;
      if (path === "/student-submissions/submission/grading-runs") return [resolved ? {...runBase, status: "completed", stage: "completed", resultRevision: 2, openReviewCount: 0, updatedAt: "2026-01-02"} : runBase] as never;
      if (path === "/grading-runs/run") return (resolved ? {...runBase, status: "completed", stage: "completed", resultRevision: 2, openReviewCount: 0, updatedAt: "2026-01-02"} : runBase) as never;
      if (path === "/grading-runs/run/questions") return [{...currentQuestion, status: resolved ? "final" : "needs_review", reviewReasons: resolved ? [] : currentQuestion.reviewReasons, resultRevision: resolved ? 1 : currentQuestion.resultRevision}] as never;
      if (path === "/grading-runs/run/questions/question") return {...currentQuestion, status: resolved ? "final" : "needs_review", reviewReasons: resolved ? [] : currentQuestion.reviewReasons, resultRevision: resolved ? 1 : currentQuestion.resultRevision} as never;
      if (path === "/grading-runs/run/review-items") return (resolved ? [] : [review]) as never;
      if (path === "/grading-runs/run/artifacts") return (resolved ? [report] : []) as never;
      throw new Error(`unexpected path ${path}`);
    });
  });

  it("refreshes the final review into a current report without a page reload", async () => {
    renderPage();
    const confirm = await screen.findByRole("button", {name: "确认当前判定"});
    fireEvent.click(confirm);
    expect(await screen.findByRole("button", {name: "预览错题报告"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "预览错题报告"}));
    await waitFor(() => expect(previewArtifact).toHaveBeenCalledWith("/api/report/preview"));
    expect(resolveGradingReview).toHaveBeenCalledTimes(1);
  });

  it("disables the confirmation button while the teacher decision is saving", async () => {
    let finish: (() => void) | undefined;
    vi.mocked(resolveGradingReview).mockImplementation(
      () => new Promise((resolve) => {
        finish = () => {
          resolved = true;
          resolve({
            reviewItemId: "review",
            gradingRunId: "run",
            questionResultId: "result",
            status: "final",
            score: "0.00",
            remainingReasons: []
          });
        };
      })
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", {name: "确认当前判定"}));

    const saving = await screen.findByRole("button", {name: "正在保存复核…"});
    expect(saving).toBeDisabled();
    finish?.();
    expect(await screen.findByRole("button", {name: "预览错题报告"})).toBeInTheDocument();
  });

  it("restores the button and shows a request error", async () => {
    vi.mocked(resolveGradingReview).mockRejectedValueOnce(
      new Error("该复核项已经处理")
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", {name: "确认当前判定"}));

    expect(await screen.findByText("该复核项已经处理")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "确认当前判定"})).toBeEnabled();
  });

  it("keeps a final question selectable while other questions still need review", async () => {
    const finalQuestion: QuestionFixture = {
      ...questionBase,
      id: "final-result",
      questionId: "final-question",
      questionNumber: "2",
      status: "final",
      rawScore: "2.00",
      finalScore: "2.00",
      reviewReasons: [],
      evidence: [{
        page_id: "page",
        region_id: "final-region",
        original_bbox: {x: 100, y: 200, width: 120, height: 40},
        cropped_image_path: null,
        recognized_text: "FINAL_QUESTION_ANSWER",
        char_or_step_range: null
      }]
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/student-submissions/submission") return submission as never;
      if (path === "/student-submissions/submission/grading-runs") return [runBase] as never;
      if (path === "/grading-runs/run") return runBase as never;
      if (path === "/grading-runs/run/questions") return [questionBase, finalQuestion] as never;
      if (path === "/grading-runs/run/questions/question") return questionBase as never;
      if (path === "/grading-runs/run/questions/final-question") return finalQuestion as never;
      if (path === "/grading-runs/run/review-items") return [review] as never;
      if (path === "/grading-runs/run/artifacts") return [] as never;
      throw new Error(`unexpected path ${path}`);
    });

    const {container} = renderPage();
    await screen.findByRole("button", {name: "确认当前判定"});
    const questionButtons = container.querySelectorAll<HTMLButtonElement>(
      ".grading-question-list button"
    );
    expect(questionButtons).toHaveLength(2);

    fireEvent.click(questionButtons[1]);

    expect(await screen.findAllByText("FINAL_QUESTION_ANSWER")).toHaveLength(2);
    await waitFor(() => expect(questionButtons[1]).toHaveClass("active"));
  });

  it("uses three accurately named layer controls with a complete frame visible by default", async () => {
    currentQuestion = {
      ...questionBase,
      questionFrames: [{
        id: "captured-frame-region",
        questionId: "question",
        pageId: "page",
        polygon: [{x: 71, y: 119}, {x: 928, y: 126}, {x: 905, y: 1118}, {x: 54, y: 1098}]
      }],
      blankAnchors: [{
        blankKey: "B1",
        pageId: "page",
        coordinateSpace: "student_page_pixel",
        studentPolygon: [{x: 201, y: 291}, {x: 421, y: 291}, {x: 421, y: 351}, {x: 201, y: 351}],
        studentBBox: {x: 201, y: 291, width: 220, height: 60}
      }]
    };
    const {container} = renderPage();

    const frames = await screen.findByRole("checkbox", {name: "完整题框"});
    const anchors = screen.getByRole("checkbox", {name: "空位锚点"});
    const recognition = screen.getByRole("checkbox", {name: "识别证据"});
    expect(frames).toBeChecked();
    expect(anchors).not.toBeChecked();
    expect(recognition).toBeChecked();
    await waitFor(() => expect(container.querySelector(".grading-question-frame")).toHaveAttribute(
      "points",
      "71,119 928,126 905,1118 54,1098"
    ));
    expect(container.querySelector(".grading-layer-legend--frame")).toBeInTheDocument();
    expect(container.querySelector(".grading-layer-legend--anchor")).toBeInTheDocument();
    expect(container.querySelector(".grading-layer-legend--evidence")).toBeInTheDocument();

    fireEvent.click(anchors);
    fireEvent.click(recognition);
    fireEvent.click(frames);
    expect(container.querySelector(".grading-question-frame")).not.toBeInTheDocument();
    expect(container.querySelector(".grading-blank-anchor")).toHaveAttribute(
      "points",
      "201,291 421,291 421,351 201,351"
    );
    expect(container.querySelector(".grading-recognition-evidence")).not.toBeInTheDocument();
    fireEvent.click(recognition);
    expect(container.querySelector(".grading-recognition-evidence")).toBeInTheDocument();
    expect(screen.queryByText("证据题框")).not.toBeInTheDocument();
  });

  it("keeps terminal progress visible and shows evidence with fit-page controls", async () => {
    const {container} = renderPage();

    const progress = await screen.findByRole("progressbar", {name: "批改进度"});
    expect(progress).toHaveTextContent("1/1");
    expect(progress).toHaveTextContent("自动批改完成，1 项待复核");
    expect(progress).toHaveAttribute("aria-valuemin", "0");
    expect(progress).toHaveAttribute("aria-valuenow", "100");
    expect(progress).toHaveAttribute("aria-valuemax", "100");
    expect(await screen.findByRole("heading", {name: "批改证据"})).toBeInTheDocument();
    expect(screen.getByText("学生识别")).toBeInTheDocument();
    expect(screen.getByText("标准答案")).toBeInTheDocument();
    expect(screen.getByText("规则/工具")).toBeInTheDocument();
    expect(screen.getByText("判分原因")).toBeInTheDocument();
    expect(screen.getByText("本题得分")).toBeInTheDocument();
    expect(screen.getByAltText("第 1 页批改证据")).toHaveAttribute(
      "src",
      "/api/grading-question-results/result/evidence/region/preview"
    );
    const evidenceCard = screen.getByRole("button", {name: /第 1 页批改证据/});
    fireEvent.click(evidenceCard);
    expect(evidenceCard).toHaveClass("active");
    const fitPage = screen.getByRole("button", {name: "整页"});
    const fitWidth = screen.getByRole("button", {name: "适宽"});
    expect(fitPage).toHaveClass("active");
    fireEvent.click(fitWidth);
    expect(fitWidth).toHaveClass("active");

    fireEvent.click(screen.getByRole("button", {name: "聚焦试卷"}));
    expect(container.querySelector(".grading-layout")).toHaveClass("is-focused");
    fireEvent.click(screen.getByRole("button", {name: "退出聚焦"}));
    expect(container.querySelector(".grading-layout")).not.toHaveClass("is-focused");
    expect(fitWidth).toHaveClass("active");
  });

  it("does not substitute the current submission frame when a captured run has none", async () => {
    currentQuestion = {...questionBase, questionFrames: []};
    const {container} = renderPage();

    await screen.findAllByRole("checkbox");
    await waitFor(() => expect(container.querySelector(".grading-overlay-warning")).toBeInTheDocument());
    expect(container.querySelector(".grading-question-frame")).not.toBeInTheDocument();
  });

  it("labels legacy frame fallback and hides unmapped blank anchors", async () => {
    currentQuestion = {
      ...questionBase,
      blankAnchors: [{
        blankKey: "B1",
        pageId: "page",
        coordinateSpace: "student_page_pixel",
        studentPolygon: [],
        studentBBox: {x: 201, y: 291, width: 220, height: 60}
      }]
    };
    const {container} = renderPage();

    const anchors = (await screen.findAllByRole("checkbox"))[1];
    fireEvent.click(anchors);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(container.querySelector(".grading-blank-anchor")).not.toBeInTheDocument();
  });

  it("corrects one dynamic blankKey with the exact frozen versions", async () => {
    currentQuestion = {
      ...questionBase,
      questionType: "fill_blank",
      gradingRevision: 8,
      frameSetId: "frame-v3",
      blankConfigVersionId: "config-v4",
      processingRevisionId: "processing-v6",
      blankResults: [blankFixture("B1", 0), blankFixture("B2", 1), blankFixture("B3", 2)].map((blank) => ({
        ...blank,
        frameSetId: null,
        blankConfigVersionId: null,
        processingRevisionId: null,
        gradingRevision: null
      }))
    };
    vi.mocked(correctGradingBlank).mockResolvedValue({
      questionResultId: "result",
      blankKey: "B2",
      gradingRevision: 9,
      runRevision: 2,
      frameSetId: "frame-v3",
      blankConfigVersionId: "config-v4",
      processingRevisionId: "processing-v6",
      blankResult: blankFixture("B2", 1),
      affectedResultIds: ["result"]
    });
    renderPage();

    fireEvent.change(await screen.findByLabelText("B2 学生答案修正"), {
      target: {value: "教师修正后的第二空"}
    });
    fireEvent.click(screen.getByRole("button", {name: "B2 按修正答案重判"}));

    await waitFor(() => expect(correctGradingBlank).toHaveBeenCalledWith("result", "B2", {
      teacherReason: "已查看学生原图，确认本题判定",
      expectedGradingRevision: 8,
      frameSetId: "frame-v3",
      blankConfigVersionId: "config-v4",
      processingRevisionId: "processing-v6",
      recognizedText: "教师修正后的第二空"
    }));
    expect(vi.mocked(correctGradingBlank).mock.calls[0][1]).toBe("B2");
  });
});
