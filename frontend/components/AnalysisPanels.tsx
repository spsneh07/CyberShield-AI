"use client";

import { useState } from "react";
import { safeHref } from "@/lib/labels";
import type { Awareness, Citation, EvidenceItem, Explanation, Guidance } from "@/lib/types";

function List({ items, tone = "signal", ordered = false }: { items: string[]; tone?: "signal" | "danger" | "safe"; ordered?: boolean }) {
  if (!items.length) return null;
  const marker = { signal: "marker:text-signal", danger: "marker:text-danger", safe: "marker:text-safe" }[tone];
  const L = ordered ? "ol" : "ul";
  return (
    <L className={`${ordered ? "list-decimal" : "list-disc"} space-y-1.5 pl-5 text-sm ${marker}`}>
      {items.map((t, i) => <li key={i}>{t}</li>)}
    </L>
  );
}

function SourceRefs({ ids, sources }: { ids: string[]; sources: Citation[] }) {
  const used = sources.filter((s) => ids.includes(s.id));
  if (!used.length) return null;
  return <p className="mt-3 font-mono text-[11px] text-slate-500">Grounded in: {used.map((s) => `[${s.id}] ${s.organization}`).join(" · ")}</p>;
}

export function ExplanationPanel({ explanation, sources }: { explanation: Explanation; sources: Citation[] }) {
  return (
    <section className="panel p-4" aria-labelledby="exp-title">
      <h2 id="exp-title" className="panel-title">What likely happened</h2>
      <p className="mt-3 text-sm text-slate-200">{explanation.simple_explanation}</p>
      {explanation.warning_signs.length > 0 && (
        <>
          <h3 className="mt-4 text-sm font-semibold text-danger">Warning signs in your case</h3>
          <div className="mt-1.5"><List items={explanation.warning_signs} tone="danger" /></div>
        </>
      )}
      <SourceRefs ids={explanation.source_ids} sources={sources} />
    </section>
  );
}

export function GuidancePanel({ guidance, sources }: { guidance: Guidance; sources: Citation[] }) {
  return (
    <section className="panel p-4" aria-labelledby="guide-title">
      <h2 id="guide-title" className="panel-title">What to do now</h2>
      <div className="mt-3 space-y-4">
        <div>
          <h3 className="mb-1.5 text-sm font-semibold text-white">Immediate actions</h3>
          <List items={guidance.immediate_actions} ordered />
        </div>
        {guidance.security_steps.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-sm font-semibold text-white">Secure your accounts</h3>
            <List items={guidance.security_steps} tone="safe" />
          </div>
        )}
        {guidance.reporting_guidance.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-sm font-semibold text-white">Reporting</h3>
            <List items={guidance.reporting_guidance} />
          </div>
        )}
        {guidance.do_not.length > 0 && (
          <div className="rounded-lg border border-danger/30 bg-danger/5 p-3">
            <h3 className="mb-1.5 text-sm font-semibold text-danger">Do not</h3>
            <List items={guidance.do_not} tone="danger" />
          </div>
        )}
      </div>
      <SourceRefs ids={guidance.source_ids} sources={sources} />
      {guidance.verification_notes.length > 0 && (
        <div className="mt-3 rounded-lg border border-warn/30 bg-warn/5 p-3" role="note">
          <h3 className="mb-1 text-xs font-semibold text-warn">
            {guidance.evidence_status === "insufficient" ? "No trusted source found" : "Check before relying on this"}
          </h3>
          <List items={guidance.verification_notes} />
        </div>
      )}
    </section>
  );
}

const PRIORITY_CLS: Record<EvidenceItem["priority"], string> = {
  high: "border-danger/40 text-danger",
  medium: "border-warn/40 text-warn",
  low: "border-ink-600 text-slate-400",
};

export function EvidenceChecklist({ items }: { items: EvidenceItem[] }) {
  const [checked, setChecked] = useState<Record<number, boolean>>(() =>
    Object.fromEntries(items.map((it, i) => [i, it.already_available])),
  );
  const done = Object.values(checked).filter(Boolean).length;
  return (
    <section className="panel p-4" aria-labelledby="ev-title">
      <div className="flex items-center justify-between">
        <h2 id="ev-title" className="panel-title">Evidence checklist</h2>
        <span className="font-mono text-xs text-slate-400">{done}/{items.length} secured</span>
      </div>
      <ul className="mt-3 space-y-2">
        {items.map((it, i) => (
          <li key={i}>
            <label className="flex cursor-pointer gap-3 rounded-lg border border-ink-700 bg-ink-800/60 p-2.5 hover:border-ink-600">
              <input type="checkbox" className="mt-1 h-4 w-4 accent-[#0b5cad]" checked={!!checked[i]}
                onChange={(e) => setChecked((c) => ({ ...c, [i]: e.target.checked }))} />
              <span className="flex-1">
                <span className={`text-sm ${checked[i] ? "text-slate-400 line-through" : "text-white"}`}>{it.item}</span>
                {it.why && <span className="block text-xs text-slate-400">{it.why}</span>}
              </span>
              <span className={`chip h-fit border ${PRIORITY_CLS[it.priority]}`}>{it.priority}</span>
            </label>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-slate-500">Keep originals. Never edit, crop misleadingly or delete evidence.</p>
    </section>
  );
}

export function SourcesPanel({ sources }: { sources: Citation[] }) {
  if (!sources.length) return null;
  return (
    <section className="panel p-4" aria-labelledby="src-title">
      <h2 id="src-title" className="panel-title">Trusted sources retrieved</h2>
      <ul className="mt-3 space-y-2">
        {sources.map((s) => (
          <li key={s.id} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
            <details>
              <summary className="cursor-pointer list-none">
                <span className="font-mono text-xs text-signal">[{s.id}]</span>{" "}
                <span className="text-sm font-medium text-white">{s.title}</span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  {s.organization}
                  <span className="chip">{s.document_type.replace("_", " ")}</span>
                  <span className="chip">relevance {s.score.toFixed(2)}</span>
                  <span className={`chip ${s.freshness === "verified" ? "text-safe" : "text-warn"}`}>
                    {s.freshness === "unverified" ? "not verified" : `${s.freshness} ${s.last_verified ?? ""}`}
                  </span>
                </span>
              </summary>
              <p className="mt-2 whitespace-pre-line text-xs text-slate-300">{s.excerpt}</p>
            </details>
            {s.source_note && <p className="mt-1 text-[11px] italic text-warn/90">{s.source_note}</p>}
            {safeHref(s.url) && (
              <a href={safeHref(s.url)} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block break-all text-xs text-signal underline-offset-2 hover:underline">
                {s.url}
              </a>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-slate-500">
        Sources are retrieved from this project&apos;s knowledge base. Verify details at the official link before acting.
      </p>
    </section>
  );
}

export function AwarenessPanel({ awareness }: { awareness: Awareness }) {
  return (
    <section className="panel border-safe/30 p-4" aria-labelledby="aw-title">
      <h2 id="aw-title" className="panel-title !text-safe">Stay safe next time</h2>
      <p className="mt-2 text-base font-semibold text-white">{awareness.headline}</p>
      <div className="mt-3 grid gap-4">
        {awareness.warning_signs.length > 0 && (<div><h3 className="mb-1 text-sm font-semibold text-white">Spot the signs</h3><List items={awareness.warning_signs} tone="danger" /></div>)}
        {awareness.prevention_tips.length > 0 && (<div><h3 className="mb-1 text-sm font-semibold text-white">Prevention</h3><List items={awareness.prevention_tips} tone="safe" /></div>)}
        {awareness.future_precautions.length > 0 && (<div><h3 className="mb-1 text-sm font-semibold text-white">Longer-term precautions</h3><List items={awareness.future_precautions} tone="safe" /></div>)}
        {awareness.resources.length > 0 && (
          <div>
            <h3 className="mb-1 text-sm font-semibold text-white">Trusted resources</h3>
            <ul className="space-y-1 text-sm">
              {awareness.resources.filter((r) => safeHref(r.url)).map((r) => (
                <li key={r.url}><a className="text-signal hover:underline" href={r.url} target="_blank" rel="noopener noreferrer">{r.title}</a> <span className="text-xs text-slate-500">{r.organization}</span></li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
