// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "../../client/src/components/StatusBadge.js";
import { AnswerProgress } from "../../client/src/features/answer-config/AnswerProgress.js";
import type {
  AnswerConfigProgress,
  AnswerConfigStatus,
} from "../../shared/contracts.js";

describe("StatusBadge", () => {
  it("uses visible text in addition to color", () => {
    render(<StatusBadge status="review_pending" />);
    expect(screen.getByText("待复核")).toBeInTheDocument();
  });

  it("shows explicit text for every answer-configuration phase", () => {
    const progress: AnswerConfigProgress = {
      total: 3,
      pending: 1,
      processing: 0,
      webSearched: 1,
      modelGenerated: 1,
      needsAttention: 1,
      approved: 1,
      rejected: 0,
      failed: 0,
    };
    const states: Array<[AnswerConfigStatus, string]> = [
      ["not_started", "尚未启动"],
      ["queued", "等待处理"],
      ["extracting", "正在识别试卷"],
      ["searching", "正在联网搜索"],
      ["generating", "正在生成答案"],
      ["review_pending", "等待教师审核"],
      ["approved", "答案版本已发布"],
      ["failed", "处理失败，可重试"],
    ];
    for (const [status, label] of states) {
      const view = render(
        <AnswerProgress status={status} progress={progress} />,
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      view.unmount();
    }
  });
});
