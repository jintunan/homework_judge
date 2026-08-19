import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MathContentEditor } from "../../client/src/components/MathContentEditor";

describe("MathContentEditor", () => {
  it("starts in a rendered reading view and exposes a plain-language edit action", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const {container} = render(<MathContentEditor value={"场强 $E=F/q$"} ariaLabel="编辑题干" onChange={onChange} />);
    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(screen.queryByText("$E=F/q$")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "编辑内容"}));
    expect(screen.getByRole("textbox", {name: "编辑题干"})).toBeInTheDocument();
    expect(screen.getByText(/直接修改文字/)).toBeInTheDocument();
  });

  it("cancels text changes without notifying the parent", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MathContentEditor value="原始文字" ariaLabel="编辑解析" onChange={onChange} />);
    await user.click(screen.getByRole("button", {name: "编辑内容"}));
    const surface = screen.getByRole("textbox", {name: "编辑解析"});
    surface.textContent = "修改后的文字";
    fireEvent.input(surface);
    await user.click(screen.getByRole("button", {name: /取消/}));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("原始文字")).toBeInTheDocument();
  });

  it("commits plain text changes only after completing field editing", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MathContentEditor value="原始文字" ariaLabel="编辑答案" onChange={onChange} />);
    await user.click(screen.getByRole("button", {name: "编辑内容"}));
    const surface = screen.getByRole("textbox", {name: "编辑答案"});
    surface.textContent = "教师直接输入的新答案";
    fireEvent.input(surface);
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", {name: /完成编辑/}));
    expect(onChange).toHaveBeenCalledWith("教师直接输入的新答案");
  });

  it("keeps linked answers read-only while rendering their formulas", () => {
    const {container} = render(<MathContentEditor disabled value={"$D$，由 $F=qE$"} ariaLabel="编辑标准答案" onChange={vi.fn()} />);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", {name: "编辑内容"})).not.toBeInTheDocument();
  });
});
