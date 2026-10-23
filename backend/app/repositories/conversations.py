from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AwarenessContent, ComplaintDraft, Conversation, Feedback, Incident, Message


class ConversationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, title: str | None = None) -> Conversation:
        conv = Conversation(title=title or "New conversation")
        self.db.add(conv)
        self.db.commit()
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        return self.db.get(Conversation, conversation_id)

    def list_messages(self, conversation_id: str, limit: int = 50) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(self.db.scalars(stmt).all()))

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Message:
        msg = Message(conversation_id=conversation_id, role=role, content=content, intent=intent, payload=payload)
        self.db.add(msg)
        self.db.commit()
        return msg

    def set_title_if_default(self, conv: Conversation, title: str) -> None:
        if conv.title == "New conversation":
            conv.title = title[:200]
            self.db.commit()

    def add_feedback(self, message_id: str, rating: int, comment: str | None) -> Feedback:
        fb = Feedback(message_id=message_id, rating=rating, comment=comment)
        self.db.add(fb)
        self.db.commit()
        return fb

    def get_message(self, message_id: str) -> Message | None:
        return self.db.get(Message, message_id)

    def delete(self, conv: Conversation) -> None:
        """Delete a conversation and everything derived from it (messages, incidents, drafts, feedback)."""
        incident_ids = select(Incident.id).where(Incident.conversation_id == conv.id)
        message_ids = select(Message.id).where(Message.conversation_id == conv.id)
        # These tables rely on DB-level ON DELETE CASCADE, which SQLite does not enforce by default.
        self.db.execute(delete(ComplaintDraft).where(ComplaintDraft.incident_id.in_(incident_ids)))
        self.db.execute(delete(AwarenessContent).where(AwarenessContent.incident_id.in_(incident_ids)))
        self.db.execute(delete(Feedback).where(Feedback.message_id.in_(message_ids)))
        self.db.delete(conv)
        self.db.commit()
