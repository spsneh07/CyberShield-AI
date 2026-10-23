# AI Cyber Crime Complaint & Awareness Assistant (CyberShield AI)

B.Tech 5th-semester course project — 21CSE306P Applied Generative AI (2026-27).
Team: Kaavya Gupta, Nikshit R, Riya Maheshwari, Sneh Prasad.

> This assistant provides educational cybersecurity guidance and complaint-drafting assistance. It is not a
> substitute for law enforcement, legal advice, or professional cybersecurity investigation.

## Problem
Cybercrime victims often can't tell what kind of scam happened, what to do first, which evidence to keep or
how to write a formal complaint. Guidance is scattered and written for experts.

## Solution
A conversational assistant that runs every message through a real GenAI pipeline:

```
message → intent → extraction (LLM + regex grounding) → validation (Pydantic)
        → classification (21-class taxonomy) → retrieval (pgvector RAG)
        → explanation + guidance + evidence checklist → complaint draft / awareness
        → persistence (PostgreSQL)
```

The UI shows each stage live (Server-Sent Events), a structured incident summary that marks every fact as
*you said / inferred / unknown*, cited trusted sources, an interactive evidence checklist, an editable
complaint draft with `[PLACEHOLDERS]` for missing facts, and personalised awareness content.

## Novelty features

Four additions on top of the pipeline. Three of them are **rule-based, not AI**; the language feature changes
only the language the LLM writes in.

| Feature | How it works | Where |
|---|---|---|
| **Red-flag highlighting** | Pasted SMS / WhatsApp / email / chat text is scanned with fixed regular-expression rules (English, romanised Hindi and some Devanagari phrasings) for 8 categories: OTP/PIN/password requests, urgency or threats, payment requests, links, impersonation claims, personal-information requests, app install / remote access, too-good-to-be-true offers. Each highlight is an exact substring with its category and a short explanation. An optional LLM pass may **only reword the explanation** of spans the rules already found; it cannot add, move or remove a span, and reworded text containing numbers, links or evidence-deletion advice is discarded. | `/check` page, Analysis tab; `services/scamcheck/red_flags.py` |
| **Lookalike-URL analyser** | Static, offline analysis of the URL **text only**: it never opens, resolves or fetches the URL. Checks: HTTPS, registered domain vs subdomains, deep subdomains, many hyphens, look-alike character substitutions and one-character typos of brand names, punycode / non-Latin look-alikes, brand names on domains that are not that brand's (small reference list), unusual TLDs, link shorteners, embedded credentials (`user@host`), raw IP hosts, percent-encoding, redirect parameters, sensitive path words, executable downloads. Returns the normalised URL, domain, registered domain, each indicator with a severity, a score, a risk level (low / medium / high) and an explanation. An unfamiliar domain alone is **never** an indicator, and a low result is described as "not a verdict". | `/check?mode=url`; links inside checked messages; `services/scamcheck/url_analyzer.py` |
| **English / Hindi replies** | A language selector (English, हिन्दी). For Hindi, the LLM is told to write user-facing prose (chat reply, explanation, guidance, awareness, red-flag rewording) in Hindi while keeping JSON keys, enum values, source ids, identifiers (phone numbers, URLs, emails, UPI IDs, transaction IDs, amounts) and `[PLACEHOLDERS]` unchanged. Extraction, classification and the complaint draft stay in English so structured fields, citations, provenance and the complaint validator keep working. All output guards still run, and the evidence-deletion guard also covers Hindi phrasing. | reply-language selector on `/assistant`; `services/language.py` |
| **Evidence integrity kit** | The user adds screenshots, statements or recordings in the Evidence tab. Each file is fingerprinted with SHA-256 **in the browser**; only the name, size, type, times and fingerprint are sent, never the file. Files can be linked to evidence-checklist items. The server builds a manifest with its own SHA-256 so changes to the manifest are detected, and a **Verify** step reports whether a file is byte-for-byte identical to one recorded (also after renaming). Generated complaints list the files and fingerprints as **Annexure A**. A matching fingerprint shows a file is unchanged since it was recorded; it does not prove the content is genuine, and no legal admissibility is claimed. | Evidence tab on `/assistant`; `services/evidence/manifest.py` |

## Architecture & tech stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind CSS |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 + pgvector (HNSW cosine index) |
| AI | Provider abstraction: OpenAI-compatible (OpenAI/Groq/OpenRouter/Ollama), Anthropic, offline mock; embeddings: local sentence-transformers (`all-MiniLM-L6-v2`), OpenAI-compatible, or offline hash |

Details: [docs/architecture.md](docs/architecture.md), [docs/architecture_decisions.md](docs/architecture_decisions.md), [docs/rag.md](docs/rag.md).

```
backend/app/
  api/v1/          conversations (+SSE stream, feedback), incidents, rag, scamcheck, evidence
  core/            config, db, redacting logger, error handlers
  models/          SQLAlchemy tables      repositories/  DB access
  schemas/         Pydantic API + LLM output schemas
  services/ai/     providers, structured output helper, prompts/
  services/{incident,classification,rag,guidance,complaint,awareness,conversation,scamcheck,evidence}, language.py
backend/alembic/   migrations             backend/tests/   pytest suite
backend/evaluation/ fictional dataset + eval runner
frontend/          Next.js app            knowledge_base/  RAG documents
```

## Runtime modes

| Mode | LLM | Embeddings | Use |
|---|---|---|---|
| **Offline / mock** | `LLM_PROVIDER=mock` — keyword/regex heuristics, *not a language model*; UI shows "offline mock AI" | `hash` (lexical feature hashing) | tests, offline demo |
| **Real LLM** | `LLM_PROVIDER=openai` (any OpenAI-compatible API, e.g. Groq) or `anthropic` | — | real extraction, classification, generation |
| **Real embeddings** | — | `local` (sentence-transformers `all-MiniLM-L6-v2`, 384-d, runs on CPU) or `openai` | semantic retrieval |

Validated configuration (2026-10-02): Groq `openai/gpt-oss-120b` (evaluation, scripted demo) and
`openai/gpt-oss-20b` (real-provider tests, web-UI demo) + local `all-MiniLM-L6-v2` + PostgreSQL 16 /
pgvector 0.8.7. Groq's free tier allows 200,000 tokens/day per model; one full evaluation run used almost
all of it for the 120b model.

## Requirements

- **PostgreSQL with the pgvector extension** is required for the application database. The provided
  `docker compose` service (`pgvector/pgvector:pg16`) includes it. A plain PostgreSQL install does **not**
  include pgvector. SQLite is used only by the automated tests (vector search there is an exact
  in-Python cosine fallback).
- Python 3.12+ (3.13 tested), Node 20+, Docker Desktop.

## Setup

```bash
cp .env.example .env
```
Edit `.env`: set `POSTGRES_PASSWORD` (and the same password in `DATABASE_URL`). If a local PostgreSQL already
uses port 5432, set `POSTGRES_PORT=5433` and use `localhost:5433` in `DATABASE_URL`. For real mode set the
`LLM_*` and `EMBEDDING_*` variables (see `.env.example`). Keys go only in `.env`, which is git-ignored.

```bash
docker compose up -d db
```

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate                      # Windows; macOS/Linux/Git Bash: source .venv/bin/activate (Git Bash: .venv/Scripts/activate)
pip install -r requirements-dev.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu   # only for EMBEDDING_PROVIDER=local
pip install -r requirements-ml.txt                                   # only for EMBEDDING_PROVIDER=local
alembic upgrade head
python -m scripts.fetch_official_kb   # downloads the official texts listed in knowledge_base/official_sources.json
python -m scripts.ingest_kb
uvicorn app.main:app --port 8000
```

Frontend:

```bash
cd ../frontend                              # from backend/
npm install
npm run dev                                 # http://localhost:3000
```

Everything in Docker (validated 2026-10-02 on fresh volumes): set `INSTALL_ML=true` in `.env`, run
`python -m scripts.fetch_official_kb` once on the host (from `backend/`), then
`docker compose up -d --build` and once `docker compose exec backend python -m scripts.ingest_kb`.
The backend container downloads the embedding model on first use into the `hfcache` volume.

`GET /health` reports the database dialect, providers, number of indexed chunks and the embedding model
the knowledge base was built with. If the configured embedding model differs from the one used at
ingestion, retrieval refuses to run (`embedding_mismatch`) until you re-run ingestion.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | sqlite dev DB | `postgresql+psycopg://user:pass@host:port/db` |
| `POSTGRES_USER/PASSWORD/DB/PORT` | | Docker database; `POSTGRES_PORT` is the host port |
| `LLM_PROVIDER` | `mock` | `mock` \| `openai` (any OpenAI-compatible) \| `anthropic` |
| `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY` | | model, endpoint, key |
| `LLM_TEMPERATURE`, `LLM_TIMEOUT_SECONDS` | 0.2, 60 | |
| `EMBEDDING_PROVIDER` | `hash` | `hash` \| `local` \| `openai` |
| `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` | | |
| `EMBEDDING_DIM` | 384 | must match the migration's `vector(384)` column |
| `RAG_TOP_K`, `RAG_MIN_SCORE` | 5, 0.05 | use ~0.25 for semantic embeddings |
| `SOURCE_MAX_AGE_DAYS` | 180 | sources last verified longer ago are flagged as stale |
| `INSTALL_ML` | false | Docker build: include sentence-transformers |
| `CORS_ORIGINS`, `NEXT_PUBLIC_API_URL` | localhost | |

Logs redact keys, tokens, emails, UPI IDs, phone and long account numbers. Provider errors are logged with
status code and the provider's (redacted) message; clients only receive generic error messages.

## Testing

```bash
cd backend
pytest                                         # unit + offline integration; Postgres/real tests are skipped with a reason
TEST_DATABASE_URL=postgresql+psycopg://cyber:<pw>@localhost:5433/cyberassist_test pytest tests/postgres
RUN_REAL_PROVIDER_TESTS=1 pytest tests/real_provider     # uses providers from .env, costs API quota
cd ../frontend && npm run typecheck && npm run build
```

| Folder | What | Needs |
|---|---|---|
| `tests/unit` | evidence manifest (deterministic digest, match / changed file / tampered manifest, complaint annexure without new placeholders), red-flag rules (exact spans; LLM may only reword), URL analyser (safe and suspicious examples, no network access), language instructions and guards on Hindi output, structured-output repair/fallback, extraction & classification scenarios, identifier grounding, complaint placeholders, guidance/awareness filters, evidence-destruction guard, log redaction | nothing |
| `tests/integration` | Alembic migration on SQLite, KB ingestion, retrieval, dedupe, provenance, prompt-injection stripping, URL validation, re-embedding on model change, full API + SSE flow | nothing |
| `tests/postgres` | pgvector extension, `vector(384)` column, HNSW index, stored vector norms, `<=>` search, embedding-mismatch guard, conversation persistence | PostgreSQL+pgvector test database (it is reset) |
| `tests/real_provider` | semantic retrieval of a paraphrase; demo end to end with the real LLM: validated outputs, no invented facts, no unsupported URLs/helplines, no evidence-deletion advice, complaint identifiers | `.env` providers + `RUN_REAL_PROVIDER_TESTS=1` |

Pytest prints every skipped test and why (`-rs`); skipped tests are not counted as passed.

## Evaluation

```bash
python -m evaluation.run_eval --mode real      # configured LLM + embeddings → evaluation/results_real.json
python -m evaluation.run_eval --mode offline   # mock LLM; pipeline check only
python -m evaluation.run_scenarios --mode offline   # 12 user-journey scenarios: right next step, no unsafe assumptions
```

See [docs/project_explanation.md](docs/project_explanation.md#evaluation) for the method, dataset and the
measured results. The offline mode's keyword rules were written together with the dataset, so its scores
are **not** model accuracy.

## RAG ingestion

The knowledge base has two clearly separated parts:

- `knowledge_base/documents/` — 14 **team-written summaries** (`curated_summary`), committed.
- `knowledge_base/official/` — **official texts** (CERT-In Cyber Security Awareness Booklet; NCRP Online
  Safety Tips page), downloaded unmodified by `python -m scripts.fetch_official_kb`, labelled
  `official_text` with URL, download date and SHA-256. Not committed (not redistributed).

Required metadata for every document: `title, organization, url (http/https), category, document_type,
source_note`. Ingest everything with `python -m scripts.ingest_kb`. See
[knowledge_base/README.md](knowledge_base/README.md) and [docs/rag.md](docs/rag.md).

## API

| Method | Path |
|---|---|
| POST | `/api/v1/conversations` |
| GET · DELETE | `/api/v1/conversations/{id}` (DELETE erases the conversation, incident, drafts and feedback) |
| POST | `/api/v1/conversations/{id}/messages` (JSON) · `/messages/stream` (SSE) |
| POST | `/api/v1/incidents/analyze` |
| GET | `/api/v1/incidents/{id}` |
| POST | `/api/v1/incidents/{id}/guidance` · `/complaint` · `/awareness` |
| POST | `/api/v1/rag/search` |
| POST | `/api/v1/check/message` — rule-based red flags (`{"text", "llm_explanations"?, "language"?}`) |
| POST | `/api/v1/check/url` — offline URL analysis (`{"url"}`) |
| POST | `/api/v1/evidence/manifest` — manifest of client-side fingerprints (`{"files": [...]}`) |
| POST | `/api/v1/evidence/verify` — check a fingerprint against a manifest (`{"manifest", "sha256", "name"?}`) |
| POST | `/api/v1/feedback` |
| GET | `/api/v1/meta` (includes supported languages), `/health` |

Chat messages accept `"language": "en" | "hi"`; results carry `language` and, for reported or checked
messages, `red_flags`. Complaint requests (chat `generate_complaint` and `/incidents/{id}/complaint`) accept
`evidence_files` for the annexure.

Interactive docs at http://localhost:8000/docs. Errors share one shape: `{"error": {"code", "message", "details"}}`.

## Limitations
- Knowledge base is small: 16 documents / 69 chunks. Only 2 are official texts; 14 are team-written
  summaries. RBI, NPCI and several cybercrime.gov.in documents could not be retrieved and are covered only
  by summaries.
- Retrieval misses remain (e.g. online-shopping and crypto-exchange cases; see docs/rag.md).
- `hash` embeddings are lexical; use `local` or `openai` for semantic retrieval.
- Mock mode is heuristic and not a language model.
- One analysed message makes ~5–6 LLM calls. On Groq's free tier, rate limits (HTTP 429, retried with
  backoff) made turns take roughly 1–2 minutes during validation.
- Output guards are rule-based (URL/helpline scrubbing, evidence-deletion filter, complaint-claim
  validator) and can miss paraphrases; the complaint validator is conservative and may also drop true
  statements the user phrased differently. Drafts must be reviewed before submission.
- No authentication; conversations are anonymous by ID, and anyone with an ID can read or delete it. Do not deploy publicly as-is.
- Team-written summaries have no `last_verified` date yet and are shown as "not verified"; guidance says so. See
  [docs/guidance_evaluation.md](docs/guidance_evaluation.md) for the grounding, ambiguity and privacy changes and their evaluation.
- Follow-up question signals (OTP shared, link clicked, loss stated/denied) are English rules.
- Complainant details entered for a draft are stored in the draft text.
- Replies in English or Hindi only. Extraction rules, the mock provider, baseline evidence items and the
  complaint draft are English; Hindi output quality depends on the model and has not been measured.
  India-focused guidance.
- Red-flag and URL checks are fixed rules: they miss phrasings and tricks they don't list, can flag
  harmless messages that use the same words, and the brand list for URL checks is small. A clean result
  does not mean a message or link is safe. The URL analyser does not check reputation or blocklists.
- The evidence kit keeps its file list in the page only (lost on reload; keep the downloaded manifest).
  Files above 200 MB are not fingerprinted in the browser. The manifest fingerprint detects accidental or
  careless edits to the manifest, not a deliberate forgery by someone who recomputes it; there is no
  server-side signature or trusted timestamp.
- It does not file complaints; users must submit through official channels.

## Future scope
Authentication and user history; more regional languages (for example Tamil); a measured evaluation of
the red-flag rules and URL analyser on a labelled dataset; voice input; OCR of
screenshot evidence; ingestion of full official advisories with scheduled refresh; hybrid (BM25 + vector)
retrieval and re-ranking; LLM-as-judge evaluation with a larger, human-labelled dataset; PDF export of the
complaint.
