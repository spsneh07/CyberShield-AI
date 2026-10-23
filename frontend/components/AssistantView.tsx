"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AwarenessPanel, EvidenceChecklist, ExplanationPanel, GuidancePanel, SourcesPanel,
} from "@/components/AnalysisPanels";
import { ComplaintEditor } from "@/components/ComplaintEditor";
import { EvidenceKit } from "@/components/EvidenceKit";
import { IncidentSummary } from "@/components/IncidentSummary";
import { LanguageSelect, useLanguage } from "@/components/LanguageSelect";
import { Markdown } from "@/components/Markdown";
import { QUICK_ACTIONS, QuickActionGrid, type QuickAction } from "@/components/QuickActions";
import { RedFlagSummary } from "@/components/ScamCheck";
import { StageProgress } from "@/components/StageProgress";
import { createConversation, deleteConversation, sendMessageStream } from "@/lib/api";
import { categoryLabel } from "@/lib/labels";
import type { AssistantResult, ChatMessage, ComplainantDetails, EvidenceFile, Intent } from "@/lib/types";

type Tab = "analysis" | "evidence" | "sources" | "complaint" | "awareness";

/** Latest non-empty value of each result section across the conversation. */
type Insights = Partial<Pick<AssistantResult,
  "incident" | "classification" | "explanation" | "guidance" | "complaint" | "awareness" | "red_flags">> & { sources: AssistantResult["sources"] };

const DISCLAIMER =
  "This assistant provides educational cybersecurity guidance and complaint-drafting assistance. It is not a substitute for law enforcement, legal advice, or professional cybersecurity investigation.";

let idCounter = 0;
const nextId = () => `m${++idCounter}`;

export function AssistantView() {
  const params = useSearchParams();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pendingAction, setPendingAction] = useState<Intent | undefined>();
  const [busy, setBusy] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [insights, setInsights] = useState<Insights>({ sources: [] });
  const [tab, setTab] = useState<Tab>("analysis");
  const [complainant, setComplainant] = useState<ComplainantDetails>({});
  const [language, setLanguage] = useLanguage();
  const [evidenceFiles, setEvidenceFiles] = useState<EvidenceFile[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, stages]);

  const ensureConversation = useCallback(async () => {
    if (conversationId) return conversationId;
    const { id } = await createConversation();
    setConversationId(id);
    return id;
  }, [conversationId]);

  const eraseConversation = async () => {
    if (!conversationId) return;
    if (!window.confirm("Delete this conversation, its incident record and drafts from the server? Download anything you need first.")) return;
    try {
      await deleteConversation(conversationId);
      setConversationId(null);
      setMessages([]);
      setInsights({ sources: [] });
      setStages([]);
      setEvidenceFiles([]);
    } catch {
      window.alert("Could not delete the conversation. Please try again.");
    }
  };

  const absorb = (r: AssistantResult) => {
    setInsights((prev) => ({
      incident: r.incident ?? prev.incident,
      classification: r.classification ?? prev.classification,
      explanation: r.explanation ?? prev.explanation,
      guidance: r.guidance ?? prev.guidance,
      complaint: r.complaint ?? prev.complaint,
      awareness: r.awareness ?? prev.awareness,
      red_flags: r.red_flags ?? prev.red_flags,
      sources: r.sources.length ? r.sources : prev.sources,
    }));
    if (r.complaint) setTab("complaint");
    else if (r.awareness) setTab("awareness");
    else if (r.intent === "evidence_checklist") setTab("evidence");
    else if (r.classification) setTab("analysis");
    else if (r.sources.length) setTab("sources");
  };

  const send = async (text: string, action?: Intent) => {
    const content = text.trim();
    if (!content || busy) return;
    setMessages((m) => [...m, { id: nextId(), role: "user", content }]);
    setInput("");
    setPendingAction(undefined);
    setBusy(true);
    setStages([]);
    try {
      const cid = await ensureConversation();
      const hasDetails = Object.values(complainant).some((v) => v?.trim());
      const result = await sendMessageStream(cid, content, {
        action,
        language,
        evidenceFiles: action === "generate_complaint" && evidenceFiles.length ? evidenceFiles : undefined,
        complainant: action === "generate_complaint" && hasDetails ? complainant : undefined,
        onStage: (s) => setStages((prev) => [...prev, s]),
      });
      setMessages((m) => [...m, { id: nextId(), role: "assistant", content: result.reply, result }]);
      absorb(result);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong.";
      setMessages((m) => [...m, { id: nextId(), role: "assistant", content: `⚠ ${msg} Please check the backend is running and try again.`, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const pick = (a: QuickAction) => {
    if (a.autoSend) {
      void send(a.autoSend, a.action);
      return;
    }
    setPendingAction(a.action);
    setInput(a.prefill ?? "");
    inputRef.current?.focus();
  };

  // Handle ?action= from the dashboard once.
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const a = QUICK_ACTIONS.find((q) => q.id === params.get("action"));
    if (a) pick(a);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tabs: { id: Tab; label: string; ready: boolean }[] = [
    { id: "analysis", label: "Analysis", ready: !!insights.incident },
    { id: "evidence", label: `Evidence${evidenceFiles.length ? ` (${evidenceFiles.length} files)` : ""}`, ready: !!insights.guidance },
    { id: "sources", label: `Sources${insights.sources.length ? ` (${insights.sources.length})` : ""}`, ready: insights.sources.length > 0 },
    { id: "complaint", label: "Complaint", ready: !!insights.complaint },
    { id: "awareness", label: "Awareness", ready: !!insights.awareness },
  ];
  const lastResult = [...messages].reverse().find((m) => m.result)?.result;

  return (
    <div className="mx-auto grid max-w-7xl gap-4 px-4 py-4 lg:h-[calc(100vh-110px)] lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      {/* Chat column */}
      <section className="flex min-h-[70vh] flex-col lg:min-h-0" aria-label="Conversation">
        <div className="mb-3"><QuickActionGrid compact onPick={pick} disabled={busy} /></div>

        <div className="panel flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
          {messages.length === 0 && (
            <div className="py-10 text-center text-slate-400">
              <p className="text-lg text-white">Tell me what happened.</p>
              <p className="mx-auto mt-2 max-w-md text-sm">
                For example: “I received a WhatsApp message claiming to be from my bank. It asked me to click a link and enter
                my OTP because my account would otherwise be blocked.”
              </p>
              <p className="mx-auto mt-4 max-w-md text-xs text-slate-500">Avoid sharing passwords, full card numbers or OTPs here.</p>
            </div>
          )}
          {messages.map((m) => (
            <article key={m.id} className={m.role === "user" ? "ml-auto max-w-[85%]" : "mr-auto max-w-[92%]"}>
              <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{m.role === "user" ? "You" : "Assistant"}</p>
              <div className={
                m.role === "user"
                  ? "rounded-xl rounded-tr-sm bg-signal/15 px-3.5 py-2.5 text-sm text-white ring-1 ring-signal/30"
                  : m.error
                    ? "rounded-xl rounded-tl-sm bg-danger/10 px-3.5 py-2.5 text-sm text-danger ring-1 ring-danger/30"
                    : "rounded-xl rounded-tl-sm bg-ink-800 px-3.5 py-2.5 text-sm ring-1 ring-ink-600"
              }>
                {m.role === "assistant" ? <Markdown text={m.content} /> : <p className="whitespace-pre-wrap">{m.content}</p>}
                {m.result?.classification && (
                  <p className="mt-2 flex flex-wrap gap-1.5">
                    <span className="chip text-signal">{categoryLabel(m.result.classification.category)}</span>
                    {m.result.sources.length > 0 && <span className="chip">{m.result.sources.length} sources</span>}
                  </p>
                )}
                {m.result?.warnings.map((w) => <p key={w} className="mt-2 text-xs text-warn">{w}</p>)}
              </div>
            </article>
          ))}
          {(busy || stages.length > 0) && busy && <StageProgress reached={stages} active={busy} />}
          <div ref={endRef} />
        </div>

        <form className="mt-3" onSubmit={(e) => { e.preventDefault(); void send(input, pendingAction); }}>
          {pendingAction && (
            <p className="mb-1 font-mono text-xs text-signal">
              Mode: {QUICK_ACTIONS.find((q) => q.action === pendingAction)?.title}
              <button type="button" className="ml-2 text-slate-400 underline" onClick={() => setPendingAction(undefined)}>clear</button>
            </p>
          )}
          <div className="panel flex items-end gap-2 p-2">
            <label htmlFor="composer" className="sr-only">Message</label>
            <textarea
              id="composer" ref={inputRef} rows={2} value={input} maxLength={6000}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input, pendingAction); } }}
              placeholder="Describe the incident, paste a suspicious message, or ask a question…"
              className="max-h-40 min-h-[44px] flex-1 resize-y bg-transparent px-2 py-1.5 text-sm text-white placeholder:text-slate-500 focus:outline-none"
            />
            <button type="submit" className="btn-primary" disabled={busy || !input.trim()}>{busy ? "Working…" : "Send"}</button>
          </div>
          <div className="mt-2 flex flex-wrap items-start justify-between gap-2">
            <p className="max-w-xl text-[11px] text-slate-500">{DISCLAIMER}</p>
            <div className="flex items-center gap-3">
              {conversationId && (
                <button type="button" className="text-xs text-slate-400 underline hover:text-danger" disabled={busy}
                  onClick={() => void eraseConversation()}>
                  Delete this conversation
                </button>
              )}
              <LanguageSelect value={language} onChange={setLanguage} />
            </div>
          </div>
        </form>
      </section>

      {/* Insights column */}
      <section className="flex min-h-0 flex-col" aria-label="Analysis results">
        <div role="tablist" aria-label="Result sections" className="mb-3 flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              className={`btn px-3 py-1.5 text-xs ${tab === t.id ? "bg-signal text-ink-950" : "border border-ink-600 bg-ink-800 text-slate-300 hover:text-white"}`}>
              {t.label}{t.ready && tab !== t.id && <span aria-hidden className="ml-1 h-1.5 w-1.5 rounded-full bg-safe" />}
            </button>
          ))}
          {lastResult?.provider === "mock" && <span className="chip ml-auto text-warn" title="No LLM API key configured">offline mock AI</span>}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto pb-6 lg:pr-1" role="tabpanel">
          {tab === "analysis" && (insights.incident ? (
            <>
              <IncidentSummary incident={insights.incident} classification={insights.classification ?? null} />
              {insights.red_flags && insights.red_flags.spans.length > 0 && <RedFlagSummary report={insights.red_flags} />}
              {insights.explanation && <ExplanationPanel explanation={insights.explanation} sources={insights.sources} />}
              {insights.guidance && <GuidancePanel guidance={insights.guidance} sources={insights.sources} />}
            </>
          ) : <Empty text="Describe an incident to see its structured summary, classification and recommended actions." />)}

          {tab === "evidence" && (
            <>
              {insights.guidance
                ? <EvidenceChecklist key={insights.incident?.description} items={insights.guidance.evidence_checklist} />
                : <Empty text="The evidence checklist appears after an incident is analysed." />}
              <EvidenceKit files={evidenceFiles} onChange={setEvidenceFiles} checklist={insights.guidance?.evidence_checklist ?? []} />
            </>
          )}

          {tab === "sources" && (insights.sources.length
            ? <SourcesPanel sources={insights.sources} />
            : <Empty text="Retrieved trusted sources will be listed here." />)}

          {tab === "complaint" && (
            <>
              <details className="panel p-4">
                <summary className="cursor-pointer text-sm text-white">Your details for the complaint (optional)</summary>
                <p className="mt-1 text-xs text-slate-400">Only used to fill the draft. Leave blank to keep placeholders.</p>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  {(["name", "contact", "address"] as const).map((f) => (
                    <label key={f} className="text-xs capitalize text-slate-400">{f}
                      <input value={complainant[f] ?? ""} onChange={(e) => setComplainant((c) => ({ ...c, [f]: e.target.value }))}
                        className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm text-white focus:border-signal focus:outline-none" />
                    </label>
                  ))}
                </div>
                <button type="button" className="btn-primary mt-3 text-xs" disabled={busy || !insights.incident}
                  onClick={() => void send("Please draft a formal complaint for my incident.", "generate_complaint")}>
                  {insights.complaint ? "Regenerate draft" : "Generate draft"}
                </button>
                <p className="mt-2 text-xs text-slate-500">
                  {evidenceFiles.length
                    ? `${evidenceFiles.length} fingerprinted evidence file(s) will be listed as Annexure A.`
                    : "Tip: add evidence files in the Evidence tab to list them, with fingerprints, as Annexure A."}
                </p>
              </details>
              {insights.complaint ? <ComplaintEditor draft={insights.complaint} />
                : <Empty text={insights.incident ? "Generate a draft above." : "Report an incident first, then generate a complaint draft."} />}
            </>
          )}

          {tab === "awareness" && (insights.awareness
            ? <AwarenessPanel awareness={insights.awareness} />
            : <Empty text="Ask for “Cyber Safety Tips” to get prevention advice personalised to your incident." />)}
        </div>
      </section>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="panel grid min-h-[200px] place-items-center p-6 text-center text-sm text-slate-400">{text}</div>;
}
