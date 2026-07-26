// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AnswerQuestionDraft } from "../../shared/contracts.js";
import { AnswerDraftCard } from "../../client/src/features/answer-config/AnswerDraftCard.js";

const draft: AnswerQuestionDraft = {
  id: "draft-1",
  versionId: "version-1",
  number: "2",
  questionText: "质量为 2 kg 的物体受 6 N 合力，求加速度。",
  type: "calculation",
  maxScore: 8,
  autoAnswer: "a=F/m=3 m/s²",
  autoScoringPoints: [
    { description: "写出 a=F/m", score: 3 },
    { description: "结果与单位正确", score: 5 },
  ],
  autoReason: "联网检索到公开题目及答案。",
  sourceType: "web_searched",
  confidence: 0.93,
  needsAttention: false,
  teacherNumber: null,
  teacherType: null,
  teacherMaxScore: null,
  teacherAnswer: null,
  teacherScoringPoints: null,
  rejectionReason: null,
  reviewStatus: "pending",
  updatedBy: null,
  updatedAt: "2026-01-01T00:00:00.000Z",
  effectiveNumber: "2",
  effectiveType: "calculation",
  effectiveMaxScore: 8,
  effectiveAnswer: "a=F/m=3 m/s²",
  effectiveScoringPoints: [
    { description: "写出 a=F/m", score: 3 },
    { description: "结果与单位正确", score: 5 },
  ],
  latestRunId: "run-1",
  sources: [
    {
      id: "source-1",
      runId: "run-1",
      draftQuestionId: "draft-1",
      title: "公开物理题答案",
      url: "https://example.edu/physics/2",
      snippet: "由牛顿第二定律求得。",
      rank: 0,
      retrievedAt: "2026-01-01T00:00:00.000Z",
    },
  ],
};

afterEach(cleanup);

describe("AnswerDraftCard", () => {
  it("shows evidence, supports physics scoring edits, and reports dirty state", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => undefined);
    const onApprove = vi.fn(async () => undefined);
    const onDirtyChange = vi.fn();
    const onHistory = vi.fn();
    render(
      <AnswerDraftCard
        draft={draft}
        subject="high_school_physics"
        readOnly={false}
        busy={false}
        onSave={onSave}
        onApprove={onApprove}
        onReject={vi.fn(async () => undefined)}
        onResearch={vi.fn(async () => undefined)}
        onRegenerate={vi.fn(async () => undefined)}
        onHistory={onHistory}
        onDirtyChange={onDirtyChange}
      />,
    );

    expect(screen.getByText("计算题")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /公开物理题答案/ }),
    ).toHaveAttribute("href", "https://example.edu/physics/2");
    expect(
      screen.getByDisplayValue("结果与单位正确"),
    ).toBeInTheDocument();

    const answer = screen.getByLabelText("标准答案");
    await user.clear(answer);
    await user.type(answer, "a = 3 m/s²");
    await waitFor(() =>
      expect(onDirtyChange).toHaveBeenLastCalledWith("draft-1", true),
    );
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "calculation",
        standardAnswer: "a = 3 m/s²",
      }),
    );

    await user.click(screen.getByRole("button", { name: "原始记录" }));
    expect(onHistory).toHaveBeenCalledWith("run-1");
  });

  it("keeps a published version read-only", async () => {
    const user = userEvent.setup();
    render(
      <AnswerDraftCard
        draft={{ ...draft, reviewStatus: "approved" }}
        subject="high_school_physics"
        readOnly
        busy={false}
        onSave={vi.fn(async () => undefined)}
        onApprove={vi.fn(async () => undefined)}
        onReject={vi.fn(async () => undefined)}
        onResearch={vi.fn(async () => undefined)}
        onRegenerate={vi.fn(async () => undefined)}
        onHistory={vi.fn()}
        onDirtyChange={vi.fn()}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: /第 2 题/ }),
    );
    expect(screen.getByLabelText("标准答案")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "审核通过" }),
    ).not.toBeInTheDocument();
  });
});
