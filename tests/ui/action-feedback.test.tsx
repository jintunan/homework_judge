import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActionFeedback } from "../../client/src/components/ActionFeedback";

describe("action feedback", () => {
  it("announces progress, errors and disabled reasons with text", () => {
    const {rerender} = render(<ActionFeedback message="正在保存" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在保存");

    rerender(<ActionFeedback error="保存失败，请重试" message="正在保存" />);
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败，请重试");
    expect(screen.queryByText("正在保存")).not.toBeInTheDocument();

    rerender(<ActionFeedback disabledReason="还有 2 道题未确认" />);
    expect(screen.getByRole("status")).toHaveTextContent("还有 2 道题未确认");
  });
});
