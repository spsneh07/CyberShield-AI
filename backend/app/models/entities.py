import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.db import Base
from app.models.types import EmbeddingVector


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    display_name: Mapped[str | None] = mapped_column(String(120))


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    incidents: Mapped[list["Incident"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # structured assistant result
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    incident_type: Mapped[str | None] = mapped_column(String(60))
    subtype: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str | None] = mapped_column(String(120))
    financial_loss: Mapped[bool | None] = mapped_column()
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    date_time: Mapped[str | None] = mapped_column(String(80))  # as stated by the user
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # full validated IncidentData
    conversation: Mapped[Conversation | None] = relationship(back_populates="incidents")
    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="Classification.created_at"
    )
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class Classification(TimestampMixin, Base):
    __tablename__ = "classifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(60))
    subtype: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(40), default="")
    incident: Mapped[Incident] = relationship(back_populates="classifications")


class EvidenceItem(TimestampMixin, Base):
    __tablename__ = "evidence_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    already_available: Mapped[bool] = mapped_column(default=False)
    incident: Mapped[Incident] = relationship(back_populates="evidence_items")


class ComplaintDraft(TimestampMixin, Base):
    __tablename__ = "complaint_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    placeholders: Mapped[list[str]] = mapped_column(JSON, default=list)


class AwarenessContent(TimestampMixin, Base):
    __tablename__ = "awareness_content"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeDocument(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300))
    organization: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(60), index=True)
    published_date: Mapped[str | None] = mapped_column(String(40))
    last_verified: Mapped[str | None] = mapped_column(String(40))  # ISO date the source was last checked
    document_type: Mapped[str] = mapped_column(String(60))
    source_note: Mapped[str | None] = mapped_column(Text)  # provenance, e.g. "team-written summary of ..."
    embedding_identity: Mapped[str | None] = mapped_column(String(200))
    source_path: Mapped[str] = mapped_column(String(500), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(get_settings().embedding_dim))
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class Feedback(TimestampMixin, Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)  # 1 = helpful, -1 = not helpful
    comment: Mapped[str | None] = mapped_column(Text)
