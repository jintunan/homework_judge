import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {beforeEach, describe, expect, it, vi} from "vitest";
import type {LayeredIssue, QuestionFrameItem, ReviewDetail} from "../../shared/contracts";
import {ReviewPage} from "../../client/src/features/review/ReviewPage";
import {
  api,
  confirmQuestionFrameItem,
  normalizeQuestionFrameDraft,
  rerecognizeQuestionFrameItem
} from "../../client/src/lib/api";

vi.mock("../../client/src/lib/api", () => ({
  api: vi.fn(),
  confirmQuestionFrameItem: vi.fn(),
  confirmQuestionFrameSet: vi.fn(),
  normalizeQuestionFrameDraft: vi.fn(),
  rerecognizeQuestionFrameItem: vi.fn(),
  saveQuestionFrameItem: vi.fn()
}));

const frameItem = (questionId: string, y: number): QuestionFrameItem => ({
  questionId,
  status: "pending",
  revision: 0,
  fragments: [{
    regionKey: `${questionId}:frame:1`,
    templatePageId: "page-1",
    pageNumber: 1,
    x: 0.1,
    y,
    width: 0.8,
    height: 0.2,
    sortOrder: 0,
    source: "model",
    confidence: 0.8,
    issues: []
  }],
  issues: [],
  carriedFromItemId: null,
  confirmedAt: null,
  confirmedBy: null
});

const reviewDetail = (geometryIssues: LayeredIssue[] = []): ReviewDetail => {
  const items = [frameItem("q1", 0.1), frameItem("q2", 0.5)];
  const questions = items.map((item, index) => ({
    id: item.questionId,
    sortOrder: index,
    original: {number: String(index + 1), stem: `题目 ${index + 1}`, options: [], type: "unknown", score: 1},
    effective: {number: String(index + 1), stem: `题目 ${index + 1}`, options: [], type: "unknown", score: 1},
    sourcePages: [1],
    answerRegions: [],
    confidence: 0.9,
    issues: [],
    isDuplicate: false,
    confirmationStatus: "pending" as const,
    questionFrame: item,
    match: {
      id: `match-${index + 1}`,
      answerEntryId: null,
      method: "manual",
      numberScore: 1,
      stemScore: 1,
      orderScore: 1,
      totalScore: 1,
      reasons: [],
      status: "matched",
      answer: "",
      explanation: "",
      answerSourcePages: []
    }
  }));
  return {
    task: {
      id: "task",
      title: "题框页面测试",
      status: "review_pending",
      active_run_id: null,
      last_error_code: null,
      last_error_message: null,
      created_at: "2026-08-10T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
      questionCount: 2,
      confirmedCount: 0
    },
    questions,
    answerEntries: [],
    documents: [{id: "exam", role: "exam", original_name: "exam.pdf", page_count: 1}],
    pages: [{
      id: "page-1",
      document_id: "exam",
      page_number: 1,
      width: 1000,
      height: 1400,
      role: "exam",
      imageUrl: "/api/pages/page-1"
    }],
    questionFrameSet: {
      id: "frame-set",
      taskId: "task",
      versionNumber: 1,
      status: "draft",
      revision: 0,
      baseFrameSetId: null,
      source: "model",
      contentHash: "hash",
      items,
      createdAt: "2026-08-10T00:00:00Z",
      createdBy: "model",
      updatedAt: "2026-08-10T00:00:00Z",
      confirmedAt: null,
      confirmedBy: null
    },
    studentUploadGate: {
      ready: false,
      frameSetId: "frame-set",
      frameSetVersion: 1,
      missingQuestionIds: [],
      unconfirmedQuestionIds: ["q1", "q2"],
      issues: [
        {code: "QUESTION_FRAME_SET_UNCONFIRMED", message: "当前题框版本尚未冻结", layer: "question_frame"},
        {code: "QUESTION_FRAME_UNCONFIRMED", message: "题框尚未由教师确认", layer: "question_frame", questionId: "q1"},
        {code: "QUESTION_FRAME_UNCONFIRMED", message: "题框尚未由教师确认", layer: "question_frame", questionId: "q2"},
        ...geometryIssues
      ],
      blankConfigIssues: []
    }
  };
};

function renderPage(detail: ReviewDetail) {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/tasks/task/review") return detail as never;
    return {} as never;
  });
  vi.mocked(normalizeQuestionFrameDraft).mockResolvedValue(detail.questionFrameSet!);
  vi.mocked(confirmQuestionFrameItem).mockResolvedValue(detail.questionFrameSet!);
  vi.mocked(rerecognizeQuestionFrameItem).mockResolvedValue({
    questionId: "q1",
    runId: "single-run",
    frameSet: detail.questionFrameSet!,
    recognizedQuestion: {
      number: "1",
      stem: "跨页完整题目",
      options: [],
      type: "unknown",
      score: 1,
      sourcePages: [1],
      confidence: 0.95,
      issues: []
    },
    teacherOverridePreserved: false
  });
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task/review"]}>
        <Routes><Route path="/tasks/:taskId/review" element={<ReviewPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("question-frame review page", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not treat pending confirmation as geometry and keeps the reference pane collapsed", async () => {
    const {container} = renderPage(reviewDetail());

    await screen.findByRole("heading", {name: "题框：1"});
    expect(container.querySelector(".review-gate-summary")).toHaveTextContent("2 道题框待确认");
    expect(screen.queryByText("题框尚未由教师确认")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "确认题框"})).toBeEnabled();
    expect(container.querySelector(".review-layout")).toHaveClass("review-layout--preview-closed");
    expect(container.querySelector(".preview")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: "打开参考页"}));
    expect(container.querySelector(".review-layout")).toHaveClass("review-layout--preview-open");
    expect(await screen.findByRole("button", {name: "原试卷"})).toBeInTheDocument();
  });

  it("distinguishes all items confirmed from the frame set being frozen", async () => {
    const detail = reviewDetail();
    const confirmedItems = detail.questionFrameSet!.items.map((item) => ({
      ...item,
      status: "confirmed" as const,
      confirmedAt: "2026-08-14T00:00:00Z",
      confirmedBy: "teacher"
    }));
    detail.questionFrameSet = {...detail.questionFrameSet!, items: confirmedItems};
    detail.questions = detail.questions.map((question, index) => ({
      ...question,
      questionFrame: confirmedItems[index]
    }));
    detail.studentUploadGate = {
      ...detail.studentUploadGate!,
      unconfirmedQuestionIds: [],
      issues: [{
        code: "QUESTION_FRAME_SET_UNCONFIRMED",
        message: "当前题框版本尚未冻结",
        layer: "question_frame"
      }]
    };
    const {container} = renderPage(detail);

    await screen.findByRole("heading", {name: "题框：1"});
    expect(container.querySelector(".review-gate-summary")).toHaveTextContent(
      "全部题框已逐题确认，等待冻结整套题框"
    );
    expect(container.querySelector(".review-gate-summary")).not.toHaveTextContent(
      "0 道题框待确认"
    );
    expect(screen.getByRole("button", {name: "冻结整套题框"})).toBeEnabled();
  });

  it("names every fill-blank config blocker and jumps to the selected question", async () => {
    const detail = reviewDetail();
    detail.questions = detail.questions.map((question, index) => ({
      ...question,
      original: {...question.original, number: String(index + 11)},
      effective: {...question.effective, number: String(index + 11)}
    }));
    detail.studentUploadGate = {
      ...detail.studentUploadGate!,
      blankConfigIssues: [
        {
          code: "BLANK_CONFIG_MISSING",
          message: "填空题尚未建立逐空配置",
          layer: "blank_config",
          questionId: "q1",
          questionNumber: "11",
          status: "pending",
          source: null
        },
        {
          code: "BLANK_CONFIG_FRAME_MISMATCH",
          message: "逐空配置绑定的题框版本已变化",
          layer: "blank_config",
          questionId: "q2",
          questionNumber: "12",
          status: "stale",
          source: "teacher"
        }
      ]
    };
    renderPage(detail);

    await screen.findByRole("heading", {name: "题框：11"});
    expect(screen.getByRole("button", {name: "第 11 题：未配置"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "第 12 题：题框变化后需重新确认"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "11 配置待确认"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "12 配置待确认"})).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: "第 12 题：题框变化后需重新确认"}));

    expect(await screen.findByRole("heading", {name: "题框：12"})).toBeInTheDocument();
  });

  it("shows related-question overlap as a blocker and runs zero-model auto layout", async () => {
    const {container} = renderPage(reviewDetail([{
      code: "frame_cross_question_overlap",
      message: "第 1 题与第 2 题的题框重叠 40%，请调整两题边界后再确认",
      layer: "question_frame",
      questionId: "q1",
      relatedQuestionId: "q2",
      nextAction: "edit_question_frame"
    }]));

    await screen.findByRole("heading", {name: "题框：1"});
    expect(container.querySelector(".review-gate-summary")).toHaveTextContent("1 处题框边界冲突，前往处理");
    const navigation = container.querySelectorAll(".question-nav__list > button");
    fireEvent.click(navigation[1]);
    expect(screen.getByText(/第 1 题与第 2 题的题框重叠 40%/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "确认题框"})).toBeDisabled();

    fireEvent.click(screen.getByRole("button", {name: "自动补齐题框（不调用模型）"}));
    await waitFor(() => expect(normalizeQuestionFrameDraft).toHaveBeenCalledWith("frame-set", 0));
  });

  it("rerecognizes only the current question with all current frame fragments", async () => {
    renderPage(reviewDetail());

    await screen.findByRole("heading", {name: "题框：1"});
    fireEvent.click(screen.getByRole("button", {name: "保存并重新识别本题"}));

    await waitFor(() => expect(rerecognizeQuestionFrameItem).toHaveBeenCalledWith(
      "frame-set",
      "q1",
      0,
      [expect.objectContaining({regionKey: "q1:frame:1"})]
    ));
    expect(await screen.findAllByText("本题原文已重新识别，请重新确认题目和题框")).not.toHaveLength(0);
  });
});
