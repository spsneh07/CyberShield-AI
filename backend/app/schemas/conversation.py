from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Awareness, Citation, ComplaintDraftOut, Explanation, Guidance
from app.schemas.incident import ComplainantDetails, IncidentData
from app.services.evidence.manifest import EvidenceFile
from app.services.language import Language
from app.services.scamcheck.red_flags import RedFlagReport

Intent = Literal[
    "report_incident", "provide_details", "check_message", "question",
    "generate_complaint", "evidence_checklist", "safety_tips", "smalltalk",
]


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(0.5, ge=0, le=1)


class ChatReply(BaseModel):
    reply: str


class MessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=6000)
    action: Intent | None = Field(None, description="Explicit quick action; skips intent detection")
    complainant: ComplainantDetails | None = None
    language: Language = Field("en", description="Language for user-facing replies (en, hi)")
    evidence_files: list[EvidenceFile] = Field(default_factory=list, max_length=100,
                                               description="Fingerprinted evidence metadata for the complaint annexure")

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=200)


class AssistantResult(BaseModel):
    intent: Intent
    reply: str
    incident_id: str | None = None
    incident: IncidentData | None = None
    classification: ClassificationResult | None = None
    explanation: Explanation | None = None
    guidance: Guidance | None = None
    sources: list[Citation] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    complaint: ComplaintDraftOut | None = None
    awareness: Awareness | None = None
    stages: list[str] = Field(default_factory=list)
    provider: str = ""
    warnings: list[str] = Field(default_factory=list)
    red_flags: RedFlagReport | None = None
    language: Language = "en"


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    intent: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)
    incident_id: str | None = None
    session_token: str | None = None


class FeedbackIn(BaseModel):
    message_id: str
    rating: Literal[1, -1]
    comment: str | None = Field(None, max_length=1000)
