"use client";

import { useEffect, useMemo, useState } from "react";
import type { ComplaintDraft } from "@/lib/types";

export function ComplaintEditor({ draft }: { draft: ComplaintDraft }) {
  const [body, setBody] = useState(draft.body);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    setBody(draft.body);
  }, [draft.body]);

  const remaining = useMemo(() => Array.from(new Set(body.match(/\[[A-Z][A-Z0-9 /'’&,.-]{1,80}\]/g) ?? [])), [body]);

  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(body);
      setCopyStatus("copied");
      setTimeout(() => setCopyStatus("idle"), 1500);
    } catch {
      const textarea = document.getElementById("complaint-body") as HTMLTextAreaElement | null;
      if (textarea) {
        textarea.focus();
        textarea.select();
      }
      setCopyStatus("failed");
      setTimeout(() => setCopyStatus("idle"), 3000);
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "cyber-complaint-draft.txt" });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="panel p-4" aria-labelledby="cmp-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="cmp-title" className="panel-title">Complaint draft</h2>
        <div className="flex gap-2">
          <button
            type="button"
            className={`btn-ghost px-2.5 py-1.5 text-xs ${
              copyStatus === "copied" ? "text-safe" : copyStatus === "failed" ? "text-warn" : ""
            }`}
            onClick={() => void copy()}
          >
            {copyStatus === "copied"
              ? "Copied ✓"
              : copyStatus === "failed"
                ? "Copy failed — text selected, press Ctrl+C"
                : "Copy"}
          </button>
          <button type="button" className="btn-ghost px-2.5 py-1.5 text-xs" onClick={download}>Download .txt</button>
        </div>
      </div>
      <p className="mt-2 text-sm text-slate-300">{draft.subject}</p>
      {remaining.length > 0 ? (
        <p className="mt-2 text-xs text-warn">
          {remaining.length} placeholder{remaining.length > 1 ? "s" : ""} to fill: {remaining.slice(0, 8).join(" ")}{remaining.length > 8 ? " …" : ""}
        </p>
      ) : (
        <p className="mt-2 text-xs text-safe">All placeholders filled.</p>
      )}
      <label htmlFor="complaint-body" className="sr-only">Editable complaint text</label>
      <textarea id="complaint-body" value={body} onChange={(e) => setBody(e.target.value)} spellCheck
        className="mt-3 h-96 w-full resize-y rounded-lg border border-ink-600 bg-ink-950 p-3 font-mono text-xs leading-relaxed text-slate-200 focus:border-signal focus:outline-none" />
      <p className="mt-2 text-xs text-slate-500">Review every line before submitting. Submit through official channels only.</p>
    </section>
  );
}
