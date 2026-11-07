import { useState } from "react";
import { Check, ChevronDown, Copy, FileText, Paperclip } from "lucide-react";

/** Keep the original path intact for copying, but lead with the useful filename. */
export function ArtifactList({ paths }: { paths: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const visible = expanded ? paths : paths.slice(0, 4);
  return <section className="artifact-list" aria-label="产出物">
    <div className="artifact-heading"><Paperclip size={15}/><strong>产出物</strong><span>{paths.length} 个文件</span></div>
    <div aria-label="产出物路径" className="artifact-files">{visible.map((path, i) => {
      const parts = path.split(/[\\/]/);
      const name = parts.pop() || path;
      return <div className="artifact-file" key={`${i}-${path}`}>
        <FileText size={18} aria-hidden="true"/>
        <details className="artifact-path" title={path}>
          <summary><span>{name}</span><small>{parts.slice(-2).join(" / ") || "文件"}</small></summary>
          <div className="artifact-full-path">{path}</div>
        </details>
        <button type="button" aria-label={`复制路径：${name}`} title="复制完整路径" onClick={async () => {
          try { await navigator.clipboard.writeText(path); setCopied(path); setError(false); }
          catch { setError(true); }
        }}>{copied === path ? <Check size={16}/> : <Copy size={16}/>}</button>
      </div>;
    })}</div>
    {paths.length > 4 && <button className="artifact-expand" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}><ChevronDown size={14} style={{transform:expanded ? "rotate(180deg)" : undefined}}/>{expanded ? "收起文件列表" : `查看全部 ${paths.length} 个文件`}</button>}
    <span className="artifact-feedback" role="status">{error ? "复制失败，请展开文件名后手动复制路径" : copied ? "已复制完整路径" : "点击文件名查看完整路径"}</span>
  </section>;
}
