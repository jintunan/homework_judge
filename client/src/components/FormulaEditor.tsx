import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MathfieldElement as MathfieldElementType } from "mathlive";
import { validateLatex } from "@/lib/math-content";

type FormulaEditorProps = {
  initialLatex: string;
  display: boolean;
  onCancel: () => void;
  onConfirm: (latex: string) => void;
};

const STRUCTURES = [
  {label: "分数", latex: "\\frac{#0}{#?}"},
  {label: "根号", latex: "\\sqrt{#0}"},
  {label: "上标", latex: "^{#0}"},
  {label: "下标", latex: "_{#0}"},
  {label: "矢量", latex: "\\vec{#0}"},
  {label: "绝对值", latex: "\\left|#0\\right|"},
  {label: "求和", latex: "\\sum_{#0}^{#?}"},
  {label: "积分", latex: "\\int_{#0}^{#?}"},
  {label: "极限", latex: "\\lim_{#0\\to#?}"}
];

const SYMBOLS = [
  {label: "π", latex: "\\pi"},
  {label: "α", latex: "\\alpha"},
  {label: "β", latex: "\\beta"},
  {label: "θ", latex: "\\theta"},
  {label: "λ", latex: "\\lambda"},
  {label: "μ", latex: "\\mu"},
  {label: "ρ", latex: "\\rho"},
  {label: "Δ", latex: "\\Delta"},
  {label: "∞", latex: "\\infty"},
  {label: "×", latex: "\\times"},
  {label: "÷", latex: "\\div"},
  {label: "±", latex: "\\pm"},
  {label: "≤", latex: "\\le"},
  {label: "≥", latex: "\\ge"},
  {label: "≠", latex: "\\ne"},
  {label: "→", latex: "\\to"},
  {label: "⇒", latex: "\\Rightarrow"}
];

export function FormulaEditor({initialLatex, display, onCancel, onConfirm}: FormulaEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const mathfieldRef = useRef<MathfieldElementType | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let field: MathfieldElementType | null = null;
    void import("mathlive").then((mathlive) => {
      if (disposed || !mathlive.MathfieldElement) return;
      mathlive.MathfieldElement.fontsDirectory = null;
      mathlive.MathfieldElement.soundsDirectory = null;
      field = new mathlive.MathfieldElement({
        defaultMode: "math",
        smartFence: true,
        mathVirtualKeyboardPolicy: "manual"
      });
      field.value = initialLatex;
      field.setAttribute("aria-label", display ? "编辑独立公式" : "编辑行内公式");
      field.className = "formula-editor__mathfield";
      field.addEventListener("input", () => setError(""));
      host.replaceChildren(field);
      mathfieldRef.current = field;
      if (window.mathVirtualKeyboard) {
        window.mathVirtualKeyboard.layouts = ["numeric", "symbols", "alphabetic", "greek"];
        window.mathVirtualKeyboard.container = panelRef.current;
      }
      window.setTimeout(() => field?.focus(), 0);
    }).catch((reason: unknown) => {
      if (!disposed) setLoadError(reason instanceof Error ? reason.message : "公式编辑器加载失败");
    });
    return () => {
      disposed = true;
      if (window.mathVirtualKeyboard) {
        window.mathVirtualKeyboard.hide();
        window.mathVirtualKeyboard.container = null;
      }
      mathfieldRef.current = null;
      field?.remove();
    };
  }, [display, initialLatex]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  const insert = (latex: string) => {
    const field = mathfieldRef.current;
    if (!field) return;
    field.focus();
    field.insert(latex, {selectionMode: "placeholder"});
  };

  const confirm = () => {
    const latex = mathfieldRef.current?.value ?? "";
    const result = validateLatex(latex, display);
    if (!result.valid) {
      setError(result.message);
      return;
    }
    onConfirm(latex);
  };

  return createPortal(
    <div className="formula-dialog" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onCancel();
    }}>
      <div className="formula-dialog__panel" role="dialog" aria-modal="true" aria-labelledby="formula-dialog-title" ref={panelRef}>
        <div className="formula-dialog__head">
          <div>
            <strong id="formula-dialog-title">{display ? "编辑独立公式" : "编辑行内公式"}</strong>
            <small>直接输入数字和字母，或点击下方结构与符号</small>
          </div>
          <button type="button" className="icon-button" aria-label="关闭公式编辑" onClick={onCancel}>×</button>
        </div>
        <div ref={hostRef} className="formula-editor__host" />
        {loadError && <div className="alert alert--error">公式编辑器加载失败：{loadError}</div>}
        {error && <div className="formula-editor__error" role="alert">{error}</div>}
        <div className="formula-editor__toolbar" aria-label="常用公式结构">
          {STRUCTURES.map((item) => <button type="button" key={item.label} onClick={() => insert(item.latex)}>{item.label}</button>)}
        </div>
        <div className="formula-editor__symbols" aria-label="常用数学符号">
          {SYMBOLS.map((symbol) => <button type="button" key={symbol.latex} aria-label={`插入 ${symbol.label}`} onClick={() => insert(symbol.latex)}>{symbol.label}</button>)}
        </div>
        <div className="formula-dialog__actions">
          <button type="button" className="button" onClick={onCancel}>取消</button>
          <button type="button" className="button button--primary" disabled={Boolean(loadError)} onClick={confirm}>确认公式</button>
        </div>
      </div>
    </div>,
    document.body
  );
}
