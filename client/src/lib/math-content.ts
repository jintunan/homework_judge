import katex from "katex";

export type MathDelimiter = "dollar-inline" | "dollar-display" | "paren" | "bracket";

export type MathContentSegment =
  | {type: "text"; text: string}
  | {type: "math"; latex: string; display: boolean; delimiter: MathDelimiter}
  | {type: "invalid"; raw: string; latex: string; display: boolean; delimiter: MathDelimiter; message: string};

export type LatexRenderResult =
  | {valid: true; html: string}
  | {valid: false; message: string};

type DelimiterDefinition = {
  type: MathDelimiter;
  open: string;
  close: string;
  display: boolean;
};

const DELIMITERS: DelimiterDefinition[] = [
  {type: "dollar-display", open: "$$", close: "$$", display: true},
  {type: "bracket", open: "\\[", close: "\\]", display: true},
  {type: "paren", open: "\\(", close: "\\)", display: false},
  {type: "dollar-inline", open: "$", close: "$", display: false}
];

const KATEX_OPTIONS = {
  throwOnError: true,
  trust: false,
  strict: "warn" as const,
  maxSize: 20,
  maxExpand: 1000,
  output: "htmlAndMathml" as const
};

const renderCache = new Map<string, LatexRenderResult>();

function isEscaped(input: string, index: number): boolean {
  let slashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && input[cursor] === "\\"; cursor -= 1) slashCount += 1;
  return slashCount % 2 === 1;
}

function looksLikeCurrency(input: string, index: number): boolean {
  if (input[index] !== "$" || input[index + 1] === "$") return false;
  const amount = input.slice(index + 1).match(/^\d+(?:\.\d+)?/);
  if (!amount) return false;
  const next = input[index + 1 + amount[0].length] ?? "";
  if (next === "$" || /[A-Za-z_{}^\\]/.test(next)) return false;
  return next === "" || /[\s,，。；;元]/.test(next);
}

function delimiterAt(input: string, index: number): DelimiterDefinition | null {
  for (const delimiter of DELIMITERS) {
    if (!input.startsWith(delimiter.open, index)) continue;
    if (delimiter.open.startsWith("$") && isEscaped(input, index)) continue;
    if (delimiter.open.startsWith("\\") && isEscaped(input, index)) continue;
    if (delimiter.type === "dollar-inline" && input.startsWith("$$", index)) continue;
    if (delimiter.type === "dollar-inline" && looksLikeCurrency(input, index)) continue;
    return delimiter;
  }
  return null;
}

function findClosingDelimiter(input: string, start: number, delimiter: DelimiterDefinition): number {
  let cursor = start;
  while (cursor < input.length) {
    const match = input.indexOf(delimiter.close, cursor);
    if (match < 0) return -1;
    if ((delimiter.close.startsWith("$") || delimiter.close.startsWith("\\")) && isEscaped(input, match)) {
      cursor = match + delimiter.close.length;
      continue;
    }
    if (delimiter.type === "dollar-inline" && (input.startsWith("$$", match) || input[match - 1] === "$")) {
      cursor = match + 1;
      continue;
    }
    return match;
  }
  return -1;
}

function appendText(segments: MathContentSegment[], text: string): void {
  if (!text) return;
  const previous = segments.at(-1);
  if (previous?.type === "text") previous.text += text;
  else segments.push({type: "text", text});
}

function errorMessage(reason: unknown): string {
  if (!(reason instanceof Error)) return "公式格式无法解析";
  return reason.message.replace(/^KaTeX parse error:\s*/i, "").slice(0, 160) || "公式格式无法解析";
}

export function renderLatex(latex: string, display = false): LatexRenderResult {
  const key = `${display ? "display" : "inline"}\u0000${latex}`;
  const cached = renderCache.get(key);
  if (cached) return cached;
  let result: LatexRenderResult;
  try {
    result = {
      valid: true,
      html: katex.renderToString(latex, {...KATEX_OPTIONS, displayMode: display})
    };
  } catch (reason) {
    result = {valid: false, message: errorMessage(reason)};
  }
  if (renderCache.size >= 500) renderCache.delete(renderCache.keys().next().value ?? "");
  renderCache.set(key, result);
  return result;
}

export function validateLatex(latex: string, display = false): LatexRenderResult {
  if (!latex.trim()) return {valid: false, message: "公式内容不能为空"};
  return renderLatex(latex, display);
}

export function parseMathContent(raw: string): MathContentSegment[] {
  if (!raw) return [{type: "text", text: ""}];
  const segments: MathContentSegment[] = [];
  let textStart = 0;
  let cursor = 0;

  while (cursor < raw.length) {
    const delimiter = delimiterAt(raw, cursor);
    if (!delimiter) {
      cursor += 1;
      continue;
    }

    appendText(segments, raw.slice(textStart, cursor));
    const contentStart = cursor + delimiter.open.length;
    const closeIndex = findClosingDelimiter(raw, contentStart, delimiter);
    if (closeIndex < 0) {
      const invalidRaw = raw.slice(cursor);
      segments.push({
        type: "invalid",
        raw: invalidRaw,
        latex: raw.slice(contentStart),
        display: delimiter.display,
        delimiter: delimiter.type,
        message: "缺少公式结束标记"
      });
      textStart = raw.length;
      cursor = raw.length;
      break;
    }

    const latex = raw.slice(contentStart, closeIndex);
    const rawFormula = raw.slice(cursor, closeIndex + delimiter.close.length);
    const rendered = validateLatex(latex, delimiter.display);
    if (rendered.valid) {
      segments.push({type: "math", latex, display: delimiter.display, delimiter: delimiter.type});
    } else {
      segments.push({
        type: "invalid",
        raw: rawFormula,
        latex,
        display: delimiter.display,
        delimiter: delimiter.type,
        message: rendered.message
      });
    }
    cursor = closeIndex + delimiter.close.length;
    textStart = cursor;
  }

  appendText(segments, raw.slice(textStart));
  return normalizeMathSegments(segments);
}

export function normalizeMathSegments(segments: MathContentSegment[]): MathContentSegment[] {
  const normalized: MathContentSegment[] = [];
  for (const segment of segments) {
    if (segment.type === "text") appendText(normalized, segment.text);
    else normalized.push(segment);
  }
  return normalized.length > 0 ? normalized : [{type: "text", text: ""}];
}

function delimiterPair(delimiter: MathDelimiter): [string, string] {
  if (delimiter === "dollar-display") return ["$$", "$$"];
  if (delimiter === "paren") return ["\\(", "\\)"];
  if (delimiter === "bracket") return ["\\[", "\\]"];
  return ["$", "$"];
}

export function serializeMathContent(segments: MathContentSegment[]): string {
  return normalizeMathSegments(segments).map((segment) => {
    if (segment.type === "text") return segment.text;
    if (segment.type === "invalid") return segment.raw;
    const [open, close] = delimiterPair(segment.delimiter);
    return `${open}${segment.latex}${close}`;
  }).join("");
}

export function wrapLatex(latex: string, display: boolean): string {
  return display ? `$$${latex}$$` : `$${latex}$`;
}
