from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    id: str
    title: str
    organization: str
    url: str
    category: str
    document_type: str
    published_date: str | None = None
    source_note: str | None = None
    last_verified: str | None = None  # date someone last checked the source is current; None = never recorded
    freshness: Literal["verified", "stale", "unverified"] = "unverified"
    score: float
    excerpt: str


class Explanation(BaseModel):
    summary: str
    why_this_type: str
    warning_signs: list[str] = Field(default_factory=list)
    simple_explanation: str
    source_ids: list[str] = Field(default_factory=list)


class EvidenceChecklistItem(BaseModel):
    item: str
    why: str = ""
    priority: Literal["high", "medium", "low"] = "medium"
    already_available: bool = False


class Guidance(BaseModel):
    immediate_actions: list[str] = Field(default_factory=list)
    security_steps: list[str] = Field(default_factory=list)
    reporting_guidance: list[str] = Field(default_factory=list)
    evidence_checklist: list[EvidenceChecklistItem] = Field(default_factory=list)
    do_not: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    # Computed after generation (never by the model): how well the guidance is backed by retrieved sources.
    evidence_status: Literal["supported", "limited", "insufficient"] = "supported"
    verification_notes: list[str] = Field(default_factory=list)


class Resource(BaseModel):
    title: str
    organization: str = ""
    url: str


class Awareness(BaseModel):
    headline: str
    warning_signs: list[str] = Field(default_factory=list)
    prevention_tips: list[str] = Field(default_factory=list)
    future_precautions: list[str] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)


class ComplaintNarrative(BaseModel):
    """LLM output: only the narrative paragraph; the rest is templated."""

    narrative: str
    requested_assistance: list[str] = Field(default_factory=list)


class ComplaintDraftOut(BaseModel):
    id: str | None = None
    incident_id: str
    subject: str
    body: str
    placeholders: list[str]
    validation_notes: list[str] = Field(default_factory=list)  # unsupported claims removed from the narrative


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    top_k: int = Field(5, ge=1, le=20)
    category: str | None = None
