import { ExternalLink, Globe2 } from "lucide-react";
import type { SearchSource } from "@shared/contracts";

export function SourceEvidence({ sources }: { sources: SearchSource[] }) {
  if (sources.length === 0) {
    return (
      <div className="source-empty">
        <Globe2 size={15} />
        <span>没有可归因的联网来源</span>
      </div>
    );
  }
  return (
    <div className="source-evidence-list">
      {sources.slice(0, 5).map((source) => (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          className="source-evidence"
          key={source.id}
        >
          <span className="source-rank">{source.rank + 1}</span>
          <span>
            <strong>{source.title}</strong>
            {source.snippet ? <small>{source.snippet}</small> : null}
            <em>{new URL(source.url).hostname}</em>
          </span>
          <ExternalLink size={14} />
        </a>
      ))}
    </div>
  );
}
