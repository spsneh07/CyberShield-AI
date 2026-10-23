import type {
  AssistantResult, ComplainantDetails, EvidenceFile, EvidenceManifest, Intent, Language, Meta, RedFlagReport, UrlAnalysis,
  VerifyResult,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

export const getMeta = () => request<Meta>("/api/v1/meta");

export const getHealth = () =>
  request<{ status: string; llm_provider: string; embedding_provider: string; knowledge_chunks: number }>("/health");

export const createConversation = () =>
  request<{ id: string }>("/api/v1/conversations", { method: "POST", body: JSON.stringify({}) });

/** Permanently deletes the conversation and its incident record on the server (204, no body). */
export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new ApiError(`Delete failed (${res.status})`, res.status);
}

export const checkMessage = (text: string, llmExplanations = false, language: Language = "en") =>
  request<RedFlagReport>("/api/v1/check/message", {
    method: "POST", body: JSON.stringify({ text, llm_explanations: llmExplanations, language }),
  });

export const checkUrl = (url: string) =>
  request<UrlAnalysis>("/api/v1/check/url", { method: "POST", body: JSON.stringify({ url }) });

export const createManifest = (files: EvidenceFile[]) =>
  request<EvidenceManifest>("/api/v1/evidence/manifest", { method: "POST", body: JSON.stringify({ files }) });

export const verifyEvidence = (manifest: EvidenceManifest, sha256: string, name?: string) =>
  request<VerifyResult>("/api/v1/evidence/verify", { method: "POST", body: JSON.stringify({ manifest, sha256, name }) });

export interface SendOptions {
  action?: Intent;
  language?: Language;
  evidenceFiles?: EvidenceFile[];
  complainant?: ComplainantDetails;
  onStage?: (stage: string) => void;
  signal?: AbortSignal;
}

/** Send a message and receive real pipeline stage events over Server-Sent Events. */
export async function sendMessageStream(
  conversationId: string,
  content: string,
  { action, complainant, language, evidenceFiles, onStage, signal }: SendOptions = {},
): Promise<AssistantResult> {
  const res = await fetch(`${API_URL}/api/v1/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, action, complainant, language, evidence_files: evidenceFiles }),
    signal,
  });
  if (!res.ok || !res.body) {
    let message = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      /* ignore */
    }
    throw new ApiError(message, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!event || data === undefined) continue;
      const parsed: unknown = JSON.parse(data);
      if (event === "stage") onStage?.((parsed as { stage: string }).stage);
      else if (event === "result") return parsed as AssistantResult;
      else if (event === "error") throw new ApiError((parsed as { message: string }).message, 500);
    }
  }
  throw new ApiError("The connection closed before a result was received.", 500);
}
