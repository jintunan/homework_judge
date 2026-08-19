import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AnswerGradingDraftPreview, GradingConfig, ReviewQuestion } from "@shared/contracts";
import { GradingConfigPanel } from "@/features/grading/GradingConfigPanel";
import {
  api,
  applyAnswerGradingDraft,
  generateAnswerGradingDraft
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
  applyAnswerGradingDraft: vi.fn(),
  generateAnswerGradingDraft: vi.fn()
}));

const question: ReviewQuestion = {
  id: "q1",
  sortOrder: 0,
  original: {number: "11", stem: "第一处______，第二处______。", options: [], type: "fill_blank", score: 5},
  effective: {number: "11", stem: "第一处______，第二处______。", options: [], type: "fill_blank", score: 5},
  sourcePages: [1], answerRegions: [], confidence: 1, issues: [], isDuplicate: false,
  confirmationStatus: "confirmed",
  match: {
    id: "m1", answerEntryId: "a1", method: "manual", numberScore: 1, stemScore: 1,
    orderScore: 1, totalScore: 1, reasons: [], status: "confirmed",
    answer: "电荷转移；CD", explanation: "旧解析", answerSourcePages: [1]
  }
};

const config: GradingConfig = {
  questionId: "q1", questionType: "fill_blank", maxScore: "5.00", configVersion: 1,
  confirmationStatus: "confirmed", frameSetId: "frame", blanks: [
    {blankKey: "B1", sortOrder: 0, maxScore: "2.50", answerKind: "text", standardAnswers: ["电荷转移"], synonyms: []},
    {blankKey: "B2", sortOrder: 1, maxScore: "2.50", answerKind: "text", standardAnswers: ["CD"], synonyms: []}
  ],
  initialization: {source: "saved", signals: null, warnings: [], autoConfirmable: true, blockingReasons: []}
};

const preview: AnswerGradingDraftPreview = {
  runId: "draft-run", questionId: "q1", createdAt: "2026-08-14T00:00:00Z",
  current: {
    questionType: "fill_blank", standardAnswer: "电荷转移；CD", explanation: "旧解析",
    maxScore: "5.00", answerOptions: [], blanks: config.blanks, rubricPoints: [], warnings: []
  },
  draft: {
    questionType: "fill_blank", standardAnswer: "电荷转移；守；CD", explanation: "新解析",
    maxScore: "5.00", answerOptions: [], rubricPoints: [], warnings: ["请核对空位数量"],
    blanks: [
      {blankKey: "B1", sortOrder: 0, maxScore: "1.67", answerKind: "text", standardAnswers: ["电荷转移"], synonyms: []},
      {blankKey: "B2", sortOrder: 1, maxScore: "1.67", answerKind: "text", standardAnswers: ["守"], synonyms: []},
      {blankKey: "B3", sortOrder: 2, maxScore: "1.66", answerKind: "text", standardAnswers: ["CD"], synonyms: []}
    ]
  },
  warnings: ["OCR 题干识别到 2 个空，题目原图与答案草稿判断为 3 个空，请教师重点核对。"]
};

describe("answer and grading regeneration preview", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset().mockResolvedValue(config);
    vi.mocked(generateAnswerGradingDraft).mockReset().mockResolvedValue(preview);
    vi.mocked(applyAnswerGradingDraft).mockReset().mockResolvedValue({
      runId: "draft-run", questionId: "q1", applied: true,
      studentResultsInvalidated: true,
      message: "新答案和批改设置已应用；旧识别、分数和报告已失效，请重新处理学生答卷。"
    });
  });

  it("previews three blanks and cancel leaves the draft unapplied", async () => {
    render(<GradingConfigPanel question={question} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", {name: "重新生成答案和批改设置"}));
    expect(await screen.findByRole("dialog", {name: "预览新答案和批改设置"})).toBeInTheDocument();
    expect(screen.getByText("逐空批改设置（3 空）")).toBeInTheDocument();
    expect(screen.getByText(/OCR 题干识别到 2 个空/)).toBeInTheDocument();
    expect(screen.getByText("第 3 空 · 1.66 分")).toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "取消"}));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(applyAnswerGradingDraft).not.toHaveBeenCalled();
  });

  it("applies only by run id and reports historical results invalidated", async () => {
    const onApplied = vi.fn().mockResolvedValue(undefined);
    render(<GradingConfigPanel question={question} onApplied={onApplied} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", {name: "重新生成答案和批改设置"}));
    await user.click(await screen.findByRole("button", {name: "应用草稿并使旧结果失效"}));
    await waitFor(() => expect(applyAnswerGradingDraft).toHaveBeenCalledWith("q1", "draft-run"));
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/旧识别、分数和报告已失效/)).toBeInTheDocument();
  });
});
