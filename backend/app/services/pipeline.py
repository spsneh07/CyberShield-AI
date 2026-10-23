"""Wires the individual services together for one DB session."""
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Incident
from app.repositories.incidents import IncidentRepository
from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Awareness, Citation, ComplaintDraftOut, Explanation, Guidance
from app.schemas.incident import ComplainantDetails, IncidentData
from app.services.ai.base import EmbeddingProvider, LLMProvider
from app.services.awareness.generator import AwarenessGenerator
from app.services.classification.classifier import IncidentClassifier
from app.services.classification.taxonomy import label
from app.services.complaint.generator import ComplaintGenerator
from app.services.evidence.manifest import EvidenceFile
from app.services.guidance.advisor import Advisor
from app.services.incident.extractor import IncidentExtractor
from app.services.rag.retriever import Retriever

StageCallback = Callable[[str], None]


def _noop(_: str) -> None:
    pass


class Pipeline:
    def __init__(self, db: Session, llm: LLMProvider, embeddings: EmbeddingProvider, settings: Settings) -> None:
        self.db, self.llm = db, llm
        self.incidents = IncidentRepository(db)
        self.extractor = IncidentExtractor(llm)
        self.classifier = IncidentClassifier(llm)
        self.retriever = Retriever(db, embeddings, settings.rag_top_k, settings.rag_min_score, settings.source_max_age_days)
        self.advisor = Advisor(llm)
        self.complaints = ComplaintGenerator(llm)
        self.awareness_gen = AwarenessGenerator(llm)

    # -- analysis ---------------------------------------------------------------------------
    def analyze(self, message: str, conversation_id: str | None = None, existing: Incident | None = None,
                emit: StageCallback = _noop) -> dict:
        previous = self.incidents.to_data(existing) if existing else None
        emit("extracting")
        data, warnings = self.extractor.extract(message, previous)
        emit("classifying")
        cls = self.classifier.classify(data)
        data.incident_type, data.subtype = cls.category, cls.subtype
        emit("retrieving")
        sources = self.retriever.for_incident(data, cls)
        emit("generating")
        explanation = self.advisor.explain(data, cls, sources)
        guidance = self.advisor.guide(data, cls, sources)
        emit("saving")
        incident = self.incidents.save(data, conversation_id, existing)
        self.incidents.add_classification(incident, cls, self.llm.name)
        self.incidents.replace_evidence(incident, guidance.evidence_checklist)
        return {"incident": incident, "data": data, "classification": cls, "sources": sources,
                "explanation": explanation, "guidance": guidance, "warnings": warnings}

    def load(self, incident: Incident) -> tuple[IncidentData, ClassificationResult]:
        data = self.incidents.to_data(incident)
        cls = self.incidents.latest_classification(incident) or ClassificationResult(
            category="unknown", confidence=0.0, reasoning="Not yet classified.")
        return data, cls

    def guidance_for(self, incident: Incident) -> tuple[Explanation, Guidance, list[Citation]]:
        data, cls = self.load(incident)
        sources = self.retriever.for_incident(data, cls)
        explanation = self.advisor.explain(data, cls, sources)
        guidance = self.advisor.guide(data, cls, sources)
        self.incidents.replace_evidence(incident, guidance.evidence_checklist)
        return explanation, guidance, sources

    def complaint_for(self, incident: Incident, complainant: ComplainantDetails | None = None,
                      evidence_files: list[EvidenceFile] | None = None) -> ComplaintDraftOut:
        data, cls = self.load(incident)
        subject, body, placeholders, notes = self.complaints.generate(data, cls, complainant, evidence_files)
        row = self.incidents.add_complaint(incident, subject, body, placeholders)
        return ComplaintDraftOut(id=row.id, incident_id=incident.id, subject=subject, body=body,
                                 placeholders=placeholders, validation_notes=notes)

    def awareness_for(self, incident: Incident | None, topic: str | None = None) -> tuple[Awareness, list[Citation]]:
        if incident is not None:
            data, cls = self.load(incident)
            sources = self.retriever.search(f"{label(cls.category)} prevention warning signs {data.description[:300]}")
        else:
            data, cls = None, None
            sources = self.retriever.search(f"{topic or 'online fraud'} safety tips prevention")
        content = self.awareness_gen.generate(data, cls, sources, topic)
        self.incidents.add_awareness(incident, content)
        return content, sources
