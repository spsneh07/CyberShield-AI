"use client";

import { useRef, useState } from "react";
import { createManifest, verifyEvidence } from "@/lib/api";
import { downloadJson, fingerprint, formatBytes, sha256File } from "@/lib/evidence";
import type { EvidenceFile, EvidenceItem, EvidenceManifest, VerifyResult } from "@/lib/types";

/**
 * Evidence integrity kit: fingerprints files on this device (SHA-256), builds a manifest and
 * verifies files against it later. Only names, sizes and fingerprints leave the browser.
 */
export function EvidenceKit({ files, onChange, checklist }: {
  files: EvidenceFile[];
  onChange: (files: EvidenceFile[]) => void;
  checklist: EvidenceItem[];
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manifest, setManifest] = useState<EvidenceManifest | null>(null);
  const [verifyManifest, setVerifyManifest] = useState<EvidenceManifest | null>(null);
  const [result, setResult] = useState<(VerifyResult & { file: string }) | null>(null);
  const addRef = useRef<HTMLInputElement>(null);

  const add = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setError(null);
    try {
      const next = [...files];
      for (const f of Array.from(list)) {
        const entry = await fingerprint(f);
        if (!next.some((e) => e.sha256 === entry.sha256)) next.push(entry);
      }
      onChange(next);
      setManifest(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not fingerprint the file.");
    } finally {
      setBusy(false);
      if (addRef.current) addRef.current.value = "";
    }
  };

  const update = (i: number, patch: Partial<EvidenceFile>) => {
    onChange(files.map((f, j) => (j === i ? { ...f, ...patch } : f)));
    setManifest(null);
  };

  const makeManifest = async () => {
    setBusy(true);
    setError(null);
    try {
      const m = await createManifest(files);
      setManifest(m);
      setVerifyManifest(m);
      downloadJson(`evidence-manifest-${m.generated_at.slice(0, 10)}.json`, m);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the manifest.");
    } finally {
      setBusy(false);
    }
  };

  const loadManifest = async (f: File | undefined) => {
    if (!f) return;
    try {
      setVerifyManifest(JSON.parse(await f.text()) as EvidenceManifest);
      setResult(null);
      setError(null);
    } catch {
      setError("That file is not a valid evidence manifest (JSON).");
    }
  };

  const check = async (f: File | undefined) => {
    if (!f || !verifyManifest) return;
    setBusy(true);
    setError(null);
    try {
      const r = await verifyEvidence(verifyManifest, await sha256File(f), f.name);
      setResult({ ...r, file: f.name });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verification failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel p-4" aria-labelledby="kit-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="kit-title" className="panel-title">Evidence integrity kit</h2>
        <span className="font-mono text-xs text-slate-400">{files.length} file(s) fingerprinted</span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Add screenshots, statements or recordings. Each file gets a SHA-256 fingerprint computed <b>on this device</b> —
        the file itself is not uploaded. Later you can check that a file is unchanged. A fingerprint shows a file
        hasn&apos;t changed since it was recorded; it doesn&apos;t prove what it shows is genuine.
      </p>

      <label className="btn-ghost mt-3 inline-flex cursor-pointer text-xs">
        {busy ? "Working…" : "＋ Add evidence files"}
        <input ref={addRef} type="file" multiple className="sr-only" disabled={busy} onChange={(e) => void add(e.target.files)} />
      </label>

      {files.length > 0 && (
        <ul className="mt-3 space-y-2">
          {files.map((f, i) => (
            <li key={f.sha256} className="rounded-lg border border-ink-700 bg-ink-800/60 p-2.5 text-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="break-all text-white">{f.name}</span>
                <span className="font-mono text-[11px] text-slate-400">{formatBytes(f.size)}</span>
              </div>
              <p className="mt-1 break-all font-mono text-[11px] text-signal" title="SHA-256">{f.sha256}</p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                Recorded {new Date(f.recorded_at).toLocaleString()}
                {f.last_modified && <> · file date on device {new Date(f.last_modified).toLocaleString()}</>}
              </p>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <label className="flex min-w-[200px] flex-1 flex-wrap items-center gap-1.5 text-xs text-slate-400">
                  <span>Evidence of</span>
                  <select
                    value={f.checklist_item ?? ""}
                    onChange={(e) => update(i, { checklist_item: e.target.value || null })}
                    title={f.checklist_item ?? "Link this file to an item from the evidence checklist"}
                    className="w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1 text-xs text-white focus:border-signal focus:outline-none sm:w-auto sm:max-w-md md:max-w-lg"
                  >
                    <option value="">— not linked —</option>
                    {checklist.map((c) => (
                      <option key={c.item} value={c.item} title={c.item}>
                        {c.item}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="shrink-0 text-xs text-slate-400 underline hover:text-danger"
                  onClick={() => {
                    onChange(files.filter((_, j) => j !== i));
                    setManifest(null);
                  }}
                >
                  remove from list
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {files.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary text-xs" disabled={busy} onClick={() => void makeManifest()}>
            Create &amp; download manifest
          </button>
          <span className="text-xs text-slate-400">Generated complaints include these files as Annexure A.</span>
        </div>
      )}
      {manifest && (
        <p className="mt-2 break-all font-mono text-[11px] text-slate-400">
          Manifest fingerprint: <span className="text-white">{manifest.manifest_sha256}</span>
        </p>
      )}

      <details className="mt-4 rounded-lg border border-ink-700 p-3">
        <summary className="cursor-pointer text-sm text-white">Verify a file against a manifest</summary>
        <div className="mt-2 space-y-2 text-xs text-slate-400">
          <label className="block">Manifest {verifyManifest ? <span className="text-safe">(loaded: {verifyManifest.files?.length ?? 0} files)</span> : "(JSON you downloaded earlier)"}
            <input type="file" accept="application/json,.json" className="mt-1 block text-xs" onChange={(e) => void loadManifest(e.target.files?.[0])} />
          </label>
          <label className="block">File to check
            <input type="file" disabled={!verifyManifest || busy} className="mt-1 block text-xs" onChange={(e) => void check(e.target.files?.[0])} />
          </label>
          {result && (
            <p className={`rounded-md p-2 text-sm ${result.match ? "bg-safe/10 text-safe" : "bg-danger/10 text-danger"}`}>
              {result.file}: {result.message}
            </p>
          )}
        </div>
      </details>

      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </section>
  );
}
