"""Real-provider integration tests (network, API quota). Opt-in:

    RUN_REAL_PROVIDER_TESTS=1 pytest tests/real_provider

Providers are read from the project .env (the test conftest forces mock for everything else).
Uses its own temporary SQLite KB ingested with the real embedding provider.
"""
import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import ROOT_DIR, Settings
from app.services.ai.factory import build_embeddings, build_llm
from app.services.guidance.safety import destroys_evidence
from app.services.incident.identifiers import extract_identifiers
from app.services.pipeline import Pipeline
from app.services.rag.ingest import Ingestor
from evaluation.run_eval import unsupported_mentions
from tests.conftest import BACKEND, DEMO, KB_DIR

pytestmark = [
    pytest.mark.real_provider,
    pytest.mark.skipif(os.environ.get("RUN_REAL_PROVIDER_TESTS") != "1", reason="RUN_REAL_PROVIDER_TESTS=1 not set"),
]


def _dotenv() -> dict[str, str]:
    path = ROOT_DIR / ".env"
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    return {k.strip().lower(): v.strip() for k, v in pairs}


@pytest.fixture(scope="module")
def real():
    env = _dotenv()
    fields = {k: v for k, v in env.items() if k in Settings.model_fields and (k.startswith(("llm_", "embedding_", "rag_")))}
    settings = Settings(**fields)
    if settings.llm_provider == "mock":
        pytest.skip("LLM_PROVIDER in .env is mock")
    tmp = tempfile.mkdtemp(prefix="cyberassist-real-")
    engine = create_engine(f"sqlite:///{Path(tmp, 'real.db').as_posix()}")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    emb = build_embeddings(settings)
    with factory() as db:
        Ingestor(db, emb).ingest_dir(Path(KB_DIR))
    db = factory()
    yield Pipeline(db, build_llm(settings), emb, settings), settings
    db.close()
    engine.dispose()


def test_real_embeddings_are_semantic(real):
    p, settings = real
    if settings.embedding_provider == "hash":
        pytest.skip("hash embeddings are lexical")
    # Paraphrase with almost no word overlap with the UPI document.
    hits = p.retriever.search("someone tricked me into approving a payment request on my phone app", top_k=3)
    assert hits and hits[0].category == "upi_fraud"


def test_demo_end_to_end_with_real_llm(real):
    p, _ = real
    out = p.analyze(DEMO)
    data, cls, guidance, sources = out["data"], out["classification"], out["guidance"], out["sources"]
    assert out["warnings"] == []  # structured outputs validated without falling back
    assert cls.category in {"phishing", "otp_scam", "smishing"} and cls.confidence >= 0.5
    assert data.platform and "whatsapp" in data.platform.lower()
    assert data.date_time is None and data.amount is None  # not invented
    assert sources and guidance.immediate_actions and guidance.evidence_checklist
    text = " ".join(guidance.immediate_actions + guidance.security_steps + guidance.reporting_guidance)
    assert unsupported_mentions(text, sources) == []
    assert not any(destroys_evidence(x) for x in guidance.immediate_actions + guidance.security_steps)
    assert set(guidance.source_ids) <= {s.id for s in sources}

    draft = p.complaint_for(out["incident"])
    found = extract_identifiers(draft.body)
    assert not (found.phone_numbers or found.upi_ids or found.emails)  # none were provided
    assert "[FULL NAME]" in draft.placeholders

    awareness, aw_sources = p.awareness_for(out["incident"])
    assert awareness.prevention_tips
    assert {r.url for r in awareness.resources} <= {s.url for s in aw_sources}
