# Knowledge base

Documents in `documents/` are ingested by `python -m scripts.ingest_kb` (from `backend/`).

## Format

Markdown or text with a front-matter header:

```
---
title: ...                # required
organization: ...         # required — the organisation whose guidance this is
url: https://...          # required, http(s) only — the official page the content is based on
category: phishing | upi_fraud | ... | general | reporting | evidence   # required
date: 2017-07-06          # publication date of the underlying guidance if known, otherwise empty
last_verified: 2026-10-20 # date a person checked the official page still says this; empty = never (shown as "not verified")
document_type: curated_summary | advisory | circular | faq | official_text   # required
source_note: ...          # required — plain statement of provenance, shown to users with the citation
---
```

PDFs are supported with a sidecar `<name>.meta.json` containing the same keys.

## Provenance — read this

The seed documents are **curated summaries written by the project team** of
publicly available guidance; they are *not* verbatim copies of the official
documents and are labelled `document_type: curated_summary`. Each links to the
official organisation page it is based on. Before relying on any detail
(helpline numbers, timelines, procedures) verify it at the linked source, and
prefer adding the original official documents here and re-running ingestion.

## Current contents

| Folder | What | In git? |
|---|---|---|
| `documents/` | 14 **team-written summaries** (`document_type: curated_summary`), each with the note "Team-written summary of public guidance from the organisation above. Not the official text; verify details at the source URL." Source URLs are organisation home pages. | yes |
| `official/` | **Official documents**, downloaded unmodified by `python -m scripts.fetch_official_kb` from the URLs in `official_sources.json` (`document_type: official_text`; source note records URL, download date and SHA-256). | no — re-downloaded on each machine; the official texts are not redistributed in this repository |

Official documents currently fetched (2026-10-02):

| File | Organisation | URL | SHA-256 |
|---|---|---|---|
| `cert_in_csa_booklet.pdf` (17 pages) | CERT-In, MeitY | https://www.cert-in.org.in/PDF/CSA_Booklet.pdf | `8709d683…dc807cea2` |
| `ncrp_online_safety_tips.txt` (web page → text) | I4C, Ministry of Home Affairs | https://cybercrime.gov.in/Webform/Crime_OnlineSafetyTips.aspx | `951007d2…5f11e832` |

Other official sources were tried but could not be retrieved from this machine: RBI BE(A)WARE booklet and
press release, cybercrime.gov.in PDF booklets (404), NPCI safety page (403). Their topics are covered only
by team-written summaries.

Topic coverage: phishing, smishing, vishing, UPI fraud, banking fraud, OTP fraud, shopping/fake websites,
job/investment scams, romance and crypto scams, identity theft, account takeover, data breaches,
social-media impersonation, sextortion, harassment, malware, ransomware, evidence preservation and
reporting. The official CERT-In booklet additionally covers phishing, vishing, malicious apps, malware,
social-media fraud, scams targeting senior citizens, women and persons with disability, attacks on
organisations, passwords and basic cyber hygiene.
