import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "../../client/src/components/EmptyState";
import { StatusBadge } from "../../client/src/components/StatusBadge";

describe("shared UI states", () => {
  it("renders an explicit processing status label", () => {
    render(<StatusBadge status="exam_recognizing" />);
    expect(screen.getByText("识别题目")).toBeInTheDocument();
  });

  it("renders an actionable empty state", () => {
    render(
      <EmptyState title="还没有识别任务" action={<button>新建</button>}>
        上传试卷和参考答案
      </EmptyState>
    );
    expect(screen.getByRole("heading", {name: "还没有识别任务"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "新建"})).toBeInTheDocument();
  });
});

