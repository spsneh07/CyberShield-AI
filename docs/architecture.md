# Architecture

```
Browser (Next.js / React / Tailwind)
   │  REST + Server-Sent Events (stage progress)
   ▼
FastAPI  /api/v1   ──►  ConversationOrchestrator
                          │
     ┌────────────────────┼──────────────────────────────────────┐
     ▼                    ▼                                      ▼
 IntentDetector   IncidentExtractor ─► Validator     Classifier (taxonomy)
                    (LLM + regex)                          │
                                                           ▼
                                         Retriever (embeddings + pgvector)
                                                           │
                    ┌──────────────┬───────────────┬───────┴─────────┐
                    ▼              ▼               ▼                 ▼
               Explanation     Guidance +     ComplaintGenerator  AwarenessGenerator
                               Evidence list  (template + LLM      (LLM, grounded)
                                               narrative)
                                                           │
                                                           ▼
                                         Repositories (SQLAlchemy) ─► PostgreSQL + pgvector
```

## Backend layout (`backend/app`)

| Package | Responsibility |
|---|---|
| `api/v1` | HTTP routes only: validation, status codes, wiring |
| `core` | settings (env), DB session, logging with redaction, error handlers |
| `models` | SQLAlchemy ORM tables |
| `schemas` | Pydantic request/response + LLM structured-output schemas |
| `repositories` | All DB access |
| `services/ai` | `LLMProvider`, `EmbeddingProvider`, providers (mock, OpenAI-compatible incl. Groq, Anthropic; embeddings: hash, local sentence-transformers, OpenAI-compatible), retry/backoff on 429/5xx, structured-output helper, `prompts/` |
| `services/incident` | extraction, identifier regexes, missing-info detection |
| `services/classification` | taxonomy + classifier |
| `services/rag` | chunking, ingestion, sanitisation, retrieval |
| `services/guidance` | explanation, actions, evidence checklist; `safety.py` output guards (no evidence-destruction advice) |
| `services/complaint` | complaint draft |
| `services/awareness` | awareness content |
| `services/conversation` | intent detection + orchestrator |
| `services/scamcheck` | rule-based red-flag detector (`red_flags.py`) and offline lookalike-URL analyser (`url_analyzer.py`); no AI, no network |
| `services/evidence` | evidence integrity manifest: validation of client-side fingerprints, manifest digest, verification, complaint annexure (no AI; file contents never received) |
| `services/language.py` | selected reply language (context variable) and the instruction appended to user-facing prompts |

## Request flow (chat message)

1. Persist user message.
2. Intent (8 fixed labels): `report_incident | provide_details | check_message | question | generate_complaint | evidence_checklist | safety_tips | smalltalk`.
3. Extraction → merge with previous incident state of the conversation (new facts
   only, never overwrite user facts with inferences) → validate with Pydantic.
4. Classification against fixed taxonomy (confidence, reasoning, alternatives;
   `unknown` allowed).
5. Retrieval: query = category + description; top-k chunks with metadata.
   Chunks are wrapped as quoted data in prompts and scanned for injection patterns.
6. Generation: explanation + guidance + evidence checklist; complaint/awareness on demand.
7. Follow-up questions derived from `missing_information` minus what is known.
8. Persist incident, classification, assistant message; stream stage events.

Novelty hooks (the stages above are unchanged):
- The message's `language` is set at step 1; `generate_structured` appends a language instruction only for
  user-facing tasks (`explanation`, `guidance`, `awareness`, `conversation`, `redflag_explain`). Intent,
  extraction, classification and the complaint stay in English.
- For `report_incident` / `check_message`, the rule-based red-flag detector runs on the raw message and
  its report (with URL analyses of any links) is attached to the result as `red_flags`.
- `/api/v1/check/message` and `/api/v1/check/url` expose the same detectors without the pipeline.
- `generate_complaint` may carry `evidence_files` (fingerprint metadata from the browser); the complaint
  template appends them deterministically as Annexure A after the narrative has been validated, so the
  complaint validator and placeholders are unaffected. `/api/v1/evidence/manifest` and `/verify` build and
  check manifests; the server stores no files.

## Data model

`users, conversations, messages, incidents, classifications, evidence_items,
complaint_drafts, awareness_content, knowledge_documents, knowledge_chunks (vector), feedback`.

## Deployment topology

```
Browser ──► Next.js (3000) ──► FastAPI (8000) ──► PostgreSQL 16 + pgvector (Docker, host port POSTGRES_PORT)
                                   │
                                   ├──► LLM API (OpenAI-compatible e.g. Groq, or Anthropic)   [real mode]
                                   └──► sentence-transformers on CPU (local embeddings)       [real mode]
```

PostgreSQL with pgvector is the application database. SQLite is used only by the automated tests.

## Output guards (deterministic, after every generation)

| Guard | Where |
|---|---|
| Identifiers not literally present in the user's text are dropped; regex-found ones are added | `incident/extractor.py` |
| URLs and helpline-style numbers absent from retrieved sources → `[official … — verify]` | `guidance/advisor.py` |
| Advice to delete/erase/wipe evidence is removed (negated forms kept), in English and Hindi | `guidance/safety.py` |
| LLM rewording of red-flag explanations: only for existing span indexes; text with numbers, links or evidence-deletion advice rejected | `scamcheck/red_flags.py` |
| Citation ids filtered to retrieved ids; awareness links filtered to retrieved URLs; only http(s) links rendered | `guidance`, `awareness`, frontend |
| Complaint narrative identifiers not provided by the user → placeholders | `complaint/generator.py` |
| Query embedding model must match the model used to build the index | `rag/retriever.py` |
| Hosts must match a source host exactly; 3–13 digit numbers must be in a source or the user's report (guidance, explanation, chat reply, awareness) | `guidance/advisor.py` |
| `evidence_status` + `verification_notes` computed from retrieval and source freshness (`last_verified`) | `guidance/advisor.py`, `rag/retriever.py` |
| Urgent bank step inserted first when money or credentials may be at risk | `guidance/advisor.py` |
| Decisive follow-ups (loss conflict, money moved, details entered, engaged); conflicting or uncertain loss stays unknown | `incident/extractor.py` |
| `<`/`>` JSON-escaped inside `<input>` so user text cannot fake a block | `prompts/common.py` |

## Tests

`tests/unit` (no I/O), `tests/integration` (SQLite + Alembic + API), `tests/postgres` (real pgvector,
opt-in via `TEST_DATABASE_URL`), `tests/real_provider` (real LLM/embeddings, opt-in via
`RUN_REAL_PROVIDER_TESTS=1`).
