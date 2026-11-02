from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AwarenessContent, Classification, ComplaintDraft, EvidenceItem, Incident
from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Awareness, EvidenceChecklistItem
from app.schemas.incident import IncidentData


class IncidentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, incident_id: str) -> Incident | None:
        return self.db.get(Incident, incident_id)

    def latest_for_conversation(self, conversation_id: str) -> Incident | None:
        stmt = (
            select(Incident)
            .where(Incident.conversation_id == conversation_id)
            .order_by(Incident.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(
        self,
        data: IncidentData,
        conversation_id: str | None = None,
        incident: Incident | None = None,
        user_id: str | None = None,
    ) -> Incident:
        incident = incident or Incident(conversation_id=conversation_id, user_id=user_id)
        if user_id and incident.user_id is None:
            incident.user_id = user_id
        incident.incident_type = data.incident_type
        incident.subtype = data.subtype
        incident.description = data.description
        incident.platform = data.platform
        incident.financial_loss = data.financial_loss
        incident.amount = data.amount
        incident.currency = data.currency
        incident.date_time = data.date_time
        incident.data = data.model_dump(mode="json")
        self.db.add(incident)
        self.db.commit()
        return incident

    def add_classification(self, incident: Incident, result: ClassificationResult, provider: str) -> Classification:
        row = Classification(
            incident_id=incident.id,
            category=result.category,
            subtype=result.subtype,
            confidence=result.confidence,
            reasoning=result.reasoning,
            alternatives=[a.model_dump() for a in result.alternatives],
            provider=provider,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def latest_classification(self, incident: Incident) -> ClassificationResult | None:
        stmt = (
            select(Classification)
            .where(Classification.incident_id == incident.id)
            .order_by(Classification.created_at.desc())
            .limit(1)
        )
        row = self.db.scalars(stmt).first()
        if row is None:
            return None
        return ClassificationResult(
            category=row.category, subtype=row.subtype, confidence=row.confidence,
            reasoning=row.reasoning, alternatives=row.alternatives,
        )

    def replace_evidence(self, incident: Incident, items: list[EvidenceChecklistItem]) -> None:
        incident.evidence_items = [
            EvidenceItem(incident_id=incident.id, **it.model_dump())
            for it in items
        ]
        self.db.commit()

    def add_complaint(self, incident: Incident, subject: str, body: str, placeholders: list[str]) -> ComplaintDraft:
        row = ComplaintDraft(incident_id=incident.id, subject=subject, body=body, placeholders=placeholders)
        self.db.add(row)
        self.db.commit()
        return row

    def add_awareness(self, incident: Incident | None, content: Awareness) -> AwarenessContent:
        row = AwarenessContent(incident_id=incident.id if incident else None, content=content.model_dump())
        self.db.add(row)
        self.db.commit()
        return row

    @staticmethod
    def to_data(incident: Incident) -> IncidentData:
        return IncidentData.model_validate(incident.data or {"description": incident.description})
