export type Intent =
  | "report_incident"
  | "provide_details"
  | "check_message"
  | "question"
  | "generate_complaint"
  | "evidence_checklist"
  | "safety_tips"
  | "smalltalk";

export type FactSource = "user_provided" | "inferred" | "unknown";

export interface IncidentData {
  incident_type: string | null;
  subtype: string | null;
  description: string;
  date_time: string | null;
  platform: string | null;
  financial_loss: boolean | null;
  amount: number | null;
  currency: string | null;
  phone_numbers: string[];
  emails: string[];
  urls: string[];
  upi_ids: string[];
  account_identifiers: string[];
  evidence_available: string[];
  actions_taken: string[];
  missing_information: string[];
  confidence: number;
  field_sources: Record<string, FactSource>;
}

export interface Classification {
  category: string;
  subtype: string | null;
  confidence: number;
  reasoning: string;
  alternatives: { category: string; confidence: number }[];
}

export interface Citation {
  id: string;
  title: string;
  organization: string;
  url: string;
  category: string;
  document_type: string;
  published_date: string | null;
  source_note: string | null;
  last_verified: string | null;
  freshness: "verified" | "stale" | "unverified";
  score: number;
  excerpt: string;
}

export interface Explanation {
  summary: string;
  why_this_type: string;
  warning_signs: string[];
  simple_explanation: string;
  source_ids: string[];
}

export interface EvidenceItem {
  item: string;
  why: string;
  priority: "high" | "medium" | "low";
  already_available: boolean;
}

export interface Guidance {
  immediate_actions: string[];
  security_steps: string[];
  reporting_guidance: string[];
  evidence_checklist: EvidenceItem[];
  do_not: string[];
  source_ids: string[];
  evidence_status: "supported" | "limited" | "insufficient";
  verification_notes: string[];
}

export interface ComplaintDraft {
  id: string | null;
  incident_id: string;
  subject: string;
  body: string;
  placeholders: string[];
}

export interface Awareness {
  headline: string;
  warning_signs: string[];
  prevention_tips: string[];
  future_precautions: string[];
  resources: { title: string; organization: string; url: string }[];
}

export interface AssistantResult {
  intent: Intent;
  reply: string;
  incident_id: string | null;
  incident: IncidentData | null;
  classification: Classification | null;
  explanation: Explanation | null;
  guidance: Guidance | null;
  sources: Citation[];
  follow_up_questions: string[];
  complaint: ComplaintDraft | null;
  awareness: Awareness | null;
  stages: string[];
  provider: string;
  warnings: string[];
  red_flags?: RedFlagReport | null;
  language?: Language;
}

export type Language = "en" | "hi";

export interface EvidenceFile {
  name: string;
  size: number;
  media_type: string;
  sha256: string;
  recorded_at: string;
  last_modified: string | null;
  checklist_item: string | null;
}

export interface EvidenceManifest {
  algorithm: string;
  files: EvidenceFile[];
  generated_at: string;
  manifest_sha256: string;
  note: string;
}

export interface VerifyResult {
  manifest_intact: boolean;
  match: boolean;
  matched_file: EvidenceFile | null;
  message: string;
}

export interface RedFlagSpan {
  start: number;
  end: number;
  text: string;
  category: string;
  label: string;
  explanation: string;
  explanation_source: "rule" | "llm";
}

export type Severity = "info" | "low" | "medium" | "high";

export interface UrlIndicator {
  code: string;
  severity: Severity;
  message: string;
}

export interface UrlAnalysis {
  input: string;
  normalized_url: string | null;
  domain: string | null;
  registered_domain: string | null;
  indicators: UrlIndicator[];
  risk_level: "low" | "medium" | "high" | "invalid";
  score: number;
  explanation: string;
  method: string;
}

export interface RedFlagReport {
  text: string;
  spans: RedFlagSpan[];
  categories: Record<string, number>;
  risk_level: "none_found" | "low" | "medium" | "high";
  summary: string;
  url_checks: UrlAnalysis[];
  method: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: AssistantResult;
  error?: boolean;
}

export interface Meta {
  disclaimer: string;
  categories: { id: string; label: string }[];
  llm_provider: string;
  embedding_provider: string;
  languages?: { id: Language; label: string }[];
}

export interface ComplainantDetails {
  name?: string;
  contact?: string;
  address?: string;
}
