"""Evaluation on the small fictional dataset.

  python -m evaluation.run_eval --mode offline   # mock heuristics as the LLM; embeddings + KB from .env
  python -m evaluation.run_eval --mode real      # provider/model/embeddings from .env, KB from DATABASE_URL

Offline mode only checks that the pipeline runs; its keyword rules were written by
the same people who wrote the dataset, so its scores are NOT model accuracy.
Real mode measures the configured LLM + embeddings. Metrics are computed fresh on every
run and written with the provider/model they belong to. The dataset is small and
fictional — results indicate behaviour, not real-world accuracy.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.schemas.guidance import Guidance
from app.services.ai.factory import build_embeddings, build_llm
from app.services.ai.prompts import guidance as guidance_prompt
from app.services.ai.structured import generate_structured
from app.services.classification.classifier import IncidentClassifier
from app.services.complaint.generator import ComplaintGenerator
from app.services.guidance.advisor import _NUMBER, _URL, known_text, scrub_unsupported
from app.services.guidance.safety import destroys_evidence
from app.services.incident.extractor import IncidentExtractor
from app.services.incident.identifiers import extract_identifiers, normalize
from app.services.rag.retriever import Retriever

HERE = Path(__file__).parent


def unsupported_mentions(text: str, sources, known: str = "") -> list[str]:
    """URLs and phone/helpline-style numbers in text that occur neither in retrieved sources nor in `known`."""
    found = [m.group(0) for m in _URL.finditer(text)] + [m.group(0) for m in _NUMBER.finditer(text)]
    return [x for x in found if scrub_unsupported(x, sources, known) != x]


def complaint_check(data, body: str) -> dict:
    provided = [v for v in (data.date_time, data.platform) if v]
    provided += data.phone_numbers + data.emails + data.urls + data.upi_ids + data.account_identifiers
    missing = [v for v in provided if normalize(v) not in normalize(body)]
    if data.amount is not None and f"{data.amount:,.2f}" not in body:
        missing.append(str(data.amount))
    known = {normalize(v) for v in data.phone_numbers + data.emails + data.urls + data.upi_ids + data.account_identifiers}
    found = extract_identifiers(body)
    invented = [v for f in ("phone_numbers", "emails", "upi_ids") for v in getattr(found, f) if normalize(v) not in known]
    return {"facts_provided": len(provided), "facts_missing": missing, "invented_identifiers": invented}


def retrieval_only(settings, cases_path: str, out: str | None) -> None:
    emb = build_embeddings(settings)
    cases = [json.loads(x) for x in Path(cases_path).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = []
    with SessionLocal() as db:
        retriever = Retriever(db, emb, top_k=3, min_score=settings.rag_min_score)
        for case in cases:
            hits = retriever.search(case["text"], top_k=3)
            row = {"id": case["id"], "retrieved": [(h.category, h.document_type, h.score) for h in hits]}
            if case["relevant_kb_categories"]:
                row["hit_at_3"] = any(h.category in case["relevant_kb_categories"] for h in hits)
                row["hit_at_1"] = bool(hits) and hits[0].category in case["relevant_kb_categories"]
            row["official_in_top3"] = any(h.document_type == "official_text" for h in hits)
            rows.append(row)
            print(case["id"], row.get("hit_at_3"), [f"{c}/{t[:8]}:{s:.2f}" for c, t, s in row["retrieved"]])
    scored = [r for r in rows if "hit_at_3" in r]
    summary = {
        "mode": "retrieval-only", "run_at": datetime.now(timezone.utc).isoformat(), "embeddings": emb.identity,
        "cases_with_relevant_docs": len(scored),
        "hit_at_1": f"{sum(r['hit_at_1'] for r in scored)}/{len(scored)}",
        "hit_at_3": f"{sum(r['hit_at_3'] for r in scored)}/{len(scored)}",
        "cases_with_official_text_in_top3": f"{sum(r['official_in_top3'] for r in rows)}/{len(rows)}",
        "note": "Relevance = topic category of the KB document; official texts are category 'general' and never count as hits.",
    }
    Path(out or HERE / "results_retrieval.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "real"], required=True)
    parser.add_argument("--dataset", default=str(HERE / "dataset.jsonl"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=0, help="evaluate only the first N cases")
    parser.add_argument("--retrieval-only", action="store_true", help="only measure retrieval (no LLM calls)")
    args = parser.parse_args()

    settings = get_settings()
    if args.retrieval_only:
        return retrieval_only(settings, cases_path=args.dataset, out=args.out)
    if args.mode == "offline":
        settings = settings.model_copy(update={"llm_provider": "mock"})  # embeddings: as configured (must match the KB)
    elif settings.llm_provider == "mock":
        raise SystemExit("--mode real needs LLM_PROVIDER=openai|anthropic in .env")
    llm, emb = build_llm(settings), build_embeddings(settings)
    extractor, classifier, complaints = IncidentExtractor(llm), IncidentClassifier(llm), ComplaintGenerator(llm)
    cases = [json.loads(x) for x in Path(args.dataset).read_text(encoding="utf-8").splitlines() if x.strip()]
    if args.limit:
        cases = cases[: args.limit]

    rows = []
    started = time.time()
    with SessionLocal() as db:
        retriever = Retriever(db, emb, top_k=3, min_score=settings.rag_min_score)
        for case in cases:
            row: dict = {"id": case["id"], "expected": case["expected_category"]}
            data, warnings = extractor.extract(case["text"])
            cls = classifier.classify(data)
            row.update(predicted=cls.category, confidence=cls.confidence,
                       correct=cls.category == case["expected_category"],
                       acceptable=cls.category == case["expected_category"] or cls.category in case["acceptable"],
                       extraction_warnings=warnings)
            id_results = {}
            for field, expected in case["expected_identifiers"].items():
                got = getattr(data, field)
                id_results[field] = (got == expected) if field == "amount" else \
                    {normalize(v) for v in expected} <= {normalize(v) for v in got}
            row["identifiers"] = id_results

            sources = retriever.search(case["text"], top_k=3)
            row["retrieved"] = [s.category for s in sources]
            if case["relevant_kb_categories"]:
                row["retrieval_hit_at_3"] = any(s.category in case["relevant_kb_categories"] for s in sources)

            if cls.category != "unknown":
                raw = generate_structured(llm, task=guidance_prompt.TASK, system=guidance_prompt.SYSTEM,
                                          user=guidance_prompt.build(data, cls, sources), schema=Guidance)
                text = " ".join(raw.immediate_actions + raw.security_steps + raw.reporting_guidance)
                row["grounding"] = {
                    "unsupported_mentions": unsupported_mentions(text, sources, known_text(data)),
                    "invalid_source_ids": [i for i in raw.source_ids if i not in {s.id for s in sources}],
                    "evidence_destruction_advice": [x for x in raw.immediate_actions + raw.security_steps if destroys_evidence(x)],
                }
                _, body, _, _ = complaints.generate(data, cls)
                row["complaint"] = complaint_check(data, body)
            rows.append(row)
            print(f"{case['id']}: {case['expected_category']:<27} -> {cls.category:<27} "
                  f"{'OK' if row['correct'] else ('~' if row['acceptable'] else 'X')}  retrieved={row['retrieved']}")

    n = len(rows)
    ids = [v for r in rows for v in r["identifiers"].values()]
    ret = [r["retrieval_hit_at_3"] for r in rows if "retrieval_hit_at_3" in r]
    gr = [r["grounding"] for r in rows if "grounding" in r]
    cp = [r["complaint"] for r in rows if "complaint" in r]
    summary = {
        "mode": args.mode,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "llm": f"{settings.llm_provider}:{settings.llm_model}" if args.mode == "real" else "mock-heuristic (not a model)",
        "embeddings": emb.identity,
        "cases": n,
        "classification_exact": f"{sum(r['correct'] for r in rows)}/{n}",
        "classification_acceptable": f"{sum(r['acceptable'] for r in rows)}/{n}",
        "identifier_fields_correct": f"{sum(ids)}/{len(ids)}",
        "retrieval_hit_at_3": f"{sum(ret)}/{len(ret)} (cases with a relevant KB document)",
        "guidance_outputs_with_unsupported_contacts": f"{sum(bool(g['unsupported_mentions']) for g in gr)}/{len(gr)} (raw model output, before scrubbing)",
        "guidance_outputs_with_invalid_source_ids": f"{sum(bool(g['invalid_source_ids']) for g in gr)}/{len(gr)}",
        "guidance_outputs_advising_evidence_deletion": f"{sum(bool(g['evidence_destruction_advice']) for g in gr)}/{len(gr)} (raw, before safety filter)",
        "complaints_with_all_provided_facts": f"{sum(not c['facts_missing'] for c in cp)}/{len(cp)}",
        "complaints_with_invented_identifiers": f"{sum(bool(c['invented_identifiers']) for c in cp)}/{len(cp)}",
        "elapsed_seconds": round(time.time() - started, 1),
        "note": "Small fictional dataset (20 cases); indicates behaviour, not real-world accuracy."
                + (" Offline mock rules were written alongside this dataset: scores are not model accuracy." if args.mode == "offline" else ""),
    }
    out = Path(args.out or HERE / f"results_{args.mode}.json")
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
