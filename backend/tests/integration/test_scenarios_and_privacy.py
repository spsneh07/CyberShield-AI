"""Scenario-based actionability evaluation (offline) plus retention / freshness integration checks."""
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.models import ComplaintDraft, Feedback, Incident, Message
from app.repositories.conversations import ConversationRepository
from app.repositories.knowledge import KnowledgeRepository
from app.services.ai.providers.hash_embeddings import HashEmbeddings
from app.services.conversation.orchestrator import Orchestrator
from app.services.pipeline import Pipeline
from app.services.rag.ingest import Ingestor
from evaluation.run_scenarios import HERE, run, summarise
from tests.conftest import DEMO


def test_all_scenarios_pass_offline(db, llm):
    scenarios = [json.loads(x) for x in (HERE / "scenarios.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

    def factory():
        return Orchestrator(db, Pipeline(db, llm, HashEmbeddings(384), get_settings())), ConversationRepository(db).create().id

    rows = run(factory, scenarios)
    assert summarise(rows)["failures"] == {}


def test_curated_summaries_are_reported_as_unverified(pipeline):
    hits = pipeline.retriever.search("report cybercrime helpline portal", top_k=3)
    assert hits and all(h.freshness == "unverified" and h.last_verified is None for h in hits
                        if h.document_type == "curated_summary")


def test_last_verified_metadata_is_ingested(db, pipeline, tmp_path: Path):
    (tmp_path / "fresh.md").write_text(
        "---\ntitle: Fresh\norganization: Test\nurl: https://fresh.test\ncategory: test_fresh\n"
        f"document_type: official_text\nsource_note: fixture\nlast_verified: 2099-01-01\n---\nZebra quokka helpline facts.\n",
        encoding="utf-8")
    Ingestor(db, HashEmbeddings(384)).ingest_dir(tmp_path, prune=False)
    try:
        [hit] = pipeline.retriever.search("zebra quokka", top_k=1, category="test_fresh")
        assert hit.last_verified == "2099-01-01" and hit.freshness == "verified"
    finally:
        db.delete(KnowledgeRepository(db).get_by_path("fresh.md"))
        db.commit()


def test_delete_conversation_erases_derived_records(client, db):
    cid = client.post("/api/v1/conversations", json={}).json()["id"]
    res = client.post(f"/api/v1/conversations/{cid}/messages", json={"content": DEMO}).json()
    client.post(f"/api/v1/conversations/{cid}/messages", json={"content": "draft", "action": "generate_complaint"})
    msg_id = db.scalars(select(Message.id).where(Message.conversation_id == cid)).first()
    assert client.post("/api/v1/feedback", json={"message_id": msg_id, "rating": 1}).status_code == 201

    assert client.delete(f"/api/v1/conversations/{cid}").status_code == 204
    db.expire_all()
    assert client.get(f"/api/v1/conversations/{cid}").status_code == 404
    assert client.get(f"/api/v1/incidents/{res['incident_id']}").status_code == 404
    assert db.scalars(select(Message).where(Message.conversation_id == cid)).first() is None
    assert db.scalars(select(Incident).where(Incident.id == res["incident_id"])).first() is None
    assert db.scalars(select(ComplaintDraft).where(ComplaintDraft.incident_id == res["incident_id"])).first() is None
    assert db.scalars(select(Feedback).where(Feedback.message_id == msg_id)).first() is None
    assert client.delete(f"/api/v1/conversations/{cid}").status_code == 404


def test_guidance_response_exposes_evidence_status(client):
    cid = client.post("/api/v1/conversations", json={}).json()["id"]
    g = client.post(f"/api/v1/conversations/{cid}/messages", json={"content": DEMO}).json()["guidance"]
    assert g["evidence_status"] in {"supported", "limited", "insufficient"} and g["verification_notes"]
