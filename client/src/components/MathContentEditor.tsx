import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Edit3, Plus, X } from "lucide-react";
import { FormulaEditor } from "@/components/FormulaEditor";
import { MathContent } from "@/components/MathContent";
import { parseMathContent, renderLatex, type MathContentSegment, type MathDelimiter } from "@/lib/math-content";

type MathContentEditorProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  ariaLabel: string;
  compact?: boolean;
};

type FormulaTarget = {
  node: HTMLElement | null;
  latex: string;
  display: boolean;
  range: Range | null;
};

const CARET_MARK = "\u200B";

function delimiterPair(delimiter: MathDelimiter): [string, string] {
  if (delimiter === "dollar-display") return ["$$", "$$"];
  if (delimiter === "paren") return ["\\(", "\\)"];
  if (delimiter === "bracket") return ["\\[", "\\]"];
  return ["$", "$"];
}

function createMathNode(segment: Exclude<MathContentSegment, {type: "text"}>): HTMLSpanElement {
  const node = document.createElement("span");
  node.contentEditable = "false";
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.dataset.math = "true";
  node.dataset.latex = segment.latex;
  node.dataset.display = String(segment.display);
  node.dataset.delimiter = segment.delimiter;
  node.className = segment.display ? "mixed-math__formula mixed-math__formula--display" : "mixed-math__formula";
  if (segment.type === "invalid") {
    node.dataset.invalid = "true";
    node.dataset.raw = segment.raw;
    node.classList.add("mixed-math__formula--invalid");
    node.textContent = segment.raw;
    node.title = `${segment.message}；按 Enter 编辑公式`;
    node.setAttribute("aria-label", `公式需检查：${segment.raw}`);
    return node;
  }
  updateMathNode(node, segment.latex, segment.display);
  return node;
}

function updateMathNode(node: HTMLElement, latex: string, display: boolean): void {
  const rendered = renderLatex(latex, display);
  node.dataset.latex = latex;
  node.dataset.display = String(display);
  node.dataset.invalid = "false";
  delete node.dataset.raw;
  node.classList.remove("mixed-math__formula--invalid");
  node.classList.toggle("mixed-math__formula--display", display);
  node.title = "点击或按 Enter 编辑公式";
  node.setAttribute("aria-label", `公式：${latex}，按 Enter 编辑`);
  if (rendered.valid) node.innerHTML = rendered.html;
  else {
    node.dataset.invalid = "true";
    node.classList.add("mixed-math__formula--invalid");
    node.textContent = latex;
    node.title = rendered.message;
  }
}

function renderEditor(root: HTMLElement, value: string): void {
  const fragment = document.createDocumentFragment();
  for (const segment of parseMathContent(value)) {
    if (segment.type === "text") fragment.append(document.createTextNode(segment.text));
    else fragment.append(createMathNode(segment));
  }
  root.replaceChildren(fragment);
}

function serializeChildren(parent: Node): string {
  let output = "";
  const children = Array.from(parent.childNodes);
  children.forEach((child, index) => {
    if (child.nodeType === Node.TEXT_NODE) {
      output += (child.nodeValue ?? "").replaceAll(CARET_MARK, "");
      return;
    }
    if (!(child instanceof HTMLElement)) return;
    if (child.dataset.math === "true") {
      if (child.dataset.invalid === "true" && child.dataset.raw) {
        output += child.dataset.raw;
        return;
      }
      const latex = child.dataset.latex ?? "";
      const delimiter = (child.dataset.delimiter as MathDelimiter | undefined) ?? (child.dataset.display === "true" ? "dollar-display" : "dollar-inline");
      const [open, close] = delimiterPair(delimiter);
      output += `${open}${latex}${close}`;
      return;
    }
    if (child.tagName === "BR") {
      output += "\n";
      return;
    }
    const isBlock = child.tagName === "DIV" || child.tagName === "P";
    if (isBlock && output && !output.endsWith("\n")) output += "\n";
    output += serializeChildren(child);
    if (isBlock && index < children.length - 1 && !output.endsWith("\n")) output += "\n";
  });
  return output;
}

function selectionInside(root: HTMLElement): Range | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return null;
  return range.cloneRange();
}

export function MathContentEditor({value, onChange, disabled = false, ariaLabel, compact = false}: MathContentEditorProps) {
  const [editing, setEditing] = useState(false);
  const [formulaTarget, setFormulaTarget] = useState<FormulaTarget | null>(null);
  const [error, setError] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);
  const savedRangeRef = useRef<Range | null>(null);

  useEffect(() => {
    if (editing && editorRef.current) renderEditor(editorRef.current, value);
  }, [editing, value]);

  const rememberSelection = useCallback(() => {
    const root = editorRef.current;
    if (!root) return;
    const range = selectionInside(root);
    if (range) savedRangeRef.current = range;
  }, []);

  const openExistingFormula = (node: HTMLElement) => {
    setFormulaTarget({
      node,
      latex: node.dataset.latex ?? "",
      display: node.dataset.display === "true",
      range: null
    });
  };

  const requestInsert = (display: boolean) => {
    const root = editorRef.current;
    if (!root) return;
    const range = savedRangeRef.current ?? document.createRange();
    if (!savedRangeRef.current) {
      range.selectNodeContents(root);
      range.collapse(false);
    }
    setFormulaTarget({node: null, latex: "", display, range: range.cloneRange()});
  };

  const confirmFormula = (latex: string) => {
    const target = formulaTarget;
    const root = editorRef.current;
    if (!target || !root) return;
    if (target.node) {
      updateMathNode(target.node, latex, target.display);
      target.node.dataset.delimiter ||= target.display ? "dollar-display" : "dollar-inline";
      target.node.focus();
    } else {
      const segment: MathContentSegment = {
        type: "math",
        latex,
        display: target.display,
        delimiter: target.display ? "dollar-display" : "dollar-inline"
      };
      const node = createMathNode(segment);
      const caret = document.createTextNode(CARET_MARK);
      const range = target.range ?? document.createRange();
      range.deleteContents();
      range.insertNode(caret);
      range.insertNode(node);
      range.setStartAfter(caret);
      range.collapse(true);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      savedRangeRef.current = range.cloneRange();
      root.focus();
    }
    setFormulaTarget(null);
    setError("");
  };

  const finish = () => {
    const root = editorRef.current;
    if (!root) return;
    try {
      onChange(serializeChildren(root));
      setEditing(false);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "内容整理失败，请保留页面并重试");
    }
  };

  const pastePlainText = (event: React.ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    const root = editorRef.current;
    if (!root) return;
    const range = selectionInside(root) ?? savedRangeRef.current;
    if (!range) return;
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    savedRangeRef.current = range.cloneRange();
  };

  if (!editing) {
    return (
      <div className={`math-editor ${compact ? "math-editor--compact" : ""} ${disabled ? "math-editor--disabled" : ""}`}>
        <MathContent value={value} />
        {!value && <span className="math-editor__placeholder">暂无内容</span>}
        {!disabled && <button type="button" className="math-editor__edit" onClick={() => setEditing(true)}><Edit3 size={14} />编辑内容</button>}
      </div>
    );
  }

  return (
    <div className={`math-editor math-editor--editing ${compact ? "math-editor--compact" : ""}`}>
      <div className="mixed-math__toolbar">
        <span>直接修改文字，点击公式可视化编辑</span>
        <div>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => requestInsert(false)}><Plus size={14} />行内公式</button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => requestInsert(true)}><Plus size={14} />独立公式</button>
        </div>
      </div>
      <div
        ref={editorRef}
        className="mixed-math__surface"
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-label={ariaLabel}
        aria-multiline="true"
        onInput={rememberSelection}
        onKeyUp={rememberSelection}
        onMouseUp={rememberSelection}
        onFocus={rememberSelection}
        onPaste={pastePlainText}
        onClick={(event) => {
          const node = (event.target as HTMLElement).closest<HTMLElement>("[data-math='true']");
          if (node) openExistingFormula(node);
        }}
        onKeyDown={(event) => {
          const node = (event.target as HTMLElement).closest<HTMLElement>("[data-math='true']");
          if (node && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            openExistingFormula(node);
          }
        }}
      />
      {error && <div className="formula-editor__error" role="alert">{error}</div>}
      <div className="mixed-math__actions">
        <button type="button" className="button" onClick={() => {setEditing(false); setError("");}}><X size={15} />取消</button>
        <button type="button" className="button button--primary" onClick={finish}><Check size={15} />完成编辑</button>
      </div>
      {formulaTarget && (
        <FormulaEditor
          initialLatex={formulaTarget.latex}
          display={formulaTarget.display}
          onCancel={() => {setFormulaTarget(null); formulaTarget.node?.focus();}}
          onConfirm={confirmFormula}
        />
      )}
    </div>
  );
}
