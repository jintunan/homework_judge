import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDuplicateQuestionDialog } from "@/features/review/ConfirmDuplicateQuestionDialog";

describe("duplicate question confirmation", () => {
  it("explains the reversible effect and confirms once", () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    render(
      <ConfirmDuplicateQuestionDialog
        number="12"
        busy={false}
        error=""
        onCancel={cancel}
        onConfirm={confirm}
      />
    );

    expect(screen.getByRole("dialog", {name: "将第 12 题标记为重复？"})).toBeInTheDocument();
    expect(screen.getByText(/关联答案会重新变为可用/)).toBeInTheDocument();
    expect(screen.getByText(/可以在“重复题”筛选中查看并恢复/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "标记为重复"}));
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("disables dismissal and repeated submission while busy", () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    render(
      <ConfirmDuplicateQuestionDialog
        number="10"
        busy
        error="学生答卷处理中"
        onCancel={cancel}
        onConfirm={confirm}
      />
    );

    expect(screen.getByText("学生答卷处理中")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "正在标记…"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "关闭"})).toBeDisabled();
  });
});
