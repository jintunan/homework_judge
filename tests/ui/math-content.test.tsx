import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MathContent } from "../../client/src/components/MathContent";

describe("MathContent", () => {
  it("renders formulas while preserving surrounding Chinese text", () => {
    const {container} = render(<MathContent value={"由 $E=\\frac{F}{q}$ 可知。\n方向相同。"} />);
    expect(screen.getByText(/由/)).toBeInTheDocument();
    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(container.textContent).toContain("方向相同");
    const visibleClone = container.cloneNode(true) as HTMLElement;
    visibleClone.querySelectorAll("annotation").forEach((node) => node.remove());
    expect(visibleClone.textContent).not.toContain("\\frac");
  });

  it("shows malformed formulas without breaking valid neighbors", () => {
    const {container} = render(<MathContent value={"合法 $F=qE$，异常 $\\badcommand{x}$。"} />);
    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(screen.getByText("公式需检查")).toBeInTheDocument();
    expect(container.textContent).toContain("$\\badcommand{x}$");
  });

  it("does not create executable links or images from untrusted commands", () => {
    const {container} = render(<MathContent value={"$\\href{https://example.com}{x}$ $\\includegraphics{https://example.com/a.png}$"} />);
    expect(container.querySelector("a[href]")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container.querySelector("script")).not.toBeInTheDocument();
  });
});
