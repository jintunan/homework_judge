import { describe, expect, it } from "vitest";
import { parseMathContent, renderLatex, serializeMathContent } from "../../client/src/lib/math-content";

describe("math content parser", () => {
  it("parses all supported inline and display delimiters in order", () => {
    const source = "库仑力 $F=k\\frac{q_1q_2}{r^2}$，场强 \\(E=F/q\\)。\n$$W=qEd$$\n\\[U=Ed\\]";
    const segments = parseMathContent(source);
    expect(segments.filter((item) => item.type === "math")).toMatchObject([
      {latex: "F=k\\frac{q_1q_2}{r^2}", display: false, delimiter: "dollar-inline"},
      {latex: "E=F/q", display: false, delimiter: "paren"},
      {latex: "W=qEd", display: true, delimiter: "dollar-display"},
      {latex: "U=Ed", display: true, delimiter: "bracket"}
    ]);
    expect(serializeMathContent(segments)).toBe(source);
  });

  it("preserves escaped dollars and ordinary currency text", () => {
    const source = "资料费 \\$5，优惠后 $2，文字转义 \\\\(x\\\\)，不是物理公式";
    const segments = parseMathContent(source);
    expect(segments).toEqual([{type: "text", text: source}]);
  });

  it("keeps malformed formulas as editable invalid segments", () => {
    const source = "电场强度 $E=F/q";
    const segments = parseMathContent(source);
    expect(segments).toMatchObject([
      {type: "text", text: "电场强度 "},
      {type: "invalid", raw: "$E=F/q", latex: "E=F/q", message: "缺少公式结束标记"}
    ]);
    expect(serializeMathContent(segments)).toBe(source);
  });

  it("is stable across repeated parse and serialize cycles", () => {
    const source = "当 $r\\to0$ 时，$F=k\\frac{q_1q_2}{r^2}$。\n\\[E=\\frac{F}{q}\\]";
    let current = source;
    for (let iteration = 0; iteration < 3; iteration += 1) {
      current = serializeMathContent(parseMathContent(current));
    }
    expect(current).toBe(source);
  });

  it("rejects unknown commands without throwing", () => {
    const result = renderLatex("\\definitelyUnknown{x}");
    expect(result.valid).toBe(false);
  });
});
