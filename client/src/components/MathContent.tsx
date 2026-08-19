import { useMemo } from "react";
import { parseMathContent, renderLatex } from "@/lib/math-content";

export function MathContent({value, className = ""}: {value: string; className?: string}) {
  const segments = useMemo(() => parseMathContent(value), [value]);
  return (
    <div className={`math-content ${className}`.trim()}>
      {segments.map((segment, index) => {
        if (segment.type === "text") return <span key={index}>{segment.text}</span>;
        if (segment.type === "invalid") {
          return (
            <span key={index} className="math-content__invalid" title={segment.message}>
              <span>{segment.raw}</span>
              <small>公式需检查</small>
            </span>
          );
        }
        const rendered = renderLatex(segment.latex, segment.display);
        if (!rendered.valid) {
          return (
            <span key={index} className="math-content__invalid" title={rendered.message}>
              <span>{segment.latex}</span>
              <small>公式需检查</small>
            </span>
          );
        }
        const Tag = segment.display ? "div" : "span";
        return (
          <Tag
            key={index}
            className={segment.display ? "math-content__display" : "math-content__inline"}
            dangerouslySetInnerHTML={{__html: rendered.html}}
          />
        );
      })}
    </div>
  );
}
